# NiFi operation

NiFi remains the orchestrator: an R2 source event triggers the existing flow,
and the flow calls the shared private gateway. The API does not start a NiFi
process group and does not read or write R2.

Use a parameter context or external secret provider for R2 and gateway
credentials. On the NiFi VPS, keep
`MASKING_GATEWAY_BASE_URL=http://172.18.0.1:18080`. Derive `team_id`
only from `incoming/octo/...` or `incoming/synthify/...`; route Berts and unknown
prefixes to the unsupported/dead-letter path for this release.

`InvokeHTTP` sends raw FlowFile bytes with the v1 contract headers. Configure a
5-second connect timeout, a 6,600-second read timeout, and one in-flight request
per team. Retry connection failures, `429`, and `5xx` at most three times using
the original request and idempotency IDs. Respect `Retry-After`, never retry
permanent `4xx`, and validate all immutable response identity headers before
writing masked output and the terminal manifest.

Logs, bulletins, provenance attributes, and alerts must exclude document content,
credentials, filenames, and extracted PII. Export only reviewed, sanitized flow
definitions to `platform/nifi/flow-exports/`; never commit runtime state or
controller-service credentials.
