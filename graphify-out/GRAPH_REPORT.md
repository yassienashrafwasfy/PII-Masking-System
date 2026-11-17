# Graph Report - full-pii-data-masking  (2026-09-10)

## Corpus Check
- 380 files · ~3,178,751 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 5056 nodes · 9589 edges · 316 communities (254 shown, 62 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 786 edges (avg confidence: 0.78)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `889867c9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- per-file-result.schema.json
- properties
- recall
- completion-manifest.schema.json
- required
- properties
- metrics
- properties
- migrate-env.py
- generate_synthetic.py
- evaluator/__init__.py
- tamerbert_generalization_trial.py
- results.py
- objectstore.py
- library.py
- RunRegistry
- ranking.py
- qa_result
- runs.py
- Settings
- leaderboard_api/schemas.py
- evaluation/tests/conftest.py
- test_arena_api.py
- test_arena_labels.py
- test_arena_library.py
- test_arena_ranking.py
- test_arena_runs.py
- test_arena_schemas.py
- http.py
- _mask_response
- gateway.py
- masking_engine/__init__.py
- core/config.py
- label_registry.py
- label_converter.py
- masking_router.py
- MaskingRouter
- MaskSpan
- regex_contact.py
- test_date_spans.py
- regex_identifiers.py
- test_regex_rules.py
- span_shape.py
- parse_date
- surrogate_numbers.py
- _SeamReader
- object_seams
- pdf_content_order.py
- Fragment
- extract_pdf_pages
- pdf_extraction.py
- pdf_masking_service.py
- _font_name
- _redraw_moved_glyphs
- PdfGlyph
- _draw_replacements
- pdf_ocr.py
- pdf_redaction_styles.py
- Fit
- models/__init__.py
- ctc_decode.py
- db_postprocess.py
- ocr_engine.py
- ._recognise
- preprocess.py
- xlm_r/__init__.py
- OnnxTokenClassifierEngine
- _ancestor_holding
- pdf_corpus.py
- test_compose_smoke.py
- test_converter_samples.py
- test_gateway.py
- test_ocr_engine.py
- test_pdf_content_order.py
- pdf_bytes
- test_the_audit_records_which_spans_came_from_ocr
- test_pdf_ocr.py
- test_pdf_redaction_corpus.py
- test_pdf_redaction_styles.py
- drawn
- test_pdf_unmaskable_channels.py
- test_qa_api_end_to_end.py
- test_settings.py
- service.py
- normalized_name
- export_paddle_ocr_onnx.py
- berts/runtime.py
- octo/training/src/__init__.py
- prepare_datasets.py
- train_token_classifier.py
- Path
- DataFrame
- train_one
- ConsoleProgressCallback
- train_token_classifier_test.py
- label_support
- split_by_verdict_support
- log_alignment_check
- dev_scores
- synthify/runtime.py
- synthify/onnx/manifest.json
- synthify/training/src/__init__.py
- convert_record
- train_gliner.py
- CreateEnvScriptTests
- test_ci_production_promotion.py
- DeploymentScriptTests
- PreflightFixture
- PII Masking API Contract v1.3
- 3. Mask request
- PII Masking API Contract v1.4
- 3. Mask request
- mlflow-vps/README.md
- Arena on the MLflow VPS
- Executive overview
- Technical overview
- CI production promotion
- Per-Document Locale Resolution
- ADR 003: OCR for scanned and image-bearing PDFs
- ADR 004: One English locale, and the identifiers that come with it
- ADR 005: ONNX is the only served artifact
- Masking a subset of a record
- Console sample documents
- Synthetic-only MLflow evidence
- Terminal manifest policy
- NiFi–R2–gateway–MLflow architecture decision
- Repository ownership boundaries
- Secrets and logging controls
- Evaluation CI jobs
- Synthetic benchmark dataset
- Cloudflare R2 boundary
- MLflow platform boundary
- Octo model card
- GLiNER Training Pipeline
- Contributor Validation Requirements
- PII Masking Model Arena
- Synthetic Clinical Intake Fixture
- Octo Training Dependencies
- Bug Report Template
- Feature Acceptance Criteria
- Default Merge Request Template
- Graph-First Repository Exploration
- NiFi Flow Exports Policy
- Team Adapter Registry
- Byte-Pair Encoding Merge Vocabulary
- TamerBERT Generalization Trial Script
- Synthify Model Example Configuration
- vps-common.sh
- create-env.sh
- deploy-arena.sh
- pii-masking-model-stage
- pii-masking-nginx-control
- promote-staged-bundle.sh
- prune-model-releases.sh
- verify-gateway.sh
- vps-docker-gc.sh
- package.json
- App.jsx
- Arena.jsx
- format.js
- getTeamDisplayName
- Console.jsx
- PerLabel.jsx
- useIntro
- theatrics.jsx
- load_mock_results
- test_arena_per_label.py
- gunicorn.conf.py
- error
- test_locale_detection.py
- locales.py
- regex_engine.py
- test_checksums.py
- test_contact_spans.py
- test_phone_spans.py
- _is_national_identifier
- surrogates.py
- test_surrogates.py
- prepare_document
- pdf_block_reflow.py
- plan
- _Token
- _tokens
- test_pdf_content_stream.py
- _rewrite_object_in_place
- _outer_splice
- page_metrics
- frag
- test_pdf_layout.py
- find_running_lines
- pdf_line_reflow.py
- _render_styled_run
- _detect_and_redact
- masking_engine/runtime.py
- test_address_spans.py
- _rows
- test_pdf_line_reflow.py
- _scan_bytes
- test_the_separator_survives_the_word_it_followed_being_redacted
- test_pdf_text_fitting.py
- test_a_single_glyph_has_no_gap_to_take_anything_out_of
- test_the_levers_apply_strictly_in_order_as_the_slot_tightens
- test_qa_label_schema.py
- test_shared_adapter.py
- test_span_shape.py
- test_service.py
- LaneSelectionTests
- pii-masking-demo
- check_graphify_hygiene.py
- onnx-en/manifest.json
- onnx-fr/manifest.json
- berts/onnx/manifest.json
- octo/onnx/manifest.json
- regex_detectors.py
- test_regex_detectors.py
- test_runtime_validation.py
- test_windowing.py
- ArenaCiWiringTests
- ArenaComposeTests
- ArenaPreflightTests
- load_ci
- load_merged_config
- select_jobs
- QaTriggerVariablePolicyTests
- CandidateImageAliasTests
- CandidateManifestRepublishTests
- LaneRuleLockstepTests
- test_repository_layout.py
- Pipeline stages and include list
- .tag_release_rules template
- workflow: rules (duplicate-pipeline guard)
- ruff local hook
- black local hook
- check-merge-conflict hook
- detect-private-key hook
- end-of-file-fixer hook
- trailing-whitespace-fixer hook
- graphify-hygiene hook
- Unified masking gateway
- container image variable definitions
- qa-automation downstream project (internshipjuly2026/qa-automation)
- gateway service
- PII Masking API Contract v1.2
- Private NiFi VPS deployment README
- x-model-toggles shared env anchor
- Automated Document Masking Delivery
- Growth Trajectory Roadmap
- Dual-Layer Detection Engine
- Twelve Canonical Entity Categories
- Extensible Architecture and Roadmap
- Deterministic Masked Output
- Throughput and Capacity (10 MB cap, per-team serialization)
- Single Interface Integration Cost
- Masking Gateway API
- contracts/masking-api/v1.4
- Platform CI Lane
- deploy_production Job
- Masking Gateway
- Team-Owned Adapters
- fast_checks Job
- POST /v1/mask (Gateway)
- evaluation/leaderboard_api/
- Run Discovery via Terminal Manifest
- NIR (French National ID) Canonical Label
- en Locale (Single English Locale)
- lingua-language-detector
- scripts/build_french_organization_pool.py
- FINESS Health Register
- Amendment 2026-09-04: Production Enables OCR by Default
- app/services/pdf_extraction.py
- apply_redactions(images=, graphics=, text=)
- Connected-Components Line Labeling (no OpenCV/pyclipper)
- inference.yml Config Assertion at Export
- PDF_OCR_MIN_LINE_CONFIDENCE (default 0.0)
- SSN/NPI/NHS_NUMBER Router-Generated Surrogates
- scripts/build_english_medical_organization_pool.py
- _building_number_spans / carries_postcode
- ADR 005: ONNX-Only Serving
- OCTO_MODEL_VERSION / SYNTHIFY_MODEL_VERSION (redefined)
- MAX_CONCURRENT_REQUESTS_PER_TEAM=1
- Markdown Table Extraction Disabled
- NiFi Idempotency-Key Deduplication
- Gateway Routing to Team Model Services
- PDF_REDACTION_STYLE (invisible/clinical_soft/bold_chips)
- Production Handoff Requirements
- Per-Model Language Routing (Berts)
- Local Deployment via docker compose
- MODEL_IDLE_TIMEOUT_SECONDS
- InvokeHTTP Processor Configuration
- data Bucket (Validation_Datasets, results/)
- logs Bucket (audit)
- Earlier Wrong Layout Documentation, Corrected
- Allowed MIME Type Extension Rule
- Pre-MR Local Checks / Change-Gated CPU Checks
- vps-ci Runner Tag
- Rootless BuildKit Image Builds
- vps-docker-gc Systemd Timer
- leaderboard_api/ arena API
- leaderboard-api service
- pii-masking-demo.postman_collection.json
- Locale-specific organization surrogate replacement pool
- Berts Model Card
- Synthify Model Card
- qa-pii-v1 label schema
- en-GB Ophthalmology Clinical Letter (synthetic)
- Dissolving-Tile Brand Mark

## God Nodes (most connected - your core abstractions)
1. `MaskSpan` - 185 edges
2. `prepare_document()` - 69 edges
3. `MaskingRouter` - 60 edges
4. `redact_document()` - 53 edges
5. `DeploymentScriptTests` - 49 edges
6. `load_board()` - 46 edges
7. `cross_team_ranking()` - 44 edges
8. `_bytes()` - 43 edges
9. `_doc()` - 41 edges
10. `train_one()` - 41 edges

## Surprising Connections (you probably didn't know these)
- `Synthetic QA Evaluation Evidence` --conceptually_related_to--> `Synthetic Clinical Intake Fixture`  [INFERRED]
  platform/r2/access-model.md → evaluation/datasets/generated/public/documents/synthetic-intake-008.pdf
- `X-Model-Format header (onnx/safetensors)` --semantically_similar_to--> `berts-model service`  [INFERRED] [semantically similar]
  contracts/masking-api/v1.2/PII_Masking_API_Contract_v1.2.md → deploy/compose.yml
- `X-Document-Locale Header` --semantically_similar_to--> `x-model-toggles shared env anchor`  [INFERRED] [semantically similar]
  docs/current-endpoints.md → deploy/compose.yml
- `Pipeline budget guidance` --semantically_similar_to--> `default: vps-ci runner config`  [INFERRED] [semantically similar]
  README.md → .gitlab-ci.yml
- `Masking gateway runtime deps (gunicorn, packaging)` --shares_data_with--> `Unified masking gateway`  [INFERRED]
  platform/masking-gateway/requirements-runtime.txt → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Production candidate build-scan-QA-record-load promotion flow** — ci_container_publish_gateway, ci_container_publish_model_service, ci_security_scan_gateway_image, ci_security_scan_model_service_image, ci_qa_qa_release_gate, ci_release_record_production_candidate, ci_release_load_production_candidate [EXTRACTED 1.00]
- **Arena image publish, MLflow-VPS deploy, and downstream evaluation flow** — ci_container_publish_arena_api, ci_container_publish_arena_ui, ci_deploy_preflight_arena_mlflow, ci_deploy_deploy_arena_mlflow, ci_qa_qa_arena_evaluation [EXTRACTED 1.00]
- **Two-lane changes: path-filtering pattern separating platform and arena pipelines** — _gitlab_ci_platform_paths, _gitlab_ci_arena_paths, _gitlab_ci_workflow_rules, _gitlab_ci_release_candidate_rules, _gitlab_ci_arena_image_rules [INFERRED 0.85]
- **Masking API contract version evolution v1.2 -> v1.3 -> v1.4** — contracts_masking_api_v1_2_pii_masking_api_contract_v1_2, contracts_masking_api_v1_3_pii_masking_api_contract_v1_3, contracts_masking_api_v1_4_pii_masking_api_contract_v1_4 [INFERRED 0.90]
- **Berts per-language checkpoint staging and routing** — deploy_readme_berts_model_staging, deploy_readme_berts_language_routing, deploy_compose_berts_model_service [EXTRACTED 0.90]
- **Arena (leaderboard + masking console) deployment topology on the MLflow VPS** — deploy_mlflow_vps_readme, deploy_mlflow_vps_arena_compose_leaderboard_api_service, deploy_mlflow_vps_arena_compose_arena_ui_service, deploy_mlflow_vps_readme_traefik_routing [EXTRACTED 0.90]
- **Production Candidate Promotion Pipeline** — docs_ci_production_promotion_record_production_candidate, docs_ci_production_promotion_load_production_candidate, docs_ci_production_promotion_preflight_production, docs_ci_production_promotion_deploy_production [EXTRACTED 0.90]
- **Per-Document Locale Detection Drives Both Surrogate Generation and Berts Model Routing** — docs_multimodel_deployment_document_language_resolution, docs_multimodel_deployment_per_model_language_routing, docs_multimodel_deployment_x_document_locale_header, docs_multimodel_deployment_berts_checkpoint_table [EXTRACTED 0.90]
- **OCR Artifact Staging and Digest Verification Pipeline** — docs_decisions_003_ocr_fallback_for_scanned_pdfs_amendment_2026_09_04, docs_decisions_003_ocr_fallback_for_scanned_pdfs_fetch_ocr_artifacts, docs_multimodel_deployment_ocr_model_dir, docs_multimodel_deployment_deploy_gateway_sh [INFERRED 0.85]
- **Cross-team ranking pipeline** — evaluation_readme_leaderboard_api, evaluation_readme_ranking_py, evaluation_readme_rank_teams_py, evaluation_readme_cross_team_ranking_document, evaluation_readme_history_py [INFERRED 0.85]
- **R2-to-masked-output pipeline architecture** — platform_nifi_readme_boundary, contracts_masking_api_v1_2_contract, evaluation_readme_masking_console [INFERRED 0.85]
- **Arena sample document configuration** — evaluation_docker_compose_leaderboard_api_service, evaluation_samples_readme_console_samples, evaluation_readme_masking_console [INFERRED 0.75]
- **qa-pii-v1 Label Schema Unification Across Teams** — teams_berts_docs_model_card_qa_pii_v1, teams_octo_readme_qa_pii_v1, teams_synthify_docs_model_card_qa_pii_v1 [INFERRED 0.85]
- **CPU-only ONNX Serving Policy Across Teams** — teams_berts_readme_onnx_cpu_runtime, teams_octo_readme_onnx_runtime, teams_synthify_readme_onnx_runtime [INFERRED 0.80]
- **Regex+Model Merge: Wider Span Wins Pattern** — teams_berts_readme_age_date_phone_merge_policy, teams_octo_readme_regex_mode, teams_synthify_docs_model_card_regex_supplement [INFERRED 0.75]
- **Cross-locale Parallel Gastroenterology Clinical Letters (en-GB/fr-FR/nl-BE)** — evaluation_samples_en_gb_gastro_clinical_letter_document, evaluation_samples_fr_fr_gastro_clinical_letter_document, evaluation_samples_nl_be_gastro_clinical_letter_document [INFERRED 0.85]
- **Cross-locale Parallel Ophthalmology Clinical Letters (en-GB/fr-FR/nl-BE)** — evaluation_samples_en_gb_ophthalmology_clinical_letter_document, evaluation_samples_fr_fr_oftalmologie_clinical_letter_document, evaluation_samples_nl_be_oftalmologie_clinical_letter_document [INFERRED 0.85]
- **Cross-locale Parallel Respiratory Medicine Clinical Letters (en-GB/fr-FR/nl-BE)** — evaluation_samples_en_gb_respiratory_clinical_letter_document, evaluation_samples_fr_fr_pneumologie_clinical_letter_document, evaluation_samples_nl_be_pneumologie_clinical_letter_document [INFERRED 0.85]
- **Evaluation Leaderboard Browser/Home-Screen Icon Asset Set** — evaluation_leaderboard_public_apple_touch_icon, evaluation_leaderboard_public_favicon_32, evaluation_leaderboard_public_favicon_48, evaluation_leaderboard_public_apple_touch_icon_dissolving_tile_mark [INFERRED 0.85]
- **Leaderboard brand-mark rendering (corporate chip, per-team mark, champion fallback)** — evaluation_leaderboard_src_assets_cegedim_logo, evaluation_leaderboard_src_assets_octo_logo, evaluation_leaderboard_src_assets_cegedim_logo_white_knockout_chip, evaluation_leaderboard_src_assets_octo_logo_champion_generic_mark [INFERRED 0.85]
- **Team Adapter Registry Flow** — platform_masking_gateway_config_gateway_example_adapter_registry, platform_masking_gateway_readme_adapter_interface, teams_berts_readme_berts, teams_octo_readme_octo, teams_synthify_readme_synthify [INFERRED 0.95]
- **Synthetic Clinical Intake Fixture Corpus** — evaluation_datasets_generated_public_documents_synthetic_intake_008_synthetic_intake_008, evaluation_datasets_generated_public_documents_synthetic_intake_009_synthetic_intake_009, evaluation_datasets_generated_public_documents_synthetic_intake_010_synthetic_intake_010, evaluation_datasets_generated_public_documents_synthetic_intake_011_synthetic_intake_011, evaluation_datasets_generated_public_documents_synthetic_intake_012_synthetic_intake_012 [EXTRACTED 1.00]

## Communities (316 total, 62 thin omitted)

### Community 156 - "per-file-result.schema.json"
Cohesion: 0.18
Nodes (10): $schema, title, type, additionalProperties, required, fixture_id, model_version, precision (+2 more)

### Community 121 - "properties"
Cohesion: 0.13
Nodes (15): properties, fixture_id, type, model_version, type, precision, type, minimum (+7 more)

### Community 222 - "recall"
Cohesion: 0.50
Nodes (4): recall, type, minimum, maximum

### Community 210 - "completion-manifest.schema.json"
Cohesion: 0.40
Nodes (4): $schema, title, type, additionalProperties

### Community 161 - "required"
Cohesion: 0.14
Nodes (14): required, status, masked_object_key, completed_at, $schema, title, type, additionalProperties (+6 more)

### Community 122 - "properties"
Cohesion: 0.13
Nodes (15): properties, status, const, correlation_id, type, minLength, source_object_key, type (+7 more)

### Community 223 - "metrics"
Cohesion: 0.50
Nodes (4): metrics, type, additionalProperties, type

### Community 104 - "properties"
Cohesion: 0.12
Nodes (17): properties, status, const, correlation_id, type, minLength, source_object_key, type (+9 more)

### Community 142 - "migrate-env.py"
Cohesion: 0.35
Nodes (11): Path, Namespace, read_env(), secure_input(), model_identity_values(), rewrite_env(), write_private(), update_model_identity() (+3 more)

### Community 133 - "generate_synthetic.py"
Cohesion: 0.30
Nodes (13): Random, Path, _nhs_number(), _npi(), make_faker_record(), make_record(), write_pdf(), build_split() (+5 more)

### Community 7 - "tamerbert_generalization_trial.py"
Cohesion: 0.05
Nodes (44): _tokens(), Counter, evaluate_text(), clamp(), normalized_inverse(), normalized_direct(), leaderboard_score(), test_masking_metrics_detect_leak_and_preserve_safe_text() (+36 more)

### Community 20 - "results.py"
Cohesion: 0.07
Nodes (44): Read-only leaderboard API., RuntimeError, Metric, Environment-driven settings for the arena API. The arena reads QA result…, _flat(), _first(), _to_frontend_row(), _champion() (+36 more)

### Community 55 - "objectstore.py"
Cohesion: 0.12
Nodes (27): Any, Settings, RuntimeError, Any, Settings, content_fingerprint(), unwrap(), archive_key() (+19 more)

### Community 106 - "library.py"
Cohesion: 0.18
Nodes (16): Any, datetime, Settings, _parse_time(), _content_type_for(), _team_from_source_key(), entry_from_manifest(), _cached_entry() (+8 more)

### Community 52 - "RunRegistry"
Cohesion: 0.09
Nodes (28): post, UploadFile, _clean(), _int(), _bool(), load_settings(), resolve(), Find a run by id, falling back to storage when the process forgot it. This is… (+20 more)

### Community 3 - "ranking.py"
Cohesion: 0.05
Nodes (54): Any, Settings, field_validator, Any, parametrize, _envelope(), RankingDocument, _latency_reason() (+46 more)

### Community 57 - "qa_result"
Cohesion: 0.14
Nodes (27): Any, Settings, fixture, parse_document(), load_results(), Map one QA result document onto a leaderboard row., Load and rank every QA result document under the configured prefix., qa_result() (+19 more)

### Community 48 - "runs.py"
Cohesion: 0.12
Nodes (25): ValueError, Settings, datetime, Pattern, RunError, Run, _safe_filename(), build_source_key() (+17 more)

### Community 42 - "Settings"
Cohesion: 0.11
Nodes (30): ValueError, Settings, Path, fixture, Settings, parametrize, Settings, True when object storage can be reached at all. (+22 more)

### Community 138 - "leaderboard_api/schemas.py"
Cohesion: 0.22
Nodes (11): BaseModel, LabelEntry, LabelTaxonomy, LibraryConfig, RankingConfig, PathsConfig, ConfigResponse, The shapes this API declares, as pydantic models. Two kinds of shape live here.… (+3 more)

### Community 43 - "evaluation/tests/conftest.py"
Cohesion: 0.08
Nodes (23): fixture, datetime, reset_cache(), Drop every cached index and manifest. Used by tests., reset_board_cache(), Drop every cached board. Used by tests, which must not see each other's., RegistrySlot, registry() (+15 more)

### Community 8 - "test_arena_api.py"
Cohesion: 0.04
Nodes (41): fixture, storage(), test_leaderboard_serves_results_and_medals_from_storage(), test_tied_teams_both_get_the_badge(), test_leaderboard_serves_the_cross_team_ranking_document(), test_overall_champion_follows_qa_rank_not_a_second_tie_computation(), test_ranking_document_wins_over_per_run_qa_documents(), test_a_missing_mlflow_package_degrades_to_demo_data() (+33 more)

### Community 56 - "test_arena_labels.py"
Cohesion: 0.09
Nodes (26): fixture, parametrize, normalize(), display_name(), describe(), taxonomy_payload(), The canonical PII entity taxonomy, as the arena needs to display it. The source…, Resolve one reported label to `(canonical, aliased_from, is_canonical)`. An… (+18 more)

### Community 36 - "test_arena_library.py"
Cohesion: 0.10
Nodes (34): fixture, parametrize, Index, build_index(), load_index(), One rebuilt view of the library, newest first., List the manifest prefix once and rebuild every run it describes. One scan from…, `build_index` behind a TTL, rebuilt by one caller at a time. The single-flight… (+26 more)

### Community 6 - "test_arena_ranking.py"
Cohesion: 0.07
Nodes (61): fixture, load_board(), Build the board from the newest applicable ranking document, or None. Returning…, cross_team_ranking(), team_report(), Build a document shaped like qa-automation's scripts/rank_teams.py output. The…, Build a document shaped like pipeline.spec.ts's per-team report. Only the…, settings() (+53 more)

### Community 34 - "test_arena_runs.py"
Cohesion: 0.11
Nodes (36): fixture, refresh(), Advance a run by inspecting object storage. Safe to call repeatedly., settings(), submit(), test_upload_lands_under_the_team_scoped_incoming_prefix(), test_unsupported_team_is_refused_before_any_upload(), test_unsupported_media_type_is_refused() (+28 more)

### Community 107 - "test_arena_schemas.py"
Cohesion: 0.12
Nodes (15): parametrize, config(), Tell the frontend which features are actually usable in this deployment., test_metric_reads_the_envelopes_documents_actually_carry(), test_a_numeric_string_is_not_a_number(), test_number_is_the_value_only_when_the_metric_is_available(), test_unknown_keys_are_ignored_rather_than_rejected(), test_payload_is_the_wire_shape_in_the_wire_order() (+7 more)

### Community 100 - "http.py"
Cohesion: 0.14
Nodes (17): ValueError, Any, media_type(), mask_labels(), model_format(), document_locale(), PayloadTooLargeError, read_body() (+9 more)

### Community 128 - "_mask_response"
Cohesion: 0.13
Nodes (14): Any, WSGI entry point for the gateway container., _load_core_module(), _health(), _upstream_response_is_usable(), _response_headers(), _mask_response(), application() (+6 more)

### Community 27 - "gateway.py"
Cohesion: 0.09
Nodes (38): Exception, _T, request_id(), Header value, truncated, with header-injection characters rejected., ContractError, _request_id(), _token_is_valid(), _upstream_url() (+30 more)

### Community 4 - "masking_engine/__init__.py"
Cohesion: 0.06
Nodes (47): Any, JSONResponse, Exception, mask_json(), Mask character offsets in valid UTF-8 JSON while preserving its structure., Path, Octo's public gateway adapter., __getattr__() (+39 more)

### Community 23 - "core/config.py"
Cohesion: 0.05
Nodes (45): Reader, Any, Settings, _text(), _ratio(), _entity_confidence_thresholds(), _choice(), _one_of() (+37 more)

### Community 109 - "label_registry.py"
Cohesion: 0.19
Nodes (16): taxonomy(), profiles(), profile_names(), masked_labels(), normalize(), canonical_aliases(), _canonical_by_key(), canonical_label() (+8 more)

### Community 135 - "label_converter.py"
Cohesion: 0.19
Nodes (9): UnknownLabelError, ValueError, LabelConverter, Translate a foreign detector's labels into the canonical taxonomy. Three teams…, Raised in strict mode when no profile entry covers a label., Map one detector's labels onto the canonical taxonomy., Return the canonical label for *label* under this profile., Return the canonical spans that replace *span*. (+1 more)

### Community 99 - "masking_router.py"
Cohesion: 0.12
Nodes (15): Faker, Path, _faker_for(), Replace each detected span with a pseudonym of its own kind. Detection finds a…, Return this thread's generator for *faker_locale*, built once. Thread-local…, _load_vocabularies(), _load_organization_pool(), Load the vocabularies and organisation-name pools a surrogate draws from. (+7 more)

### Community 0 - "MaskingRouter"
Cohesion: 0.04
Nodes (74): Random, MaskingRouter, Turn detected spans into their replacements for one document., Record the final replacement applied to one source span., Return the replacement for *span*, whose text is *original*., Return `_pseudonymize`'s result, reused for a repeated value. One document…, Return the value that decides the surrogate for *original*. Two mentions of one…, Return a generator bound to this document, label, and value. (+66 more)

### Community 16 - "MaskSpan"
Cohesion: 0.09
Nodes (40): _MODE, ShapeRule, MaskSpan, Identify one contiguous PII or PHI span. ``page=None`` means that offsets refer…, _position(), _Claims, Orchestrator, Combine model and regex spans according to label ownership. ``model`` mode… (+32 more)

### Community 37 - "regex_contact.py"
Cohesion: 0.10
Nodes (33): NamedTuple, _has_phone_context(), _Prefix, _read_prefix(), _national_length_ok(), _Shape, _PhoneCandidate, _not_a_decimal() (+25 more)

### Community 80 - "test_date_spans.py"
Cohesion: 0.16
Nodes (21): Match, _is_real_date(), _valid_date(), _textual_date_ok(), _date_spans(), Say whether *candidate* is a real calendar date in either field order. Day-…, Detect numeric, ISO-timestamp and month-name dates., dates() (+13 more)

### Community 30 - "regex_identifiers.py"
Cohesion: 0.07
Nodes (35): Guard, _btw_ok(), _riziv_ok(), Belgian identifiers: the INSZ, the RIZIV/INAMI number and the BTW number., Validate a Belgian enterprise/VAT number (BE + ten digits)., Validate modern and legacy RIZIV numbers., _kvnr_ok(), German identifiers: the KVNR health-insurance number. (+27 more)

### Community 19 - "test_regex_rules.py"
Cohesion: 0.08
Nodes (40): Guard, Enum, Match, Pattern, _clinical_spans(), Clinical values: blood group, BMI, height, weight and dose., Detect the measurements, blood groups and dosing notations., every() (+32 more)

### Community 175 - "span_shape.py"
Cohesion: 0.22
Nodes (8): ShapeRule, gated_labels(), admits(), admitted(), Refuse a model detection that cannot be the identifier class it claims. A…, Return the labels this rule can judge., Return whether *text* could be a *label*, judged on shape alone. True for every…, Drop the spans in *spans* whose text cannot be the class they claim. Lives here…

### Community 82 - "parse_date"
Cohesion: 0.15
Nodes (20): date, Random, NamedTuple, _redraw_date(), _render_numeric_date(), _year_only(), _numeric_date(), _Written (+12 more)

### Community 53 - "surrogate_numbers.py"
Cohesion: 0.12
Nodes (27): Random, _ssn_digits(), _npi_digits(), _nhs_digits(), _place_digits(), _birth_date(), _insz_digits(), _nir_digits() (+19 more)

### Community 101 - "_SeamReader"
Cohesion: 0.16
Nodes (8): Exception, _Refused, _SeamReader, _font_of(), A shape this module does not understand; the object stays whole., Reads one text object, collecting the slots a replacement may take. Written as…, Return the resource name of the `Tf` whose operator starts at *before*., test_the_font_of_a_show_operator_is_the_name_before_it()

### Community 92 - "object_seams"
Cohesion: 0.12
Nodes (16): NamedTuple, _Seam, object_seams(), Where an object may be cut open, and what to write at the cut. A cut drops…, Candidate seams for the kerns sitting between two strings of a `TJ`. Redaction…, Return `(font, size, y, seams, trailing)` for a reopenable text object. A…, test_an_object_redaction_never_opened_has_no_gaps(), test_a_kern_only_array_marks_where_a_value_was_taken_out() (+8 more)

### Community 58 - "pdf_content_order.py"
Cohesion: 0.10
Nodes (27): Document, Page, Matrix, _matrix_operands(), _drawn_at(), _rows(), _read_base(), _in_page_space() (+19 more)

### Community 81 - "Fragment"
Cohesion: 0.12
Nodes (20): Page, Fragment, _table_fragments(), _line_fragments(), _normalise_glyphs(), _quarter_turns(), _reading_frame(), _contains() (+12 more)

### Community 110 - "extract_pdf_pages"
Cohesion: 0.15
Nodes (17): ValueError, Document, ExtractedPdfPage, extract_pdf_pages(), UnmaskableContentError, _check_unmaskable_channels(), _check_image_pages(), One page's model text in recursive XY-cut reading order. (+9 more)

### Community 68 - "pdf_extraction.py"
Cohesion: 0.10
Nodes (24): Path, Namespace, _Cleaning, _blank(), _drop(), _join(), _keep(), _rule_for() (+16 more)

### Community 59 - "pdf_masking_service.py"
Cohesion: 0.12
Nodes (27): Rect, CorruptInputError, LocaleBadge, Carry a language-detection verdict to the PDF diagnostic overlay. Lives here,…, _same_line(), _vertically_overlaps(), _runs_for_span(), _validate_redaction_options() (+19 more)

### Community 130 - "_font_name"
Cohesion: 0.15
Nodes (15): Font, _covers(), _builtin_like(), _font_name(), Whether *metrics* can map every character *text* needs. A PDF may embed a…, Return the base-14 font closest to the source face named *normalized*. Reached…, Reuse an embedded source font when it can be registered safely. *text* is what…, _embedded_face() (+7 more)

### Community 153 - "_redraw_moved_glyphs"
Cohesion: 0.23
Nodes (12): Point, ResolvedFont, _color(), _insert_replacement(), _draw_fitted(), _words_with_spaces(), _redraw_moved_glyphs(), A page font resource and matching metrics. (+4 more)

### Community 102 - "PdfGlyph"
Cohesion: 0.16
Nodes (18): NamedTuple, PdfGlyph, One source character and the style needed to redraw it., _rotation(), _ChipText, _chip_text(), _PendingRun, _reflow_plan() (+10 more)

### Community 165 - "_draw_replacements"
Cohesion: 0.27
Nodes (7): Document, _Reflow, _draw_replacements(), _strip_document_metadata(), A line plan plus the widths that were asked for, keyed by pending index. The…, Draw each replacement, then splice the new content back into place. Everything…, Clear the metadata this pipeline cannot mask in place. Extraction reads page…

### Community 13 - "pdf_ocr.py"
Cohesion: 0.07
Nodes (53): Exception, Protocol, Page, Pixmap, Point, Font, DocumentText, PreparedDocument (+45 more)

### Community 40 - "pdf_redaction_styles.py"
Cohesion: 0.10
Nodes (34): Rect, EntityStyle, resolve_style(), _tint(), _darken(), _font(), _text_width(), line_height() (+26 more)

### Community 111 - "Fit"
Cohesion: 0.13
Nodes (14): NamedTuple, Fit, plan(), width(), Decide how to draw a replacement that is wider than the room it has.…, How to draw one replacement: the levers, in the order they applied. `word_gap`…, True when the run keeps the typeface's own proportions., True when nothing had to be done at all: draw it as it comes. (+6 more)

### Community 103 - "ctc_decode.py"
Cohesion: 0.18
Nodes (17): Path, ndarray, DecodedLine, load_charset(), _softmax(), looks_like_probabilities(), char_intervals(), greedy_decode() (+9 more)

### Community 73 - "db_postprocess.py"
Cohesion: 0.15
Nodes (22): ndarray, DetectedBox, binarize(), extract_runs(), label_runs(), components_to_boxes(), unclip_box(), order_boxes() (+14 more)

### Community 51 - "ocr_engine.py"
Cohesion: 0.12
Nodes (17): Path, Any, Platform-owned OCR for scanned and image-bearing PDF pages. This package lives…, _file_digest(), _verify(), OcrEngine, _char_offsets(), _engine() (+9 more)

### Community 211 - "._recognise"
Cohesion: 0.29
Nodes (7): ndarray, OcrLine, _rung_for(), One recognised line, in the pixel space of the image it was read from., Return every line read from *page*, a 2-D grayscale array., Crop, batch by width rung, decode, and restore the reading order., Return the padded width for *crop*, from its own shape alone.

### Community 74 - "preprocess.py"
Cohesion: 0.14
Nodes (22): ndarray, to_gray(), ink_ratio(), _box_downsample(), _axis_weights(), resize_bilinear(), resize_for_detection(), pad_to_multiple() (+14 more)

### Community 60 - "OnnxTokenClassifierEngine"
Cohesion: 0.10
Nodes (14): Any, Path, _document_slices(), OnnxTokenClassificationPipeline, OnnxTokenClassifierEngine, Return overlapping (offset, chunk) pairs covering *text*., CPU ONNX Runtime equivalent of Transformers' simple token classifier., Widen a span to the whole token it lands in the middle of. A token classifier… (+6 more)

### Community 219 - "_ancestor_holding"
Cohesion: 0.40
Nodes (4): Path, _ancestor_holding(), Put the platform's shared packages on `sys.path` for the whole suite. Every…, The nearest ancestor directory that contains `marker`.

### Community 2 - "pdf_corpus.py"
Cohesion: 0.08
Nodes (67): Document, Page, Case, _doc(), _bytes(), wrap_two_lines(), wrap_three_lines(), wrap_four_lines() (+59 more)

### Community 207 - "test_compose_smoke.py"
Cohesion: 0.67
Nodes (5): mask(), pdf_bytes(), ready_status(), wait_ready(), main()

### Community 166 - "test_converter_samples.py"
Cohesion: 0.31
Nodes (9): Path, records(), _assert_every_sample_span_survives(), test_the_octo_converter_leaves_every_sample_span_unchanged(), test_the_synthify_converter_leaves_every_sample_span_unchanged(), test_the_berts_converter_leaves_every_sample_span_unchanged(), main(), Run every annotation in every sample file through one team's converter. The… (+1 more)

### Community 9 - "test_gateway.py"
Cohesion: 0.08
Nodes (48): BaseHTTPRequestHandler, ModelService, upstream_url(), _serve(), invoke(), error_of(), get(), test_every_team_masks_and_returns_the_required_headers() (+40 more)

### Community 25 - "test_ocr_engine.py"
Cohesion: 0.05
Nodes (41): ndarray, _map(), test_to_gray_accepts_every_shape_pymupdf_can_hand_it(), test_box_downsample_is_an_exact_mean(), test_bilinear_is_half_pixel_centred_and_preserves_corners(), test_detection_input_is_capped_and_padded_to_the_stride(), test_recognition_crop_is_padded_on_the_right_only(), test_ink_ratio_separates_a_blank_page_from_a_dead_detector() (+33 more)

### Community 12 - "test_pdf_content_order.py"
Cohesion: 0.06
Nodes (55): Document, ndarray, Rect, _serialise(), _document(), _run_split_document(), _one_object_document(), _collision_document() (+47 more)

### Community 119 - "pdf_bytes"
Cohesion: 0.23
Nodes (16): Path, extract_page_lines(), extract_text_from_pdf(), pdf_bytes(), test_a_page_is_read_in_reading_order(), test_flow_order_is_a_plain_top_to_bottom_sort(), test_an_empty_page_yields_no_lines(), test_a_saved_document_carries_a_banner_per_page() (+8 more)

### Community 159 - "test_the_audit_records_which_spans_came_from_ocr"
Cohesion: 0.18
Nodes (11): Pixmap, _grey_block(), _bar_pixmap(), _typed_then_scanned_bytes(), test_the_fallback_is_decided_page_by_page(), test_the_audit_records_which_spans_came_from_ocr(), A synthetic scan: a black bar on white, standing in for a line of text., A scan with a solid dark bar, so ink can be counted before and after. (+3 more)

### Community 31 - "test_pdf_ocr.py"
Cohesion: 0.08
Nodes (37): Document, Page, scanned_page(), whole_raster(), _open(), test_blocks_that_touch_become_one_region(), test_render_zoom_is_bounded_by_pixels_not_by_dpi(), test_a_line_maps_back_into_the_block_it_was_read_from() (+29 more)

### Community 112 - "test_pdf_redaction_corpus.py"
Cohesion: 0.22
Nodes (16): Page, parametrize, _flat(), _spans(), _words(), _source_words(), _upright_words(), _order_break() (+8 more)

### Community 75 - "test_pdf_redaction_styles.py"
Cohesion: 0.13
Nodes (14): Rect, _width_at(), _too_narrow_box(), test_an_ample_box_fits_at_the_maximum_size(), test_a_tight_box_shrinks_the_type_rather_than_cutting_the_text(), test_a_box_that_cannot_fit_signals_rather_than_overflowing(), test_a_last_resort_truncation_marks_the_text_and_stays_in_the_box(), test_enough_room_grows_the_box_instead_of_truncating() (+6 more)

### Community 120 - "drawn"
Cohesion: 0.13
Nodes (16): parametrize, drawn(), test_a_degenerate_measurement_asks_for_nothing(), test_a_value_wider_than_its_line_is_distorted_only_as_a_last_resort(), test_a_slot_of_nothing_asks_for_no_width_rather_than_a_negative_one(), test_the_drawn_width_never_exceeds_the_room_it_was_given(), test_every_fit_takes_the_full_shortfall_and_no_more(), test_a_fraction_of_a_point_is_taken_by_the_next_lever_in_turn() (+8 more)

### Community 131 - "test_pdf_unmaskable_channels.py"
Cohesion: 0.33
Nodes (14): Document, _page(), _serialise(), _refused(), test_an_annotation_carrying_text_refuses_the_document(), test_an_empty_annotation_does_not_refuse_the_document(), test_a_filled_form_field_refuses_the_document(), test_an_unfilled_form_field_does_not_refuse_the_document() (+6 more)

### Community 201 - "test_qa_api_end_to_end.py"
Cohesion: 0.47
Nodes (4): Path, invoke(), first_record(), main()

### Community 41 - "test_settings.py"
Cohesion: 0.10
Nodes (34): Path, _ocr_artifacts(), isolated(), settings(), refused(), test_every_accepted_setting_reaches_its_field(), test_every_refused_setting_names_itself(), test_every_setting_is_covered() (+26 more)

### Community 15 - "service.py"
Cohesion: 0.08
Nodes (50): Run one private team model service for the Compose demo., Exception, Any, ServiceError, _request_id(), _error(), _adapter(), _model_format() (+42 more)

### Community 11 - "normalized_name"
Cohesion: 0.07
Nodes (53): Path, Path, Path, Path, Path, _content_xml(), organization_names(), _is_medical() (+45 more)

### Community 94 - "export_paddle_ocr_onnx.py"
Cohesion: 0.22
Nodes (18): Path, sha256_file(), _load_yaml(), _transform(), check_detection_config(), extract_charset(), convert(), verify_detection() (+10 more)

### Community 22 - "berts/runtime.py"
Cohesion: 0.09
Nodes (42): Berts' public gateway adapter., Path, Checkpoint, language_routing(), checkpoint_for(), _model_path(), _expected_sha256(), _model_format() (+34 more)

### Community 17 - "prepare_datasets.py"
Cohesion: 0.11
Nodes (47): DatasetSpec, _parse_span_list(), Any, _normalize_spans(), _canonical_frame(), DataFrame, adapt_openpii(), adapt_gretel() (+39 more)

### Community 29 - "train_token_classifier.py"
Cohesion: 0.10
Nodes (39): Any, Dataset, available_cpu_count(), available_memory_gib(), parse_spans(), _validate_row_spans(), clean_frame(), DataFrame (+31 more)

### Community 141 - "Path"
Cohesion: 0.19
Nodes (12): Path, checkpoint_has_weights(), checkpoint_has_tokenizer(), selected_checkpoint_path(), assert_best_checkpoint_complete(), find_resumable_checkpoint(), write_run_artifacts(), Report whether *checkpoint* holds adapter or full model weights. (+4 more)

### Community 32 - "train_one"
Cohesion: 0.09
Nodes (38): Optimizer, Module, TrainingArguments, TrainConfig, parse_args(), _configured_label_names(), resize_token_classifier(), make_separate_lr_optimizer() (+30 more)

### Community 85 - "ConsoleProgressCallback"
Cohesion: 0.11
Nodes (11): Tensor, Trainer, TrainerCallback, class_weights(), WeightedTrainer, ConsoleProgressCallback, shutdown_distributed(), run_training() (+3 more)

### Community 18 - "train_token_classifier_test.py"
Cohesion: 0.10
Nodes (48): make_config(), Path, write_checkpoint(), parametrize, test_checkpoint_name_from_reference(), test_training_reached_endpoint_uses_steps_when_max_steps_is_set(), test_training_reached_endpoint_uses_epochs_when_max_steps_is_unset(), test_completed_full_run_selects_the_best_checkpoint() (+40 more)

### Community 235 - "label_support"
Cohesion: 0.50
Nodes (4): test_label_support_reports_configured_labels_with_no_gold_spans(), test_label_support_keeps_labels_absent_from_the_taxonomy(), label_support(), Return the gold span count per label, with configured labels at zero.

### Community 251 - "split_by_verdict_support"
Cohesion: 0.67
Nodes (3): test_rare_labels_are_excluded_from_the_verdict(), split_by_verdict_support(), Split labels into those the verdict scores and those too rare to score. A label…

### Community 209 - "log_alignment_check"
Cohesion: 0.40
Nodes (5): test_alignment_check_refuses_an_all_o_dataset(), test_alignment_check_accepts_entity_tokens(), CaptureFixture, log_alignment_check(), Print the label token counts, or raise when no entity token survived. Span…

### Community 95 - "dev_scores"
Cohesion: 0.13
Nodes (19): make_spans(), test_dev_scores_report_overlap_shared_pii_and_source(), test_validation_scores_report_every_match_mode_and_breakdown(), test_a_prediction_below_the_threshold_does_not_count(), Return one predicted span and the gold span it matches., shared_pii_predictions(), evaluate_by_column(), fbeta() (+11 more)

### Community 90 - "synthify/runtime.py"
Cohesion: 0.19
Nodes (17): Synthify's public gateway adapter., Path, test_a_shaped_entity_is_kept_and_a_bare_word_is_not(), _model(), _onnx_path(), ready(), unload(), release() (+9 more)

### Community 195 - "synthify/onnx/manifest.json"
Cohesion: 0.29
Nodes (6): format, precision, runtime, opset, source_model_sha256, model_sha256

### Community 33 - "convert_record"
Cohesion: 0.07
Nodes (31): ValueError, Any, Path, normalize(), to_training_label(), The label space shared by Synthify training and inference. A GLiNER prompt *is*…, Fold a label to a spelling-insensitive key., Return the native training label for `label`, or None if unsupported. (+23 more)

### Community 180 - "train_gliner.py"
Cohesion: 0.31
Nodes (8): Path, load_jsonl(), sample_weights(), _install_trainer_patches(), main(), Fine-tune GLiNER from prepared Synthify JSONL datasets. Reproduces the training…, Weight each sample by the rarest (highest-weighted) entity it contains., Give GLiNER's internal Trainer a weighted sampler and early stopping.…

### Community 155 - "CreateEnvScriptTests"
Cohesion: 0.30
Nodes (4): CompletedProcess, CreateEnvScriptTests, create-env.sh's one real job: encode the basic-auth hash so it passes deploy-…, The console gate is gone, so nothing should ask for a password. This previously…

### Community 91 - "test_ci_production_promotion.py"
Cohesion: 0.14
Nodes (15): SafeLoader, Node, Pattern, GitLabLoader, _reference(), _glob_to_regex(), _matches_changes(), _evaluate_if() (+7 more)

### Community 1 - "DeploymentScriptTests"
Cohesion: 0.05
Nodes (13): Path, CompletedProcess, DeploymentScriptTests, OperatorSettingReachabilityTests, Exercise blue/green promotion and rollback without a Docker daemon or Nginx., Every setting the masking engine reads must be reachable in production.…, Return every variable ``load_settings`` reads, recorded as it runs. Recorded…, Return each shared toggle in production, mapped to its default. A shared toggle… (+5 more)

### Community 26 - "PreflightFixture"
Cohesion: 0.10
Nodes (5): Path, CompletedProcess, TransactionalDeploymentTests, PreflightFixture, Regression tests for the transactional VPS migration interfaces.

### Community 115 - "PII Masking API Contract v1.3"
Cohesion: 0.13
Nodes (15): PII Masking API Contract v1.3, 1. Purpose and responsibility boundary, 2. Endpoints, 4. Successful mask response, 5. Error contract, 6. Runtime and security requirements, 7. Versioning and compatibility, 8. End-to-end platform flow (+7 more)

### Community 212 - "3. Mask request"
Cohesion: 0.40
Nodes (5): 3. Mask request, Request line, Request body, Required request headers, Current payload limits

### Community 105 - "PII Masking API Contract v1.4"
Cohesion: 0.12
Nodes (16): PII Masking API Contract v1.4, 1. Purpose and responsibility boundary, 2. Endpoints, 4. Successful mask response, 5. Error contract, 6. Runtime and security requirements, 7. Versioning and compatibility, 8. End-to-end platform flow (+8 more)

### Community 213 - "3. Mask request"
Cohesion: 0.40
Nodes (5): 3. Mask request, Request line, Request body, Required request headers, Current payload limits

### Community 132 - "mlflow-vps/README.md"
Cohesion: 0.18
Nodes (13): Traefik single-router console gating (basicauth removed), create-env.sh, deploy-arena.sh, preflight_arena_mlflow CI job, deploy_arena_mlflow CI job, arena-compose.yml (Arena MLflow VPS Compose project), leaderboard-api service, arena-ui service (+5 more)

### Community 197 - "Arena on the MLflow VPS"
Cohesion: 0.33
Nodes (6): Arena on the MLflow VPS, The console is not gated by the proxy, Generating the .env file, Prerequisites, Operator sequence, If something doesn't come up

### Community 190 - "Executive overview"
Cohesion: 0.29
Nodes (6): Executive overview, 1. The problem, 2. What it delivers, 3. Competitive quality assurance, built in, 4. Time-to-market for adoption, 5. Growth trajectory

### Community 149 - "Technical overview"
Cohesion: 0.17
Nodes (11): Technical overview, 1. Pipeline, 2. Dual-layer detection engine, 3. In-house model portfolio, continuously benchmarked, 4. Entity coverage, 5. Format fidelity, 6. Localization, 7. Deterministic output (+3 more)

### Community 188 - "CI production promotion"
Cohesion: 0.29
Nodes (6): CI production promotion, Flow, Two lanes, Pipeline matrix, The web pipeline is the escape hatch, Rollback and retention

### Community 144 - "Per-Document Locale Resolution"
Cohesion: 0.18
Nodes (10): X-Document-Locale Request Header, ADR 002: French Entities (NIR, RPPS) and the Surrogate Locale, ADR 004: One English Locale and Its Identifiers, contracts/masking-api/v1.3, Per-Document Locale Resolution, PDF_LOCALE_BADGE, Locale Detection Guards (40 letters / 0.70 confidence), ORGANIZATION_SURROGATE_POOL (+2 more)

### Community 170 - "ADR 004: One English locale, and the identifiers that come with it"
Cohesion: 0.22
Nodes (9): ADR 004: One English locale, and the identifiers that come with it, One English, not a British and an American one, Four labels move in one schema bump, Dates are read in both orders, The organisation pool merges two registers, An English address puts its number first, Two consequences worth knowing about, Small things that follow from existing rules (+1 more)

### Community 216 - "ADR 005: ONNX is the only served artifact"
Cohesion: 0.40
Nodes (4): ADR 005: ONNX is the only served artifact, What was removed, The identity keys, The contract

### Community 172 - "Masking a subset of a record"
Cohesion: 0.22
Nodes (8): Masking a subset of a record, Masking only some labels, Throughput, What is accepted, Text the pipeline never reads: refused, or stripped, An embedded image: refused, or read with OCR, Lab results are untested, Failure behaviour across a batch

### Community 203 - "Console sample documents"
Cohesion: 0.33
Nodes (5): What may go here, Console sample documents, test_brief_01_urologie.pdf, Locale/specialty clinical letter set (nl-BE/fr-FR/en-GB), deploy/mlflow-vps/arena-compose.yml

### Community 158 - "Synthetic benchmark dataset"
Cohesion: 0.18
Nodes (11): PII entity definitions, Evaluation scoring policy, Evaluation eligibility thresholds, Synthetic benchmark dataset, Synthetic clinical intake fixture 001, Synthetic clinical intake fixture 002, Synthetic clinical intake fixture 003, Synthetic clinical intake fixture 004 (+3 more)

### Community 208 - "GLiNER Training Pipeline"
Cohesion: 0.33
Nodes (6): Synthify Training, GLiNER Training Pipeline, QA PII V1 Labels, Approved Training Data, Synthify Training Requirements, GLiNER Dependency

### Community 123 - "PII Masking Model Arena"
Cohesion: 0.15
Nodes (15): Masking API v1.2 Contract, PII Masking Model Arena, #/arena leaderboard view, #/console masking console, #/teams team console, config/ entity definitions and score weights, datasets/ synthetic benchmark generator, evaluator/ official run sender (+7 more)

### Community 164 - "Synthetic Clinical Intake Fixture"
Cohesion: 0.20
Nodes (10): R2 Access Model, NiFi, Least-Privilege Team Access, Synthetic QA Evaluation Evidence, Synthetic Clinical Intake 008, Synthetic Clinical Intake 009, Synthetic Clinical Intake 010, Synthetic Clinical Intake 011 (+2 more)

### Community 238 - "Graph-First Repository Exploration"
Cohesion: 1.00
Nodes (3): Graphify Workflow, Graph-First Repository Exploration, Source Verification of Graph Conclusions

### Community 249 - "Team Adapter Registry"
Cohesion: 1.00
Nodes (3): Gateway Example Configuration, Supported Teams Registry, Team Adapter Registry

### Community 250 - "TamerBERT Generalization Trial Script"
Cohesion: 0.67
Nodes (3): Berts Training Record, TamerBERT Generalization Trial Script, Training Record Excluded from Production Inference

### Community 236 - "Synthify Model Example Configuration"
Cohesion: 0.50
Nodes (4): Synthify Model Example Configuration, Synthify Immutable Model Identity, Synthify ONNX Artifact Configuration, Legacy GLiNER Belgian PII Schema

### Community 5 - "vps-common.sh"
Cohesion: 0.08
Nodes (55): deploy-gateway.sh script, usage(), validate_model_directory(), validate_octo_artifacts(), validate_synthify_artifacts(), require_approved_value(), validate_berts_routing_switch(), berts_language_routing() (+47 more)

### Community 162 - "deploy-arena.sh"
Cohesion: 0.29
Nodes (9): deploy-arena.sh script, PROJECT, COMPOSE_FILE, usage(), validate_image_ref(), validate_storage_settings(), validate_traefik_network(), ARENA_API_IMAGE_REF (+1 more)

### Community 116 - "pii-masking-model-stage"
Cohesion: 0.19
Nodes (9): pii-masking-model-stage script, require_argument_count(), read_stdin_parameters(), verify_tree(), checkout_pointers(), assemble_tree(), harden_tree(), find_identical_release() (+1 more)

### Community 214 - "pii-masking-nginx-control"
Cohesion: 0.60
Nodes (3): pii-masking-nginx-control script, require_argument_count(), switch_upstream()

### Community 76 - "package.json"
Cohesion: 0.09
Nodes (21): name, private, version, type, scripts, dev, build, preview (+13 more)

### Community 77 - "App.jsx"
Cohesion: 0.16
Nodes (17): VIEWS, NAV_ITEMS, renderNavLinks(), renderSectionTabs(), routeFromHash(), CegedimLogo(), THEME_ICON, App() (+9 more)

### Community 35 - "Arena.jsx"
Cohesion: 0.08
Nodes (29): ACCENTS, ACCENT_HEX, WEIGHT_CLASSES, weightClassOf(), RECALL_METRICS_BY_BASIS, PRECISION_METRICS_BY_BASIS, F1_METRICS_BY_BASIS, AWARD_ICON (+21 more)

### Community 87 - "format.js"
Cohesion: 0.22
Nodes (14): rivalryLine(), scoreForBasis(), exportBoardCsv(), Comparison(), Arena(), HIGHLIGHTS, Highlight(), metricOf() (+6 more)

### Community 78 - "getTeamDisplayName"
Cohesion: 0.14
Nodes (19): TeamMark(), MedalWall(), displayName(), Home(), Row(), starsFor(), useComets(), useShimmerStars() (+11 more)

### Community 49 - "Console.jsx"
Cohesion: 0.14
Nodes (26): TERMINAL, STEPS, stepState(), Stepper(), RunSummary(), DEFAULT_COPY, Console(), TEXT_TYPES (+18 more)

### Community 127 - "PerLabel.jsx"
Cohesion: 0.31
Nodes (13): METRICS, SORTS, valueOf(), reasonOf(), supportOf(), formatValue(), exportPerLabelCsv(), Cell() (+5 more)

### Community 134 - "useIntro"
Cohesion: 0.23
Nodes (9): ArenaGates(), prefersReducedMotion(), useIntro(), useStars(), StarfallVeil(), Laurel(), FlameGlyph(), TICKS (+1 more)

### Community 70 - "theatrics.jsx"
Cohesion: 0.14
Nodes (21): SAMPLE_TEXT, SAMPLE_SPANS, recallFor(), tokenFor(), segments(), originalSegments(), maskedLength(), assertSpansAlign() (+13 more)

### Community 83 - "load_mock_results"
Cohesion: 0.16
Nodes (19): _clamp01(), _available(), _score(), _mock_document(), load_mock_results(), TEMPORARY: fabricated leaderboard data for the preview UI. Nothing has been…, One fabricated run at a given quality level, in the QA result schema shape.…, Build a full mock board: several historical runs per team, freshly randomized… (+11 more)

### Community 47 - "test_arena_per_label.py"
Cohesion: 0.09
Nodes (30): _per_label_block(), Normalise one report's `per_label` into the board's own shape. Keeps every…, _per_label_from_flat_metrics(), Collect `<metric>__<LABEL>` keys into the board's per-label block., _qa_release_entry(), test_precision_f1_and_support_are_kept_not_discarded(), test_support_is_read_from_qas_own_key_name(), test_release_precision_is_unavailable_with_qas_own_reason() (+22 more)

### Community 232 - "gunicorn.conf.py"
Cohesion: 0.50
Nodes (3): positive_integer(), Shared production HTTP settings for the gateway and model services., Read a strictly positive integer from the environment.

### Community 108 - "error"
Cohesion: 0.18
Nodes (12): error(), Return the contract's small, sanitized JSON error body., span(), convert(), test_only_a_profile_the_registry_knows_can_be_asked_for(), test_a_composite_address_stays_one_span(), test_a_label_outside_the_taxonomy_is_dropped_rather_than_invented(), test_an_unmapped_label_is_logged_by_name_and_never_by_its_text() (+4 more)

### Community 50 - "test_locale_detection.py"
Cohesion: 0.11
Nodes (30): _sample(), _detector(), confidence_report(), resolve_locale(), detect_locale(), Resolve which language a document is written in, per document. A pseudonym has…, Return the fixed prefix put to the detector and its letter count. One place…, Return the process-wide detector, built on first use. Building is free; the… (+22 more)

### Community 79 - "locales.py"
Cohesion: 0.12
Nodes (21): _no_ordinal(), _french_ordinal(), _english_ordinal(), LocaleProfile, profile(), _fold(), _months_by_folded_name(), month_number() (+13 more)

### Community 10 - "regex_engine.py"
Cohesion: 0.06
Nodes (52): _postcode_spans(), _building_number_spans(), Postcodes and house numbers, across the locales the service accepts., Detect postcodes: the UK form standalone, the others in an address slot., Detect the house number in an address line, anchored on the postcode. A bare…, _to_output_spans(), _arbitrate_overlaps(), coalesce_spans() (+44 more)

### Community 28 - "test_checksums.py"
Cohesion: 0.08
Nodes (39): _insz_ok(), Validate a Belgian national register number. The check value is ``97 - (base %…, _ssn_ok(), Validate a US social security number against its allocation rules. There is no…, insz(), test_a_number_the_register_really_issues_is_accepted(), test_separators_do_not_change_the_verdict(), test_an_impossible_date_is_rejected() (+31 more)

### Community 174 - "test_contact_spans.py"
Cohesion: 0.28
Nodes (7): _email_ok(), _domain_ok(), _url_ok(), test_an_email_is_accepted_only_with_a_well_formed_local_part_and_domain(), test_a_host_needs_two_labels_and_an_alphabetic_top_label(), test_a_web_address_is_judged_on_its_host_with_or_without_a_scheme(), test_a_calendar_date_is_never_reported_as_a_telephone_number()

### Community 88 - "test_phone_spans.py"
Cohesion: 0.16
Nodes (19): _read_shape(), Measure the writing style of one matched digit run., _assert_cases(), test_belgian_formats(), test_a_written_trunk_zero_is_not_part_of_the_number(), test_dutch_formats(), test_french_formats(), test_united_kingdom_formats() (+11 more)

### Community 67 - "_is_national_identifier"
Cohesion: 0.09
Nodes (22): _card_ok(), A Luhn-valid card number that is not really a national identifier., _luhn_ok(), Payment card numbers, and the Luhn check they are claimed on., Check a digit string with the Luhn algorithm., _nir_ok(), French identifiers: the NIR and the RPPS/ADELI number., Validate a French social security number: mod-97 over the first thirteen.… (+14 more)

### Community 72 - "surrogates.py"
Cohesion: 0.13
Nodes (22): _year_offsets(), Return valid 4-50-year offsets for *value*, preferring the past. A date in the…, _dialling_prefix_length(), Return how many leading digits of a phone number survive masking. A masked…, _match_case(), Return *value* capitalized the way *model* is., SurrogateContext, from_vocabulary() (+14 more)

### Community 38 - "test_surrogates.py"
Cohesion: 0.12
Nodes (34): redraw(), surrogate_checked_number(), surrogate_age(), surrogate_organization(), surrogate_national_number(), resolve(), Return the original with its digits redrawn, separators kept. Faker has no…, Return a surrogate that passes the same check the original did. (+26 more)

### Community 24 - "prepare_document"
Cohesion: 0.09
Nodes (47): prepare_document(), redact_document(), Validate and extract the model inputs for one supported document. *ocr_bounds*…, Apply the shared deterministic replacement policy to original bytes. *locale*…, pdf_bytes(), wrapped_pdf_bytes(), _image_and_text_pdf_bytes(), _spans() (+39 more)

### Community 204 - "pdf_block_reflow.py"
Cohesion: 0.40
Nodes (5): _paragraphs(), plan(), Close a line a masked value emptied, by lifting the lines under it.…, Split *lines* into runs of one leading and one left edge., Return how far up each line moves, keyed by `Line.key`. Only lines that…

### Community 89 - "plan"
Cohesion: 0.21
Nodes (19): Line, One visual line of a page: where it sits, and whether anything is left. *blank*…, paragraph(), test_every_line_under_a_blank_one_comes_up_by_the_leading(), test_two_blank_lines_lift_twice(), test_only_the_lines_below_move(), test_a_blank_last_line_moves_nothing(), test_a_paragraph_with_no_blank_line_is_untouched() (+11 more)

### Community 45 - "_Token"
Cohesion: 0.11
Nodes (12): _Unsupported, _Token, _concat(), _Scanner, The scanner met something it will not reason about., One lexical unit of a content stream, with where it sits in the bytes., The little stack machine a content stream drives. One method per operator that…, A string, a name, a delimiter: an operand whose value is not a number. (+4 more)

### Community 184 - "_tokens"
Cohesion: 0.25
Nodes (8): _skip_literal_string(), _tokens(), Return the offset just past the `(...)` string beginning at *start*., Walk a content stream, yielding every token in order. Whitespace and comments…, test_a_literal_string_ends_at_its_own_closing_bracket(), test_a_string_that_never_closes_is_refused(), test_every_token_carries_its_kind_and_its_offsets(), test_a_byte_the_lexer_cannot_classify_is_refused()

### Community 44 - "test_pdf_content_stream.py"
Cohesion: 0.10
Nodes (31): scan(), _assemble_content(), Return where text is drawn, and where a blob may be spliced in. The first list…, Return *base* with every splice applied, dropping none of them. An insertion…, refused(), test_operators_written_inside_a_string_are_not_operators(), test_matrices_concatenate_in_pdf_order(), test_a_text_object_reports_where_it_draws() (+23 more)

### Community 151 - "_rewrite_object_in_place"
Cohesion: 0.17
Nodes (12): _rewrite_object_in_place(), _matched_seams(), _rewritten_objects(), Return *chunk* with each owned replacement spliced into its own gap. Returns…, Keep the seams between operators, and the interior kerns that fit. A seam…, Rewrite each text object that can carry its own replacements inside it., test_a_replacement_is_spliced_into_the_gap_redaction_left(), test_a_value_that_ended_the_line_takes_the_close_of_the_object() (+4 more)

### Community 205 - "_outer_splice"
Cohesion: 0.33
Nodes (6): _outer_splice(), Place a replacement outside the object it came from. Snap back to the last…, test_a_replacement_snaps_back_to_the_last_seam_before_its_target(), test_with_no_seam_before_it_a_replacement_stays_at_the_end(), test_a_seam_inside_a_text_object_wins_when_it_is_the_nearer_one(), And the blob closes the object, then reopens it where `scan` said.

### Community 139 - "page_metrics"
Cohesion: 0.18
Nodes (13): PageMetrics, page_metrics(), _whitespace_gaps(), _split_at(), _vertical_parts(), _horizontal_parts(), Derive cut thresholds from the page's own typography., Gaps between the merged occupied intervals of a projection profile. (+5 more)

### Community 69 - "frag"
Cohesion: 0.11
Nodes (25): column_cuts(), _aligned_table(), xy_cut(), group_rows(), assemble_region(), Keep only the vertical cuts that separate genuine columns. A wide blank band is…, True when candidate columns are really the columns of a borderless table.…, Segment fragments into leaf regions, returned in reading order. Axis… (+17 more)

### Community 46 - "test_pdf_layout.py"
Cohesion: 0.10
Nodes (27): assemble_region_with_glyphs(), Render one region and retain a source glyph for every real character., glyph(), test_whitespace_collapses_without_losing_a_glyph_per_character(), test_normalising_a_rewritten_glyph_keeps_every_other_field(), test_an_all_whitespace_line_normalises_away(), test_the_glyph_map_stays_one_entry_per_character(), test_a_table_has_no_source_geometry_so_the_glyph_path_refuses_it() (+19 more)

### Community 206 - "find_running_lines"
Cohesion: 0.33
Nodes (6): _shape(), find_running_lines(), Header/footer text repeated across pages, matched by shape not exact text., test_running_lines_need_a_majority_of_pages_and_two_at_least(), test_only_the_three_lines_at_each_margin_can_run(), test_a_short_page_cannot_vote_for_its_own_removal_twice()

### Community 129 - "pdf_line_reflow.py"
Cohesion: 0.24
Nodes (13): Slot, Fixed, Plan, _midpoint(), _lines(), _plan_line(), Make room on a line for a box wider than the text it replaces. A redaction chip…, A chip that wants room: where it sits now, and how wide it wants to be. (+5 more)

### Community 152 - "_render_styled_run"
Cohesion: 0.27
Nodes (12): GlyphRun, _safe_expand(), _chip_max_size(), _render_styled_run(), Adjacent source glyphs that can share one replacement rectangle., Return how far right *run*'s box may grow without covering real content.…, Return the largest type a chip over *run* should be set in. `fit_text`'s 8.5pt…, Draw one run as a visible redaction box and return the text drawn. *shift* and… (+4 more)

### Community 185 - "_detect_and_redact"
Cohesion: 0.25
Nodes (7): Read the bounds an operator set, falling back to each default., _settings(), _model_file_size_mb(), _cancel_idle_unload(), _detect_and_redact(), Size on disk of the weights this service actually loaded, in MB. Self-reported…, Run one team's detector inside the platform-owned document pipeline. Returns…

### Community 39 - "masking_engine/runtime.py"
Cohesion: 0.08
Nodes (33): reader_for(), Return a reader for *settings*, or None when OCR is switched off. The engine is…, _runtime(), _model_format(), _drop_word_fragments(), _drop_time_fragments(), _trim_line_overrun(), _merge_address_parts() (+25 more)

### Community 140 - "test_address_spans.py"
Cohesion: 0.31
Nodes (12): postcodes(), buildings(), test_an_address_line_yields_its_postcode_and_its_house_number(), test_a_number_with_no_postcode_beside_it_is_not_a_house_number(), test_a_uk_postcode_stands_alone(), test_a_european_postcode_needs_the_slot_an_address_puts_it_in(), test_a_year_written_after_a_comma_is_not_a_postcode(), test_a_us_zip_needs_a_real_state_in_front_of_it() (+4 more)

### Community 54 - "test_pdf_line_reflow.py"
Cohesion: 0.17
Nodes (28): slot(), fixed(), test_a_chip_that_already_fits_moves_nothing(), test_a_wider_chip_pushes_the_rest_of_the_line_right(), test_letters_inside_a_word_move_together(), test_a_gutter_absorbs_the_shift_and_the_next_column_stays_put(), test_a_line_with_no_room_left_is_refused_whole(), test_two_tightly_set_chips_on_a_line_accumulate() (+20 more)

### Community 63 - "_scan_bytes"
Cohesion: 0.10
Nodes (26): _image_ink(), _scan_bytes(), _read_top_bar(), test_a_masked_scan_loses_the_pixels_not_just_the_look(), test_an_image_ocr_read_nothing_from_keeps_its_pixels(), test_a_page_that_has_a_text_layer_is_never_read_by_ocr(), test_an_ocr_replacement_is_set_at_the_size_it_replaces(), test_the_injected_text_layer_makes_the_scan_extractable() (+18 more)

### Community 233 - "test_the_separator_survives_the_word_it_followed_being_redacted"
Cohesion: 0.50
Nodes (4): _read_two_words(), test_the_separator_survives_the_word_it_followed_being_redacted(), Read the bar as two words, so the space between them is under test., A space carried on the end of its word dies with that word. The injected layer…

### Community 64 - "test_pdf_text_fitting.py"
Cohesion: 0.08
Nodes (25): test_a_run_that_already_fits_is_left_completely_alone(), test_the_smallest_overflow_comes_out_of_the_spaces_only(), test_spaces_are_never_squeezed_past_the_justification_minimum(), test_tracking_only_starts_once_the_spaces_are_spent(), test_letters_are_never_pulled_closer_than_three_percent_of_the_em(), test_glyph_scaling_is_the_third_lever_and_stops_at_ninety_seven(), test_the_whole_run_shrinks_before_the_letterforms_are_distorted(), test_a_uniform_shrink_stops_at_four_fifths_of_the_size() (+17 more)

### Community 71 - "test_qa_label_schema.py"
Cohesion: 0.14
Nodes (20): _labelled(), _labels(), test_a_mixed_line_reports_only_approved_labels(), test_a_keyword_rescues_an_insz_whose_checksum_failed(), test_a_bare_insz_still_needs_its_checksum(), test_the_english_identifiers_reach_the_output(), test_an_unallocatable_ssn_area_is_not_claimed(), test_npi_is_only_reported_next_to_its_keyword() (+12 more)

### Community 193 - "test_shared_adapter.py"
Cohesion: 0.43
Nodes (6): _assert_exports_the_interface(), test_the_octo_adapter_exports_the_interface(), test_the_synthify_adapter_exports_the_interface(), test_the_berts_adapter_exports_the_interface(), main(), Run every test in this module, discovered rather than listed.

### Community 93 - "test_span_shape.py"
Cohesion: 0.11
Nodes (13): test_the_counts_are_the_ones_the_validators_enforce(), test_an_age_has_to_carry_a_digit(), test_a_span_list_keeps_only_the_shapes_the_rule_admits(), test_no_rule_means_no_gate(), test_a_span_whose_text_cannot_be_read_is_kept(), test_a_substituted_rule_is_the_one_that_decides(), _riziv_lengths(), main() (+5 more)

### Community 65 - "test_service.py"
Cohesion: 0.14
Nodes (22): invoke(), pdf_bytes(), _unloaded(), _loaded(), test_a_generic_word_is_not_kept_as_a_detection(), test_health_is_up_without_a_model(), test_ready_is_unavailable_until_a_request_loads_the_model(), test_version_reports_the_model_without_loading_it() (+14 more)

### Community 84 - "LaneSelectionTests"
Cohesion: 0.15
Nodes (8): test_absent_or_blank_mask_labels_mean_the_full_taxonomy(), pipeline_is_created(), LaneSelectionTests, Each lane runs for its own paths and stays out of the other's., The point of the change: no torch build for a stylesheet edit., The ONNX artifacts are baked into the image by .dockerignore. Without…, load_production_candidate would 404: no candidate was recorded., A manual main pipeline must offer everything, whatever changed.

### Community 234 - "check_graphify_hygiene.py"
Cohesion: 0.83
Nodes (3): tracked_graphify_files(), validate_json_deliverables(), main()

### Community 176 - "onnx-en/manifest.json"
Cohesion: 0.22
Nodes (8): format, locale, model_sha256, opset, precision, runtime, source_model, source_revision

### Community 177 - "onnx-fr/manifest.json"
Cohesion: 0.22
Nodes (8): format, locale, model_sha256, opset, precision, runtime, source_model, source_revision

### Community 178 - "berts/onnx/manifest.json"
Cohesion: 0.22
Nodes (8): format, locale, model_sha256, opset, precision, runtime, source_model, source_revision

### Community 194 - "octo/onnx/manifest.json"
Cohesion: 0.29
Nodes (6): format, precision, runtime, opset, source_model_sha256, model_sha256

### Community 179 - "regex_detectors.py"
Cohesion: 0.33
Nodes (8): _has_phone_context(), _insz_checksum_valid(), _luhn_valid(), _overlaps_any(), detect_entities(), Deterministic regex-based detectors for PII categories that pattern-matching…, digits: the 11-digit INSZ candidate with all separators stripped., Returns a list of {"start", "end", "label", "score"} in `text`'s coordinates.…

### Community 96 - "test_regex_detectors.py"
Cohesion: 0.21
Nodes (17): _merge_regex_spans(), Supplement the model's spans with checksum-validated regex detections. Regex…, _found(), _spans(), test_detects_insz_riziv_credit_card_and_url(), test_rejects_credit_card_failing_luhn(), test_rejects_insz_failing_mod97_checksum(), test_drops_sentence_punctuation_trailing_a_url() (+9 more)

### Community 221 - "test_runtime_validation.py"
Cohesion: 0.50
Nodes (3): FragmentingModel, main(), Validate Synthify's post-processing of fragmented model entities.

### Community 62 - "test_windowing.py"
Cohesion: 0.14
Nodes (22): RecordingModel, _find_all(), _long_text(), test_short_text_bypasses_windowing(), test_entity_past_the_token_limit_is_still_found(), test_offsets_map_back_to_the_original_text(), test_windows_overlap_and_cover_the_whole_text(), test_chunking_terminates_on_a_single_oversized_word() (+14 more)

### Community 167 - "ArenaCiWiringTests"
Cohesion: 0.20
Nodes (4): ArenaCiWiringTests, Exercise the MLflow-VPS arena deployment without a Docker daemon. This target…, Regression guard for the exact bug fixed at commit 0417116: a job whose `needs`…, Several tests in this repository parse ci/deploy.yml with a plain…

### Community 66 - "ArenaComposeTests"
Cohesion: 0.08
Nodes (10): ArenaComposeTests, The UI must not need a capability `cap_drop: [ALL]` takes away. A root nginx…, Traefik's port label and both healthchecks must track nginx.conf. These four…, The picker must not depend on a folder that exists only on a laptop. It…, ARENA_TEAMS also gates runs.py server-side, not just the picker. The team…, Used only when the board cannot be read; without it there is no destination at…, qa-automation overwrites one key per run and R2 has no versioning, so without…, Left on, the mock hides real published results behind fake ones. (+2 more)

### Community 137 - "ArenaPreflightTests"
Cohesion: 0.30
Nodes (3): ArenaPreflightTests, Run deploy-arena.sh preflight with docker and curl stubbed out., The console is no longer gated, so the credential is not required. Four tests…

### Community 187 - "load_ci"
Cohesion: 0.39
Nodes (3): load_ci(), ProductionPromotionPolicyTests, The gate must be blocking and the arena must never be.

### Community 160 - "select_jobs"
Cohesion: 0.22
Nodes (8): resolve_job(), job_names(), select_jobs(), needs_of(), NoNeedsMayDangleTests, Flatten a job's `extends:` chain the way GitLab does. Later parents override…, The jobs GitLab would put in this pipeline., No selected job may `need` a job the same rules excluded. GitLab does not…

### Community 196 - "QaTriggerVariablePolicyTests"
Cohesion: 0.29
Nodes (3): QaTriggerVariablePolicyTests, Neither QA trigger may carry `variables:`. They would become pipeline variables…, Inherited globals would trip the same permission error.

### Community 168 - "CandidateImageAliasTests"
Cohesion: 0.20
Nodes (5): CandidateImageAliasTests, The promoted candidate must gain a tag named after the main commit. Downstream…, A rebuild could yield a digest other than the tested candidate's., Aliasing an unvalidated digest would publish an unchecked image., A manifest list and a single manifest are not interchangeable.

### Community 186 - "CandidateManifestRepublishTests"
Cohesion: 0.25
Nodes (4): CandidateManifestRepublishTests, The promoted candidate's manifest must be reachable by the main SHA. Downstream…, Main and the MR head are the same commit, so it already exists., Uploading anything unvalidated would hand QA an unchecked candidate.

### Community 113 - "LaneRuleLockstepTests"
Cohesion: 0.15
Nodes (7): LaneRuleLockstepTests, Jobs bound by `needs:` must share one rule template, not copies. Two templates…, load_production_candidate and its three dependants, or none of them., A copied rule block agrees today and drifts on the next edit., `rules:` overrides the job-level `when:`, so each arm must restate it., The escape hatch stops being one the moment it is filtered., A lane the workflow rule cannot match would never build on main.

### Community 14 - "Pipeline stages and include list"
Cohesion: 0.06
Nodes (53): platform_paths path anchor, arena_paths path anchor, .mr_only_rules template, .release_candidate_rules template, .arena_image_rules template, .arena_scan_rules template, .arena_deploy_rules template, .main_promotion_rules template (+45 more)

### Community 118 - "Unified masking gateway"
Cohesion: 0.14
Nodes (16): Shared PII Data Masking Monorepo, NiFi ingestion routing (model-agnostic), Unified masking gateway, masking API v1.2 contract, Trusted team_id derivation from R2 key, mutmut mutation testing setup, MUTANT_UNDER_TEST os.environ pitfall, functools.partial handler binding pattern (+8 more)

### Community 181 - "gateway service"
Cohesion: 0.54
Nodes (8): gateway service, octo-model service, synthify-model service, berts-model service, public network, models internal network, unset PROD_LOGS_S3 credentials disable audit upload, BERTS_LANGUAGE_ROUTING per-checkpoint routing

### Community 114 - "PII Masking API Contract v1.2"
Cohesion: 0.15
Nodes (16): PII Masking API Contract v1.2, GET /health endpoint, GET /ready endpoint, GET /version endpoint, POST /v1/mask endpoint, Idempotency-Key header / replay safety, Error contract (4xx/5xx taxonomy), Status manifest (SUCCEEDED/FAILED) (+8 more)

### Community 86 - "Private NiFi VPS deployment README"
Cohesion: 0.14
Nodes (20): Private NiFi VPS deployment README, Blue/green Nginx gateway (18081/18082, stable 18080), migrate-env.py, preflight_production CI job, deploy_production CI job, deploy-gateway.sh, install-vps.sh, watchdog-gateway.sh (+12 more)

### Community 182 - "x-model-toggles shared env anchor"
Cohesion: 0.32
Nodes (8): deploy/compose.yml (NiFi VPS Compose project), x-model-toggles shared env anchor, gateway service, octo-model service, synthify-model service, MODEL_INTRA_OP_THREADS benchmarked half-core default, tests/test_deployment_scripts.py toggle-drift tests, platform/masking-gateway/src/masking_engine/app/core/config.py Settings defaults

### Community 199 - "Automated Document Masking Delivery"
Cohesion: 0.33
Nodes (6): Manual Redaction Problem, Automated Document Masking Delivery, Competitive Model Benchmarking (Internal Arena), Single Integration Interface, In-House Model Portfolio (Octo, Synthify, Berts), PII Masking Model Arena

### Community 229 - "Growth Trajectory Roadmap"
Cohesion: 0.50
Nodes (4): Growth Trajectory Roadmap, OCR for Scanned Documents (Roadmap), Multilingual Detection (Roadmap), Medical-Domain Entity Support (Roadmap)

### Community 218 - "Dual-Layer Detection Engine"
Cohesion: 0.40
Nodes (5): Document Masking Pipeline, Dual-Layer Detection Engine, Deterministic Checksum-Validated Layer, Trained Model Detection Layer, PDF/Text/CSV/JSON Format Fidelity

### Community 143 - "Masking Gateway API"
Cohesion: 0.17
Nodes (12): Masking Gateway API, Independently-Owned Team Model Services, NiFi Trusted Routing, incoming/<team>/<request-id>/<filename> R2 Key, MLflow Synthetic-Evaluation Child Run, R2-to-R2 Runtime Masking Flow, Multimodel Deployment / Compose Demo Guide, X-Team-ID Header (+4 more)

### Community 124 - "contracts/masking-api/v1.4"
Cohesion: 0.15
Nodes (15): contracts/masking-api/v1.4, X-Model-Format Header (onnx only), contracts/masking-api/v1.4, Team Boundary Ownership Rule (teams/team-N/), adapter.detect(text, metadata) -> list[MaskSpan] Interface, from masking_engine import MaskSpan, LabelConverter, masking_engine.__all__ Supported Surface, API v1.4 Gateway Contract (+7 more)

### Community 145 - "Platform CI Lane"
Cohesion: 0.18
Nodes (12): MR Verification vs Default-Branch Promotion Pipeline Split, record_production_candidate Job, production-candidate.env Manifest, load_production_candidate Job, Platform CI Lane, Arena CI Lane, .gitlab-ci.yml, Rule Templates (.main_promotion_rules, .production_deploy_rules, etc.) (+4 more)

### Community 227 - "deploy_production Job"
Cohesion: 0.67
Nodes (4): preflight_production Job, deploy_production Job, deploy-gateway.sh Script, Blue/Green Rollback Behavior

### Community 97 - "Masking Gateway"
Cohesion: 0.13
Nodes (18): R2 Source Object, NiFi, Masking Gateway, contracts/masking-api/v1.4, R2 Masked Object + Terminal Manifest, Generated Synthetic PDFs + Manifest, evaluator/run_benchmark.py, Per-File Scores and Latency (+10 more)

### Community 146 - "Team-Owned Adapters"
Cohesion: 0.20
Nodes (12): Team-Owned Adapters, teams/octo/adapter.py, teams/synthify/adapter.py, teams/berts/adapter.py, Shared Masking Engine, runtime.py, Regex Engine + Model Detector, Orchestrator (+4 more)

### Community 125 - "fast_checks Job"
Cohesion: 0.13
Nodes (15): evaluation/scripts/validate_contracts.py, Evaluation + Manifest JSON Schemas, CI Top-Level Pipeline, ci/validate.yml, ci/ml.yml, ci/security.yml, ci/deploy.yml, fast_checks Job (+7 more)

### Community 98 - "POST /v1/mask (Gateway)"
Cohesion: 0.13
Nodes (18): GET /health (Gateway), GET /ready (Gateway), GET /version (Gateway), POST /v1/mask (Gateway), GET /health (Model Service), GET /ready (Model Service), GET /version (Model Service), POST /mask (Model Service) (+10 more)

### Community 163 - "evaluation/leaderboard_api/"
Cohesion: 0.22
Nodes (10): GET /health (Arena API), GET /api/v1/config, GET /api/v1/leaderboard, GET /api/v1/samples, POST /api/v1/runs, GET /api/v1/runs, GET /api/v1/runs/{run_id}/content/{side}, evaluation/leaderboard_api/ (+2 more)

### Community 147 - "Run Discovery via Terminal Manifest"
Cohesion: 0.20
Nodes (12): GET /api/v1/runs/{run_id}, Run Discovery via Terminal Manifest, incoming/<team>/requests/routed/<filename> Convention, platform/nifi/configuration/team-api-routing.properties.example, source Bucket (incoming/<team>/requests/...), destination Bucket (masked/, manifests/), PII Masking API Contract v1.2, NiFi ListS3 Processor (+4 more)

### Community 169 - "NIR (French National ID) Canonical Label"
Cohesion: 0.31
Nodes (9): NIR (French National ID) Canonical Label, RPPS (French Practitioner Registration) Canonical Label, Regex Detection Engine (_UNMAPPED_OUTPUT_LABELS), Octo Model Checkpoint (B-NIR/I-NIR/B-RPPS/I-RPPS), qa-pii-v1 Entity Labels Schema (Version 3), Orchestrator._REGEX_EXCLUSIVE, fr_FR.ssn() NIR Surrogate, RPPS Digit-Redraw Surrogate Generator (+1 more)

### Community 148 - "en Locale (Single English Locale)"
Cohesion: 0.17
Nodes (12): LabelConverter.convert, LocaleProfile (app/core/locales.py), app/engines/masking_router.py, app/core/config.py, label_registry, One Locale Per Document Principle, en Locale (Single English Locale), faker_locale='en_GB' House Style (+4 more)

### Community 189 - "lingua-language-detector"
Cohesion: 0.29
Nodes (7): app/core/locale_detection.py, redact_document Function, MASKING_LOCALE Deployment Setting (removed), PDF_LOCALE_BADGE Overlay, Locale Detection Confidence Guards (40 letters / 0.70 confidence), _DECOY_LANGUAGES (English as Decoy), lingua-language-detector

### Community 240 - "scripts/build_french_organization_pool.py"
Cohesion: 0.67
Nodes (3): scripts/build_french_organization_pool.py, SIRENE Business Register, _PERSON_LIKE / _is_person_name Filter

### Community 117 - "Amendment 2026-09-04: Production Enables OCR by Default"
Cohesion: 0.13
Nodes (16): ADR 003: OCR Fallback for Scanned PDFs, Bit-Identical Determinism Claim, PDF_OCR Flag (default off), Amendment 2026-09-04: Production Enables OCR by Default, fetch_ocr_artifacts Build Step, OCR_MODEL_DIR Host-Mounted Directory, OCR_DET_SHA256 / OCR_REC_SHA256 / OCR_CHARSET_SHA256 Pins, deploy-gateway.sh (Artifact Verification) (+8 more)

### Community 126 - "app/services/pdf_extraction.py"
Cohesion: 0.13
Nodes (15): extract_pdf_pages Function, Image-Bearing Page Rejection Rule, Per-Page Text-Layer Fallback Gate, get_pixmap(clip=...) Dedup Rule, inject_text_layer Function, MaskSpan.source_layer='ocr' Audit Field, Accepted Formats / Size Table, PDF Annotation/Form/Embedded-File Refusal (+7 more)

### Community 183 - "apply_redactions(images=, graphics=, text=)"
Cohesion: 0.25
Nodes (8): PDF_REDACT_IMAGE_PIXELS Flag, PDF_REDACT_IMAGE_NONE Flag, apply_redactions(images=, graphics=, text=), pdf_content_order Splicing Function, Chip Overhang on Adjacent Text, Kern-Only TJ Array Slot, Box Clamping at Line-Centre Midpoint, Row-Tolerance Reading Order

### Community 157 - "SSN/NPI/NHS_NUMBER Router-Generated Surrogates"
Cohesion: 0.27
Nodes (11): SSN Canonical Label, NPI Canonical Label, NHS_NUMBER Canonical Label, GMC Canonical Label, qa-pii-v1 Schema Version 4, _nhs_ok mod-11 Check, _GMC_CONTEXT_RE, _NPI_CONTEXT_RE (+3 more)

### Community 228 - "scripts/build_english_medical_organization_pool.py"
Cohesion: 0.50
Nodes (4): scripts/build_english_medical_organization_pool.py, NHS ODS Register, CMS Provider-Data Facility Listings, organization_pool_for Function

### Community 242 - "_building_number_spans / carries_postcode"
Cohesion: 0.67
Nodes (3): _US_ZIP_RE, _building_number_spans / carries_postcode, _trim_line_overrun (Any-Length Tail Trim Fix)

### Community 198 - "ADR 005: ONNX-Only Serving"
Cohesion: 0.33
Nodes (6): ADR 005: ONNX-Only Serving, SafeTensors/Transformers Torch Pipeline (removed), TokenClassifierEngine (removed), OnnxTokenClassifierEngine (standalone), ONNX-Only Model Serving, ACCELERATOR=cpu (No CUDA Support)

### Community 171 - "OCTO_MODEL_VERSION / SYNTHIFY_MODEL_VERSION (redefined)"
Cohesion: 0.22
Nodes (9): scripts/export_onnx.py (removed), OCTO_MODEL_SHA256 / SYNTHIFY_MODEL_DIGEST (removed), OCTO_MODEL_VERSION / SYNTHIFY_MODEL_VERSION (redefined), migrate-env.py, teams/<team>/onnx/manifest.json, source_model_sha256 Provenance Field, Berts source_revision Field, preflight_production (Env Schema Check) (+1 more)

### Community 244 - "MAX_CONCURRENT_REQUESTS_PER_TEAM=1"
Cohesion: 0.67
Nodes (3): Measured Throughput (~48 letters/min per team), MAX_CONCURRENT_REQUESTS_PER_TEAM=1, Model Service Process-Wide Lock (service.py:206)

### Community 243 - "Markdown Table Extraction Disabled"
Cohesion: 0.67
Nodes (3): Lab Results Coverage Gap (Untested), Markdown Table Extraction Disabled, masking_engine/runtime.py:117-122 Structured Field Path

### Community 230 - "Gateway Routing to Team Model Services"
Cohesion: 0.50
Nodes (4): Gateway Routing to Team Model Services, Octo Team (XLM-R + regex), Synthify Team (GLiNER), Berts Team (int8 ONNX per-language + shared regex)

### Community 245 - "PDF_REDACTION_STYLE (invisible/clinical_soft/bold_chips)"
Cohesion: 0.67
Nodes (3): PDF_REDACTION_STYLE (invisible/clinical_soft/bold_chips), PDF_REDACTION_MODE (detect/pseudonymize), PDF_CHIP_REFLOW (Line/Paragraph Reflow)

### Community 200 - "Production Handoff Requirements"
Cohesion: 0.33
Nodes (6): X-Document-Locale Header Override, Production Handoff Requirements, ENABLED_TEAMS Env Var, /srv/pii-models/releases/<sha>/berts/ Staging Directory, Berts Canaries (nl_BE/en/fr_FR), 11 GiB VPS Blue/Green Memory Constraint

### Community 217 - "Per-Model Language Routing (Berts)"
Cohesion: 0.40
Nodes (5): Per-Model Language Routing (Berts), Berts Per-Locale ONNX Checkpoints (nl_BE/en/fr_FR), BERTS_LANGUAGE_ROUTING=off Rollback, X-Model-Name/Version/Digest Headers, verify-gateway.sh Canary Script

### Community 231 - "MODEL_IDLE_TIMEOUT_SECONDS"
Cohesion: 0.50
Nodes (4): NiFi Contract (MASKING_GATEWAY_BASE_URL), SERVICE_TOKEN, MODEL_IDLE_TIMEOUT_SECONDS, Idle-Unload Memory Verification Procedure

### Community 247 - "vps-ci Runner Tag"
Cohesion: 0.67
Nodes (3): vps-ci Runner Tag, Docker Executor Config (unprivileged, 1 concurrent), gpu Runner (Full-Corpus Job Only)

### Community 248 - "Rootless BuildKit Image Builds"
Cohesion: 0.67
Nodes (3): Rootless BuildKit Image Builds, moby/buildkit:v0.31.2-rootless, --oci-worker-no-process-sandbox Flag

### Community 246 - "vps-docker-gc Systemd Timer"
Cohesion: 0.67
Nodes (3): 2026-08-30 Disk-Full Incident (99% Root Filesystem), vps-docker-gc Systemd Timer, DOCKER_GC_GRACE / DOCKER_GC_ALERT_THRESHOLD

### Community 150 - "leaderboard_api/ arena API"
Cohesion: 0.18
Nodes (12): leaderboard_api/ arena API, ranking.py, results.py, runs.py, objectstore.py, config.py, scripts/rank_teams.py (qa-automation), pipeline.spec.ts (qa-automation) (+4 more)

### Community 191 - "leaderboard-api service"
Cohesion: 0.33
Nodes (7): MLflow fallback when no results prefix populated, leaderboard-api service, leaderboard service (nginx UI), arena bridge network, Arena API runtime dependencies, Evaluation toolchain dependencies, Masking gateway runtime deps (gunicorn, packaging)

### Community 173 - "pii-masking-demo.postman_collection.json"
Cohesion: 0.28
Nodes (9): X-Model-Format header (onnx), detect() adapter interface, Octo Unified-repo detector and regex rules, Synthify GLiNER artifact, Berts int8 ONNX MedRoBERTa model, pii-masking-demo.postman_collection.json, SERVICE_TOKEN collection variable, model_format=onnx collection variable (+1 more)

### Community 192 - "Locale-specific organization surrogate replacement pool"
Cohesion: 0.29
Nodes (7): Platform-owned extraction, pseudonymization, and rendering, English medical-organization surrogate pool, French medical-organization surrogate pool, Dutch medical-organization surrogate pool, French general-organization surrogate pool, Dutch general-organization surrogate pool, Locale-specific organization surrogate replacement pool

### Community 61 - "Berts Model Card"
Cohesion: 0.10
Nodes (27): Berts, Platform Shared Regex Engine, AGE/DATE/PHONE_NUMBER Merge Policy, X-Document-Locale, nl_BE Checkpoint (onnx/), en Checkpoint (onnx-en/), fr_FR Checkpoint (onnx-fr/), BERTS_LANGUAGE_ROUTING (+19 more)

### Community 154 - "Synthify Model Card"
Cohesion: 0.20
Nodes (12): Unified Gateway, Synthify, Synthify GLiNER Detector (adapter.py), Platform Document Pipeline (recursive XY-cut PDF extraction), onnx/ GLiNER export (Git LFS), Synthify Model Card, onnx/model.onnx artifact, GLiNER Checkpoint fine-tuned from urchade/gliner_medium-v2.1 (+4 more)

### Community 136 - "qa-pii-v1 label schema"
Cohesion: 0.20
Nodes (14): qa-pii-v1 label schema, Octo, adapter.py, implementation/ (migrated detector, regex rules, pseudonymization runtime), onnx/ migrated model artifact (45-label classifier head), app/core/label_profiles.json, qa-pii-v1 schema, Octo default regex mode (+6 more)

### Community 21 - "en-GB Ophthalmology Clinical Letter (synthetic)"
Cohesion: 0.23
Nodes (48): en-GB Gastroenterology Clinical Letter (synthetic), en-GB Ophthalmology Clinical Letter (synthetic), en-GB Respiratory Medicine Clinical Letter (synthetic), fr-FR Gastro-entérologie Clinical Letter (synthetic), fr-FR Ophtalmologie Clinical Letter (synthetic), fr-FR Pneumologie Clinical Letter (synthetic), nl-BE Gastro-enterologie Clinical Letter (synthetic), nl-BE Oftalmologie Clinical Letter (synthetic) (+40 more)

### Community 202 - "Dissolving-Tile Brand Mark"
Cohesion: 0.73
Nodes (6): Leaderboard Apple Touch Icon (180px app icon), Leaderboard Favicon 32px, Leaderboard Favicon 48px, Dissolving-Tile Brand Mark, Dark Navy / White Icon Identity, Multi-Resolution Favicon Set

## Ambiguous Edges - Review These
- `Box Clamping at Line-Centre Midpoint` → `Row-Tolerance Reading Order`  [AMBIGUOUS]
  docs/decisions/003-ocr-fallback-for-scanned-pdfs.md · relation: conceptually_related_to
- `Platform Shared Regex Engine` → `Berts Validated Regex Layer`  [AMBIGUOUS]
  teams/berts/docs/model-card.md · relation: conceptually_related_to

## Knowledge Gaps
- **478 isolated node(s):** `$schema`, `title`, `type`, `additionalProperties`, `fixture_id` (+473 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **62 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Box Clamping at Line-Centre Midpoint` and `Row-Tolerance Reading Order`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Platform Shared Regex Engine` and `Berts Validated Regex Layer`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `MaskSpan` connect `MaskSpan` to `MaskingRouter`, `test_pdf_unmaskable_channels.py`, `masking_engine/__init__.py`, `label_converter.py`, `regex_engine.py`, `test_pdf_content_order.py`, `test_regex_rules.py`, `berts/runtime.py`, `prepare_document`, `regex_identifiers.py`, `test_the_audit_records_which_spans_came_from_ocr`, `regex_contact.py`, `_draw_replacements`, `masking_engine/runtime.py`, `test_converter_samples.py`, `span_shape.py`, `test_pdf_line_reflow.py`, `pdf_masking_service.py`, `OnnxTokenClassifierEngine`, `_scan_bytes`, `test_service.py`, `test_date_spans.py`, `synthify/runtime.py`, `test_span_shape.py`, `test_regex_detectors.py`, `masking_router.py`, `PdfGlyph`, `test_the_separator_survives_the_word_it_followed_being_redacted`, `error`, `test_pdf_redaction_corpus.py`?**
  _High betweenness centrality (0.093) - this node is a cross-community bridge._
- **Why does `RankingDocument` connect `ranking.py` to `test_qa_api_end_to_end.py`, `Settings`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Why does `first_records()` connect `OnnxTokenClassifierEngine` to `test_qa_api_end_to_end.py`?**
  _High betweenness centrality (0.075) - this node is a cross-community bridge._
- **Are the 82 inferred relationships involving `MaskSpan` (e.g. with `_assert_every_sample_span_survives()` and `test_a_bare_year_is_replaced_by_a_bare_year()`) actually correct?**
  _`MaskSpan` has 82 INFERRED edges - model-reasoned connections that need verification._
- **Are the 56 inferred relationships involving `prepare_document()` (e.g. with `test_a_line_redrawn_around_a_replacement_comes_back_in_two_pieces()` and `test_a_pdf_value_is_gone_from_the_rendered_text()`) actually correct?**
  _`prepare_document()` has 56 INFERRED edges - model-reasoned connections that need verification._