# Berts model card

Berts serves one int8 ONNX token classifier per document language. The platform
resolves the language of each document once and hands it to the adapter, which
picks the matching checkpoint; Dutch is the fallback when detection abstains or
the language has no checkpoint of its own.

| Locale | Directory | Reported model name | Source |
| --- | --- | --- | --- |
| `nl_BE` (default) | `onnx/` | `berts-pii-medroberta-nl-int8` | `ziadosama/medroberta_nl_PII_PHI_onnx_int8` @ `07f18eb53e127ed34aa77a64e1e15f24ea622be8` |
| `en` | `onnx-en/` | `berts-pii-bert-en-int8` | `ziadosama/deid_bert_en_PII_PHI_onnx_int8` @ `45f397a28f3efc462ec89552a7b8de574f3f61dc` |
| `fr_FR` | `onnx-fr/` | `berts-pii-camembert-bio-fr-int8` | `farahelmashad/pii-camembert-bio-fr-int8-onnx` @ `162c057100bb174252303bb350df4c47296ea5bd` |

Committed `model.onnx` SHA-256:

- `nl_BE` `e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292`
- `en` `c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd`
- `fr_FR` `2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4`

All three use ONNX opset 18, have dynamic batch and sequence dimensions, are
int8-quantized, and run on the CPU execution provider. No team has a torch
fallback any more. The three differ in tokenizer family, and the graph inputs follow:
the Dutch RoBERTa and the French CamemBERT take `input_ids` and
`attention_mask`, the English BERT additionally takes `token_type_ids`. The
shared ONNX engine feeds whatever inputs the loaded graph declares, so nothing
in the adapter branches on this.

Each checkpoint is loaded lazily, on the first document in its language, and
all loaded checkpoints are dropped together by idle unloading. A deployment
that only ever sees Dutch never pays memory for the other two; a mixed-language
batch holds all three.

Every checkpoint emits the same ten labels -- ADDRESS, AGE, DATE, DISEASE,
MEDICATION, NAME, ORGANIZATION, PHONE, PROCEDURE, SYMPTOM -- with the same
`label2id`, so label mapping to `qa-pii-v1` and the merge policy are
identical whichever one answered. DISEASE/MEDICATION/SYMPTOM are clinical PHI,
masked as `routing_only_labels` rather than identity PII (see ADR 006);
DISEASE is aliased to the taxonomy's `DIAGNOSIS`, and PROCEDURE has no
canonical target and is dropped, the same as Octo's own PROCEDURE class. Berts
combines the model with its own validated regex layer: it checksum-validates
Belgian INSZ, RIZIV, and VAT identifiers; detects email and URLs; and rejects
dosage, blood-pressure, and clinical-measurement hard negatives. AGE, DATE, and
PHONE are owned by neither source: model and regex spans both reach the merge
and the wider one is kept, so a value the checkpoint tags only part of is still
covered whole. It supports `application/pdf` and `text/plain`.

The returned provenance names the checkpoint that actually answered, so
`X-Model-Name` and `X-Model-Version` differ between a Dutch and a French
document from the same deployment. That is deliberate: one name covering three
artifacts would make the response provenance unable to say which weights
produced the masked bytes.

## Known checkpoint quality notes

The French checkpoint over-masks. It emits occasional spurious spans on
function words (`de` as AGE, `à`/`la` as ORGANIZATION) and over-spans
organization names into the following sentence boundary. This is the safe
direction -- more masked, not less -- and is checkpoint behaviour, not routing:
no stopword filter is applied, because dropping model spans by surface form is
exactly the kind of silent under-masking this service must not introduce.
