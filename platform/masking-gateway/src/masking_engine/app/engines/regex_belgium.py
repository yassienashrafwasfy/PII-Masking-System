"""Belgian identifiers: the INSZ, the RIZIV/INAMI number and the BTW number."""

from __future__ import annotations

import re

_INSZ_RE = re.compile(
    r"(?<!\d)\d{2}[./ -]?\d{2}[./ -]?\d{2}[-. ]?\d{3}[-. ]?\d{2}(?!\d)"
)

_INSZ_CONTEXT_RE = re.compile(
    r"\b(?:INSZ|NISS|rijksregisternummer|nationaal nummer)\b"
    r"[^\r\n\d]{0,40}(?P<value>\d{2}[./ -]?\d{2}[./ -]?\d{2}"
    r"[-. ]?\d{3}[-. ]?\d{2})(?!\d)",
    re.IGNORECASE,
)

_RIZIV_CONTEXT_RE = re.compile(
    r"\b(?:RIZIV|INAMI)\b[^\r\n\d]{0,40}(?P<value>\d(?:[-./ ]?\d){7,10})(?!\d)",
    re.IGNORECASE,
)

# "/" is a delimiter here, not a separator, even though the value may be
# written with one inside it. A run packed as
# "78.03.12-123.45/1-23456-78-901/09 245 67 89" put a "/" on both sides of a
# canonical RIZIV number, and the old guards refused it from either end -- so
# the number left the document unmasked, the model detection for it having
# already been dropped as a regex-owned label. A digit, a dot and a hyphen
# still bound the match, which is what keeps it from starting or ending part
# way through a longer number.
_RIZIV_GROUPED_RE = re.compile(
    r"(?<![\d.-])(?P<value>\d[./\- ]\d{5}[./\- ]\d{2}(?:[./\- ]\d{3})?)(?![\d.-])"
)

_RIZIV_COMPACT_RE = re.compile(r"(?<!\d)(?:\d{8}|\d{11})(?!\d)")

_BTW_RE = re.compile(
    r"(?<![A-Z0-9])BE(?:\d{10}|[ .-]?\d{4}(?:[ .-]\d{3}){2})(?![A-Z0-9])",
    re.IGNORECASE,
)


def _btw_ok(number: str) -> bool:
    """Validate a Belgian enterprise/VAT number (BE + ten digits)."""
    digits = re.sub(r"\D", "", number)
    if len(digits) != 10:
        return False
    return 97 - (int(digits[:8]) % 97) == int(digits[8:])


def _insz_ok(number: str) -> bool:
    """Validate a Belgian national register number.

    The check value is ``97 - (base % 97)``, where ``base`` contains the first
    nine digits. The alternate formula adds ``2_000_000_000`` to the base for
    people born in or after 2000. Accept the number if either formula matches
    the final two digits.

    The checksum alone leaves roughly one in a hundred arbitrary digit runs
    looking valid, so the embedded birth date is checked too. That matters
    beyond precision: this function is half of `_is_national_identifier`, which
    keeps a national number out of the phone detector, and a false positive
    there suppresses a real telephone number.

    The rules follow the stricter validator Berts already ships, including its
    two hard-won cases. A bis number offsets the month by 20 or 40, and it is a
    genuine register number rather than a malformed one. A register that does
    not know a birth date writes the unknown part as zeroes -- month 00, day
    00, or both -- and a foreign or undocumented patient really does carry one.
    Rejecting those took the number out of INSZ detection entirely, after which
    a weaker pattern claimed part of it and the leading digits stayed in the
    clear.
    """
    number = re.sub(r"\D", "", number)
    if len(number) != 11:
        return False
    base = int(number[:9])
    check = int(number[9:])
    if (97 - (base % 97)) != check and (97 - ((base + 2_000_000_000) % 97)) != check:
        return False
    month = int(number[2:4])
    if 21 <= month <= 32:
        month -= 20
    elif 41 <= month <= 52:
        month -= 40
    if month and not 1 <= month <= 12:
        return False
    day = int(number[4:6])
    # February takes 29 unconditionally: the year is two digits, so the century
    # is unknown and a leap-year test would reject a real number.
    month_days = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if day > (month_days[month - 1] if month else 31):
        return False
    # The serial's documented 001-997 range is a numbering convention rather
    # than part of the mod-97 key, so only a zero serial is rejected.
    return int(number[6:9]) != 0


def _riziv_ok(number: str) -> bool:
    """Validate modern and legacy RIZIV numbers."""
    digits = re.sub(r"\D", "", number)
    if len(digits) not in {8, 11}:
        return False
    base = int(digits[:6])
    check = int(digits[6:8])
    return check in {89 - base % 89, 97 - base % 97}
