"""Romanian identifiers: CNP and PARAFA."""

from __future__ import annotations

import re

_CNP_WEIGHTS = (2, 7, 9, 1, 4, 6, 3, 5, 8, 2, 7, 9)
_CNP_RE = re.compile(r"(?<!\d)\d(?:[ .\-/]?\d){12}(?!\d)")
_CNP_CONTEXT_RE = re.compile(
    r"\b(?:CNP|cod(?:ul)?\s+numeric\s+personal)\b[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){12})(?!\d)",
    re.IGNORECASE,
)
_PARAFA_CONTEXT_RE = re.compile(
    r"\b(?:PARAFA|cod(?:ul)?\s+(?:de\s+)?paraf[aă]|identificator(?:ul)?)\b[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){4})(?!\d)",
    re.IGNORECASE,
)


def _cnp_ok(value: str) -> bool:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 13 or digits[0] == "0":
        return False
    check = (
        sum(
            int(char) * weight
            for char, weight in zip(digits[:12], _CNP_WEIGHTS, strict=True)
        )
        % 11
    )
    return (1 if check == 10 else check) == int(digits[12])
