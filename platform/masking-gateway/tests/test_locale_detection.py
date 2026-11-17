#!/usr/bin/env python3
"""Pin per-document locale detection and the guards that reject a guess."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import document_verdict, prepare_document, redact_document
from masking_engine.app.core import locale_detection
from masking_engine.app.core.locale_detection import (
    MIN_CONFIDENCE,
    MIN_LETTERS,
    SAMPLE_LIMIT,
    detect_locale,
    resolve_locale,
)

FRENCH_NOTE = (
    "COMPTE RENDU DE CONSULTATION\n"
    "Le patient a ete recu ce matin pour un controle de routine.\n"
    "Le praticien a prescrit un traitement pour deux semaines.\n"
)
DUTCH_NOTE = (
    "VERSLAG VAN CONSULTATIE\n"
    "De patient werd vanmorgen gezien voor een routinecontrole.\n"
    "De arts schreef een behandeling voor twee weken voor.\n"
)
ENGLISH_NOTE = (
    "CLINICAL CONSULTATION REPORT\n"
    "The patient was seen this morning for a routine check.\n"
    "The physician prescribed a treatment for two weeks.\n"
)
GERMAN_NOTE = "BEFUNDBERICHT\nDer Patient wurde heute zur Routinekontrolle untersucht."
SPANISH_NOTE = (
    "INFORME CLINICO\nEl paciente fue atendido hoy para una revision rutinaria."
)
ROMANIAN_NOTE = (
    "RAPORT CLINIC\nPacientul a fost consultat astazi pentru un control de rutina."
)


def test_detects_every_locale_that_has_a_profile() -> None:
    assert detect_locale(FRENCH_NOTE) == "fr_FR"
    assert detect_locale(DUTCH_NOTE) == "nl_BE"
    assert detect_locale(ENGLISH_NOTE) == "en"
    assert detect_locale(GERMAN_NOTE) == "de_DE"
    assert detect_locale(SPANISH_NOTE) == "es_ES"
    assert detect_locale(ROMANIAN_NOTE) == "ro_RO"


def test_english_resolves_to_its_own_locale_rather_than_the_fallback() -> None:
    """This is the inverse of what it once pinned.

    English was loaded so it could be recognised and *rejected*: a detector
    holding only Dutch and French has nowhere to put an English note and
    scored one 0.97 as French, past any confidence threshold. Now that English
    has surrogates of its own it resolves to them, and the *default* is what
    proves the verdict came from the detector rather than from a fallback --
    an English note reaches `en` whatever the caller nominates.
    """
    assert detect_locale(ENGLISH_NOTE) == "en"
    assert detect_locale(ENGLISH_NOTE, default="fr_FR") == "en"


def test_text_without_signal_falls_back_rather_than_guessing() -> None:
    """A wrong verdict changes every surrogate; the fallback changes nothing."""
    for text in ("", "   ", "NIR : 219078519105027", "Sexe : F", "10003411442"):
        assert detect_locale(text) == "nl_BE", repr(text)
    # The guard is the letter count, so a French note is still detected even
    # when it sits just above the floor.
    assert len([c for c in FRENCH_NOTE if c.isalpha()]) > MIN_LETTERS
    assert 0.0 < MIN_CONFIDENCE < 1.0


def test_detection_is_deterministic_and_reads_a_bounded_prefix() -> None:
    """Masking has to be byte-reproducible for NiFi to replay a key."""
    assert len({detect_locale(FRENCH_NOTE) for _ in range(25)}) == 1
    # Text past the sample limit cannot change the verdict.
    padded = FRENCH_NOTE + " " * SAMPLE_LIMIT + DUTCH_NOTE * 20
    assert detect_locale(padded) == "fr_FR"


def test_redact_document_detects_the_locale_when_none_is_passed() -> None:
    """The pipeline resolves the locale itself; callers pass nothing."""
    from masking_engine import coalesce_spans, find_regex_spans

    french = FRENCH_NOTE + "Site : https://www.chu-exemple.fr\n"
    dutch = DUTCH_NOTE + "Site : https://www.uzgent.be\n"
    english = ENGLISH_NOTE + "Site : https://www.example-hospital.org\n"
    for note, tld in ((french, ".fr"), (dutch, ".be"), (english, ".uk")):
        document = prepare_document(note.encode(), "text/plain")
        spans = coalesce_spans(find_regex_spans(document.texts[0].text))
        masked = redact_document(document, spans).decode()
        host = [line for line in masked.splitlines() if line.startswith("Site")][0]
        assert host.endswith(tld), host
        # An explicit locale still overrides detection.
        forced = redact_document(document, spans, locale="nl_BE").decode()
        assert [line for line in forced.splitlines() if line.startswith("Site")][
            0
        ].endswith(".be")


def test_confidence_report_ranks_every_candidate_and_marks_the_rejects() -> None:
    """The badge needs the losers too, or a fallback looks unexplained."""
    from masking_engine.app.core.locale_detection import confidence_report

    rows = confidence_report(FRENCH_NOTE)
    assert rows, "a real note must produce a report"
    names = [name for name, _, _ in rows]
    scores = [score for _, score, _ in rows]
    assert names[0] == "FRENCH"
    assert scores == sorted(scores, reverse=True), scores
    mapped = {name: locale for name, _, locale in rows if locale is not None}
    assert mapped == {
        "FRENCH": "fr_FR",
        "DUTCH": "nl_BE",
        "ENGLISH": "en",
        "SPANISH": "es_ES",
        "GERMAN": "de_DE",
        "ROMANIAN": "ro_RO",
    }, mapped
    assert set(names) == {
        "FRENCH",
        "DUTCH",
        "ENGLISH",
        "SPANISH",
        "GERMAN",
        "ROMANIAN",
    }, names
    # Short text produces no report rather than a meaningless ranking.
    assert confidence_report("NIR: 1") == ()


def test_locale_badge_draws_on_a_pdf_without_changing_replacements() -> None:
    """The badge is a diagnostic overlay, never part of masking."""
    import pymupdf

    from masking_engine import coalesce_spans, find_regex_spans

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 90), FRENCH_NOTE.replace("\n", " "), fontsize=9)
    page.insert_text((60, 130), "Site : https://www.chu-exemple.fr", fontsize=9)
    pdf_bytes = document.tobytes()
    document.close()

    prepared = prepare_document(pdf_bytes, "application/pdf")
    spans = coalesce_spans(
        [
            span
            for page_text in prepared.texts
            for span in _paged(find_regex_spans(page_text.text), page_text.page)
        ]
    )
    plain = redact_document(prepared, spans)
    badged = redact_document(prepared, spans, locale_badge=True)

    # The overlay adds ink, so the files differ...
    assert plain != badged
    # ...but the replacements do not: the text layer of the badged output
    # contains the badge's own words and nothing else new.
    added = _words(badged) - _words(plain)
    # One chip per loaded language, each carrying its name and its score,
    # and nothing else: the overlay adds no other text to the page.
    LOADED_LANGUAGES = {
        "French",
        "Dutch",
        "English",
        "Spanish",
        "German",
        "Romanian",
    }
    assert LOADED_LANGUAGES <= added, added
    assert any(word.endswith("%") for word in added), added
    assert added <= LOADED_LANGUAGES | {f"{value}%" for value in range(101)}, added


def _paged(spans, page):
    from dataclasses import replace

    return [replace(span, page=page) if page is not None else span for span in spans]


def _words(pdf_bytes: bytes) -> set[str]:
    import pymupdf

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return {
            word
            for index in range(len(document))
            for word in document[index].get_text().split()
        }
    finally:
        document.close()


def test_a_verdict_says_where_the_locale_came_from() -> None:
    # The locale alone cannot answer "did anything actually recognise this
    # document" -- three different situations all report nl_BE.
    assert resolve_locale(DUTCH_NOTE).source == "detected"
    assert resolve_locale(DUTCH_NOTE).detected is True
    assert resolve_locale("NIR : 219078519105027").source == "fallback"
    assert resolve_locale("NIR : 219078519105027").detected is False
    pinned = resolve_locale(FRENCH_NOTE, pinned="nl_BE")
    assert (pinned.locale, pinned.source, pinned.detected) == ("nl_BE", "pinned", False)


def test_a_dutch_fallback_is_not_reported_as_a_detection() -> None:
    # The fallback locale is Dutch, so a Dutch document the detector was not
    # confident about resolves to the same string a confident detection
    # would. Only the source separates them, which is exactly what the
    # diagnostic badge exists to show.
    short_dutch = "De patient werd gezien voor een routinecontrole vandaag."
    verdict = resolve_locale(short_dutch, default="nl_BE")
    assert verdict.locale == "nl_BE"
    if verdict.source == "fallback":
        assert verdict.detected is False


def test_one_document_is_detected_exactly_once() -> None:
    # Masking resolved the locale twice per document: once before detection
    # so a per-language model could be chosen, once inside redaction for the
    # surrogates. Two runs of a non-deterministic-in-principle detector on
    # one document is both wasted work and a way for the two halves to
    # disagree. The verdict is resolved once and handed on.
    document = prepare_document(FRENCH_NOTE.encode("utf-8"), "text/plain")
    calls: list[str] = []
    real = locale_detection._detector

    def counting():
        calls.append("x")
        return real()

    locale_detection._detector = counting
    try:
        verdict = document_verdict(document)
        redact_document(document, [], locale=verdict)
    finally:
        locale_detection._detector = real
    assert verdict.locale == "fr_FR"
    assert len(calls) == 1, f"detector consulted {len(calls)} times, expected 1"


def main() -> None:
    test_detects_every_locale_that_has_a_profile()
    test_english_resolves_to_its_own_locale_rather_than_the_fallback()
    test_text_without_signal_falls_back_rather_than_guessing()
    test_detection_is_deterministic_and_reads_a_bounded_prefix()
    test_redact_document_detects_the_locale_when_none_is_passed()
    test_confidence_report_ranks_every_candidate_and_marks_the_rejects()
    test_locale_badge_draws_on_a_pdf_without_changing_replacements()
    test_a_verdict_says_where_the_locale_came_from()
    test_a_dutch_fallback_is_not_reported_as_a_detection()
    test_one_document_is_detected_exactly_once()
    print("Validated per-document locale detection, guards, and determinism.")


if __name__ == "__main__":
    main()
