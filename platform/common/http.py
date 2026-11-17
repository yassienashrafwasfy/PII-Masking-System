"""Shared WSGI request/response helpers for the gateway and model services.

Both processes are hand-written WSGI apps sitting on either side of the same
private network hop. This module holds the parts that must behave identically
on both sides -- reading the request ID, parsing the body, and shaping the
contract's error JSON -- so the two copies cannot drift out of sync the way
their status-code tables once did.
"""

from __future__ import annotations

import json
from typing import IO, Any, cast

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_MODEL_FORMATS = frozenset({"onnx"})
DEFAULT_MODEL_FORMAT = "onnx"

STATUS_TEXT = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}
ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_DOCUMENT",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}
RETRYABLE = frozenset({429, 500, 502, 503, 504})


def request_id(environ: dict[str, object]) -> str:
    """Header value, truncated, with header-injection characters rejected."""
    value = str(environ.get("HTTP_X_REQUEST_ID", ""))[:256]
    return "" if "\r" in value or "\n" in value else value


def media_type(raw: str) -> str:
    """'application/pdf; charset=utf-8' -> 'application/pdf'."""
    return raw.split(";", 1)[0].strip().lower()


def mask_labels(raw: str, *, allowed: frozenset[str]) -> frozenset[str] | None:
    """Checked label selection, or None when the header is absent.

    Absent means mask everything, so a caller that sends nothing keeps the full
    taxonomy and today's behaviour is unchanged. A value naming an unknown
    label is rejected rather than narrowed silently: a typo must never release a
    document with less masked than the caller believed. Callers wrap ValueError
    in their own error type.
    """
    if not raw.strip():
        return None
    labels = frozenset(part.strip().upper() for part in raw.split(",") if part.strip())
    unknown = sorted(labels - allowed)
    if not labels or unknown:
        raise ValueError("X-Mask-Labels must name known labels, comma-separated.")
    return labels


def model_format(raw: str, *, allowed: frozenset[str] = SUPPORTED_MODEL_FORMATS) -> str:
    """Checked artifact format. Callers wrap ValueError in their own error type."""
    value = (raw or DEFAULT_MODEL_FORMAT).lower()
    if value not in allowed:
        # Named from *allowed*, not hard-coded: an adapter may serve a
        # narrower set than the platform, and the caller has to be told
        # what that adapter actually accepts.
        raise ValueError("X-Model-Format must be " + " or ".join(sorted(allowed)) + ".")
    return value


def document_locale(raw: str, *, allowed: tuple[str, ...]) -> str | None:
    """Checked surrogate language, or None when the header is absent.

    Absent means the language is detected from the document itself, which is
    both the normal case and the behaviour before this header existed, so a
    caller that sends nothing is unaffected. An unknown locale is rejected
    rather than quietly ignored: a caller that asked for one language and
    silently received another would have no way to tell.

    Matched without regard to case, and the canonical spelling is returned, so
    "NL_be" reaches the same profile as "nl_BE" and the value that travels on
    is the one the profile table uses. Callers wrap ValueError in their own
    error type.
    """
    if not raw.strip():
        return None
    canonical = {name.casefold(): name for name in allowed}
    try:
        return canonical[raw.strip().casefold()]
    except KeyError as exc:
        raise ValueError(
            f"X-Document-Locale must be one of: {', '.join(sorted(allowed))}."
        ) from exc


class PayloadTooLargeError(ValueError):
    """The declared Content-Length exceeds the caller's limit (maps to 413)."""


def read_body(environ: dict[str, object], limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Body bytes, or ValueError naming the specific problem.

    Raises :class:`PayloadTooLargeError` (a ``ValueError`` subclass) when the
    declared size exceeds *limit*, so callers that map it to 413 don't have
    to parse the message text; every other failure is a plain ``ValueError``.
    """
    try:
        size = int(str(environ.get("CONTENT_LENGTH", "")))
    except ValueError as exc:
        raise ValueError("Content-Length is required.") from exc
    if size < 1:
        raise ValueError("Payload is empty.")
    if size > limit:
        raise PayloadTooLargeError(f"Payload exceeds {limit} bytes.")
    stream = cast(IO[bytes], environ["wsgi.input"])
    content = stream.read(size)
    if len(content) != size:
        raise ValueError("Payload length does not match Content-Length.")
    return content


def error_body(
    start_response: Any,
    status: int,
    message: str,
    rid: str,
    *,
    code_override: str | None = None,
    retry_after: int | None = None,
) -> list[bytes]:
    """The contract's sanitized JSON error. Never carries document content."""
    headers = [("Content-Type", "application/json"), ("X-Request-ID", rid)]
    if retry_after is not None:
        headers.append(("Retry-After", str(retry_after)))
    start_response(f"{status} {STATUS_TEXT[status]}", headers)
    body = {
        "error": {
            "code": code_override or ERROR_CODES[status],
            "message": message,
            "retryable": status in RETRYABLE,
            "request_id": rid,
        }
    }
    return [json.dumps(body, separators=(",", ":")).encode()]
