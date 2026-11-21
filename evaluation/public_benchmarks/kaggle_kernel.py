"""Kaggle kernel: run one configuration of the public-benchmark harness on a CPU session.

Not meant to be run by hand. `CONFIG` and `COMMIT` are filled in when a kernel is pushed
(see README.md). The session installs the locked environment from the repository at
that commit, downloads the pinned weights from the Kaggle model and the pinned datasets,
runs the shipped CPU ONNX path, and leaves only aggregates and offset-only predictions
in /kaggle/working/result. The repository and the data are deleted before it ends.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

CONFIG = "__CONFIG__"
COMMIT = "__COMMIT__"
TEAM = {"mdeberta-model-only": "mdeberta"}.get(CONFIG, CONFIG)
WORK = Path("/kaggle/working")
REPO, DATA, OUT = WORK / "repo", WORK / "data", WORK / "result"


def sh(command: str, cwd: Path | None = None) -> None:
    print(f"+ {command}", flush=True)
    subprocess.run(command, shell=True, check=True, cwd=cwd)


started = time.monotonic()
try:
    sh("pip install -q uv")
    sh(f"git clone -q https://github.com/yassienashrafwasfy/PII-Masking-System {REPO}")
    sh(f"git checkout -q {COMMIT}", REPO)
    sh("uv sync --locked --no-dev --no-install-project", REPO)
    sh("uv pip install -q pyyaml kagglehub", REPO)
    py = ".venv/bin/python"
    sh(f"{py} scripts/fetch_models.py --team {TEAM}", REPO)
    sh(f"{py} -m evaluation.public_benchmarks.fetch_data --out {DATA}", REPO)
    OUT.mkdir(exist_ok=True)
    sh(
        f"{py} -m evaluation.public_benchmarks.runner predict --config {CONFIG} "
        f"--data {DATA} --out {OUT}",
        REPO,
    )
    sh(
        f"{py} -m evaluation.public_benchmarks.score --data {DATA} --predictions {OUT} "
        f"--out {OUT}/results-{CONFIG}.json --markdown {OUT}/results-{CONFIG}.md",
        REPO,
    )
    (OUT / "run.json").write_text(
        json.dumps(
            {
                "config": CONFIG,
                "commit": COMMIT,
                "cpus": os.cpu_count(),
                "wall_seconds": round(time.monotonic() - started),
            }
        )
    )
finally:
    shutil.rmtree(REPO, ignore_errors=True)
    shutil.rmtree(DATA, ignore_errors=True)
