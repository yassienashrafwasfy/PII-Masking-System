# Current HTTP endpoints

This report describes the endpoints implemented in the current source tree. It covers the public masking gateway, the private per-team model service, and the arena API that backs the leaderboard and the masking console.

## Quick map

| Surface | Exposure in the supplied Compose files | Endpoints |
|---|---|---|
| Masking gateway | Local Compose port `8080` on every interface; production `127.0.0.1:<blue-or-green-port>` behind private Nginx on `18080` | `GET /health`, `GET /ready`, `GET /version`, `POST /v1/mask` |
| Team model service | No published port. Local Compose attaches it to `models` and `public`; production to `models` and `egress` | `GET /health`, `GET /ready`, `GET /version`, `POST /mask` |
| Arena API | Internal `arena` network; `/api/*` is proxied through the arena UI on `127.0.0.1:8088` | `GET /health`, `GET /api/v1/config`, `GET /api/v1/leaderboard`, `GET /api/v1/samples`, `POST /api/v1/runs`, `GET /api/v1/runs`, `GET /api/v1/runs/{run_id}`, `GET /api/v1/runs/{run_id}/content/{side}` |

The masking request flow is:

`NiFi -> POST /v1/mask on gateway -> POST /mask on selected team service -> masked bytes back to NiFi`

## Endpoint template

Each endpoint below uses the same fields:

- **Exposure:** who can reach it in the supplied deployment.
- **Purpose:** why it exists.
- **Request:** method, inputs, and authentication.
- **Behavior:** what the implementation does.
- **Success:** status, body, and important headers.
- **Errors:** endpoint-specific failure behavior.

## Masking gateway

Source: `platform/masking-gateway/src/gateway.py`. Gunicorn binds `0.0.0.0:8080` with one worker and eight threads (`gunicorn.conf.py:19-25`). One worker is load-bearing: the concurrency semaphores are in-process memory, so a second worker would multiply the per-team limit.

### `GET /health`

- **Exposure:** Public gateway endpoint.
- **Purpose:** Liveness check for the gateway process.
- **Request:** No body, headers, or authentication required.
- **Behavior:** Returns immediately. It does not check a team model service.
- **Success:** `200 OK`, `Content-Type: application/json`, body `{"status":"UP"}`. Also returns `X-Git-SHA` and `X-Image-Digest`, each `unknown` when the environment variable is unset. This is the only gateway route that does not return `X-Request-ID`.
- **Errors:** No endpoint-specific error path.

### `GET /ready`

- **Exposure:** Public gateway endpoint.
- **Purpose:** Readiness check for a selected team model.
- **Request:** Optional `X-Team-ID` and `X-Model-Format: onnx`. They default to `DEFAULT_TEAM_ID` then `octo`, and `onnx`, respectively. No authentication required.
- **Behavior:** Validates the team and format, calls that private service's `GET /ready` for the selected runtime, and accepts only a `200` JSON response. Only `X-Model-Format` is forwarded upstream; `X-Request-ID` is not.
- **Success:** `200 OK`, `Content-Type: application/json`, with the upstream response body unchanged. The current model service returns `{"status":"READY","model_loaded":true}`.
- **Errors:** `400 Unsupported team.` for an unknown team, `400 X-Model-Format must be onnx.` for a bad format. Every other failure — missing model URL, unreachable, timeout, non-`200`, or non-JSON — returns `503 Model is not ready.` with `Retry-After: 5`. The upstream's own reason never reaches the caller.

### `GET /version`

- **Exposure:** Public gateway endpoint.
- **Purpose:** Report the deployed identity and capabilities of a selected team model service.
- **Request:** Optional `X-Team-ID` and `X-Model-Format`. They use the same defaults as `/ready`. No authentication required.
- **Behavior:** Validates the team and format, calls that private service's `GET /version`, and accepts only a `200` JSON response.
- **Success:** `200 OK`, `Content-Type: application/json`, with the upstream identity JSON unchanged. The body carries the thirteen keys listed under the model service's `/version` below, including `max_file_bytes` at 10 MiB.
- **Errors:** Same set as `/ready`: `400` for an unsupported team or format, otherwise `503 Model is not ready.` with `Retry-After: 5`.

### `POST /v1/mask`

- **Exposure:** Public gateway endpoint and the main NiFi integration point.
- **Purpose:** Mask one file with the model selected by `X-Team-ID`.
- **Request:** Raw file bytes, not multipart or a JSON wrapper. Requires `Authorization: Bearer <SERVICE_TOKEN>`, `X-Request-ID`, `Idempotency-Key`, `X-Team-ID`, `X-Source-Object-Key`, `X-Source-ETag`, `Content-Type`, `Accept`, and `Content-Length`. Optional `X-Model-Format: onnx` selects the artifact per request; `onnx` is the only accepted value and the default, so a caller normally omits it. Optional `X-Mask-Labels` narrows masking to the named labels and leaves every other detected label readable; absent means the full taxonomy, and an unknown label is `400`. Optional `X-Document-Locale: nl_BE|fr_FR|en` sets the language every pseudonym reads as; absent means it is detected from the document's own text, and an unknown locale is `400`. Supported teams are `octo`, `synthify`, and `berts`. The gateway accepts PDF, plain text, CSV, and JSON, but a team may accept fewer — see the per-team table below. The body must be 1 byte to 10 MiB.
- **Behavior:** Validates in this order: route, credential, request metadata, team-to-source-key binding, source ETag, media type, `Accept`, body, model format, then the concurrency slot. The source key must start with `incoming/<team-id>/`; the check is a prefix test and does not constrain what follows, so a deeper key such as `incoming/octo/requests/routed/x.pdf` is accepted (see `document-subsets.md`). It then forwards the bytes to that team's private `POST /mask`. Production permits one active request per team by default, so Octo and Synthify can run together while excess same-team work receives `429`. The gateway does not mask content itself.
- **Success:** `200 OK` with the non-empty masked bytes in the original media type. It returns `X-Request-ID`, `X-API-Version: v1`, `X-Model-Format`, and service, Git, image, model name, model version, and model digest headers supplied by the model service. `X-Document-Locale` comes back only when the request carried it — a detected locale is not reported, because detection happens in the model service behind the gateway.
- **Errors:** `400` for missing or inconsistent metadata, a team that does not match the source key, a missing or mismatched `Content-Length`, an empty body, an `Accept` that excludes the media type, an invalid model format, or any other path or method; `401` for an invalid credential; `413` above 10 MiB; `415` for a media type this release does not support; `422` when the document cannot be masked; `429` when the selected team's request slot is busy; `502` for an empty, wrongly typed, or incompletely identified upstream success response; `503` when the selected service is unavailable; and `500` for an unexpected gateway exception. Errors use sanitized JSON and echo a safe `X-Request-ID`. `429` and `503` include `Retry-After: 5`; `500`, `502`, and `504` do not, although the error body marks them retryable.

Per-team acceptance, which the gateway does not enforce:

| Team | Media types | Model formats |
|---|---|---|
| `octo` | PDF, text, CSV, JSON | `onnx` only |
| `synthify` | PDF, text | `onnx` only |
| `berts` | PDF, text | `onnx` only |

Implementation notes:

- `Idempotency-Key` is required but is not stored or compared by the gateway. A retry is recomputed; safety currently depends on masking having no external side effects.
- Unknown routes and unsupported methods return `400 Invalid masking request`, not `404` or `405`.
- An empty or unset `SERVICE_TOKEN` rejects every masking request with `401`, because the comparison requires a non-empty token.
- A request the gateway allows but the team does not is rejected one layer later and rewritten: CSV to Synthify passes the gateway and comes back as `415`. So the same client mistake produces a different message depending on the team. A bad `X-Model-Format` is no longer such a case -- `onnx` is the only value any team supplies, so the gateway rejects everything else itself with `400`.
- `MAX_CONCURRENT_REQUESTS_PER_TEAM` is read once at import. A value below 1 or a non-integer fails container startup rather than a request.
- Berts is deployed by the `berts` Compose profile. It uses the int8 ONNX artifact. In `deploy/compose.yml`, `BERTS_MODEL_URL` is empty unless the deploy script enables that team, so `X-Team-ID: berts` otherwise returns `503`.

## Private team model service

Source: `platform/model-service/src/service.py`. One shared WSGI application loads the adapter selected by `TEAM_ID`; supported values are `octo`, `synthify`, and `berts`. It binds `0.0.0.0:8080` using the same Gunicorn config as the gateway, with two threads in production. No Compose file publishes a host port for it. Local Compose attaches each model container to both `models` and `public`; production attaches them to `models` and an outbound-only `egress` network and never to `edge`.

### `GET /health`

- **Exposure:** Private model network.
- **Purpose:** Liveness check for the model-service process.
- **Request:** No body, headers, or authentication required.
- **Behavior:** Returns immediately. It does not load or inspect the model.
- **Success:** `200 OK`, `Content-Type: application/json`, body `{"status":"UP"}`. Unlike the gateway's `/health`, it returns no build headers.
- **Errors:** No endpoint-specific error path.

### `GET /ready`

- **Exposure:** Private model network.
- **Purpose:** State whether the selected adapter's model is loaded and ready.
- **Request:** No body or authentication required. Optional `X-Model-Format: onnx` selects the runtime and defaults to ONNX; `X-Request-ID` is echoed on errors.
- **Behavior:** Calls the configured team's `ready(format)` adapter function.
- **Success:** `200 OK`, `Content-Type: application/json`, body `{"status":"READY","model_loaded":true}`.
- **Errors:** `400 X-Model-Format must be onnx.` for an unrecognized value. `400 The selected model format is not available.` is still returned for a valid format a team does not supply, but no such format exists today: every team supplies `onnx` and the platform accepts nothing else. `503 Model is not ready.` when `ready()` is false or raises.

### `GET /version`

- **Exposure:** Private model network.
- **Purpose:** Return the service and model release identity used by the gateway.
- **Request:** No body or authentication required. Optional `X-Model-Format: onnx` selects the release identity and defaults to ONNX; `X-Request-ID` is echoed on errors.
- **Behavior:** Reads the selected adapter release identity and supported media types, plus release values from `SERVICE_RELEASE`, `GIT_SHA`, and `IMAGE_DIGEST`.
- **Success:** `200 OK` JSON with fifteen keys: `service`, `api_contract_version`, `service_release`, `git_sha`, `image_digest`, `model_name`, `model_version`, `model_digest`, `model_format`, `model_file_size_mb`, `selectable_labels`, `default_masked_labels`, `supported_mime_types`, `supported_model_formats`, and `max_file_bytes`. `selectable_labels` is what a caller may name in `X-Mask-Labels`, and `default_masked_labels` is what it gets when it names none; see `document-subsets.md`. `model_file_size_mb` is `null` when the artifact cannot be stat'ed, which consumers must read as "not reported" rather than as a size of zero.
- **Errors:** The same two `400`s as `/ready`, then `503 Model is not ready.` if identity generation fails.

### `POST /mask`

- **Exposure:** Private model network; called by the gateway, not NiFi.
- **Purpose:** Detect PII in one document and return a redacted document.
- **Request:** Raw bytes with `X-Request-ID`, `Content-Type`, `Content-Length`, `X-Source-Object-Key`, and optional `X-Model-Format: onnx` defaulting to ONNX. The media type must be supported by the active team adapter, and the body must be 1 byte to 10 MiB. There is no application-level credential check; isolation is provided by the internal network. The gateway also sends `Accept` and `X-Source-ETag`, which this service ignores.
- **Behavior:** Parses the document through the shared platform pipeline, calls the team's detector for each extracted text segment, drops a small blocklist of common words misclassified as names, and redacts the detected spans. `X-Source-Object-Key` is used only for its basename, which names the best-effort audit object. Model access is serialized with one lock, so a second admitted request waits rather than being refused. Idle unloading is controlled by `MODEL_IDLE_TIMEOUT_SECONDS`: local and demo behavior defaults to 120 seconds, while production sets zero so deployment and watchdog canaries keep the model ready.
- **Success:** `200 OK` with masked bytes and the input `Content-Type`, plus eight headers — `X-Model-Name`, `X-Model-Version`, `X-Model-Digest`, `X-Model-Format`, `X-Service-Release`, `X-Git-SHA`, and `X-Image-Digest`. These are exactly the set the gateway requires before returning `200`. `X-Request-ID` appears on errors but not on success.
- **Errors:** `400` for missing metadata, a missing or mismatched `Content-Length`, an empty body, an absent `X-Source-Object-Key`, or an unavailable model format; `413 Payload exceeds the model limit.` above 10 MiB; `415` for a media type this team does not accept; `422` for corrupt, unsafe, or empty masking output; and `500` for unexpected failures. All errors use sanitized JSON with code `MODEL_SERVICE_ERROR` and never set `Retry-After`.

Implementation note: any unsupported route or method returns `400 Invalid model request`, not `404` or `405`.

## Arena API

Source: `evaluation/leaderboard_api/`. Uvicorn listens on port `8080` inside the `arena` network. Nginx exposes `/api/*` through the arena UI at host address `127.0.0.1:8088`; it does not proxy `/health`, `/docs`, `/redoc`, or `/openapi.json`.

The API has two responsibilities: serving the leaderboard from the QA result documents published to object storage, and driving interactive masking runs through the production pipeline. It never masks content and never calls the gateway; per `contracts/masking-api/v1.2` the masking API performs no object-storage I/O, and NiFi owns routing, the request ID, and the destination key.

### `GET /health`

- **Exposure:** Directly available inside the `arena` network. Used by the API container health check.
- **Purpose:** Liveness check for the arena API.
- **Request:** No parameters or authentication.
- **Success:** `200 OK` JSON with `status: "ok"` and an ISO-formatted `time`.

### `GET /api/v1/config`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** Tell the frontend which features this deployment can actually offer, so the console disables itself instead of failing at request time.
- **Request:** No parameters or authentication.
- **Success:** `200 OK` JSON with `teams`, `storage_configured`, `max_upload_bytes`, `allowed_content_types`, `run_timeout_seconds`, `mock_leaderboard`, `samples_available`, `champion_fallback_team`, a `paths` object (`source_bucket`, `incoming_prefix`, `masked_bucket`, `masked_prefix`, `manifest_prefix`, `results_bucket`, `results_prefix`), and a `ranking` object (`basis`, `mode`, `pinned_key`). No credentials are returned. `samples_available` reports only that a sample directory is configured, so it can be true while the sample list is empty.

### `GET /api/v1/leaderboard`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** Supply ranked model metrics and category medals to the frontend.
- **Request:** Optional string query parameter `round`; default `benchmark-v1-2026-08`. No authentication.
- **Behavior:** Five sources, tried in order. A `mock_leaderboard` deployment short-circuits to randomized preview data before storage is even consulted. Otherwise, when object storage is configured, it reads QA's cross-team ranking document first (`source: ranking`, cached for 60 seconds and capped at 40 documents per rebuild), then falls back to parsing every `*.json` under `ARENA_RESULTS_PREFIX` as `qa-result.schema.json` v1.0 (`source: results`). Failing that it tries MLflow when `MLFLOW_TRACKING_URI` is set, and finally five hard-coded demo models.
- **Success:** `200 OK` JSON. `round` is an object with `id`, `label`, `period`, and `policy`. Alongside it are `updated_at`, the published `weights` and `gates`, `source` (`mock`, `ranking`, `results`, `mlflow`, or `demo`), `models`, `awards`, `champion` (the first eligible row, or `null`), and `diagnostics`. Each model in the mock, ranking, and results paths carries full `{available, value, reason}` metric envelopes alongside flat `recall`/`precision`/`f1`/`p95` fields for the table; the `mlflow` and `demo` paths emit flat rows only and no `diagnostics.results_source`.
- **Errors:** None. This endpoint raises no HTTP error: a storage or parse failure on one document skips it and reports it under `diagnostics.skipped`, and a total failure degrades to the next source with the reason in `diagnostics.error` rather than returning `500`.

### `GET /api/v1/samples`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** List server-side documents the console can offer as one-click uploads. Browsers do not let a page choose which folder a file dialog opens to, so listing a configured folder is the only way to give the console a ready-to-use default.
- **Request:** No parameters or authentication.
- **Behavior:** Lists supported files directly inside `ARENA_SAMPLE_DIRECTORY`. Off by default; both shipped Compose files point it at `/app/samples`, which the API image bakes in. Only regular files in that one directory are exposed — no subfolders, no symlinks, and no path traversal through a crafted name.
- **Success:** `200 OK` JSON `{"samples": [{"name", "size", "content_type"}, ...]}`. An unset or missing directory returns an empty list, not an error. Unreadable files and unsupported extensions are skipped silently.
- **Errors:** None.

### `POST /api/v1/runs`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** Upload one document to the source prefix so NiFi picks it up.
- **Request:** `multipart/form-data` with a required `team` and exactly one of `file` or `sample`. `sample` names a document from `GET /api/v1/samples`, which the API reads server-side and submits through the same path a browser upload uses. No authentication.
- **Behavior:** Reads the sample or the upload, guessing a missing or generic content type from the file extension. It then validates the team against `ARENA_TEAMS`, the media type against the gateway's supported set, and the size against `ARENA_MAX_UPLOAD_BYTES`; sanitizes the filename; and writes `incoming/<team>/requests/<run-id>-<filename>` into the `source` bucket, which is the prefix NiFi watches. The layout is set by `ARENA_INCOMING_KEY_TEMPLATE`, and a template that would escape `incoming/<team>/` or drop the filename is refused before anything is uploaded.
- **Success:** `200 OK` with the run record, including the source bucket, key, and ETag.
- **Errors:** `400` for supplying both `file` and `sample` or neither, an unsupported team or media type, an empty file, one above the limit, or a bad key template; `404` for an unknown, unsupported, or disallowed sample name; `422` from FastAPI when `team` is absent; `503` when object storage is not configured; `502` when the upload itself fails.

### `GET /api/v1/runs/{run_id}`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** Report the current state of a run, advancing it by inspecting object storage.
- **Behavior:** NiFi derives its request ID as a UUID5 over source bucket, key, and ETag using a namespace this repository does not define, so the destination key cannot be predicted. The run is resolved by discovery instead: the API searches the manifest prefix in the `destination` bucket for a terminal manifest whose recorded source key matches the uploaded key, and reads the masked key from it. The live layout, `manifests/team=<id>/request=<id>/status.json`, is searched first. Both manifest vocabularies are accepted — the schema's `completed`/`correlation_id`/`source_object_key`/`masked_object_key` and the contract prose's `SUCCEEDED`/`request_id`/`source_key`/`destination_key`. If no manifest is visible, it falls back to matching a masked object by filename plus either the run id or an upload-time comparison. A run with no result after `ARENA_RUN_TIMEOUT_SECONDS` becomes `timeout`.
- **Success:** `200 OK` with `status` in `uploaded`, `processing`, `completed`, `failed`, or `timeout`, plus the source and masked locations, manifest key, correlation id, model version, timing, how the result was discovered, and a status history.
- **Errors:** `404` for an unknown run; `503` when object storage is not configured.

### `GET /api/v1/runs`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** List recently submitted runs, newest first.
- **Request:** Optional `limit` between 1 and 100; default 25. A value outside that range returns FastAPI's `422`.
- **Behavior:** Runs are tracked in process and capped; the durable record of any submission is the manifest in object storage, so a restart loses the list but not the results.

### `GET /api/v1/runs/{run_id}/content/{side}`

- **Exposure:** Proxied through the arena UI.
- **Purpose:** Serve the original or masked document for the side-by-side view.
- **Request:** `side` is `original` or `masked`. No authentication.
- **Behavior:** Streams the bytes through the API rather than issuing presigned URLs, so storage credentials stay server-side and the browser needs no bucket CORS rules. Responses set `Content-Disposition: inline`, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`.
- **Errors:** `404` for an unknown run or an unrecognized side; `409` when the masked document does not exist yet; `502` when the object cannot be read; `503` when object storage is not configured.

Implementation notes:

- CORS permits `GET` and `POST` from `LEADERBOARD_ALLOWED_ORIGINS`, defaulting to `http://localhost:5173` in source, `http://localhost:8088` in the supplied Compose deployment, and the configured domain in the VPS arena deployment. The value is split on commas without trimming, so an entry written with a space after the comma never matches.
- The nginx `/api/` location raises `client_max_body_size` to 12 MiB; its 1 MiB default would reject uploads at the gateway's 10 MiB ceiling.
- The console renders the uploaded original, which contains unmasked content. It has no access control, so the supplied Compose deployment binds it to `127.0.0.1`.
- `history.py` archives QA's ranking documents so trend lines survive, because QA overwrites the same object key on every run and the bucket has no object versioning. It backs the `trend` and `run_count` fields inside each leaderboard row; there is no history endpoint.

## FastAPI-generated endpoints

Because the arena app uses FastAPI defaults, it also serves these framework-generated routes on the API container:

| Method | Path | Purpose |
|---|---|---|
| `GET`, `HEAD` | `/openapi.json` | Generated OpenAPI schema |
| `GET`, `HEAD` | `/docs` | Swagger UI |
| `GET`, `HEAD` | `/docs/oauth2-redirect` | Swagger UI OAuth redirect helper |
| `GET`, `HEAD` | `/redoc` | ReDoc UI |

These routes are not proxied by the supplied Nginx `/api/` location, so they are available only to clients that can reach the leaderboard API container directly. Starlette also answers `HEAD` on every arena `GET` route above.

## Production port topology

The gateway is not published on `18080` directly. Nginx owns that port and proxies to whichever container is live:

```text
NiFi -> 18080 (Nginx, on 127.0.0.1 and the NiFi Docker bridge IP)
          -> 127.0.0.1:18081 (blue) or 127.0.0.1:18082 (green)
             -> gateway container port 8080
```

`deploy/compose.yml` publishes the gateway as `127.0.0.1:${GATEWAY_PORT}:8080`, where the deploy script sets `GATEWAY_PORT` to the blue or green candidate. `PRIVATE_GATEWAY_PORT` is pinned to `18080`, and the install and deploy scripts fail if it is anything else. Nginx proxies every path, not a filtered set, and applies `client_max_body_size 10m` with a 5 second connect timeout and 6,600 second read and send timeouts, unbuffered in both directions.

## Sources checked

- `platform/masking-gateway/src/gateway.py`
- `platform/model-service/src/service.py`
- `platform/common/http.py`
- `platform/masking-gateway/gunicorn.conf.py`
- `teams/{octo,synthify,berts}/adapter.py` and `runtime.py`
- `evaluation/leaderboard_api/` (`main.py`, `config.py`, `objectstore.py`, `results.py`, `runs.py`, `samples.py`, `ranking.py`, `history.py`, `mock_data.py`)
- `compose.yaml`
- `deploy/compose.yml`, `deploy/nginx.conf.example`, `deploy/.env.example`, `deploy/vps-common.sh`
- `evaluation/docker-compose.yml`
- `evaluation/leaderboard/nginx.conf`
- `contracts/masking-api/v1.5/openapi.json`
- Gateway and model-service contract tests
