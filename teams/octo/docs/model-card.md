# Octo model card

The migrated runtime masks `application/pdf`, `text/plain`, `text/csv`, and `application/json`. Its public schema is `qa-pii-v1`: `NAME`, `GENDER`, `DATE`, `ORGANIZATION_NAME`, `ADDRESS`, `AGE`, `PHONE_NUMBER`, `BTW_EENHEID`, `RIZIV_INAMI`, `INSZ_NISS`, `URL`, and `EMAIL`. It combines the Octo XLM-R detector with checksum-gated regex rules and deterministic Belgian-Dutch pseudonymization. `DETECTION_MODE=regex` is usable without a model; model mode uses the migrated immutable artifact and maps its legacy output labels into this schema.

Training code is in [`training/`](../training/). The approved model artifact is tracked with Git LFS; no datasets are committed. QA uses the shared synthetic evaluator and records the exact model fingerprint returned by the gateway.
