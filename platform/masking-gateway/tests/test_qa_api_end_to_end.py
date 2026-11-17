#!/usr/bin/env python3
"""Exercise a deployed team through the public gateway and model-service boundary."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[3]
TEAM_ID = os.getenv("QA_TEAM_ID", "octo")
if TEAM_ID not in {"octo", "berts"}:
    raise SystemExit("QA_TEAM_ID must be octo or berts")
MODEL_VERSION = (
    "qa-api-end-to-end"
    if TEAM_ID == "octo"
    else "07f18eb53e127ed34aa77a64e1e15f24ea622be8"
)
os.environ.update(
    {
        "SERVICE_TOKEN": "qa-api-test-token",
        "TEAM_ID": TEAM_ID,
        "ACCELERATOR": os.environ.get("ACCELERATOR", "cpu"),
        "MODEL_VERSION": MODEL_VERSION,
    }
)
if TEAM_ID == "octo":
    os.environ.update(
        {
            "DETECTION_MODE": "model_regex",
            "ONNX_MODEL_PATH": str(ROOT / "teams/octo/onnx"),
            "ONNX_MODEL_SHA256": (
                "21b0e69d83e992751c6e1420e74e17f6bb8f5765d0259651a62dd55d85a09236"
            ),
        }
    )
else:
    os.environ.update(
        {
            "ONNX_MODEL_PATH": str(ROOT / "teams/berts/onnx"),
            "ONNX_MODEL_SHA256": "e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292",
        }
    )
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))
sys.path.insert(0, str(ROOT / "platform/model-service/src"))
import gateway
import service

SAMPLE = ROOT / "teams/octo/samples/combined_development.jsonl"
REQUIRED_LABELS = {"NAME", "EMAIL", "PHONE_NUMBER", "DATE", "URL"}


def invoke(
    payload: bytes, model_format: str = "onnx"
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}
    environ: dict[str, object] = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/v1/mask",
        "HTTP_AUTHORIZATION": "Bearer qa-api-test-token",
        "HTTP_X_REQUEST_ID": "qa-api-octo",
        "HTTP_IDEMPOTENCY_KEY": "qa-api-octo",
        "HTTP_X_TEAM_ID": TEAM_ID,
        "HTTP_X_SOURCE_OBJECT_KEY": f"incoming/{TEAM_ID}/qa-api-{TEAM_ID}/sample.txt",
        "HTTP_X_SOURCE_ETAG": "qa-api-etag",
        "HTTP_X_MODEL_FORMAT": model_format,
        "CONTENT_TYPE": "text/plain",
        "HTTP_ACCEPT": "text/plain",
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": io.BytesIO(payload),
    }
    response = gateway.application(
        environ,
        lambda status, headers: captured.update(status=status, headers=dict(headers)),
    )
    return captured["status"], captured["headers"], b"".join(response)  # type: ignore[return-value,arg-type]


def first_record(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.loads(next(source))


def main() -> None:
    server = make_server("127.0.0.1", 0, service.application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ[f"{TEAM_ID.upper()}_MODEL_URL"] = (
        f"http://127.0.0.1:{server.server_port}"
    )
    try:
        record = first_record(SAMPLE)
        status, headers, masked = invoke(record["source_text"].encode())
        # Retired in contract v1.4, and retired for every team at once:
        # this used to be a Berts-only check because Berts was the only team
        # that never shipped the SafeTensors engine.
        unsupported_status, _, unsupported_body = invoke(b"x", "safetensors")
        assert unsupported_status == "400 Bad Request"
        assert b"BAD_REQUEST" in unsupported_body
    finally:
        server.shutdown()
        thread.join()
    assert status == "200 OK"
    assert headers["X-Model-Version"] == MODEL_VERSION
    for entity in record["privacy_mask"]:
        if entity["label"] in REQUIRED_LABELS:
            assert entity["value"].encode() not in masked
    print(f"Validated {TEAM_ID} model masking through the gateway boundary.")


if __name__ == "__main__":
    main()
