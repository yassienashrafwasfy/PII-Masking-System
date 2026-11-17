# Synthify training

This is the reproducible GLiNER training path extracted from
`gliner_belgian_pii_ner_finetuning.ipynb`. It accepts source JSONL records,
converts their character spans to GLiNER word spans, then trains a model.

```sh
python -m pip install -r requirements.example.txt
python training/src/prepare_gliner_dataset.py \
  --input /approved/train.jsonl \
  --output /approved/train.gliner.jsonl
python training/src/prepare_gliner_dataset.py \
  --input ../samples/Synthify_Val.jsonl \
  --output /approved/validation.gliner.jsonl \
  --unsupported-labels drop
python training/src/train_gliner.py \
  --train /approved/train.gliner.jsonl \
  --validation /approved/validation.gliner.jsonl \
  --output /approved/model
```

`prepare_gliner_dataset.py` uses the canonical `qa-pii-v1` labels and the
Synthify converter profile. It fails on labels that QA has not approved. The
current validation file contains `Title` and `CREDITCARDNUMBER`; use
`--unsupported-labels drop` only after approving their exclusion.

The supplied `samples/Synthify_Val.jsonl` is validation data, not a training
set. Pass an approved training JSONL explicitly; datasets and trained output
remain outside Git.
