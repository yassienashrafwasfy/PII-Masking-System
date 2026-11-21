#!/usr/bin/env python3
"""Exercise the mDeBERTa graph through the platform's own detect() path.

Values and labels, not scores: a re-export may move a score and must not move a
span. The sentences are synthetic and cover the three claims this team makes:
identity PII, the clinical (PHI) labels, and more than one language.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MODEL_SHA256 = "b561e1fd1ac8a46e139c94744ec49dfda3b46aee63a8cfe2bf4ec1d5a975b120"


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    onnx_dir = root / "teams/mdeberta/onnx"
    os.environ.update(
        {
            "DETECTION_MODE": "model_regex",
            "ONNX_MODEL_PATH": str(onnx_dir),
            "DETECTOR_LABEL_PROFILE": "mdeberta",
            "ACCELERATOR": "cpu",
            "MODEL_VERSION": "inference-smoke",
            "ONNX_MODEL_SHA256": MODEL_SHA256,
        }
    )
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    import masking_engine
    from masking_engine import OnnxTokenClassifierEngine

    engine = OnnxTokenClassifierEngine(
        model_path=str(onnx_dir),
        model_version="inference-smoke",
        confidence_threshold=0.5,
        accelerator="cpu",
    )
    assert engine.ready
    assert engine.model_sha256 == MODEL_SHA256

    dutch = (
        "Patiënt Pieter De Smet (geboren 12/03/1975) woont aan Kerkstraat 12, "
        "9000 Gent. E-mail pieter.desmet@example.com. Diagnose: type 2 diabetes, "
        "medicatie metformine 500 mg."
    )
    german = (
        "Herr Hans Müller, geboren am 21.09.1964, Hauptstraße 5, 10115 Berlin. "
        "Diagnose: Bluthochdruck, Ramipril 5 mg."
    )
    for text, expected in (
        (
            dutch,
            {
                ("Pieter De Smet", "NAME"),
                ("pieter.desmet@example.com", "EMAIL"),
                ("type 2 diabetes", "DIAGNOSIS"),
            },
        ),
        (german, {("Hans Müller", "NAME"), ("Bluthochdruck", "DIAGNOSIS")}),
    ):
        spans = masking_engine.detect(text, {"content_type": "text/plain"})
        found = {(text[s.start : s.end], s.label) for s in spans}
        assert expected <= found, (sorted(expected - found), sorted(found))
        # Street, postcode and city arrive as three native labels and leave as
        # one ADDRESS span (the profile's composite rule).
        assert any(label == "ADDRESS" for _, label in found), sorted(found)
        assert not any(label in {"STREET", "ZIPCODE", "CITY"} for _, label in found)
        assert any(label == "PRESCRIPTIONINFO" for _, label in found), sorted(found)
        assert any(
            label == "DATE" and value.startswith(("12/03", "21.09"))
            for value, label in found
        )

    # CUDA is not a configuration this graph is validated against.
    try:
        OnnxTokenClassifierEngine(
            model_path=str(onnx_dir),
            model_version="inference-smoke",
            confidence_threshold=0.5,
            accelerator="cuda",
        )
    except Exception as exc:
        assert "CPU only" in str(exc), exc
    else:
        raise AssertionError("the ONNX engine accepted a CUDA accelerator")
    print("Validated mDeBERTa ONNX inference.")


if __name__ == "__main__":
    main()
