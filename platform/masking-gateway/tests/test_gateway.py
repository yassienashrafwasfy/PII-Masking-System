#!/usr/bin/env python3
"""Synthetic contract tests for the public masking gateway."""

from __future__ import annotations

import io
import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The gateway reads SERVICE_TOKEN from the environment per request, and other
# test modules in this suite set their own at import time -- pytest imports
# every module during collection, before running anything, so the last writer
# would otherwise decide this module's credential. Setting it here makes the
# import work standalone; `_serve` re-asserts it per request so a shared
# process cannot take it away.
SERVICE_TOKEN = "synthetic-gateway-token"
os.environ["SERVICE_TOKEN"] = SERVICE_TOKEN
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(SRC))
gateway = importlib.import_module("gateway")


class ModelService(BaseHTTPRequestHandler):
    block_condition = threading.Condition()
    block_release = threading.Event()
    blocked_requests = 0

    @classmethod
    def reset_blocking(cls) -> None:
        with cls.block_condition:
            cls.blocked_requests = 0
        cls.block_release = threading.Event()

    @classmethod
    def wait_for_blocked(cls, count: int) -> bool:
        with cls.block_condition:
            return cls.block_condition.wait_for(
                lambda: cls.blocked_requests >= count, timeout=5
            )

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - HTTP method name
        if self.path == "/ready":
            body = b'{"status":"READY","model_loaded":true}'
        elif self.path == "/version":
            body = b'{"model_name":"test-model","model_version":"test-v1"}'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - HTTP method name
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if body.startswith(b"hold-"):
            with type(self).block_condition:
                type(self).blocked_requests += 1
                type(self).block_condition.notify_all()
            type(self).block_release.wait(timeout=5)
        if body == b"unmappable":
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", self.headers["Content-Type"])
        self.send_header("X-Model-Name", "test-model")
        self.send_header("X-Model-Version", "test-v1")
        self.send_header("X-Model-Digest", "sha256:test")
        self.send_header("X-Model-Format", self.headers["X-Model-Format"])
        type(self).last_document_locale = self.headers.get("X-Document-Locale")
        self.send_header("X-Service-Release", "test-release")
        self.send_header("X-Git-SHA", "test-sha")
        self.send_header("X-Image-Digest", "sha256:test-image")
        self.end_headers()
        self.wfile.write(body.replace(b"synthetic@example.org", b"[EMAIL]"))


_UPSTREAM_URL: str | None = None

# Every response the gateway returns for a successful mask carries these.
_REQUIRED_RESPONSE_HEADERS = {
    "X-Request-ID",
    "X-API-Version",
    "X-Service-Release",
    "X-Git-SHA",
    "X-Image-Digest",
    "X-Model-Name",
    "X-Model-Version",
    "X-Model-Digest",
    "X-Model-Format",
}


def upstream_url() -> str:
    """Return the private model service, starting it on first use.

    One server for the whole module rather than one per case: it holds no
    per-request state worth isolating, and every case that does depend on its
    state (the concurrency ones) resets that itself. The thread is a daemon, so
    it needs no shutdown -- which is what lets each test call this
    independently instead of nesting inside a shared `with` block.
    """
    global _UPSTREAM_URL
    if _UPSTREAM_URL is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelService)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _UPSTREAM_URL = f"http://127.0.0.1:{server.server_port}"
        for team_id in gateway.SUPPORTED_TEAMS:
            os.environ[f"{team_id.upper().replace('-', '_')}_MODEL_URL"] = _UPSTREAM_URL
    return _UPSTREAM_URL


def _serve(environ: dict[str, object]) -> tuple[str, dict[str, str], bytes]:
    """Call the WSGI app and capture the status, headers and body it returned."""
    # Re-asserted per request rather than only at import: the gateway reads the
    # credential from the environment on every call, and any module sharing
    # this process may have set its own since collection.
    os.environ["SERVICE_TOKEN"] = SERVICE_TOKEN
    captured: dict[str, object] = {}
    response = gateway.application(
        environ,
        lambda status, headers: captured.update(status=status, headers=dict(headers)),
    )
    return captured["status"], captured["headers"], b"".join(response)  # type: ignore[return-value,arg-type]


def invoke(
    team_id: str, payload: bytes, **changes: str
) -> tuple[str, dict[str, str], bytes]:
    environ: dict[str, object] = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/v1/mask",
        "HTTP_AUTHORIZATION": f"Bearer {SERVICE_TOKEN}",
        "HTTP_X_REQUEST_ID": "synthetic-request-123",
        "HTTP_IDEMPOTENCY_KEY": "synthetic-request-123",
        "HTTP_X_TEAM_ID": team_id,
        "HTTP_X_SOURCE_OBJECT_KEY": f"incoming/{team_id}/synthetic-request-123/fixture.txt",
        "HTTP_X_SOURCE_ETAG": "synthetic-etag",
        "CONTENT_TYPE": "text/plain",
        "HTTP_ACCEPT": "text/plain",
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": io.BytesIO(payload),
    }
    environ.update(changes)
    return _serve(environ)


def error_of(body: bytes) -> dict[str, object]:
    """Return the contract's error object, so a message can be checked exactly.

    `b"..." in body` cannot tell a correct message from one with extra text
    around it, which is the whole difference between a sanitized error and a
    leaked one.
    """
    return json.loads(body)["error"]


def get(path: str, **changes: str) -> tuple[str, dict[str, str], bytes]:
    environ: dict[str, object] = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    environ.update(changes)
    return _serve(environ)


def test_every_team_masks_and_returns_the_required_headers() -> None:
    upstream_url()
    for team_id in gateway.SUPPORTED_TEAMS:
        status, headers, body = invoke(team_id, b"Contact synthetic@example.org.")
        assert (
            status == "200 OK" and body and _REQUIRED_RESPONSE_HEADERS <= headers.keys()
        )
        assert b"synthetic@example.org" not in body
        assert headers["Content-Type"] == "text/plain"
        assert headers["X-Request-ID"] == "synthetic-request-123"
        assert headers["X-Model-Format"] == "onnx"


def test_the_retired_safetensors_runtime_is_refused_at_the_edge() -> None:
    """Contract v1.4 narrowed the enum, so this is a 400 rather than a 200.

    A v1.3 caller could ask for the torch/SafeTensors engine and be served by
    it. Nothing ships that engine any more, and the refusal happens in the
    gateway rather than at the model service so the caller learns the value is
    invalid instead of that some team happens not to supply it.
    """
    upstream_url()
    status, _, body = invoke(
        "octo", b"Contact synthetic@example.org.", HTTP_X_MODEL_FORMAT="safetensors"
    )
    assert status == "400 Bad Request", status
    assert b"X-Model-Format must be onnx." in body, body


def test_a_requested_document_locale_is_honoured_and_echoed() -> None:
    # `X-Document-Locale` overrides language detection for one request. It
    # is the second header that shapes masking output, and like
    # `X-Mask-Labels` it is checked at the edge -- a caller that asked for
    # a language this release cannot write gets an error, not a document
    # in another language.
    upstream_url()
    status, headers, _ = invoke(
        "octo", b"Contact synthetic@example.org.", HTTP_X_DOCUMENT_LOCALE="en"
    )
    assert status == "200 OK", status
    assert headers["X-Document-Locale"] == "en", headers
    assert ModelService.last_document_locale == "en"


def test_a_document_locale_is_matched_without_regard_to_case() -> None:
    # Matched without regard to case, and the canonical spelling is what
    # travels on, so the service re-validates the value the router is given.
    upstream_url()
    status, headers, _ = invoke(
        "octo", b"Contact synthetic@example.org.", HTTP_X_DOCUMENT_LOCALE="NL_be"
    )
    assert status == "200 OK" and headers["X-Document-Locale"] == "nl_BE", headers
    assert ModelService.last_document_locale == "nl_BE"


def test_an_unsupported_document_locale_is_refused() -> None:
    upstream_url()
    for raw in ("en_GB", "english", "pt_BR"):
        status, _, body = invoke("octo", b"x", HTTP_X_DOCUMENT_LOCALE=raw)
        assert status == "400 Bad Request" and b"BAD_REQUEST" in body, raw


def test_an_absent_document_locale_leaves_detection_to_the_service() -> None:
    # Absent is the normal case: the language is detected from the document
    # and the response says nothing, because the gateway is not what
    # detects it.
    upstream_url()
    status, headers, _ = invoke("octo", b"Contact synthetic@example.org.")
    assert status == "200 OK" and "X-Document-Locale" not in headers, headers
    assert ModelService.last_document_locale == ""


def test_health_reports_the_deployed_identity() -> None:
    upstream_url()
    # Restored afterwards. /health echoes these unvalidated, but they are not
    # values `load_settings` accepts -- IMAGE_DIGEST there must be sha256 plus
    # 64 hex digits. Left set, they make every later load_settings() in the
    # process raise, which reaches the model-service suite as an unexplained
    # 422. Harmless while this file ran in its own process; not once it is
    # collected alongside the rest.
    previous = {name: os.environ.get(name) for name in ("GIT_SHA", "IMAGE_DIGEST")}
    os.environ.update(
        {"GIT_SHA": "gateway-test-sha", "IMAGE_DIGEST": "sha256:gateway-test"}
    )
    try:
        status, headers, body = get("/health")
        assert status == "200 OK" and body == b'{"status":"UP"}'
        assert headers["X-Git-SHA"] == "gateway-test-sha"
        assert headers["X-Image-Digest"] == "sha256:gateway-test"
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_ready_and_version_report_the_loaded_model() -> None:
    upstream_url()
    status, _, body = get("/ready")
    assert status == "200 OK" and body == b'{"status":"READY","model_loaded":true}'
    status, _, body = get("/version")
    assert status == "200 OK" and b'"model_name":"test-model"' in body


def test_an_invalid_model_format_is_refused_on_ready() -> None:
    upstream_url()
    status, _, body = get("/ready", HTTP_X_MODEL_FORMAT="invalid")
    assert status == "400 Bad Request" and b"BAD_REQUEST" in body


def test_a_request_without_a_credential_is_refused() -> None:
    upstream_url()
    status, headers, body = invoke("octo", b"x", HTTP_AUTHORIZATION="")
    assert (
        status == "401 Unauthorized" and headers["Content-Type"] == "application/json"
    )
    assert b"UNAUTHORIZED" in body


def test_an_unsupported_media_type_is_refused() -> None:
    upstream_url()
    status, _, body = invoke("octo", b"x", CONTENT_TYPE="image/png")
    assert status == "415 Unsupported Media Type" and b"UNSUPPORTED_MEDIA_TYPE" in body


def test_a_request_without_a_source_etag_is_refused() -> None:
    upstream_url()
    status, _, body = invoke("octo", b"x", HTTP_X_SOURCE_ETAG="")
    assert status == "400 Bad Request" and b"BAD_REQUEST" in body


def test_a_request_id_carrying_a_header_injection_is_refused() -> None:
    upstream_url()
    status, headers, _ = invoke("octo", b"x", HTTP_X_REQUEST_ID="bad\r\nheader")
    assert status == "400 Bad Request" and headers["X-Request-ID"] == ""


def test_an_invalid_model_format_is_refused() -> None:
    upstream_url()
    status, _, body = invoke("octo", b"x", HTTP_X_MODEL_FORMAT="invalid")
    assert status == "400 Bad Request" and b"BAD_REQUEST" in body


def test_a_document_the_model_cannot_map_is_unprocessable() -> None:
    upstream_url()
    status, _, body = invoke("octo", b"unmappable")
    assert status == "422 Unprocessable Content" and b"UNPROCESSABLE_DOCUMENT" in body


def test_a_routing_segment_under_the_team_prefix_is_allowed() -> None:
    # A routing segment under the team prefix is how a caller masks part of
    # what it holds rather than everything (docs/document-subsets.md). The
    # key binding is a prefix test, so the extra segment must pass while a
    # key naming another team must still be refused.
    upstream_url()
    for suffix in ("requests/routed/fixture.txt", "requests/routed/a/b.txt"):
        status, _, _ = invoke(
            "octo",
            b"Contact synthetic@example.org.",
            HTTP_X_SOURCE_OBJECT_KEY=f"incoming/octo/{suffix}",
        )
        assert status == "200 OK", suffix


def test_a_source_key_naming_another_team_is_refused() -> None:
    upstream_url()
    status, _, body = invoke(
        "octo",
        b"x",
        HTTP_X_SOURCE_OBJECT_KEY="incoming/synthify/requests/routed/fixture.txt",
    )
    assert status == "400 Bad Request" and b"BAD_REQUEST" in body


def test_mask_labels_narrow_masking_for_one_request() -> None:
    # `X-Mask-Labels` narrows masking for one request. The gateway checks it
    # against the published taxonomy so a typo fails at the edge instead of
    # reaching a detector that would mask less than the caller believed.
    upstream_url()
    status, _, _ = invoke(
        "octo",
        b"Contact synthetic@example.org.",
        HTTP_X_MASK_LABELS="NAME, organization_name",
    )
    assert status == "200 OK"


def test_an_unknown_mask_label_is_refused() -> None:
    upstream_url()
    for raw in ("NAEM", "NAME,NOPE"):
        status, _, body = invoke("octo", b"x", HTTP_X_MASK_LABELS=raw)
        assert status == "400 Bad Request" and b"BAD_REQUEST" in body, raw


def test_omitting_mask_labels_is_unchanged_behaviour() -> None:
    # Omitting it is unchanged behaviour, which every other assertion here
    # already relies on.
    upstream_url()
    assert invoke("octo", b"x", HTTP_X_MASK_LABELS="")[0] != "400 Bad Request"


def test_a_second_request_for_a_busy_team_is_refused_with_retry_after() -> None:
    upstream_url()
    ModelService.reset_blocking()
    first_result: list[str] = []
    first = threading.Thread(
        target=lambda: first_result.append(invoke("octo", b"hold-first")[0]),
        daemon=True,
    )
    first.start()
    assert ModelService.wait_for_blocked(1)
    status, headers, _ = invoke("octo", b"second")
    assert status == "429 Too Many Requests"
    assert headers["Retry-After"] == "5"
    ModelService.block_release.set()
    first.join(timeout=5)
    assert first_result == ["200 OK"]


def test_two_teams_mask_concurrently() -> None:
    upstream_url()
    ModelService.reset_blocking()
    cross_team_results: list[str] = []
    cross_team_threads = [
        threading.Thread(
            target=lambda selected=team_id: cross_team_results.append(
                invoke(selected, f"hold-{selected}".encode())[0]
            ),
            daemon=True,
        )
        for team_id in ("octo", "synthify")
    ]
    for thread in cross_team_threads:
        thread.start()
    assert ModelService.wait_for_blocked(2)
    ModelService.block_release.set()
    for thread in cross_team_threads:
        thread.join(timeout=5)
    assert sorted(cross_team_results) == ["200 OK", "200 OK"]


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------- #
# Status endpoints
# --------------------------------------------------------------------------- #


class _Stub(BaseHTTPRequestHandler):
    """A model service that answers however the case at hand needs it to."""

    status = 200
    content_type = "application/json"
    body = b"{}"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - HTTP method name
        self.send_response(type(self).status)
        self.send_header("Content-Type", type(self).content_type)
        self.end_headers()
        self.wfile.write(type(self).body)


def _stub_upstream(team_id: str, **behaviour: object) -> str:
    """Point *team_id* at a stub answering with *behaviour*, for this process."""
    for name, value in behaviour.items():
        setattr(_Stub, name, value)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    os.environ[f"{team_id.upper()}_MODEL_URL"] = url
    return url


def _restore_upstream(team_id: str) -> None:
    os.environ[f"{team_id.upper()}_MODEL_URL"] = upstream_url()


def test_a_status_endpoint_refuses_a_team_this_gateway_does_not_serve() -> None:
    upstream_url()
    for path in ("/ready", "/version"):
        status, _, body = get(path, HTTP_X_TEAM_ID="nobody")
        assert status == "400 Bad Request", path
        assert error_of(body)["message"] == "Unsupported team.", path


def test_an_invalid_model_format_is_refused_on_version() -> None:
    upstream_url()
    status, _, body = get("/version", HTTP_X_MODEL_FORMAT="invalid")
    assert status == "400 Bad Request" and b"BAD_REQUEST" in body


def test_a_status_endpoint_defaults_to_the_deployment_s_own_team() -> None:
    upstream_url()
    previous = os.environ.get("DEFAULT_TEAM_ID")
    os.environ["DEFAULT_TEAM_ID"] = "berts"
    try:
        assert get("/ready")[0] == "200 OK"
        os.environ["DEFAULT_TEAM_ID"] = "nobody"
        assert get("/ready")[0] == "400 Bad Request"
    finally:
        if previous is None:
            os.environ.pop("DEFAULT_TEAM_ID", None)
        else:
            os.environ["DEFAULT_TEAM_ID"] = previous


def test_a_model_that_cannot_be_reached_is_not_ready() -> None:
    upstream_url()
    previous = os.environ["SYNTHIFY_MODEL_URL"]
    timeout = os.environ.get("UPSTREAM_TIMEOUT_SECONDS")
    try:
        # A port nothing listens on. Bounded by a short timeout rather than
        # trusting the host to refuse: inside a container or WSL the connection
        # is dropped rather than reset, and the default wait is two minutes.
        os.environ["UPSTREAM_TIMEOUT_SECONDS"] = "0.25"
        os.environ["SYNTHIFY_MODEL_URL"] = "http://127.0.0.1:1"
        for path in ("/ready", "/version"):
            status, headers, body = get(path, HTTP_X_TEAM_ID="synthify")
            assert status == "503 Service Unavailable", path
            assert error_of(body)["message"] == "Model is not ready."
            assert b"127.0.0.1" not in body
            assert headers["Retry-After"] == str(gateway.RETRY_AFTER_SECONDS)
        # An unset URL is the same answer, and still says nothing about why.
        os.environ["SYNTHIFY_MODEL_URL"] = ""
        assert get("/ready", HTTP_X_TEAM_ID="synthify")[0] == "503 Service Unavailable"
    finally:
        os.environ["SYNTHIFY_MODEL_URL"] = previous
        if timeout is None:
            os.environ.pop("UPSTREAM_TIMEOUT_SECONDS", None)
        else:
            os.environ["UPSTREAM_TIMEOUT_SECONDS"] = timeout


def test_a_model_answering_anything_but_json_200_is_not_ready() -> None:
    upstream_url()
    try:
        _stub_upstream("synthify", status=503, content_type="application/json")
        assert get("/ready", HTTP_X_TEAM_ID="synthify")[0] == "503 Service Unavailable"
        _stub_upstream("synthify", status=200, content_type="text/plain")
        assert get("/ready", HTTP_X_TEAM_ID="synthify")[0] == "503 Service Unavailable"
        # A charset parameter is part of the media type, not a different one.
        _stub_upstream(
            "synthify",
            status=200,
            content_type="application/json; charset=utf-8",
            body=b'{"status":"READY"}',
        )
        status, _, body = get("/ready", HTTP_X_TEAM_ID="synthify")
        assert status == "200 OK" and body == b'{"status":"READY"}'
    finally:
        _restore_upstream("synthify")


def test_health_needs_neither_a_model_nor_a_deployed_identity() -> None:
    previous = {
        name: os.environ.pop(name, None) for name in ("GIT_SHA", "IMAGE_DIGEST")
    }
    try:
        status, headers, body = get("/health")
        assert status == "200 OK" and body == b'{"status":"UP"}'
        assert headers["X-Git-SHA"] == "unknown"
        assert headers["X-Image-Digest"] == "unknown"
        assert headers["Content-Type"] == "application/json"
    finally:
        for name, value in previous.items():
            if value is not None:
                os.environ[name] = value


def test_a_route_the_contract_does_not_name_is_a_bad_request() -> None:
    upstream_url()
    for method, path in (
        ("GET", "/"),
        ("GET", "/v1/mask"),
        ("POST", "/health"),
        ("DELETE", "/ready"),
        ("GET", "/ready/"),
    ):
        status, _, body = _serve({"REQUEST_METHOD": method, "PATH_INFO": path})
        assert status == "400 Bad Request", (method, path)
        assert error_of(body)["message"] == "Invalid masking request."


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #


def test_a_concurrency_limit_must_be_a_positive_integer() -> None:
    previous = os.environ.get("SYNTHETIC_LIMIT")
    try:
        assert gateway._positive_integer("SYNTHETIC_LIMIT", 3) == 3
        os.environ["SYNTHETIC_LIMIT"] = "7"
        assert gateway._positive_integer("SYNTHETIC_LIMIT", 3) == 7
        for bad in ("0", "-1", "two", "", "1.5"):
            os.environ["SYNTHETIC_LIMIT"] = bad
            try:
                gateway._positive_integer("SYNTHETIC_LIMIT", 3)
            except RuntimeError as error:
                assert "SYNTHETIC_LIMIT must be a positive integer" in str(error)
            else:
                raise AssertionError(f"accepted {bad!r}")
    finally:
        if previous is None:
            os.environ.pop("SYNTHETIC_LIMIT", None)
        else:
            os.environ["SYNTHETIC_LIMIT"] = previous


def test_the_gateway_loads_the_taxonomy_without_the_engine_package() -> None:
    # The point of `_load_core_module`: the gateway image installs none of what
    # `masking_engine/__init__.py` imports, so each core module has to load on
    # its own -- registered under its own name, never as a package submodule.
    module = gateway._load_core_module("locales")
    assert module is sys.modules["gateway_locales"]
    assert module.__name__ == "gateway_locales"
    assert gateway.MASKABLE_LABELS
    assert "EMAIL" in gateway.MASKABLE_LABELS
    assert "nl_BE" in gateway.SUPPORTED_LOCALES
    assert gateway.SUPPORTED_LOCALES == module.SUPPORTED_LOCALES
    # Registered before execution: `locales.py` resolves a dataclass field
    # annotation through `sys.modules[cls.__module__]`, which is empty until.
    assert gateway._load_core_module("label_registry").masked_labels()


def test_one_slot_per_supported_team() -> None:
    assert set(gateway.SLOTS) == set(gateway.SUPPORTED_TEAMS)
