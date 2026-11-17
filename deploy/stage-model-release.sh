#!/usr/bin/env bash
set -Eeuo pipefail

# Stages each enabled team's model artifacts for a reviewed commit and points
# the deployment environment file at them.
#
# Splitting this out of ci/deploy.yml is deliberate: promoting the production
# environment file means backing it up, rewriting model identity for several
# teams, and replacing it atomically, with a restore path when the deploy that
# follows fails. That is not logic to maintain inside a quoted YAML ssh string.
#
# `plan` never mutates anything, matching install-vps.sh's plan/apply
# discipline: it reports what staging would do, so preflight stays free of side
# effects. `apply` does the staging and the promotion. The deploy-gateway.sh
# apply that follows re-runs its own full preflight against the promoted file
# and rolls the containers back on its own if the new model fails to verify.
deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/vps-common.sh
source "$deploy_dir/vps-common.sh"

usage() {
  echo "Usage: $0 {plan|apply|rollback} --env-file FILE --release-sha SHA [--approve]" >&2
  exit 2
}

mode=${1:-}; shift || true
[[ "$mode" == plan || "$mode" == apply || "$mode" == rollback ]] || usage
env_file=${ENV_FILE:-}
release_sha=
approved=false
while (( $# )); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --release-sha) release_sha=${2:-}; shift 2 ;;
    --approve) approved=true; shift ;;
    *) usage ;;
  esac
done
[[ -n "$env_file" ]] || { echo "An explicit --env-file or ENV_FILE is required." >&2; exit 1; }
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "--release-sha must be a 40-character commit hash." >&2; exit 1; }
vps_validate_absolute_path ENV_FILE "$env_file"

backup_file="$env_file.pre-model-release.$release_sha"
candidate_dir=

cleanup() { [[ -z "$candidate_dir" ]] || rm -rf -- "$candidate_dir"; }
trap cleanup EXIT

if [[ "$mode" == rollback ]]; then
  # Only ever restores a backup this script wrote for this exact release, so a
  # stray invocation cannot resurrect an unrelated environment file.
  [[ -f "$backup_file" ]] || { echo "No environment backup exists for $release_sha; nothing to roll back." >&2; exit 0; }
  install -m 600 "$backup_file" "$env_file.restore.$$"
  mv -f -- "$env_file.restore.$$" "$env_file"
  rm -f -- "$backup_file"
  echo "Restored $env_file from the pre-release backup."
  exit 0
fi

vps_load_env_file "$env_file"
vps_require_var ENABLED_TEAMS

# The rollout gate, checked before anything is required of the environment
# file. A host that has not yet been provisioned is one that either lacks the
# staging settings or lacks the installed helper, and both have to skip rather
# than fail the release -- requiring the settings first would turn "not yet
# provisioned" into a hard error, which is exactly what this gate exists to
# prevent. It becomes a hard error once every host is provisioned.
if [[ -z "${MODEL_STAGE_HELPER:-}" || -z "${MODEL_RELEASES_ROOT:-}" ]]; then
  echo "Model staging is not configured on this host; leaving model artifacts and $env_file untouched."
  exit 0
fi
vps_validate_absolute_path MODEL_STAGE_HELPER "$MODEL_STAGE_HELPER"
vps_validate_absolute_path MODEL_RELEASES_ROOT "$MODEL_RELEASES_ROOT"

if ! sudo -n "$MODEL_STAGE_HELPER" status >/dev/null 2>&1; then
  echo "Model staging helper is not provisioned on this host; leaving model artifacts and $env_file untouched."
  exit 0
fi

# The token arrives on stdin rather than argv so it stays out of `ps` output
# and the process table, and is forwarded to the helper the same way. Both
# modes need it: `plan` still fetches LFS pointers (not the weights) to read
# the committed manifest and size the payload, so a plan without credentials
# would report nothing useful.
IFS= read -r token_line || true
[[ "$token_line" == GIT_TOKEN=* ]] || { echo "$mode requires GIT_TOKEN=<token> on stdin." >&2; exit 1; }
git_token=${token_line#GIT_TOKEN=}
[[ -n "$git_token" ]] || { echo "GIT_TOKEN is empty." >&2; exit 1; }

teams=()
IFS=',' read -r -a requested <<< "$ENABLED_TEAMS"
for team in "${requested[@]}"; do
  team=${team// /}
  [[ -n "$team" ]] || continue
  case "$team" in
    octo|synthify|berts) teams+=("$team") ;;
    *) echo "ENABLED_TEAMS names an unsupported team: $team" >&2; exit 1 ;;
  esac
done
(( ${#teams[@]} > 0 )) || { echo "ENABLED_TEAMS is empty." >&2; exit 1; }

if [[ "$mode" == plan ]]; then
  for team in "${teams[@]}"; do
    printf 'RELEASE_SHA=%s\nGIT_TOKEN=%s\n' "$release_sha" "$git_token" \
      | sudo -n "$MODEL_STAGE_HELPER" plan "$team"
  done
  echo "Planned model staging for $release_sha; no artifacts or environment files were changed."
  exit 0
fi

[[ "$approved" == true || "${APPROVE_PRODUCTION_MIGRATION:-}" == 1 ]] || {
  echo "Apply requires --approve or APPROVE_PRODUCTION_MIGRATION=1." >&2; exit 1;
}

for team in "${teams[@]}"; do
  printf 'RELEASE_SHA=%s\nGIT_TOKEN=%s\n' "$release_sha" "$git_token" \
    | sudo -n "$MODEL_STAGE_HELPER" stage "$team"
done

# Identity is derived from the manifest inside the tree that was just staged
# and verified, not from the checkout, so the recorded digests can only ever
# describe the bytes actually mounted into production.
candidate_dir=$(mktemp -d)
current="$env_file"
for team in "${teams[@]}"; do
  manifest="$MODEL_RELEASES_ROOT/$release_sha/$team/onnx/manifest.json"
  [[ -f "$manifest" ]] || { echo "Staged manifest is missing for $team." >&2; exit 1; }
  next="$candidate_dir/env.$team"
  "$deploy_dir/migrate-env.py" \
    --current "$current" \
    --team "$team" \
    --release-sha "$release_sha" \
    --manifest "$manifest" \
    --releases-root "$MODEL_RELEASES_ROOT" \
    --output "$next"
  current="$next"
done

if cmp -s "$current" "$env_file"; then
  echo "Model identity for $release_sha already matches $env_file; leaving it untouched."
  exit 0
fi

# Backup first, then replace by rename. The watchdog re-reads this file every
# 60 seconds before it takes the deployment lock, so the lock cannot protect
# it -- only the atomic rename guarantees the watchdog never sees a half-written
# file.
install -m 600 "$env_file" "$backup_file"
install -m 600 "$current" "$env_file.next.$$"
mv -f -- "$env_file.next.$$" "$env_file"
# Each backup is a full copy of a file holding the service token and the
# object-storage credentials, so they are not left to pile up next to the live
# one. The newest few are kept because rollback only ever targets the release
# currently being deployed.
ls -1t "$env_file".pre-model-release.* 2>/dev/null | tail -n +4 | xargs -r rm -f --

echo "Promoted model identity for $release_sha; previous environment saved to $(basename "$backup_file")."
