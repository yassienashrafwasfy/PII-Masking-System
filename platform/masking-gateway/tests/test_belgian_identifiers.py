#!/usr/bin/env python3
"""Pin the Belgian national number's date rules, not just its checksum.

The checksum alone leaves roughly one arbitrary digit run in a hundred looking
valid, and `_insz_ok` is half of `_is_national_identifier`, which keeps a
national number out of the phone detector -- so a false positive there
suppresses a real telephone number.

Every case is built with a genuinely valid checksum, so each one tests the date
rules rather than accidentally passing on a bad key.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import find_regex_spans
from masking_engine.app.engines.regex_belgium import _insz_ok


def insz(
    year: int, month: int, day: int, serial: int, *, after_2000: bool = False
) -> str:
    """Build an INSZ whose checksum is correct by construction."""
    body = f"{year:02d}{month:02d}{day:02d}{serial:03d}"
    base = int(("2" + body) if after_2000 else body)
    return f"{body}{97 - (base % 97):02d}"


ACCEPTED = [
    ("an ordinary number", insz(85, 7, 30, 336)),
    ("born in or after 2000, alternate formula", insz(3, 4, 15, 210, after_2000=True)),
    # A bis number offsets the month by 20 or 40. It is a genuine register
    # number, not a malformed one.
    ("bis number, month + 20", insz(85, 27, 30, 336)),
    ("bis number, month + 40", insz(85, 47, 30, 336)),
    # A register that does not know the birth date writes the unknown part as
    # zeroes. A foreign or undocumented patient really does carry one, and
    # rejecting these once left the leading digits in the clear.
    ("unknown month", insz(85, 0, 30, 336)),
    ("unknown day", insz(85, 7, 0, 336)),
    ("unknown month and day", insz(85, 0, 0, 336)),
    # February takes 29 unconditionally: the year is two digits, so the century
    # is unknown and a leap-year test would reject a real number.
    ("29 February", insz(85, 2, 29, 336)),
]

REJECTED = [
    ("month 13", insz(85, 13, 30, 336)),
    ("month 33, outside both bis ranges", insz(85, 33, 30, 336)),
    ("day 32", insz(85, 7, 32, 336)),
    ("31 April", insz(85, 4, 31, 336)),
    ("30 February", insz(85, 2, 30, 336)),
    ("zero serial", insz(85, 7, 30, 0)),
]


def test_a_number_the_register_really_issues_is_accepted() -> None:
    for why, value in ACCEPTED:
        assert _insz_ok(value), f"{value} should be accepted: {why}"


def test_separators_do_not_change_the_verdict() -> None:
    # The number is written several ways in the wild and all of them have to
    # reach the same answer, or the phone detector's guard depends on
    # punctuation.
    for why, value in ACCEPTED:
        spaced = f"{value[:2]}.{value[2:4]}.{value[4:6]}-{value[6:9]}.{value[9:]}"
        assert _insz_ok(spaced), f"{spaced} should be accepted: {why}"


def test_an_impossible_date_is_rejected() -> None:
    for why, value in REJECTED:
        assert not _insz_ok(value), f"{value} should be rejected: {why}"


def test_a_correct_date_with_a_broken_key_is_not_an_insz() -> None:
    assert not _insz_ok("850730336" + "00"), "a wrong checksum must still fail"


def test_a_short_digit_run_is_not_an_insz() -> None:
    assert not _insz_ok("123"), "a short run is not an INSZ"


def test_slash_packed_fields_yield_every_identifier_in_the_run() -> None:
    # Read end to end rather than against the RIZIV pattern alone, because
    # what failed was the interaction: the "/" guards refused the number, and
    # RIZIV is a label the orchestrator lets only the rules claim, so the
    # model detection for it had already been dropped. Nothing masked the
    # value and it left the document in the clear.
    packed = "78.03.12-123.45/1-23456-78-901/09 245 67 89"
    found = {
        span.label: packed[span.start : span.end] for span in find_regex_spans(packed)
    }
    assert found.get("INSZ_NISS") == "78.03.12-123.45", found
    assert found.get("RIZIV_INAMI") == "1-23456-78-901", found
    assert found.get("PHONE_NUMBER") == "09 245 67 89", found


def test_a_slashed_number_run_is_still_not_carved_into_identifiers() -> None:
    # The "/" is a delimiter now, but a digit, a dot and a hyphen still bound
    # a match, so a longer reference number is not read as a RIZIV hiding
    # inside it.
    for text in ("ref 100/12345/67/890 einde", "Dosering 1/2 tablet, 3/4 dosis"):
        labels = {span.label for span in find_regex_spans(text)}
        assert "RIZIV_INAMI" not in labels, f"{text!r} produced {labels}"


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
