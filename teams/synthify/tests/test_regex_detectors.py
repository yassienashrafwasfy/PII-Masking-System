"""Tests for Synthify's regex detectors and their merge into model spans."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan  # noqa: E402
from teams.synthify import regex_detectors  # noqa: E402
from teams.synthify.runtime import _merge_regex_spans  # noqa: E402

# One INSZ (mod-97 valid), one RIZIV, one Luhn-valid card, one schemeless URL.
TEXT = (
    "Patient Els Wouters, INSZ 98.02.21-128.53, RIZIV 1/21941/78/004.\n"
    "Betaling met kaart 4111 1111 1111 1111. Meer info op www.azorg.be."
)
CARD = "4111 1111 1111 1111"
CARD_START = TEXT.index(CARD)


def _found(text: str) -> list[tuple[str, str]]:
    return sorted(
        (text[entity["start"] : entity["end"]], entity["label"])
        for entity in regex_detectors.detect_entities(text)
    )


def _spans(merged: list) -> list[tuple[int, int, str, str]]:
    return [(s.start, s.end, s.label, s.source) for s in merged]


def test_detects_insz_riziv_credit_card_and_url() -> None:
    assert _found(TEXT) == sorted(
        [
            ("98.02.21-128.53", "INSZ"),
            ("1/21941/78/004", "RVIZ"),
            (CARD, "CREDITCARDNUMBER"),
            ("www.azorg.be", "URL"),
        ]
    )


def test_rejects_credit_card_failing_luhn() -> None:
    assert _found("Kaart 4111 1111 1111 1112.") == []


def test_rejects_insz_failing_mod97_checksum() -> None:
    # Dotted grouping, so the RIZIV shape cannot claim it on the rebound.
    assert _found("INSZ 98.02.21-128.54") == []


def test_drops_sentence_punctuation_trailing_a_url() -> None:
    assert _found("Zie https://azorg.be/contact.") == [
        ("https://azorg.be/contact", "URL")
    ]


def test_merge_adds_entities_the_model_missed() -> None:
    assert _spans(_merge_regex_spans(TEXT, [])) == [
        (26, 41, "INSZ_NISS", "regex"),
        (49, 63, "RIZIV_INAMI", "regex"),
        (CARD_START, CARD_START + len(CARD), "BTW_EENHEID", "regex"),
        (118, 130, "URL", "regex"),
    ]


def test_merge_emits_spans_the_gateway_accepts() -> None:
    merged = _merge_regex_spans(TEXT, [])
    assert {s.source for s in merged} == {"regex"}  # a MaskSource literal
    assert all(s.end > s.start for s in merged)  # MaskSpan.__post_init__ guard
    assert {s.page for s in merged} == {None}  # same page basis as model spans


def test_merge_keeps_model_spans_it_does_not_overlap() -> None:
    name = MaskSpan(8, 19, "NAME", "model")  # "Els Wouters"
    merged = _merge_regex_spans(TEXT, [name])
    assert (8, 19, "NAME", "model") in _spans(merged)
    assert len(merged) == 5


def test_merge_reanchors_a_truncated_model_span() -> None:
    # The model caught "4111 1111"; masking only that leaks the other 8 digits.
    truncated = MaskSpan(CARD_START, CARD_START + 9, "BTW_EENHEID", "model")
    merged = _merge_regex_spans(TEXT, [truncated])
    assert _spans(merged)[2] == (
        CARD_START,
        CARD_START + len(CARD),
        "BTW_EENHEID",
        "regex",
    )
    assert TEXT[merged[2].start : merged[2].end] == CARD


def test_merge_keeps_a_wider_model_span() -> None:
    wide = MaskSpan(CARD_START - 6, CARD_START + len(CARD), "BTW_EENHEID", "model")
    merged = _merge_regex_spans(TEXT, [wide])
    assert (CARD_START - 6, CARD_START + len(CARD), "BTW_EENHEID", "model") in _spans(
        merged
    )
    assert [s for s in merged if s.start < wide.end and wide.start < s.end] == [wide]


def test_merge_breaks_an_exact_tie_toward_the_model() -> None:
    exact = MaskSpan(CARD_START, CARD_START + len(CARD), "BTW_EENHEID", "model")
    merged = _merge_regex_spans(TEXT, [exact])
    assert [s for s in _spans(merged) if s[0] == CARD_START] == [
        (CARD_START, CARD_START + len(CARD), "BTW_EENHEID", "model")
    ]


def test_merge_returns_spans_sorted_by_position() -> None:
    merged = _merge_regex_spans(TEXT, [MaskSpan(8, 19, "NAME", "model")])
    starts = [s.start for s in merged]
    assert starts == sorted(starts)


def test_merge_is_a_noop_without_regex_matches() -> None:
    name = MaskSpan(8, 19, "NAME", "model")
    assert _spans(_merge_regex_spans("Patient Els Wouters.", [name])) == [
        (8, 19, "NAME", "model")
    ]
    assert _merge_regex_spans("", []) == []


def main() -> None:
    cases = [
        case for name, case in sorted(globals().items()) if name.startswith("test_")
    ]
    for case in cases:
        case()
    print(f"Validated Synthify regex detectors and span merge ({len(cases)} cases).")


if __name__ == "__main__":
    main()
