"""Age and sex, each claimed only in a slot that names it."""

from __future__ import annotations

import re
from ..core.schemas import MaskSpan

_AGE_CONTEXT_RE = re.compile(
    r"(?P<value>\d{1,3})\s*-?\s*(?:jarige|jaar\b|years?\b|yrs?\b|y/o\b|ans\b)"
    r"|\b(?:age|leeftijd|aged|âg[eé]e?\s+de)\b[^\r\n\d]{0,10}"
    r"(?P<value2>\d{1,3})(?!\d)",
    re.IGNORECASE,
)

# "(41, M)" beside a name is a real convention in clinical correspondence. It is
# the one slot pattern here that is a convention rather than a checksum or a
# unit, and it is the only way a bare age or sex is reachable at all.
_DEMOGRAPHIC_SLOT_RE = re.compile(
    r"\(\s*(?P<age>\d{1,3})\s*,\s*(?P<sex>[MFVH]|male|female|man|vrouw"
    r"|homme|femme)\s*\)",
    re.IGNORECASE,
)

_SEX_CONTEXT_RE = re.compile(
    r"\b(?:sex|sexe|geslacht|gender)\b[^\r\n\w]{0,10}"
    r"(?P<value>[MFVH]|male|female|man|vrouw|homme|femme|mannelijk|vrouwelijk)"
    r"(?![\w])",
    re.IGNORECASE,
)


def _demographic_spans(text: str) -> list[MaskSpan]:
    """Detect an age or a sex where a cue word or a conventional slot names it."""
    spans: list[MaskSpan] = []
    for m in _AGE_CONTEXT_RE.finditer(text):
        group = "value" if m.group("value") is not None else "value2"
        start, end = m.span(group)
        spans.append(MaskSpan(start, end, "AGE", "regex"))
    for m in _SEX_CONTEXT_RE.finditer(text):
        start, end = m.span("value")
        spans.append(MaskSpan(start, end, "SEX", "regex"))
    for m in _DEMOGRAPHIC_SLOT_RE.finditer(text):
        spans.append(MaskSpan(*m.span("age"), "AGE", "regex"))
        spans.append(MaskSpan(*m.span("sex"), "SEX", "regex"))
    return spans
