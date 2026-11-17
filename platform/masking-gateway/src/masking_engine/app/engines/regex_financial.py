"""Payment card numbers, and the Luhn check they are claimed on."""

from __future__ import annotations

import re

_CREDITCARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

_CREDITCARD_CONTEXT_RE = re.compile(
    r"\b(?:creditcard|creditcardnummer|kaartnummer)\b[^\r\n\d]{0,40}"
    r"(?P<value>(?:\d[ -]?){13,19})\b",
    re.IGNORECASE,
)


def _luhn_ok(number: str) -> bool:
    """Check a digit string with the Luhn algorithm."""
    digits = [int(c) for c in number]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0
