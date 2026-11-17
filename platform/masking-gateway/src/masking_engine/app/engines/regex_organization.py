"""Organisation names, claimed on a legal-form suffix."""

from __future__ import annotations

import re
from ..core.schemas import MaskSpan

# Additive ORGANIZATION_NAME backstop, unioned with the model's detections.
# A name carrying an organizational marker is an organization by definition:
# a token ending in uppercase ORG ("AZORG"; case keeps "zorg" words out),
# the word "org" after a capitalized name, or a legal-form suffix from the
# deployment locales (NL/FR/DE/UK/ES/RO). Unmarked names are the model's job.
_ORGANIZATION_NAME_RE = re.compile(
    r"(?:\b[A-ZÀ-Ý][\wÀ-ÿ'.-]*ORG\b"
    r"|\b(?:[A-ZÀ-Ý][\wÀ-ÿ'.-]*[^\S\r\n]+){1,3}[Oo][Rr][Gg]\b"
    r"|\b(?:[A-ZÀ-Ý][\wÀ-ÿ'.-]*[^\S\r\n]+|&[^\S\r\n]+){1,5}"
    r"(?:BVBA|CVBA|SPRL|SARL|SASU|SLL|GmbH|GbR|OHG|Corp|LLC|LLP|PLC|Inc|"
    r"NV|BV|VZW|EESV|ASBL|SRL|SNC|SAS|EURL|SA|UG|AG|KG|eG|eK|"
    r"Ltd|SL|Co|Cie|Zn)\b)"
)


def _organization_name_spans(text: str) -> list[MaskSpan]:
    """Detect a facility name by its designator word, additive to the model."""
    return [
        MaskSpan(m.start(), m.end(), "ORGANIZATION_NAME", "regex")
        for m in _ORGANIZATION_NAME_RE.finditer(text)
    ]
