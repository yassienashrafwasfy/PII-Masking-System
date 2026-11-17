#!/usr/bin/env python3
"""Validate Berts' span policy over the shared platform detector."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from masking_engine import MaskSpan, find_regex_spans
    from teams.berts.spans import (
        HYBRID_LABELS,
        cover_whole_numbers,
        drop_impossible_shapes,
        merge_drop_all,
        shape_gate,
        trim_title_prefix,
    )

    def labelled(text: str) -> set[tuple[str, str]]:
        return {
            (span.label, text[span.start : span.end]) for span in find_regex_spans(text)
        }

    text = (
        "INSZ 80.01.01-001.07, RIZIV 1-23456-25-000, BTW BE 0123.456.749, "
        "mail emily@example.be and call +32 470 12 34 56 on 29/02/2024."
    )
    assert {
        ("INSZ_NISS", "80.01.01-001.07"),
        ("RIZIV_INAMI", "1-23456-25-000"),
        ("BTW_EENHEID", "BE 0123.456.749"),
        ("EMAIL", "emily@example.be"),
        ("PHONE_NUMBER", "+32 470 12 34 56"),
        ("DATE", "29/02/2024"),
    } <= labelled(text)
    assert not find_regex_spans("Onbekend 31/02/2024.")

    # A number the note itself calls the national number is masked whether or
    # not its check digits verify. One mistyped, OCR'd or synthesised out of
    # checksum is still somebody's national number, and dropping it handed the
    # digits to the phone pattern, which masked the tail and printed the head:
    # the first two digits of an INSZ are the patient's year of birth.
    for sentence, number in (
        ("Invalid INSZ 80.01.01-001.99", "80.01.01-001.99"),
        ("Rijksregisternummer (INSZ/NISS): 78.07.03-456.12", "78.07.03-456.12"),
        ("nationaal nummer 97.02.31-430.12", "97.02.31-430.12"),
    ):
        assert ("INSZ_NISS", number) in labelled(sentence), sentence

    # This team no longer detects identifiers itself, and the platform
    # detector is not a drop-in for the table that used to live here: it
    # masks more. It calls an out-of-checksum eleven-digit run a national
    # number with no label word nearby at all, where this team required one,
    # so an order number in that exact shape is now masked. That direction is
    # the safe one -- the failure it prevents is printing a real national
    # number whose checksum a typo broke -- and it is asserted rather than
    # left to be noticed, because it is the one behaviour the swap widened.
    assert ("INSZ_NISS", "80.01.01-001.99") in labelled(
        "Bestelnummer 80.01.01-001.99 verzonden."
    )

    # What the swap bought: identifiers from the other two languages these
    # checkpoints read. Every one of these reached a Berts-masked document in
    # the clear while this team carried its own Belgian-only table, and a
    # live run of a French clinical letter found exactly that.
    for sentence, label, value in (
        (
            "Le patient, NIR 2 06 03 75 102 003 95, a consulte.",
            "NIR",
            "2 06 03 75 102 003 95",
        ),
        (
            "Numero de securite sociale 206037510200395 au dossier.",
            "NIR",
            "206037510200395",
        ),
        (
            "Patient NHS number 943 476 5919 attended the clinic.",
            "NHS_NUMBER",
            "943 476 5919",
        ),
        ("Patient SSN 796-33-4489 was seen today.", "SSN", "796-33-4489"),
    ):
        assert (label, value) in labelled(sentence), sentence

    # Detection is locale-blind, so the document's language cannot change
    # what is found -- only which checkpoint reads the prose around it.
    belgian = "Patient met INSZ 80.01.01-001.07 kwam langs."
    assert ("INSZ_NISS", "80.01.01-001.07") in labelled(belgian)

    # No detector may mask only part of a number: the digits it leaves behind
    # are printed unchanged in a document that looks masked.
    number = "Rijksregisternummer 65.03.00-121.04 van de patiënt"
    partial = MaskSpan(23, 35, "PHONE_NUMBER", "model")
    assert cover_whole_numbers([partial], number) == [
        MaskSpan(20, 35, "PHONE_NUMBER", "model")
    ]
    # A date range is two numbers, so neither date grows over the other.
    span_range = "01/01/2020-31/12/2020"
    dates = [MaskSpan(0, 10, "DATE", "regex"), MaskSpan(11, 21, "DATE", "regex")]
    assert cover_whole_numbers(dates, span_range) == dates

    # A title is not part of the name: masking it scrambles "dr." into "vv."
    # and tells the reader less than leaving it alone.
    doctor = "Behandeld door dr. Khadija Chakiri."
    assert trim_title_prefix([MaskSpan(15, 34, "NAME", "model")], doctor) == [
        MaskSpan(19, 34, "NAME", "model")
    ]
    # Only NAME spans, and only a real title prefix.
    other = [MaskSpan(0, 5, "ORGANIZATION_NAME", "model")]
    assert trim_title_prefix(other, "dr. x") == other

    # An exact tie on a hybrid label goes to the model: both sources score
    # the same and cover the same characters, and the model list sorts first.
    model_phone = MaskSpan(0, 10, "PHONE_NUMBER", "model")
    regex_phone = MaskSpan(0, 10, "PHONE_NUMBER", "regex")
    regex_email = MaskSpan(12, 28, "EMAIL", "regex")
    assert merge_drop_all([model_phone], [regex_phone, regex_email]) == [
        model_phone,
        regex_email,
    ]

    # A validated identifier outranks a model guess that overlaps it, whether
    # or not the identifier's label is one this policy names explicitly --
    # NIR, NHS_NUMBER and SSN have no entry, and must not therefore lose to a
    # model NAME that swallowed the digits beside a person's name.
    for label in ("NIR", "NHS_NUMBER", "SSN", "INSZ_NISS"):
        identifier = MaskSpan(4, 20, label, "regex")
        name = MaskSpan(0, 24, "NAME", "model")
        assert merge_drop_all([name], [identifier]) == [identifier], label

    # The hybrid labels reach the document from either source. Neither owns
    # them, so an empty model span list leaves every regex date, age and
    # phone standing rather than swallowing it -- which is the only thing
    # that catches a value the checkpoint tagged partly or not at all.
    sentence = "Patient op 12/10/2025, 47 jaar, tel 0470 12 34 56, INSZ 80.01.01-001.07"
    survivors = merge_drop_all([], find_regex_spans(sentence))
    assert {span.label for span in survivors} == {
        "INSZ_NISS",
        *HYBRID_LABELS,
    }, survivors

    # And where both spoke about the same value, the one covering more of it
    # wins. This is the Dutch checkpoint on "- 04-2025:", which tagged the
    # year and left the month: the regex span covering the whole date has to
    # take those characters, or "04" is released in the clear.
    dated = "- 04-2025: Wisselende stoelgang"
    partial = MaskSpan(4, 9, "DATE", "model")
    whole = MaskSpan(2, 9, "DATE", "regex")
    assert merge_drop_all([partial], [whole]) == [whole]
    assert dated[whole.start : whole.end] == "04-2025"

    # It cuts the other way too: a model date the regex only clipped part of
    # keeps its own span, so the tie is decided by coverage and not by which
    # detector happened to speak.
    long_model = MaskSpan(0, 13, "DATE", "model")
    short_regex = MaskSpan(9, 13, "DATE", "regex")
    assert merge_drop_all([long_model], [short_regex]) == [long_model]

    # The platform's shape gate, which this team reaches by hand because it
    # has no Orchestrator. An AGE prediction over a word has no number for the
    # surrogate to reformat, so it was redrawn as letters and released as
    # "Rrovz woman presenting". The regex layer cannot cover for it: it finds
    # a number, and there is no number under a detection like this one.
    prose = "Young woman, 71 jaar, RIZIV 535581078 en RIZIV 1-23456-25-000."
    guesses = [
        MaskSpan(0, 5, "AGE", "model"),
        MaskSpan(13, 15, "AGE", "model"),
        MaskSpan(28, 37, "RIZIV_INAMI", "model"),
        MaskSpan(6, 11, "GENDER", "model"),
    ]
    kept = drop_impossible_shapes(guesses, prose)
    assert [(span.start, span.label) for span in kept] == [
        (13, "AGE"),
        (6, "GENDER"),
    ], kept

    # The switch is the platform's own name, so one deployment variable
    # governs every team; off has to be the ungated list exactly, and a typo
    # has to fail rather than quietly leave the gate on.
    previous = os.environ.get("MODEL_ID_SHAPE_GATE")
    try:
        os.environ["MODEL_ID_SHAPE_GATE"] = "off"
        assert not shape_gate()
        assert drop_impossible_shapes(guesses, prose) == guesses
        os.environ["MODEL_ID_SHAPE_GATE"] = "of"
        try:
            shape_gate()
        except ValueError as exc:
            assert "MODEL_ID_SHAPE_GATE" in str(exc), exc
        else:  # pragma: no cover - the assertion above is the test
            raise AssertionError("a typo left the gate silently on")
    finally:
        if previous is None:
            del os.environ["MODEL_ID_SHAPE_GATE"]
        else:
            os.environ["MODEL_ID_SHAPE_GATE"] = previous

    print("Validated Berts' span policy over the shared platform detector.")


if __name__ == "__main__":
    main()
