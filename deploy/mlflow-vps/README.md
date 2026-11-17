# Arena on the MLflow VPS

Deploys the leaderboard and masking console publicly at
`https://leaderboard.mlflow.me`, behind the Traefik instance the
`mlflow-auth-gui` project already runs on this host. This is a different
target from the (superseded, never-applied) NiFi-VPS design described in
`../README.md`: that one was SSH-tunnel-only and fully private; this one is
public, with only the masking console's API paths gated.

This deployment never edits `mlflow-auth-gui`'s own compose files. It is an
independent Compose project that joins the same external Docker network
Traefik already watches, and Traefik's Docker-provider label discovery does
the rest -- the same mechanism already routing `mlflow.me` itself.

## The console is not gated by the proxy

The leaderboard and the masking console are one single-page app sharing a
browser-side hash route (`#/console`). The server never sees that fragment --
`/#/console` and `/#/arena` both send the identical `GET /` -- so a reverse
proxy cannot gate "the console page" by path at all.

An earlier design gated the API calls the console needs instead
(`/api/v1/runs*` and `/api/v1/samples`) behind a Traefik `basicauth`
middleware on a second, higher-priority router. That has been removed. In a
browser it surfaced as a sign-in dialog the moment anyone uploaded a document,
which was never the intended experience, and the apr1 hash behind it had to
survive both Docker Compose's `.env` parser and bash's `$$` expansion intact
to authenticate at all -- a step that broke repeatedly and silently.

`arena-compose.yml` now declares a single Traefik router for the whole host.
What bounds the console is in the API rather than the proxy: it accepts only
the content types in `runs.ALLOWED_CONTENT_TYPES`, caps uploads at
`ARENA_MAX_UPLOAD_BYTES`, and writes only under the prefixes of the teams
listed in `ARENA_TEAMS`. The page carries a visible warning that uploads land
in the shared source bucket.

## Generating the .env file

Run `./create-env.sh` on the VPS. It prompts for everything below, hides
secret input, writes the file at mode `0600`, and refuses to overwrite an
existing one:

```bash
./create-env.sh /path/to/.env
```

It does not know your R2 credentials -- it only prompts for them and writes
what you type.

## Prerequisites

1. **DNS**: `leaderboard.mlflow.me` must already resolve to this VPS's public
   IP. Certificate issuance fails otherwise.
2. **A dedicated R2 credential** -- see the comment in `.env.example`. Not the
   QA dataset token, not `SERVICE_TOKEN`.
3. **GitLab CI/CD variables** for this target, distinct from the NiFi VPS's
   `VPS_*`/`SSH_*` variables so the two deployments can coexist:
   `MLFLOW_VPS_HOST`, `MLFLOW_VPS_USER`, `MLFLOW_VPS_DEPLOY_DIR`,
   `MLFLOW_VPS_ENV_FILE`, `MLFLOW_VPS_SSH_PRIVATE_KEY` (file variable),
   `MLFLOW_VPS_SSH_KNOWN_HOSTS` (file variable). Set these in GitLab's
   project settings; this repository's CI jobs only ever reference the
   variable names, never a raw key.

## Operator sequence

1. Create `$MLFLOW_VPS_ENV_FILE` on the VPS (mode 0600) from `.env.example`,
   filling in the R2 credential as above.
2. Run the manual `preflight_arena_mlflow` CI job, or by hand:
   ```bash
   ./deploy-arena.sh preflight --env-file /path/to/.env \
     --api-image-ref <registry>/arena-api@sha256:... \
     --ui-image-ref <registry>/arena-ui@sha256:... --git-sha <sha>
   ```
   It pulls no image and starts no container.
3. Run the manual `deploy_arena_mlflow` CI job, or `apply --approve` by hand.
   It pulls the pinned images, recreates the project, and verifies the real
   public URL end to end: DNS, Traefik, TLS, the container, and that the
   console API answers `200` rather than prompting for a credential.

Both CI jobs are `allow_failure: true`: a failure here never blocks or fails
the rest of the pipeline, including the gateway's own deployment.

## If something doesn't come up

- **Preflight fails on the Traefik network check**: the `mlflow-auth-gui`
  project is not running, or its network was renamed. Check
  `docker network ls` on the VPS.
- **`apply` times out waiting for `https://leaderboard.mlflow.me/health`**:
  check DNS (`getent hosts leaderboard.mlflow.me` should match this VPS's
  public IP), then `docker logs` on the Traefik container for ACME/certificate
  errors, then whether the `arena-ui` container is actually healthy
  (`docker compose -p pii-masking-arena ps`).
- **`apply` fails with a `401` from `/api/v1/samples`**: a `basicauth`
  middleware has been reattached to the console router. Check `arena-ui`'s
  Traefik labels (`docker inspect pii-masking-arena-arena-ui-1`) -- there
  should be exactly one router for the host and no `middlewares=` label.
