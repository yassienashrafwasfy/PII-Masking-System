#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 {plan|apply} --source DIR --target DIR [--approve]" >&2
  exit 2
}

mode=${1:-}
shift || true
source_dir=
target_dir=
approved=false
while (( $# )); do
  case "$1" in
    --source) source_dir=${2:-}; shift 2 ;;
    --target) target_dir=${2:-}; shift 2 ;;
    --approve) approved=true; shift ;;
    *) usage ;;
  esac
done
[[ "$mode" == plan || "$mode" == apply ]] || usage
[[ "$source_dir" =~ ^/[A-Za-z0-9_./-]+$ && "$target_dir" =~ ^/[A-Za-z0-9_./-]+$ ]] || {
  echo "Source and target must be absolute paths without whitespace." >&2
  exit 1
}
[[ "/$source_dir/" != *"/../"* && "/$source_dir/" != *"/./"* && "/$target_dir/" != *"/../"* && "/$target_dir/" != *"/./"* ]] || {
  echo "Source and target paths must be normalized." >&2
  exit 1
}
source_dir=$(realpath -e "$source_dir")
target_dir=$(realpath -m "$target_dir")
[[ "$source_dir" != "$target_dir" ]] || { echo "Source and target must differ." >&2; exit 1; }

files=(
  .env.example
  compose.yml
  deploy-gateway.sh
  verify-gateway.sh
  watchdog-gateway.sh
  install-vps.sh
  migrate-env.py
  pii-masking-nginx-control
  nginx.conf.example
  promote-staged-bundle.sh
  vps-common.sh
  pii-masking-model-stage
  stage-model-release.sh
  prune-model-releases.sh
  sudoers/pii-masking-nginx-control
  sudoers/pii-masking-model-stage
  systemd/nginx-docker-ordering.conf
  systemd/pii-masking-watchdog.service
  systemd/pii-masking-watchdog.timer
)

for relative in "${files[@]}"; do
  [[ -f "$source_dir/$relative" ]] || { echo "Staged bundle is missing: $relative" >&2; exit 1; }
  if [[ -e "$target_dir/$relative" ]] && cmp -s "$source_dir/$relative" "$target_dir/$relative"; then
    echo "unchanged $relative"
  elif [[ -e "$target_dir/$relative" ]]; then
    echo "replace $relative"
  else
    echo "create $relative"
  fi
done
[[ "$mode" == plan ]] && exit 0
[[ "$approved" == true || "${APPROVE_PRODUCTION_MIGRATION:-}" == 1 ]] || {
  echo "Promotion requires --approve or APPROVE_PRODUCTION_MIGRATION=1." >&2
  exit 1
}

release=${DEPLOYMENT_RELEASE:-$(date -u +%Y%m%dT%H%M%SZ)}
[[ "$release" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "DEPLOYMENT_RELEASE is invalid." >&2; exit 1; }
backup_dir="$target_dir/.migration-backups/$release"
install -d -m 700 "$target_dir" "$target_dir/systemd" "$target_dir/sudoers" "$backup_dir"

installed=()
rollback() {
  local relative
  for relative in "${installed[@]}"; do
    if [[ -f "$backup_dir/$relative" ]]; then
      install -d -m 700 "$(dirname "$target_dir/$relative")"
      install -m "$(stat -Lc '%a' "$backup_dir/$relative")" "$backup_dir/$relative" "$target_dir/$relative"
    else
      rm -f -- "$target_dir/$relative"
    fi
  done
}
trap 'status=$?; (( status == 0 )) || rollback; exit "$status"' EXIT

for relative in "${files[@]}"; do
  target="$target_dir/$relative"
  backup="$backup_dir/$relative"
  [[ -f "$target" ]] && cmp -s "$source_dir/$relative" "$target" && continue
  install -d -m 700 "$(dirname "$target")" "$(dirname "$backup")"
  [[ ! -f "$target" || -f "$backup" ]] || install -m "$(stat -Lc '%a' "$target")" "$target" "$backup"
  case "$relative" in
    *.sh|migrate-env.py|pii-masking-nginx-control) mode_bits=700 ;;
    *) mode_bits=600 ;;
  esac
  next="$target.next.$$"
  install -m "$mode_bits" "$source_dir/$relative" "$next"
  mv -f -- "$next" "$target"
  installed+=("$relative")
done
trap - EXIT
echo "Promoted validated deployment bundle $release without modifying .env."
