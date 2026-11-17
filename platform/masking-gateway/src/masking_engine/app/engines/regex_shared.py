"""Primitives every regex detector module builds on."""

from __future__ import annotations

import re


def _digit_run(count: int) -> str:
    """A run of exactly *count* digits, optionally broken by separators.

    Written as a helper rather than by hand for each scheme so that every
    numeric detector accepts the same punctuation. The bounds are plain digit
    lookarounds: a separator-aware lookahead would reject a valid identifier
    followed by an unrelated number on the same line, and the checksum is a
    better filter than the boundary.
    """
    return rf"(?<!\d)\d(?:[ .\-/]?\d){{{count - 1}}}(?!\d)"


def _after_phone_prefix(text: str, start: int) -> bool:
    """True if a written country code sits immediately before *start*.

    A subscriber run following "+44 " or "0032-" is the rest of a telephone
    number. Without this the tail of an international number satisfies a
    national scheme's checksum often enough to matter.
    """
    return (
        re.search(
            r"(?:\+|\b00)\s?\d{0,4}[\s.\-/()]{0,3}$",
            text[max(0, start - 10) : start],
        )
        is not None
    )


def _trim_right(text: str, start: int, end: int, junk: str) -> int:
    """Return *end* moved left past any trailing *junk* characters."""
    while end > start and text[end - 1] in junk:
        end -= 1
    return end


# National-identifier phrasing immediately before a digit run means the run is
# an ID, not a phone -- even when the shape matches a mobile. Measured on
# multilingual_synthetic test (2026-08-25): 18 of 21 TELEFOON false positives
# carried one of these cues ("national ID 0954569369", "Their NHS number is
# 0676033164", "insurance 0220147299").
_IDENTIFIER_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"national\s+(?:ID|identification(?:\s+number)?)"
    r"|identification\s+number"
    r"|identifier"
    r"|NHS\s+(?:number|no\.?)"
    r"|insurance(?:\s+number|\s+ID)?"
    r"|BSN"
    r"|rijksregisternummer"
    r"|social\s+security"
    r")\b"
    r"[^\r\n.]{0,24}$",
    re.IGNORECASE,
)


def _has_identifier_context(text: str, start: int) -> bool:
    return _IDENTIFIER_CONTEXT_RE.search(text[max(0, start - 60) : start]) is not None
