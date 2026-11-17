#!/usr/bin/env python3
"""Pin the identifier shape rule against real and impossible forms."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core.schemas import MaskSpan
from masking_engine.app.engines import span_shape
from masking_engine.app.engines.regex_belgium import _insz_ok, _riziv_ok
from masking_engine.app.engines.regex_france import _nir_ok

NBSP = " "

# Every written form the grammars accept for a class the rule gates. These are
# the shapes a real document carries, so refusing any of them would cost
# recall the model is the only source for.
ADMITTED = [
    ("RIZIV_INAMI", "8-34742-78-981"),
    ("RIZIV_INAMI", "1-23456-78-901"),
    ("RIZIV_INAMI", "83474278"),
    ("RIZIV_INAMI", "1 23456 78 901"),
    ("RIZIV_INAMI", f"1{NBSP}23456{NBSP}78{NBSP}901"),
    ("INSZ_NISS", "41.05.12-509.56"),
    ("INSZ_NISS", "800705 609 97"),
    ("INSZ_NISS", "50010368687"),
    ("NIR", "2 69 05 49 588 157 80"),
    ("NIR", "269054958815780"),
    ("NHS_NUMBER", "943 476 5919"),
    ("NHS_NUMBER", "9434765919"),
]

# The detections this rule exists to refuse, taken from the development set.
REFUSED = [
    ("RIZIV_INAMI", "535581078"),
    ("RIZIV_INAMI", "672448934"),
    ("INSZ_NISS", "158567364"),
    ("INSZ_NISS", "071881769"),
    ("NIR", "3074185296307418"),
    ("NHS_NUMBER", "30741852963"),
]

# A digit count alone is not the whole shape: a bracket the span over-ran or a
# currency sign in front of an amount says the text is not an identifier
# whatever its length. "377249721033083" really is fifteen digits.
NOT_ONLY_DIGITS = [
    ("NIR", "€377249721033083"),
    ("RIZIV_INAMI", "738226896)"),
    ("RIZIV_INAMI", "(83474278"),
    ("INSZ_NISS", "50010368687a"),
    ("NHS_NUMBER", "NHS 9434765919"),
]


def test_every_written_form_of_a_gated_identifier_is_admitted() -> None:
    for label, text in ADMITTED:
        assert span_shape.admits(label, text), (label, text)


def test_a_run_of_the_wrong_length_is_refused() -> None:
    for label, text in REFUSED:
        assert not span_shape.admits(label, text), (label, text)


def test_a_character_no_identifier_carries_is_refused() -> None:
    for label, text in NOT_ONLY_DIGITS:
        assert not span_shape.admits(label, text), (label, text)


def test_an_ungated_label_is_always_admitted() -> None:
    # The rule is applied to whole span lists, so a label it cannot judge has
    # to pass through rather than be dropped for want of an opinion.
    for label in ("NAME", "ADDRESS", "DATE", "PHONE_NUMBER", "EMAIL", "GMC"):
        assert label not in span_shape.gated_labels()
        assert span_shape.admits(label, "anything at all 12345")


def test_the_counts_are_the_ones_the_validators_enforce() -> None:
    # The rule must not become a second opinion about these formats. Each
    # count below is asserted against the checksum validator that owns it,
    # so a format change in one place fails here rather than drifting.
    assert _riziv_lengths() == span_shape._DIGIT_COUNTS["RIZIV_INAMI"]
    assert {11} == set(span_shape._DIGIT_COUNTS["INSZ_NISS"])
    assert _insz_ok("50010368687") and not _insz_ok("5001036868")
    assert _nir_ok("269054958815780") and not _nir_ok("26905495881578")
    assert {15} == set(span_shape._DIGIT_COUNTS["NIR"])


def test_an_age_has_to_carry_a_digit() -> None:
    """The words the detector read as ages on a Dutch clinical letter.

    "Sinds ongeveer drie maanden" -- "since approximately three months" -- came
    back as two AGE spans at 0.82 and 0.93, and the surrogate, having no number
    to reformat, redrew their letters: the released page said "Sinds trisrztc
    plen maanden".
    """
    assert not span_shape.admits("AGE", "ongeveer")
    assert not span_shape.admits("AGE", "drie")
    # Every shape a real age is written in on these documents.
    for age in ("71", "71-jarige", "20-year-old", "45 jaar", "de 8"):
        assert span_shape.admits("AGE", age), age


def test_age_is_one_of_the_labels_the_rule_judges() -> None:
    # A caller filtering on `gated_labels` has to see it, or the rule is
    # applied to identifiers alone and AGE goes back through ungated.
    assert "AGE" in span_shape.gated_labels()


# --------------------------------------------------------------------------- #
# Applying the rule to a span list
# --------------------------------------------------------------------------- #


def test_a_span_list_keeps_only_the_shapes_the_rule_admits() -> None:
    text = "Young woman, RIZIV 535581078, 71 jaar oud."
    spans = [
        MaskSpan(0, 5, "AGE", "model"),
        MaskSpan(19, 28, "RIZIV_INAMI", "model"),
        MaskSpan(30, 32, "AGE", "model"),
        MaskSpan(6, 11, "GENDER", "model"),
    ]

    kept = span_shape.admitted(spans, {None: text})

    # The ungated label survives untouched: this rule refuses what it can
    # judge and admits everything else, which is what lets a caller hand it a
    # whole span list rather than pre-filtering on `gated_labels`.
    assert [(span.start, span.label) for span in kept] == [
        (30, "AGE"),
        (6, "GENDER"),
    ]


def test_no_rule_means_no_gate() -> None:
    """`MODEL_ID_SHAPE_GATE=off` has to be the ungated behaviour exactly."""
    spans = [MaskSpan(0, 5, "AGE", "model")]

    assert span_shape.admitted(spans, {None: "Young"}, rule=None) == spans


def test_a_span_whose_text_cannot_be_read_is_kept() -> None:
    """The rule refuses what it reads and never guesses from an empty slice.

    Two ways to arrive here: a page this caller supplied no text for, and a
    span past the end of the text it was given. Dropping either would
    suppress a detection on the strength of the caller's bookkeeping rather
    than the span's own shape. (A zero-width span is a third way in theory
    and `MaskSpan` refuses to be constructed as one, so it is not tested.)
    """
    unknown_page = MaskSpan(0, 5, "AGE", "model", page=7)
    past_the_end = MaskSpan(40, 45, "AGE", "model")

    assert span_shape.admitted([unknown_page], {None: "Young"}) == [unknown_page]
    assert span_shape.admitted([past_the_end], {None: "Young"}) == [past_the_end]


def test_a_substituted_rule_is_the_one_that_decides() -> None:
    # The seam `Orchestrator` injects through, pinned here so it cannot decay
    # into an import at the point of use.
    spans = [MaskSpan(0, 5, "AGE", "model"), MaskSpan(6, 11, "NAME", "model")]

    kept = span_shape.admitted(spans, {None: "Young woman"}, rule=lambda *_: False)

    assert kept == []


def _riziv_lengths() -> frozenset[int]:
    """Return the digit counts `_riziv_ok` will consider, found by probing."""
    lengths = set()
    for length in range(1, 17):
        # A base of six zeroes has ``89 - 0 % 89 == 89``, so this candidate is
        # valid at exactly the lengths the validator accepts. Below eight
        # digits it cannot carry a base and a check at all, and is not probed.
        candidate = "0" * 6 + "89" + "0" * max(0, length - 8)
        if len(candidate) == length and _riziv_ok(candidate):
            lengths.add(length)
    return frozenset(lengths)


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()
