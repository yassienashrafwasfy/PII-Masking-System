"""Define shared masking pipeline types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MaskSource = Literal["model", "regex"]


@dataclass(frozen=True)
class MaskSpan:
    """Identify one contiguous PII or PHI span.

    ``page=None`` means that offsets refer to a plain-text body. A page number
    means that offsets refer to the extracted text for that PDF page.
    """

    start: int
    end: int
    label: str
    source: MaskSource
    page: int | None = None
    confidence: float | None = None
    source_layer: str | None = None

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"MaskSpan end must exceed start: {self}")


@dataclass(frozen=True)
class LocaleBadge:
    """Carry a language-detection verdict to the PDF diagnostic overlay.

    Lives here, without importing the detector, so the PDF renderer can draw
    the verdict without depending on `lingua`. `entries` is
    `(language_name, confidence, locale_or_None)` best first, where None
    marks a language loaded only so it can be recognised and rejected.
    """

    entries: tuple[tuple[str, float, str | None], ...]
    resolved: str
    detected: bool
