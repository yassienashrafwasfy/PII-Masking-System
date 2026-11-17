#!/usr/bin/env bash
set -Eeuo pipefail

# Reclaims immutable model release directories under the releases root.
#
# Nothing removed these before, and deploy-gateway.sh deliberately forbids a
# mutable `current` symlink, so every staged release stayed on disk forever at
# roughly 2 GiB per team. This host has already filled its root filesystem to
# 99% once (docs/vps-gitlab-runner.md), so the growth is not theoretical.
#
# The rules are deliberately conservative, because deleting the wrong tree
# takes production down at the next container recreate and costs a multi-GiB
# refetch to undo:
#   * never a release any *_MODEL_DIR in the live environment file names;
#   * never a release a container currently has bind-mounted;
#   * never the newest KEEP_RELEASES releases -- this is what keeps a rollback
#     target on disk, since a completed deploy removes the old slot's
#     containers and the recorded slot state does not name model directories;
#   * never a release younger than MIN_AGE_DAYS;
#   * and nothing at all unless the environment file parsed, so a missing or
#     unreadable input can never be mistaken for "nothing is referenced".
releases_root="@MODEL_RELEASES_ROOT@"
env_file="@ENV_FILE@"
keep_releases="${KEEP_RELEASES:-3}"
min_age_days="${MIN_AGE_DAYS:-7}"

dry_run=false
case "${1:-}" in
  --dry-run) dry_run=true ;;
  "") ;;
  *) echo "Usage: prune-model-releases.sh [--dry-run]" >&2; exit 2 ;;
esac

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

[[ -d "$releases_root" && ! -L "$releases_root" ]] || {
  echo "Model releases root is missing or a symlink: $releases_root" >&2
  exit 1
}

# Fail closed: without a parsable environment file we cannot know which release
# is live, and guessing is how you delete the running model.
referenced=$(
  {
    [[ -r "$env_file" ]] || { echo "Environment file is unreadable: $env_file" >&2; exit 1; }
    grep -oE '^[A-Z0-9_]*MODEL_DIR=[^[:space:]]+' "$env_file" | cut -d= -f2- || true

    # Any release a container still mounts is in use even if the environment
    # file has already moved on -- for instance between an .env update and the
    # blue/green switch that follows it.
    if command -v docker >/dev/null 2>&1; then
      docker ps --all --quiet \
        | xargs --no-run-if-empty docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' \
        || { echo "Could not inspect containers for mounted releases." >&2; exit 1; }
    else
      echo "docker is required to determine mounted releases." >&2
      exit 1
    fi
  } | sed -n "s#^\(${releases_root%/}/[0-9a-f]\{40\}\)\(/.*\)\?\$#\1#p" | sort -u
) || exit 1

log "protected releases: $(printf '%s' "$referenced" | grep -c . || true)"

# Newest first, so the head of this list is what a rollback would reach for.
mapfile -t releases < <(
  find "$releases_root" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
    -regex "${releases_root%/}/[0-9a-f]{40}" -printf '%T@ %p\n' \
    | sort -rn | cut -d' ' -f2-
)

removed=0
for index in "${!releases[@]}"; do
  release=${releases[$index]}
  if (( index < keep_releases )); then
    continue
  fi
  if printf '%s\n' "$referenced" | grep -Fxq "$release"; then
    log "keep (in use) $release"
    continue
  fi
  if [[ -n "$(find "$release" -maxdepth 0 -mtime -"$min_age_days")" ]]; then
    log "keep (younger than ${min_age_days}d) $release"
    continue
  fi
  if [[ "$dry_run" == true ]]; then
    log "would remove $release"
  else
    chmod -R u+w "$release"
    rm -rf -- "$release"
    log "removed $release"
  fi
  removed=$(( removed + 1 ))
done

log "model release gc complete; $(( ${#releases[@]} )) releases present, $removed $([[ "$dry_run" == true ]] && echo 'would be removed' || echo removed)"
