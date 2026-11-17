"""Dates: numeric, ISO and written, in either day-first or month-first order."""

from __future__ import annotations

import re
from datetime import date
from ..core.schemas import MaskSpan

# The full-date branches accept a space or non-breaking space between the
# parts, so "01 02 1985" is read as the date it is. Without that it was not
# matched here at all and reached the phone detector, which masked it as a
# telephone number.
#
# The space is deliberately NOT extended to the trailing month-year branch
# (`\d{1,2} \d{4}`): that shape is too loose to claim on a space alone, and
# would swallow a trailing digit group of a phone number or a quantity next to
# a year. A full date is safe because it needs three parts in calendar order
# and is checked by `_valid_date` afterwards.
_DATE_SEP = "[./\\-\\u00a0 ]"
_FULL_OR_MONTH_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}" + _DATE_SEP + r"\d{1,2}" + _DATE_SEP + r"\d{1,2}|"
    r"\d{1,2}" + _DATE_SEP + r"\d{1,2}" + _DATE_SEP + r"\d{4}|"
    r"\d{1,2}[./-]\d{4})(?!\d)"
)

# An ISO timestamp is one date, not a date beside a time, so it is matched whole
# and the shorter date match inside it is dropped.
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?!\d)"
)

# Month names for the three deployment locales, plus the abbreviations source
# systems emit. No name is ambiguous across the three languages.
_MONTHS = {
    "januari": 1,
    "january": 1,
    "janvier": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "fevrier": 2,
    "février": 2,
    "feb": 2,
    "fev": 2,
    "fév": 2,
    "maart": 3,
    "march": 3,
    "mars": 3,
    "mar": 3,
    "mrt": 3,
    "april": 4,
    "avril": 4,
    "apr": 4,
    "avr": 4,
    "mei": 5,
    "may": 5,
    "mai": 5,
    "juni": 6,
    "june": 6,
    "juin": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "juillet": 7,
    "jul": 7,
    "juil": 7,
    "augustus": 8,
    "august": 8,
    "aout": 8,
    "août": 8,
    "aug": 8,
    "september": 9,
    "septembre": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "october": 10,
    "octobre": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "novembre": 11,
    "nov": 11,
    "december": 12,
    "decembre": 12,
    "décembre": 12,
    "dec": 12,
    "déc": 12,
}

# Longest first, so "juillet" is never truncated to "jul" by the alternation.
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ORDINALS = r"(?:st|nd|rd|th|er|re|e|ste|de)"

_TEXTUAL_DATE_RE = re.compile(
    rf"(?<!\w)(?:"
    rf"(?P<day1>\d{{1,2}}){_ORDINALS}?\s+(?P<month1>{_MONTH_NAMES})\.?"
    rf"\s+(?P<year1>\d{{4}})"
    rf"|(?P<month2>{_MONTH_NAMES})\.?\s+"
    rf"(?:(?P<day2>\d{{1,2}}){_ORDINALS}?,?\s+)?(?P<year2>\d{{4}})"
    rf")(?!\w)",
    re.IGNORECASE,
)


def _is_real_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _valid_date(candidate: str) -> bool:
    """Say whether *candidate* is a real calendar date in either field order.

    Day-first is tried first, so every date this rule already claimed is
    claimed the same way. Month-first is then tried, because "05/30/1968" is a
    real date read the American way and no date at all read the Belgian one --
    and a date the rule fails to claim is a date left in the released
    document. The router reads the two orders in the same sequence, so a value
    detected here is a value it can render back in the shape it found.
    """
    # Split on the same separator set the pattern above matches, space and
    # non-breaking space included; otherwise a space-separated date arrives
    # here as one unparseable part and is rejected as invalid.
    parts = re.split(_DATE_SEP, candidate)
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return False
    if len(numbers) == 3:
        if len(parts[0]) == 4:
            year, month, day = numbers
            return _is_real_date(year, month, day)
        first, second, year = numbers
        return _is_real_date(year, second, first) or _is_real_date(year, first, second)
    if len(numbers) == 2:
        month, year = numbers
        return 1 <= month <= 12 and 1900 <= year <= 2100
    return False


def starts_with_calendar_date(value: str) -> tuple[int, int] | None:
    """Return the span of a real calendar date *value* opens with, or None.

    A digit run that begins with a date is not a phone number and not a
    national identifier, however long it runs on. The loose shapes -- the
    phone pattern above all -- start matching inside a date and carry on
    through the separator into the leading digits of a time written beside
    it, so "05/05/1985 - 14" arrives at the phone detector as one candidate.
    Because that candidate is *longer* than the date, arbitration (which
    ranks length before label priority) drops the DATE span outright and the
    value is masked by shifting each digit independently -- which is how a
    1985 appointment came back reading 5570.

    The whole-value calendar test the phone detector already applies cannot
    see this: "05/05/1985 - 14" is not a calendar shape, only a calendar
    shape with a time stuck to it. This one is anchored at the start and
    confirmed by `_valid_date`, so it claims a prefix rather than the whole
    run, and a dialling style that merely contains digits ("03.502.688",
    "07700.06.40.43") still matches nothing here.
    """
    match = _FULL_OR_MONTH_DATE_RE.match(value)
    if match is None or not _valid_date(match.group(0)):
        return None
    return match.span()


def _textual_date_ok(match: re.Match[str]) -> bool:
    month_name = (match.group("month1") or match.group("month2")).lower()
    month = _MONTHS.get(month_name.rstrip("."))
    if month is None:
        return False
    year = int(match.group("year1") or match.group("year2"))
    day = match.group("day1") or match.group("day2")
    if day is None:
        return 1900 <= year <= 2100
    try:
        date(year, month, int(day))
    except ValueError:
        return False
    return True


def _date_spans(text: str) -> list[MaskSpan]:
    """Detect numeric, ISO-timestamp and month-name dates."""
    spans: list[MaskSpan] = []
    iso_ranges: list[tuple[int, int]] = []
    for m in _ISO_DATETIME_RE.finditer(text):
        iso_ranges.append((m.start(), m.end()))
        spans.append(MaskSpan(m.start(), m.end(), "DATE", "regex"))

    for m in _FULL_OR_MONTH_DATE_RE.finditer(text):
        inside_iso = any(
            start <= m.start() and m.end() <= end for start, end in iso_ranges
        )
        if not inside_iso and _valid_date(m.group(0)):
            spans.append(MaskSpan(m.start(), m.end(), "DATE", "regex"))

    for m in _TEXTUAL_DATE_RE.finditer(text):
        if _textual_date_ok(m):
            spans.append(MaskSpan(m.start(), m.end(), "DATE", "regex"))
    return spans
