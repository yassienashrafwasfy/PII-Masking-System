"""mDeBERTa's public gateway adapter.

Everything is the platform's shared engine except one correction of this
checkpoint's own habit: it labels the punctuation that follows a value as part
of the value ("12/03/1975)", "Kerkstraat 12,", "metformine 500 mg."). The
router replaces exactly the span it is given, so a span that ends on a comma
removes the comma from the document and leaves the sentence run together. The
other checkpoints do not do this, which is why the correction lives here and
not in the shared engine, where trimming a full stop would also cut the dot off
an "Inc." or a "St." that another team's model rightly included.
"""

from __future__ import annotations

from dataclasses import replace

from masking_engine import MaskSpan, ready, release, supported_model_formats, unload
from masking_engine import detect as _detect

TEAM_ID = "mdeberta"

_TRAILING = ".,;:!?"
_PAIRS = {")": "(", "]": "["}
_OPENERS = "(["


def supported_media_types() -> frozenset[str]:
    return frozenset({"application/pdf", "text/plain", "text/csv", "application/json"})


def _trimmed(text: str, span: MaskSpan) -> MaskSpan | None:
    """Return *span* without punctuation that is around the value, not in it.

    A closing bracket is dropped only when its opener is not inside the span
    (so "(+32) 478" keeps its brackets and "12/03/1975)" loses one), and an
    opening bracket only when its closer is not. Returns None when nothing but
    punctuation is left, so the caller drops the span.
    """
    start, end = span.start, span.end
    while end > start:
        last = text[end - 1]
        if last in _TRAILING or (
            last in _PAIRS and _PAIRS[last] not in text[start : end - 1]
        ):
            end -= 1
        else:
            break
    while start < end and text[start] in _OPENERS:
        closer = ")" if text[start] == "(" else "]"
        if closer in text[start + 1 : end]:
            break
        start += 1
    if not any(character.isalnum() for character in text[start:end]):
        return None
    if (start, end) == (span.start, span.end):
        return span
    return replace(span, start=start, end=end)


def detect(text: str, metadata: dict[str, str]) -> list[MaskSpan]:
    spans = _detect(text, metadata)
    kept: list[MaskSpan] = []
    for span in spans:
        if span.source != "model":
            kept.append(span)
            continue
        trimmed = _trimmed(text, span)
        if trimmed is not None:
            kept.append(trimmed)
    return kept


__all__ = [
    "TEAM_ID",
    "detect",
    "ready",
    "release",
    "supported_model_formats",
    "unload",
    "supported_media_types",
]
