# Synthify model card

## Artifact

`onnx/model.onnx` is the served artifact: a float32 CPU ONNX export, opset 19, SHA-256 `8a6baba537c4a00d518586a2b56f62547259f12685b9b1be9b6cfc91fa44dd45`. It was exported from a GLiNER checkpoint fine-tuned from `urchade/gliner_medium-v2.1` for Belgian PII NER, which contained 224 tensors and had SHA-256 `64ec6d9460b902afd8b9516b23952203a4cf322c2aa056cf96c7262206a5a097`; that checkpoint is recorded in `onnx/manifest.json` as `source_model_sha256` and is not in this repository. The PyTorch checkpoint it was converted from was kept in `Deployment-repo/best_cc/`, also outside this repository.

The artifact includes `gliner_config.json`, `tokenizer.json`, and `tokenizer_config.json`. It excludes optimizer, scheduler, and RNG state because they are training-only.

## Training evidence

The source notebook trained on 35,677 records and evaluated on 8,783 records. It used focal loss (`alpha=0.75`, `gamma=2.0`), a weighted sampler, ten epochs, and early stopping after two non-improving epochs.

Reported validation scores: micro F1 `0.7842`, micro F2 `0.8229`, macro F1 `0.9074`, and macro F2 `0.9119`.

## Deployment status

The private Synthify service loads this GLiNER artifact for the Compose demo. It
accepts `text/plain` and text-bearing `application/pdf`. The platform-owned
document pipeline uses recursive XY-cut PDF extraction, then `runtime.py` maps
its 13 native labels to the shared masking taxonomy before shared redaction.

The artifact's 13-label training schema differs from the repository's QA
`qa-pii-v1` schema. Its `URL` F1 was `0.078`. Do not promote this artifact
beyond the demo until its label conversion and QA evaluation are approved.

## Regex supplement

`runtime.py` merges deterministic detections from `regex_detectors.py` into the
model's own spans before returning them. It covers `URL` (which the model
scores `0.078` on), plus checksum-validated `INSZ` (mod-97) and
`CREDITCARDNUMBER` (Luhn) and structural `RVIZ`, so these survive formats the
checkpoint misses. On overlap the wider span wins, so the merge never shrinks
masked coverage; merged spans carry source `regex` rather than `model`. The
reported validation scores above are model-only and do not include this
supplement. Set `REGEX_DETECTORS=off` to get model-only spans.
