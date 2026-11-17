from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.prepare_datasets import (
    CANONICAL_SCHEMA,
    DATA_PREP_VERSION,
    DATASETS,
    _assert_canonical_schema,
    _reusable_manifest,
    _sample_allocations,
    _write_json_durably,
)

RUNTIME = {"datasets": "3.0.0"}
BUILDER_SHA = "builder-sha"


def make_outputs(tmp_path: Path) -> dict[str, Path]:
    """Write a Parquet file per split and return their paths."""
    paths = {}
    for split in ("train", "validation"):
        path = tmp_path / f"openpii_{split}.parquet"
        pq.write_table(CANONICAL_SCHEMA.empty_table(), path)
        paths[split] = path
    return paths


def make_manifest(output_paths: dict[str, Path], **overrides: Any) -> dict[str, Any]:
    """Return a manifest that matches the files in *output_paths*."""
    from src.prepare_datasets import _sha256

    spec = DATASETS["openpii"]
    manifest = {
        "adapter_version": DATA_PREP_VERSION,
        "builder_sha256": BUILDER_SHA,
        "runtime_versions": RUNTIME,
        "dataset_id": spec.dataset_id,
        "revision": spec.revision,
        "target_languages": ["nl"],
        "nemotron_sample_rows": None,
        "file_sha256": {split: _sha256(path) for split, path in output_paths.items()},
    }
    manifest.update(overrides)
    return manifest


def reuse(manifest_path: Path, output_paths: dict[str, Path]) -> dict[str, Any] | None:
    return _reusable_manifest(
        manifest_path,
        output_paths,
        DATASETS["openpii"],
        BUILDER_SHA,
        RUNTIME,
        ["nl"],
        None,
    )


def test_matching_manifest_is_reused(tmp_path: Path) -> None:
    output_paths = make_outputs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(make_manifest(output_paths)))

    assert reuse(manifest_path, output_paths) is not None


def test_missing_manifest_forces_a_rebuild(tmp_path: Path) -> None:
    output_paths = make_outputs(tmp_path)
    assert reuse(tmp_path / "absent.json", output_paths) is None


def test_missing_parquet_file_forces_a_rebuild(tmp_path: Path) -> None:
    output_paths = make_outputs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(make_manifest(output_paths)))
    output_paths["train"].unlink()

    assert reuse(manifest_path, output_paths) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("adapter_version", "1900-01-01.0"),
        ("builder_sha256", "a-different-builder"),
        ("runtime_versions", {"datasets": "2.0.0"}),
        ("revision", "another-revision"),
        ("target_languages", ["nl", "fr"]),
        ("nemotron_sample_rows", 2000),
    ],
)
def test_any_changed_input_forces_a_rebuild(
    tmp_path: Path, field: str, value: Any
) -> None:
    output_paths = make_outputs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(make_manifest(output_paths, **{field: value})))

    assert reuse(manifest_path, output_paths) is None


def test_edited_parquet_file_forces_a_rebuild(tmp_path: Path) -> None:
    output_paths = make_outputs(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(make_manifest(output_paths)))
    output_paths["validation"].write_bytes(b"not the file the manifest recorded")

    assert reuse(manifest_path, output_paths) is None


def test_non_nemotron_datasets_read_every_row() -> None:
    spec = DATASETS["openpii"]
    allocations = _sample_allocations("openpii", spec, 2000)

    assert allocations == {split: None for split in spec.source_splits}


def test_nemotron_budget_spreads_evenly_across_splits() -> None:
    spec = DATASETS["nemotron"]
    allocations = _sample_allocations("nemotron", spec, 2000)

    counts = [count for count in allocations.values() if count is not None]
    assert len(counts) == len(allocations)
    assert sum(counts) == 2000
    assert max(counts) - min(counts) <= 1


def test_a_zero_budget_reads_every_nemotron_row() -> None:
    spec = DATASETS["nemotron"]
    allocations = _sample_allocations("nemotron", spec, 0)

    assert allocations == {split: None for split in spec.source_splits}


def test_canonical_schema_check_accepts_a_written_file(tmp_path: Path) -> None:
    _assert_canonical_schema(make_outputs(tmp_path).values())


def test_canonical_schema_check_rejects_a_different_schema(tmp_path: Path) -> None:
    path = tmp_path / "wrong.parquet"
    pq.write_table(pa.table({"unexpected": pa.array([1, 2])}), path)

    with pytest.raises(RuntimeError, match="Unexpected canonical schema"):
        _assert_canonical_schema([path])


def test_json_is_written_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write_json_durably(path, {"row_counts": {"train": 10}})

    assert json.loads(path.read_text(encoding="utf-8")) == {"row_counts": {"train": 10}}
