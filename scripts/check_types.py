"""Run mypy over every tracked Python file, one invocation per source group.

Why groups: several of these trees keep test files with the same module name
(`test_service.py`, `adapter.py`, ...). One mypy run over the whole repository
reports those as duplicate modules and stops, so each group is checked on its
own, with the shared settings from `[tool.mypy]` in pyproject.toml.

Why a coverage check: a group list rots silently. A new top-level directory
would otherwise be skipped by every run and nobody would notice. Any tracked
`.py` file that is neither under a group nor listed in EXCLUDED fails the run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name -> paths handed to one mypy invocation.
GROUPS: dict[str, list[str]] = {
    "gateway": ["platform/masking-gateway/src"],
    "gateway-tests": ["platform/masking-gateway/tests"],
    "gateway-config": ["platform/masking-gateway/gunicorn.conf.py"],
    "model-service": ["platform/model-service"],
    "common": ["platform/common"],
    "evaluation": [
        "evaluation/leaderboard_api",
        "evaluation/evaluator",
        "evaluation/scripts",
        "evaluation/datasets/generate_synthetic.py",
    ],
    "evaluation-tests": ["evaluation/tests"],
    "team-octo": ["teams/octo"],
    "team-synthify": ["teams/synthify"],
    "team-synthify-flat": [
        "teams/synthify/training",
        "teams/synthify/tests/test_labels.py",
        "teams/synthify/tests/test_windowing.py",
        "teams/synthify/tests/test_prepare_gliner_dataset.py",
    ],
    "team-berts": ["teams/berts"],
    "scripts": ["scripts"],
    "repo-tests": ["tests"],
    "deploy": ["deploy/migrate-env.py"],
    "misc": ["vulture_whitelist.py"],
}

# Synthify's modules are reached two ways: as the package `teams.synthify`
# (the model service loads `adapter.py` that way, so `runtime.py` uses relative
# imports) and as flat top-level modules (the training script runs as a script,
# and three tests put the directory on sys.path). One mypy run cannot hold both:
# with the directory on the search path `runtime.py` becomes a top-level module
# and its relative import is an error with no error code, which cannot be
# ignored. So the package view is the default group and the flat view gets its
# own invocation with the directory on MYPYPATH.
EXCLUDE: dict[str, str] = {
    # The one tracked file every tool skips (see EXCLUDED below).
    "team-berts": r"^teams/berts/training/tamerbert_generalization_trial\.py$",
    "team-synthify": (
        r"(^teams/synthify/training/"
        r"|^teams/synthify/tests/test_(labels|windowing|prepare_gliner_dataset)\.py$)"
    ),
}
MYPYPATH: dict[str, str] = {
    "team-synthify-flat": "teams/synthify:teams/synthify/training/src",
}

# Excluded from every tool in .pre-commit-config.yaml as well; keep in sync.
EXCLUDED = {"teams/berts/training/tamerbert_generalization_trial.py"}


def tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def uncovered() -> list[str]:
    prefixes = [p for paths in GROUPS.values() for p in paths]
    missing = []
    for path in tracked_python():
        if path in EXCLUDED:
            continue
        if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes):
            missing.append(path)
    return missing


def main() -> int:
    missing = uncovered()
    if missing:
        print("Python files not covered by any mypy group in scripts/check_types.py:")
        for path in missing:
            print(f"  {path}")
        return 2

    failed = []
    for name, paths in GROUPS.items():
        print(f"== mypy: {name}", flush=True)
        command = [sys.executable, "-m", "mypy", "--no-error-summary"]
        if name in EXCLUDE:
            command += ["--exclude", EXCLUDE[name]]
        environment = dict(os.environ)
        if name in MYPYPATH:
            environment["MYPYPATH"] = os.pathsep.join(
                str(ROOT / part) for part in MYPYPATH[name].split(":")
            )
        result = subprocess.run([*command, *paths], cwd=ROOT, env=environment)
        if result.returncode != 0:
            failed.append(name)
    if failed:
        print(f"\nmypy failed in: {', '.join(failed)}")
        return 1
    print("\nmypy: all groups clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
