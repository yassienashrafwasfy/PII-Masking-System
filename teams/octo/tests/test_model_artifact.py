#!/usr/bin/env python3
"""Check that Octo's committed XLM-R ONNX artifact is complete."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The SafeTensors checkpoint this graph was exported from. It is no longer in
# the repository -- ONNX is the only served format -- so this is pinned as a
# literal rather than hashed from a file. It still has to match the manifest:
# a graph re-exported from a different checkpoint must not inherit the old
# provenance record silently.
SOURCE_MODEL_SHA256 = "813ee595c6241df9c7cdd8ece6d3139fd3fdef282066f230c9ae9fe27238cdfe"


def main() -> None:
    onnx_dir = Path(__file__).resolve().parents[1] / "onnx"
    config = json.loads((onnx_dir / "config.json").read_text(encoding="utf-8"))
    assert config["model_type"] == "xlm-roberta"
    assert config["architectures"] == ["XLMRobertaForTokenClassification"]
    assert len(config["id2label"]) == 77
    for name in (
        "sentencepiece.bpe.model",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        assert (onnx_dir / name).is_file()

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
    assert inputs["input_ids"][:2] == ["batch", "sequence"]
    assert inputs["attention_mask"][:2] == ["batch", "sequence"]
    print("Validated Octo XLM-R ONNX artifact.")


if __name__ == "__main__":
    main()
