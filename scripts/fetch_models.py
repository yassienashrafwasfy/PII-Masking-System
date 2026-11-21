"""Download the model weights from Kaggle into the directories the code loads from.

The weights are not in this repository. They are published, with a model card and
per-variation licenses, at https://www.kaggle.com/models/yassienwasfy/pii-masking-system
and this script puts each variation where its team's adapter expects it:

    uv run --no-project --with kagglehub python scripts/fetch_models.py --team octo
    uv run --no-project --with kagglehub python scripts/fetch_models.py --team all
    uv run --no-project --with huggingface-hub python scripts/fetch_models.py \\
        --team mdeberta --source hub

``--source hub`` downloads from Hugging Face instead of Kaggle. Only the
mdeberta graph is published there (its own model repo, a quantized-relation child
of the checkpoint it was exported from); the Kaggle model holds every variation.

`kagglehub` is fetched for the run by ``uv --with`` and is deliberately not a
project dependency: it is only needed once, and adding it would change the
locked environment every service image is built from. ``--no-project`` matters:
without it uv first installs the project's whole locked environment (torch and
all) just to run a script that imports only the standard library and kagglehub.

Every ``model.onnx`` is checked against a SHA-256 pinned in this file, not
against the ``manifest.json`` that ships beside it. A manifest delivered with the
weights can only say the download is intact; the pin in the source is what says
these are the weights this repository was tested with. A mismatch deletes the
file and fails the run rather than leaving unverified weights where the service
would load them.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

KAGGLE_MODEL = "yassienwasfy/pii-masking-system/onnx"
# Published as version 1. Pinning it keeps a later upload from changing what this
# commit downloads; the digests below would catch it anyway.
KAGGLE_VERSION = "1"

ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "platform/masking-gateway/src/masking_engine/src/models/ocr/artifacts"

# Files that describe the upload rather than the model. They are not copied.
_NOT_COPIED = {"model-instance-metadata.json", "README.md", ".gitattributes"}

HUB_REPO = "masterofaudio2077/medical-pii-ner-mdeberta-v3-onnx"
# Pinned for the same reason KAGGLE_VERSION is: a later push to the hub repo
# cannot change what this commit downloads. The digest below catches it anyway.
HUB_REVISION = "2bb8de51d41ab1b1a14764282e24dd40b77510b0"


@dataclass(frozen=True)
class Variation:
    """One Kaggle variation: where it lands and the digests it must match."""

    team: str
    slug: str
    destination: Path
    # Path of each verified graph, relative to the destination, with its SHA-256.
    graphs: dict[str, str]
    license_note: str = ""
    # Hub repo holding this variation at its root; empty when Kaggle is the only home.
    hub_repo: str = ""


UNCONFIRMED = (
    "UPSTREAM LICENSE NOT CONFIRMED: see NOTICE. These are third-party weights "
    "whose authors have not stated a license."
)

VARIATIONS: tuple[Variation, ...] = (
    Variation(
        "octo",
        "octo-xlmr-fp32",
        ROOT / "teams/octo/onnx",
        {
            "model.onnx": "21b0e69d83e992751c6e1420e74e17f6bb8f5765d0259651a62dd55d85a09236"
        },
    ),
    Variation(
        "synthify",
        "synthify-gliner-fp32",
        ROOT / "teams/synthify/onnx",
        {
            "model.onnx": "8a6baba537c4a00d518586a2b56f62547259f12685b9b1be9b6cfc91fa44dd45"
        },
    ),
    Variation(
        "berts",
        "berts-nl-medroberta-int8",
        ROOT / "teams/berts/onnx",
        {
            "model.onnx": "e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292"
        },
        UNCONFIRMED,
    ),
    Variation(
        "berts",
        "berts-en-bert-int8",
        ROOT / "teams/berts/onnx-en",
        {
            "model.onnx": "c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd"
        },
        UNCONFIRMED,
    ),
    Variation(
        "berts",
        "berts-fr-camembert-int8",
        ROOT / "teams/berts/onnx-fr",
        {
            "model.onnx": "2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4"
        },
        UNCONFIRMED,
    ),
    Variation(
        "mdeberta",
        "mdeberta-medical-onnx",
        ROOT / "teams/mdeberta/onnx",
        {
            "model.onnx": "b561e1fd1ac8a46e139c94744ec49dfda3b46aee63a8cfe2bf4ec1d5a975b120"
        },
        hub_repo=HUB_REPO,
    ),
    Variation(
        "ocr",
        "pp-ocrv6-small",
        OCR_DIR,
        {
            "det/model.onnx": "049d5107f028a88efb9c96857ab72af847f0be7aeb413285669a84cf00b8f973",
            "rec/model.onnx": "14a9e7d184b186fe9c0551737e0bfe53d0a3afee97806a0eeafc6565fc732a1d",
        },
    ),
)

TEAMS = sorted({variation.team for variation in VARIATIONS})


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_installed(variation: Variation) -> bool:
    """True when every graph is already in place and matches its pinned digest."""
    return all(
        (variation.destination / name).is_file()
        and sha256_of(variation.destination / name) == expected
        for name, expected in variation.graphs.items()
    )


def verify(variation: Variation) -> list[str]:
    """Return the problems with an installed variation; delete graphs that fail."""
    problems = []
    for name, expected in variation.graphs.items():
        graph = variation.destination / name
        if not graph.is_file():
            problems.append(f"{variation.slug}: {name} was not in the download")
        elif sha256_of(graph) != expected:
            graph.unlink()
            problems.append(
                f"{variation.slug}: {name} does not match its pinned SHA-256"
            )
    return problems


def copy_into(source: Path, destination: Path) -> None:
    """Copy a downloaded variation over its destination, keeping subfolders."""
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if item.is_dir() or relative.name in _NOT_COPIED:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def download(variation: Variation, source: str, *, force: bool) -> Path | str:
    """Return the downloaded folder, or a message when this source cannot serve it."""
    if source == "hub":
        if not variation.hub_repo:
            return f"{variation.slug} is not published on the hub; use --source kaggle"
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            return "huggingface-hub is missing: run with `uv run --no-project --with huggingface-hub python scripts/fetch_models.py --source hub`"
        print(f"download hf:{variation.hub_repo}@{HUB_REVISION[:10]}")
        return Path(
            snapshot_download(
                variation.hub_repo, revision=HUB_REVISION, force_download=force
            )
        )
    try:
        import kagglehub
    except ImportError:
        return "kagglehub is missing: run with `uv run --no-project --with kagglehub python scripts/fetch_models.py`"
    handle = f"{KAGGLE_MODEL}/{variation.slug}/{KAGGLE_VERSION}"
    print(f"download {handle}")
    return Path(kagglehub.model_download(handle, force_download=force))


def fetch(variation: Variation, *, force: bool, source: str = "kaggle") -> list[str]:
    if not force and is_installed(variation):
        print(f"ok       {variation.slug} (already installed, digest matches)")
        return []
    cached = download(variation, source, force=force)
    if isinstance(cached, str):
        return [cached]
    copy_into(cached, variation.destination)
    problems = verify(variation)
    if not problems:
        print(f"ok       {variation.slug} -> {variation.destination.relative_to(ROOT)}")
        if variation.license_note:
            print(f"notice   {variation.license_note}")
    return problems


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--team", choices=[*TEAMS, "all"], required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="download again even if the files already match",
    )
    parser.add_argument(
        "--source",
        choices=("kaggle", "hub"),
        default="kaggle",
        help="where to download from (hub: Hugging Face, mdeberta only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    chosen = [v for v in VARIATIONS if args.team in ("all", v.team)]
    if args.source == "hub" and args.team == "all":
        # `all` means everything the hub has, not an error for each Kaggle-only graph.
        chosen = [v for v in chosen if v.hub_repo]
    problems = [
        problem
        for v in chosen
        for problem in fetch(v, force=args.force, source=args.source)
    ]
    for problem in problems:
        print(f"FAILED   {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
