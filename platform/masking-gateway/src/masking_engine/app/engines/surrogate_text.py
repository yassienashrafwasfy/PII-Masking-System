"""Keep a replacement safe to write back, and shaped like what it replaced."""

from __future__ import annotations

import re

# A replacement goes back into a TXT, CSV, JSON, or PDF body. These characters
# end a CSV field or a JSON string, so no replacement may contain one. A Faker
# company name, for example, can contain a comma.
_UNSAFE_IN_REPLACEMENT = re.compile(r'[,;"\\\r\n\t]')


def _sanitize(text: str, label: str) -> str:
    """Return *text* fit to write into a CSV field or a JSON string."""
    cleaned = _UNSAFE_IN_REPLACEMENT.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or f"[{label}]"


def _match_organization_case(candidate: str, original: str) -> str:
    """Return *candidate* cased to sit in the same sentence as *original*.

    `_match_case` is wrong for an organisation. It treats the whole name as
    one word, and both pools are register exports: the French one is written
    in capitals almost throughout, so every multi-word French organisation
    came back as "Sosan ime", and a Dutch name lost its own capitals the same
    way ("Zorg & Welzijn 010 (B.V.)" -> "Zorg & welzijn 010 (b.v.)").

    A name's internal capitals are part of it, so a candidate that already
    carries its own mixed case is left exactly as the register wrote it.
    """
    if original.strip().isupper():
        return candidate.upper()
    if candidate.isupper():
        # A register export in capitals is not a claim that the name is an
        # acronym, so it is title-cased to read as the proper noun it is.
        return candidate.title()
    return candidate


def _match_case(value: str, model: str) -> str:
    """Return *value* capitalized the way *model* is."""
    if model.isupper():
        return value.upper()
    if model[:1].isupper():
        return value.capitalize()
    return value
