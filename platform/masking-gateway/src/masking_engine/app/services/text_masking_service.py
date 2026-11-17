"""Replace each text span with the text its masking algorithm returned."""

from __future__ import annotations

from ..core.error_codes import CorruptInputError
from ..core.schemas import MaskSpan
from ..engines.masking_router import MaskingRouter
from ..engines.regex_arbitration import coalesce_spans


def decode_utf8(content: bytes, kind: str) -> str:
    """Return *content* as text, or reject it as unmaskable."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorruptInputError(f"{kind} input must be valid UTF-8.") from exc


def apply_replacements(text: str, spans: list[MaskSpan], router: MaskingRouter) -> str:
    """Return *text* with every span replaced by its routed value.

    A replacement rarely has the length of the value it covers, so every offset
    is read against the original text and the result is assembled once. Slicing
    a new string per span instead copied the whole document each time, which is
    quadratic in the span count: 4000 spans over an 800 KB body copied 3.3 GB.

    ``coalesce_spans`` returns non-overlapping spans in ascending order, so the
    untouched text between two replacements is one slice and no offset needs
    adjusting. The audit list records front to back; `MaskingRouter` seeds each
    surrogate from the value rather than from a running count, so a replacement
    does not depend on how many came before it.
    """
    ordered = coalesce_spans(spans)
    for span in ordered:
        if not 0 <= span.start < span.end <= len(text):
            raise CorruptInputError("A text span is outside the input text.")

    pieces: list[str] = []
    cursor = 0
    for span in ordered:
        original = text[span.start : span.end]
        replacement = router.replacement(span, original)
        router.record(span, original, replacement.text)
        pieces.append(text[cursor : span.start])
        pieces.append(replacement.text)
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def mask_txt(content: bytes, spans: list[MaskSpan], router: MaskingRouter) -> bytes:
    text = decode_utf8(content, "TXT")
    return apply_replacements(text, spans, router).encode("utf-8")
