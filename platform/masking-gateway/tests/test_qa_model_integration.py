#!/usr/bin/env python3
"""Run shared model inference on annotated samples for all team converters."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import (
    LabelConverter,
    OnnxTokenClassifierEngine,
    Orchestrator,
    find_regex_spans,
    label_registry,
)

SAMPLES = (
    ROOT / "teams/octo/samples/combined_development.jsonl",
    ROOT / "teams/synthify/samples/Synthify_Val.jsonl",
)
PROFILES = ("octo", "synthify", "berts")


def first_records(path: Path, limit: int):
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source):
            if index == limit:
                return
            yield json.loads(line)


def annotated_labels(path: Path) -> frozenset[str]:
    """Return the labels *path*'s ground truth uses, and only those.

    A detection outside this set cannot be adjudicated by this file: the samples
    carry no annotation to be a false positive against, and the QA taxonomy
    scores whatever the samples exercise. A label the file *does* annotate is
    scored against its own annotation, which is what caught the model's
    wrong-length identifiers before `span_shape` refused them.
    """
    labels: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            labels.update(
                entity["label"] for entity in json.loads(line)["privacy_mask"]
            )
    return frozenset(labels)


def overlaps(prediction, expected: dict) -> bool:
    return (
        prediction.label == expected["label"]
        and prediction.start < expected["end"]
        and expected["start"] < prediction.end
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records-per-dataset", type=int, default=3)
    args = parser.parse_args()
    if args.records_per_dataset < 1:
        raise ValueError("--records-per-dataset must be positive")

    # Every label the router masks, not just the identity taxonomy: clinical PHI
    # (MEDICATION/SYMPTOM/DIAGNOSIS) and the locale identifiers reach the mask
    # through `routing_only_labels`, deliberately outside `entity_labels`, and
    # the Octo checkpoint emits the PHI classes natively (ADR 006). A label
    # neither list carries would still be caught here.
    approved = set(label_registry.masked_labels())
    orchestrator = Orchestrator("model_regex")
    engine = OnnxTokenClassifierEngine(
        model_path=str(ROOT / "teams/octo/onnx"),
        model_version="qa-integration",
        confidence_threshold=0.9,
        accelerator=os.environ.get("ACCELERATOR", "cpu"),
        model_sha256="",
        max_model_tokens=510,
        model_stride_tokens=192,
    )
    checked = 0
    for path in SAMPLES:
        scorable = annotated_labels(path)
        for record in first_records(path, args.records_per_dataset):
            text, expected = record["source_text"], record["privacy_mask"]
            assert {entity["label"] for entity in expected} <= approved
            predicted = engine.infer(text)
            regex_spans = find_regex_spans(text)
            for profile in PROFILES:
                converted = LabelConverter(profile).convert_all(predicted, {None: text})
                final = orchestrator.execute(converted, regex_spans, {None: text})
                assert {span.label for span in final} <= approved
                assert all(
                    any(overlaps(span, entity) for entity in expected)
                    for span in final
                    if span.label in scorable
                ), f"{profile} produced a false positive in {path.name}"
            checked += 1
    print(
        f"Validated QA inference and no false positives across {checked} annotated records."
    )


if __name__ == "__main__":
    main()
