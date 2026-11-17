"""United Kingdom identifiers: the NHS number and the GMC reference."""

from __future__ import annotations

import re
from .regex_shared import _digit_run

_NHS_RE = re.compile(_digit_run(10))

_NHS_CONTEXT_RE = re.compile(
    r"\bNHS(?:\s+(?:number|no\.?))?\b[^\r\n\d]{0,40}"
    r"(?P<value>\d(?:[ .\-/]?\d){9})(?!\d)",
    re.IGNORECASE,
)

_GMC_CONTEXT_RE = re.compile(
    # The gap is lazy so that an associate's "A" prefix lands inside the value
    # rather than being swallowed as part of the gap.
    r"\bGMC(?:\s+(?:number|no\.?|reference))?\b[^\r\n\d]{0,40}?"
    r"(?P<value>A?\d(?:[ .\-/]?\d){6})(?!\d)",
    re.IGNORECASE,
)


def _nhs_ok(number: str) -> bool:
    """Validate an NHS number: mod-11 over nine digits, weights ten down to two.

    A remainder giving ten has no valid check digit, so such a candidate is
    rejected rather than coerced. A leading zero is rejected too: no allocated
    NHS number range opens with one, and without that rule one telephone number
    in eleven satisfies the checksum.
    """
    digits = re.sub(r"\D", "", number)
    if digits[:1] == "0":
        return False
    return _nhs_checksum_ok(digits)


def _nhs_checksum_ok(number: str) -> bool:
    """The mod-11 check alone, without the leading-zero rule."""
    digits = re.sub(r"\D", "", number)
    if len(digits) != 10:
        return False
    total = sum(int(char) * (10 - index) for index, char in enumerate(digits[:9]))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    elif check == 10:
        return False
    return check == int(digits[9])
