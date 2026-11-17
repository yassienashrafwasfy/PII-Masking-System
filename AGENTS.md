# AGENTS.md

## Commands (uv + Python 3.12 only, `>=3.12,<3.13`)

```sh
uv sync --locked                                   # always first; mutmut/vulture are NOT in lockfile
just test                                          # full local suite (superset of README list)
just lint      # black --check + ruff check       # just format to fix
just launch octo                                   # SERVICE_TOKEN=... docker compose up gateway + <team>-model
```

- Run a single test directly, not via bare `pytest`: `uv run python platform/masking-gateway/tests/test_gateway.py` or `uv run python teams/octo/tests/test_model_inference.py`.
- Only `evaluation/` uses pytest: `PYTHONPATH=evaluation uv run python -m pytest -q evaluation/tests`.
- Never run bare `black`/`ruff` on the repo: both exclude `teams/berts/training/tamerbert_generalization_trial.py` (see `justfile`); `ruff.toml` per-file-ignores `E402` for gateway/model-service tests (sys.path bootstrap before imports is intentional).
- Pre-commit runs `repo: local` hooks against pinned uv binaries; verify with `uv run pre-commit run --all-files --show-diff-on-failure`. It excludes `graphify-out/`, LFS binaries (`*.onnx/bin/model`, `tokenizer.json`), and the berts trial file. Secret scanning is Trivy in CI (`ci/security.yml`), not a pre-commit hook.
- `pytest` `norecursedirs` ignores `mutants/` (see `pyproject.toml`): after a mutation run, plain root `pytest` collects garbage. Mutation testing: `uv pip install mutmut==3.7.0` then `cd platform/masking-gateway && uv run python -m mutmut run`. Dead code: `uv pip install vulture` then `uv run python -m vulture` from root (config in `[tool.vulture]`, `teams/*/training` out of scope).
- Local Compose demo: `cp .env.example .env` (long random `SERVICE_TOKEN`; R2 audit keys optional, blank = no audit object), `docker compose --profile octo up --detach --no-build`. Full ports/headers/idle-unload: `docs/multimodel-deployment.md`.

## Architecture (read `docs/repository-guide.md`, `docs/architecture.md`, `docs/team-onboarding.md` before structural changes)

- One public gateway (`platform/masking-gateway/src/gateway.py`, port 8080, masking API v1.2–v1.4) + private per-team model services (`platform/model-service/src/service.py`, `TEAM_ID` env, `importlib`-loads `teams/<team>/adapter.py`). Only the gateway has a published port; model URLs (`<TEAM>_MODEL_URL`) resolve server-side only.
- Flow: R2 `incoming/<team>/<request-id>/<filename>` → NiFi derives trusted `team_id` from key → `POST /v1/mask` with `X-Team-ID` → gateway validates team + verifies `X-Source-Object-Key` prefix → proxies to team service (shared pipeline + team detector) → masked bytes + safe metadata headers → NiFi writes `masked/team=<team>/model=<model>/version=<version>/...`.
- Ownership (enforced by `scripts/validate_repository.py` + `tests/test_repository_layout.py`): `contracts/` = versioned source of truth (add a version dir, never edit incompatibly); `platform/` = MLOps-owned; `teams/{octo,synthify,berts}/` = team-owned adapter + model/samples/training/docs, no HTTP; `evaluation/` = immutable scorer + fixtures + arena service; `ci/` = GitLab fragments; `deploy/` = VPS surface; root `tests/` = cross-cutting repo/deploy tests.
- `masking_engine` public API is its root: `from masking_engine import MaskSpan, MaskingRouter, prepare_document` (see `__all__`). Only reach into `masking_engine.app.*` for a helper a test pins deliberately. Regex: add rules as `Rule` rows in the pattern-table modules, never a new `finditer` loop; `find_regex_spans` composes all country/domain detectors, `regex_arbitration` has the last word (checksummed ID > strict format > loose shape; longer span wins in-class). Detectors stay independent and may overlap — never resolve conflicts inside a detector.
- Team adapter contract only: `detect(text, metadata) -> list[MaskSpan]`, `ready/release/supported_media_types/supported_model_formats/unload`. Octo (XLM-R+regex: PDF/text/CSV/JSON, demo-ready), Synthify (GLiNER: PDF/text, demo-only), Berts (int8 ONNX MedRoBERTa+regex, PDF/text, production-wired). Octo/Synthify = float32 CPU ORT + SafeTensors fallback (`ACCELERATOR=cuda` applies to SafeTensors only); Berts = int8 ONNX only. Per-request format via `X-Model-Format: onnx|safetensors` (default `onnx`), gateway rejects unsupported.
- Document pipeline is shared (`pdf_extraction.py` XY-cut reading order → team detection → pseudonymize → render back). Image-bearing PDFs refused. `PDF_OCR=on` is per-page fallback only: a page with zero characters gets PP-OCRv6 + invisible text layer + `PDF_REDACT_IMAGE_PIXELS`; pages with any text layer are never OCR'd. Detection is locale-blind (all countries' rules run); locale binds replacement only via optional `X-Document-Locale` validated against `SUPPORTED_LOCALES` (`app/core/locales.py`). Check `docs/decisions/002-*` and `004-*` before touching this. Endpoint reference: `docs/current-endpoints.md`.

## Trust invariants (do not weaken without review)

- `team_id` trusted only from R2 key prefix + gateway `X-Source-Object-Key` check. Never select team/model/URL from body, filename, or other headers; never echo model URLs to callers.
- `SERVICE_TOKEN` bearer on `POST /v1/mask`, compared with `hmac.compare_digest`.
- Synthetic fixtures only — never commit real PII, credentials, endpoints, NiFi state, or runtime output. Only exceptions: Git-LFS model artifacts and approved synthetic samples. Use `*.example` placeholders.

## CI (`.gitlab-ci.yml` + `ci/`, self-hosted `vps-ci` runner)

- MRs run `build → validate → evaluate` (ML changes only) `→ package → security → qa/release`; full-corpus GPU gate is manual. Keep `GIT_LFS_SKIP_SMUDGE`, `vps-ci` tag, consolidated `fast_checks`, change-based package rules, and the duplicate-pipeline workflow guard — pipeline-minute budget is tight.
- Two change lanes: platform (`platform/**`, `teams/**/*.py`, `teams/*/onnx/**`, most of `deploy/`) vs arena (`evaluation/leaderboard*`, `evaluation/samples`, `deploy/mlflow-vps`). Docs/contracts-only changes still get build+validate (no `changes:` filter).
- Default-branch pipelines never rebuild/republish/rescan — only candidate load + two blocking manual jobs (preflight, then blue/green promotion). Production flow: `docs/ci-production-promotion.md`, `deploy/README.md`; runner setup: `docs/vps-gitlab-runner.md`.

## Graphify workflow

A Graphify knowledge graph is available in `graphify-out/`.

For repository-wide architecture questions, dependency tracing, call-flow
analysis, impact analysis, blast-radius analysis, or unfamiliar-code
exploration:

1. Use `graphify query "<focused question>"` before broad grep, find, or
   repository-wide file scans.
2. Use `graphify explain "<symbol>"` to inspect a symbol and its relationships.
3. Use `graphify path "<source>" "<target>"` to trace connections.
4. Read `graphify-out/GRAPH_REPORT.md` for high-level repository orientation.
5. Open only the source files identified by the graph when practical.
6. Verify graph-derived conclusions against source code before editing.
7. Rebuild or refresh the graph after meaningful structural code changes.

For small, localized edits in a known file, direct file inspection is allowed.
Do not use Graphify when it would add more overhead than reading the relevant
file directly.
