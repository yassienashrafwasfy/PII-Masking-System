"""Tests that the training and inference label spaces cannot drift apart."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from labels import (  # noqa: E402
    CANONICAL_LABELS,
    TRAINING_LABELS,
    to_training_label,
)

# FINAL_LABELS from Milestone_1/train_colab.ipynb, cell 3.
NOTEBOOK_LABELS = [
    "Date",
    "Name",
    "Email",
    "Address",
    "Title",
    "TELEFOON",
    "Age",
    "RVIZ",
    "INSZ",
    "Gender",
    "CREDITCARDNUMBER",
    "URL",
    "Hospital_Name",
]


def test_training_labels_match_the_source_notebook() -> None:
    assert list(TRAINING_LABELS) == NOTEBOOK_LABELS


def test_every_canonical_mapping_starts_from_a_trained_label() -> None:
    """A canonical entry for an untrained label would never fire."""
    assert set(CANONICAL_LABELS) <= set(TRAINING_LABELS)


def test_only_title_lacks_a_canonical_label() -> None:
    assert set(TRAINING_LABELS) - set(CANONICAL_LABELS) == {"Title"}


def test_native_and_canonical_spellings_resolve_to_the_same_label() -> None:
    for native, canonical in CANONICAL_LABELS.items():
        assert to_training_label(native) == native
        assert to_training_label(canonical) == native


def test_spelling_variants_are_folded() -> None:
    assert to_training_label("hospital name") == "Hospital_Name"
    assert to_training_label("phone-number") == "TELEFOON"
    assert to_training_label("insz_niss") == "INSZ"


def test_unknown_label_returns_none() -> None:
    assert to_training_label("IBAN") is None


def main() -> None:
    cases = [
        case for name, case in sorted(globals().items()) if name.startswith("test_")
    ]
    for case in cases:
        case()
    print(f"Validated Synthify training/inference label space ({len(cases)} cases).")


if __name__ == "__main__":
    main()
