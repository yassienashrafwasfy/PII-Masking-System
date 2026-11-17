# Processing manifests v1.0

Every NiFi submission produces exactly one terminal manifest: `completed` or `failed`. Manifests contain object keys and operational metadata only; they must never contain source text or detected PII.
