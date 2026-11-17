# ADR 005: ONNX is the only served artifact

**Status:** Accepted

Every model service in this repository loaded one of two artifacts, chosen per
request by the optional `X-Model-Format` header: the exported ONNX graph, or
the source SafeTensors checkpoint through a Transformers/torch pipeline. Only
the first was ever deployed. `onnx` has been the header's default since v1.2,
compose set `ONNX_MODEL_PATH` for all three teams, and Berts — the newest team
— shipped with `SUPPORTED_MODEL_FORMATS = frozenset({"onnx"})` and no
checkpoint at all.

The second path was therefore reachable but unused: a caller who sent
`X-Model-Format: safetensors` to Octo or Synthify got a torch pipeline that no
canary, no promotion gate and no benchmark had ever exercised in production.
That is the shape of defect worth removing rather than testing.

## What was removed

- `teams/octo/model/` and `teams/synthify/model/` — the SafeTensors weights and
  the config/tokenizer files beside them, all of which `teams/*/onnx/`
  duplicates. The source checkpoints for the two graphs live outside this
  repository, as Berts' already did.
- `TokenClassifierEngine`, the torch engine in `xlm_r/model_engine.py`.
  `OnnxTokenClassifierEngine` subclassed it, so its tokenization, windowing and
  span-merging moved into that class and it is now standalone. `resolve_device`,
  `model_checksum` and `validate_model_checksum` went with it: nothing but the
  torch load path read them.
- `scripts/export_onnx.py` and the `export` dependency group. Both existed only
  to turn a committed checkpoint into `teams/<team>/onnx/`, and there is no
  committed checkpoint to start from any more.
- `*.safetensors` from Git LFS tracking, and `safetensors` from the direct
  dependency pins.

## The identity keys

`OCTO_MODEL_SHA256` and `SYNTHIFY_MODEL_DIGEST` hashed the SafeTensors file, so
they are gone. `OCTO_MODEL_VERSION` and `SYNTHIFY_MODEL_VERSION` hashed it too,
which meant `/version` reported the identity of a file the service had never
opened; both now name the graph's own digest, derived by `migrate-env.py` from
`teams/<team>/onnx/manifest.json` exactly as the `*_ONNX_*` keys already were.

This is a breaking environment-schema change, and `preflight_production` will
refuse an environment file carried forward from before it until
`stage-model-release.sh apply … --approve` re-derives the keys — the same
failure mode, and the same remedy, as the per-language Berts release.

`source_model_sha256` stays in each ONNX manifest. It records which checkpoint
the graph was exported from, which is provenance worth keeping even though the
file is no longer here; Berts' `source_revision` plays the same role. What
changed is that no runtime value is derived from it any more.

## The contract

`X-Model-Format` and `supported_model_formats` are published surface, so the
narrowed enum is a new versioned directory rather than an edit:
`contracts/masking-api/v1.4`, copied from v1.3 the way v1.3 was copied from
v1.2. A caller that omitted the header or sent `onnx` — every caller NiFi
actually makes — is unaffected. A caller that sent `safetensors` now gets
`400`, which is the honest answer: it would previously have been served by an
engine no release had validated.
