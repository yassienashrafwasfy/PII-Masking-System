#!/usr/bin/env python3
"""scripts/fetch_models.py: the pinned digests, and what it does to a bad download.

No network and no kagglehub: the download itself is not under test, the
decisions around it are -- which digest a graph must match, and that a graph that
does not match is removed instead of being left where the service would load it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "fetch_models", ROOT / "scripts" / "fetch_models.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_models"] = module
    spec.loader.exec_module(module)
    return module


fetch_models = _load_script()


def test_pinned_digests_match_the_tracked_manifests() -> None:
    """A typo in one of the pinned hashes would make a good download fail."""
    for variation in fetch_models.VARIATIONS:
        for graph, pinned in variation.graphs.items():
            manifest = json.loads(
                (
                    variation.destination / Path(graph).parent / "manifest.json"
                ).read_text()
            )
            assert manifest["model_sha256"] == pinned, (variation.slug, graph)


def test_every_team_has_a_variation_and_every_slug_is_unique() -> None:
    slugs = [variation.slug for variation in fetch_models.VARIATIONS]
    assert len(slugs) == len(set(slugs))
    assert {"octo", "synthify", "berts", "ocr"} <= set(fetch_models.TEAMS)


def test_the_unconfirmed_licenses_are_flagged_on_every_berts_variation() -> None:
    berts = [v for v in fetch_models.VARIATIONS if v.team == "berts"]
    assert len(berts) == 3
    assert all("NOT CONFIRMED" in v.license_note for v in berts)
    assert not any(v.license_note for v in fetch_models.VARIATIONS if v.team != "berts")


def _variation(root: Path, content: bytes, pinned: bytes):
    destination = root / "dest"
    destination.mkdir()
    (destination / "model.onnx").write_bytes(content)
    digest = hashlib.sha256(pinned).hexdigest()
    return replace(
        fetch_models.VARIATIONS[0],
        destination=destination,
        graphs={"model.onnx": digest},
    )


def test_a_matching_graph_is_installed_and_kept() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        variation = _variation(Path(tmp), b"weights", b"weights")
        assert fetch_models.is_installed(variation)
        assert fetch_models.verify(variation) == []
        assert (variation.destination / "model.onnx").exists()


def test_a_tampered_graph_is_reported_and_deleted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        variation = _variation(Path(tmp), b"tampered", b"weights")
        assert not fetch_models.is_installed(variation)
        problems = fetch_models.verify(variation)
        assert len(problems) == 1 and "pinned SHA-256" in problems[0]
        assert not (
            variation.destination / "model.onnx"
        ).exists(), "bad weights left in place"


def test_a_missing_graph_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        variation = _variation(Path(tmp), b"x", b"x")
        (variation.destination / "model.onnx").unlink()
        assert "was not in the download" in fetch_models.verify(variation)[0]


def test_copy_keeps_subfolders_and_skips_the_upload_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source, destination = Path(tmp, "src"), Path(tmp, "dst")
        (source / "det").mkdir(parents=True)
        (source / "det" / "model.onnx").write_bytes(b"a")
        (source / "model-instance-metadata.json").write_text("{}")
        fetch_models.copy_into(source, destination)
        assert (destination / "det" / "model.onnx").read_bytes() == b"a"
        assert not (destination / "model-instance-metadata.json").exists()


def main() -> None:
    tests = [
        (name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")
    ]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")


if __name__ == "__main__":
    main()
