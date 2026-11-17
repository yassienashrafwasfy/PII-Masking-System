"""Synthify GLiNER runtime used only by the private model service."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path

from masking_engine import MaskSpan

from . import regex_detectors
from .labels import CANONICAL_LABELS, TRAINING_LABELS
from .windowing import predict_sliding_window

MODEL_NAME = "synthify-gliner-belgian-pii-ner"
# The exported ONNX graph is the served artifact, so its digest is the
# version. The GLiNER source checkpoint it was exported from is not in this
# repository and cannot be what a caller is told answered a request.
MODEL_VERSION = (
    "sha256:8a6baba537c4a00d518586a2b56f62547259f12685b9b1be9b6cfc91fa44dd45"
)
SUPPORTED_MEDIA_TYPES = frozenset({"application/pdf", "text/plain"})
SUPPORTED_MODEL_FORMATS = frozenset({"onnx"})
DEFAULT_MODEL_FORMAT = "onnx"
_loaded_formats: set[str] = set()
# Prompt with every label the checkpoint was fine-tuned on, including `Title`,
# which has no taxonomy counterpart and is dropped after prediction. Dropping a
# type from the prompt set instead would change how the remaining types compete
# for the same span -- title tokens would start landing on `Name`.
LABELS = TRAINING_LABELS
BLOCKED_SINGLE_NAME_TERMS = frozenset({"anamnese", "collega", "patient", "patiënt"})
DATE_PATTERN = re.compile(
    r"^(?:\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?|\d{1,2}\s+[A-Za-zé]+\s+\d{4})$"
)
URL_PATTERN = re.compile(r"^(?:https?://|www\.)\S+|\S+\.\w{2,}(?:/\S*)?$", re.I)
URL_FALLBACK_PATTERN = re.compile(r"(?i)(?<!\w)(?:https?://|www\.)[^\s]+")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^[+\d][\d\s()./-]{7,}$")
ADDRESS_PATTERN = re.compile(
    r"^(?=.*[A-Za-zÀ-ÿ])(?=.*\d).*(?:straat|weg|laan|plein|dreef|baan|boulevard|\,).*$",
    re.I,
)
NAME_TOKEN_PATTERN = re.compile(r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+$")
NAME_PARTICLES = frozenset(
    {
        "af",
        "al",
        "bin",
        "da",
        "de",
        "del",
        "den",
        "der",
        "di",
        "dos",
        "el",
        "ibn",
        "la",
        "le",
        "op",
        "te",
        "ten",
        "ter",
        "van",
        "von",
        "zu",
        "zur",
    }
)
PHONE_PREFIX_RE = re.compile(r"^T\s+(?=[+\d])", re.I)


@cache
def _model(model_format: str):
    # *model_format* stays in the signature so `@cache` still keys on it and
    # `_loaded_formats` still reports what a readiness probe asked for, even
    # though ONNX is now the only value it can hold.
    from gliner import GLiNER

    if os.getenv("ACCELERATOR", "cpu").lower() == "cuda":
        raise ValueError("The ONNX model is configured for CPU only.")
    model = GLiNER.from_pretrained(
        _onnx_path(), load_onnx_model=True, map_location="cpu"
    )
    _loaded_formats.add(model_format)
    return model


def _onnx_path() -> Path:
    return Path(
        os.getenv("ONNX_MODEL_PATH", str(Path(__file__).resolve().parent / "onnx"))
    )


def ready(model_format: str | None = None) -> bool:
    return (
        bool(_loaded_formats)
        if model_format is None
        else model_format in _loaded_formats
    )


def unload() -> None:
    """Release the cached GLiNER model after the model service becomes idle."""
    _model.cache_clear()
    _loaded_formats.clear()


def release(
    model_format: str = DEFAULT_MODEL_FORMAT, *, locale: str | None = None
) -> tuple[str, str, str]:
    # Accepted and ignored: Synthify's GLiNER checkpoint is multilingual, so
    # the document's language never changes which artifact answered.
    del locale, model_format
    manifest = _onnx_path() / "manifest.json"
    digest = os.getenv("ONNX_MODEL_DIGEST", "")
    if not digest and manifest.is_file():
        digest = "sha256:" + str(json.loads(manifest.read_text())["model_sha256"])
    return (
        os.getenv("MODEL_NAME", MODEL_NAME),
        os.getenv("MODEL_VERSION", MODEL_VERSION),
        digest or "unknown",
    )


def supported_media_types() -> frozenset[str]:
    return SUPPORTED_MEDIA_TYPES


def supported_model_formats() -> frozenset[str]:
    return SUPPORTED_MODEL_FORMATS


def _valid_name(value: str, folded: str) -> bool:
    if folded in BLOCKED_SINGLE_NAME_TERMS:
        return False
    if re.fullmatch(r"[A-Z]+_[A-Z0-9]+", value):
        return True
    tokens = value.split()
    return (
        bool(tokens)
        and any(NAME_TOKEN_PATTERN.fullmatch(token) for token in tokens)
        and all(
            NAME_TOKEN_PATTERN.fullmatch(token) or token.casefold() in NAME_PARTICLES
            for token in tokens
        )
    )


def _digit_count(value: str) -> int:
    return len(re.sub(r"\D", "", value))


# One predicate per label, given the stripped value and its casefolded form. A
# label with no entry is rejected: only the categories listed here are accepted.
_ENTITY_VALIDATORS: dict[str, Callable[[str, str], bool]] = {
    "Name": _valid_name,
    "Date": lambda value, folded: bool(DATE_PATTERN.fullmatch(value)),
    "Email": lambda value, folded: bool(EMAIL_PATTERN.fullmatch(value)),
    "Address": lambda value, folded: bool(ADDRESS_PATTERN.fullmatch(value)),
    "TELEFOON": lambda value, folded: bool(PHONE_PATTERN.fullmatch(value)),
    "Age": lambda value, folded: bool(re.fullmatch(r"\d{1,3}(?:-jarige)?", value)),
    "Gender": lambda value, folded: folded
    in {"man", "vrouw", "m", "v", "x", "non-binair"},
    "RVIZ": lambda value, folded: _digit_count(value) >= 7,
    "INSZ": lambda value, folded: _digit_count(value) == 11,
    "CREDITCARDNUMBER": lambda value, folded: 13 <= _digit_count(value) <= 19,
    "URL": lambda value, folded: bool(URL_PATTERN.fullmatch(value)),
    "Hospital_Name": lambda value, folded: any(
        term in folded for term in ("ziekenhuis", "hospital", "kliniek", "azorg")
    ),
}


def _valid_entity(label: str, value: str) -> bool:
    """Reject high-confidence category errors before they reach redaction.

    Off by default (`ENTITY_VALIDATORS=on` re-enables it). This filter has no
    counterpart in the source notebook, so none of the published scores
    describe its behaviour, and its patterns drop correct Belgian PII outright:
    `Els van den Berg` and `J. Peeters` (Name), `2025-03-12` (Date),
    `Rue de la Loi 16` and `9000 Gent` (Address), `UZ Leuven` and `CHU de
    Liege` (Hospital_Name), `45 jaar` (Age), `vrouwelijk` (Gender). It is
    biased against exactly the French forms and Dutch tussenvoegsels this
    checkpoint exists to mask, on classes it already scores 0.90-0.997 on.

    It also compounds with boundary error: the notebook counts a match at
    IoU >= 0.5, but only 92.6% of `Date` and 95.5% of `Address` predictions
    land on exact boundaries, and the near-misses are fed here as raw strings.
    """
    value = value.strip()
    validator = _ENTITY_VALIDATORS.get(label)
    return validator(value, value.casefold()) if validator else False


def _merge_regex_spans(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Supplement the model's spans with checksum-validated regex detections.

    Regex covers formats the checkpoint misses outright (its URL F1 is 0.078,
    see docs/model-card.md) and re-anchors numeric spans it truncates -- a
    half-detected credit card or INSZ leaks the digits left outside the span.
    Where the two overlap the wider span wins, so merging only ever grows
    masked coverage; ties go to the model, which keeps every span the model
    already found on its own.
    """
    merged = list(spans)
    for entity in regex_detectors.detect_entities(text):
        label = CANONICAL_LABELS.get(str(entity["label"]))
        if label is None:
            continue
        start, end = int(entity["start"]), int(entity["end"])
        overlapping = [s for s in merged if start < s.end and s.start < end]
        if any(s.end - s.start >= end - start for s in overlapping):
            continue
        merged = [s for s in merged if not (start < s.end and s.start < end)]
        merged.append(MaskSpan(start, end, label, "regex"))
    return sorted(merged, key=lambda span: (span.start, span.end))


def detect(text: str, metadata: dict[str, str]) -> list[MaskSpan]:
    """Return canonical Synthify detections for one shared pipeline text unit."""
    if metadata.get("content_type") not in SUPPORTED_MEDIA_TYPES:
        raise ValueError("unsupported content type")
    model_format = metadata.get("model_format", DEFAULT_MODEL_FORMAT).lower()
    if model_format not in SUPPORTED_MODEL_FORMATS:
        raise ValueError("model_format must be onnx")
    validate = os.getenv("ENTITY_VALIDATORS", "off").lower() == "on"
    entities = predict_sliding_window(
        _model(model_format),
        text,
        list(LABELS),
        threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.5")),
    )
    source_layer = str(_onnx_path())
    spans = []
    for entity in entities:
        label = str(entity["label"])
        # `Title` is prompted for but has no taxonomy label, so it drops here.
        if label not in CANONICAL_LABELS:
            continue
        start, end = int(entity["start"]), int(entity["end"])
        if label == "TELEFOON":
            prefix = PHONE_PREFIX_RE.match(text[start:end])
            if prefix is not None:
                start += prefix.end()
        if end > start and (not validate or _valid_entity(label, text[start:end])):
            spans.append(
                MaskSpan(
                    start,
                    end,
                    CANONICAL_LABELS[label],
                    "model",
                    confidence=float(entity.get("score", 0.0)),
                    source_layer=source_layer,
                )
            )
    if os.getenv("REGEX_DETECTORS", "on").lower() != "off":
        spans = _merge_regex_spans(text, spans)
        detected_ranges = {
            (span.start, span.end) for span in spans if span.label == "URL"
        }
        for match in URL_FALLBACK_PATTERN.finditer(text):
            end = len(match.group(0).rstrip(".,;:!?)]}"))
            if end > 0 and (match.start(), match.start() + end) not in detected_ranges:
                spans.append(
                    MaskSpan(match.start(), match.start() + end, "URL", "regex")
                )
    return spans
