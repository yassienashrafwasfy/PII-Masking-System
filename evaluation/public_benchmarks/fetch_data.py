"""Download the three open benchmarks into the layout `loaders.py` reads.

    uv run python -m evaluation.public_benchmarks.fetch_data --out ~/benchmark-data

Each file is checked against a SHA-256 pinned here, and a mismatch stops the run. All
three are CC BY 4.0; cite them as README.md says. The data stays outside this
repository: MEDDOCAN is built on real case reports.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
import zipfile
from pathlib import Path

# MEDDOCAN: Zenodo 4279322 (version record 4279323). MultiGraSCCo: Zenodo 19489040
# (v2; v1 has annotation span errors). MedDeID: Hugging Face dataset, revision pinned.
_MEDDEID_REVISION = "45c601bccaf5e0ba5c5fabf55384a9b4c5b5346a"
FILES = (
    (
        "https://zenodo.org/api/records/4279323/files/meddocan.zip/content",
        "meddocan/meddocan.zip",
        "d0e4708b58689bc1440ede6f89e017e58d667827d927827622d73810cd68eac3",
        True,
    ),
    (
        "https://zenodo.org/api/records/19489040/files/MultiGraSCCo_annotations_v2.zip/content",
        "multigrascco/ann_v2.zip",
        "b77cff1116f5746d28fce7133929fa7e9730954e00e6ad9f01d47907ce01e98a",
        True,
    ),
    (
        "https://huggingface.co/datasets/stighellemans/meddeid-dutch-synthetic-benchmark"
        f"/resolve/{_MEDDEID_REVISION}/data/test.jsonl",
        "meddeid-nl/data/test.jsonl",
        "f1fc1a0447d1c447d742a1d32c1bb18393840665cc44d591eccc95e8cf3f5d01",
        False,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(out: Path) -> None:
    for url, relative, expected, unpack in FILES:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or _sha256(target) != expected:
            print(f"download {relative}")
            urllib.request.urlretrieve(url, target)  # noqa: S310 (pinned https URL)
        if _sha256(target) != expected:
            target.unlink()
            raise SystemExit(f"{relative}: SHA-256 does not match the pinned digest")
        if unpack:
            with zipfile.ZipFile(target) as archive:
                archive.extractall(target.parent)
        print(f"ok       {relative}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    fetch(parser.parse_args().out)
