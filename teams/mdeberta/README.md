# mDeBERTa

A multilingual (nl, fr, de, es, en) PII and clinical (PHI) token classifier, served
as the team `mdeberta` behind the shared gateway. Source model:
[masterofaudio2077/medical-pii-ner-mdeberta-v3](https://huggingface.co/masterofaudio2077/medical-pii-ner-mdeberta-v3)
(Apache-2.0, fine-tuned from `microsoft/mdeberta-v3-base`, MIT). No separate HTTP
API: `adapter.py` is the same thin wrapper the other teams use and the platform's
shared engine does the work.

The approved artifact is the fp32 ONNX graph in `onnx/` (one `model.onnx`,
1.1 GB), published on Kaggle and in its own Hugging Face repo,
[masterofaudio2077/medical-pii-ner-mdeberta-v3-onnx](https://huggingface.co/masterofaudio2077/medical-pii-ner-mdeberta-v3-onnx)
(listed under the source model's quantized children). Fetch it with:

```sh
uv run --no-project --with kagglehub python scripts/fetch_models.py --team mdeberta
uv run --no-project --with huggingface-hub python scripts/fetch_models.py --team mdeberta --source hub
```

`training/export_onnx.py` re-creates the graph from the pinned checkpoint
revision (a re-export was checked to reproduce the same `model.onnx` SHA-256); see [ADR 007](../../docs/decisions/007-mdeberta-team-and-shared-clinical-labels.md)
for why this team keeps one and how its 36 labels map to the platform's.

Run it locally: `docker compose --profile mdeberta up --build --no-deps gateway mdeberta-model`,
then send `X-Team-ID: mdeberta`.
