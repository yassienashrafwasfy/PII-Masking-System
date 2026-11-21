#!/usr/bin/env python3
"""The adapter trims punctuation around a model span, never inside the value."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))
from masking_engine import MaskSpan  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "mdeberta_adapter", ROOT / "teams/mdeberta/adapter.py"
)
assert _spec is not None and _spec.loader is not None
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)


def trimmed(text: str, value: str, source: str = "model") -> str | None:
    start = text.index(value)
    span = MaskSpan(start, start + len(value), "DATE", source)  # type: ignore[arg-type]
    result = adapter._trimmed(text, span)
    return None if result is None else text[result.start : result.end]


def test_a_closing_bracket_without_its_opener_is_dropped() -> None:
    assert trimmed("(geboren 12/03/1975) woont", "12/03/1975)") == "12/03/1975"


def test_a_bracket_pair_inside_the_value_is_kept() -> None:
    assert trimmed("Tel +32 (0)478 12 34 56 hier", "+32 (0)478 12 34 56") == (
        "+32 (0)478 12 34 56"
    )
    assert trimmed("Tel (02) 123 45 67, ok", "(02) 123 45 67,") == "(02) 123 45 67"


def test_trailing_sentence_punctuation_is_dropped() -> None:
    assert trimmed("Kerkstraat 12, 9000 Gent.", "Gent.") == "Gent"
    assert trimmed("dosis metformine 500 mg.", "metformine 500 mg.") == (
        "metformine 500 mg"
    )
    assert trimmed("a b: x", "b:") == "b"


def test_an_opening_bracket_without_its_closer_is_dropped() -> None:
    assert trimmed("zie (Jan Peeters", "(Jan Peeters") == "Jan Peeters"


def test_a_span_of_only_punctuation_is_dropped() -> None:
    assert trimmed("klaar ).", ").") is None


def test_the_value_itself_is_untouched() -> None:
    assert trimmed("op 12/03/1975 geboren", "12/03/1975") == "12/03/1975"
    assert trimmed("mail a.b@example.com nu", "a.b@example.com") == "a.b@example.com"


def test_regex_spans_are_never_trimmed() -> None:
    """Only the model's habit is corrected: a rule's span is exactly what it matched."""
    text = "Beste Dr. Jan. en (12/03/1975)"
    spans = [
        MaskSpan(6, 14, "NAME", "regex"),
        MaskSpan(18, 29, "DATE", "model"),
    ]
    original = adapter._detect
    adapter._detect = lambda _text, _metadata: spans  # type: ignore[attr-defined]
    try:
        out = adapter.detect(text, {})
    finally:
        adapter._detect = original  # type: ignore[attr-defined]
    assert [(text[s.start : s.end], s.source) for s in out] == [
        ("Dr. Jan.", "regex"),
        ("12/03/1975", "model"),
    ]


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")


if __name__ == "__main__":
    main()
