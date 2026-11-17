# MLflow evaluation

QA runs the same immutable validation dataset and evaluator version against each team service separately. MLflow creates one child run for every `(team_id, immutable_model_version)` pair. Log only safe aggregate metrics, dataset/evaluator hashes, durations, contract versions, and evaluation artifact references. Never log raw PII, production masked output, URLs, tokens, or Cloudflare credentials. Production-file tracking stays in R2 terminal manifests and NiFi provenance.
