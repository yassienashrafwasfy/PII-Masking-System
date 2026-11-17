"""Postcodes and house numbers, across the locales the service accepts."""

from __future__ import annotations

import re
from ..core.schemas import MaskSpan
from .regex_dates import _TEXTUAL_DATE_RE
from .regex_shared import _trim_right

# A United Kingdom postcode is distinctive enough to stand alone. The inward
# code excludes the letters the Royal Mail specification forbids.
_UK_POSTCODE_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{1,2}\d[A-Z\d]?\s?\d[ABD-HJLNP-UW-Z]{2}(?![A-Za-z0-9])"
)

# Four or five bare digits are a year as often as a postcode, so the Belgian and
# French forms are accepted only where an address puts them: after a comma or at
# the start of a line, and immediately before a capitalised place name.
_EURO_POSTCODE_RE = re.compile(
    # The place name may open with an elided article -- "'S Herenelderen",
    # "'s-Hertogenbosch" -- so the capital is allowed to follow an apostrophe,
    # and may stand alone before the rest of the name.
    r"(?:^|[\n,]\s*)(?P<value>\d{4,5}(?!\d)(?:\s+CEDEX(?:\s+\d{1,2})?)?)"
    r"(?=\s+['’]?[A-ZÀ-Þ][A-Za-zÀ-ÿ’'-]*|\s*$)",
    re.MULTILINE,
)

# An address line: a street part, a comma, then a part carrying the postcode.
# Both halves are bounded so a long prose sentence containing a comma cannot
# masquerade as one.
_ADDRESS_LINE_RE = re.compile(
    r"^[ \t]*(?P<pre>[^\n,]{3,70}?)[ \t]*,[ \t]*(?P<post>[^\n]{3,70}?)[ \t]*$",
    re.MULTILINE,
)

# A US ZIP, which an address writes after the state: "Chicago, IL 60601". The
# state is required and is matched against the real list rather than any two
# capitals, because five digits are a year, a quantity and a reference number
# far more often than they are a postcode, and a two-letter prefix alone would
# have claimed "ER 60601" and "OK 12345" out of ordinary prose.
#
# Without this, `carries_postcode` did not recognise a US address line at all,
# so `_ADDRESS_LEADING_NUMBER_RE` never ran on one and the house number of
# "482 Lakeshore Drive, Chicago, IL 60601" was left in the released document
# while the street, city and ZIP around it were masked.
_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC AS GU MP PR VI"
).split()

_US_ZIP_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(_US_STATES) + r")\s+"
    r"(?P<value>\d{5}(?:-\d{4})?)(?![0-9])"
)

_BARE_POSTCODE_RE = re.compile(r"\d{4,5}(?!\d)")

# A house number may carry a single letter suffix ("12A") or a bis/ter marker.
_ADDRESS_LEADING_NUMBER_RE = re.compile(
    r"^[ \t]*(?P<value>\d{1,4}\s?(?:[A-Za-z](?![A-Za-z])|bis|ter)?)(?=\s+\S)"
)

_ADDRESS_TRAILING_NUMBER_RE = re.compile(
    r"(?<=\s)(?P<value>\d{1,4}\s?(?:[A-Za-z](?![A-Za-z])|bis|ter)?)[ \t]*$"
)

_POSTCODE_CONTEXT_RE = re.compile(
    r"\b(?:postcode|postal\s+code|code\s+postal|zip(?:\s*code)?)\b"
    r"[^\r\n\w]{0,10}(?-i:(?P<value>(?=[A-Z0-9 -]{0,10}\d)"
    r"[A-Z0-9][A-Z0-9 -]{2,8}[A-Z0-9]))(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _postcode_spans(text: str) -> list[MaskSpan]:
    """Detect postcodes: the UK form standalone, the others in an address slot."""
    spans: list[MaskSpan] = []
    # "April 14th, 2023" puts a bare four-digit year immediately after a comma,
    # which is precisely the address slot the Belgian and French forms look for,
    # so every year written that way was being masked as a postcode. Reuse the
    # date matcher rather than re-deriving the year shape here.
    date_ranges = [m.span() for m in _TEXTUAL_DATE_RE.finditer(text)]
    for m in _US_ZIP_RE.finditer(text):
        start, end = m.span("value")
        spans.append(MaskSpan(start, end, "ZIPCODE", "regex"))

    for m in _UK_POSTCODE_RE.finditer(text):
        spans.append(MaskSpan(m.start(), m.end(), "ZIPCODE", "regex"))
    for m in _EURO_POSTCODE_RE.finditer(text):
        start, end = m.span("value")
        if any(ds <= start and end <= de for ds, de in date_ranges):
            continue
        spans.append(MaskSpan(start, end, "ZIPCODE", "regex"))
    for m in _POSTCODE_CONTEXT_RE.finditer(text):
        start, end = m.span("value")
        end = _trim_right(text, start, end, " -")
        spans.append(MaskSpan(start, end, "ZIPCODE", "regex"))
    return spans


def _building_number_spans(text: str) -> list[MaskSpan]:
    """Detect the house number in an address line, anchored on the postcode.

    A bare number is a house number only because of where it sits, so this
    looks for the whole address line rather than the number: a street part, a
    comma, then a part carrying a postcode. Both orders occur --

        Kerkplein 59, 3271 Averbode        (nl-BE, en-GB: number after street)
        62 allee du Marche, 95190 Goussainville   (fr-FR: number first)

    -- so the street part is searched from whichever end does not hold the
    street name. Anchoring on the postcode is what keeps ward numbers, room
    numbers and procedure codes out: "Room 214" and "Procedure code 145.30"
    carry no postcode and never match.
    """

    def carries_postcode(part: str) -> bool:
        return bool(
            _UK_POSTCODE_RE.search(part)
            or _US_ZIP_RE.search(part)
            or _BARE_POSTCODE_RE.match(part.strip())
        )

    spans: list[MaskSpan] = []
    for m in _ADDRESS_LINE_RE.finditer(text):
        if not carries_postcode(m.group("post")):
            # A long address wraps, leaving the postcode on the next line:
            # "Springfield Street 43, Preston upon the Weald Moors\nTF6 6WH".
            next_start = m.end() + 1
            next_end = text.find("\n", next_start)
            following = text[next_start : next_end if next_end != -1 else len(text)]
            if not carries_postcode(following):
                continue
        head_start = m.start("pre")
        head = m.group("pre")
        lead = _ADDRESS_LEADING_NUMBER_RE.match(head)
        trail = None if lead else _ADDRESS_TRAILING_NUMBER_RE.search(head)
        hit = lead or trail
        if hit is None:
            continue
        start, end = hit.span("value")
        spans.append(
            MaskSpan(head_start + start, head_start + end, "BUILDINGNUM", "regex")
        )
    return spans
