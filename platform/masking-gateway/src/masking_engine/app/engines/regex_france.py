"""French identifiers: the NIR and the RPPS/ADELI number."""

from __future__ import annotations

import re
from .regex_shared import _digit_run

_NIR_RE = re.compile(_digit_run(15))

_NIR_CONTEXT_RE = re.compile(
    r"\b(?:NIR|INSEE|num[eé]ro de s[eé]curit[eé] sociale|NSS)\b"
    r"[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){14})(?!\d)",
    re.IGNORECASE,
)

_RPPS_CONTEXT_RE = re.compile(
    r"\b(?:RPPS|ADELI)\b[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){8,10})(?!\d)",
    re.IGNORECASE,
)


def _nir_ok(number: str) -> bool:
    """Validate a French social security number: mod-97 over the first thirteen.

    Corsican numbers write ``2A`` or ``2B`` in the department slot and are not
    matched by the numeric pattern above, so they are a known gap rather than a
    silently wrong result.
    """
    digits = re.sub(r"\D", "", number)
    if len(digits) != 15:
        return False
    return 97 - (int(digits[:13]) % 97) == int(digits[13:])
