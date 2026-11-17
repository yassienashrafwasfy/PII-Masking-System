#!/usr/bin/env python3
"""Exercise Synthify's ONNX GLiNER runtime end to end on a known sentence.

This replaced a SafeTensors/ONNX numerical parity check. See the note in
teams/octo/tests/test_model_inference.py: with the source checkpoint no longer
in the repository there is no second engine to compare against, so what is
pinned is that the graph loads under the runtime the service uses, finds the
identifiers, and reports its own digest as the artifact that answered.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from teams.synthify.runtime import (
        SUPPORTED_MODEL_FORMATS,
        _model,
        ready,
        release,
        unload,
    )

    assert SUPPORTED_MODEL_FORMATS == frozenset({"onnx"})
    text = "Patient Els Wouters emailed els@example.com on 12/03/2025."
    labels = ["Date", "Name", "Email", "Address"]
    model = _model("onnx")
    found = {
        (text[entity["start"] : entity["end"]], entity["label"])
        for entity in model.predict_entities(text, labels, threshold=0.3)
    }
    assert ("Els Wouters", "Name") in found, sorted(found)
    assert ("els@example.com", "Email") in found, sorted(found)
    assert ("12/03/2025", "Date") in found, sorted(found)
    assert ready("onnx")

    # The reported digest comes from the committed manifest beside the graph,
    # so a staged artifact and the identity written into the masked object's
    # key cannot drift apart.
    name, version, digest = release("onnx")
    assert name == "synthify-gliner-belgian-pii-ner", name
    assert digest == (
        "sha256:8a6baba537c4a00d518586a2b56f62547259f12685b9b1be9b6cfc91fa44dd45"
    ), digest
    assert version == digest, (version, digest)

    unload()
    assert not ready("onnx")
    print(f"Validated Synthify ONNX inference on {len(found)} spans.")


if __name__ == "__main__":
    main()
