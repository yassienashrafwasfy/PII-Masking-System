# NiFi platform boundary

NiFi owns R2 detection, trusted team derivation, bounded retries, result writes,
and terminal manifests. The masking API does not trigger NiFi and never receives
R2 credentials. The existing flow should continue to trigger from the trusted R2
source event and call one stable gateway URL.

For the Docker-based NiFi runtime on the same VPS:

1. Confirm the NiFi container can reach the stable bridge endpoint
   `http://172.18.0.1:18080`.
2. Import `configuration/team-api-routing.properties.example` into the production
   parameter context. Store the bearer token as a sensitive parameter.
3. Route only `incoming/octo/...` and `incoming/synthify/...` to masking. Derive
   `team_id` from the key; uploaded attributes cannot override it. Route Berts or
   unknown prefixes to the unsupported/dead-letter relationship.
4. Configure `InvokeHTTP` to POST the FlowFile content directly to `/v1/mask`
   with the required request, idempotency, source, MIME, and team headers. Use a
   5-second connect timeout and 6,600-second read timeout.
5. Allow one in-flight request per team. Retry connection failures, `429`, and
   `5xx` at most three times with the original request and idempotency IDs,
   respecting `Retry-After` with a five-second minimum. Do not retry permanent
   `4xx` responses.
6. Accept output only after validating `X-Request-ID`, `X-API-Version`,
   `X-Model-Name`, `X-Model-Version`, `X-Model-Digest`, `X-Git-SHA`, and
   `X-Image-Digest`. Write the masked object and a `SUCCEEDED` manifest; exhausted
   or permanent failures produce a `FAILED` manifest and dead-letter retention.

For a source `incoming/<team>/<request-id>/<filename>`, write results below
`masked/team=<team>/model=<model-name>/version=<immutable-version>/...`. Never
log FlowFile content, response content, credentials, source filenames, or model
service URLs. Commit only reviewed sanitized flow exports to `flow-exports/`.
