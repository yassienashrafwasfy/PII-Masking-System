#!/usr/bin/env python3
"""Exercise Octo, Synthify, and Berts through the public Compose endpoint."""

from __future__ import annotations

import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pymupdf

BASE_URL = os.getenv("MASKING_GATEWAY_URL", "http://localhost:8080")
TOKEN = os.getenv("SERVICE_TOKEN", "")
TEAM_ID = os.getenv("COMPOSE_TEAM_ID", "")
MODEL_FORMAT = os.getenv("COMPOSE_MODEL_FORMAT", "onnx")


def mask(
    team_id: str,
    payload: bytes,
    content_type: str = "text/plain",
    model_format: str = "onnx",
) -> tuple[dict[str, str], bytes]:
    suffix = "pdf" if content_type == "application/pdf" else "txt"
    request = Request(
        f"{BASE_URL}/v1/mask",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": content_type,
            "Accept": content_type,
            "X-Request-ID": f"compose-{team_id}",
            "Idempotency-Key": f"compose-{team_id}",
            "X-Team-ID": team_id,
            "X-Model-Format": model_format,
            "X-Source-ETag": "compose-demo-etag",
            "X-Source-Object-Key": f"incoming/{team_id}/compose-{team_id}/sample.{suffix}",
        },
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return dict(response.headers.items()), response.read()


def pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def ready_status(team_id: str, model_format: str = "onnx") -> int:
    request = Request(
        f"{BASE_URL}/ready",
        headers={"X-Team-ID": team_id, "X-Model-Format": model_format},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def wait_ready(team_id: str, model_format: str = "onnx") -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if ready_status(team_id, model_format) == 200:
            return
        time.sleep(2)
    raise AssertionError(f"{team_id} did not become ready within 180 seconds")


def main() -> None:
    if not TOKEN:
        raise SystemExit("Set SERVICE_TOKEN before running this smoke test.")
    payload = b"Contact Alice Example at alice@example.org."
    teams: tuple[tuple[str, str], ...] = (
        ("octo", "octo-pii-masker"),
        ("synthify", "synthify-gliner-belgian-pii-ner"),
        ("berts", "berts-pii-medroberta-nl-int8"),
        ("mdeberta", "mdeberta-medical-pii-ner"),
    )
    if TEAM_ID:
        teams = tuple(team for team in teams if team[0] == TEAM_ID)
        assert teams, f"Unsupported COMPOSE_TEAM_ID: {TEAM_ID}"
    for team_id, expected_model in teams:
        assert ready_status(team_id, MODEL_FORMAT) == 503
        headers, masked = mask(team_id, payload, model_format=MODEL_FORMAT)
        wait_ready(team_id, MODEL_FORMAT)
        assert headers["X-Model-Name"] == expected_model
        assert headers["X-Model-Version"]
        assert headers["X-Model-Format"] == MODEL_FORMAT
        assert b"alice@example.org" not in masked
        headers, masked = mask(
            team_id, pdf_bytes(payload.decode()), "application/pdf", MODEL_FORMAT
        )
        assert headers["X-Model-Name"] == expected_model
        with pymupdf.open(stream=masked, filetype="pdf") as document:
            assert "alice@example.org" not in document[0].get_text()
    try:
        mask("not-a-team", payload)
    except HTTPError as exc:
        assert exc.code == 400
    else:
        raise AssertionError("unknown team was accepted")
    print(
        "Validated "
        + ", ".join(team_id for team_id, _ in teams)
        + " through the public gateway endpoint."
    )


if __name__ == "__main__":
    main()
