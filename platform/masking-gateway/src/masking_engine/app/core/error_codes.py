"""Define domain errors and the error body every endpoint returns.

Exceptions must not contain raw input. They can contain non-PHI fields such as
the content type, byte size, and request ID.

PII Masking API contract v1.2 section 5 fixes the error body, the status codes,
and which of them NiFi retries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.responses import JSONResponse

# The code a caller matches on, and whether NiFi may retry the same request.
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
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def error_response(
    status: int,
    message: str,
    request_id: str,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    """Return the sanitized error body for *status*.

    *message* states a limit, a media type, or a condition. It never carries
    document content, a filename, a path, or a stack trace. *request_id* must
    already be safe to echo.

    Imports fastapi lazily: the gateway image imports this module (through
    `masking_engine.label_registry`) for the taxonomy alone and never calls
    this function, and fastapi is not on its dependency list. A module-level
    import would make every gateway request importing `label_registry` fail
    to boot the process, the way it did before this function deferred it.
    """
    from fastapi.responses import JSONResponse

    headers = {"X-Request-ID": request_id}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status,
        media_type="application/json",
        headers=headers,
        content={
            "error": {
                "code": ERROR_CODES.get(status, "ERROR"),
                "message": message,
                "retryable": status in RETRYABLE_STATUS,
                "request_id": request_id,
            }
        },
    )


class MaskingError(Exception):
    """Store a masking failure and its HTTP status."""

    status_code: int = 500

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class UnsupportedMediaTypeError(MaskingError):
    status_code = 415


class CorruptInputError(MaskingError):
    status_code = 422


class ModelUnreadyError(MaskingError):
    status_code = 503


class InferenceFailureError(MaskingError):
    status_code = 500
