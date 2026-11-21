#!/usr/bin/env python3
"""Check that the mDeBERTa ONNX artifact is complete and is the one pinned here."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The checkpoint this graph was exported from (the hub repo's model.safetensors at
# the revision training/export_onnx.py pins). Pinned as a literal so a graph
# re-exported from another checkpoint cannot inherit this provenance silently.
SOURCE_MODEL_SHA256 = "131db8ef1aa4980e93a753a70690d3721df3644597fce2e5a8797d3258262917"
SOURCE_REVISION = "e93f0cceae9287062456c7058a1bb5d204550998"
MODEL_SHA256 = "b561e1fd1ac8a46e139c94744ec49dfda3b46aee63a8cfe2bf4ec1d5a975b120"


def main() -> None:
    onnx_dir = Path(__file__).resolve().parents[1] / "onnx"
    config = json.loads((onnx_dir / "config.json").read_text(encoding="utf-8"))
    assert config["model_type"] == "deberta-v2"
    assert config["architectures"] == ["DebertaV2ForTokenClassification"]
    # 36 entity labels in BIO form plus O.
    assert len(config["id2label"]) == 73
    for name in ("tokenizer.json", "tokenizer_config.json", "pii_config.json"):
        assert (onnx_dir / name).is_file(), name

    manifest = json.loads((onnx_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "format": "onnx",
        "model_sha256": MODEL_SHA256,
        "opset": 17,
        "precision": "float32",
        "runtime": "onnxruntime-cpu",
        "source_model": "masterofaudio2077/medical-pii-ner-mdeberta-v3",
        "source_model_sha256": SOURCE_MODEL_SHA256,
        "source_revision": SOURCE_REVISION,
    }
    onnx_model = onnx_dir / "model.onnx"
    with onnx_model.open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == MODEL_SHA256
    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_model), providers=["CPUExecutionProvider"])
    inputs = {item.name: item.shape for item in session.get_inputs()}
    # No token_type_ids: the engine feeds exactly the inputs the graph declares.
    assert set(inputs) == {"input_ids", "attention_mask"}, inputs
    assert inputs["input_ids"][:2] == ["batch", "sequence"]
    print("Validated mDeBERTa ONNX artifact.")


if __name__ == "__main__":
    main()
