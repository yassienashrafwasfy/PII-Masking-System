# ADR 006: Clinical PHI as routing-only labels

**Status:** Accepted

Octo's checkpoint carries `B-MEDICATION/I-MEDICATION`, `B-SYMPTOM/I-SYMPTOM` and
`B-DIAGNOSIS/I-DIAGNOSIS` (`teams/octo/onnx/config.json`) alongside `PROCEDURE`,
`LAB_RESULT` and several administrative classes, and every one of them was
discarded at `label_converter.py:convert` for want of a canonical target: the model
predicted a patient's medication list and the converter dropped the spans because
`MEDICATION` was not in `masked_labels()`. Masking a clinical letter therefore left
the medication, symptom and diagnosis text intact.

`app/core/label_profiles.json` already carried the fix's shape before this change:
an empty `routing_only_labels` list, whose note named exactly this situation --
"Octo's checkpoint natively emits these as clinical/administrative PHI classes, and
this list lets it mask them without expanding qa-pii-v1 itself." That note pointed
at `docs/results/TRAINING_DATA_REALISM_GAPS.md`, a file that does not exist in this
repository; this ADR replaces that dangling reference.

## MEDICATION, SYMPTOM, DIAGNOSIS join `routing_only_labels`, not `entity_labels`

`entity_labels` is the identity-PII taxonomy: canonical, schema-versioned, scored by
the arena, mirrored into six vendored copies (`evaluation/leaderboard_api/labels.py`,
team `model.example.yaml` files, training label tables). `routing_only_labels` is
masked the same way but carries none of that: no schema bump, no arena column, no
QA ground truth. Clinical PHI is the case this second list exists for -- it is
sensitive and must leave the document, but it is not an identity taxonomy entry
and there is no QA-defined ground truth for it yet. Promoting it to `entity_labels`
later, once ground truth exists, is a deliberate follow-up ADR, not an accident of
this one.

## DIAGNOSIS, not DISEASE

Octo's checkpoint emits `DIAGNOSIS`, and `core/surrogate_vocabularies_nl.json`
already ships a 288-term `DIAGNOSIS` pool sourced from `nl.wikipedia
Categorie:Aandoening`. Renaming either side to `DISEASE` would need an alias hop for
no benefit; the taxonomy takes the model's own spelling, the way `RPPS` and `NIR`
took the model's and the regex engine's own spellings in ADR 002.

## Model-only detection

These three are open-vocabulary text with no unit anchor, no checksum and no fixed
grammar -- none of the properties `orchestrator._REGEX_EXCLUSIVE` requires ("a regex
validates outright"). No regex rule is written for them, and they are not added to
that set: the model is the only plausible detector.

Octo's checkpoint was the first to carry these classes; Berts' three checkpoints
(`ziadosama/medroberta_nl_PII_PHI_onnx_int8`,
`farahelmashad/pii-camembert-bio-fr-int8-onnx`,
`ziadosama/deid_bert_en_PII_PHI_onnx_int8`) gained the same four PHI classes at the
same time this taxonomy was extended, and all three teams speak a different native
spelling for the same concept: Octo says `DIAGNOSIS`, Berts says `DISEASE`. The
canonical label stays `DIAGNOSIS` -- it was already the vocabulary file's key and
the palette entry before Berts' spelling was known -- and Berts' `berts` profile in
`label_profiles.json` carries `"DISEASE": "DIAGNOSIS"`, the same per-profile alias
pattern `PROVIDER_ID -> RIZIV_INAMI` already uses. Berts' checkpoints also carry a
fifth class, `PROCEDURE`, that Octo's carries too; neither is given a canonical
target, so both are dropped at `label_converter.convert` and logged, the same as
any other unmapped label -- masking PROCEDURE was considered and deliberately left
out of this change's scope, not overlooked.

Synthify has no clinical head in its GLiNER checkpoint (verified against
`teams/synthify/labels.py`), so routing a document to Synthify masks identity PII
only, exactly as before this change.

## Evaluation stays blind on purpose

`masked_labels()` feeds both the router and `gateway.py`/`service.py`'s
`selectable_labels`/`default_masked_labels`, so the three are masked by default and
selectable via `X-Mask-Labels` -- that is required, since the goal is that a
document leaves the service with these values replaced. What does not change is
`entity_labels`, which is what `evaluation/leaderboard_api/labels.py` mirrors and
`evaluator/metrics.py` scores against. No arena column appears, and the synthetic
benchmark corpus (`evaluation/datasets/generate_synthetic.py`) carries no clinical
prose in the first place, so `overmasking_rate` cannot move either. A label neither
list recognises is never dropped by `leaderboard_api/labels.py:normalize` -- it
surfaces flagged off-taxonomy rather than vanishing -- so no filter was added there;
nothing reports these labels today; the exclusion is achieved entirely by keeping
them out of `entity_labels`.

## Locale follows ADR 002

A pseudonym has to read like the document it replaced a value in. `core/locales.py`
gained a `vocabulary_file` field per locale, mirroring `organization_pools`, so a
French document draws French medication and symptom names and an English one draws
English ones, rather than every locale drawing from the Dutch pool the way the
identical earlier gap in ADR 002 was fixed for organisation names. The Dutch pool's
two invariants -- provenance recorded beside the generated file, and no value
containing a comma or semicolon so a replacement survives a CSV or JSON field --
carry over unchanged to the French and English pools built alongside it.
