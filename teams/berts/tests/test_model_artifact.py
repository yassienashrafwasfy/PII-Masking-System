#!/usr/bin/env python3
"""Validate Berts' immutable int8 ONNX artifacts, one per document language.

Three checkpoints are interchangeable to the rest of the service only if they
stay interchangeable: same twenty-one BIO tags in the same order -- six
identity fields plus DISEASE/MEDICATION/PROCEDURE/SYMPTOM, see ADR 006 --
same int8 quantization, same opset. That is what this pins, alongside the
digest each one is approved at. A checkpoint that drifts from the shared
shape would be caught here rather than by a document masked in the wrong
taxonomy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# The BIO tag set every Berts checkpoint emits. Not just the same names: the
# same ids, because the runtime reads `id2label` from whichever config it
# loaded and a permuted map would mislabel every span.
LABELS = {
    "O": 0,
    "B-ADDRESS": 1,
    "I-ADDRESS": 2,
    "B-AGE": 3,
    "I-AGE": 4,
    "B-DATE": 5,
    "I-DATE": 6,
    "B-DISEASE": 7,
    "I-DISEASE": 8,
    "B-MEDICATION": 9,
    "I-MEDICATION": 10,
    "B-NAME": 11,
    "I-NAME": 12,
    "B-ORGANIZATION": 13,
    "I-ORGANIZATION": 14,
    "B-PHONE": 15,
    "I-PHONE": 16,
    "B-PROCEDURE": 17,
    "I-PROCEDURE": 18,
    "B-SYMPTOM": 19,
    "I-SYMPTOM": 20,
}

EXPECTED = {
    "nl_BE": {
        "architectures": ["RobertaForTokenClassification"],
        "inputs": {"input_ids", "attention_mask"},
        "manifest": {
            "format": "onnx",
            "locale": "nl_BE",
            "model_sha256": (
                "e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292"
            ),
            "opset": 18,
            "precision": "int8",
            "runtime": "onnxruntime-cpu",
            "source_model": "ziadosama/medroberta_nl_PII_PHI_onnx_int8",
            "source_revision": "07f18eb53e127ed34aa77a64e1e15f24ea622be8",
        },
    },
    "en": {
        "architectures": ["BertForTokenClassification"],
        # A BERT graph asks for segment ids as well. The shared pipeline feeds
        # whatever the graph declares and refuses an input the tokenizer does
        # not produce, so this is pinned rather than assumed.
        "inputs": {"input_ids", "attention_mask", "token_type_ids"},
        "manifest": {
            "format": "onnx",
            "locale": "en",
            "model_sha256": (
                "c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd"
            ),
            "opset": 18,
            "precision": "int8",
            "runtime": "onnxruntime-cpu",
            "source_model": "ziadosama/deid_bert_en_PII_PHI_onnx_int8",
            "source_revision": "45f397a28f3efc462ec89552a7b8de574f3f61dc",
        },
    },
    "fr_FR": {
        "architectures": ["CamembertForTokenClassification"],
        "inputs": {"input_ids", "attention_mask"},
        "manifest": {
            "format": "onnx",
            "locale": "fr_FR",
            "model_sha256": (
                "2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4"
            ),
            "opset": 18,
            "precision": "int8",
            "runtime": "onnxruntime-cpu",
            "source_model": "farahelmashad/pii-camembert-bio-fr-int8-onnx",
            "source_revision": "162c057100bb174252303bb350df4c47296ea5bd",
        },
    },
}


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "platform"))
    sys.path.insert(0, str(root / "platform/masking-gateway/src"))
    from teams.berts.runtime import CHECKPOINTS

    assert set(CHECKPOINTS) == set(EXPECTED), "a checkpoint is untested"

    import onnxruntime as ort

    for locale, expected in EXPECTED.items():
        checkpoint = CHECKPOINTS[locale]
        onnx_dir = Path(__file__).resolve().parents[1] / checkpoint.directory
        manifest = json.loads((onnx_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest == expected["manifest"], locale

        # The runtime refuses to load an artifact whose digest differs, so the
        # digest it is pinned at and the one the manifest records must agree
        # or the service fails at load rather than here.
        assert checkpoint.sha256 == manifest["model_sha256"], locale
        assert checkpoint.revision == manifest["source_revision"], locale

        model = onnx_dir / "model.onnx"
        with model.open("rb") as handle:
            assert hashlib.file_digest(handle, "sha256").hexdigest() == (
                checkpoint.sha256
            ), locale
        # int8, not a float32 artifact renamed: the quantized graph multiplies
        # through integer matmuls, and nothing else in the file says so.
        assert b"MatMulInteger" in model.read_bytes(), locale

        config = json.loads((onnx_dir / "config.json").read_text(encoding="utf-8"))
        assert config["architectures"] == expected["architectures"], locale
        assert config["label2id"] == LABELS, locale

        session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        inputs = {item.name: item.shape for item in session.get_inputs()}
        assert set(inputs) == expected["inputs"], (locale, sorted(inputs))
        assert all(
            shape == ["batch_size", "sequence_length"] for shape in inputs.values()
        ), locale
        assert session.get_outputs()[0].shape == [
            "batch_size",
            "sequence_length",
            len(LABELS),
        ], locale

    print(f"Validated {len(EXPECTED)} Berts int8 ONNX artifacts.")


if __name__ == "__main__":
    main()
