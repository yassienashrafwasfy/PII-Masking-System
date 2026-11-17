# Repository guide

- `contracts/` is the MLOps-and-QA-reviewed source of truth for the shared API, manifests, and evaluation formats.
- `platform/` is MLOps-owned and contains the unified masking gateway plus NiFi routing, R2 access, and MLflow evaluation boundaries. It contains no live infrastructure configuration.
- `teams/team-N/` is owned by that AI team and contains its model implementation, internal gateway adapter, metadata, tests, safe configuration, and model card. Teams do not expose separate APIs to NiFi.
- `evaluation/` is MLOps-and-QA-owned. It provides the immutable evaluator contract and synthetic fixtures used to compare teams separately.
- `ci/` contains `fast_checks` and `pre_commit` for merge requests, change-gated ML checks, a weekly scheduled Trivy scan, and a manual deploy placeholder. Ordinary jobs require the `vps-ci` Docker runner described in [the VPS runner guide](vps-gitlab-runner.md); fast checks must stay consolidated to preserve the runner budget and do not contact services or infrastructure.

Version a shared contract by adding a version directory. Do not store runtime exports, secrets, weights, real data, or production output here.
