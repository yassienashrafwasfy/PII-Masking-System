"""Keep Octo's token-by-token address detections in one PDF replacement slot."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan

# `_merge_address_parts` is private: this regression pins it on purpose, so it
# stays a deep import rather than a public-surface name.
from masking_engine.runtime import _merge_address_parts, _trim_line_overrun


def test_merges_address_fragments_but_not_the_following_phone_number() -> None:
    text = "Kapelstraat 32, 8500 Kortrijk T 03/665.47.90"
    spans = [
        MaskSpan(0, 11, "ADDRESS", "model"),
        MaskSpan(12, 14, "ADDRESS", "model"),
        MaskSpan(16, 20, "ADDRESS", "model"),
        MaskSpan(21, 29, "ADDRESS", "model"),
        MaskSpan(32, 44, "PHONE_NUMBER", "regex"),
    ]

    assert _merge_address_parts(text, spans) == [
        MaskSpan(0, 29, "ADDRESS", "model"),
        MaskSpan(32, 44, "PHONE_NUMBER", "regex"),
    ]


def test_trims_a_span_that_ran_past_the_line_break() -> None:
    """A footer's phone number must not carry off the next line's house number.

    The model reads a page as one string, so a value at the end of a line runs
    into the start of the next. The overlap that created was enough for
    `coalesce_spans` to merge the phone into the address printed below it, and
    the phone left the document labelled ADDRESS.
    """
    text = (
        "12 rue de la Republique, 59000 Lille Tel. 03 20 45 12 78\n"
        "8 avenue Victor Hugo, 59100 Roubaix"
    )
    overrun = text.index("03 20 45")
    spans = [
        MaskSpan(overrun, text.index("8 avenue") + 1, "PHONE_NUMBER", "model"),
    ]
    trimmed = _trim_line_overrun(text, spans)
    assert text[trimmed[0].start : trimmed[0].end] == "03 20 45 12 78", trimmed

    # A value that genuinely wraps keeps the whole of its second line.
    wrapped = "Patient Jean-Baptiste\nMoreau werd opgenomen"
    start = wrapped.index("Jean-Baptiste")
    span = MaskSpan(start, wrapped.index(" werd"), "NAME", "model")
    assert _trim_line_overrun(wrapped, [span]) == [span]

    # A span that never crosses a break is returned untouched.
    plain = [MaskSpan(0, 7, "NAME", "model")]
    assert _trim_line_overrun("Patient X", plain) == plain


def test_trims_an_overrun_of_any_length_when_another_span_covers_it() -> None:
    """An English address puts its house number first, so the tail is longer.

    The length test alone trims one or two characters. English writes
    "119 Harbor View Ave", so a footer telephone ran three digits into the
    next line -- "+1 (312) 555-0142\n119" -- the address rule claimed
    "119 Harbor View Ave, Evanston, IL 60201" from the same "119", and
    `coalesce_spans` merged the two: the telephone left the letter drawn as
    an address, which is what the highlight overlay showed as `ADDR` on a
    `Tel.` line.

    Trimming a covered tail is safe at any length because the span that
    covers it still masks it.
    """
    text = (
        "482 Lakeshore Drive, Chicago, IL 60601        Tel. +1 (312) 555-0142\n"
        "119 Harbor View Ave, Evanston, IL 60201        Tel. (847) 555-0198"
    )
    phone_start = text.index("+1 (312)")
    address_start = text.index("119 Harbor")
    address_end = text.index("        Tel. (847)")
    spans = [
        # The model's telephone, three digits past the break.
        MaskSpan(phone_start, address_start + 3, "PHONE_NUMBER", "model"),
        # The address the rules claim, starting at the same house number.
        MaskSpan(address_start, address_end, "ADDRESS", "regex"),
    ]
    trimmed = _trim_line_overrun(text, spans)
    assert text[trimmed[0].start : trimmed[0].end] == "+1 (312) 555-0142", trimmed
    # The covering span is untouched, so the house number is still masked.
    assert trimmed[1] == spans[1]

    # Nothing covers the tail, and three characters is past the length test,
    # so the span keeps it: a trim here would leave "119" in the document.
    alone = [MaskSpan(phone_start, address_start + 3, "PHONE_NUMBER", "model")]
    assert _trim_line_overrun(text, alone) == alone

    # Two spans that both overrun cannot cover each other into a trim that
    # leaves the tail masked by neither: the covering span has to begin on
    # the next line and carry no break of its own.
    both = [
        MaskSpan(phone_start, address_start + 3, "PHONE_NUMBER", "model"),
        MaskSpan(phone_start, address_start + 3, "PHONE_NUMBER", "regex"),
    ]
    assert _trim_line_overrun(text, both) == both


def main() -> None:
    """Run every test in this module.

    Discovered rather than listed by hand. This file had no runner at all and
    was named by no job, so its cases had never once executed; a hand-written
    list is the same failure one step later, because the next test added is
    the one nobody remembers to add to it.
    """
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
