#!/usr/bin/env python3
"""Exercise Octo's ONNX runtime end to end on a known sentence.

This replaced a SafeTensors/ONNX numerical parity check. Parity was the right
test while two engines could answer the same request; with the checkpoint gone
from the repository there is no second engine and no reference logits to
compare against. What still has to hold is that the graph actually loads under
the runtime the service uses and finds the identifiers in a document, so that
is what this asserts -- values and offsets, not scores, because a re-export
may legitimately move a score and must not legitimately move a span.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    os.environ.update(
        {
            "DETECTION_MODE": "model_regex",
            "ONNX_MODEL_PATH": str(root / "teams/octo/onnx"),
            "ACCELERATOR": os.environ.get("ACCELERATOR", "cpu"),
            "MODEL_VERSION": "inference-smoke",
        }
    )
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from masking_engine import OnnxTokenClassifierEngine

    text = "Patient Els Wouters emailed els@example.com on 12/03/2025."
    engine = OnnxTokenClassifierEngine(
        model_path=str(root / "teams/octo/onnx"),
        model_version="inference-smoke",
        confidence_threshold=0.5,
        accelerator="cpu",
    )
    assert engine.ready
    # The digest the engine reports is the graph's own, computed from the file
    # it opened -- not a value handed to it -- so a mounted artifact that is
    # not the approved one cannot be reported as if it were.
    assert engine.model_sha256 == (
        "21b0e69d83e992751c6e1420e74e17f6bb8f5765d0259651a62dd55d85a09236"
    )
    found = {(text[span.start : span.end], span.label) for span in engine.infer(text)}
    assert ("Els Wouters", "NAME") in found, sorted(found)
    assert ("els@example.com", "EMAIL") in found, sorted(found)
    assert ("12/03/2025", "DATE") in found, sorted(found)
    # CUDA is not a configuration this graph is validated against, and a host
    # that happens to expose a GPU must not silently get one.
    try:
        OnnxTokenClassifierEngine(
            model_path=str(root / "teams/octo/onnx"),
            model_version="inference-smoke",
            confidence_threshold=0.5,
            accelerator="cuda",
        )
    except Exception as exc:
        assert "CPU only" in str(exc), exc
    else:
        raise AssertionError("the ONNX engine accepted a CUDA accelerator")
    print(f"Validated Octo ONNX inference on {len(found)} spans.")


if __name__ == "__main__":
    main()
