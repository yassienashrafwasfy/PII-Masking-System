#!/usr/bin/env python3
"""Check the production monorepo layout and reject superseded CI scaffolding."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    ".gitlab-ci.yml",
    ".gitlab/CODEOWNERS.example",
    "ci/build.yml",
    "ci/validate.yml",
    "ci/ml.yml",
    "ci/container.yml",
    "ci/security.yml",
    "ci/deploy.yml",
    "deploy/.env.example",
    "deploy/compose.yml",
    "deploy/deploy-gateway.sh",
    "deploy/install-vps.sh",
    "deploy/migrate-env.py",
    "deploy/pii-masking-nginx-control",
    "deploy/promote-staged-bundle.sh",
    "deploy/vps-common.sh",
    "deploy/nginx.conf.example",
    "deploy/verify-gateway.sh",
    "deploy/watchdog-gateway.sh",
    "deploy/systemd/pii-masking-watchdog.service",
    "deploy/systemd/pii-masking-watchdog.timer",
    "deploy/systemd/nginx-docker-ordering.conf",
    "deploy/sudoers/pii-masking-nginx-control",
    "contracts/masking-api/v1.2/openapi.json",
    "contracts/manifests/v1.0/completion-manifest.schema.json",
    "contracts/manifests/v1.0/failure-manifest.schema.json",
    "contracts/evaluation/v1.0/per-file-result.schema.json",
    "docs/architecture.md",
    "docs/decisions/001-nifi-r2-api-mlflow.md",
    "docs/vps-gitlab-runner.md",
    "scripts/check_graphify_hygiene.py",
    "evaluation/scripts/run_evaluation.py",
    "evaluation/scripts/validate_contracts.py",
    "platform/nifi/configuration/r2-and-api.properties.example",
    "platform/nifi/configuration/team-api-routing.properties.example",
    "platform/mlflow/README.md",
    "platform/r2/access-model.md",
    "platform/masking-gateway/Dockerfile",
    "platform/masking-gateway/gunicorn.conf.py",
    "platform/masking-gateway/requirements-runtime.txt",
    "platform/masking-gateway/src/gateway.py",
    "platform/masking-gateway/tests/test_gateway.py",
    "platform/model-service/Dockerfile",
    "platform/model-service/src/service.py",
    "pyproject.toml",
    "uv.lock",
    ".pre-commit-config.yaml",
    "tests/test_repository_layout.py",
]
LEGACY = [
    "pipelines",
    "src",
    "configs",
    ".gitlab/ci",
    "ci/gateway-test-requirements.txt",
    "ci/team.yml",
    "ci/evaluation.yml",
]

missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
present = [path for path in LEGACY if (ROOT / path).exists()]
if missing or present:
    raise SystemExit(f"Missing: {missing}; obsolete paths present: {present}")
print("Repository layout is valid.")
