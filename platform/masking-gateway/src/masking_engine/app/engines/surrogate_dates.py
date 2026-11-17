"""Read a date in any accepted shape, and write one back in the same shape."""

from __future__ import annotations
from .surrogate_text import _match_case
from ..core.locales import LocaleProfile
from ..core import locales

import calendar
import os
import random
import re
from collections.abc import Callable
from typing import NamedTuple
from datetime import date, datetime

# Each date moves by a random 1-4 years, forward or back. The minimum
# guarantees the year itself changes -- `_redraw_date` already redraws the
# month and the day, so a one-year shift is a visible change and not a
# near-miss. The maximum is what keeps a masked date plausible: at the 50
# years this used to allow, an appointment made in 2026 came back reading
# 1977, and a two-digit-year field ("15/03/98" -> 1948) lost its century
# band as well.
#
# Four years is also why the minimum is not four. Were both bounds 4, the
# offset set would hold a single value, every date in every document would
# move by exactly the same amount, and adding four years back would undo the
# masking. Eight offsets is not a large keyspace either, but the month and
# the day are drawn independently on top of it.
DATE_MIN_YEARS = 1

DATE_MAX_YEARS = 4

# The environment variable that pins the date a value this engine cannot parse
# is shifted from. An ISO date; anything else is a configuration error and is
# left to raise rather than be silently ignored.
_DATE_ANCHOR_ENV = "MASKING_DATE_ANCHOR"


# A parsed date: its value, a renderer that restores the source spelling for any
# moved date, and whether the source year was written with two digits.
ParsedDate = tuple[date, Callable[[date], str], bool]


def unparsed_date_anchor() -> date:
    """Return the date an unreadable value is shifted from.

    Defaults to today, so the stand-in lands beside the dates it replaces
    rather than in whichever year this constant was last edited.

    Masking has to be byte-reproducible: NiFi replays an idempotency key, and
    the same document must answer with the same bytes. Today's date is not
    that -- an unreadable value masks to one date this year and another the
    next. Within the +/-4-year band above the drift is a year boundary rather
    than a day, but it is still drift, so a deployment that replays keys pins
    `MASKING_DATE_ANCHOR` and gets the settled behaviour back. Nothing
    downstream reads the anchor, so any fixed date will do there.
    """
    pinned = os.getenv(_DATE_ANCHOR_ENV, "").strip()
    if pinned:
        return date.fromisoformat(pinned)
    return date.today()  # noqa: DTZ011


# The space-separated spellings are here because they are separators in
# `regex_dates._DATE_SEP`: "01 02 1985" is detected as the date it is. Until
# the two sides agreed, such a date parsed under no pattern and took the
# unparsed fallback -- which renders a full ISO date, so a 1985 birth date
# arrived as "2027-07-27": the wrong shape, and shifted from today rather
# than from the year the document actually carried.
#
# One pattern covers the non-breaking space PDF extraction emits as well:
# `strptime` compiles any whitespace in a format to `\s+`, and `\s` matches
# NBSP. A separate NBSP pattern would therefore be unreachable -- whichever
# of the two came first would claim both spellings -- so the surrogate of an
# NBSP-separated date is written with an ordinary space, which is the same
# width and the same date.
#
# No two-digit-year spelling among them: the detector's space-separated
# branch requires a four-digit year, so a "%d %m %y" pattern here could only
# claim a value that rule never emits.
_NUMERIC_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %m %Y",
    "%Y %m %d",
    "%d/%m/%y",
    "%d-%m-%y",
)

# The same shapes written month-first, tried only after every day-first
# pattern has failed. So a date this engine already reads keeps reading the
# same way and Dutch and French output does not move; what these add is the
# dates that parse under no pattern at all today. There is no seventeenth
# month, so "02/17/2026" fell through to the unparsed fallback and left an
# English letter carrying "1994-11-19" where it had written a US date.
#
# Locale-independent on purpose, exactly as `locales.month_number` is: a
# Dutch note can quote an American date as easily as an English one, and
# which order parses is decided by the value rather than by the document.
# Where both orders work -- "03/04/2026" -- day-first wins, which is British
# usage as well as Belgian. The rendered shape is the matched pattern either
# way, so that choice moves which surrogate date lands in the field and
# nothing a reader could see.
_NUMERIC_MONTH_FIRST_DATE_FORMATS = (
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%m %d %Y",
    "%m/%d/%y",
    "%m-%d-%y",
)

# The clock spellings a timestamp is written with, longest first so a value
# carrying seconds is never read as one that stops at the minute and leaves
# ":30" behind. `regex_dates._ISO_DATETIME_RE` claims all four as a single
# DATE span; until they were listed here the two it did not cover -- the `T`
# separator and the seconds -- parsed under no pattern at all, fell through to
# the unparsed fallback, and came back as a bare ISO date with the time of day
# silently deleted and the year shifted off the anchor instead of off the
# value the document actually carried.
_TIME_SUFFIXES = (" %H:%M:%S", "T%H:%M:%S", " %H:%M", "T%H:%M")


def _with_times(patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        f"{pattern}{suffix}" for pattern in patterns for suffix in _TIME_SUFFIXES
    )


_NUMERIC_DATETIME_FORMATS = _with_times(_NUMERIC_DATE_FORMATS)

_NUMERIC_MONTH_FIRST_DATETIME_FORMATS = _with_times(_NUMERIC_MONTH_FIRST_DATE_FORMATS)

# A month and a year with no day. A clinical history is written this way --
# "04-2025 : début des douleurs" -- and with no pattern for it the value fell
# through to the unparsed fallback, which renders a full ISO date. That both
# put a day in the record that the source never claimed and made the value
# longer than the slot it had to fit, so it arrived on the page truncated
# ("1997-01…"). Tried after the full-date patterns, so a complete date is
# never read as a month.
_NUMERIC_MONTH_FORMATS = (
    "%m-%Y",
    "%m/%Y",
    "%m.%Y",
    "%Y-%m",
)

# Any unicode letter, not just `é`: a written French date carries `û` in
# "août" and `é` in "février", and an `[A-Za-zé]` class silently failed to
# parse both, which left the date to the digit-redraw fallback.
# `ordinal` catches the French "1er janvier" and the English "17th February",
# which are how those languages write those days and are therefore not edge
# cases in their own notes. Without it the whole date failed to parse and fell
# through to the numeric fallback, putting an ISO date in the middle of a
# sentence.
#
# `dot` is captured rather than skipped so an abbreviation that carried a
# period gets one back: the surrogate keeps the form it replaced.
_WRITTEN_DATE_RE = re.compile(
    r"^(?P<day>\d{1,2})(?P<ordinal>er|re|ere|ère|st|nd|rd|th)?"
    r"\s+(?P<month>[^\W\d_]+)(?P<dot>\.)?\s+(?P<year>\d{4})$",
    re.IGNORECASE | re.UNICODE,
)

# The month-first written form, which is how English most often writes a date
# out: "February 17, 2026", "Feb. 17 2026", "February 17th, 2026". Tried after
# the day-first form, so "17 February 2026" is unaffected.
#
# The comma is matched but deliberately not carried into the surrogate. A
# replacement may not contain one -- `_sanitize` turns it into a space so no
# replacement can end a CSV field or a JSON string -- so a rendered comma is
# stripped a moment later anyway, and rendering one would only make the
# renderer disagree with the bytes that actually leave. "November 3 2019" is
# the price of that rule; the order and the written month, which are what make
# the field read as English, both survive.
_WRITTEN_MONTH_DAY_YEAR_RE = re.compile(
    r"^(?P<month>[^\W\d_]+)(?P<dot>\.)?\s+(?P<day>\d{1,2})"
    r"(?P<ordinal>st|nd|rd|th)?,?\s+(?P<year>\d{4})$",
    re.IGNORECASE | re.UNICODE,
)

_WRITTEN_MONTH_YEAR_RE = re.compile(
    r"^(?P<month>[^\W\d_]+)(?P<dot>\.)?\s+(?P<year>\d{4})$",
    re.IGNORECASE | re.UNICODE,
)


def _redraw_date(value: date, years: int, generator: random.Random) -> date:
    """Shift a date by *years* and redraw its month and day.

    Keeping the source month and day intact would leave a fixed point in
    every masked date: a birthday stays a birthday even after its year
    moves. The day is drawn from the actual length of the chosen month in
    the target year (via `calendar.monthrange`), so a 30-day month or a
    non-leap February can never be handed an out-of-range day.

    A timestamp keeps its time of day. Only the date carries the identifying
    information, and returning a bare `date` for one dropped the clock: every
    "15-01-2024 14:21" in a document came back reading "00:00".
    """
    target_year = value.year + years
    month = generator.randint(1, 12)
    day = generator.randint(1, calendar.monthrange(target_year, month)[1])
    moved = date(target_year, month, day)
    if isinstance(value, datetime):
        return datetime.combine(moved, value.time())
    return moved


def _year_offsets(value: date, *, two_digit_year: bool) -> tuple[int, ...]:
    """Return the valid 1-to-4-year offsets for *value*, in either direction.

    Both directions, rather than the past with the future as a rescue: a
    clinical document dates appointments ahead as readily as it records
    history, and drawing from eight offsets instead of four is the difference
    between a shift that is guessable and one that is merely small.

    A date in the first years of the era has no offset into the past at all --
    every shift would land before year 1 -- and returning none of them left
    `random.choice` an empty sequence to pick from, so masking a document
    containing "01-01-0001" raised `IndexError` and failed the whole request
    rather than the one field. Filtering rather than branching means such a
    date simply keeps the forward half of the range.
    """
    span = range(DATE_MIN_YEARS, DATE_MAX_YEARS + 1)

    def usable(offset: int) -> bool:
        return 1 <= value.year + offset <= 9999 and (
            not two_digit_year or abs(offset) % 100 != 0
        )

    offsets = tuple(
        offset for years in span for offset in (-years, years) if usable(offset)
    )
    # Every year from 5 to 9995 keeps the whole range, so this is the two ends
    # of the calendar only -- but an OCR misread puts a date there, and an
    # empty sequence is an `IndexError` on a real request.
    return offsets or (0,)


def _render_numeric_date(value: date, pattern: str) -> str:
    """Format years below 1000 with all four digits on every platform."""
    marker = "__MASKING_YEAR__"
    return value.strftime(pattern.replace("%Y", marker)).replace(
        marker, f"{value.year:04d}"
    )


_YEAR_ONLY_RE = re.compile(r"\d{4}")


def _year_only(text: str) -> ParsedDate | None:
    """Return a bare year parsed, and a renderer that writes a bare year back.

    "sinds eind 2020" and "In 2003" carry no separator, so no numeric pattern
    reads them and they reached the unparsed fallback -- which renders a
    complete ISO date and left "sinds eind 2010-01-22" in a sentence that had
    written a year. DATE is deliberately kept out of the orchestrator's
    regex-exclusive set so the model can catch exactly these, which is what
    made the missing format reachable in the first place.

    Four digits and nothing else. `strptime` would read "45" as the year 45,
    and a two-digit span the model labelled DATE is far likelier to be an age
    than a year.
    """
    if not _YEAR_ONLY_RE.fullmatch(text):
        return None
    parsed = datetime.strptime(text, "%Y").date()  # noqa: DTZ007
    return parsed, lambda moved: f"{moved.year:04d}", False


# Every numeric spelling, in the order they are tried, paired with whether the
# pattern carries a time. Built once: the tuple is the same on every call.
_NUMERIC_PATTERNS: tuple[tuple[str, bool], ...] = (
    *((pattern, True) for pattern in _NUMERIC_DATETIME_FORMATS),
    *((pattern, False) for pattern in _NUMERIC_DATE_FORMATS),
    *((pattern, True) for pattern in _NUMERIC_MONTH_FIRST_DATETIME_FORMATS),
    *((pattern, False) for pattern in _NUMERIC_MONTH_FIRST_DATE_FORMATS),
    *((pattern, False) for pattern in _NUMERIC_MONTH_FORMATS),
)


def _numeric_date(text: str) -> ParsedDate | None:
    """Parse a numeric spelling, keeping the pattern that read it."""
    for pattern, has_time in _NUMERIC_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern)  # noqa: DTZ007
        except ValueError:
            continue
        return (
            parsed if has_time else parsed.date(),
            lambda moved, pattern=pattern: _render_numeric_date(moved, pattern),
            "%y" in pattern,
        )
    return None


# A numeric date whose two separators need not match. `regex_dates._DATE_SEP`
# is a character class tested afresh at each position, so the detector claims
# "02.07 2024" and "2024-03 12" as readily as it claims a consistently written
# date -- which is exactly what an OCR pass produces when it reads one dot of
# a scanned "02.07.2024" as a space. No `strptime` pattern can express "either
# separator here", so every such value parsed under none of them and took the
# unparsed fallback: rendered as a full ISO date, and shifted from the anchor
# instead of from the year the document carried, so a 1985 birth date came
# back sitting in the current decade.
#
# Tried only after every fixed pattern has failed, so no date this engine
# already reads changes how it is read. The separators are captured rather
# than normalised, because the surrogate has to go back into the field in the
# shape it came out of.
_MIXED_SEPARATOR_DATE_RE = re.compile(
    r"^(?P<first>\d{1,4})(?P<sep1>[./\-\u00a0 ])"
    r"(?P<second>\d{1,2})(?P<sep2>[./\-\u00a0 ])"
    r"(?P<third>\d{1,4})$"
)


def _mixed_separator_date(text: str) -> ParsedDate | None:
    """Parse a numeric date written with two different separators."""
    match = _MIXED_SEPARATOR_DATE_RE.match(text)
    if match is None:
        return None
    first, second, third = (
        int(match["first"]),
        int(match["second"]),
        int(match["third"]),
    )
    # Year-first where the leading field is written as four digits, exactly as
    # `regex_dates._valid_date` decides it; otherwise the year is last and the
    # two leading fields are tried day-first and then month-first, which is
    # the order every other reader in this module uses.
    order: tuple[tuple[int, int], ...]
    if len(match["first"]) == 4:
        year, order = first, ((second, third),)
    elif len(match["third"]) == 4:
        year, order = third, ((second, first), (first, second))
    else:
        # Neither end is a four-digit year. The detector's mixed-separator
        # branches both require one, so this is a run of digits that only
        # looks like a date -- left unparsed rather than read as year 24.
        return None
    for month, day in order:
        try:
            parsed = date(year, month, day)
        except ValueError:
            continue
        widths = (len(match["first"]), len(match["second"]), len(match["third"]))
        sep1, sep2 = match["sep1"], match["sep2"]
        year_first = len(match["first"]) == 4
        day_first = (month, day) == (second, first)

        def render(moved: date, _widths=widths, _sep=(sep1, sep2)) -> str:
            if year_first:
                fields = (moved.year, moved.month, moved.day)
            elif day_first:
                fields = (moved.day, moved.month, moved.year)
            else:
                fields = (moved.month, moved.day, moved.year)
            # Zero-padding follows the source field by field: a source that
            # wrote "2.7 2024" gets a surrogate written the same way.
            written = [f"{value:0{width}d}" for value, width in zip(fields, _widths)]
            return f"{written[0]}{_sep[0]}{written[1]}{_sep[1]}{written[2]}"

        return parsed, render, False
    return None


class _Written(NamedTuple):
    """A written-out date, and which of the three shapes matched it."""

    match: re.Match[str]
    day_written: bool
    month_first: bool


def _written_date(text: str) -> _Written | None:
    """Match a written-out spelling, in the order that keeps them distinct."""
    match = _WRITTEN_DATE_RE.match(text)
    if match is not None:
        return _Written(match, day_written=True, month_first=False)
    # The month-first form, which is what an English letter writes.
    match = _WRITTEN_MONTH_DAY_YEAR_RE.match(text)
    if match is not None:
        return _Written(match, day_written=True, month_first=True)
    # The same month-and-year shape, written out: "mars 1962".
    match = _WRITTEN_MONTH_YEAR_RE.match(text)
    if match is not None:
        return _Written(match, day_written=False, month_first=False)
    return None


def parse_date(text: str, locale: LocaleProfile) -> ParsedDate | None:
    """Return the parsed date and a renderer that restores its format."""
    numeric = _numeric_date(text)
    if numeric is not None:
        return numeric

    mixed = _mixed_separator_date(text)
    if mixed is not None:
        return mixed

    written = _written_date(text)
    if written is None:
        # Tried last, so nothing that parses under a pattern above changes.
        return _year_only(text)
    match, day_written, month_first = written
    month = locales.month_number(match["month"])
    if month is None:
        return None
    try:
        parsed = date(
            int(match["year"]), month, int(match["day"]) if day_written else 1
        )
    except ValueError:
        return None
    # Not `len(month) <= 4`: "mars", "mai", "juin", "août", "mei", "juni"
    # and "juli" are whole months no longer than an abbreviation, and the
    # length test rendered every one of them back abbreviated.
    written_month = match["month"]
    abbreviated = not locales.is_full_month_name(written_month)
    dotted = match["dot"] is not None
    # Only the two day-carrying patterns have this group, so it is not
    # read on the month-and-year fallback.
    source_ordinal = day_written and match["ordinal"] is not None

    months = locale.months
    # French writes "1er" whether or not the source did, because
    # "1 janvier" is a misspelling. English writes "3rd" only where the
    # source wrote an ordinal of its own, because "3 November" is ordinary.
    write_ordinal = source_ordinal or locale.ordinal_is_mandatory
    day_ordinal = locale.day_ordinal

    def render(moved: date) -> str:
        name = months[moved.month - 1]
        if abbreviated:
            name = name[:3]
        # `_match_case` rather than a capitalize-only test, so a month
        # written in a caps header comes back in caps instead of losing
        # the case of the line it sits on.
        name = _match_case(name, written_month)
        if dotted:
            name += "."
        # The suffix belongs to the day it is written after, so it
        # follows the redrawn day rather than the source one: a French
        # first of the month keeps "er" only if the surrogate also lands
        # on the first, and an English "1st" becomes "3rd".
        if not day_written:
            # No day in the source, so none in the surrogate.
            return f"{name} {moved.year}"
        day = f"{moved.day}{day_ordinal(moved.day)}" if write_ordinal else moved.day
        if month_first:
            return f"{name} {day} {moved.year}"
        return f"{day} {name} {moved.year}"

    return parsed, render, False
