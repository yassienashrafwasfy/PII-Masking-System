"""Translate a foreign detector's labels into the canonical taxonomy.

Three teams detect PII with three vocabularies. This service masks with one.
The converter reads the label a detector already produced and returns the
canonical label the masking router routes on. It runs between detection and
orchestration, because the orchestrator decides regex ownership by canonical
label: a Berts ``phone`` span has to be a ``PHONE_NUMBER`` span before the regex
phone span that covers it can suppress it.

Nothing here touches a model. The converter reads the entity string that
decoding already produced, so ``id2label``, the checkpoint, and the reported
metrics of every team's model stay as they are.

Synthify and Berts both emit a whole address. The QA schema keeps addresses as
one ``ADDRESS`` span, so conversion never decomposes it.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from ..core import label_registry
from ..core.schemas import MaskSpan

log = logging.getLogger(__name__)


class UnknownLabelError(ValueError):
    """Raised in strict mode when no profile entry covers a label."""


class LabelConverter:
    """Map one detector's labels onto the canonical taxonomy."""

    def __init__(self, profile: str = "octo", *, strict: bool = False) -> None:
        registry = label_registry.profiles()
        if profile not in registry["profiles"]:
            raise ValueError(f"Unknown label profile: {profile}")

        self.profile = profile
        self.strict = strict
        entry = registry["profiles"][profile]
        # A profile entry wins over the taxonomy alias of the same name, so a
        # team can bind a name this repository already uses to another label.
        self._aliases = {
            label_registry.normalize(native): canonical.upper()
            for native, canonical in entry["aliases"].items()
        }
        self._composite_addresses = frozenset(
            label_registry.normalize(label)
            for label in entry["composite_address_labels"]
        )

    def canonical_label(self, label: str) -> str:
        """Return the canonical label for *label* under this profile."""
        normalized = label_registry.normalize(label)
        mapped = self._aliases.get(normalized) or label_registry.canonical_label(label)
        if mapped is not None:
            return mapped
        if self.strict:
            raise UnknownLabelError(
                f"Profile {self.profile} has no entry for label {normalized}"
            )
        # The label alone, never the text it matched. The router still masks the
        # span, so an unmapped entity cannot reach the released document.
        log.info(
            "label_conversion_unmapped profile=%s label=%s", self.profile, normalized
        )
        return label.upper()

    def convert(self, span: MaskSpan, text: str) -> list[MaskSpan]:
        """Return the canonical spans that replace *span*."""
        if label_registry.normalize(span.label) in self._composite_addresses:
            return [replace(span, label="ADDRESS")]
        label = self.canonical_label(span.label)
        if label not in label_registry.masked_labels():
            # The API contract permits only the QA taxonomy. A legacy model can
            # still emit a retired class, but it must not create a new output
            # label merely because one old checkpoint knows it.
            log.info(
                "label_conversion_dropped profile=%s label=%s", self.profile, label
            )
            return []
        return [replace(span, label=label)]

    def convert_all(
        self, spans: list[MaskSpan], texts: dict[int | None, str]
    ) -> list[MaskSpan]:
        """Return canonical spans for *spans*, whose text is *texts* by page."""
        converted: list[MaskSpan] = []
        for span in spans:
            converted += self.convert(span, texts.get(span.page, ""))
        log.info(
            "label_conversion profile=%s in=%d out=%d",
            self.profile,
            len(spans),
            len(converted),
        )
        return converted
