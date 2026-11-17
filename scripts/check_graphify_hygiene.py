#!/usr/bin/env python3
"""Keep Graphify's generated deliverables separate from local cache/state."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPHIFY_DIR = ROOT / "graphify-out"
TRACKED_DELIVERABLES = {
    "graphify-out/GRAPH_REPORT.md",
    "graphify-out/graph.html",
    "graphify-out/graph.json",
    "graphify-out/manifest.json",
}


def tracked_graphify_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", "graphify-out"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {path for path in result.stdout.splitlines() if path}


def validate_json_deliverables() -> None:
    for name in ("graph.json", "manifest.json"):
        path = GRAPHIFY_DIR / name
        try:
            with path.open(encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"Invalid Graphify deliverable {path}: {error}") from error


def main() -> None:
    tracked = tracked_graphify_files()
    unexpected = sorted(tracked - TRACKED_DELIVERABLES)
    missing = sorted(TRACKED_DELIVERABLES - tracked)
    if unexpected or missing:
        raise SystemExit(
            "Graphify tracking policy violation. "
            f"Unexpected cache/state files: {unexpected}; "
            f"missing deliverables: {missing}"
        )
    validate_json_deliverables()
    print("Graphify tracking policy is valid.")


if __name__ == "__main__":
    main()
