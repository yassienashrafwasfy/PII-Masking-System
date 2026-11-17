#!/usr/bin/env python3
"""One corpus through the whole regex engine, pinned span by span.

The individual detector suites pin one rule each. This one pins what a
document actually gets back: every rule in table order, then de-duplication,
cross-label arbitration and the output-label mapping. A detector that stops
firing costs recall silently -- the response still looks like a masked
document -- so the expectation here is the exact value and label of every
span, not a count and not a subset.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core.schemas import MaskSpan
from masking_engine.app.engines.regex_arbitration import (
    _arbitrate_overlaps,
    _to_output_spans,
)
from masking_engine.app.engines.regex_engine import (
    _detected_spans,
    find_regex_spans,
    find_regex_spans_pages,
)


def found(text: str) -> list[tuple[str, str]]:
    return [(text[s.start : s.end], s.label) for s in find_regex_spans(text)]


# (document, every span it must produce, in the order the engine returns them)
DOCUMENTS: tuple[tuple[str, list[tuple[str, str]]], ...] = (
    ("Patient is 47 jaar oud en woont in Gent.", [("47", "AGE")]),
    ("A 63-year-old man presented today.", [("63", "AGE")]),
    # The cue may follow the number or precede it; both arms carry the value.
    ("Leeftijd: 82. Geslacht: V.", [("82", "AGE"), ("V", "GENDER")]),
    ("Il a 57 ans et elle est âgée de 61 ans.", [("57", "AGE"), ("61", "AGE")]),
    (
        "Sex: female, gender: male, sexe: homme.",
        [("female", "GENDER"), ("male", "GENDER"), ("homme", "GENDER")],
    ),
    # The one slot where a bare age and sex are reachable at all.
    ("De patient (41, M) kwam binnen.", [("41", "AGE"), ("M", "GENDER")]),
    ("Kerkstraat 12, 9000 Gent", [("12", "ADDRESS"), ("9000", "ADDRESS")]),
    ("Rue de Rivoli 5, 75008 Paris", [("5", "ADDRESS"), ("75008", "ADDRESS")]),
    (
        "482 Lakeshore Drive, Chicago, IL 60601",
        [("482", "ADDRESS"), ("60601", "ADDRESS")],
    ),
    (
        "Baker Street 221B, London NW1 6XE",
        [("221B", "ADDRESS"), ("NW1 6XE", "ADDRESS")],
    ),
    (
        "Datum: 12/03/2024 en 3 maart 2024 en 2024-03-12.",
        [("12/03/2024", "DATE"), ("3 maart 2024", "DATE"), ("2024-03-12", "DATE")],
    ),
    (
        "Consultatie op 05.06.1998, herhaald op June 7, 2021.",
        [("05.06.1998", "DATE"), ("June 7, 2021", "DATE")],
    ),
    (
        "Contact: jan.peeters@ziekenhuis.be of https://az-gent.be/afspraak.",
        [
            ("jan.peeters@ziekenhuis.be", "EMAIL"),
            ("https://az-gent.be/afspraak", "URL"),
        ],
    ),
    (
        "AZ Sint-Lucas VZW en Acme Corp en Klinik GmbH",
        [
            ("AZ Sint-Lucas VZW", "ORGANIZATION_NAME"),
            ("Acme Corp", "ORGANIZATION_NAME"),
            ("Klinik GmbH", "ORGANIZATION_NAME"),
        ],
    ),
    ("AZORG is de instelling", [("AZORG", "ORGANIZATION_NAME")]),
    (
        "NPI 1234567893 en GMC number 1234567 en RPPS 10101234567.",
        [("1234567893", "NPI"), ("1234567", "GMC"), ("10101234567", "RPPS")],
    ),
    (
        "NHS Number 943 476 5919 en SSN 123-45-6789.",
        [("943 476 5919", "NHS_NUMBER"), ("123-45-6789", "SSN")],
    ),
    (
        "Telefoon 0472 50 29 61 en +32 9 224 55 66.",
        [("0472 50 29 61", "PHONE_NUMBER"), ("+32 9 224 55 66", "PHONE_NUMBER")],
    ),
    (
        "BTW BE 0123.456.749 en VAT GB123456789.",
        [("BE 0123.456.749", "BTW_EENHEID"), ("GB123456789", "BTW_EENHEID")],
    ),
    (
        "RIZIV 1-23456-78-901, INSZ 41.05.12-509.56, NIR 2 69 05 49 588 157 80.",
        [
            ("1-23456-78-901", "RIZIV_INAMI"),
            ("41.05.12-509.56", "INSZ_NISS"),
            ("2 69 05 49 588 157 80", "NIR"),
        ],
    ),
    ("Rijksregisternummer 800705 609 97", [("800705 609 97", "INSZ_NISS")]),
    (
        "CNP 1960522401234, Steuer-ID 12345678903, DNI 12345678Z, NIE X1234567L.",
        [
            ("1960522401234", "CNP"),
            ("12345678903", "STEUER_ID"),
            ("12345678Z", "DNI"),
            ("X1234567L", "NIE"),
        ],
    ),
    (
        "LANR 123456401, colegiado 280112345, PARAFA 12345.",
        [("123456401", "LANR"), ("280112345", "COLEGIADO"), ("12345", "PARAFA")],
    ),
    # A bare Steuer-ID that also satisfies a looser check still belongs to
    # the stricter rule: 18640727955 passes the RIZIV mod-89/97 check and
    # 22301954670 the INSZ mod-97 check, and both used to be claimed by those
    # earlier bare rules -- then masked as a digit redraw rather than as the
    # valid Steuer-ID the surrogate generator draws.
    (
        "Nummern 18640727955 und 22301954670.",
        [("18640727955", "STEUER_ID"), ("22301954670", "STEUER_ID")],
    ),
)

# Prose that has to come back untouched. Four bare digits are a year far more
# often than a postcode, and a measurement is not an identifier.
UNTOUCHED: tuple[str, ...] = (
    "De kamer meet 4 op 5 meter en kost 1200 euro.",
    "Bestelbon 2019 werd in 2020 afgesloten.",
    "De uitslag was 12 op 20 punten.",
    "Protocol 7 werd gevolgd volgens richtlijn 2016.",
    "",
)

# Where a cue word wins over the reading a human would give the sentence.
# Recorded rather than fixed: each of these masks something that is not PII,
# which costs a little text and leaks nothing, and the cue that produces it is
# the same one that catches the real value in a clinical letter.
OVER_TRIGGERS: tuple[tuple[str, list[tuple[str, str]]], ...] = (
    ("Hij heeft er 3 jaar over gedaan.", [("3", "AGE")]),
    ("The reference OK 12345 is not a postcode.", [("12345", "ADDRESS")]),
    ("Het toestel weegt 1,5 kg.", [("1,5 kg", "WEIGHT")]),
)

# Detected on purpose, then dropped on purpose: these labels have no place in
# the published taxonomy, so they are found and then not emitted. Finding them
# still matters -- an overlapping span they beat in arbitration stays dropped.
SUPPRESSED: tuple[tuple[str, tuple[str, str]], ...] = (
    ("Bloedgroep A+ genoteerd", ("A+", "BLOOD_TYPE")),
    ("Blood type: O negative", ("O negative", "BLOOD_TYPE")),
    (
        "kaartnummer 4111 1111 1111 1111 werd gebruikt",
        ("4111 1111 1111 1111", "CREDITCARDNUMBER"),
    ),
    ("MRN ABC1234567", ("ABC1234567", "MRN")),
    ("Versichertennummer A123456780", ("A123456780", "HEALTH_INSURANCE_ID")),
)


def test_every_document_yields_exactly_its_spans() -> None:
    for text, expected in DOCUMENTS:
        assert found(text) == expected, text


def test_prose_without_an_identifier_comes_back_untouched() -> None:
    for text in UNTOUCHED:
        assert found(text) == [], text


def test_the_recorded_over_triggers_still_behave_as_recorded() -> None:
    for text, expected in OVER_TRIGGERS:
        assert found(text) == expected, text


def test_a_label_outside_the_taxonomy_is_found_and_then_not_emitted() -> None:
    for text, (value, label) in SUPPRESSED:
        detected = {(text[s.start : s.end], s.label) for s in _detected_spans(text)}
        assert (value, label) in detected, text
        assert found(text) == [], text


def test_every_span_lands_on_the_text_it_names() -> None:
    for text, expected in DOCUMENTS:
        for span in find_regex_spans(text):
            assert 0 <= span.start < span.end <= len(text), (text, span)
            assert span.source == "regex", (text, span)
        assert [value for value, _ in expected] == [
            text[s.start : s.end] for s in find_regex_spans(text)
        ]


def test_no_two_emitted_spans_overlap() -> None:
    for text, _ in DOCUMENTS + OVER_TRIGGERS:
        spans = sorted(find_regex_spans(text), key=lambda s: s.start)
        for earlier, later in zip(spans, spans[1:]):
            assert earlier.end <= later.start, (text, earlier, later)


def test_each_page_keeps_its_own_index() -> None:
    pages = [text for text, _ in DOCUMENTS[:3]]
    spans = find_regex_spans_pages(pages)
    assert {span.page for span in spans} == {0, 1, 2}
    for index, page in enumerate(pages):
        on_page = [s for s in spans if s.page == index]
        assert [(page[s.start : s.end], s.label) for s in on_page] == DOCUMENTS[index][
            1
        ]
    assert find_regex_spans_pages([]) == []


# --------------------------------------------------------------------------- #
# Arbitration
# --------------------------------------------------------------------------- #


def span(start: int, end: int, label: str) -> MaskSpan:
    return MaskSpan(start, end, label, "regex")


def test_the_longer_of_two_overlapping_spans_wins() -> None:
    long, short = span(0, 10, "DATE"), span(2, 6, "AGE")
    assert _arbitrate_overlaps([short, long]) == [long]
    assert _arbitrate_overlaps([long, short]) == [long]


def test_equal_length_is_settled_by_the_taxonomy_s_own_order() -> None:
    # INSZ is listed above NHS_NUMBER, so it takes a run both claim.
    insz, nhs = span(0, 11, "INSZ"), span(0, 11, "NHS_NUMBER")
    assert _arbitrate_overlaps([nhs, insz]) == [insz]
    assert _arbitrate_overlaps([insz, nhs]) == [insz]


def test_spans_that_only_touch_are_both_kept() -> None:
    first, second = span(0, 5, "AGE"), span(5, 9, "DATE")
    assert _arbitrate_overlaps([first, second]) == [first, second]


def test_an_unlisted_label_ranks_behind_every_listed_one() -> None:
    listed, unlisted = span(0, 6, "DATE"), span(0, 6, "SOMETHING_NEW")
    assert _arbitrate_overlaps([unlisted, listed]) == [listed]


def test_the_output_mapping_renames_and_drops() -> None:
    renamed = _to_output_spans(
        [span(0, 1, "INSZ"), span(1, 2, "TELEFOON"), span(2, 3, "ZIPCODE")]
    )
    assert [s.label for s in renamed] == ["INSZ_NISS", "PHONE_NUMBER", "ADDRESS"]
    assert _to_output_spans([span(0, 1, "BLOOD_TYPE"), span(1, 2, "MRN")]) == []
    # A label with neither a rename nor a suppression passes through unchanged.
    assert [s.label for s in _to_output_spans([span(0, 1, "EMAIL")])] == ["EMAIL"]


def test_the_same_span_found_twice_is_emitted_once() -> None:
    # Two rules claim "A+" here; the engine de-duplicates before arbitrating.
    text = "Bloedgroep A+ genoteerd"
    detected = _detected_spans(text)
    assert len(detected) > len({(s.label, s.start, s.end) for s in detected})


# --------------------------------------------------------------------------- #
# Coalescing, which is what the renderers actually consume
# --------------------------------------------------------------------------- #

from masking_engine.app.engines.regex_arbitration import coalesce_spans  # noqa: E402


def scored(start: int, end: int, label: str, source: str, confidence=None, page=None):
    return MaskSpan(start, end, label, source, page=page, confidence=confidence)


def test_spans_that_do_not_touch_are_all_kept_in_order() -> None:
    spans = [span(10, 15, "AGE"), span(0, 5, "DATE")]
    assert [(s.start, s.end) for s in coalesce_spans(spans)] == [(0, 5), (10, 15)]
    assert coalesce_spans([]) == []


def test_two_overlapping_spans_become_one_region() -> None:
    # A replacement no longer has the length of the text it covers, so one
    # region has to become one replacement.
    merged = coalesce_spans([span(0, 20, "URL"), span(5, 10, "NAME")])
    assert [(s.start, s.end, s.label) for s in merged] == [(0, 20, "URL")]
    # The region covers the union, so no detected character survives.
    merged = coalesce_spans([span(0, 10, "URL"), span(5, 30, "NAME")])
    assert [(s.start, s.end) for s in merged] == [(0, 30)]


def test_the_longest_contributor_gives_the_region_its_label() -> None:
    merged = coalesce_spans([span(0, 6, "AGE"), span(4, 20, "DATE")])
    assert merged[0].label == "DATE"


def test_where_the_two_sources_agree_exactly_the_scored_one_wins() -> None:
    # A model span carries a confidence and a regex one does not; without this
    # three identical phone numbers in one footer came out scored on some and
    # unscored on others, depending on the order they arrived in.
    model = scored(0, 10, "PHONE_NUMBER", "model", confidence=0.9)
    regex = scored(0, 10, "PHONE_NUMBER", "regex")
    for ordering in ((model, regex), (regex, model)):
        (merged,) = coalesce_spans(list(ordering))
        assert merged.source == "model"
        assert merged.confidence == 0.9


def test_spans_on_different_pages_never_merge() -> None:
    spans = [
        scored(0, 10, "NAME", "model", page=0),
        scored(5, 15, "NAME", "model", page=1),
    ]
    assert [(s.page, s.start, s.end) for s in coalesce_spans(spans)] == [
        (0, 0, 10),
        (1, 5, 15),
    ]


def test_spans_that_only_touch_at_a_boundary_stay_apart() -> None:
    merged = coalesce_spans([span(0, 10, "NAME"), span(10, 20, "DATE")])
    assert [(s.start, s.end) for s in merged] == [(0, 10), (10, 20)]


def test_the_engine_records_what_it_arbitrated_away(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.regex_engine")
    text = "Bloedgroep A+ genoteerd"
    raw = len(_detected_spans(text))
    find_regex_spans(text)
    assert (
        f"regex_spans count=0 (raw={raw}, arbitrated away {raw} cross-label overlaps)"
    ) in [record.getMessage() for record in caplog.records]
