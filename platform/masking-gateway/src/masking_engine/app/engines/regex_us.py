"""United States identifiers: the SSN and the NPI."""

from __future__ import annotations

import re

# A US social security number, written in the 3-2-4 grouping that is the
# only shape the SSA prints. The bare form insists on separators, because a
# nine-digit run with none is not distinctive: it is also a Dutch chamber
# number, an order reference and half a telephone number. With the grouping
# and the allocation rules together the shape is specific enough to claim
# without a keyword, and the keyword form below then catches the compact
# spellings the bare rule deliberately leaves alone.
_SSN_RE = re.compile(r"(?<!\d)\d{3}[ -]\d{2}[ -]\d{4}(?!\d)")

_SSN_CONTEXT_RE = re.compile(
    r"\b(?:SSN|SSA|social security(?:\s+(?:number|no\.?|#))?"
    r"|soc\.?\s*sec\.?)\b[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){8})(?!\d)",
    re.IGNORECASE,
)

# A US National Provider Identifier. Keyword-only, like RPPS: the value is a
# bare ten-digit run, which is exactly what `_NHS_RE` claims, so a rule
# without a keyword would spend its life arbitrating against the NHS number
# rather than finding anything the NHS rule misses. The keyword is the
# evidence, so no checksum is required of the original -- the surrogate is
# still generated Luhn-valid, the way RPPS's is generated digit-for-digit.
_NPI_CONTEXT_RE = re.compile(
    r"\b(?:NPI|national provider identifier)(?:\s+(?:number|no\.?|#))?\b"
    r"[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){9})(?!\d)",
    re.IGNORECASE,
)


def _ssn_ok(number: str) -> bool:
    """Validate a US social security number against its allocation rules.

    There is no checksum, so the only test available is which blocks the SSA
    has never issued: area 000, area 666 and the whole 900-999 range, group
    00, and serial 0000. A 900-series run is an individual taxpayer number
    rather than an SSN, and the rest are the placeholders a form prints when
    it has no value -- so this rejects a useful share of look-alikes without
    claiming a certainty the format cannot give.

    Deliberately not part of `_is_national_identifier`: that union exists to
    keep a *checksummed* number out of the phone detector, and a test this
    weak would suppress genuine phone numbers by the dozen. An SSN outranks a
    phone number in `_LABEL_PRIORITY` instead.
    """
    digits = re.sub(r"\D", "", number)
    if len(digits) != 9:
        return False
    area = int(digits[:3])
    return (
        area not in {0, 666}
        and area < 900
        and int(digits[3:5]) != 0
        and int(digits[5:]) != 0
    )
