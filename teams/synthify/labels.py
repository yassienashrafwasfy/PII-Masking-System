"""The label space shared by Synthify training and inference.

A GLiNER prompt *is* a label string: the checkpoint only answers to the exact
spellings it was fine-tuned on. Training-data preparation and the runtime must
therefore agree, so both import `TRAINING_LABELS` from here instead of each
declaring its own list -- that duplication is what let the two drift apart.

`TRAINING_LABELS` reproduces `FINAL_LABELS` from the source notebook
(Milestone_1/train_colab.ipynb), which is what the committed checkpoint was
fine-tuned on. `CANONICAL_LABELS` is a separate, inference-only concern: the
mapping from those native spellings onto the shared masking taxonomy.
"""

from __future__ import annotations

import re

TRAINING_LABELS = (
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
)

# Native checkpoint label -> shared masking taxonomy label.
#
# `Title` is deliberately absent: it is not in the `qa-pii-v1` taxonomy, so its
# spans are dropped after prediction. It must still be *prompted* for, because
# the checkpoint was trained with it and removing a type from the prompt set
# changes how the remaining types compete -- see `runtime.detect`.
#
# `CREDITCARDNUMBER -> BTW_EENHEID` is inherited from the original runtime and
# has no evaluation evidence behind it: the notebook scored this class at
# F1 0.995 under the name `CREDITCARDNUMBER`, and `BTW_EENHEID` appears nowhere
# in the validation data. It is preserved here unchanged because the taxonomy
# side of it is a QA decision, not a training one, but it needs confirming.
CANONICAL_LABELS = {
    "Date": "DATE",
    "Name": "NAME",
    "Email": "EMAIL",
    "Address": "ADDRESS",
    "TELEFOON": "PHONE_NUMBER",
    "Age": "AGE",
    "RVIZ": "RIZIV_INAMI",
    "INSZ": "INSZ_NISS",
    "Gender": "GENDER",
    "CREDITCARDNUMBER": "BTW_EENHEID",
    "URL": "URL",
    "Hospital_Name": "ORGANIZATION_NAME",
}

_SEPARATORS = re.compile(r"[\s\-_]+")


def normalize(label: str) -> str:
    """Fold a label to a spelling-insensitive key."""
    return _SEPARATORS.sub("", label.strip()).upper()


# Every source spelling we accept -> the native label to train on. Covers both
# the checkpoint's own spellings and the canonical taxonomy ones, so a corpus
# that has already been canonicalised (samples/Synthify_Val.jsonl carries
# `NAME`/`PHONE_NUMBER`) converts back to trainable labels too.
SOURCE_ALIASES = {normalize(label): label for label in TRAINING_LABELS} | {
    normalize(canonical): native for native, canonical in CANONICAL_LABELS.items()
}


def to_training_label(label: str) -> str | None:
    """Return the native training label for `label`, or None if unsupported."""
    return SOURCE_ALIASES.get(normalize(label))
