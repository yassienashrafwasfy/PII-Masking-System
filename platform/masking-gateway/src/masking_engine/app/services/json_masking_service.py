"""Mask character offsets in valid UTF-8 JSON while preserving its structure."""

from __future__ import annotations

import json

from ..core.error_codes import CorruptInputError
from ..core.schemas import MaskSpan
from ..engines.masking_router import MaskingRouter
from .text_masking_service import apply_replacements, decode_utf8


def mask_json(content: bytes, spans: list[MaskSpan], router: MaskingRouter) -> bytes:
    text = decode_utf8(content, "JSON")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptInputError("JSON input is not syntactically valid.") from exc

    masked = apply_replacements(text, spans, router).encode("utf-8")
    # The router strips the quote and the backslash from every replacement, so
    # a replacement cannot end a JSON string. This check still runs, because a
    # span that starts or ends outside a string would break the document and
    # the caller must not receive an unparsable file.
    try:
        json.loads(masked.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CorruptInputError("Masking would produce invalid JSON.") from exc
    return masked
