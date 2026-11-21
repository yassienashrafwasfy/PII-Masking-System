#!/usr/bin/env python3
"""Check the required files for each registered team."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_team(team: str) -> None:
    base = ROOT / "teams" / team
    required = [
        "README.md",
        "adapter.py",
        "onnx/manifest.json",
        "config/model.example.yaml",
        "docs/model-card.md",
    ]
    missing = [item for item in required if not (base / item).is_file()]
    if missing:
        raise SystemExit(f"{team} missing: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", choices=("octo", "synthify", "berts", "mdeberta"))
    args = parser.parse_args()
    teams = (args.team,) if args.team else ("octo", "synthify", "berts", "mdeberta")
    for team in teams:
        check_team(team)
    print(f"Validated team integration files for: {', '.join(teams)}")


if __name__ == "__main__":
    main()
