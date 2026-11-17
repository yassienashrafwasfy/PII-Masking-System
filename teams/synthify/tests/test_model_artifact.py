#!/usr/bin/env python3
"""Check that Synthify's committed GLiNER ONNX artifact is complete."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The SafeTensors checkpoint this graph was exported from. See the same
# constant in teams/octo/tests/test_model_artifact.py: the checkpoint is not in
# the repository any more, so the manifest's provenance field is pinned here
# rather than hashed from a file.
SOURCE_MODEL_SHA256 = "64ec6d9460b902afd8b9516b23952203a4cf322c2aa056cf96c7262206a5a097"


def main() -> None:
    onnx_dir = Path(__file__).resolve().parents[1] / "onnx"
    config = json.loads((onnx_dir / "gliner_config.json").read_text(encoding="utf-8"))
    assert config["model_name"] == "microsoft/deberta-v3-base"
    assert (onnx_dir / "tokenizer.json").is_file()
    assert (onnx_dir / "tokenizer_config.json").is_file()

    manifest = json.loads((onnx_dir / "manifest.json").read_text(encoding="utf-8"))
    onnx_model = onnx_dir / "model.onnx"
    with onnx_model.open("rb") as handle:
        assert (
            hashlib.file_digest(handle, "sha256").hexdigest()
            == manifest["model_sha256"]
        )
    assert manifest == {
        "format": "onnx",
        "model_sha256": manifest["model_sha256"],
        "opset": 19,
        "precision": "float32",
        "runtime": "onnxruntime-cpu",
        "source_model_sha256": SOURCE_MODEL_SHA256,
    }
    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_model), providers=["CPUExecutionProvider"])
    inputs = {item.name: item.shape for item in session.get_inputs()}
    assert inputs["input_ids"][:2] == ["batch_size", "sequence_length"]
    print("Validated Synthify GLiNER ONNX artifact.")


if __name__ == "__main__":
    main()
