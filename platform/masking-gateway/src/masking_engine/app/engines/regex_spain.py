"""Spanish DNI, NIE and professional-register identifiers."""

from __future__ import annotations

import re

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_DNI_RE = re.compile(
    r"(?<![A-Za-z0-9])\d{2}[. ]?\d{3}[. ]?\d{3}[- ]?[A-Za-z](?![A-Za-z0-9])"
)
_NIE_RE = re.compile(
    r"(?<![A-Za-z0-9])[XYZ][- .]?\d{3}[. ]?\d{4}[- ]?[A-Za-z](?![A-Za-z0-9])",
    re.IGNORECASE,
)
_DNI_CONTEXT_RE = re.compile(
    r"\b(?:DNI|documento\s+nacional\s+de\s+identidad)\b[^\r\n\d]{0,40}(?P<value>\d{2}[. ]?\d{3}[. ]?\d{3}[- ]?[A-Za-z])(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_COLEGIADO_CONTEXT_RE = re.compile(
    r"\b(?:N[.º°\s]*COL(?:EGIADO)?|n[uú]mero\s+de\s+colegiado|colegiado|profesional\s+sanitario)\b[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){8})(?!\d)",
    re.IGNORECASE,
)


def _spanish_id(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum())


def _dni_ok(value: str) -> bool:
    text = _spanish_id(value)
    return (
        len(text) == 9
        and text[:8].isdigit()
        and text[0] not in "XYZ"
        and _DNI_LETTERS[int(text[:8]) % 23] == text[8]
    )


def _nie_ok(value: str) -> bool:
    text = _spanish_id(value)
    prefixes = {"X": "0", "Y": "1", "Z": "2"}
    return (
        len(text) == 9
        and text[:1] in prefixes
        and text[1:8].isdigit()
        and _DNI_LETTERS[int(prefixes[text[0]] + text[1:8]) % 23] == text[8]
    )
