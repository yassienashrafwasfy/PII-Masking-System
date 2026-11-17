#!/usr/bin/env python3
"""Validate safe, synthetic per-file evaluation results without contacting MLflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for path in sorted(args.fixtures.glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        required = {"fixture_id", "model_version", "precision", "recall", "status"}
        if not required <= result.keys() or result["status"] not in {
            "passed",
            "failed",
        }:
            raise SystemExit(f"Invalid evaluation fixture: {path}")
        if not all(0 <= result[key] <= 1 for key in ("precision", "recall")):
            raise SystemExit(f"Out-of-range metric: {path}")
        results.append(result)
    if not results:
        raise SystemExit("No synthetic evaluation fixtures found")
    passed = sum(result["status"] == "passed" for result in results)
    print(
        json.dumps(
            {
                "fixtures": len(results),
                "passed": passed,
                "failed": len(results) - passed,
            }
        )
    )


if __name__ == "__main__":
    main()
