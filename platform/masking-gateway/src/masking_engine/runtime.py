"""Public Octo runtime API consumed by its gateway adapter."""

from __future__ import annotations

from dataclasses import replace
from functools import cache

from .app.core.config import load_settings
from .app.core.schemas import MaskSpan
from .app.engines.label_converter import LabelConverter
from .app.engines.orchestrator import Orchestrator
from .app.engines.regex_engine import find_regex_spans
from .app.engines import span_shape
from .app.services.pdf_ocr import reader_for
from .app.services.document_pipeline import prepare_document, redact_document

SUPPORTED_MODEL_FORMATS = frozenset({"onnx"})
DEFAULT_MODEL_FORMAT = "onnx"
_loaded_formats: set[str] = set()


@cache
def _runtime(model_format: str):
    # *model_format* stays in the signature so `@cache` still keys on it and
    # `_loaded_formats` still reports what a readiness probe asked for, even
    # though ONNX is now the only value it can hold.
    settings = load_settings(require_service_token=False)
    engine = None
    if settings.detection_mode != "regex":
        from .src.models.xlm_r.model_engine import OnnxTokenClassifierEngine

        engine = OnnxTokenClassifierEngine(
            model_path=settings.onnx_model_path,
            model_version=settings.model_version,
            confidence_threshold=settings.confidence_threshold,
            entity_confidence_thresholds=settings.entity_confidence_thresholds,
            accelerator=settings.accelerator,
            model_sha256=settings.onnx_model_sha256,
            max_model_tokens=settings.max_model_tokens,
            model_stride_tokens=settings.model_stride_tokens,
        )
    runtime = (
        settings,
        engine,
        Orchestrator(
            settings.detection_mode,
            shape_rule=span_shape.admits if settings.model_id_shape_gate else None,
        ),
        LabelConverter(settings.detector_label_profile),
    )
    _loaded_formats.add(model_format)
    return runtime


def _model_format(metadata: dict[str, str]) -> str:
    model_format = metadata.get("model_format", DEFAULT_MODEL_FORMAT).lower()
    if model_format not in SUPPORTED_MODEL_FORMATS:
        raise ValueError("model_format must be onnx")
    return model_format


def _drop_word_fragments(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Drop entity spans the model split off mid-word.

    A short (<=2 char) span glued to a letter or digit on either side is a
    subword-tokenization artifact (e.g. a stray "B-AGE" on the "j" of
    "jarige" right after a genuine "41" AGE span), not a standalone entity:
    the rest of the word carries no span of its own, so masking only the
    fragment breaks the word instead of protecting anything.
    """
    kept: list[MaskSpan] = []
    for span in spans:
        if span.end - span.start <= 2:
            before = text[span.start - 1] if span.start > 0 else ""
            after = text[span.end] if span.end < len(text) else ""
            if before.isalnum() or after.isalnum():
                continue
        kept.append(span)
    return kept


def _drop_time_fragments(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Drop spans that open with a colon.

    The model occasionally tags the tail of a timestamp ("15:33" -> ":33")
    as a DATE; no useful detection opens with a colon, so the artifact is
    dropped instead of masked.
    """
    return [span for span in spans if text[span.start : span.start + 1] != ":"]


def _trim_line_overrun(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Trim a span back to its own line when it overran the line break.

    The model reads a page as one string, so a value sitting at the end of a
    line runs straight into the start of the next. A footer's phone number
    came back as "03 20 66 34 09\n45", carrying off the house number of the
    address printed below it -- and that one-character overlap was enough for
    `coalesce_spans` to merge the phone span into the address span, so the
    phone left the document labelled ADDRESS and drew an address box.

    A tail is trimmed on either of two tests. **Length**: one or two
    characters, which is the original rule. **Coverage**: another span begins
    exactly where the tail begins and reaches at least as far, whatever the
    length. An English address puts its house number first, so a three-digit
    one defeated the length test on its own -- the model returned
    "+1 (312) 555-0142\n119", the address rule claimed
    "119 Harbor View Ave, Evanston, IL 60201" from the same "119", and the
    merge drew the telephone as an address in an English letter.

    Coverage is the safer of the two tests and needs no length limit, because
    the span that covers the tail still masks it -- trimming moves which label
    the value is masked under and never whether it is masked. The covering
    span is required to start on the next line and to carry no line break of
    its own, so it cannot be a span this same pass would trim, which is what
    would otherwise let two overrunning spans cover each other and leave the
    tail masked by neither.

    A value that genuinely wraps keeps all of it: its continuation carries
    real text -- "Jean-Baptiste" then "Moreau" -- which is too long for the
    length test and is claimed by no span of its own.

    The coverage scan runs only for a span that actually crosses a line
    break, so a page whose spans all sit on one line costs the single pass
    this has always been.
    """
    trimmed: list[MaskSpan] = []
    for span in spans:
        body = text[span.start : span.end]
        break_at = body.rfind("\n")
        tail = body[break_at + 1 :] if break_at != -1 else ""
        if break_at > 0 and tail and tail.isalnum():
            tail_start = span.start + break_at + 1
            covered = any(
                other is not span
                and other.start == tail_start
                and other.end >= span.end
                and "\n" not in text[other.start : other.end]
                for other in spans
            )
            if len(tail) <= 2 or covered:
                span = replace(span, end=span.start + break_at)
        trimmed.append(span)
    return trimmed


def _merge_address_parts(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Join the Octo model's word-by-word address detections on one line."""
    merged: list[MaskSpan] = []
    for span in sorted(spans, key=lambda item: (item.start, item.end)):
        last = merged[-1] if merged else None
        gap = text[last.end : span.start] if last is not None else ""
        if (
            last is not None
            and last.label == span.label == "ADDRESS"
            and gap.strip(" ,") == ""
        ):
            merged[-1] = replace(last, end=span.end)
        else:
            merged.append(span)
    return merged


def detect(text: str, metadata: dict[str, str]) -> list:
    """Return Octo detections for one shared pipeline text unit.

    The platform owns extraction and rendering. This function only performs
    Octo's model/regex detection and converts its labels to the shared taxonomy.
    """
    settings, engine, orchestrator, converter = _runtime(_model_format(metadata))
    content_type = metadata.get("content_type", "").split(";", 1)[0].lower()
    if content_type not in settings.supported_media_types:
        raise ValueError("unsupported content type")
    model_spans = engine.infer(text) if engine else []
    if engine and content_type in {"text/csv", "application/json"}:
        from .src.models.xlm_r.model_engine import clip_spans_to_fields

        model_spans += engine.infer_fields(text, settings.structured_field_threshold)
        model_spans = clip_spans_to_fields(text, model_spans)
    regex_spans = find_regex_spans(text) if orchestrator.mode != "model" else []
    return _merge_address_parts(
        text,
        _trim_line_overrun(
            text,
            _drop_time_fragments(
                text,
                _drop_word_fragments(
                    text,
                    orchestrator.execute(
                        converter.convert_all(model_spans, {None: text}),
                        regex_spans,
                        {None: text},
                    ),
                ),
            ),
        ),
    )


def ready(model_format: str | None = None) -> bool:
    return (
        bool(_loaded_formats)
        if model_format is None
        else model_format in _loaded_formats
    )


def unload() -> None:
    """Release the cached Octo runtime after the model service becomes idle."""
    _runtime.cache_clear()
    _loaded_formats.clear()


def release(
    model_format: str = DEFAULT_MODEL_FORMAT, *, locale: str | None = None
) -> tuple[str, str, str]:
    """Return the identity of the checkpoint that answers a request.

    *locale* is accepted and ignored: this runtime holds one model for every
    language, so the document's language cannot change which checkpoint
    produced the bytes. A team that keeps one model per language reads it.
    """
    del locale
    settings = load_settings(require_service_token=False)
    if settings.detection_mode == "regex":
        return settings.model_name, "regex-rules-v1", "unknown"
    del model_format
    digest = settings.onnx_model_sha256
    return (
        settings.model_name,
        settings.model_version,
        f"sha256:{digest}" if digest else "unknown",
    )


def supported_model_formats() -> frozenset[str]:
    return SUPPORTED_MODEL_FORMATS


def mask(content: bytes, metadata: dict[str, str]) -> tuple[bytes, dict[str, str]]:
    """Compatibility entry point for local Octo checks.

    Private model services use :func:`detect` directly and invoke the same
    shared pipeline themselves.
    """
    model_format = _model_format(metadata)
    settings, engine, _, _ = _runtime(model_format)
    content_type = metadata.get("content_type", "").split(";", 1)[0].lower()
    if content_type not in settings.supported_media_types:
        raise ValueError("unsupported content type")
    if not content or len(content) > settings.max_upload_size_bytes:
        raise ValueError("invalid content size")
    if engine and len(content) > settings.max_model_input_bytes:
        raise ValueError("invalid content size")
    document = prepare_document(content, content_type, ocr=reader_for(settings))
    spans = [
        replace(span, page=text.page) if text.page is not None else span
        for text in document.texts
        for span in detect(
            text.text, {"content_type": content_type, "model_format": model_format}
        )
    ]
    masked = redact_document(
        document,
        spans,
        organization_pool=settings.organization_surrogate_pool,
        redaction_style=settings.pdf_redaction_style,
        redaction_mode=settings.pdf_redaction_mode,
        locale_badge=settings.pdf_locale_badge,
    )
    model_name, model_version, _ = release(model_format)
    return masked, {"model_name": model_name, "model_version": model_version}
