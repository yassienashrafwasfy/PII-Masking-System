"""Private HTTP wrapper for one team-owned masking adapter."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time
import types
from gc import collect
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Any

from common import http
from masking_engine import (
    CorruptInputError,
    configure_logging,
    document_verdict,
    label_registry,
    load_settings,
    locales,
    OcrBounds,
    prepare_document,
    reader_for,
    redact_document,
)

log = logging.getLogger(__name__)
TEAM_ID = os.getenv("TEAM_ID", "")
SUPPORTED_TEAMS = frozenset({"octo", "synthify", "berts", "mdeberta"})
MAX_FILE_BYTES = http.MAX_UPLOAD_BYTES
BLOCKED_NAME_VALUES = frozenset({"anamnese", "collega", "nicht", "patient", "patiënt"})
ROOT = Path(os.getenv("REPOSITORY_ROOT", ""))
if not ROOT:
    ROOT = Path(__file__).resolve().parents[3]
try:
    IDLE_TIMEOUT_SECONDS = float(os.getenv("MODEL_IDLE_TIMEOUT_SECONDS", "120"))
except ValueError as exc:
    raise RuntimeError("MODEL_IDLE_TIMEOUT_SECONDS must be zero or greater") from exc
if IDLE_TIMEOUT_SECONDS < 0:
    raise RuntimeError("MODEL_IDLE_TIMEOUT_SECONDS must be zero or greater")
# ponytail: one model service handles one request at a time; add finer locking only if throughput requires it.
_lifecycle_lock = threading.Lock()
_idle_timer: threading.Timer | None = None
_idle_generation = 0


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def _request_id(environ: dict[str, object]) -> str:
    return http.request_id(environ)


def _error(
    start_response: Any, status: int, message: str, request_id: str
) -> list[bytes]:
    return http.error_body(
        start_response, status, message, request_id, code_override="MODEL_SERVICE_ERROR"
    )


@cache
def _adapter() -> Any:
    if TEAM_ID not in SUPPORTED_TEAMS:
        raise RuntimeError("TEAM_ID is not configured")
    adapter_path = ROOT / "teams" / TEAM_ID / "adapter.py"
    package_name = f"model_service_{TEAM_ID.replace('-', '_')}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(adapter_path.parent)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.adapter", adapter_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("team adapter could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _settings():
    settings = load_settings(require_service_token=False)
    # Switched on here rather than at import: this is the first moment
    # LOG_LEVEL is known, and @cache makes it exactly once per process.
    # Without it the root logger keeps its WARNING default and every
    # log.info in the engine is discarded, which is what made LOG_LEVEL
    # look like a setting that did nothing.
    configure_logging(settings.log_level)
    return settings


def _model_format(environ: dict[str, object]) -> str:
    try:
        value = http.model_format(str(environ.get("HTTP_X_MODEL_FORMAT", "")))
    except ValueError as exc:
        raise ServiceError(400, str(exc)) from exc
    if value not in _adapter().supported_model_formats():
        raise ServiceError(400, "The selected model format is not available.")
    return value


def _model_file_size_mb(model_format: str) -> float | None:
    """Size on disk of the weights this service actually loaded, in MB.

    Self-reported because nothing outside the container can see it. The weights
    live in this image, not in the gateway's and not in any repository a QA run
    checks out, so the only alternative was reading them from a masking-repo
    working copy -- which is why QA has been reporting model size as null.

    Best effort by design: a missing or unreadable file yields None rather than
    an error. This is descriptive metadata on an endpoint whose real job is
    reporting identity, and /version must not start failing because a size
    could not be stat'ed.
    """
    del model_format
    settings = _settings()
    directory = Path(settings.onnx_model_path or "")
    candidate = directory / "model.onnx" if directory.is_dir() else directory
    try:
        return round(candidate.stat().st_size / (1024 * 1024), 1)
    except OSError:
        return None


def _identity(model_format: str, locale: str | None = None) -> dict[str, object]:
    """Describe what would answer, or -- given *locale* -- what just did.

    `GET /version` has no document and so no language, and reports the team's
    default checkpoint. A masked response passes the locale the document
    resolved to, so a team holding one model per language attributes the
    bytes to the checkpoint that actually produced them.
    """
    model_name, model_version, model_digest = _adapter().release(
        model_format, locale=locale
    )
    # Which languages this team holds a checkpoint for, beside the other
    # capabilities a caller has to know before it builds a request. The
    # platform's list stands in for a team that declares no restriction: it is
    # exactly what such a team serves.
    supported_locales = _supported_locales() or frozenset(locales.SUPPORTED_LOCALES)
    return {
        "service": f"{TEAM_ID}-model-service",
        "api_contract_version": "v1",
        "service_release": os.getenv("SERVICE_RELEASE", "unknown"),
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "image_digest": os.getenv("IMAGE_DIGEST", "unknown"),
        "model_name": model_name,
        "model_version": model_version,
        "model_digest": model_digest,
        "model_format": model_format,
        # Null when the file cannot be stat'ed; consumers must treat it as
        # "not reported" rather than as a size of zero.
        "model_file_size_mb": _model_file_size_mb(model_format),
        # Which labels a caller may name in `X-Mask-Labels`, and what it gets
        # when it names none. Declared beside the other capabilities a caller
        # has to know before it builds a request, for the same reason
        # `supported_model_formats` is.
        "selectable_labels": sorted(label_registry.masked_labels()),
        "default_masked_labels": sorted(label_registry.masked_labels()),
        "supported_mime_types": sorted(_adapter().supported_media_types()),
        "supported_model_formats": sorted(_adapter().supported_model_formats()),
        "supported_locales": sorted(supported_locales),
        "max_file_bytes": MAX_FILE_BYTES,
    }


def _cancel_idle_unload() -> None:
    global _idle_generation, _idle_timer
    _idle_generation += 1
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _unload_model(generation: int) -> None:
    global _idle_timer
    with _lifecycle_lock:
        if generation != _idle_generation:
            return
        _idle_timer = None
        try:
            _adapter().unload()
            collect()
            log.info(
                "model_unloaded team=%s idle_seconds=%s", TEAM_ID, IDLE_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - cleanup must not terminate the service
            log.exception("model_unload_failed team=%s", TEAM_ID)


def _schedule_idle_unload() -> None:
    global _idle_timer
    if IDLE_TIMEOUT_SECONDS <= 0 or not _adapter().ready():
        return
    generation = _idle_generation
    _idle_timer = threading.Timer(
        IDLE_TIMEOUT_SECONDS, _unload_model, args=(generation,)
    )
    _idle_timer.daemon = True
    _idle_timer.start()


@dataclass(frozen=True)
class _MaskRequest:
    """A validated masking request: every field was checked before it was set."""

    content: bytes
    content_type: str
    request_id: str
    model_format: str
    document_name: str


def _mask_request(environ: dict[str, object]) -> _MaskRequest:
    request_id = _request_id(environ)
    content_type = http.media_type(str(environ.get("CONTENT_TYPE", "")))
    if not request_id or not content_type:
        raise ServiceError(400, "Required request metadata is missing.")
    if content_type not in _adapter().supported_media_types():
        raise ServiceError(
            415, "The supplied media type is not supported by this model."
        )
    try:
        content = http.read_body(environ, MAX_FILE_BYTES)
    except http.PayloadTooLargeError as exc:
        raise ServiceError(413, "Payload exceeds the model limit.") from exc
    except ValueError as exc:
        raise ServiceError(400, str(exc)) from exc
    document_name = PurePosixPath(str(environ.get("HTTP_X_SOURCE_OBJECT_KEY", ""))).name
    if not document_name:
        raise ServiceError(400, "Source object key is required.")
    return _MaskRequest(
        content=content,
        content_type=content_type,
        request_id=request_id,
        model_format=_model_format(environ),
        document_name=document_name,
    )


def _selection(
    detected: list[Any], page: object, selected: frozenset[str] | None
) -> tuple[list[Any], list[dict[str, object]]]:
    """Split one text's detections into what is masked and what is left readable.

    Filtered here, before `coalesce_spans` merges overlaps and discards the
    losing label: a span dropped after the merge can take a kept label's text
    out of the mask with it.
    """
    if selected is None:
        return detected, []
    # Detected but deliberately left readable. Offsets and label only: a value
    # we chose not to mask must not be copied into a log object.
    skipped: list[dict[str, object]] = [
        {
            "label": span.label,
            "start": span.start,
            "end": span.end,
            "page": page,
            "masked": False,
        }
        for span in detected
        if span.label not in selected
    ]
    return [span for span in detected if span.label in selected], skipped


def _detected_spans(
    document: Any,
    content_type: str,
    model_format: str,
    selected: frozenset[str] | None,
    locale: str,
) -> tuple[list[Any], list[dict[str, object]]]:
    """Run the team's detector over every extracted text span.

    *locale* is the whole document's language, resolved once by the caller.
    A team whose detector is one model per language reads it to choose the
    checkpoint; a team with one model ignores it. It is deliberately not
    re-derived per text: a single page carries too few letters to detect,
    and one document must not change detector halfway through.
    """
    metadata = {
        "content_type": content_type,
        "model_format": model_format,
        "document_locale": locale,
    }
    spans: list[Any] = []
    skipped: list[dict[str, object]] = []
    for text in document.texts:
        detected = [
            span
            for span in _adapter().detect(text.text, metadata)
            if _keep_detection(text.text, span)
        ]
        kept, left = _selection(detected, text.page, selected)
        skipped.extend(left)
        spans.extend(
            replace(span, page=text.page) if text.page is not None else span
            for span in kept
        )
    if selected is not None:
        log.info(
            "mask_label_selection labels=%s masked=%d left=%d",
            ",".join(sorted(selected)),
            len(spans),
            len(skipped),
        )
    return spans, skipped


def _detect_and_redact(
    content: bytes,
    content_type: str,
    model_format: str,
    selected: frozenset[str] | None = None,
    locale: str | None = None,
    request_id: str = "",
) -> tuple[bytes, list[dict[str, object]], str]:
    """Run one team's detector inside the platform-owned document pipeline.

    Returns the resolved document locale alongside the masked bytes, because
    the identity the response reports depends on it: a team with one model
    per language ran the one this locale selected, and `X-Model-Name` has to
    name that checkpoint rather than a bundle.
    """
    stage_start = time.perf_counter()
    document = prepare_document(
        content,
        content_type,
        ocr=reader_for(_settings()),
        # Without this the OCR bounds an operator set were validated, shipped
        # in compose, and then dropped here -- `inject_text_layer` fell back to
        # its own defaults for every one of them.
        ocr_bounds=OcrBounds.from_settings(_settings()),
    )
    extract_seconds = time.perf_counter() - stage_start
    # Resolved before detection, not during redaction, because the detector
    # may pick its checkpoint from it. The verdict is then handed to
    # redaction, so one document is detected once and the model half and the
    # surrogate half cannot answer differently.
    verdict = document_verdict(document, locale)
    _report_locale_routing(verdict)
    with _lifecycle_lock:
        _cancel_idle_unload()
        try:
            stage_start = time.perf_counter()
            spans, skipped = _detected_spans(
                document, content_type, model_format, selected, verdict.locale
            )
            detect_seconds = time.perf_counter() - stage_start
            audit: list[dict[str, object]] = []
            stage_start = time.perf_counter()
            masked = redact_document(
                document,
                spans,
                audit,
                organization_pool=_settings().organization_surrogate_pool,
                redaction_style=_settings().pdf_redaction_style,
                redaction_mode=_settings().pdf_redaction_mode,
                chip_reflow=_settings().pdf_chip_reflow,
                locale=verdict,
                locale_badge=_settings().pdf_locale_badge,
            )
            redact_seconds = time.perf_counter() - stage_start
            log.info(
                "mask_stage_timing team=%s request_id=%s locale=%s extract=%.3fs "
                "detect=%.3fs redact=%.3fs total=%.3fs",
                TEAM_ID,
                request_id,
                verdict.locale,
                extract_seconds,
                detect_seconds,
                redact_seconds,
                extract_seconds + detect_seconds + redact_seconds,
            )
            return masked, audit + skipped, verdict.locale
        finally:
            _schedule_idle_unload()


def _keep_detection(text: str, span: Any) -> bool:
    """Reject known section and role words that are not person names."""
    return not (
        span.label == "NAME"
        and text[span.start : span.end].strip().casefold() in BLOCKED_NAME_VALUES
    )


def _document_locale(environ: dict[str, object]) -> str | None:
    """Return the locale `X-Document-Locale` asks for, or None to detect it.

    Re-validated here rather than trusted: the gateway has already checked it,
    but the model service is correct on its own, the way it re-checks
    `X-Model-Format` and `X-Mask-Labels` instead of believing the proxy.
    """
    try:
        return http.document_locale(
            str(environ.get("HTTP_X_DOCUMENT_LOCALE", "")),
            allowed=locales.SUPPORTED_LOCALES,
        )
    except ValueError as exc:
        raise ServiceError(400, str(exc)) from exc


def _supported_locales() -> frozenset[str] | None:
    """Return the locales the selected team holds a checkpoint for.

    None means "no team-level restriction": a team whose one model answers
    every language serves all of `locales.SUPPORTED_LOCALES`, and repeating
    that list here would only be a second copy of it. Berts holds one
    checkpoint per language and names the languages it has, which is what
    lets a caller be told it asked for one this team cannot read.
    """
    declared = getattr(_adapter(), "supported_locales", None)
    return None if declared is None else declared()


def _require_team_locale(locale: str | None, supported: frozenset[str] | None) -> None:
    """Refuse a pinned locale this team has no checkpoint for.

    A caller that *names* a language is asking for a specific model, so the
    answer has to be that model or a refusal. Falling back to the default
    checkpoint here reads from outside as "the team served French" while the
    bytes were produced by a model that cannot read it.

    A locale nobody pinned stays a fallback rather than an error:
    `teams/berts/runtime.checkpoint_for` documents that, and
    `_detect_and_redact` logs it instead.
    """
    if locale is None or supported is None or locale in supported:
        return
    raise ServiceError(
        400, f"The {TEAM_ID} model serves: {', '.join(sorted(supported))}."
    )


def _report_locale_routing(verdict: Any) -> None:
    """Say which language a document resolved to and which checkpoint reads it.

    Only a team that holds fewer checkpoints than the platform names locales
    has anything to report, and it has two things:

    * the document resolved to a language this team has no checkpoint for, so
      the default checkpoint answers it;
    * nothing named the language at all -- too few letters, or a detection the
      detector was not confident about -- so the default checkpoint answers it
      and the response reports that default. That is the case a caller
      experiences as "it is not routing to the right model": the per-page
      `locale_detection_*` lines say why, and this one says which checkpoint
      paid for it.

    A caller that pinned the locale chose it, so there is nothing surprising
    to report about the choice itself.
    """
    supported = _supported_locales()
    if supported is None or verdict.source == "pinned":
        return
    checkpoints = ",".join(sorted(supported))
    if verdict.locale not in supported:
        log.warning(
            "locale_routing_unsupported team=%s document_locale=%s checkpoints=%s",
            TEAM_ID,
            verdict.locale,
            checkpoints,
        )
    elif not verdict.detected:
        log.info(
            "locale_routing_fallback team=%s document_locale=%s checkpoints=%s",
            TEAM_ID,
            verdict.locale,
            checkpoints,
        )


def _selected_labels(environ: dict[str, object]) -> frozenset[str] | None:
    """Return the labels `X-Mask-Labels` asks for, or None for all of them.

    The gateway has already validated this header against the published
    taxonomy; re-checking here keeps the model service correct on its own, the
    way it re-checks `X-Model-Format` rather than trusting the proxy.

    None rather than the full taxonomy, so "no selection" stays distinguishable
    from "a selection naming everything" in the log line and the audit.
    """
    try:
        return http.mask_labels(
            str(environ.get("HTTP_X_MASK_LABELS", "")),
            allowed=label_registry.masked_labels(),
        )
    except ValueError as exc:
        raise ServiceError(400, str(exc)) from exc


def _debug_spans_requested(environ: dict[str, object]) -> bool:
    """True only for an explicit, exact `X-Debug-Spans: true` request header.

    QA-only: this is gated on nothing but the header's presence because
    reaching this endpoint at all already requires the same team-scoped
    SERVICE_TOKEN every real request needs -- no new credential, no new
    surface a caller doesn't already have to authenticate for. Real
    production traffic never sends this header, so its response shape is
    completely unchanged; this branch only ever adds one extra header, never
    replaces the masked-bytes body or its Content-Type (the gateway
    validates that verbatim against what the caller asked for, so changing
    it here would make the gateway reject the response as invalid).
    """
    return str(environ.get("HTTP_X_DEBUG_SPANS", "")).strip().lower() == "true"


def _spans_debug_header(audit: list[dict[str, object]]) -> str:
    """Compact JSON of {label, start, end, page} for every audited span.

    Offsets and label only -- the same fields the audit log itself is
    restricted to (see `_detect_and_redact`'s own comment): a value this
    service chose to mask must never be copied into a header or a log
    object. This is what lets release-mode QA scoring compare real,
    label-aware spans against ground truth instead of inferring candidate
    spans from a before/after byte diff.
    """
    return json.dumps(
        [
            {
                "label": entry.get("label"),
                "start": entry.get("start"),
                "end": entry.get("end"),
                "page": entry.get("page"),
            }
            for entry in audit
        ],
        separators=(",", ":"),
    )


R2_BUCKET = "logs"
R2_URL = "https://<account-id>.r2.cloudflarestorage.com"

# The audit fields that may leave this process.
#
# `MaskingRouter.record` writes `text` and `replaced_text` into every entry so
# a caller can explain one decision while it still holds the document. Neither
# belongs in an object written to storage: `text` is the detected value
# itself, in the clear. The `skipped` list beside it already carries offsets
# and label only, for the reason its own comment gives -- a value we chose not
# to mask must not be copied into a log object. That holds just as well for a
# value we did mask.
_PUBLISHABLE_AUDIT_FIELDS = frozenset(
    {"label", "start", "end", "page", "confidence", "source_layer", "masked"}
)


def _publishable(spans: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the audit entries with every detected value removed.

    An allow-list rather than a blocklist: a field added to the audit later is
    withheld until someone decides it is safe to publish, instead of being
    uploaded because nobody remembered to exclude it.
    """
    return [
        {key: value for key, value in span.items() if key in _PUBLISHABLE_AUDIT_FIELDS}
        for span in spans
    ]


def _upload_audit(
    request_id: str, document_name: str, spans: list[dict[str, object]]
) -> None:
    """Upload one best-effort development audit object to S3-compatible storage."""
    required = ("PROD_LOGS_S3_ACCESS_KEY", "PROD_LOGS_S3_SECRET_KEY")
    if not all(os.getenv(name) for name in required):
        log.info("audit_upload_disabled")
        return
    key = f"dev-logs/team={TEAM_ID}/request={request_id}/audit.json"
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=R2_URL,
            aws_access_key_id=os.environ["PROD_LOGS_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["PROD_LOGS_S3_SECRET_KEY"],
            region_name="auto",
            # This runs inside the masking request. Bound it: botocore otherwise
            # defaults to 60s connect and 60s read across five attempts, so one
            # unreachable endpoint would stall masking for minutes.
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        body = json.dumps(
            {"document_name": document_name, "spans": _publishable(spans)},
            separators=(",", ":"),
        ).encode()
        client.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        log.info(
            "audit_uploaded request_id=%s team=%s spans=%d bytes=%d",
            request_id,
            TEAM_ID,
            len(spans),
            len(body),
        )
    except Exception as exc:  # noqa: BLE001 - audit storage must not block masking
        log.warning(
            "audit_upload_failed request_id=%s team=%s spans=%d error=%s",
            request_id,
            TEAM_ID,
            len(spans),
            type(exc).__name__,
        )


_IDENTITY_HEADERS = (
    ("X-Model-Name", "model_name"),
    ("X-Model-Version", "model_version"),
    ("X-Model-Digest", "model_digest"),
    ("X-Model-Format", "model_format"),
    ("X-Service-Release", "service_release"),
    ("X-Git-SHA", "git_sha"),
    ("X-Image-Digest", "image_digest"),
)


def _json(start_response: Any, body: bytes) -> list[bytes]:
    start_response("200 OK", [("Content-Type", "application/json")])
    return [body]


def _health(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    """Liveness only, so nothing about the request is read."""
    del environ
    return _json(start_response, b'{"status":"UP"}')


def _model_status(
    start_response: Any,
    environ: dict[str, object],
    body_for: Callable[[str], bytes],
) -> list[bytes]:
    """Answer a status endpoint for the requested model format.

    A format this model does not support is the caller's mistake and keeps its
    own status; a model that cannot answer is a 503 with nothing leaked about
    why.
    """
    request_id = _request_id(environ)
    try:
        model_format = _model_format(environ)
    except ServiceError as exc:
        return _error(start_response, exc.status, exc.message, request_id)
    try:
        body = body_for(model_format)
    except Exception:  # noqa: BLE001 - model errors stay private
        return _error(start_response, 503, "Model is not ready.", request_id)
    return _json(start_response, body)


def _ready_body(model_format: str) -> bytes:
    if not _adapter().ready(model_format):
        raise RuntimeError("model is not loaded")
    return b'{"status":"READY","model_loaded":true}'


def _version_body(model_format: str) -> bytes:
    return json.dumps(_identity(model_format), separators=(",", ":")).encode()


def _ready(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    return _model_status(start_response, environ, _ready_body)


def _version(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    return _model_status(start_response, environ, _version_body)


def _masked_response(
    start_response: Any,
    environ: dict[str, object],
    request: _MaskRequest,
    identity: dict[str, object],
    masked: bytes,
    audit: list[dict[str, object]],
) -> list[bytes]:
    """Return the masked bytes under the headers that attribute them."""
    headers = [("Content-Type", request.content_type)]
    headers.extend(
        (header, str(identity[field])) for header, field in _IDENTITY_HEADERS
    )
    if _debug_spans_requested(environ):
        headers.append(("X-Debug-Spans", _spans_debug_header(audit)))
    start_response("200 OK", headers)
    return [masked]


def _unprocessable(
    start_response: Any, request_id: str, exc: Exception, event: str
) -> list[bytes]:
    """Record why a document was refused without putting it in the response.

    Worth logging even though the response stays generic: a malformed setting
    -- a digest with a `sha256:` prefix where a bare one belongs, say --
    otherwise surfaces as an unlogged 422 that looks identical to a genuinely
    unmaskable document, which is expensive to diagnose from outside.
    """
    log.warning(
        "%s team=%s error=%s reason=%s", event, TEAM_ID, type(exc).__name__, exc
    )
    return _error(
        start_response, 422, "The document could not be masked safely.", request_id
    )


def _mask(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    """Validate, detect, redact, answer -- each step failing on its own."""
    request_id = _request_id(environ)
    try:
        request = _mask_request(environ)
        request_id = request.request_id
        requested_locale = _document_locale(environ)
        # Refused before extraction rather than after: a caller that named a
        # language this team has no checkpoint for gets an answer about the
        # request, not about the document.
        _require_team_locale(requested_locale, _supported_locales())
        masked, audit, resolved_locale = _detect_and_redact(
            request.content,
            request.content_type,
            request.model_format,
            _selected_labels(environ),
            requested_locale,
            request_id,
        )
        if not masked:
            raise ServiceError(422, "The document could not be masked safely.")
        _upload_audit(request_id, request.document_name, audit)
        identity = _identity(request.model_format, resolved_locale)
    except ServiceError as exc:
        return _error(start_response, exc.status, exc.message, request_id)
    except CorruptInputError as exc:
        return _unprocessable(start_response, request_id, exc, "mask_unprocessable")
    except ValueError as exc:
        return _unprocessable(start_response, request_id, exc, "mask_rejected")
    except Exception:  # noqa: BLE001 - model errors stay private
        return _error(start_response, 500, "Masking failed.", request_id)
    return _masked_response(start_response, environ, request, identity, masked, audit)


# The private surface this model service exposes to the gateway, in one place.
# Anything not listed is a bad request: no route is derived from a header, a
# body or a filename.
_ROUTES: dict[tuple[str, str], Callable[[Any, dict[str, object]], list[bytes]]] = {
    ("GET", "/health"): _health,
    ("GET", "/ready"): _ready,
    ("GET", "/version"): _version,
    ("POST", "/mask"): _mask,
}


def application(environ: dict[str, object], start_response: Any) -> list[bytes]:
    route = (str(environ.get("REQUEST_METHOD")), str(environ.get("PATH_INFO")))
    handler = _ROUTES.get(route)
    if handler is None:
        return _error(
            start_response, 400, "Invalid model request.", _request_id(environ)
        )
    return handler(start_response, environ)
