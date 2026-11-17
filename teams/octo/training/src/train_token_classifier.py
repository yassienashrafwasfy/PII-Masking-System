"""Fine-tune a Hugging Face encoder on canonical PII spans."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import random
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from itertools import pairwise
from numbers import Integral
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from datasets import Dataset, load_from_disk
from torch.utils.data import DataLoader
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    Trainer,
    TrainerCallback,
    TrainingArguments,
    set_seed,
)

try:
    from peft import LoraConfig, PeftModel, TaskType, get_peft_model
except ImportError:
    LoraConfig = None
    PeftModel = None
    TaskType = None
    get_peft_model = None


WORKER_VERSION = "2026-07-29.3"
DATASET_IDS = {
    "openpii": "ai4privacy/pii-masking-openpii-1.5m",
    "gretel": "gretelai/synthetic_pii_finance_multilingual",
    "nemotron": "nvidia/Nemotron-PII",
}
SHARED_PII_LABELS = {
    "ADDRESS",
    "AGE",
    "BTW_EENHEID",
    "DATE",
    "EMAIL",
    "GENDER",
    "INSZ_NISS",
    "NAME",
    "NIR",
    "ORGANIZATION_NAME",
    "PHONE_NUMBER",
    "RIZIV_INAMI",
    "RPPS",
    "URL",
}
SHARED_PII_LABEL_ALIASES = {
    "ADELI": "RPPS",
    "BUILDINGNUM": "ADDRESS",
    "CITY": "ADDRESS",
    "DOCTORNAME": "NAME",
    "DOCTOR_NAME": "NAME",
    "HOSPITALNAME": "ORGANIZATION_NAME",
    "HOSPITAL_NAME": "ORGANIZATION_NAME",
    "INAMI": "RIZIV_INAMI",
    "INSEE": "NIR",
    "INSZ": "INSZ_NISS",
    "NATIONAL_ID": "INSZ_NISS",
    "NISS": "INSZ_NISS",
    "NSS": "NIR",
    "PATIENTNAME": "NAME",
    "PATIENT_NAME": "NAME",
    "PHONE": "PHONE_NUMBER",
    "RIZIV": "RIZIV_INAMI",
    "STREET": "ADDRESS",
    "TELEFOON": "PHONE_NUMBER",
    "TELEPHONENUM": "PHONE_NUMBER",
    "UNIT_NUMBER": "BTW_EENHEID",
    "VAT_NUMBER": "BTW_EENHEID",
    "ZIPCODE": "ADDRESS",
}


def available_cpu_count() -> int:
    affinity = getattr(os, "sched_getaffinity", None)
    if callable(affinity):
        try:
            return max(1, len(affinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def available_memory_gib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024**2
    return None


@dataclass
class TrainConfig:
    """Inputs for one training run."""

    model_id: str
    run_name: str
    train_path: Path
    validation_path: Path
    output_dir: Path
    cache_dir: Path
    taxonomy_path: Path | None = None
    dev_path: Path | None = None
    model_name: str = "model"
    dataset_name: str = "dataset"
    run_mode: str = "full"
    data_fingerprint: str = ""
    data_manifest_sha256: str = ""
    model_revision: str = ""
    worker_sha256: str = ""
    runtime_versions: dict[str, str] = field(default_factory=dict)
    max_length: int = 512
    stride: int = 128
    epochs: float = 6.0
    max_steps: int = -1
    train_batch_size: int = 32
    eval_batch_size: int = 64
    gradient_accumulation: int = 1
    learning_rate: float = 5e-5
    encoder_learning_rate: float | None = None
    classifier_learning_rate: float | None = None
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    dev_fraction: float = 0.1
    max_train_rows: int = 0
    max_validation_rows: int = 0
    class_weighting: str = "sqrt"
    logging_steps: int = 10
    save_steps: int = 0
    dataloader_workers: int = 2
    preprocess_workers: int = 0
    preprocess_batch_size: int = 2048
    preprocess_writer_batch_size: int = 2048
    post_train_eval: bool = True
    seed: int = 42
    amp_precision: str = "fp16"
    allow_online: bool = False
    save_model: bool = True
    lora_enabled: bool = False
    lora_rank: int = 16
    lora_alpha: float = 32.0
    lora_dropout: float = 0.1
    lora_target_modules: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "train_path",
            "validation_path",
            "output_dir",
            "cache_dir",
            "taxonomy_path",
            "dev_path",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                setattr(self, name, Path(value))
        if self.run_mode not in {"smoke", "probe", "full"}:
            raise ValueError("run_mode must be smoke, probe, or full")
        if self.amp_precision not in {"fp16", "bf16", "none"}:
            raise ValueError("amp_precision must be fp16, bf16, or none")
        if (
            min(self.train_batch_size, self.eval_batch_size, self.gradient_accumulation)
            < 1
        ):
            raise ValueError("Batch sizes and gradient accumulation must be positive")
        if min(self.dataloader_workers, self.preprocess_workers) < 0:
            raise ValueError("Worker counts cannot be negative")
        if min(self.preprocess_batch_size, self.preprocess_writer_batch_size) < 1:
            raise ValueError("Preprocessing batch sizes must be positive")
        if self.class_weighting != "none" and self.gradient_accumulation != 1:
            raise ValueError("Weighted loss currently requires gradient_accumulation=1")
        if self.lora_enabled:
            if self.lora_rank < 1:
                raise ValueError("lora_rank must be positive")
            if self.lora_alpha <= 0:
                raise ValueError("lora_alpha must be positive")
            if not 0 <= self.lora_dropout < 1:
                raise ValueError("lora_dropout must be in [0, 1)")
            if (
                self.lora_target_modules is not None
                and not self.lora_target_modules.strip()
            ):
                raise ValueError("Set lora_target_modules to enable LoRA")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> TrainConfig:
        normalized = dict(mapping)
        allowed = {field.name for field in fields(cls)}
        unknown = set(normalized) - allowed
        if unknown:
            raise TypeError(f"Unknown TrainConfig keys: {sorted(unknown)}")
        return cls(**normalized)


def parse_args() -> TrainConfig:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config-json", type=Path)
    config_namespace, _ = config_parser.parse_known_args()
    if config_namespace.config_json:
        return TrainConfig.from_mapping(
            json.loads(config_namespace.config_json.read_text(encoding="utf-8"))
        )

    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--train-path", type=Path, required=True)
    parser.add_argument("--validation-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--taxonomy-path", type=Path)
    parser.add_argument("--dev-path", type=Path)
    parser.add_argument("--model-name", default="model")
    parser.add_argument("--dataset-name", default="dataset")
    parser.add_argument(
        "--run-mode", choices=("smoke", "probe", "full"), default="full"
    )
    parser.add_argument("--data-fingerprint", default="")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--stride", type=int, default=128)
    parser.add_argument("--epochs", type=float, default=6.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--train-batch-size", type=int, default=32)
    parser.add_argument("--eval-batch-size", type=int, default=64)
    parser.add_argument("--gradient-accumulation", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--encoder-learning-rate", type=float)
    parser.add_argument("--classifier-learning-rate", type=float)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--dev-fraction", type=float, default=0.1)
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--max-validation-rows", type=int, default=0)
    parser.add_argument("--class-weighting", choices=("none", "sqrt"), default="sqrt")
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--save-steps", type=int, default=0)
    parser.add_argument("--dataloader-workers", type=int, default=2)
    parser.add_argument("--preprocess-workers", type=int, default=0)
    parser.add_argument("--preprocess-batch-size", type=int, default=2048)
    parser.add_argument("--preprocess-writer-batch-size", type=int, default=2048)
    parser.add_argument(
        "--post-train-eval", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--amp-precision", choices=("fp16", "bf16", "none"), default="fp16"
    )
    parser.add_argument("--allow-online", action="store_true")
    parser.add_argument(
        "--save-model", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--lora-enabled", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=float, default=32.0)
    parser.add_argument("--lora-dropout", type=float, default=0.1)
    parser.add_argument("--lora-target-modules", type=str, default=None)
    # Every option name above matches a TrainConfig field, and from_mapping
    # rejects any that stops matching.
    return TrainConfig.from_mapping(vars(parser.parse_args()))


def parse_spans(value: Any) -> list[dict[str, Any]]:
    """Normalize Parquet nested values while rejecting malformed annotations."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError(
            "Annotation container is null. Use [] for an annotated negative."
        )
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        if not all(isinstance(span, dict) for span in value):
            raise ValueError("Every span must be an object")
        return [dict(span) for span in value]
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, str):
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(value)
            except (ValueError, SyntaxError, json.JSONDecodeError):
                continue
            return parse_spans(parsed)
    raise ValueError(f"Expected a span list, received {type(value).__name__}")


def _validate_row_spans(
    source_text: str, spans: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for span in spans:
        label = span.get("label")
        start = span.get("start")
        end = span.get("end")
        value = span.get("value")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Span has an invalid label: {span!r}")
        if isinstance(start, bool) or not isinstance(start, Integral):
            raise TypeError(f"Span has a non-integral start offset: {span!r}")
        if isinstance(end, bool) or not isinstance(end, Integral):
            raise TypeError(f"Span has a non-integral end offset: {span!r}")
        start, end = int(start), int(end)
        if not 0 <= start < end <= len(source_text):
            raise ValueError(
                f"Span offsets [{start}, {end}) exceed text length {len(source_text)}"
            )
        if not isinstance(value, str) or value != source_text[start:end]:
            raise ValueError(
                f"Span value does not match source text at [{start}, {end})"
            )
        normalized.append({"label": label, "start": start, "end": end, "value": value})
    ordered = sorted(normalized, key=lambda item: (item["start"], item["end"]))
    for previous, current in pairwise(ordered):
        if current["start"] < previous["end"]:
            raise ValueError(
                f"Overlapping spans cannot be represented as BIO labels: {previous!r}, {current!r}"
            )
    return normalized


def clean_frame(
    path: Path,
    limit: int,
    seed: int,
    dataset_name: str | None = None,
    expected_split: str | None = None,
) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing input data: {path}")
    columns = [
        "source_text",
        "privacy_mask",
        "language",
        "source_dataset",
        "dataset_split",
    ]
    frame = pd.read_parquet(path, columns=columns)
    if frame[columns].isna().any().any():
        raise ValueError(f"Canonical data contains null values: {path}")
    if dataset_name:
        expected_dataset = DATASET_IDS.get(dataset_name)
        if expected_dataset is not None:
            observed_datasets = set(frame["source_dataset"].astype(str).unique())
            if observed_datasets != {expected_dataset}:
                raise ValueError(
                    f"Unexpected source_dataset values in {path}: {sorted(observed_datasets)}"
                )
    if expected_split:
        observed_splits = set(frame["dataset_split"].astype(str).unique())
        if observed_splits != {expected_split}:
            raise ValueError(
                f"Unexpected dataset_split values in {path}: {sorted(observed_splits)}"
            )
    frame = frame.reset_index(drop=True)
    if limit and len(frame) > limit:
        frame = frame.sample(n=limit, random_state=seed).reset_index(drop=True)
    frame["source_text"] = frame["source_text"].astype(str)
    blank = frame["source_text"].str.strip().eq("")
    if blank.any():
        raise ValueError(
            f"Canonical data contains {int(blank.sum())} blank source texts: {path}"
        )
    frame["language"] = frame["language"].astype(str)
    frame["spans"] = [
        _validate_row_spans(text, parse_spans(spans))
        for text, spans in zip(frame["source_text"], frame["privacy_mask"], strict=True)
    ]
    return frame[["source_text", "language", "source_dataset", "spans"]]


def split_train_dev(
    frame: pd.DataFrame, dev_fraction: float, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < dev_fraction < 1:
        raise ValueError("Set --dev-fraction from 0 to 1")
    if len(frame) < 20:
        raise ValueError("Use at least 20 training rows to create a development split")
    groups = frame.groupby("source_text", sort=False).indices
    group_keys = list(groups)
    random.Random(seed).shuffle(group_keys)
    target_size = max(1, round(len(frame) * dev_fraction))
    dev_indices: list[int] = []
    for key in group_keys:
        if len(dev_indices) >= target_size:
            break
        dev_indices.extend(int(index) for index in groups[key])
    dev_index_set = set(dev_indices)
    train_indices = [index for index in frame.index if index not in dev_index_set]
    if not train_indices:
        raise ValueError("Duplicate grouping left no rows in the training split")
    return frame.loc[train_indices].reset_index(drop=True), frame.loc[
        dev_indices
    ].reset_index(drop=True)


def load_train_dev_frames(config: TrainConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_train = clean_frame(
        config.train_path,
        config.max_train_rows,
        config.seed,
        config.dataset_name,
        "train",
    )
    if config.dev_path is None:
        return split_train_dev(all_train, config.dev_fraction, config.seed)
    dev = clean_frame(
        config.dev_path, 0, config.seed, config.dataset_name, "development"
    )
    return all_train, dev


def label_names(*frames: pd.DataFrame) -> list[str]:
    labels = {
        str(span["label"])
        for frame in frames
        for spans in frame["spans"]
        for span in spans
        if isinstance(span, dict) and span.get("label")
    }
    if not labels:
        raise ValueError("The supplied training data contains no PII spans")
    entity_labels = sorted(labels)
    return [
        "O",
        *[f"B-{label}" for label in entity_labels],
        *[f"I-{label}" for label in entity_labels],
    ]


def _read_bio_labels(path: Path) -> list[str]:
    taxonomy = json.loads(path.read_text(encoding="utf-8"))
    labels = taxonomy.get("bio_labels")
    if (
        not isinstance(labels, list)
        or not labels
        or not all(isinstance(label, str) for label in labels)
    ):
        raise ValueError(
            f"Taxonomy {path} must contain a non-empty string bio_labels list"
        )
    if len(labels) != len(set(labels)) or labels[0] != "O":
        raise ValueError(f"Taxonomy {path} bio_labels must be unique and start with O")
    return list(labels)


def _declared_entities(path: Path, labels: list[str]) -> set[str]:
    """The entity names the BIO labels declare, after checking the pairing."""
    declared_entities = {
        label[2:]
        for label in labels
        if label.startswith(("B-", "I-")) and len(label) > 2
    }
    malformed = [
        label
        for label in labels[1:]
        if not label.startswith(("B-", "I-")) or len(label) <= 2
    ]
    if malformed:
        raise ValueError(f"Taxonomy {path} contains invalid BIO labels: {malformed}")
    missing_pairs = sorted(
        entity
        for entity in declared_entities
        if f"B-{entity}" not in labels or f"I-{entity}" not in labels
    )
    if missing_pairs:
        raise ValueError(f"Taxonomy {path} is missing B/I pairs for: {missing_pairs}")
    return declared_entities


def load_taxonomy_labels(path: Path, *frames: pd.DataFrame) -> list[str]:
    """Load the taxonomy's authoritative BIO order and validate observed entities."""
    labels = _read_bio_labels(path)
    declared_entities = _declared_entities(path, labels)
    observed = {
        str(span["label"])
        for frame in frames
        for spans in frame["spans"]
        for span in spans
    }
    undeclared = sorted(observed - declared_entities)
    if undeclared:
        raise ValueError(
            f"Observed span labels are not declared by taxonomy {path}: {undeclared}"
        )
    return labels


def _configured_label_names(model) -> list[str]:
    id_to_label = getattr(model.config, "id2label", None)
    if not isinstance(id_to_label, dict):
        raise TypeError("Model config has no usable id2label mapping")
    try:
        indexed_labels = sorted(
            (int(index), str(label)) for index, label in id_to_label.items()
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Model config id2label keys must be integer-like") from exc
    if [index for index, _ in indexed_labels] != list(range(len(indexed_labels))):
        raise ValueError("Model config id2label indices must be contiguous from zero")
    labels = [label for _, label in indexed_labels]
    label_to_id = getattr(model.config, "label2id", None)
    if not isinstance(label_to_id, dict) or label_to_id != {
        label: index for index, label in enumerate(labels)
    }:
        raise ValueError("Model config label2id does not match id2label")
    return labels


def resize_token_classifier(model, new_labels: list[str]):
    """Resize a token classifier, copying weights for labels that keep the same name."""
    classifier = getattr(model, "classifier", None)
    if not isinstance(classifier, torch.nn.Linear):
        raise TypeError(
            "Model must expose its token-classification head as model.classifier (torch.nn.Linear)"
        )
    old_labels = _configured_label_names(model)
    if len(old_labels) != classifier.out_features:
        raise ValueError("Model classifier size does not match its id2label mapping")
    if len(new_labels) != len(set(new_labels)):
        raise ValueError("New label names must be unique")
    missing = [label for label in old_labels if label not in new_labels]
    uses_default_labels = old_labels == [
        f"LABEL_{index}" for index in range(len(old_labels))
    ]
    if len(new_labels) > len(old_labels) and missing and not uses_default_labels:
        raise ValueError(
            f"Existing model labels are absent from the new label map: {missing}"
        )
    initializer = getattr(model, "_init_weights", None)
    if not callable(initializer):
        raise TypeError(
            "Model does not expose _init_weights required for architecture-consistent initialization"
        )

    replacement = torch.nn.Linear(
        classifier.in_features,
        len(new_labels),
        bias=classifier.bias is not None,
        device=classifier.weight.device,
        dtype=classifier.weight.dtype,
    )
    initializer(replacement)
    old_index = {label: index for index, label in enumerate(old_labels)}
    new_index = {label: index for index, label in enumerate(new_labels)}
    with torch.no_grad():
        for label, source_index in old_index.items():
            if label not in new_index:
                continue
            target_index = new_index[label]
            replacement.weight[target_index].copy_(classifier.weight[source_index])
            if classifier.bias is not None:
                replacement.bias[target_index].copy_(classifier.bias[source_index])
    model.classifier = replacement
    model.config.id2label = {index: label for index, label in enumerate(new_labels)}
    model.config.label2id = {label: index for index, label in enumerate(new_labels)}
    model.config.num_labels = len(new_labels)
    return model


def make_separate_lr_optimizer(
    model,
    learning_rate: float,
    encoder_learning_rate: float | None,
    classifier_learning_rate: float | None,
    weight_decay: float,
) -> torch.optim.Optimizer | None:
    if encoder_learning_rate is None and classifier_learning_rate is None:
        return None
    classifier = getattr(model, "classifier", None)
    if not isinstance(classifier, torch.nn.Module):
        raise TypeError(
            "Separate learning rates require model.classifier to be a torch module"
        )
    classifier_ids = {id(parameter) for parameter in classifier.parameters()}
    rates = {
        False: (
            learning_rate if encoder_learning_rate is None else encoder_learning_rate
        ),
        True: (
            learning_rate
            if classifier_learning_rate is None
            else classifier_learning_rate
        ),
    }
    groups = []
    named_parameters = [
        (name, parameter)
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    ]
    for is_classifier in (False, True):
        for use_decay in (True, False):
            parameters = [
                parameter
                for name, parameter in named_parameters
                if (id(parameter) in classifier_ids) == is_classifier
                and (
                    not name.endswith(("bias", "LayerNorm.weight", "layer_norm.weight"))
                )
                == use_decay
            ]
            if parameters:
                groups.append(
                    {
                        "params": parameters,
                        "lr": rates[is_classifier],
                        "weight_decay": weight_decay if use_decay else 0.0,
                    }
                )
    return torch.optim.AdamW(groups, lr=learning_rate)


def make_tokenizer_fn(
    tokenizer,
    label_to_id: dict[str, int],
    max_length: int,
    stride: int,
    include_metadata: bool,
):
    def tokenize(
        examples: dict[str, list[Any]], indices: list[int]
    ) -> dict[str, list[Any]]:
        encoded = tokenizer(
            examples["source_text"],
            truncation=True,
            max_length=max_length,
            stride=stride,
            return_overflowing_tokens=True,
            return_offsets_mapping=True,
        )
        labels: list[list[int]] = []
        raw_indices: list[int] = []
        sample_mapping = encoded.pop("overflow_to_sample_mapping")
        for chunk_index, source_index in enumerate(sample_mapping):
            offsets = encoded["offset_mapping"][chunk_index]
            spans = examples["spans"][source_index]
            token_labels = []
            seen_spans: set[int] = set()
            for start, end in offsets:
                if start == end:
                    token_labels.append(-100)
                    continue
                matched_index = None
                matched_overlap = 0
                for span_index, span in enumerate(spans):
                    span_start, span_end = int(span.get("start", -1)), int(
                        span.get("end", -1)
                    )
                    overlap = max(0, min(end, span_end) - max(start, span_start))
                    raw_label = span.get("label")
                    if overlap > matched_overlap and f"B-{raw_label}" in label_to_id:
                        matched_index, matched_overlap = span_index, overlap
                if matched_index is None:
                    token_labels.append(label_to_id["O"])
                    continue
                span = spans[matched_index]
                prefix = "B" if matched_index not in seen_spans else "I"
                seen_spans.add(matched_index)
                token_labels.append(label_to_id[f"{prefix}-{span['label']}"])
            labels.append(token_labels)
            raw_indices.append(indices[source_index])
        encoded["labels"] = labels
        if include_metadata:
            encoded["raw_index"] = raw_indices
        else:
            encoded.pop("offset_mapping")
        return encoded

    return tokenize


def prepare_features(
    frame: pd.DataFrame,
    tokenizer,
    label_to_id: dict[str, int],
    max_length: int,
    stride: int,
    include_metadata: bool = True,
    num_proc: int = 0,
    batch_size: int = 1000,
    writer_batch_size: int = 1000,
) -> tuple[Dataset, list[list[tuple[int, int]]] | None, list[int] | None]:
    dataset = Dataset.from_pandas(frame, preserve_index=False)
    map_kwargs = {
        "batched": True,
        "with_indices": True,
        "remove_columns": dataset.column_names,
        "desc": "Tokenizing and aligning spans",
        "batch_size": batch_size,
        "writer_batch_size": writer_batch_size,
    }
    if num_proc > 1:
        map_kwargs["num_proc"] = num_proc
    encoded = dataset.map(
        make_tokenizer_fn(tokenizer, label_to_id, max_length, stride, include_metadata),
        **map_kwargs,
    )
    offsets = encoded["offset_mapping"] if include_metadata else None
    raw_indices = encoded["raw_index"] if include_metadata else None
    features = (
        encoded.remove_columns(["offset_mapping", "raw_index"])
        if include_metadata
        else encoded
    )
    return features, offsets, raw_indices


def _feature_cache_path(
    args: TrainConfig,
    split: str,
    include_metadata: bool,
    labels: list[str],
    row_count: int,
) -> Path:
    payload = {
        "worker": WORKER_VERSION,
        "split": split,
        "dataset": args.dataset_name,
        "data": args.data_fingerprint,
        "manifest": args.data_manifest_sha256,
        "model": args.model_id,
        "revision": args.model_revision,
        "labels": labels,
        "rows": row_count,
        "max_length": args.max_length,
        "stride": args.stride,
        "include_metadata": include_metadata,
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    return args.cache_dir / "tokenized_features" / f"{split}-{key}"


def prepare_features_cached(
    split: str,
    frame: pd.DataFrame,
    tokenizer,
    label_to_id: dict[str, int],
    labels: list[str],
    args: TrainConfig,
    include_metadata: bool,
    is_main_process: bool,
) -> tuple[Dataset, list[list[tuple[int, int]]] | None, list[int] | None]:
    cache_path = _feature_cache_path(args, split, include_metadata, labels, len(frame))
    if is_main_process and not cache_path.exists():
        started = time.perf_counter()
        available_ram = available_memory_gib()
        ram_text = (
            f"{available_ram:.1f} GiB" if available_ram is not None else "unknown"
        )
        print(
            f"TOKENIZATION CACHE MISS | split={split} | rows={len(frame):,} "
            f"| cpu_available={available_cpu_count()} | num_proc={args.preprocess_workers or 1} "
            f"| batch_size={args.preprocess_batch_size} | writer_batch_size={args.preprocess_writer_batch_size} "
            f"| mem_available={ram_text} | path={cache_path}",
            flush=True,
        )
        features, offsets, raw_indices = prepare_features(
            frame,
            tokenizer,
            label_to_id,
            args.max_length,
            args.stride,
            include_metadata=include_metadata,
            num_proc=args.preprocess_workers,
            batch_size=args.preprocess_batch_size,
            writer_batch_size=args.preprocess_writer_batch_size,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_name(cache_path.name + ".tmp")
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        features_to_save = features
        if include_metadata:
            features_to_save = features.add_column(
                "offset_mapping", offsets
            ).add_column("raw_index", raw_indices)
        features_to_save.save_to_disk(str(tmp_path))
        if cache_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        else:
            shutil.move(str(tmp_path), str(cache_path))
        elapsed = time.perf_counter() - started
        print(
            f"TOKENIZATION CACHE WRITE | split={split} | feature_chunks={len(features):,} "
            f"| rows_per_second={len(frame) / max(1e-9, elapsed):.1f} | elapsed={elapsed / 60:.1f}m",
            flush=True,
        )
    if not is_main_process and not cache_path.exists():
        started_wait = time.perf_counter()
        while not cache_path.exists():
            if time.perf_counter() - started_wait > 3600:
                raise TimeoutError(
                    f"Timed out waiting for tokenized feature cache: {cache_path}"
                )
            time.sleep(2)
    if torch.distributed.is_available() and torch.distributed.is_initialized():
        torch.distributed.barrier()
    if not cache_path.exists():
        raise FileNotFoundError(f"Missing tokenized feature cache: {cache_path}")
    loaded = load_from_disk(str(cache_path))
    offsets = loaded["offset_mapping"] if include_metadata else None
    raw_indices = loaded["raw_index"] if include_metadata else None
    features = (
        loaded.remove_columns(["offset_mapping", "raw_index"])
        if include_metadata
        else loaded
    )
    if is_main_process:
        print(
            f"TOKENIZATION CACHE READY | split={split} | feature_chunks={len(features):,} | path={cache_path}",
            flush=True,
        )
    return features, offsets, raw_indices


def class_weights(features: Dataset, num_labels: int, mode: str) -> torch.Tensor | None:
    if mode == "none":
        return None
    counts = np.zeros(num_labels, dtype=np.int64)
    for row in features["labels"]:
        for label in row:
            if label >= 0:
                counts[label] += 1
    weights = np.ones(num_labels, dtype=np.float32)
    present = counts > 0
    weights[present] = np.sqrt(
        counts[present].sum() / (present.sum() * counts[present])
    )
    weights /= weights[present].mean()
    return torch.tensor(weights, dtype=torch.float32)


def label_token_counts(
    features: Dataset, id_to_label: dict[int, str]
) -> dict[str, int]:
    counts = np.zeros(len(id_to_label), dtype=np.int64)
    for row in features["labels"]:
        for label in row:
            if label >= 0:
                counts[label] += 1
    return {
        id_to_label[index]: int(count) for index, count in enumerate(counts) if count
    }


def _default_lora_target_modules() -> list[str]:
    """Use the standard XLM-R attention projections for LoRA runs."""
    return ["query", "value"]


def lora_target_modules(config: TrainConfig) -> list[str]:
    targets = (
        config.lora_target_modules.split(",")
        if config.lora_target_modules
        else _default_lora_target_modules()
    )
    targets = [target.strip() for target in targets if target.strip()]
    if not targets:
        raise ValueError("LoRA requires at least one target module")
    return targets


def _apply_lora(model: torch.nn.Module, config: TrainConfig) -> torch.nn.Module:
    if get_peft_model is None or LoraConfig is None or TaskType is None:
        raise RuntimeError("Install peft: python -m pip install 'peft>=0.14,<1'")
    if not config.lora_enabled:
        return model
    lora_config = LoraConfig(
        task_type=TaskType.TOKEN_CLS,
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=lora_target_modules(config),
        modules_to_save=["classifier"],
    )
    peft_model = get_peft_model(model, lora_config)
    peft_model.print_trainable_parameters()
    return peft_model


def _trainable_parameter_count(model: torch.nn.Module) -> int:
    return sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )


def lora_run_metadata(
    config: TrainConfig, model: torch.nn.Module | None = None
) -> dict[str, Any]:
    """Return adapter metadata for metrics and artifact manifests."""
    if not config.lora_enabled:
        return {"training_method": "full_fine_tuning"}
    if LoraConfig is None:
        raise RuntimeError("LoRA metadata requested without peft installed")
    import peft
    import transformers

    metadata = {
        "training_method": "lora",
        "lora_rank": config.lora_rank,
        "lora_alpha": config.lora_alpha,
        "lora_dropout": config.lora_dropout,
        "lora_target_modules": lora_target_modules(config),
        "lora_modules_to_save": ["classifier"],
        "peft_version": peft.__version__,
        "transformers_version": transformers.__version__,
    }
    if model is not None:
        metadata["trainable_parameters"] = _trainable_parameter_count(model)
        metadata["total_parameters"] = sum(
            parameter.numel() for parameter in model.parameters()
        )
    return metadata


def save_lora_artifacts(
    model: torch.nn.Module, tokenizer, output_dir: Path, config: TrainConfig
) -> torch.nn.Module:
    """Save a resumable adapter and a merged inference model."""
    if PeftModel is None or not isinstance(model, PeftModel):
        raise TypeError("LoRA artifact saving requires a PeftModel")
    adapter_dir = output_dir / "adapter"
    model_dir = output_dir / "model"
    model.save_pretrained(adapter_dir)
    artifact_manifest = {
        "schema_version": 1,
        "base_model_id": config.model_id,
        "base_model_revision": config.model_revision,
        "adapter_directory": "adapter",
        "merged_model_directory": "model",
        **lora_run_metadata(config, model),
    }
    (adapter_dir / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    merged_model = model.merge_and_unload()
    merged_model.save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)
    return merged_model


FULL_WEIGHT_FILENAMES = (
    "model.safetensors",
    "pytorch_model.bin",
    "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
)
ADAPTER_WEIGHT_FILENAMES = ("adapter_model.safetensors", "adapter_model.bin")
# Narrower than the evaluator's TOKENIZER_FILENAMES: a checkpoint never has vocab.json.
CHECKPOINT_TOKENIZER_FILENAMES = (
    "tokenizer.json",
    "sentencepiece.bpe.model",
    "spm.model",
)


def checkpoint_has_weights(checkpoint: Path, lora_enabled: bool) -> bool:
    """Report whether *checkpoint* holds adapter or full model weights."""
    names = (
        FULL_WEIGHT_FILENAMES + ADAPTER_WEIGHT_FILENAMES
        if lora_enabled
        else FULL_WEIGHT_FILENAMES
    )
    return any((checkpoint / name).exists() for name in names)


def checkpoint_has_tokenizer(checkpoint: Path) -> bool:
    return any((checkpoint / name).exists() for name in CHECKPOINT_TOKENIZER_FILENAMES)


def selected_checkpoint_path(trainer_state: Any, checkpoints_dir: Path) -> Path:
    """Return the best checkpoint or the final smoke-run checkpoint."""
    reference = getattr(trainer_state, "best_model_checkpoint", None)
    if reference:
        path = Path(str(reference))
    else:
        from transformers.trainer_utils import get_last_checkpoint

        last_checkpoint = get_last_checkpoint(str(checkpoints_dir))
        if not last_checkpoint:
            raise RuntimeError(f"No checkpoint is available under {checkpoints_dir}")
        path = Path(last_checkpoint)
    if not path.is_dir():
        raise RuntimeError(f"Selected checkpoint does not exist: {path}")
    return path


def reload_lora_adapter_for_export(model: torch.nn.Module, checkpoint: Path) -> None:
    """Load the selected adapter after DDP shuts down."""
    if PeftModel is None or not isinstance(model, PeftModel):
        raise TypeError("LoRA checkpoint reload requires a PeftModel")
    model.load_adapter(str(checkpoint), adapter_name="default", is_trainable=False)


def should_load_best_model_at_end(config: TrainConfig) -> bool:
    """Let PEFT reload the best adapter after DDP shuts down."""
    return bool(
        config.save_model and config.run_mode == "full" and not config.lora_enabled
    )


@dataclass(frozen=True)
class DistributedContext:
    """Where this worker sits in a single-GPU or multi-GPU run."""

    local_rank: int
    world_size: int
    device_index: int
    is_main_process: bool


def prepare_run_environment(config: TrainConfig) -> DistributedContext:
    """Pin this worker to its GPU, seed every generator, and create the run dirs.

    Every rank runs this before it reads the dataset, so all ranks shuffle and
    initialise the same way and each one writes to its own device.
    """
    if not torch.cuda.is_available():
        raise RuntimeError("This training worker requires a CUDA GPU")
    local_rank = int(os.environ.get("LOCAL_RANK", "-1"))
    if local_rank >= 0:
        torch.cuda.set_device(local_rank)
    set_seed(config.seed)
    random.seed(config.seed)
    np.random.seed(config.seed)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    if config.preprocess_workers == 0:
        config.preprocess_workers = available_cpu_count()
    # Each dataloader worker forks the fast tokenizer, whose own threads then
    # deadlock in the child.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return DistributedContext(
        local_rank=local_rank,
        world_size=int(os.environ.get("WORLD_SIZE", "1")),
        device_index=max(local_rank, 0),
        is_main_process=int(os.environ.get("RANK", "0")) == 0,
    )


def checkpoint_name_from_reference(reference: str) -> str:
    """Return the directory name from a checkpoint path in trainer state.

    Trainer state records an absolute path from the machine that trained. Only
    the final component survives a move to another machine or mount point.
    """
    return str(reference).replace("\\", "/").rstrip("/").split("/")[-1]


def assert_checkpoint_resumable(
    checkpoint: Path, lora_enabled: bool, world_size: int
) -> dict[str, Any]:
    """Return the trainer state of *checkpoint*, or raise if it cannot resume.

    A resume needs the optimizer, the scheduler, the trainer state, weights, a
    tokenizer, and one RNG state per rank. Resuming from a partial checkpoint
    would change the run without saying so, so this refuses instead.
    """
    required_files: tuple[str, ...] = (
        "optimizer.pt",
        "scheduler.pt",
        "trainer_state.json",
    )
    if lora_enabled:
        required_files += ("adapter_config.json",)
    else:
        required_files += ("config.json", "tokenizer_config.json")
    missing_files = [
        name for name in required_files if not (checkpoint / name).exists()
    ]
    has_model = checkpoint_has_weights(checkpoint, lora_enabled)
    has_tokenizer = lora_enabled or checkpoint_has_tokenizer(checkpoint)
    rng_states = list(checkpoint.glob("rng_state_*.pth"))
    if (checkpoint / "rng_state.pth").exists():
        rng_states.append(checkpoint / "rng_state.pth")
    if (
        missing_files
        or not has_model
        or not has_tokenizer
        or len(rng_states) < world_size
    ):
        raise RuntimeError(
            f"Checkpoint {checkpoint} is incomplete. missing={missing_files}, "
            f"has_model={has_model}, has_tokenizer={has_tokenizer}, rng_states={len(rng_states)}/{world_size}"
        )
    return json.loads((checkpoint / "trainer_state.json").read_text(encoding="utf-8"))


def assert_best_checkpoint_complete(checkpoint: Path, lora_enabled: bool) -> None:
    """Raise when the best checkpoint in trainer state cannot serve an export."""
    required_files = (
        ("trainer_state.json", "adapter_config.json")
        if lora_enabled
        else ("config.json", "trainer_state.json")
    )
    has_model = checkpoint_has_weights(checkpoint, lora_enabled)
    has_tokenizer = lora_enabled or (
        (checkpoint / "tokenizer_config.json").exists()
        and checkpoint_has_tokenizer(checkpoint)
    )
    if (
        not checkpoint.is_dir()
        or not all((checkpoint / name).exists() for name in required_files)
        or not has_model
        or not has_tokenizer
    ):
        raise RuntimeError(
            f"Best checkpoint referenced by trainer state is missing or incomplete: {checkpoint}"
        )


def find_resumable_checkpoint(
    checkpoints_dir: Path, lora_enabled: bool, world_size: int, is_main_process: bool
) -> tuple[str | None, dict[str, Any]]:
    """Return the checkpoint to resume from and its trainer state.

    Returns ``(None, {})`` when no checkpoint exists yet. Raises when one exists
    but cannot resume, because starting over would discard finished epochs.
    """
    from transformers.trainer_utils import get_last_checkpoint

    if not checkpoints_dir.exists():
        return None, {}
    try:
        last_checkpoint = get_last_checkpoint(str(checkpoints_dir))
    except Exception as exc:  # noqa: BLE001 - an unreadable checkpoint starts fresh
        if is_main_process:
            print(
                f"RESUME | checkpoint scan failed ({exc}). Starting fresh", flush=True
            )
        return None, {}
    if not last_checkpoint:
        return None, {}

    state = assert_checkpoint_resumable(Path(last_checkpoint), lora_enabled, world_size)
    best_reference = state.get("best_model_checkpoint")
    if best_reference:
        assert_best_checkpoint_complete(
            Path(last_checkpoint).parent
            / checkpoint_name_from_reference(best_reference),
            lora_enabled,
        )
    return last_checkpoint, state


def training_reached_endpoint(state: dict[str, Any], config: TrainConfig) -> bool:
    """Report whether a checkpoint already finished the configured schedule."""
    if config.max_steps > 0:
        return int(state.get("global_step") or 0) >= config.max_steps
    epoch = state.get("epoch")
    return epoch is not None and float(epoch) >= config.epochs - 1e-6


def select_completed_checkpoint(
    last_checkpoint: str, state: dict[str, Any], config: TrainConfig
) -> Path:
    """Return the checkpoint to export from a run that already finished.

    A full run exports the best checkpoint by evaluation loss. A smoke or probe
    run never evaluates, so it exports the last one.
    """
    if config.run_mode != "full":
        return Path(last_checkpoint)
    best_reference = state.get("best_model_checkpoint")
    if not best_reference:
        raise RuntimeError(
            "Completed full run has no best_model_checkpoint in trainer state"
        )
    return Path(last_checkpoint).parent / checkpoint_name_from_reference(best_reference)


def assert_checkpoint_labels_match(checkpoint: Path, names: list[str]) -> None:
    """Raise when a resumed checkpoint carries a different label set."""
    checkpoint_config = json.loads(
        (checkpoint / "config.json").read_text(encoding="utf-8")
    )
    checkpoint_labels = [
        label
        for _, label in sorted(
            (int(index), label)
            for index, label in checkpoint_config.get("id2label", {}).items()
        )
    ]
    if checkpoint_labels != names:
        raise RuntimeError(
            "Checkpoint label map does not match the current dataset label map"
        )


class WeightedTrainer(Trainer):
    def __init__(self, *args, label_weights: torch.Tensor | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.label_weights = label_weights

    def compute_loss(
        self, model, inputs, return_outputs=False, num_items_in_batch=None
    ):
        if self.label_weights is None:
            return super().compute_loss(
                model,
                inputs,
                return_outputs=return_outputs,
                num_items_in_batch=num_items_in_batch,
            )
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        num_labels = (
            model.module.config.num_labels
            if hasattr(model, "module")
            else model.config.num_labels
        )
        flat_labels = labels.view(-1)
        valid_labels = flat_labels[flat_labels != -100]
        weights = self.label_weights.to(outputs.logits.device)
        if valid_labels.numel():
            loss_sum = F.cross_entropy(
                outputs.logits.view(-1, num_labels),
                flat_labels,
                weight=weights,
                ignore_index=-100,
                reduction="sum",
            )
            denominator = weights[valid_labels].sum()
        else:
            loss_sum = outputs.logits.sum() * 0
            denominator = outputs.logits.new_zeros(())
        world_size = 1
        if torch.distributed.is_available() and torch.distributed.is_initialized():
            denominator = denominator.clone()
            torch.distributed.all_reduce(denominator, op=torch.distributed.ReduceOp.SUM)
            world_size = torch.distributed.get_world_size()
        if denominator.item() == 0:
            loss = loss_sum
        else:
            loss = (
                loss_sum
                * world_size
                / denominator.clamp_min(torch.finfo(denominator.dtype).tiny)
            )
        return (loss, outputs) if return_outputs else loss


class ConsoleProgressCallback(TrainerCallback):
    """Emit concise progress and memory diagnostics."""

    def __init__(self, started_at: float):
        self.started_at = started_at

    @staticmethod
    def _number(value: Any, digits: int = 5) -> str:
        if value is None:
            return "-"
        if isinstance(value, (float, np.floating)):
            return f"{float(value):.{digits}g}"
        return str(value)

    def on_train_begin(self, args, state, control, **kwargs):
        effective_batch = (
            args.per_device_train_batch_size
            * args.gradient_accumulation_steps
            * args.world_size
        )
        print(
            f"TRAINING START | max_steps={state.max_steps:,} | epochs={args.num_train_epochs} "
            f"| batch/device={args.per_device_train_batch_size} | grad_accum={args.gradient_accumulation_steps} "
            f"| effective_batch={effective_batch} "
            f"| logging_every={args.logging_steps} steps",
            flush=True,
        )

    def on_step_end(self, args, state, control, **kwargs):
        if state.global_step == 1 and state.is_world_process_zero:
            device = torch.cuda.current_device()
            gib = 1024**3
            print(
                f"GPU MEMORY | allocated={torch.cuda.max_memory_allocated(device) / gib:.2f} GiB "
                f"| reserved={torch.cuda.max_memory_reserved(device) / gib:.2f} GiB "
                f"| total={torch.cuda.get_device_properties(device).total_memory / gib:.2f} GiB",
                flush=True,
            )

    def on_log(self, args, state, control, logs=None, **kwargs):
        if not logs:
            return
        elapsed = time.perf_counter() - self.started_at
        progress = 100.0 * state.global_step / max(1, state.max_steps)
        values = [
            f"step={state.global_step:,}/{state.max_steps:,} ({progress:5.1f}%)",
            f"epoch={self._number(state.epoch, 4)}",
        ]
        for key in ("loss", "eval_loss", "learning_rate", "grad_norm"):
            if key in logs:
                values.append(f"{key}={self._number(logs[key])}")
        values.append(f"elapsed={elapsed / 60:.1f}m")
        print("TRAINING STATUS | " + " | ".join(values), flush=True)

    def on_epoch_end(self, args, state, control, **kwargs):
        print(
            f"EPOCH COMPLETE | epoch={self._number(state.epoch, 4)} "
            f"| global_step={state.global_step:,}/{state.max_steps:,}",
            flush=True,
        )

    def on_train_end(self, args, state, control, **kwargs):
        print(
            f"TRAINING END | steps={state.global_step:,} | final_epoch={self._number(state.epoch, 4)}",
            flush=True,
        )

    def on_save(self, args, state, control, **kwargs):
        if state.is_world_process_zero:
            checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            print(
                f"CHECKPOINT SAVE COMPLETE | step={state.global_step:,} | path={checkpoint}",
                flush=True,
            )


def decode_predictions(
    model,
    features: Dataset,
    offsets: list[list[tuple[int, int]]],
    raw_indices: list[int],
    collator,
    id_to_label: dict[int, str],
    batch_size: int,
    amp_precision: str,
    num_workers: int,
) -> list[dict[str, Any]]:
    if amp_precision not in {"fp16", "bf16", "none"}:
        raise ValueError(f"Unknown AMP precision: {amp_precision!r}")
    amp_dtype = torch.bfloat16 if amp_precision == "bf16" else torch.float16
    loader = DataLoader(
        features.remove_columns("labels"),
        batch_size=batch_size,
        collate_fn=collator,
        num_workers=num_workers,
        pin_memory=True,
        persistent_workers=num_workers > 0,
    )
    model.eval()
    predictions: list[dict[str, Any]] = []
    cursor = 0
    with torch.inference_mode():
        for batch in loader:
            batch_size_actual = batch["input_ids"].shape[0]
            batch = {
                name: value.to(model.device, non_blocking=True)
                for name, value in batch.items()
            }
            with torch.autocast(
                device_type="cuda", dtype=amp_dtype, enabled=amp_precision != "none"
            ):
                probabilities = (
                    torch.softmax(model(**batch).logits, dim=-1).float().cpu().numpy()
                )
            for row_probs, row_offsets, raw_index in zip(
                probabilities,
                offsets[cursor : cursor + batch_size_actual],
                raw_indices[cursor : cursor + batch_size_actual],
                strict=False,
            ):
                active: dict[str, Any] | None = None
                for probabilities_at_token, (start, end) in zip(
                    row_probs, row_offsets, strict=False
                ):
                    if start == end:
                        continue
                    label_id = int(probabilities_at_token.argmax())
                    label = id_to_label[label_id]
                    if label == "O":
                        if active is not None:
                            predictions.append(active)
                            active = None
                        continue
                    prefix, entity_label = label.split("-", 1)
                    confidence = float(probabilities_at_token[label_id])
                    if (
                        prefix == "I"
                        and active is not None
                        and active["label"] == entity_label
                    ):
                        active["end"] = end
                        active["token_scores"].append(confidence)
                    else:
                        if active is not None:
                            predictions.append(active)
                        active = {
                            "sample_index": raw_index,
                            "label": entity_label,
                            "start": start,
                            "end": end,
                            "token_scores": [confidence],
                        }
                if active is not None:
                    predictions.append(active)
            cursor += batch_size_actual
    for prediction in predictions:
        prediction["confidence"] = float(np.mean(prediction.pop("token_scores")))
    return deduplicate_predictions(predictions)


def deduplicate_predictions(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer complete spans over boundary fragments from overlapping chunks."""
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        grouped[(prediction["sample_index"], prediction["label"])].append(prediction)
    selected: list[dict[str, Any]] = []
    for group in grouped.values():
        kept: list[dict[str, Any]] = []
        for candidate in sorted(
            group,
            key=lambda item: (item["end"] - item["start"], item["confidence"]),
            reverse=True,
        ):
            duplicate = False
            for existing in kept:
                intersection = max(
                    0,
                    min(candidate["end"], existing["end"])
                    - max(candidate["start"], existing["start"]),
                )
                shortest = min(
                    candidate["end"] - candidate["start"],
                    existing["end"] - existing["start"],
                )
                if shortest and intersection / shortest >= 0.8:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
        selected.extend(kept)
    return selected


def gold_predictions(frame: pd.DataFrame) -> list[dict[str, Any]]:
    gold = []
    for sample_index, spans in enumerate(frame["spans"]):
        for span in spans:
            try:
                start, end = int(span["start"]), int(span["end"])
                label = str(span["label"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= start < end:
                gold.append(
                    {
                        "sample_index": sample_index,
                        "label": label,
                        "start": start,
                        "end": end,
                    }
                )
    return gold


def shared_pii_predictions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map shared PII labels for cross-source metrics."""
    normalized: list[dict[str, Any]] = []
    for item in items:
        label = SHARED_PII_LABEL_ALIASES.get(str(item["label"]), str(item["label"]))
        if label in SHARED_PII_LABELS:
            normalized.append({**item, "label": label})
    return normalized


def reconstruct_char_spans(
    frame: pd.DataFrame, predictions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Trim boundary whitespace without relocating a span to another occurrence."""
    source_by_index = {index: text for index, text in enumerate(frame["source_text"])}
    fixed = []
    for prediction in predictions:
        text = source_by_index.get(prediction["sample_index"], "")
        surface = text[prediction["start"] : prediction["end"]]
        trimmed = surface.strip()
        if not trimmed:
            fixed.append(prediction)
            continue
        item = dict(prediction)
        left_trim = len(surface) - len(surface.lstrip())
        item["start"] = prediction["start"] + left_trim
        item["end"] = item["start"] + len(trimmed)
        fixed.append(item)
    return fixed


def evaluate_by_column(
    predictions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    frame: pd.DataFrame,
    threshold: float,
    column: str,
) -> dict[str, Any]:
    """Report metrics separately for each value of *column*.

    Rows with no value, or frames without the column, fall into ``unknown``.
    """
    values = frame.get(column, pd.Series(["unknown"] * len(frame)))
    values = values.fillna("unknown").astype(str)
    results = {}
    for value in sorted(values.unique()):
        sample_indices = set(frame.index[values == value].tolist())
        group_predictions = [
            item for item in predictions if item["sample_index"] in sample_indices
        ]
        group_gold = [item for item in gold if item["sample_index"] in sample_indices]
        results[value] = {
            "rows": len(sample_indices),
            "gold_spans": len(group_gold),
            **{
                match: evaluate(group_predictions, group_gold, threshold, match=match)
                for match in MATCH_MODES
            },
        }
    return results


def fbeta(precision: float, recall: float, beta: float) -> float:
    """Calculate F-beta. A beta above 1 gives more weight to recall."""
    beta_sq = beta * beta
    return (1 + beta_sq) * precision * recall / max(1e-12, beta_sq * precision + recall)


MATCH_MODES = ("overlap", "exact", "iou")
MIN_VERDICT_SUPPORT = 30


def _match_counts(
    predictions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    threshold: float,
    match: str = "overlap",
) -> tuple[int, int, int, dict[str, dict[str, int]]]:
    """Match spans greedily in descending confidence order.

    Return micro ``tp``, ``fp``, and ``fn`` counts and per-label counts. Labels
    must match. ``overlap`` requires the prediction to cover at least 50% of
    the gold span. ``exact`` requires equal boundaries. ``iou`` requires an
    intersection-over-union score of at least 0.50.
    """
    if match not in MATCH_MODES:
        raise ValueError(f"match must be one of {MATCH_MODES}, got {match!r}")
    active = [item for item in predictions if item["confidence"] >= threshold]
    gold_by_key: dict[tuple[int, str], set[int]] = defaultdict(set)
    for gold_index, target in enumerate(gold):
        gold_by_key[(target["sample_index"], target["label"])].add(gold_index)
    unmatched = set(range(len(gold)))
    true_positive = 0
    per_label: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for prediction in sorted(active, key=lambda item: item["confidence"], reverse=True):
        candidates = []
        key = (prediction["sample_index"], prediction["label"])
        for gold_index in gold_by_key.get(key, set()) & unmatched:
            target = gold[gold_index]
            intersection = max(
                0,
                min(prediction["end"], target["end"])
                - max(prediction["start"], target["start"]),
            )
            if match == "exact":
                matched = (
                    prediction["start"] == target["start"]
                    and prediction["end"] == target["end"]
                )
            elif match == "iou":
                union = (
                    (prediction["end"] - prediction["start"])
                    + (target["end"] - target["start"])
                    - intersection
                )
                matched = intersection / max(1, union) >= 0.5
            else:  # overlap: coverage of the gold span
                matched = intersection / max(1, target["end"] - target["start"]) >= 0.5
            if matched:
                candidates.append((intersection, gold_index))
        if candidates:
            _, winner = max(candidates)
            unmatched.remove(winner)
            true_positive += 1
            per_label[prediction["label"]]["tp"] += 1
        else:
            per_label[prediction["label"]]["fp"] += 1
    for gold_index in unmatched:
        per_label[gold[gold_index]["label"]]["fn"] += 1
    false_positive = len(active) - true_positive
    false_negative = len(gold) - true_positive
    return true_positive, false_positive, false_negative, per_label


def _score_counts(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": fbeta(precision, recall, 1.0),
        "f2": fbeta(precision, recall, 2.0),
    }


def evaluate(
    predictions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    threshold: float,
    match: str = "overlap",
) -> dict[str, float | int]:
    tp, fp, fn, per_label = _match_counts(predictions, gold, threshold, match)
    result: dict[str, Any] = {
        "threshold": threshold,
        "match": match,
        **_score_counts(tp, fp, fn),
    }
    result["per_label"] = {
        label: {
            **_score_counts(counts["tp"], counts["fp"], counts["fn"]),
            "support": counts["tp"] + counts["fn"],
            "verdict_eligible": counts["tp"] + counts["fn"] >= MIN_VERDICT_SUPPORT,
        }
        for label, counts in sorted(per_label.items())
    }
    return result


TARGET_RECALL = 0.95
TARGET_PRECISION = 0.85


def _threshold_candidates() -> list[float]:
    low = [0.01, 0.02, 0.03, 0.05, 0.07]
    high = [round(v, 2) for v in np.arange(0.10, 0.96, 0.05)]
    return low + high


def tune_threshold(
    predictions: list[dict[str, Any]], gold: list[dict[str, Any]]
) -> dict[str, Any]:
    """Select the operating threshold with the overlap metric.

    Among thresholds that reach ``TARGET_RECALL``, select the threshold with
    the highest precision. Select the lower threshold when precision is tied.
    If no threshold reaches the target, select the maximum-recall threshold.
    Also return the F2-optimal threshold for reporting.
    """
    candidates = _threshold_candidates()
    print(
        f"THRESHOLD TUNING START | predictions={len(predictions):,} | gold={len(gold):,} "
        f"| thresholds={len(candidates)}",
        flush=True,
    )
    scores = []
    for threshold in candidates:
        scores.append(evaluate(predictions, gold, threshold, match="overlap"))
        print(f"THRESHOLD TUNING | threshold={threshold:.2f} complete", flush=True)
    reaching = [s for s in scores if s["recall"] >= TARGET_RECALL]
    if reaching:
        chosen = max(reaching, key=lambda s: (s["precision"], -s["threshold"]))
        rule = "target_recall"
    else:
        chosen = max(scores, key=lambda s: (s["recall"], s["f2"]))
        rule = "max_recall_fallback"
    f2_optimal = max(scores, key=lambda s: (s["f2"], s["recall"]))
    return {
        "selected_threshold": chosen["threshold"],
        "selection_rule": rule,
        "target_reached": bool(reaching),
        "selected_dev_overlap": {
            k: chosen[k] for k in ("threshold", "precision", "recall", "f1", "f2")
        },
        "f2_optimal_threshold": f2_optimal["threshold"],
        "f2_optimal_dev_overlap": {
            k: f2_optimal[k] for k in ("threshold", "precision", "recall", "f1", "f2")
        },
    }


def config_log_payload(config: TrainConfig, world_size: int) -> dict[str, Any]:
    """Return the run settings printed on the CONFIG line.

    A hosted run keeps only its console log, so this records every setting that
    changes the result. Reading two CONFIG lines shows what differed.
    """
    return {
        "dataset": config.dataset_name,
        "model": config.model_name,
        "run_mode": config.run_mode,
        "data_fingerprint": config.data_fingerprint,
        "data_manifest_sha256": config.data_manifest_sha256,
        "model_revision": config.model_revision,
        "worker_sha256": config.worker_sha256,
        "runtime_versions": config.runtime_versions,
        "max_length": config.max_length,
        "stride": config.stride,
        "epochs": config.epochs,
        "max_steps": config.max_steps,
        "train_batch_size_per_device": config.train_batch_size,
        "eval_batch_size_per_device": config.eval_batch_size,
        "gradient_accumulation": config.gradient_accumulation,
        "world_size": world_size,
        "effective_batch_size": effective_batch_size(config, world_size),
        "learning_rate": config.learning_rate,
        "encoder_learning_rate": config.encoder_learning_rate,
        "classifier_learning_rate": config.classifier_learning_rate,
        "adapter_learning_rate": (
            config.encoder_learning_rate if config.lora_enabled else None
        ),
        "warmup_ratio": config.warmup_ratio,
        "weight_decay": config.weight_decay,
        "class_weighting": config.class_weighting,
        "dataloader_workers": config.dataloader_workers,
        "preprocess_workers": config.preprocess_workers,
        "preprocess_batch_size": config.preprocess_batch_size,
        "preprocess_writer_batch_size": config.preprocess_writer_batch_size,
        "post_train_eval": config.post_train_eval,
        "logging_steps": config.logging_steps,
        "seed": config.seed,
        "lora": lora_run_metadata(config, torch.nn.Module()),
    }


def effective_batch_size(config: TrainConfig, world_size: int) -> int:
    """Return the examples per optimizer step across all ranks."""
    return config.train_batch_size * config.gradient_accumulation * world_size


def load_validation_split(
    config: TrainConfig,
    tokenizer: Any,
    label_to_id: dict[str, int],
    names: list[str],
    is_main_process: bool,
) -> tuple[pd.DataFrame, Dataset, list[Any], list[Any]]:
    """Return the official validation split with its decode metadata.

    This split never trains the model and never tunes the threshold, so its
    scores are the ones a report quotes.
    """
    frame = clean_frame(
        config.validation_path,
        config.max_validation_rows,
        config.seed,
        config.dataset_name,
        None,
    )
    if config.taxonomy_path:
        load_taxonomy_labels(config.taxonomy_path, frame)
    print("TOKENIZATION | official validation", flush=True)
    features, offsets, indices = prepare_features_cached(
        "validation",
        frame,
        tokenizer,
        label_to_id,
        names,
        config,
        include_metadata=True,
        is_main_process=is_main_process,
    )
    if offsets is None or indices is None:
        raise RuntimeError("Validation decode metadata is missing")
    print(
        f"TOKENIZATION COMPLETE | validation_feature_chunks={len(features):,}",
        flush=True,
    )
    return frame, features, offsets, indices


def verdict_block(
    config: TrainConfig,
    verdict_overlap: dict[str, Any],
    verdict_labels: set[str],
    insufficient_support: dict[str, int],
) -> dict[str, Any]:
    """Return the pass or fail section of metrics.json.

    A verdict applies only to a full run over the whole validation split. A
    smoke run or a truncated split reports ``None`` rather than a number that
    would read as a result.
    """
    applicable = config.run_mode == "full" and config.max_validation_rows == 0
    return {
        "targets_met": {
            "recall": (
                bool(verdict_overlap["recall"] >= TARGET_RECALL) if applicable else None
            ),
            "precision": (
                bool(verdict_overlap["precision"] >= TARGET_PRECISION)
                if applicable
                else None
            ),
        },
        "verdict": {
            "applicable": applicable,
            "minimum_label_support": MIN_VERDICT_SUPPORT,
            "eligible_labels": sorted(verdict_labels),
            "insufficient_support_labels": insufficient_support,
        },
    }


def dev_scores(
    predictions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    frame: pd.DataFrame,
    threshold: float,
) -> dict[str, Any]:
    """Return the development split scores recorded in metrics.json.

    The threshold was tuned on this split, so these scores are optimistic. They
    are kept to show the gap against the validation split.
    """
    return {
        "dev_overlap": evaluate(predictions, gold, threshold, match="overlap"),
        "dev_overlap_shared_pii": evaluate(
            shared_pii_predictions(predictions),
            shared_pii_predictions(gold),
            threshold,
            match="overlap",
        ),
        "dev_by_source": evaluate_by_column(
            predictions, gold, frame, threshold, "source_dataset"
        ),
    }


def validation_scores(
    predictions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    frame: pd.DataFrame,
    threshold: float,
) -> dict[str, Any]:
    """Return the validation split scores recorded in metrics.json.

    Overlap is the headline match mode. Exact and IoU sit beside it so a reader
    can see how much of the overlap score rests on loose span boundaries.
    """
    return {
        "validation_overlap": evaluate(predictions, gold, threshold, match="overlap"),
        "validation_overlap_shared_pii": evaluate(
            shared_pii_predictions(predictions),
            shared_pii_predictions(gold),
            threshold,
            match="overlap",
        ),
        "validation_exact": evaluate(predictions, gold, threshold, match="exact"),
        "validation_iou": evaluate(predictions, gold, threshold, match="iou"),
        "validation_by_language": evaluate_by_column(
            predictions, gold, frame, threshold, "language"
        ),
        "validation_by_source": evaluate_by_column(
            predictions, gold, frame, threshold, "source_dataset"
        ),
    }


def decode_split_predictions(
    model: torch.nn.Module,
    frame: pd.DataFrame,
    features: Dataset,
    offsets: list[Any],
    indices: list[Any],
    collator: Any,
    id_to_label: dict[int, str],
    config: TrainConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the predicted and gold spans of one split, in character offsets."""
    predictions = decode_predictions(
        model,
        features,
        offsets,
        indices,
        collator,
        id_to_label,
        config.eval_batch_size,
        config.amp_precision,
        config.dataloader_workers,
    )
    return reconstruct_char_spans(frame, predictions), gold_predictions(frame)


def label_support(gold: list[dict[str, Any]], names: list[str]) -> dict[str, int]:
    """Return the gold span count per label, with configured labels at zero."""
    observed = Counter(item["label"] for item in gold)
    configured = {label.split("-", 1)[1] for label in names if label != "O"}
    return {label: observed.get(label, 0) for label in configured | set(observed)}


def split_by_verdict_support(
    support: dict[str, int],
) -> tuple[set[str], dict[str, int]]:
    """Split labels into those the verdict scores and those too rare to score.

    A label with a handful of gold spans moves precision and recall by whole
    points per span. The verdict reports those labels separately instead of
    letting them decide whether the run met its targets.
    """
    eligible = {
        label for label, count in support.items() if count >= MIN_VERDICT_SUPPORT
    }
    insufficient = {
        label: count
        for label, count in sorted(support.items())
        if count < MIN_VERDICT_SUPPORT
    }
    return eligible, insufficient


def log_alignment_check(token_counts: dict[str, int], names: list[str]) -> None:
    """Print the label token counts, or raise when no entity token survived.

    Span alignment fails quietly: a tokenizer mismatch or a bad offset map turns
    every span into ``O`` and training still converges, on a model that predicts
    nothing. This is the cheapest place to catch that.
    """
    entity_token_count = sum(
        count for label, count in token_counts.items() if label != "O"
    )
    if entity_token_count == 0:
        raise RuntimeError(
            "Span alignment produced zero entity tokens. Refusing to train an all-O classifier."
        )
    print(
        f"ALIGNMENT CHECK | entity_tokens={entity_token_count:,} | O_tokens={token_counts.get('O', 0):,} "
        f"| represented_labels={len(token_counts)}/{len(names)}",
        flush=True,
    )
    print(
        "ENTITY TOKEN COUNTS | "
        + " | ".join(
            f"{label}={count:,}"
            for label, count in token_counts.items()
            if label != "O"
        ),
        flush=True,
    )


@dataclass(frozen=True)
class TrainingPlan:
    """Step counts derived from the dataset size and the batch settings."""

    batches_per_epoch: int
    optimizer_steps: int
    warmup_steps: int


def training_plan(
    config: TrainConfig, train_feature_count: int, world_size: int
) -> TrainingPlan:
    """Return the step counts this run will take.

    Reported before training starts so a run that is scheduled wrong, such as
    one warmup step or a single optimizer step, is visible in the first seconds
    rather than after an hour on a rented GPU.
    """
    batches_per_epoch = math.ceil(
        train_feature_count / (config.train_batch_size * world_size)
    )
    steps_per_epoch = math.ceil(batches_per_epoch / config.gradient_accumulation)
    optimizer_steps = (
        config.max_steps
        if config.max_steps > 0
        else math.ceil(steps_per_epoch * config.epochs)
    )
    return TrainingPlan(
        batches_per_epoch=batches_per_epoch,
        optimizer_steps=optimizer_steps,
        warmup_steps=round(optimizer_steps * config.warmup_ratio),
    )


def build_training_arguments(config: TrainConfig) -> TrainingArguments:
    """Return the Trainer settings for one run.

    Saving and evaluation follow each other: a run that saves on a step schedule
    also evaluates on it, so the best checkpoint is chosen from scores measured
    at the same points.
    """
    kwargs: dict[str, Any] = {
        "output_dir": str(config.output_dir / "checkpoints"),
        "run_name": config.run_name,
        "num_train_epochs": config.epochs,
        "per_device_train_batch_size": config.train_batch_size,
        "per_device_eval_batch_size": config.eval_batch_size,
        "gradient_accumulation_steps": config.gradient_accumulation,
        "learning_rate": config.learning_rate,
        "warmup_ratio": config.warmup_ratio,
        "weight_decay": config.weight_decay,
        "fp16": config.amp_precision == "fp16",
        "bf16": config.amp_precision == "bf16",
        "group_by_length": True,
        "save_strategy": (
            ("steps" if config.save_steps else "epoch") if config.save_model else "no"
        ),
        "save_total_limit": 2,
        # Reload the selected adapter after DDP shuts down to avoid PEFT and
        # Transformers version conflicts.
        "load_best_model_at_end": should_load_best_model_at_end(config),
        "metric_for_best_model": "eval_loss",
        "greater_is_better": False,
        "ddp_find_unused_parameters": False,
        "logging_strategy": "steps",
        "logging_steps": config.logging_steps,
        "logging_first_step": True,
        "disable_tqdm": True,
        "report_to": [],
        "remove_unused_columns": True,
        "dataloader_num_workers": config.dataloader_workers,
        "dataloader_pin_memory": True,
        "dataloader_persistent_workers": config.dataloader_workers > 0,
        "seed": config.seed,
    }
    if config.max_steps > 0:
        kwargs["max_steps"] = config.max_steps

    # Only a full run evaluates. Smoke and probe runs check that the pipeline
    # completes, and evaluation would cost more than the run itself.
    if config.save_model and config.save_steps:
        kwargs["save_steps"] = config.save_steps
        if config.run_mode == "full":
            kwargs["eval_steps"] = config.save_steps
            kwargs["eval_strategy"] = "steps"
        else:
            kwargs["eval_strategy"] = "no"
    else:
        kwargs["eval_strategy"] = "epoch" if config.run_mode == "full" else "no"
    return TrainingArguments(**kwargs)


def shutdown_distributed(*, barrier: bool = False) -> None:
    """Close the DDP process group when one is running."""
    if not (torch.distributed.is_available() and torch.distributed.is_initialized()):
        return
    if barrier:
        torch.distributed.barrier()
    torch.distributed.destroy_process_group()


def run_training(
    trainer: WeightedTrainer, last_checkpoint: str | None, already_finished: bool
) -> None:
    """Train the model, or adopt the state of a checkpoint that already finished."""
    try:
        if already_finished:
            from transformers.trainer_callback import TrainerState

            assert last_checkpoint is not None, "a finished run has a checkpoint"
            trainer.state = TrainerState.load_from_json(
                str(Path(last_checkpoint) / "trainer_state.json")
            )
            print(
                f"TRAINING SKIPPED | checkpoint already reached the configured endpoint at "
                f"step={trainer.state.global_step}",
                flush=True,
            )
        else:
            trainer.train(resume_from_checkpoint=last_checkpoint or None)
    except Exception:
        # Avoid leaking NCCL resources when one DDP rank raises during training.
        shutdown_distributed()
        raise


def export_model(
    trainer: WeightedTrainer,
    model: torch.nn.Module,
    tokenizer: Any,
    selected_checkpoint: Path | None,
    config: TrainConfig,
) -> torch.nn.Module:
    """Save the final model and return the one to evaluate.

    A LoRA run evaluates the merged model rather than the adapter it trained. A
    run with ``save_model=False`` keeps its metrics but deletes its checkpoints,
    which otherwise fill the disk of a shared trainer.
    """
    if config.lora_enabled:
        if selected_checkpoint is None:
            raise RuntimeError("LoRA run has no checkpoint selected for export")
        reload_lora_adapter_for_export(model, selected_checkpoint)

    if not config.save_model:
        print(
            "SAVE SKIPPED | save_model=False (disk safety). Metrics still written",
            flush=True,
        )
        checkpoints_dir = config.output_dir / "checkpoints"
        if checkpoints_dir.exists():
            shutil.rmtree(checkpoints_dir, ignore_errors=True)
            print(f"CLEANUP | removed transient {checkpoints_dir}", flush=True)
        return model

    print(f"FINAL MODEL SAVE START | path={config.output_dir / 'model'}", flush=True)
    if config.lora_enabled:
        model = save_lora_artifacts(model, tokenizer, config.output_dir, config)
        print(
            f"LORA ARTIFACTS SAVED | adapter={config.output_dir / 'adapter'} | merged_model={config.output_dir / 'model'}",
            flush=True,
        )
    else:
        trainer.save_model(str(config.output_dir / "model"))
        tokenizer.save_pretrained(config.output_dir / "model")
    print(f"FINAL MODEL SAVE COMPLETE | path={config.output_dir / 'model'}", flush=True)
    return model


def write_run_artifacts(
    output_dir: Path, metrics: dict[str, Any], names: list[str]
) -> None:
    """Write metrics.json and labels.json into the run directory."""
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (output_dir / "labels.json").write_text(
        json.dumps(names, indent=2), encoding="utf-8"
    )


def build_trainer(
    model: torch.nn.Module,
    tokenizer: Any,
    training_args: TrainingArguments,
    train_features: Dataset,
    dev_features: Dataset,
    collator: Any,
    label_weights: torch.Tensor | None,
    config: TrainConfig,
    started_at: float,
) -> WeightedTrainer:
    """Return the Trainer for one run.

    A run that sets an encoder or classifier learning rate needs its own
    optimizer. Every other run takes the one the Trainer builds.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "args": training_args,
        "train_dataset": train_features,
        "eval_dataset": dev_features,
        "data_collator": collator,
        "label_weights": label_weights,
        "callbacks": [ConsoleProgressCallback(started_at)],
        "processing_class": tokenizer,
    }
    optimizer = make_separate_lr_optimizer(
        model,
        config.learning_rate,
        config.encoder_learning_rate,
        config.classifier_learning_rate,
        config.weight_decay,
    )
    if optimizer is not None:
        kwargs["optimizers"] = (optimizer, None)
    return WeightedTrainer(**kwargs)


def _load_tokenizer(
    config: TrainConfig,
    last_checkpoint: str | None,
    loading_from_ckpt: bool,
    local_files_only: bool,
    revision: str | None,
) -> Any:
    try:
        return AutoTokenizer.from_pretrained(
            (
                config.model_id
                if config.lora_enabled
                else str(last_checkpoint or config.model_id)
            ),
            cache_dir=config.cache_dir,
            use_fast=True,
            local_files_only=local_files_only,
            revision=revision,
        )
    except (OSError, ValueError):
        if not loading_from_ckpt:
            raise
        # An interrupted save can leave a checkpoint without its tokenizer. The
        # base tokenizer matches the one this checkpoint trained with.
        print(
            f"TOKENIZER FALLBACK | checkpoint tokenizer unavailable. Loading {config.model_id}",
            flush=True,
        )
        return AutoTokenizer.from_pretrained(
            config.model_id,
            cache_dir=config.cache_dir,
            use_fast=True,
            local_files_only=not config.allow_online,
            revision=(
                None
                if Path(config.model_id).exists()
                else (config.model_revision or None)
            ),
        )


def _load_token_classifier(
    config: TrainConfig,
    names: list[str],
    label_to_id: dict[str, int],
    id_to_label: dict[int, str],
    last_checkpoint: str | None,
    loading_from_ckpt: bool,
    local_files_only: bool,
    revision: str | None,
) -> torch.nn.Module:
    load_kwargs = {
        "cache_dir": config.cache_dir,
        "local_files_only": local_files_only,
        "revision": revision,
    }
    if loading_from_ckpt or config.taxonomy_path is None:
        return AutoModelForTokenClassification.from_pretrained(
            str(last_checkpoint or config.model_id),
            num_labels=len(names),
            id2label=id_to_label,
            label2id=label_to_id,
            ignore_mismatched_sizes=not loading_from_ckpt,
            **load_kwargs,
        )
    model = AutoModelForTokenClassification.from_pretrained(
        config.model_id, **load_kwargs
    )
    if _configured_label_names(model) != names:
        resize_token_classifier(model, names)
    return model


def load_tokenizer_and_model(
    config: TrainConfig,
    names: list[str],
    label_to_id: dict[str, int],
    id_to_label: dict[int, str],
    last_checkpoint: str | None,
) -> tuple[Any, torch.nn.Module]:
    """Return the tokenizer and an FP32 model on CPU, ready for the accelerator.

    A resume reads the checkpoint. A fresh run reads the base model and resizes
    the classifier head when the packaged labels differ from the taxonomy.
    """
    loading_from_ckpt = bool(last_checkpoint and not config.lora_enabled)
    local_files_only = loading_from_ckpt or not config.allow_online
    revision = (
        None
        if loading_from_ckpt or Path(config.model_id).exists()
        else (config.model_revision or None)
    )
    print(
        f"MODEL LOAD | source={'checkpoint' if loading_from_ckpt else 'base'} | {config.model_id}",
        flush=True,
    )
    tokenizer = _load_tokenizer(
        config, last_checkpoint, loading_from_ckpt, local_files_only, revision
    )
    model = _load_token_classifier(
        config,
        names,
        label_to_id,
        id_to_label,
        last_checkpoint,
        loading_from_ckpt,
        local_files_only,
        revision,
    )

    model.float()
    if config.lora_enabled:
        if last_checkpoint:
            if PeftModel is None:
                raise RuntimeError("Install peft to resume a LoRA checkpoint")
            model = PeftModel.from_pretrained(model, last_checkpoint, is_trainable=True)
        else:
            model = _apply_lora(model, config)

    # AMP scales an FP32 master copy. Trainable parameters that arrive in half
    # precision would train at half precision throughout.
    trainable_dtypes = {
        parameter.dtype for parameter in model.parameters() if parameter.requires_grad
    }
    if trainable_dtypes != {torch.float32}:
        raise RuntimeError(
            f"Trainable model parameters must be FP32 before AMP setup. Found {trainable_dtypes}."
        )
    return tokenizer, model


def _log_resume(
    last_ckpt: str | None, last_state: dict[str, Any], is_main_process: bool
) -> None:
    if not is_main_process:
        return
    if last_ckpt:
        _ep, _step = last_state.get("epoch"), last_state.get("global_step")
        print(
            f"RESUME | FOUND {last_ckpt} | epoch={_ep} global_step={_step} -> CONTINUING",
            flush=True,
        )
    else:
        print("RESUME | no checkpoint found. TRAINING FROM SCRATCH", flush=True)


def _completed_run(
    last_ckpt: str | None, last_state: dict[str, Any], args: TrainConfig
) -> tuple[bool, Path | None]:
    """Whether the checkpoint already finished the schedule, and what to export."""
    completed_training = bool(last_ckpt) and training_reached_endpoint(last_state, args)
    selected_checkpoint: Path | None = (
        select_completed_checkpoint(last_ckpt, last_state, args)
        if completed_training and last_ckpt
        else None
    )
    return completed_training, selected_checkpoint


def _label_names_for(
    args: TrainConfig, train_frame: pd.DataFrame, dev_frame: pd.DataFrame
) -> list[str]:
    if args.taxonomy_path:
        return load_taxonomy_labels(args.taxonomy_path, train_frame, dev_frame)
    return label_names(train_frame)


def _log_class_weights(weights: torch.Tensor | None) -> None:
    if weights is None:
        return
    print(
        f"CLASS WEIGHTS | min={weights.min().item():.3f} "
        f"| max={weights.max().item():.3f} | mean={weights.mean().item():.3f}",
        flush=True,
    )


def train_one(config: TrainConfig | dict[str, Any]) -> dict[str, Any]:
    """Train and evaluate one encoder."""
    args = (
        config if isinstance(config, TrainConfig) else TrainConfig.from_mapping(config)
    )
    context = prepare_run_environment(args)
    world_size = context.world_size
    device_index = context.device_index
    is_main_process = context.is_main_process

    started_at = time.perf_counter()
    print(
        f"RUN START | worker={WORKER_VERSION} | dataset={args.dataset_name} | model={args.model_name} "
        f"| model_source={args.model_id} | run={args.run_name}",
        flush=True,
    )

    checkpoints_dir = args.output_dir / "checkpoints"
    last_ckpt, last_state = find_resumable_checkpoint(
        checkpoints_dir, args.lora_enabled, world_size, is_main_process
    )
    _log_resume(last_ckpt, last_state, is_main_process)
    completed_training, selected_checkpoint = _completed_run(
        last_ckpt, last_state, args
    )
    print(
        "CONFIG | " + json.dumps(config_log_payload(args, world_size), sort_keys=True),
        flush=True,
    )

    train_frame, dev_frame = load_train_dev_frames(args)
    names = _label_names_for(args, train_frame, dev_frame)
    label_to_id = {name: index for index, name in enumerate(names)}
    id_to_label = {index: name for name, index in label_to_id.items()}
    if last_ckpt and not args.lora_enabled:
        assert_checkpoint_labels_match(Path(last_ckpt), names)
    print(
        f"DATA | train_rows={len(train_frame):,} | dev_rows={len(dev_frame):,} | labels={len(names)}",
        flush=True,
    )

    tokenizer, model = load_tokenizer_and_model(
        args, names, label_to_id, id_to_label, last_ckpt
    )
    model.to(torch.device("cuda", device_index))
    print(
        f"MODEL READY | device={torch.cuda.get_device_name(device_index)} | "
        f"local_rank={context.local_rank} | world_size={world_size} | "
        f"parameters={sum(parameter.numel() for parameter in model.parameters()):,} | "
        f"trainable={_trainable_parameter_count(model):,}",
        flush=True,
    )
    run_metadata = lora_run_metadata(args, model)
    print("TOKENIZATION | train", flush=True)
    train_features, _, _ = prepare_features_cached(
        "train",
        train_frame,
        tokenizer,
        label_to_id,
        names,
        args,
        include_metadata=False,
        is_main_process=is_main_process,
    )
    print("TOKENIZATION | development", flush=True)
    dev_features, dev_offsets, dev_indices = prepare_features_cached(
        "development",
        dev_frame,
        tokenizer,
        label_to_id,
        names,
        args,
        include_metadata=args.post_train_eval,
        is_main_process=is_main_process,
    )
    token_counts = label_token_counts(train_features, id_to_label)
    log_alignment_check(token_counts, names)

    collator = DataCollatorForTokenClassification(tokenizer, pad_to_multiple_of=8)
    plan = training_plan(args, len(train_features), world_size)
    training_args = build_training_arguments(args)
    label_weight_tensor = class_weights(
        train_features, len(names), args.class_weighting
    )
    _log_class_weights(label_weight_tensor)
    print(
        f"TRAIN PLAN | train_chunks={len(train_features):,} | dev_chunks={len(dev_features):,} "
        f"| batches/epoch={plan.batches_per_epoch:,} | optimizer_steps={plan.optimizer_steps:,} "
        f"| estimated_warmup_steps={plan.warmup_steps:,}",
        flush=True,
    )
    trainer = build_trainer(
        model,
        tokenizer,
        training_args,
        train_features,
        dev_features,
        collator,
        label_weight_tensor,
        args,
        started_at,
    )
    run_training(trainer, last_ckpt, completed_training)

    if args.lora_enabled:
        selected_checkpoint = selected_checkpoint_path(trainer.state, checkpoints_dir)
        if is_main_process:
            print(f"LORA CHECKPOINT SELECTED | path={selected_checkpoint}", flush=True)

    shutdown_distributed(barrier=True)
    if not is_main_process:
        print(
            "RANK COMPLETE | non-main process skipping evaluation and artifact writing",
            flush=True,
        )
        return {
            "run_name": args.run_name,
            "rank": int(os.environ.get("RANK", "0")),
            "status": "trained",
        }

    model = export_model(trainer, model, tokenizer, selected_checkpoint, args)

    def run_metrics(
        *, separate_lrs: bool, validation_rows: int | None
    ) -> dict[str, Any]:
        """Build the run identity and hyperparameter block of metrics.json."""
        header: dict[str, Any] = {
            "dataset": args.dataset_name,
            "model": args.model_name,
            "run_mode": args.run_mode,
            "data_fingerprint": args.data_fingerprint,
            "data_manifest_sha256": args.data_manifest_sha256,
            "model_id": args.model_id,
            "model_revision": args.model_revision,
            "worker_version": WORKER_VERSION,
            "worker_sha256": args.worker_sha256,
            "runtime_versions": args.runtime_versions,
            **run_metadata,
            "run_name": args.run_name,
            "device": torch.cuda.get_device_name(device_index),
            "world_size": world_size,
            "num_labels": len(names),
            "seed": args.seed,
            "learning_rate": args.learning_rate,
        }
        if separate_lrs:
            header["encoder_learning_rate"] = args.encoder_learning_rate
            header["classifier_learning_rate"] = args.classifier_learning_rate
        return header | {
            "epochs": args.epochs,
            "max_steps": args.max_steps,
            "train_batch_size_per_device": args.train_batch_size,
            "eval_batch_size_per_device": args.eval_batch_size,
            "gradient_accumulation": args.gradient_accumulation,
            "effective_batch_size": effective_batch_size(args, world_size),
            "amp_precision": args.amp_precision,
            "fp16": args.amp_precision == "fp16",
            "saved_model": bool(args.save_model),
            "train_rows": len(train_frame),
            "dev_rows": len(dev_frame),
            "validation_rows": validation_rows,
            "selection_metric": (
                "eval_loss" if trainer.state.best_model_checkpoint else "last_step"
            ),
            "selected_checkpoint": trainer.state.best_model_checkpoint
            or str(selected_checkpoint or last_ckpt or "fresh_run_last_step"),
            "best_metric": trainer.state.best_metric,
        }

    if not args.post_train_eval:
        metrics = run_metrics(separate_lrs=True, validation_rows=None) | {
            "post_train_eval": False,
            "training": trainer.state.log_history,
        }
        write_run_artifacts(args.output_dir, metrics, names)
        print(
            f"RUN COMPLETE | elapsed={(time.perf_counter() - started_at) / 60:.1f}m "
            f"| best_metric={trainer.state.best_metric} | artifacts={args.output_dir}",
            flush=True,
        )
        print(
            "TRAINING SUMMARY | "
            + json.dumps(
                {
                    "run_mode": args.run_mode,
                    "train_rows": len(train_frame),
                    "dev_rows": len(dev_frame),
                    "num_labels": len(names),
                    "effective_batch_size": effective_batch_size(args, world_size),
                    "selected_checkpoint": metrics["selected_checkpoint"],
                    "best_metric": trainer.state.best_metric,
                    "metrics_path": str(args.output_dir / "metrics.json"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return metrics

    if dev_offsets is None or dev_indices is None:
        raise RuntimeError("Main process is missing development decode metadata")
    validation_frame, validation_features, validation_offsets, validation_indices = (
        load_validation_split(args, tokenizer, label_to_id, names, is_main_process)
    )

    print("POST-TRAIN EVAL | decoding dev predictions", flush=True)
    dev_predictions, dev_gold = decode_split_predictions(
        model,
        dev_frame,
        dev_features,
        dev_offsets,
        dev_indices,
        collator,
        id_to_label,
        args,
    )
    print(
        f"POST-TRAIN EVAL | dev_predictions={len(dev_predictions):,} | dev_gold={len(dev_gold):,}",
        flush=True,
    )
    tuning = tune_threshold(dev_predictions, dev_gold)
    threshold = tuning["selected_threshold"]
    print(
        f"POST-TRAIN EVAL | selected_threshold={threshold:.2f}. Decoding validation predictions",
        flush=True,
    )
    validation_predictions, validation_gold = decode_split_predictions(
        model,
        validation_frame,
        validation_features,
        validation_offsets,
        validation_indices,
        collator,
        id_to_label,
        args,
    )
    print(
        f"POST-TRAIN EVAL | validation_predictions={len(validation_predictions):,} "
        f"| validation_gold={len(validation_gold):,}. Scoring",
        flush=True,
    )
    verdict_labels, insufficient_support = split_by_verdict_support(
        label_support(validation_gold, names)
    )
    verdict_overlap = evaluate(
        [item for item in validation_predictions if item["label"] in verdict_labels],
        [item for item in validation_gold if item["label"] in verdict_labels],
        threshold,
        match="overlap",
    )
    print(
        f"VERDICT | minimum_support={MIN_VERDICT_SUPPORT} | eligible_labels={len(verdict_labels)} "
        f"| excluded_labels={insufficient_support}",
        flush=True,
    )
    metrics = (
        run_metrics(separate_lrs=False, validation_rows=len(validation_frame))
        | {
            "tuning_metric": "dev_overlap_target_recall",
            "target_recall": TARGET_RECALL,
            "target_precision": TARGET_PRECISION,
            "threshold_selection": tuning,
            "threshold_selected_on_dev_overlap": threshold,
        }
        | verdict_block(args, verdict_overlap, verdict_labels, insufficient_support)
        | dev_scores(dev_predictions, dev_gold, dev_frame, threshold)
        | validation_scores(
            validation_predictions, validation_gold, validation_frame, threshold
        )
        | {
            "validation_overlap_supported": verdict_overlap,
            "training": trainer.state.log_history,
        }
    )
    write_run_artifacts(args.output_dir, metrics, names)
    print(
        f"RUN COMPLETE | elapsed={(time.perf_counter() - started_at) / 60:.1f}m | artifacts={args.output_dir}",
        flush=True,
    )
    print(
        json.dumps(
            {key: value for key, value in metrics.items() if key != "training"},
            indent=2,
        )
    )
    return metrics


def main() -> None:
    train_one(parse_args())


if __name__ == "__main__":
    main()
