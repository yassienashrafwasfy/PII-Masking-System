# Codebase graph

```mermaid
flowchart LR
  R2[(R2 source object)] --> NIFI[NiFi]
  NIFI -->|derive trusted team_id<br/>call /v1/mask| GW[Masking Gateway]
  GW -->|validate contract, token,<br/>source-key/team binding| ADAPTERS

  subgraph ADAPTERS[Team-owned adapters]
    OCTO[teams/octo/adapter.py]
    SYN[teams/synthify/adapter.py]
    BERTS[teams/berts/adapter.py]
  end

  OCTO --> ENGINE
  SYN --> ENGINE
  BERTS --> ENGINE

  subgraph ENGINE[Shared masking engine]
    RT[runtime.py]
    DETECT[regex engine + model detector]
    ORCH[orchestrator]
    CONVERT[label converter]
    ROUTER[masking router]
    FORMAT[TXT / JSON / PDF services]
    RT --> DETECT --> ORCH --> CONVERT --> ROUTER --> FORMAT
  end

  FORMAT -->|masked bytes + safe metadata| GW
  GW --> NIFI
  NIFI --> R2OUT[(R2 masked object + terminal manifest)]

  CONTRACT[contracts/masking-api/v1.4<br/>OpenAPI, examples] -.defines.-> GW
  TEAMCFG[teams/*/config + model artifacts] -.configures.-> ADAPTERS
```

```mermaid
flowchart LR
  DATA[Generated synthetic PDFs + manifest] --> BENCH[evaluator/run_benchmark.py]
  BENCH -->|HTTP requests| GW[Masking Gateway]
  GW --> RESULT[Per-file scores and latency]
  RESULT --> METRICS[evaluator/metrics.py]
  METRICS --> MLFLOW[(MLflow runs)]
  MLFLOW -.fallback source.-> API[leaderboard_api]
  QARESULT[(QA result documents in results/)] --> API
  API --> WEB[React/Vite arena]

  UPLOAD[Masking console upload] --> INCOMING[(incoming/ prefix)]
  INCOMING --> NIFI[NiFi]
  NIFI -->|X-Team-ID from the key| GW
  NIFI --> MASKED[(masked/ prefix)]
  NIFI --> MANIFEST[(terminal manifest)]
  MANIFEST -.associates the run.-> API
  MASKED --> WEB

  POLICY[entities.yaml + thresholds.yaml + scoring-policy.yaml] -.controls.-> BENCH
  POLICY -.gates eligibility.-> API
  VALIDATE[evaluation/scripts/validate_contracts.py] -.validates.-> CONTRACT[Evaluation + manifest JSON schemas]
```

```mermaid
flowchart TB
  CI --> VALIDATE[ci/validate.yml]
  CI --> ML[ci/ml.yml]
  CI --> SECURITY[ci/security.yml]
  CI --> DEPLOY[ci/deploy.yml]

  VALIDATE --> FAST[fast_checks]
  VALIDATE --> PRE[pre_commit]
  FAST --> REPO[scripts/validate_repository.py]
  FAST --> CONTRACTS[evaluation/scripts/validate_contracts.py]
  FAST --> TEAMTESTS[repository layout + gateway tests]
  FAST --> EVALTESTS[evaluation/tests]
  ML --> MLCHECKS[model_artifacts + ml_smoke_cpu]
  SECURITY --> TRIVY[weekly trivy_scan]
  DEPLOY --> PLACEHOLDER[manual deploy_placeholder]
```

## Key ownership boundaries

| Area | Primary contents | Responsibility |
| --- | --- | --- |
| `contracts/` | API and terminal-manifest schemas | Stable integration contracts |
| `platform/masking-gateway/` | WSGI gateway and masking engine | External API, trust boundary, content masking |
| `platform/nifi/`, `platform/r2/`, `platform/mlflow/` | Operational conventions | Routing, storage, experiment tracking |
| `teams/` | Adapters, models, configs, tests, model cards | Independently evolved model implementations |
| `evaluation/` | Synthetic data, benchmark, scoring, leaderboard, masking console | Offline model comparison, presentation, and interactive pipeline demos |
| `ci/` | GitLab pipeline fragments | Fast validation, ML tiers, scheduled security, and manual deployment placeholder |
