"""Tests for Synthify GLiNER dataset preparation."""

from __future__ import annotations

import sys
from pathlib import Path

_SYNTHIFY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SYNTHIFY / "training" / "src"))
sys.path.insert(0, str(_SYNTHIFY))
from labels import TRAINING_LABELS  # noqa: E402
from prepare_gliner_dataset import UnsupportedLabelError, convert_record  # noqa: E402


def test_keeps_native_labels_the_checkpoint_was_trained_on() -> None:
    """GLiNER labels are prompts: training must use the runtime's spellings."""
    record = {
        "source_text": "Call Ada at 0470 00 00 00.",
        "privacy_mask": [
            {"label": "Name", "start": 5, "end": 8},
            {"label": "TELEFOON", "start": 12, "end": 26},
        ],
    }
    assert convert_record(record, unsupported="error") == {
        "tokenized_text": ["Call", "Ada", "at", "0470", "00", "00", "00."],
        "ner": [[1, 1, "Name"], [3, 6, "TELEFOON"]],
    }


def test_converts_canonical_spellings_back_to_training_labels() -> None:
    """samples/Synthify_Val.jsonl ships canonicalised; it must still convert."""
    record = {
        "source_text": "Call Ada at 0470 00 00 00.",
        "privacy_mask": [
            {"label": "NAME", "start": 5, "end": 8},
            {"label": "PHONE_NUMBER", "start": 12, "end": 26},
        ],
    }
    assert convert_record(record, unsupported="error")["ner"] == [
        [1, 1, "Name"],
        [3, 6, "TELEFOON"],
    ]


def test_title_is_a_training_label() -> None:
    """The checkpoint was fine-tuned on Title (F1 0.987 on 4,180 entities)."""
    record = {
        "source_text": "Dr Ada",
        "privacy_mask": [{"label": "Title", "start": 0, "end": 2}],
    }
    assert "Title" in TRAINING_LABELS
    assert convert_record(record, unsupported="error")["ner"] == [[0, 0, "Title"]]


def test_rejects_label_outside_the_training_schema() -> None:
    record = {
        "source_text": "IBAN BE68 5390 0754 7034",
        "privacy_mask": [{"label": "IBAN", "start": 5, "end": 24}],
    }
    try:
        convert_record(record, unsupported="error")
    except UnsupportedLabelError:
        return
    raise AssertionError("Unknown labels must require an explicit decision")


def test_can_drop_unsupported_label_after_approval() -> None:
    record = {
        "source_text": "IBAN BE68 5390 0754 7034",
        "privacy_mask": [{"label": "IBAN", "start": 5, "end": 24}],
    }
    assert convert_record(record, unsupported="drop")["ner"] == []


def main() -> None:
    cases = [
        case for name, case in sorted(globals().items()) if name.startswith("test_")
    ]
    for case in cases:
        case()
    print(f"Validated Synthify GLiNER dataset preparation ({len(cases)} cases).")


if __name__ == "__main__":
    main()
