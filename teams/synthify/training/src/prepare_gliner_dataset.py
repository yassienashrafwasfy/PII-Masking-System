"""Convert annotated JSONL records into GLiNER's word-span format.

Emits the checkpoint's *native* label spellings (`Name`, `TELEFOON`, ...),
because a GLiNER label is a prompt: a model fine-tuned on `PHONE_NUMBER` does
not answer to `TELEFOON`. This mirrors the source notebook
(Milestone_1/train_colab.ipynb, `convert_to_gliner`), which keeps source labels
verbatim through both training and evaluation.

The canonical `qa-pii-v1` mapping is an inference-side concern and lives in
`labels.CANONICAL_LABELS`, applied by `runtime.detect` on the model's output.
Keeping it in one place is what stops the two ends drifting apart again.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# This module is executed as a script, not imported as a package member, so the
# shared label definitions are reached by path rather than relative import.
_SYNTHIFY_ROOT = Path(__file__).resolve().parents[2]
if str(_SYNTHIFY_ROOT) not in sys.path:
    sys.path.insert(0, str(_SYNTHIFY_ROOT))

from labels import TRAINING_LABELS, to_training_label  # noqa: E402


class UnsupportedLabelError(ValueError):
    """Raised when source data uses a label outside the training schema."""


def tokenize_with_offsets(text: str) -> list[tuple[int, int, str]]:
    return [
        (match.start(), match.end(), match.group())
        for match in re.finditer(r"\S+", text)
    ]


def convert_record(record: dict[str, Any], *, unsupported: str) -> dict[str, Any]:
    """Convert one source record to GLiNER word spans with native labels."""
    text = record["source_text"]
    tokens = tokenize_with_offsets(text)
    ner: list[list[Any]] = []
    for entity in record.get("privacy_mask", []):
        source_label = entity["label"]
        label = to_training_label(source_label)
        if label is None:
            if unsupported == "error":
                raise UnsupportedLabelError(
                    f"Unsupported Synthify label: {source_label}. "
                    f"Training labels are: {', '.join(TRAINING_LABELS)}"
                )
            continue
        start, end = entity["start"], entity["end"]
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= len(text)
        ):
            raise ValueError(f"Invalid span offsets for {label}")
        covered = [
            index
            for index, (left, right, _) in enumerate(tokens)
            if right > start and left < end
        ]
        if not covered:
            raise ValueError(f"Span for {label} does not cover a token")
        ner.append([covered[0], covered[-1], label])
    return {"tokenized_text": [token for _, _, token in tokens], "ner": ner}


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON at line {line_number}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--unsupported-labels", choices=("error", "drop"), default="error"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8") as destination:
        for record in load_jsonl(args.input):
            destination.write(
                json.dumps(convert_record(record, unsupported=args.unsupported_labels))
                + "\n"
            )
            count += 1
    print(f"Wrote {count} GLiNER records to {args.output}")


if __name__ == "__main__":
    main()
