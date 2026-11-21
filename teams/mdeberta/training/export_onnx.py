"""Export the mDeBERTa medical PII/PHI checkpoint to the fp32 ONNX graph the service loads.

The served artifact is ONNX only (docs/decisions/005-onnx-only-serving.md), so the
checkpoint published at https://huggingface.co/masterofaudio2077/medical-pii-ner-mdeberta-v3
has to be converted before it can run here. This script is that conversion and it
is not part of the locked environment; run it with the packages it needs added for
the one run:

    uv sync --locked      # torch and transformers come from the locked environment
    uv run --no-sync --with onnx --with onnxscript --with 'huggingface-hub>=1.5,<2' \\
        python teams/mdeberta/training/export_onnx.py --out /tmp/mdeberta-onnx

(``--checkpoint DIR`` uses a local copy instead of downloading the pinned revision.)

The checkpoint is stored in fp16. The graph is exported in fp32: the upcast is
exact, and fp32 is what the CPU execution provider runs fastest and what the other
served graphs use.

Output (the directory the service mounts as ONNX_MODEL_PATH):
    model.onnx, tokenizer.json, tokenizer_config.json, config.json, pii_config.json,
    manifest.json (source revision, source weights digest, graph digest, opset).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

REPO = "masterofaudio2077/medical-pii-ner-mdeberta-v3"
# Pinned so the export is reproducible: a later push to the hub repo cannot
# change what this script produces.
REVISION = "e93f0cceae9287062456c7058a1bb5d204550998"
OPSET = 17
SIDECARS = ("tokenizer.json", "tokenizer_config.json", "config.json", "pii_config.json")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_checkpoint(checkpoint: str | None, revision: str) -> Path:
    if checkpoint:
        return Path(checkpoint)
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(REPO, revision=revision))


def export_graph(source: Path, destination: Path) -> None:
    import torch
    from transformers import AutoModelForTokenClassification

    model = AutoModelForTokenClassification.from_pretrained(source, dtype=torch.float32)
    model.eval()
    # Two different lengths and batch sizes so the dynamic axes are real axes and
    # not constants the tracer folded in.
    input_ids = torch.randint(5, 1000, (2, 37))
    attention_mask = torch.ones_like(input_ids)
    attention_mask[1, 30:] = 0
    with torch.no_grad():
        torch.onnx.export(
            model,
            (input_ids, attention_mask),
            str(destination),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "logits": {0: "batch", 1: "sequence"},
            },
            opset_version=OPSET,
            do_constant_folding=True,
            dynamo=False,
        )


def write_manifest(source: Path, out: Path, revision: str) -> dict[str, object]:
    manifest: dict[str, object] = {
        "format": "onnx",
        "precision": "float32",
        "runtime": "onnxruntime-cpu",
        "opset": OPSET,
        "source_model": REPO,
        "source_revision": revision,
        "source_model_sha256": sha256_of(source / "model.safetensors"),
        "model_sha256": sha256_of(out / "model.onnx"),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--checkpoint", help="local checkpoint directory (default: download)"
    )
    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args()

    source = fetch_checkpoint(args.checkpoint, args.revision)
    args.out.mkdir(parents=True, exist_ok=True)
    export_graph(source, args.out / "model.onnx")
    for name in SIDECARS:
        shutil.copy2(source / name, args.out / name)
    manifest = write_manifest(source, args.out, args.revision)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
