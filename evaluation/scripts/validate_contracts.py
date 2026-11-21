#!/usr/bin/env python3
"""Verify that versioned JSON contracts and examples are valid JSON."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    ROOT / "contracts/masking-api/v1.2/openapi.json",
    ROOT / "contracts/masking-api/v1.2/examples/request.json",
    ROOT / "contracts/masking-api/v1.2/examples/response.json",
    ROOT / "contracts/masking-api/v1.3/openapi.json",
    ROOT / "contracts/masking-api/v1.3/examples/request.json",
    ROOT / "contracts/masking-api/v1.3/examples/response.json",
    ROOT / "contracts/masking-api/v1.4/openapi.json",
    ROOT / "contracts/masking-api/v1.4/examples/request.json",
    ROOT / "contracts/masking-api/v1.4/examples/response.json",
    ROOT / "contracts/masking-api/v1.5/openapi.json",
    ROOT / "contracts/masking-api/v1.5/examples/request.json",
    ROOT / "contracts/masking-api/v1.5/examples/response.json",
    ROOT / "contracts/manifests/v1.0/completion-manifest.schema.json",
    ROOT / "contracts/manifests/v1.0/failure-manifest.schema.json",
    ROOT / "contracts/evaluation/v1.0/per-file-result.schema.json",
]

for path in FILES:
    with path.open(encoding="utf-8") as handle:
        json.load(handle)
print(f"Validated {len(FILES)} contract documents.")
