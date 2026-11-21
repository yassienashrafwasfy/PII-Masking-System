# Team onboarding

1. Work only in your `teams/team-N/` boundary unless changing a reviewed shared contract.
2. Keep your model implementation and private `adapter.py` in your ownership area. Add tests, safe model configuration, and a model card. The adapter exposes `detect(text, metadata) -> list[MaskSpan]`; it does not extract files or choose replacements.
3. Import shared engine names from the package root: `from masking_engine import MaskSpan, LabelConverter`. `masking_engine.__all__` is the supported surface; the module layout below it (`masking_engine.app.*`, `masking_engine.src.*`) is platform implementation detail and may move without a contract change.
4. The platform-owned gateway implements `GET /health` and `POST /v1/mask` exactly as defined in API v1.5. The private model service uses the shared document pipeline for extraction and redaction, then returns masked bytes plus model name and immutable model version.
5. Treat routing as platform-owned. NiFi derives the trusted `team_id` from the R2 key and calls one gateway URL; your adapter must not use uploaded request metadata to select a target model, team, or endpoint.
6. Use an immutable model fingerprint in responses and MLflow evidence. QA evaluates every team against the same immutable synthetic dataset and evaluator version.
7. Add only MIME types that the shared document pipeline can extract and render safely. The current common formats are UTF-8 text and text-bearing PDFs, extracted with recursive XY-cut reading order.
8. Never commit credentials, endpoints, raw PII, production output, weights, or runtime state.
9. Before opening a merge request, run the README's local checks. Model changes also trigger change-gated CPU checks; the full GPU corpus check remains a manual pre-release gate.
