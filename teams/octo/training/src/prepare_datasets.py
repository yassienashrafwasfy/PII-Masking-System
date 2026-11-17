"""Download and normalize the supported PII datasets."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import platform
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from importlib.metadata import version
from itertools import pairwise
from numbers import Integral
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from filelock import FileLock

DATA_PREP_VERSION = "2026-07-28.1"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    dataset_id: str
    revision: str
    source_splits: tuple[str, ...]
    split_map: dict[str, str]
    license: str
    role: str


DATASETS = {
    "openpii": DatasetSpec(
        key="openpii",
        dataset_id="ai4privacy/pii-masking-openpii-1.5m",
        revision="a785eb528e28be2693c3718a27e066970de5dadb",
        source_splits=("train", "validation"),
        split_map={"train": "train", "validation": "validation"},
        license="CC-BY-4.0",
        role="Primary multilingual token-classification dataset",
    ),
    "gretel": DatasetSpec(
        key="gretel",
        dataset_id="gretelai/synthetic_pii_finance_multilingual",
        revision="7b844d16738527a04264f50214cb426a4cea0897",
        source_splits=("train", "test"),
        split_map={"train": "train", "test": "validation"},
        license="Apache-2.0",
        role="Multilingual structured-finance stress test",
    ),
    "nemotron": DatasetSpec(
        key="nemotron",
        dataset_id="nvidia/Nemotron-PII",
        revision="b70ffaf5ff39e079776134c5bf4381f00a9fd1ed",
        source_splits=("train", "test"),
        split_map={"train": "train", "test": "validation"},
        license="CC-BY-4.0",
        role="English document-format and taxonomy reference",
    ),
}

GRETEL_LANGUAGES = {
    "English": "en",
    "Dutch": "nl",
    "France": "fr",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Italian": "it",
    "Swedish": "sv",
}

CANONICAL_COLUMNS = (
    "source_text",
    "privacy_mask",
    "language",
    "source_dataset",
    "dataset_split",
)

CANONICAL_SCHEMA = pa.schema(
    [
        pa.field("source_text", pa.string()),
        pa.field(
            "privacy_mask",
            pa.list_(
                pa.struct(
                    [
                        pa.field("label", pa.string()),
                        pa.field("start", pa.int64()),
                        pa.field("end", pa.int64()),
                        pa.field("value", pa.string()),
                    ]
                )
            ),
        ),
        pa.field("language", pa.string()),
        pa.field("source_dataset", pa.string()),
        pa.field("dataset_split", pa.string()),
    ]
)

SOURCE_COLUMNS = {
    "openpii": ("source_text", "privacy_mask", "language"),
    "gretel": ("generated_text", "pii_spans", "language"),
    "nemotron": ("text", "spans"),
}


def _parse_span_list(value: Any) -> list[dict[str, Any]]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError(
            "Annotation container is null. Use [] for an annotated negative."
        )
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes, dict)):
        value = value.tolist()
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, str):
        for parser in (json.loads, ast.literal_eval):
            try:
                value = parser(value)
                break
            except (json.JSONDecodeError, ValueError, SyntaxError):
                continue
    if not isinstance(value, list):
        raise TypeError(f"Expected a span list, received {type(value).__name__}")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("Every span must be an object")
    return value


def _normalize_spans(
    source_text: str,
    value: Any,
    *,
    source_value_key: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for span in _parse_span_list(value):
        start = span.get("start")
        end = span.get("end")
        label = span.get("label")
        if isinstance(start, bool) or not isinstance(start, Integral):
            raise TypeError(f"Span has a non-integral start offset: {span!r}")
        if isinstance(end, bool) or not isinstance(end, Integral):
            raise TypeError(f"Span has a non-integral end offset: {span!r}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Span has an invalid label: {span!r}")
        start, end = int(start), int(end)
        if not 0 <= start < end <= len(source_text):
            raise ValueError(
                f"Span offsets [{start}, {end}) exceed text length {len(source_text)}"
            )
        actual_value = source_text[start:end]
        if source_value_key:
            supplied_value = span.get(source_value_key)
            if not isinstance(supplied_value, str):
                raise ValueError(
                    f"Span is missing string field {source_value_key!r}: {span!r}"
                )
            if actual_value != supplied_value:
                raise ValueError(
                    f"Span value mismatch at [{start}, {end}): {supplied_value!r} != {actual_value!r}"
                )
        normalized.append(
            {
                "label": label,
                "start": start,
                "end": end,
                "value": actual_value,
            }
        )
    ordered = sorted(normalized, key=lambda item: (item["start"], item["end"]))
    for previous, current in pairwise(ordered):
        if current["start"] < previous["end"]:
            raise ValueError(
                f"Overlapping spans cannot be represented as BIO labels: {previous!r}, {current!r}"
            )
    return normalized


def _canonical_frame(
    frame: pd.DataFrame,
    *,
    text_column: str,
    spans_column: str,
    languages: Iterable[str],
    dataset_id: str,
    split: str,
    source_value_key: str | None = None,
) -> pd.DataFrame:
    if frame[text_column].isna().any():
        raise ValueError(f"{dataset_id}:{split} contains null source text")
    source_text = frame[text_column].astype(str)
    blank = source_text.str.strip().eq("")
    if blank.any():
        raise ValueError(
            f"{dataset_id}:{split} contains {int(blank.sum())} blank source texts"
        )
    language_values = list(languages)
    if len(language_values) != len(frame):
        raise ValueError("Language mapping length does not match the dataset frame")
    result = pd.DataFrame(
        {
            "source_text": source_text,
            "privacy_mask": [
                _normalize_spans(
                    text,
                    spans,
                    source_value_key=source_value_key,
                )
                for text, spans in zip(source_text, frame[spans_column], strict=True)
            ],
            "language": language_values,
            "source_dataset": dataset_id,
            "dataset_split": split,
        }
    )
    return result.loc[:, CANONICAL_COLUMNS]


def adapt_openpii(
    frame: pd.DataFrame, split: str, target_languages: set[str]
) -> pd.DataFrame:
    selected = frame[frame["language"].isin(target_languages)].reset_index(drop=True)
    return _canonical_frame(
        selected,
        text_column="source_text",
        spans_column="privacy_mask",
        languages=selected["language"].astype(str),
        dataset_id=DATASETS["openpii"].dataset_id,
        split=split,
        source_value_key="value",
    )


def adapt_gretel(
    frame: pd.DataFrame, split: str, target_languages: set[str]
) -> pd.DataFrame:
    mapped_languages = frame["language"].map(GRETEL_LANGUAGES)
    unknown = sorted(
        frame.loc[mapped_languages.isna(), "language"].astype(str).unique()
    )
    if unknown:
        raise ValueError(f"Unknown Gretel language values: {unknown}")
    selected = frame[mapped_languages.isin(target_languages)].reset_index(drop=True)
    selected_languages = selected["language"].map(GRETEL_LANGUAGES)
    return _canonical_frame(
        selected,
        text_column="generated_text",
        spans_column="pii_spans",
        languages=selected_languages,
        dataset_id=DATASETS["gretel"].dataset_id,
        split=split,
    )


def adapt_nemotron(
    frame: pd.DataFrame, split: str, target_languages: set[str]
) -> pd.DataFrame:
    if "en" not in target_languages:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    return _canonical_frame(
        frame.reset_index(drop=True),
        text_column="text",
        spans_column="spans",
        languages=["en"] * len(frame),
        dataset_id=DATASETS["nemotron"].dataset_id,
        split=split,
        source_value_key="text",
    )


ADAPTERS: dict[str, Callable[[pd.DataFrame, str, set[str]], pd.DataFrame]] = {
    "openpii": adapt_openpii,
    "gretel": adapt_gretel,
    "nemotron": adapt_nemotron,
}


def systematic_indices(length: int, count: int) -> list[int]:
    if count < 0:
        raise ValueError("Sample count cannot be negative")
    if count == 0 or length == 0:
        return []
    if count >= length:
        return list(range(length))
    width = length / count
    return [min(length - 1, int((index + 0.5) * width)) for index in range(count)]


def _target_language_set(target_languages: Iterable[str]) -> set[str]:
    values = (
        [target_languages]
        if isinstance(target_languages, str)
        else list(target_languages)
    )
    normalized = {str(value).strip().lower() for value in values if str(value).strip()}
    if not normalized:
        raise ValueError("Specify at least one target language")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_source_rows(dataset: Any, key: str, target_languages: set[str]) -> Any:
    if key == "openpii":
        dataset = dataset.filter(
            lambda language: language in target_languages, input_columns="language"
        )
    elif key == "gretel":
        observed_languages = set(dataset.unique("language"))
        unknown_languages = sorted(
            str(value) for value in observed_languages if value not in GRETEL_LANGUAGES
        )
        if unknown_languages:
            raise ValueError(f"Unknown Gretel language values: {unknown_languages}")
        native_languages = {
            native
            for native, canonical in GRETEL_LANGUAGES.items()
            if canonical in target_languages
        }
        dataset = dataset.filter(
            lambda language: language in native_languages, input_columns="language"
        )
    elif "en" not in target_languages:
        dataset = dataset.select([])
    return dataset.select_columns(list(SOURCE_COLUMNS[key]))


def _write_normalized(
    dataset: Any, key: str, split: str, target_languages: set[str], path: Path
) -> int:
    if path.exists():
        path.unlink()
    writer = pq.ParquetWriter(path, CANONICAL_SCHEMA, compression="snappy")
    row_count = 0
    try:
        for batch in dataset.iter(batch_size=2_048):
            normalized = ADAPTERS[key](pd.DataFrame(batch), split, target_languages)
            table = pa.Table.from_pandas(
                normalized, schema=CANONICAL_SCHEMA, preserve_index=False
            )
            writer.write_table(table)
            row_count += len(normalized)
    finally:
        writer.close()
    return row_count


def _text_digests(path: Path) -> set[bytes]:
    digests = set()
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=8_192, columns=["source_text"]):
        for text in batch.column(0).to_pylist():
            digests.add(hashlib.sha256(text.encode("utf-8")).digest())
    return digests


def _assert_disjoint_texts(train_path: Path, validation_path: Path) -> None:
    validation_digests = _text_digests(validation_path)
    overlap = validation_digests & _text_digests(train_path)
    if overlap:
        raise ValueError(
            f"Train and validation contain {len(overlap)} duplicate source texts"
        )


def _runtime_identity() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "datasets": version("datasets"),
        "pandas": version("pandas"),
        "pyarrow": version("pyarrow"),
    }


def _reusable_manifest(
    manifest_path: Path,
    output_paths: dict[str, Path],
    spec: DatasetSpec,
    builder_sha256: str,
    runtime_identity: dict[str, str],
    target_languages: list[str],
    sample_rows: int | None,
) -> dict[str, Any] | None:
    """Return the manifest of a matching earlier run, or ``None`` to rebuild.

    Every input that changes the output takes part: the adapter version, the
    hash of this file, the runtime versions, the dataset revision, the language
    filter, the sample size, and the hash of each Parquet file. A run that
    differs in any of them rebuilds rather than reuse rows it did not produce.
    """
    if not manifest_path.exists() or not all(
        path.exists() for path in output_paths.values()
    ):
        return None
    cached = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = (
        cached.get("adapter_version") == DATA_PREP_VERSION
        and cached.get("builder_sha256") == builder_sha256
        and cached.get("runtime_versions") == runtime_identity
        and cached.get("dataset_id") == spec.dataset_id
        and cached.get("revision") == spec.revision
        and cached.get("target_languages") == target_languages
        and cached.get("nemotron_sample_rows") == sample_rows
        and all(
            cached.get("file_sha256", {}).get(split) == _sha256(path)
            for split, path in output_paths.items()
        )
    )
    return cached if matches else None


def _sample_allocations(
    key: str, spec: DatasetSpec, sample_rows: int
) -> dict[str, int | None]:
    """Return how many rows to take from each source split.

    ``None`` takes the whole split. Nemotron is large enough that a full pass
    costs more than it adds, so its budget spreads evenly over its splits.
    """
    if key != "nemotron" or sample_rows <= 0:
        return {split: None for split in spec.source_splits}
    base, remainder = divmod(sample_rows, len(spec.source_splits))
    return {
        split: base + int(index < remainder)
        for index, split in enumerate(spec.source_splits)
    }


def _assert_canonical_schema(paths: Iterable[Path]) -> None:
    """Raise when a written Parquet file does not carry the canonical schema."""
    for path in paths:
        if not pq.read_schema(path).equals(CANONICAL_SCHEMA, check_metadata=False):
            raise RuntimeError(f"Unexpected canonical schema in {path}")


def _write_json_durably(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* and force it to disk, so a crash cannot leave it partial."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.flush()
        os.fsync(file.fileno())


def prepare_dataset(
    key: str,
    project_dir: Path,
    target_languages: Iterable[str],
    *,
    nemotron_sample_rows: int = 2_000,
    force: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if key not in DATASETS:
        raise KeyError(f"Unknown dataset {key!r}. Choose from {sorted(DATASETS)}.")
    if nemotron_sample_rows < 0:
        raise ValueError("nemotron_sample_rows cannot be negative")

    spec = DATASETS[key]
    processed_dir = project_dir / "data" / "processed" / key
    raw_dir = project_dir / "data" / "raw" / key
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        canonical_split: processed_dir / f"{key}_{canonical_split}.parquet"
        for canonical_split in spec.split_map.values()
    }
    manifest_path = processed_dir / "manifest.json"
    target_set = _target_language_set(target_languages)
    expected_sample_rows = nemotron_sample_rows if key == "nemotron" else None
    builder_sha256 = _sha256(Path(__file__).resolve())
    runtime_identity = _runtime_identity()

    with FileLock(str(processed_dir / ".prepare.lock")):
        cached = (
            None
            if force
            else _reusable_manifest(
                manifest_path,
                output_paths,
                spec,
                builder_sha256,
                runtime_identity,
                sorted(target_set),
                expected_sample_rows,
            )
        )
        if cached is not None:
            cached["files"] = {split: str(path) for split, path in output_paths.items()}
            return cached

        from datasets import load_dataset

        row_counts: dict[str, int] = {}
        sample_allocations = _sample_allocations(key, spec, nemotron_sample_rows)

        generation = uuid.uuid4().hex
        temporary_paths = {
            split: path.with_name(f".{path.name}.{generation}.tmp")
            for split, path in output_paths.items()
        }
        temporary_manifest = manifest_path.with_name(f".manifest.{generation}.tmp")
        try:
            for source_split in spec.source_splits:
                canonical_split = spec.split_map[source_split]
                dataset = load_dataset(
                    spec.dataset_id,
                    split=source_split,
                    cache_dir=str(raw_dir / "hf_cache"),
                    revision=spec.revision,
                    token=token,
                    streaming=key == "openpii",
                )
                sample_count = sample_allocations[source_split]
                if sample_count is not None:
                    dataset = dataset.select(
                        systematic_indices(
                            len(dataset), min(sample_count, len(dataset))
                        )
                    )
                dataset = _select_source_rows(dataset, key, target_set)
                row_counts[canonical_split] = _write_normalized(
                    dataset,
                    key,
                    canonical_split,
                    target_set,
                    temporary_paths[canonical_split],
                )

            if key != "nemotron" and not any(row_counts.values()):
                raise ValueError(
                    f"{spec.dataset_id} produced zero rows for target languages {sorted(target_set)}. "
                    "check the language values and tuple syntax (for example, ('nl',))."
                )

            _assert_canonical_schema(temporary_paths.values())
            _assert_disjoint_texts(
                temporary_paths["train"], temporary_paths["validation"]
            )
            file_sha256 = {
                split: _sha256(path) for split, path in temporary_paths.items()
            }
            manifest = {
                **asdict(spec),
                "adapter_version": DATA_PREP_VERSION,
                "builder_sha256": builder_sha256,
                "runtime_versions": runtime_identity,
                "target_languages": sorted(target_set),
                "nemotron_sample_rows": expected_sample_rows,
                "row_counts": row_counts,
                "files": {split: path.name for split, path in output_paths.items()},
                "file_sha256": file_sha256,
            }
            _write_json_durably(temporary_manifest, manifest)
            for split, path in output_paths.items():
                temporary_paths[split].replace(path)
            temporary_manifest.replace(manifest_path)
        finally:
            for temporary_path in [*temporary_paths.values(), temporary_manifest]:
                if temporary_path.exists():
                    temporary_path.unlink()

    manifest["files"] = {split: str(path) for split, path in output_paths.items()}
    return manifest
