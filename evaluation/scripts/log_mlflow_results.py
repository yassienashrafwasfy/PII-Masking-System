#!/usr/bin/env python3
"""Build a safe MLflow payload; callers supply credentials outside this repository."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--team-id", required=True, choices=("octo", "synthify", "berts", "mdeberta")
    )
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    payload = args.results.read_bytes()
    results = json.loads(payload)
    safe = {
        "team_id": args.team_id,
        "model_version": args.model_version,
        "evaluator_version": "v1.0",
        "results_sha256": hashlib.sha256(payload).hexdigest(),
        "fixture_count": results["fixtures"],
        "passed": results["passed"],
        "failed": results["failed"],
    }
    print(json.dumps(safe, sort_keys=True))


if __name__ == "__main__":
    main()
