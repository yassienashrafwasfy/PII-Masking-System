# Synthify

Synthify owns a migrated GLiNER Belgian PII NER artifact, exported to `onnx/`. It is published on Kaggle (`scripts/fetch_models.py --team synthify`) and documented in [the model card](docs/model-card.md).

`adapter.py` exports the private Synthify GLiNER detector. The platform-owned
document pipeline extracts `application/pdf` with recursive XY-cut reading order
and applies one shared redaction policy for both supported types: `application/pdf`
and `text/plain`. The adapter is callable only through the unified gateway.

## ONNX deployment artifact

Float32 CPU ONNX Runtime is the only runtime. `onnx/` holds `model.onnx`, the
GLiNER config/tokenizer files, and an immutable checksum manifest; the
SafeTensors checkpoint it was exported from is not in this repository, and
neither is an export script -- see
[ADR 005](../../docs/decisions/005-onnx-only-serving.md). `X-Model-Format`
accepts `onnx` alone and defaults to it, so a caller normally omits it.
