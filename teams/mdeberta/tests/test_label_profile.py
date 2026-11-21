#!/usr/bin/env python3
"""Every one of the checkpoint's 36 labels must be mapped, none silently dropped."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from masking_engine.app.core import label_registry
    from masking_engine.app.engines.label_converter import LabelConverter

    labels = json.loads((root / "teams/mdeberta/onnx/pii_config.json").read_text())[
        "labels"
    ]
    assert len(labels) == 36
    # strict=True raises on a label no profile entry covers.
    converter = LabelConverter("mdeberta", strict=True)
    masked = label_registry.masked_labels()
    for label in labels:
        target = converter.canonical_label(label)
        assert target in masked, (label, target)

    # The three-way agreement with the other teams: same concept, same name.
    assert converter.canonical_label("DIAGNOSIS") == "DIAGNOSIS"
    assert converter.canonical_label("MEDICATION") == "MEDICATION"
    assert converter.canonical_label("SYMPTOM") == "SYMPTOM"
    assert (
        LabelConverter("octo", strict=True).canonical_label("LAB_RESULT")
        == "TESTRESULTS"
    )
    assert (
        LabelConverter("berts", strict=True).canonical_label("PROCEDURE") == "PROCEDURE"
    )
    # NATIONAL_ID merges Steuer-ID and SSN here, so it must not become a Belgian INSZ.
    assert converter.canonical_label("NATIONAL_ID") == "NATIONAL_ID"
    assert (
        LabelConverter("berts", strict=True).canonical_label("NATIONAL_ID")
        == "INSZ_NISS"
    )
    print("Validated the mdeberta label profile.")


if __name__ == "__main__":
    main()
