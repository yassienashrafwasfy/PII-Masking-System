#!/usr/bin/env bash
set -Eeuo pipefail

# This host runs the vps-ci GitLab Runner and production containers side by
# side (see docs/vps-gitlab-runner.md). Every package-stage pipeline pushes a
# new tagged model-service/masking-gateway image and orphans the previous
# one; nothing ever removed those, so they piled up until the root
# filesystem hit 99% and publish_model_service failed with "no space left on
# device". This script reclaims that space on a schedule instead of by hand.
#
# Only BuildKit's build cache, unused images, and stopped containers older
# than GRACE are removed. Named volumes are never touched: a container
# between the watchdog's blue/green slots is stopped, not gone, and this
# script has no way to tell "abandoned" from "about to be reused" apart from
# that.
GRACE="${DOCKER_GC_GRACE:-72h}"
ALERT_THRESHOLD="${DOCKER_GC_ALERT_THRESHOLD:-85}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

avail_bytes() { df --output=avail -B1 / | tail -1; }
used_pct() { df --output=pcent / | tail -1 | tr -dc '0-9'; }

before=$(avail_bytes)

docker builder prune -af --filter "until=${GRACE}" >/dev/null
docker container prune -f --filter "until=${GRACE}" >/dev/null
docker image prune -af --filter "until=${GRACE}" >/dev/null

after=$(avail_bytes)
reclaimed_mib=$(( (after - before) / 1024 / 1024 ))
free_gib=$(( after / 1024 / 1024 / 1024 ))
log "docker gc reclaimed ${reclaimed_mib} MiB; ${free_gib} GiB free on /"

pct=$(used_pct)
if (( pct >= ALERT_THRESHOLD )); then
  log "WARNING: / is ${pct}% full after garbage collection (threshold ${ALERT_THRESHOLD}%). Manual review needed: docker system df -v, docker volume ls."
fi
