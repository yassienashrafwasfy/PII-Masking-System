#!/usr/bin/env python3
"""Validate every real sample annotation through every shared converter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import LabelConverter, MaskSpan, label_registry

SAMPLES = {
    "octo": ROOT / "teams/octo/samples/combined_development.jsonl",
    "synthify": ROOT / "teams/synthify/samples/Synthify_Val.jsonl",
}


def records(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def _assert_every_sample_span_survives(profile: str) -> int:
    """Run every annotation in every sample file through one team's converter.

    The annotations are already written in the approved taxonomy, so a
    conversion that changes any of them is the converter inventing work. Both
    teams' files go through every profile: a converter is not allowed to be
    correct only on the samples its own team happens to own.
    """
    approved = set(label_registry.taxonomy()["entity_labels"])
    converter = LabelConverter(profile, strict=True)
    total = 0
    for path in SAMPLES.values():
        for record in records(path):
            text = record["source_text"]
            source = [
                MaskSpan(entity["start"], entity["end"], entity["label"], "model")
                for entity in record["privacy_mask"]
            ]
            converted = converter.convert_all(source, {None: text})
            assert {span.label for span in converted} <= approved
            assert [(span.start, span.end, span.label) for span in converted] == [
                (entity["start"], entity["end"], entity["label"])
                for entity in record["privacy_mask"]
            ]
            total += len(converted)
    assert total, f"the {profile} converter saw no spans at all"
    return total


def test_the_octo_converter_leaves_every_sample_span_unchanged() -> None:
    _assert_every_sample_span_survives("octo")


def test_the_synthify_converter_leaves_every_sample_span_unchanged() -> None:
    _assert_every_sample_span_survives("synthify")


def test_the_berts_converter_leaves_every_sample_span_unchanged() -> None:
    _assert_every_sample_span_survives("berts")


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()
