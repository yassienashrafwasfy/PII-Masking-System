"""German identifiers: KVNR, Steuer-ID and LANR."""

from __future__ import annotations

import re

_HEALTH_INSURANCE_DE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{9}(?![A-Za-z0-9])")
_STEUER_ID_RE = re.compile(r"(?<!\d)\d(?:[ .\-/]?\d){10}(?!\d)")
_STEUER_ID_CONTEXT_RE = re.compile(
    r"\b(?:Steuer[- ]?ID|Steueridentifikationsnummer|Steuernummer|IdNr\.?)\b"
    r"[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){10})(?!\d)",
    re.IGNORECASE,
)
_LANR_CONTEXT_RE = re.compile(
    r"\b(?:LANR|lebenslange\s+Arztnummer|Arzt(?:nummer|kennung)|Kennung"
    r"|Behandlernummer|Anbieternummer)\b"
    r"[^\r\n\d]{0,40}(?P<value>\d(?:[ .\-/]?\d){8})(?!\d)",
    re.IGNORECASE,
)


def _kvnr_ok(value: str) -> bool:
    """Validate a German insurance number: mod-10 over the expanded letter.

    The leading letter becomes 01 through 26, giving ten digits weighted
    alternately one and two, with a doubled result above nine reduced by nine.
    """
    text = "".join(char for char in value.upper() if char.isalnum())
    if len(text) != 10 or not text[0].isalpha() or not text[1:].isdigit():
        return False
    expanded = f"{ord(text[0]) - ord('A') + 1:02d}{text[1:9]}"
    total = 0
    for index, char in enumerate(expanded):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == int(text[9])


def _steuer_id_ok(value: str) -> bool:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 11 or digits[0] == "0":
        return False
    body = digits[:10]
    counts = {digit: body.count(digit) for digit in set(body)}
    repeats = [digit for digit, count in counts.items() if count > 1]
    if len(repeats) != 1 or counts[repeats[0]] > 3:
        return False
    product = 10
    for char in body:
        total = (int(char) + product) % 10
        product = (10 if total == 0 else total) * 2 % 11
    return (11 - product) % 10 == int(digits[10])


def _lanr_ok(value: str) -> bool:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 9:
        return False
    return sum(
        int(char) * weight
        for char, weight in zip(digits[:6], (4, 9, 4, 9, 4, 9), strict=True)
    ) % 10 == int(digits[6])
