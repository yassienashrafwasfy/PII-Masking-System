# Private NiFi VPS deployment

The production stack exposes one stable gateway only to the host and the NiFi
container. Nginx listens on `127.0.0.1:18080` and the configured Docker bridge
address on port `18080`; the blue/green gateway containers remain bound to
`127.0.0.1:18081` and `127.0.0.1:18082`. Octo, Synthify, and Berts model services
have no host ports and share an internal Docker network with the gateway.

The default-branch pipeline publishes immutable gateway and model-service image
digests. Deployment warms the inactive slot with synthetic canaries, requires
at least 25 percent host memory to remain available while both slots are warm,
atomically switches Nginx, waits for the stable endpoint to expose the candidate
Git SHA and image digest, verifies the private stable URL, waits for established
connections to leave the old slot, and then removes it. Before the drain limit,
any failure restores the previous upstream and removes only the candidate slot.

## Transactional migration from the legacy deployment

The failed deployment copied a bundle before discovering that the legacy
environment did not satisfy the new schema. `migrate-env.py` now validates all
supplied and derived values before it creates `.env.next`; an unresolved key
leaves no output file. The manual `preflight_production` job uploads only to
`/opt/pii-masking/staging/$CI_COMMIT_SHA/deploy` and validates staged code
against the explicitly selected `VPS_ENV_FILE`. Only a separately approved
manual `deploy_production` job, which requires that preflight to succeed, can
promote the exact staged bundle and apply it. Neither staging nor promotion
includes `.env`; both jobs are default-branch-only, excluded from merge-request
pipelines, and serialized by the `production` resource group.

Use this operator sequence; none of these host steps is performed by merging
the repository change:

1. Pause the legacy timer: `sudo systemctl stop pii-masking-watchdog.timer`.
2. Keep the working legacy `.env` in place. Create a private operator values
   file containing only reviewed new values, then run
   `./migrate-env.py --legacy .env --values-file /private/operator-values --output .env.next`.
   The helper copies only unambiguous legacy keys, marks unresolved key names,
   writes mode `0600`, never prints values, and never replaces either input.
3. Resolve every reported key. In particular, explicitly retain
   `NGINX_UPSTREAM_FILE=/etc/nginx/snippets/pii-masking-gateway-upstream.inc`
   for the existing production layout.
4. Run `deploy-gateway.sh preflight --env-file /absolute/path/.env.next ...`.
   Preflight checks commands, schema, permissions, immutable image identities,
   model layouts and hashes, Compose rendering, capacity inputs, ports, Nginx,
   Docker projects, listeners, and release headers. It pulls no image, starts no
   container, writes no state, and changes no host file.
5. Run `install-vps.sh plan --env-file /absolute/path/.env.next --state-dir /opt/pii-masking/deploy/state`.
   Review its secret-free diffs, blockers, commands, service implications, and
   preserved active upstream. Plan mode is non-mutating.
6. Only after review, run the same command with `apply --approve`. Apply repeats
   preflight, creates restrictive timestamped backups, installs changed host
   files atomically, validates before reload, and rolls back on failure. It does
   not replace the upstream snippet or select a different slot. If no matching
   validated release state exists yet, host installation succeeds but explicitly
   leaves watchdog enablement deferred.
7. Perform the separately approved deployment. It validates and warms the
   inactive slot, persists the unanimously inferred migration baseline only in
   apply mode, and writes release state containing the explicit environment path.
8. Re-run installer `plan`, then approved `apply`. The unchanged host files are
   left alone; `watchdog-gateway.sh validate` now gates enabling the new timer.
9. Promote `.env.next` operationally only after the migration and deployment
   have succeeded. Renaming or deleting the legacy `.env` is a separate,
   reviewed operator action and is never automated here.

When `state/active-slot` is missing, preflight never chooses a default. It parses
the configured upstream (`18081` is blue; `18082` is green), then requires the
same slot's Compose gateway, listener, direct health provenance, and stable
endpoint provenance to agree. Both slots running, an occupied candidate port,
invalid state, missing evidence, or any conflict stops the operation. Read-only
preflight reports the inferred slot; only explicitly approved apply persists it.

Berts production support uses the exact public team ID `berts`; `onnx` is its
model format, not a team alias. Set `ENABLED_TEAMS=octo,synthify,berts` only
after its complete immutable release directory has been staged. When `berts` is
omitted, the gateway receives no Berts upstream URL and returns `503` without
attempting an internal Berts request. `team-3`, `team3`, and `onnx` are rejected
as production team IDs.

## One-time VPS setup

1. Install Docker Engine with Compose, Nginx, `curl`, `python3`, `iproute2`
   (provides `ip` and `ss`), `util-linux` (provides `flock`), and OpenSSH.
2. Create a dedicated deployment user, add it to the Docker group, and create
   the directory configured as `VPS_DEPLOY_DIR`.
3. For a fresh host, copy `.env.example` to a private environment file and
   replace every placeholder. For an existing host, use the migration procedure
   above and do not overwrite the legacy `.env`.
4. Confirm that the production NiFi bridge address is assigned to the host:

   ```sh
   ip -o -4 addr show | grep '172.18.0.1/'
   ```

   Keep `NIFI_DOCKER_BRIDGE_IP=172.18.0.1`. `install-vps.sh` rejects an address
   that is not assigned to a host interface.
5. Provision immutable Octo and Synthify model directories at the absolute paths
   in `.env`. Each directory must contain its exported ONNX graph under `onnx/`
   with the configured checksum, and nothing else is read. If Berts is
   enabled, stage its complete artifact set using the procedure below and set
   `BERTS_MODEL_DIR=/srv/pii-models/releases/<release-sha>/berts`.
   If `PDF_OCR=on`, stage the OCR artifacts the same way and set
   `OCR_MODEL_DIR=/srv/pii-models/releases/<release-sha>/ocr`. With `PDF_OCR=off`
   no OCR directory is needed and none is checked.
6. Authenticate Docker with a GitLab deploy token restricted to `read_registry`.
7. Run `./install-vps.sh plan --env-file /absolute/path/to/environment --state-dir /absolute/path/to/state`
   first, then an explicitly approved `apply`. It installs the root-owned Nginx
   control helper and a sudoers policy limited to `exists`, `test`, `restart`,
   `switch 18081`, and `switch 18082`; no unrestricted shell, file-copy, or
   `systemctl` command is granted to the deployment user.
8. The installer installs the private Nginx server and Docker ordering. It
   enables the one-minute watchdog only after configuration and active-release
   validation succeed.
9. Configure NiFi with the stable URL `http://172.18.0.1:18080`.

The host must fit both fully loaded blue/green slots simultaneously. The current
11.4 GiB VPS with no swap is not certified for overlapping Octo, Synthify, and
Berts in both slots. Upgrade to at least 16 GiB before full deployment, or run
an approved isolated Berts warm-memory measurement under resource limits before
certifying the current host. `BERTS_WARM_RSS_MIB` must record measured warm RSS
on a host below 16 GiB; ONNX file size is not a runtime-memory estimate. Do not
lower `MIN_AVAILABLE_MEMORY_PERCENT` below 25 to force promotion.

Berts loads a checkpoint per document language, on first use of that language
and not before, and holds every one it has loaded until the idle timeout drops
them together. So its warm RSS depends on the traffic mix: a Dutch-only day
measures one session, a trilingual day measures three. Measure
`BERTS_WARM_RSS_MIB` with all three loaded -- that is the number the capacity
gate has to hold, and it is what a single mixed-language batch produces.
The memory gate is a final safety check, not a substitute for measuring loaded
RSS and disk usage before production.

## Immutable Berts model staging

The required immutable layouts and repository-recorded weight checksums are:

- Octo: `onnx/` contains `model.onnx`, `manifest.json`, config, SentencePiece,
  special-token and tokenizer files. ONNX SHA-256 is
  `21b0e69d83e992751c6e1420e74e17f6bb8f5765d0259651a62dd55d85a09236`.
- Synthify: `onnx/` contains `model.onnx`, `manifest.json`, GLiNER config,
  special-token and tokenizer files. ONNX SHA-256 is
  `8a6baba537c4a00d518586a2b56f62547259f12685b9b1be9b6cfc91fa44dd45`.
- Berts: three checkpoints, one per document language, each with
  `model.onnx`, `manifest.json`, `config.json`, `tokenizer.json` and
  `tokenizer_config.json`. Dutch adds `special_tokens_map.json`, `vocab.json`
  and `merges.txt`; French adds `special_tokens_map.json`; English needs no
  extra tokenizer file. ONNX SHA-256 and source revision per language:
  - `onnx/` (nl_BE)
    `e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292`
    at `07f18eb53e127ed34aa77a64e1e15f24ea622be8`
  - `onnx-en/` (en)
    `c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd`
    at `45f397a28f3efc462ec89552a7b8de574f3f61dc`
  - `onnx-fr/` (fr_FR)
    `2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4`
    at `162c057100bb174252303bb350df4c47296ea5bd`

Octo and Synthify may reuse the verified immutable directories under
`/srv/pii-models/releases/52cfa88c8be366b7d309bda4f1b3cac1ecf5c759/`.
Changing the gateway release SHA never copies those model trees. Every mount is
read-only and every enabled model is checked before an image pull or candidate
startup.

Berts requires all three committed checkpoint directories --
`teams/berts/onnx/`, `teams/berts/onnx-en/` and `teams/berts/onnx-fr/`. Which
one answers is decided per document by the language it is written in, so a
release staged with only the Dutch one does not fail at startup: it fails at
the first French or English document. Stage all three together. Deployment
never downloads a model. An operator stages a reviewed Git release before
requesting manual production approval:

```sh
release_sha=<reviewed-main-commit-sha>
checkout_dir=$(mktemp -d)
GIT_LFS_SKIP_SMUDGE=1 git clone --no-checkout \
  https://gitlab.com/internshipjuly2026/full-pii-data-masking.git "$checkout_dir"
GIT_LFS_SKIP_SMUDGE=1 git -C "$checkout_dir" checkout "$release_sha" -- \
  .gitattributes teams/berts/onnx teams/berts/onnx-en teams/berts/onnx-fr
git -C "$checkout_dir" lfs pull \
  --include='teams/berts/onnx/**,teams/berts/onnx-en/**,teams/berts/onnx-fr/**' \
  --exclude=''
printf '%s  %s\n' \
  'e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292' \
  "$checkout_dir/teams/berts/onnx/model.onnx" \
  'c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd' \
  "$checkout_dir/teams/berts/onnx-en/model.onnx" \
  '2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4' \
  "$checkout_dir/teams/berts/onnx-fr/model.onnx" | sha256sum --check --strict

destination="/srv/pii-models/releases/$release_sha/berts"
for subdir in onnx onnx-en onnx-fr; do
  sudo install -d -o root -g root -m 0555 "$destination/$subdir"
  sudo cp -a "$checkout_dir/teams/berts/$subdir/." "$destination/$subdir/"
done
sudo chown -R root:root "$destination"
sudo find "$destination" -type d -exec chmod 0555 {} +
sudo find "$destination" -type f -exec chmod 0444 {} +
```

Set the committed `.env` contract only in the VPS's chmod-600 `.env`:

```dotenv
MODEL_RELEASES_ROOT=/srv/pii-models/releases
BERTS_MODEL_DIR=/srv/pii-models/releases/<release-sha>/berts
BERTS_MODEL_NAME=berts-pii-medroberta-nl-int8
BERTS_MODEL_VERSION=07f18eb53e127ed34aa77a64e1e15f24ea622be8
BERTS_ONNX_MODEL_SHA256=e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292
BERTS_MODEL_NAME_EN=berts-pii-bert-en-int8
BERTS_MODEL_VERSION_EN=45f397a28f3efc462ec89552a7b8de574f3f61dc
BERTS_ONNX_MODEL_SHA256_EN=c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd
BERTS_MODEL_NAME_FR=berts-pii-camembert-bio-fr-int8
BERTS_MODEL_VERSION_FR=162c057100bb174252303bb350df4c47296ea5bd
BERTS_ONNX_MODEL_SHA256_FR=2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4
```

`BERTS_LANGUAGE_ROUTING=off` sends every document to the Dutch checkpoint and
stops preflight requiring the English and French artifacts. That is the
rollback for a checkpoint that masks badly: set it, restart the Berts service,
and the deployment is back to the single-model behaviour that predates the
other two -- no release needed. A misspelled value fails preflight rather than
being read as `off`.

`migrate-env.py --team berts --manifest teams/berts/onnx/manifest.json` derives
all six version and digest values from the committed manifests, reading the
English and French ones from the sibling directories, rather than being typed
by hand.

Run that host step *before* re-running `preflight_production`, not after. A
release that adds required keys cannot pass preflight on its own: the job
validates the live `VPS_ENV_FILE`, and the only thing that puts new keys into
that file is this derivation, which is an operator step by design -- the
pipeline's own `stage-model-release.sh plan` writes nothing and runs after
preflight anyway. This is what a first per-language deployment looks like from
CI:

```
Environment file is missing 4 key(s) this release requires:
  BERTS_MODEL_VERSION_EN
  BERTS_ONNX_MODEL_SHA256_EN
  BERTS_MODEL_VERSION_FR
  BERTS_ONNX_MODEL_SHA256_FR
Derive them: ./stage-model-release.sh apply --env-file FILE --release-sha SHA --approve, or set BERTS_LANGUAGE_ROUTING=off to keep serving the Dutch checkpoint alone
```

Preflight reports the whole group rather than the first name, because it is a
manual production job and finding four keys one refusal at a time costs four
approvals.

`deploy-gateway.sh` resolves the directory, rejects mutable `current` paths,
checks every required file in all three checkpoints, verifies each ONNX digest
and manifest revision, and fails before pulling images or starting the inactive
slot on any mismatch. `verify-gateway.sh` then canaries each language through
the promoted slot, so a checkpoint that is present but unloadable is caught
before the slot takes traffic rather than by the first document in that
language.

## Staging the OCR artifacts

Only needed when `PDF_OCR=on`. The OCR weights are committed through Git LFS.
`fetch_ocr_artifacts` pulls them during the build so the published
model-service image carries real models — QA needs that, because it runs the
contract gate against that image and CI cannot mount volumes. The gateway image
takes none of them; it never loads OCR. Production does not rely on the baked copy:
it reads a staged host directory mounted read-only at `/models/ocr`, verified
before any image is pulled.

The digests are checked because a Git LFS pointer is a 130-byte text file where
a model should be, and it satisfies every existence check in the chain,
including the service's own startup check. Only a hash tells the two apart, so
both the build and this preflight hash the files rather than stat them.

```sh
release_sha=<reviewed-main-commit-sha>
artifacts=platform/masking-gateway/src/masking_engine/src/models/ocr/artifacts
checkout_dir=$(mktemp -d)
GIT_LFS_SKIP_SMUDGE=1 git clone --no-checkout \
  https://gitlab.com/internshipjuly2026/full-pii-data-masking.git "$checkout_dir"
GIT_LFS_SKIP_SMUDGE=1 git -C "$checkout_dir" checkout "$release_sha" -- \
  .gitattributes "$artifacts"
git -C "$checkout_dir" lfs pull --include="$artifacts/**" --exclude=''
printf '%s  %s\n' \
  '049d5107f028a88efb9c96857ab72af847f0be7aeb413285669a84cf00b8f973' \
  "$checkout_dir/$artifacts/det/model.onnx" \
  '14a9e7d184b186fe9c0551737e0bfe53d0a3afee97806a0eeafc6565fc732a1d' \
  "$checkout_dir/$artifacts/rec/model.onnx" \
  'b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d' \
  "$checkout_dir/$artifacts/rec/charset.txt" | sha256sum --check --strict

destination="/srv/pii-models/releases/$release_sha/ocr"
sudo install -d -o root -g root -m 0555 "$destination"
sudo cp -a "$checkout_dir/$artifacts/." "$destination/"
sudo chown -R root:root "$destination"
sudo find "$destination" -type d -exec chmod 0555 {} +
sudo find "$destination" -type f -exec chmod 0444 {} +
```

Copy the whole tree, not only the three hashed files: `rec/manifest.json`
carries the DB thresholds the engine reads on load.

Then set the OCR contract in the VPS's chmod-600 `.env`:

```dotenv
PDF_OCR=on
OCR_MODEL_DIR=/srv/pii-models/releases/<release-sha>/ocr
OCR_DET_SHA256=049d5107f028a88efb9c96857ab72af847f0be7aeb413285669a84cf00b8f973
OCR_REC_SHA256=14a9e7d184b186fe9c0551737e0bfe53d0a3afee97806a0eeafc6565fc732a1d
OCR_CHARSET_SHA256=b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d
```

`deploy-gateway.sh` validates this directory with the same rules it applies to a
model directory and verifies all three digests, before pulling images or
starting the inactive slot. That matters because the service itself checks far
later than it looks: settings are cached and first read on a request, and the
OCR engine is built on the first page that needs it.

## Protected GitLab variables

Configure protected, production-scoped variables:

- `VPS_HOST`, `VPS_PORT`, `VPS_USER`, `VPS_DEPLOY_DIR`, and the absolute
  `VPS_ENV_FILE` selected for the release (for migration, `.env.next`).
- Optional `VPS_STAGING_ROOT`; by default staging is a sibling of the live
  deploy directory.
- `VPS_VERIFY_URL=http://127.0.0.1:18080`.
- `SSH_PRIVATE_KEY` and `SSH_KNOWN_HOSTS` as file variables.

`preflight_production` is available only as a manual action on the default
branch after its image publishing and security-scan dependencies pass. It may
create the commit-scoped staging directory, upload the bundle, and run the
read-only deployment preflight; it cannot promote, install host configuration,
start containers, reload Nginx, or deploy. `deploy_production` is a separate,
blocking manual production-environment action. It requires successful
`preflight_production` and is the only job permitted to promote the preflighted
bundle and run apply. Merge-request pipelines cannot expose or start either job.
The deploy action has a 150-minute limit so a request can drain for the
contracted 6,600 seconds.

## Runtime and watchdog

The gateway runs one Gunicorn worker with eight threads. Each model service runs
one worker with two threads; masking remains serialized inside each service. The
gateway therefore accepts one masking request per team while allowing Octo,
Synthify, and Berts to run concurrently. Excess same-team requests return `429` with
`Retry-After: 5`.

Production disables idle model unloading. Deployment and watchdog canaries warm
every enabled model, after which `/ready` remains `200`. Every minute the watchdog probes
the stable `/health` endpoint and each enabled team's `/ready` endpoint. It makes
one targeted restart, waits ten seconds, and reruns the immutable canaries. When
`ALERT_WEBHOOK_URL` is configured it posts PII-free failure and recovery events;
otherwise it writes the same structured JSON to stderr for systemd-journald. A
ten-minute cooldown prevents restart loops and repeated alerts.

Release identity is stored in `state/releases/<slot>.env`, and `state/active-slot`
is switched only after public-facing private verification succeeds. The watchdog
and deployer share `state/operation.lock`, so they cannot mutate the stack at the
same time.

## Verification and recovery

Confirm the gateway is private after setup:

```sh
ss -ltn | grep ':18080'
curl http://127.0.0.1:18080/health
systemctl status pii-masking-watchdog.timer
```

Port `18080` must appear only on loopback and `NIFI_DOCKER_BRIDGE_IP`, never on
`0.0.0.0`, `[::]`, or the VPS public address. Inspect deployment state with the
`pii-masking-blue` and `pii-masking-green` Compose projects, the Nginx upstream
file, and `state/active-slot`. Failed candidates are removed automatically; a
failed runtime repair leaves the service degraded and sends one alert until it
recovers.

For Berts, candidate and stable canaries send `X-Team-ID: berts` and
`X-Model-Format: onnx`, then require the configured model name, version, digest,
model format, Git SHA, and image digest in the response provenance. Because the
answering checkpoint follows the document's language, those canaries pin
`X-Document-Locale: nl_BE` rather than letting detection pick from the canary
text, and two further Berts-only canaries send `en` and `fr_FR` and require the
English and French model names back. A checkpoint that is staged but does not
load is therefore caught in verification, not by the first document written in
that language. Disabling
Berts means removing only `berts` from `ENABLED_TEAMS`; Octo and Synthify retain
their existing profiles and canaries.

This repository change does not update NiFi. After a capacity-approved manual
production deployment, adding trusted `incoming/berts/...` routing in NiFi is a
separate operator-controlled step; the stable endpoint remains
`http://172.18.0.1:18080`.

## The arena is deployed separately, on a different VPS

The leaderboard and masking console are not part of this deployment at all.
They run on the MLflow VPS, behind its existing Traefik instance, at
`https://leaderboard.mlflow.me` -- see `deploy/mlflow-vps/README.md`. That
target has its own GitLab CI/CD variables (`MLFLOW_VPS_*`), its own compose
project, and is `allow_failure: true` throughout, so a failure there never
affects anything documented above.
