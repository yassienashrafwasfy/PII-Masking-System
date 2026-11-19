# Berts

Berts serves int8-quantized ONNX PII token classifiers through the unified
gateway, one checkpoint per document language. It supports text-bearing PDFs
and plain text. Every checkpoint recognizes names, organizations, addresses,
ages, dates, and phone numbers. Structured PII -- identifiers, VAT numbers,
postcodes, house numbers, email addresses, URLs, dates and phones -- comes from
the platform's shared regex engine, the same one Octo runs; Berts carried its
own Belgian-only table until it was found printing a French national number in
the clear. Neither source owns AGE, DATE or PHONE_NUMBER: both reach the merge
and the one covering more of the value wins, with an exact tie going to the
model. Berts used to drop every regex span carrying one of those three
outright, which left a value the checkpoint tagged only part of with nothing
to catch the rest -- the Dutch model read "- 04-2025:" as "-2025" and the
letter went out reading "- 04" in the clear beside the masked year.

`MODEL_ID_SHAPE_GATE` is the platform's too, and this adapter calls it
directly: there is no `Orchestrator` on the Berts path, which is where the
gate is applied for every other team. It matters most for AGE, where the regex
layer cannot cover for the model at all: a prediction over a word instead of a
number has no number under it for any pattern to find, and without the gate it
is released redrawn as letters.

The platform resolves the document's language once -- from `X-Document-Locale`
when the caller sets it, otherwise by detection -- and passes it to the
adapter, which selects the checkpoint. Dutch is the fallback for any language
without one.

| Locale | Directory | Source revision |
| --- | --- | --- |
| `nl_BE` (default) | `onnx/` | `ziadosama/medroberta_nl_PII_PHI_onnx_int8@07f18eb53e127ed34aa77a64e1e15f24ea622be8` |
| `en` | `onnx-en/` | `ziadosama/deid_bert_en_PII_PHI_onnx_int8@45f397a28f3efc462ec89552a7b8de574f3f61dc` |
| `fr_FR` | `onnx-fr/` | `farahelmashad/pii-camembert-bio-fr-int8-onnx@162c057100bb174252303bb350df4c47296ea5bd` |

`BERTS_LANGUAGE_ROUTING=off` sends every document to the Dutch checkpoint,
restoring the single-model behaviour that predates the other two. It is the
rollback for a bad checkpoint: one restart, no release.

The only supported runtime format is CPU `onnx`. All three immutable artifacts
are published on Kaggle (`scripts/fetch_models.py --team berts`; upstream license not confirmed, see NOTICE); a deployment stages all three together,
because a missing one is not noticed at startup but at the first document in
that language. See `docs/model-card.md` for digests, graph inputs, and known
checkpoint quality notes, and `deploy/README.md` for the staging procedure.

`training/tamerbert_generalization_trial.py` is the supplied TamerBERT trial
source, retained unchanged as the model-training and evaluation record.
