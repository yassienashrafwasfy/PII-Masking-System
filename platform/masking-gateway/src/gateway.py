"""Platform gateway implementing PII Masking API contract v1.5."""

from __future__ import annotations

import hmac
import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common import http

_T = TypeVar("_T")

SUPPORTED_TEAMS = ("octo", "synthify", "berts", "mdeberta")
SUPPORTED_MEDIA_TYPES = frozenset(
    {"application/pdf", "text/plain", "text/csv", "application/json"}
)


def _load_core_module(name: str) -> Any:
    """Load one `app/core` module directly, without importing `masking_engine`.

    `from masking_engine import label_registry` runs the package's own
    `__init__.py` first, which eagerly imports every detection engine --
    fastapi, faker, pdf libraries -- because those names are its public
    surface for the model service. The gateway installs none of that by
    design (`requirements-runtime.txt` is gunicorn and packaging, nothing
    else); it only ever needs the taxonomy and the list of locales. Both
    `label_registry.py` and `locales.py` touch nothing but the standard
    library, so loading either as a standalone module -- bypassing
    `masking_engine/__init__.py` entirely -- is safe and keeps the gateway
    image thin.
    """
    path = Path(__file__).resolve().parent / "masking_engine" / "app" / "core"
    module_name = f"gateway_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, not after: `locales.py` defines a frozen
    # dataclass, and `dataclasses` resolves a field's annotation through
    # `sys.modules[cls.__module__]`. A module loaded this way is absent from
    # that table until it is put there, so the decorator raised on import.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


label_registry = _load_core_module("label_registry")
# The published taxonomy, read once. `X-Mask-Labels` is checked against it here
# so a typo fails at the edge rather than reaching a detector.
MASKABLE_LABELS = label_registry.masked_labels()
# The locales that have a surrogate profile. `X-Document-Locale` is checked
# against this here for the same reason: a caller that asked for a language
# this release cannot write gets an error rather than a document in another
# language.
SUPPORTED_LOCALES = _load_core_module("locales").SUPPORTED_LOCALES
MAX_FILE_BYTES = http.MAX_UPLOAD_BYTES
RETRY_AFTER_SECONDS = 5


def _positive_integer(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


MAX_CONCURRENT_REQUESTS_PER_TEAM = _positive_integer(
    "MAX_CONCURRENT_REQUESTS_PER_TEAM", 1
)
SLOTS = {
    team: BoundedSemaphore(MAX_CONCURRENT_REQUESTS_PER_TEAM) for team in SUPPORTED_TEAMS
}


class ContractError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def _request_id(environ: dict[str, object]) -> str:
    return http.request_id(environ)


def error(
    start_response: Any, status: int, message: str, request_id: str
) -> list[bytes]:
    """Return the contract's small, sanitized JSON error body."""
    retry_after = RETRY_AFTER_SECONDS if status in (429, 503) else None
    return http.error_body(
        start_response, status, message, request_id, retry_after=retry_after
    )


def _token_is_valid(environ: dict[str, object]) -> bool:
    token = os.getenv("SERVICE_TOKEN", "")
    authorization = str(environ.get("HTTP_AUTHORIZATION", ""))
    return bool(token) and hmac.compare_digest(authorization, f"Bearer {token}")


def _upstream_url(team_id: str) -> str:
    value = os.getenv(f"{team_id.upper().replace('-', '_')}_MODEL_URL", "").rstrip("/")
    if not value:
        raise ContractError(503, "The selected model is not available.")
    return value


def _upstream(
    team_id: str,
    path: str,
    *,
    method: str = "GET",
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Call a private model service without exposing its URL to callers."""
    request = Request(
        f"{_upstream_url(team_id)}{path}",
        data=content,
        headers=headers or {},
        method=method,
    )
    try:
        with urlopen(
            request, timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120"))
        ) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()
    except (URLError, TimeoutError) as exc:
        raise ContractError(503, "The selected model is not available.") from exc


def _health(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    """Liveness only, so nothing about the request is read."""
    del environ
    start_response(
        "200 OK",
        [
            ("Content-Type", "application/json"),
            ("X-Git-SHA", os.getenv("GIT_SHA", "unknown")),
            ("X-Image-Digest", os.getenv("IMAGE_DIGEST", "unknown")),
        ],
    )
    return [b'{"status":"UP"}']


def _team_for_status(environ: dict[str, object]) -> str:
    return str(environ.get("HTTP_X_TEAM_ID", os.getenv("DEFAULT_TEAM_ID", "octo")))


def _header(environ: dict[str, object], name: str) -> str:
    return str(environ.get(name, ""))


def _validated(status: int, read: Callable[[], _T]) -> _T:
    """Read a caller-supplied value, reporting a bad one as a contract error."""
    try:
        return read()
    except ValueError as exc:
        raise ContractError(status, str(exc)) from exc


def _model_format(environ: dict[str, object]) -> str:
    """Return the requested artifact format from the public HTTP contract."""
    return _validated(
        400, lambda: http.model_format(_header(environ, "HTTP_X_MODEL_FORMAT"))
    )


def _document_locale(environ: dict[str, object]) -> str | None:
    """Return the requested surrogate language, or None to detect it."""
    return _validated(
        400,
        lambda: http.document_locale(
            _header(environ, "HTTP_X_DOCUMENT_LOCALE"), allowed=SUPPORTED_LOCALES
        ),
    )


def _model_answered_in_json(status: int, headers: dict[str, str]) -> bool:
    """The model answered, and answered in the media type the contract names."""
    return (
        status == 200
        and headers.get("Content-Type", "").split(";", 1)[0] == "application/json"
    )


def _proxied_status(
    path: str, start_response: Any, environ: dict[str, object]
) -> list[bytes]:
    """Relay the model service's own answer at *path*.

    `/ready` and `/version` differ in one thing: which upstream path they ask.
    Everything else -- refusing an unknown team, passing the requested artifact
    format through, and treating anything but a JSON 200 as "not ready" -- is
    one behaviour, so it is written once and bound to each path in `_ROUTES`.
    """
    team_id = _team_for_status(environ)
    if team_id not in SUPPORTED_TEAMS:
        return error(start_response, 400, "Unsupported team.", _request_id(environ))
    try:
        model_format = _model_format(environ)
    except ContractError as exc:
        return error(start_response, exc.status, exc.message, _request_id(environ))
    try:
        status, headers, body = _upstream(
            team_id, path, headers={"X-Model-Format": model_format}
        )
    except ContractError:
        return error(start_response, 503, "Model is not ready.", _request_id(environ))
    if not _model_answered_in_json(status, headers):
        return error(start_response, 503, "Model is not ready.", _request_id(environ))
    start_response("200 OK", [("Content-Type", "application/json")])
    return [body]


@dataclass(frozen=True)
class _MaskRequest:
    """A validated masking request: every field was checked before it was set."""

    team_id: str
    content_type: str
    content: bytes
    request_id: str
    model_format: str
    locale: str | None


@dataclass(frozen=True)
class _Metadata:
    """The request headers the contract requires, read once and never again."""

    request_id: str
    team_id: str
    source_key: str
    source_etag: str
    idempotency_key: str
    content_type: str
    accept: str

    @classmethod
    def read(cls, environ: dict[str, object]) -> _Metadata:
        def header(name: str) -> str:
            return str(environ.get(name, ""))

        return cls(
            request_id=_request_id(environ),
            team_id=header("HTTP_X_TEAM_ID"),
            source_key=header("HTTP_X_SOURCE_OBJECT_KEY"),
            source_etag=header("HTTP_X_SOURCE_ETAG"),
            idempotency_key=header("HTTP_IDEMPOTENCY_KEY"),
            content_type=http.media_type(header("CONTENT_TYPE")),
            accept=header("HTTP_ACCEPT"),
        )


@dataclass(frozen=True)
class _Check:
    """One contract condition and the answer the caller gets when it fails."""

    holds: Callable[[_Metadata], bool]
    status: int
    message: str


# Checked in order, so the first failure decides the response. Team binding is
# checked before anything about the document: a team id that does not match the
# trusted source key is refused whatever else the request carries.
_METADATA_CHECKS: tuple[_Check, ...] = (
    _Check(
        lambda m: bool(m.request_id and m.team_id and m.idempotency_key),
        400,
        "Required request metadata is missing.",
    ),
    _Check(
        lambda m: m.team_id in SUPPORTED_TEAMS
        and m.source_key.startswith(f"incoming/{m.team_id}/"),
        400,
        "Team does not match the trusted source key.",
    ),
    _Check(lambda m: bool(m.source_etag), 400, "Required request metadata is missing."),
    _Check(
        lambda m: m.content_type in SUPPORTED_MEDIA_TYPES,
        415,
        "The supplied media type is not supported by this release.",
    ),
    _Check(
        lambda m: m.content_type in m.accept or "*/*" in m.accept,
        400,
        "Accept must include the supplied media type.",
    ),
)


def _checked_metadata(environ: dict[str, object]) -> _Metadata:
    metadata = _Metadata.read(environ)
    for check in _METADATA_CHECKS:
        if not check.holds(metadata):
            raise ContractError(check.status, check.message)
    return metadata


def _body(environ: dict[str, object]) -> bytes:
    try:
        return http.read_body(environ, MAX_FILE_BYTES)
    except http.PayloadTooLargeError as exc:
        raise ContractError(413, str(exc)) from exc
    except ValueError as exc:
        raise ContractError(400, str(exc)) from exc


def _request(environ: dict[str, object]) -> _MaskRequest:
    metadata = _checked_metadata(environ)
    content = _body(environ)
    # The label selection, the model format and the locale are all rejected
    # here, post-auth, rather than forwarded for the model service to judge: an
    # unknown label must not reach a detector that would mask less than the
    # caller asked for, and a locale this release has no profile for must not
    # reach the router, which would fail the document late instead of answering
    # the caller now.
    _validated(
        400,
        lambda: http.mask_labels(
            _header(environ, "HTTP_X_MASK_LABELS"), allowed=MASKABLE_LABELS
        ),
    )
    return _MaskRequest(
        team_id=metadata.team_id,
        content_type=metadata.content_type,
        content=content,
        request_id=metadata.request_id,
        model_format=_model_format(environ),
        locale=_document_locale(environ),
    )


# What the model service must return for its answer to be usable. A missing
# one means the response cannot be attributed to a release, which is a 502
# rather than something to pass on to the caller.
_REQUIRED_MODEL_HEADERS = frozenset(
    {
        "X-Model-Name",
        "X-Model-Version",
        "X-Model-Digest",
        "X-Model-Format",
        "X-Service-Release",
        "X-Git-SHA",
        "X-Image-Digest",
    }
)


def _forwarded_headers(
    environ: dict[str, object],
    content_type: str,
    request_id: str,
    model_format: str,
    locale: str | None,
) -> dict[str, str]:
    """Return the headers the private model service is called with.

    Each caller-supplied value here was validated before this point and is
    forwarded verbatim; nothing is derived from the request body, and the
    upstream URL is never built from anything the caller sent.
    """
    return {
        "Content-Type": content_type,
        "Accept": content_type,
        "X-Request-ID": request_id,
        "X-Source-ETag": str(environ.get("HTTP_X_SOURCE_ETAG", "")),
        "X-Source-Object-Key": str(environ.get("HTTP_X_SOURCE_OBJECT_KEY", "")),
        "X-Model-Format": model_format,
        # Validated above and forwarded verbatim. Absent means the
        # full taxonomy, so omitting it is today's behaviour.
        "X-Mask-Labels": str(environ.get("HTTP_X_MASK_LABELS", "")),
        # QA-only opt-in, forwarded verbatim -- gated on nothing
        # but reaching this point at all, which already needs the
        # same SERVICE_TOKEN every request needs. See
        # service.py's _debug_spans_requested for why this is
        # safe to forward unconditionally: a caller without a
        # valid token never gets this far, and a caller that
        # doesn't ask for it never sees any change in response.
        "X-Debug-Spans": str(environ.get("HTTP_X_DEBUG_SPANS", "")),
        # Likewise: absent means the model service detects the
        # language from the document, which is what it did before
        # this header existed. Forwarded in the canonical spelling
        # rather than the caller's, so the service re-validates
        # the value the router will actually be given.
        "X-Document-Locale": locale or "",
    }


def _upstream_response_is_usable(
    masked: bytes,
    headers: dict[str, str],
    content_type: str,
    model_format: str,
) -> bool:
    """Return whether the model's answer can be returned to the caller."""
    return bool(
        masked
        and headers.get("Content-Type", "").split(";", 1)[0] == content_type
        and _REQUIRED_MODEL_HEADERS <= headers.keys()
        and headers["X-Model-Format"].lower() == model_format
    )


def _response_headers(
    upstream: dict[str, str],
    content_type: str,
    request_id: str,
    model_format: str,
    locale: str | None,
) -> list[tuple[str, str]]:
    """Return the caller's response headers, naming what actually answered.

    Built as an allow-list rather than by copying the upstream response, so a
    header the model service adds -- now or later -- cannot reach the caller
    unless it is named here. That is what keeps the private service's own
    details, and its URL, off the public response.
    """
    headers = [
        ("Content-Type", content_type),
        ("X-Request-ID", request_id),
        ("X-API-Version", "v1"),
        ("X-Service-Release", upstream["X-Service-Release"]),
        ("X-Git-SHA", upstream["X-Git-SHA"]),
        ("X-Image-Digest", upstream["X-Image-Digest"]),
        ("X-Model-Name", upstream["X-Model-Name"]),
        ("X-Model-Version", upstream["X-Model-Version"]),
        ("X-Model-Digest", upstream["X-Model-Digest"]),
        ("X-Model-Format", model_format),
    ]
    # Only present when the caller sent X-Debug-Spans: true -- see
    # service.py's _debug_spans_requested. Not in _REQUIRED_MODEL_HEADERS,
    # since its absence is the normal case, not an invalid response.
    debug_spans = upstream.get("X-Debug-Spans")
    if debug_spans is not None:
        headers.append(("X-Debug-Spans", debug_spans))
    if locale is not None:
        # Echoed only when the caller chose it. The gateway cannot report a
        # detected locale -- detection happens in the model service, behind
        # it -- and a header that sometimes means "you asked for this" and
        # sometimes "we guessed this" would be worse than no header.
        headers.append(("X-Document-Locale", locale))
    return headers


def _proxy_mask(
    environ: dict[str, object], request: _MaskRequest
) -> tuple[int, dict[str, str], bytes]:
    """Forward the document to the team's private model service.

    The team's concurrency slot is held for exactly the upstream call: the
    URL is resolved server-side from the trusted team id and never built from
    anything the caller sent.
    """
    if not SLOTS[request.team_id].acquire(blocking=False):
        raise ContractError(429, "The service is at its concurrency limit.")
    try:
        return _upstream(
            request.team_id,
            "/mask",
            method="POST",
            content=request.content,
            headers=_forwarded_headers(
                environ,
                request.content_type,
                request.request_id,
                request.model_format,
                request.locale,
            ),
        )
    finally:
        SLOTS[request.team_id].release()


# Upstream statuses the caller is allowed to see verbatim; anything else the
# model service returns is reported as an unavailable dependency.
_PASSED_THROUGH_STATUSES = frozenset({400, 413, 415, 422, 429})


def _mask_response(
    start_response: Any,
    request: _MaskRequest,
    status: int,
    headers: dict[str, str],
    masked: bytes,
) -> list[bytes]:
    """Turn the model service's answer into this service's answer."""
    if status != 200:
        status = status if status in _PASSED_THROUGH_STATUSES else 503
        return error(
            start_response,
            status,
            "The model could not mask the document.",
            request.request_id,
        )
    if not _upstream_response_is_usable(
        masked, headers, request.content_type, request.model_format
    ):
        return error(
            start_response,
            502,
            "The selected model returned an invalid response.",
            request.request_id,
        )
    start_response(
        "200 OK",
        _response_headers(
            headers,
            request.content_type,
            request.request_id,
            request.model_format,
            request.locale,
        ),
    )
    return [masked]


def _mask(start_response: Any, environ: dict[str, object]) -> list[bytes]:
    """Authenticate, validate, proxy, answer -- each step failing on its own."""
    request_id = _request_id(environ)
    if not _token_is_valid(environ):
        return error(start_response, 401, "Service credential is invalid.", request_id)
    try:
        request = _request(environ)
        status, headers, masked = _proxy_mask(environ, request)
    except ContractError as exc:
        return error(start_response, exc.status, exc.message, request_id)
    except ValueError:
        return error(
            start_response, 422, "The document could not be masked safely.", request_id
        )
    except Exception:  # noqa: BLE001 - return the contract's safe 500 response
        return error(start_response, 500, "Masking failed.", request_id)
    return _mask_response(start_response, request, status, headers, masked)


# The published surface, in one place. Anything not listed is a bad request:
# no route is derived from a header, a body or a filename.
_ROUTES: dict[tuple[str, str], Callable[[Any, dict[str, object]], list[bytes]]] = {
    ("GET", "/health"): _health,
    ("GET", "/ready"): partial(_proxied_status, "/ready"),
    ("GET", "/version"): partial(_proxied_status, "/version"),
    ("POST", "/v1/mask"): _mask,
}


def application(environ: dict[str, object], start_response: Any) -> list[bytes]:
    """Serve the required v1.5 endpoints without logging document content."""
    route = (str(environ.get("REQUEST_METHOD")), str(environ.get("PATH_INFO")))
    handler = _ROUTES.get(route)
    if handler is None:
        return error(
            start_response, 400, "Invalid masking request.", _request_id(environ)
        )
    return handler(start_response, environ)
