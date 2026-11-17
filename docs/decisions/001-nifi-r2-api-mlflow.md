# ADR 001: NiFi, R2, model API, and MLflow boundaries

**Status:** Accepted

We use NiFi for orchestration, Cloudflare R2 for durable object exchange, one platform-owned masking gateway API, internally owned team model adapters, and MLflow for evaluation tracking. NiFi derives a trusted team identity from the R2 source key and calls only the gateway; the gateway selects the corresponding internal adapter. This separates operational routing from model implementation and makes hand-offs explicit through versioned contracts. Kubeflow pipelines are excluded because shared orchestration is handled by NiFi and the repository is not a workflow-runtime home.
