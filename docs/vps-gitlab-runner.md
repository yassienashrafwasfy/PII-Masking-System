# VPS GitLab Runner

Every CI job carries the `vps-ci` tag and runs on a self-hosted Docker executor. No job uses a `gpu` runner.

## GitLab configuration

1. Create a group runner with the `vps-ci` tag and disable **Run untagged jobs**.
2. Allow only trusted project members to submit pipelines. Do not execute untrusted fork code on a runner sharing the production network.
3. Disable GitLab shared runners for this project.
4. Register the runner with the Docker executor and a pinned Python/uv default image.

```sh
sudo gitlab-runner register \
  --url https://gitlab.com \
  --token "$GITLAB_RUNNER_TOKEN" \
  --executor docker \
  --docker-image ghcr.io/astral-sh/uv:python3.12-bookworm-slim
```

Keep the runner unprivileged, limited to one concurrent job, and isolated from host paths:

```toml
concurrent = 1

[[runners]]
  executor = "docker"
  limit = 1

  [runners.docker]
    image = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
    privileged = false
    memory = "2g"
    cpus = "1"
    disable_cache = false
    volumes = ["/cache"]
    security_opt = ["apparmor:unconfined", "seccomp:unconfined"]
```

Never mount `/var/run/docker.sock`, deployment configuration, SSH keys, model directories, or arbitrary host paths into CI containers.

## Rootless image builds

Package jobs use `moby/buildkit:v0.31.2-rootless`, `buildctl-daemonless.sh`, and `BUILDKITD_FLAGS=--oci-worker-no-process-sandbox`. The jobs authenticate directly to the GitLab registry and use registry-backed caches; Docker-in-Docker and privileged mode are not used.

RootlessKit must create a user namespace inside the job container. Docker's default seccomp and AppArmor profiles block the operations BuildKit needs, so the runner configuration above follows BuildKit's containerized-rootless guidance and unconfines those two profiles while keeping the container non-root and the runner non-privileged. `--oci-worker-no-process-sandbox` replaces BuildKit's otherwise-required `systempaths=unconfined`, which GitLab Runner does not support.

After changing `/etc/gitlab-runner/config.toml`, wait a few seconds for GitLab Runner to reload the file and use `sudo gitlab-runner verify` to confirm that the registered runner remains reachable. Then run a container-related merge-request pipeline and confirm both images build and push. If RootlessKit still reports `operation not permitted`, check the host before retrying:

```sh
sysctl user.max_user_namespaces
sysctl kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || true
```

`user.max_user_namespaces` must be greater than zero. On Ubuntu 24.04 or later, BuildKit also requires `kernel.apparmor_restrict_unprivileged_userns=0`. Keep this runner restricted to trusted project members because the relaxed profiles apply to every job assigned to it; use a separately tagged package runner if unrelated or untrusted jobs share the host. Do not enable privileged mode.

## Docker/BuildKit disk garbage collection

Every `publish_gateway` / `publish_model_service` pipeline run pushes a new tagged image and orphans the previous one; nothing removes those automatically. On 2026-08-30 this filled the runner host's root filesystem to 99% and failed `publish_model_service` with "no space left on device" mid-build, after an earlier `.dockerignore`/cache-mode fix (commit `9e366f7`) had already trimmed everything a repo-side change can trim. A manual `docker builder prune -af`, `docker container prune -f`, `docker image prune -af` reclaimed 127.5 GB.

Install a daily timer so this does not recur:

```sh
sudo install -m 755 deploy/vps-docker-gc.sh /usr/local/bin/vps-docker-gc.sh
sudo install -m 644 deploy/systemd/vps-docker-gc.service /etc/systemd/system/vps-docker-gc.service
sudo install -m 644 deploy/systemd/vps-docker-gc.timer /etc/systemd/system/vps-docker-gc.timer
sudo systemctl daemon-reload
sudo systemctl enable --now vps-docker-gc.timer
```

The job only removes BuildKit build cache, images unused for more than 72h (`DOCKER_GC_GRACE`), and stopped containers past the same grace window; it never touches named volumes, since a container between blue/green cutovers is stopped, not abandoned. It logs reclaimed space to the journal (`journalctl -u vps-docker-gc.service`) and warns there if `/` is still at or above 85% (`DOCKER_GC_ALERT_THRESHOLD`) after a run, which is the signal that scheduled GC is no longer enough and needs a human look (bigger volume, or a real image-retention policy in the registry).

## Acceptance and operation

- Confirm build, validation, evaluation, package, scan, and deploy jobs use `vps-ci`; the full-corpus job alone uses `gpu`.
- Keep the weekly schedule variable `SECURITY_SCAN=weekly`.
- Monitor runner logs, host CPU/memory, registry cache growth, `docker system df`, and production health.
- Confirm `vps-docker-gc.timer` is enabled and its last run left `/` below the alert threshold (`systemctl status vps-docker-gc.timer`, `journalctl -u vps-docker-gc.service -n 20`).
- The default-branch deployment requires protected SSH variables and fails when any required value is missing.
