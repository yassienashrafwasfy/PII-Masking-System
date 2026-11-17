# Unified masking gateway

This platform-owned gateway is the only masking API exposed to NiFi. It preserves [masking API v1.2](../../contracts/masking-api/v1.2/): `GET /health` and `POST /v1/mask` accept the same request bytes and return the same masked bytes and safe response headers.

NiFi derives `team_id` from the trusted R2 key `incoming/<team-id>/<request-id>/<filename>`, sends it in `X-Team-ID`, and calls one configured `MASKING_GATEWAY_BASE_URL`. The gateway accepts only `octo`, `synthify`, and `berts`; it also requires that the header matches `X-Source-Object-Key`. Unsupported or mismatched values receive a `400 Bad Request` without internal endpoint, routing, or secret information.

An authenticated request may send `X-Model-Format: onnx`; omitted
means ONNX. The gateway validates and forwards this format only to the selected
private service, then requires the private response to echo it.

Internally, the router maps each supported team to one private model service.
The Compose demo uses `octo-model`, `synthify-model`, and `berts-model`. Each service owns its
detector. The platform owns extraction, pseudonymization, and rendering. The
adapter interface is:

```python
detect(text: str, metadata: Mapping[str, str]) -> list[MaskSpan]
```

The adapter returns only canonical detection spans. The private model service adds
model name and immutable version, while the shared document pipeline applies
recursive XY-cut PDF extraction and deterministic redaction. Octo uses the
migrated Unified-repo detector and regex rules. Synthify uses its GLiNER artifact
for text and text-bearing PDF demo requests. Berts uses its supplied int8 ONNX
MedRoBERTa model for text and text-bearing PDFs.

Runtime flow: R2 source upload → NiFi derives trusted `team_id` from object path → NiFi calls one masking gateway API → gateway selects that team’s internal model adapter → gateway returns masked content and immutable model metadata → NiFi writes the result to R2 destination.

## Mutation testing

A green suite is weak evidence here: a detector that stops firing masks *less*
and still returns a well-formed document, so nothing fails. `setup.cfg` in this
directory configures [mutmut](https://mutmut.readthedocs.io) over the gateway
and the masking engine, scored against the subset of `tests/` that needs no
model weights and runs in a few seconds:

```sh
uv pip install mutmut==3.7.0          # not a project dependency
cd platform/masking-gateway
uv run python -m mutmut run           # writes mutants/ (git-ignored)
uv run python -m mutmut results       # per-mutant verdicts
uv run python -m mutmut show <mutant> # the diff one mutant applied
```

Run it from this directory, not the repository root: mutmut derives a mutant's
name from the file path relative to the working directory, and only `src/`
here matches the module path the tests import.

Two things to know before adding tests for it:

* Never clear `os.environ` wholesale in a test. mutmut selects the active
  mutant through `MUTANT_UNDER_TEST`, so `mock.patch.dict(..., clear=True)`
  makes every mutant in the code under it look uncovered. Unset the variables
  the code reads instead — `tests/test_settings.py` does.
* A handler built by a factory at import time is never attributed to a test.
  Bind arguments with `functools.partial` so the body runs per request.

Use [the multimodel deployment guide](../../docs/multimodel-deployment.md) for
the Compose demo. Do not commit live endpoints, credentials, weights, datasets,
or environment files.
