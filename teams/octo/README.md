# Octo

Team-owned model implementation space. `adapter.py` now uses the migrated Unified-repo detector, regex rules, and pseudonymization runtime in `implementation/`. Do not expose a separate API to NiFi.

The approved migrated model artifact is the exported graph in `onnx/`, published on Kaggle (`scripts/fetch_models.py --team octo`). Its 45-label classifier head is intentionally unchanged; the shared engine's `app/core/label_profiles.json` maps its legacy outputs into the QA `qa-pii-v1` schema. A newly trained model must emit that 12-label schema directly. Do not edit
`onnx/config.json` unless replacing both the graph and its label mapping. The default `regex` mode keeps local and contract checks independent of model weights.

## ONNX deployment artifact

Float32 CPU ONNX Runtime is the only runtime. `onnx/` holds `model.onnx`, the
tokenizer/config files, and an immutable checksum manifest; the SafeTensors
checkpoint it was exported from is not in this repository, and neither is an
export script -- see [ADR 005](../../docs/decisions/005-onnx-only-serving.md).
`X-Model-Format` accepts `onnx` alone and defaults to it, so a caller normally
omits it. Re-exporting means re-obtaining the checkpoint and re-deriving
`onnx/manifest.json`, which is a release decision rather than a local step.
