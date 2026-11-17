# CI production promotion

Production promotion is deliberately split between a merge-request verification pipeline and a default-branch promotion pipeline. A merge request never receives protected deployment variables and cannot contact the VPS.

## Flow

1. Every merge request runs build, validation, and change-selected model checks. A release-relevant change also publishes the two immutable SHA-tagged images and scans their digest references.
2. After both publish and scan jobs succeed, `record_production_candidate` writes exactly six fields to `production-candidate.env` and uploads it to the GitLab Generic Package Registry at the merge commit SHA. It uses only `CI_JOB_TOKEN`; it does not receive deployment credentials.
3. A release-relevant merge to the protected default branch starts a pipeline containing only `load_production_candidate`, `preflight_production`, and `deploy_production`. `load_production_candidate` downloads the manifest for the exact `CI_COMMIT_SHA`, validates its keys, SHA, registry namespace, and digest formats, then passes the validated values as a dotenv artifact.
4. `preflight_production` is a blocking manual job. It stages the already-verified bundle and runs `deploy-gateway.sh preflight`; it does not promote files or change the live service.
5. `deploy_production` is a separate blocking manual job and requires the successful preflight. It alone promotes the staged bundle and runs `deploy-gateway.sh apply` using the candidate image digests.

Neither manual job starts automatically. Both retain `resource_group: production`; protected deployment variables remain available only in protected default-branch pipelines.

The merge policy must preserve the verified commit SHA on main (for example, fast-forward/rebase merging). If merge commits or squash commits produce a different SHA, run the candidate-producing MR pipeline against the merge result so the recorded `CANDIDATE_SHA` is the main commit SHA. Otherwise `load_production_candidate` fails closed.

## Two lanes

Change detection is split into two independent lanes, defined as the `*platform_paths` and `*arena_paths` anchors at the top of `.gitlab-ci.yml`.

- **Platform lane** — the gateway, the model service, `teams/` (adapters *and* the ONNX artifacts baked into the model-service image), the root dependency set, and everything under `deploy/` that `preflight_production` ships to the gateway VPS. This lane produces the production candidate.
- **Arena lane** — `evaluation/leaderboard`, `evaluation/leaderboard_api`, `evaluation/config`, and `deploy/mlflow-vps/`. This lane produces the two arena images and deploys them to the MLflow VPS.

The lanes share no code, no dependency set, no image content and no host, so a change to one runs none of the other's jobs. `deploy/vps-common.sh` is in both, because both deploy paths ship it. `.gitlab-ci.yml` and `ci/**/*.yml` are in both, so any CI edit exercises the whole pipeline.

Jobs bound by `needs:` are always scoped by the *same* rule template — `.main_promotion_rules`, `.production_deploy_rules`, `.arena_image_rules`, `.arena_scan_rules`, `.arena_deploy_rules`. This is a correctness requirement, not tidiness: a job whose `needs:` target is excluded from the pipeline makes the whole configuration invalid and GitLab then refuses to create the pipeline at all. `tests/test_ci_production_promotion.py` simulates job selection across every context and fails if any lane splits apart.

## Pipeline matrix

| Event | Expected jobs |
| --- | --- |
| Platform-lane MR | Build/validate/evaluate jobs as selected, both image publishes, both image scans, `qa_release_gate`, then `record_production_candidate`; no arena jobs, no production jobs |
| Arena-lane MR | Build/validate jobs, plus `publish_arena_api` and `publish_arena_ui` built but not pushed; no gateway or model-service build, no image scan, no QA gate, no candidate |
| MR touching both lanes | The union of the two rows above |
| Merge of a platform-lane MR to main | `load_production_candidate`, advisory `qa_arena_evaluation`, then manual blocking `preflight_production` and `deploy_production`; no verification, build, package, scan, or arena jobs |
| Merge of an arena-lane MR to main | Both arena publishes (pushed), both arena scans, then manual `preflight_arena_mlflow` and `deploy_arena_mlflow`; no `load_production_candidate` and no production jobs — the arena records no candidate, so loading one would fail closed on a 404 |
| Docs-only MR / merge | MR build/validate checks may run; no image package, scan, or candidate job. The docs-only main push creates no pipeline and consumes no VPS runner capacity |
| Direct platform-lane main push without a candidate | `load_production_candidate` fails while downloading or validating the manifest. The manual production jobs remain unavailable, and production is untouched |

Scheduled pipelines remain limited to `repository_security` when `SECURITY_SCAN=weekly`. Ordinary feature-branch push pipelines are suppressed, including duplicate push pipelines once a merge request exists.

### The web pipeline is the escape hatch

Every lane filter applies to the `push` arm only. A manually started (`web`) pipeline on main carries no `changes:` filter anywhere, so it builds both arena images and offers every manual deploy regardless of what the last commit touched. Use it to redeploy either target from a commit whose own push pipeline did not include that lane. It still fails closed: `load_production_candidate` requires a verified candidate manifest for the commit.

## Rollback and retention

Application rollback remains the deployment gateway's blue/green rollback behavior: a failed apply restores the last healthy slot. To roll forward or deliberately re-promote, use a protected main pipeline only for a commit that has a verified candidate manifest; do not rebuild images on main.

Keep the most recent 30 `production-candidate` Generic Package manifests, deleting older versions only after their associated deployment/audit retention window. Configure the GitLab project to reject duplicate Generic package files for the `production-candidate` package/version. That makes a candidate immutable and prevents a retry from silently replacing the manifest for an existing commit SHA.
