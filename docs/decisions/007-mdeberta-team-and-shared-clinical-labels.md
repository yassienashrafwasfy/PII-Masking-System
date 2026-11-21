# ADR 007: The mDeBERTa team, and one name per clinical concept

**Status:** Accepted. Supersedes the PROCEDURE and LAB_RESULT exclusion in
[ADR 006](006-phi-routing-only-labels.md).

## A fourth team, served like the other three

`masterofaudio2077/medical-pii-ner-mdeberta-v3` (Apache-2.0, fine-tuned from
`microsoft/mdeberta-v3-base`, MIT) is a 36-label multilingual (nl, fr, de, es, en)
PII and clinical token classifier. It joins as the team `mdeberta`: a private
model service behind the one gateway, selected with `X-Team-ID`, with no API of
its own. The contract moves to `contracts/masking-api/v1.5/` only because the
`TeamId` enum gains a value; v1.4 is untouched.

The checkpoint is fp16 safetensors. ONNX-only serving ([ADR 005](005-onnx-only-serving.md))
means it is converted once and the graph is what ships. ADR 005 removed the
repository's export tooling for the existing teams; this team keeps its exporter
at `teams/mdeberta/training/export_onnx.py` because the checkpoint lives on the
hub, not here, and a graph nobody can re-derive is not auditable. It is team
code, outside the locked environment, and is run with its packages added for the
one run. The exported graph matched torch to a worst-case 1.7e-4 logit difference
with identical argmax on every token (batch 1 and 5, sequences to 1536).

## One name per clinical concept

This checkpoint emits `ALLERGIES`, `PRESCRIPTIONINFO`, `TREATMENTINFO`,
`TESTRESULTS`, `PROCEDURE` beside `DIAGNOSIS`, `MEDICATION` and `SYMPTOM`. Octo and
Berts already emit `PROCEDURE`, and Octo emits `LAB_RESULT`; ADR 006 dropped both
"deliberately, not overlooked". Three teams speaking about one concept in three
spellings is the situation ADR 006 solved for `DISEASE`, so the same rule applies:

- `DIAGNOSIS`, `MEDICATION`, `SYMPTOM` keep their names (nothing changes).
- `PROCEDURE` and `TESTRESULTS` become routing-only labels. `octo` aliases its
  `LAB_RESULT` to `TESTRESULTS`. **Octo and Berts therefore start masking
  procedures and lab results**, which they did not before; this is the intended
  effect of "same concept, same name", and it errs toward masking more.
- `ALLERGIES`, `PRESCRIPTIONINFO`, `TREATMENTINFO` are routing-only and emitted
  by `mdeberta` only.

Identifiers the checkpoint emits that had no label: `BSN`, `KVNR`, `IBAN`,
`MEDICALRECORDNUM`, `NATIONAL_ID`. Its `NATIONAL_ID` is a merge of Steuer-ID and
SSN, so the profile keeps it as its own label rather than taking the global
`NATIONAL_ID -> INSZ_NISS` alias, which would replace a German or US number with a
Belgian-shaped one. `BTW`, `INSZ`, `RIZIV`, `PHONE`, `ORGANIZATION` alias onto the
existing canonical labels; `STREET`, `ZIPCODE`, `CITY` merge into one `ADDRESS`.

All of these are **routing-only, not `entity_labels`**. Adding them to the scored
taxonomy would change `qa-pii-v1` for every team and make the arena score classes
only one team can emit. They are masked by default and selectable through
`X-Mask-Labels` like the other PHI.

## Consequences

- The five clinical free-text labels and `NATIONAL_ID`, `BSN`, `KVNR`, `IBAN`,
  `MEDICALRECORDNUM` have no surrogate vocabulary; the engine's existing fallback
  redraws their characters at the same length. Nothing leaks, but the output is
  visibly scrambled text, not plausible text.
- This checkpoint's own card reports weak F1 for `SYMPTOM`, `PROCEDURE`,
  `TREATMENTINFO`, `ALLERGIES` and `NATIONAL_ID` (< 0.7) and misses identifiers in
  layouts it was not trained on (for example `BSN 123456782`). The platform regex
  layer still runs on top of the model for the identifiers it has rules for.
- Production (`deploy/`) is not wired for this team. Enabling it there is a
  separate, reviewed step: it needs the staged artifact, the pinned digest and
  capacity approval.
