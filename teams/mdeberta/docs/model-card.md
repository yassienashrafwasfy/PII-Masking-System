# mDeBERTa model card

**What it is.** `masterofaudio2077/medical-pii-ner-mdeberta-v3`, a token classifier
fine-tuned from `microsoft/mdeberta-v3-base` for personal identifiers and clinical
information in Dutch, French, German, Spanish and English. Served here as an fp32
ONNX graph (opset 17, CPU, ONNX Runtime) exported from the checkpoint revision
`e93f0cceae9287062456c7058a1bb5d204550998`. Graph SHA-256:
`b561e1fd1ac8a46e139c94744ec49dfda3b46aee63a8cfe2bf4ec1d5a975b120`.

**Accepted types.** PDF, plain text, CSV and JSON, through the shared pipeline.

**Labels.** 36 native labels. Identity: NAME, DATE, AGE, GENDER, EMAIL, URL, PHONE,
ORGANIZATION, STREET/ZIPCODE/CITY (merged into one ADDRESS) and the identifiers
INSZ, RIZIV, BTW, RPPS, NIR, NHS_NUMBER, NPI, GMC, DNI, NIE, LANR, COLEGIADO, BSN,
KVNR, IBAN, MEDICALRECORDNUM, NATIONAL_ID. Clinical: DIAGNOSIS, MEDICATION, SYMPTOM,
ALLERGIES, PRESCRIPTIONINFO, TREATMENTINFO, TESTRESULTS, PROCEDURE. The mapping to
the platform's labels is the `mdeberta` entry of `label_profiles.json`
([ADR 007](../../../docs/decisions/007-mdeberta-team-and-shared-clinical-labels.md)).

**Training data.** 11,007 synthetic documents (9,905 train / 551 validation / 551
test), written by an LLM from structured fact sheets; no real patient text.

**Reported quality (the source card, synthetic held-out test, not re-measured here).**
Span micro F1 0.910; PII-only span micro F1 0.988. Per language: de 0.919, en 0.899,
es 0.906, fr 0.925, nl 0.888. Weak labels (< 0.7 F1): SYMPTOM, PROCEDURE,
TREATMENTINFO, ALLERGIES, NATIONAL_ID.

**Limitations.** Synthetic training data, so expect lower scores on real notes.
Formats absent from training are missed (for example `BSN 123456782`, `06-12345678`).
In an end-to-end run on one short synthetic English note, the drug and condition (`lisinopril`, `hypertension`) were left unmasked while the Dutch, French, German and Spanish samples were fully masked; this is a model miss, so check English clinical text before relying on it. The platform's regex layer runs on top for the identifiers it has rules for, and the
model is not a guarantee: keep a human review step before documents leave the
organisation.

**License.** Apache-2.0. Derived from `microsoft/mdeberta-v3-base` (MIT); both notices
apply (see `NOTICE`).
