#!/usr/bin/env bash
set -Eeuo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/vps-common.sh
source "$deploy_dir/vps-common.sh"

usage() {
  echo "Usage: $0 {plan|apply} --env-file FILE --state-dir DIR [--approve]" >&2
  exit 2
}

operation=${1:-}; shift || true
[[ "$operation" == plan || "$operation" == apply ]] || usage
env_file=${ENV_FILE:-}
state_dir=${STATE_DIR:-$deploy_dir/state}
approved=false
while (( $# )); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --state-dir) state_dir=${2:-}; shift 2 ;;
    --approve) approved=true; shift ;;
    *) usage ;;
  esac
done
[[ -n "$env_file" ]] || { echo "An explicit --env-file or ENV_FILE is required." >&2; exit 1; }
vps_validate_absolute_path STATE_DIR "$state_dir"

work_dir=$(mktemp -d)
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT
rendered_server="$work_dir/pii-masking-gateway.conf"
rendered_helper="$work_dir/pii-masking-nginx-control"
rendered_sudoers="$work_dir/pii-masking-nginx-control.sudoers"
rendered_service="$work_dir/pii-masking-watchdog.service"
rendered_timer="$work_dir/pii-masking-watchdog.timer"
rendered_ordering="$work_dir/nginx-docker-ordering.conf"
rendered_model_stage="$work_dir/pii-masking-model-stage"
rendered_model_sudoers="$work_dir/pii-masking-model-stage.sudoers"
rendered_model_prune="$work_dir/prune-model-releases.sh"
rendered_model_gc_service="$work_dir/pii-masking-model-gc.service"
rendered_model_gc_timer="$work_dir/pii-masking-model-gc.timer"
nginx_test_config="$work_dir/nginx.conf"

installer_preflight() {
  local active_slot active_port
  for command_name in curl diff docker flock grep ip nginx python3 ss stat sudo systemctl; do vps_require_command "$command_name"; done
  command -v visudo >/dev/null 2>&1 || vps_die "visudo is required."

  deploy_user=${DEPLOY_USER:-$(id -un)}
  [[ "$deploy_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || {
    vps_die "DEPLOY_USER is invalid."
    return
  }
  vps_load_env_file "$env_file" "$deploy_user"

  for key in NIFI_DOCKER_BRIDGE_IP NGINX_UPSTREAM_FILE NGINX_SERVER_FILE NGINX_CONTROL_HELPER; do vps_require_var "$key"; done
  vps_validate_absolute_path NGINX_UPSTREAM_FILE "$NGINX_UPSTREAM_FILE"
  vps_validate_absolute_path NGINX_SERVER_FILE "$NGINX_SERVER_FILE"
  vps_validate_absolute_path NGINX_CONTROL_HELPER "$NGINX_CONTROL_HELPER"

  # Model staging. The work root has to sit on the same filesystem as the
  # releases root so the final `mv -T` is an atomic rename and identical
  # checkpoints can be hardlinked instead of refetched; defaulting it inside
  # that root guarantees both. The leading dot keeps it out of the
  # `<release-sha>/<team>` glob that discovers staged releases.
  for key in MODEL_RELEASES_ROOT MODEL_STAGE_HELPER MODEL_STAGE_REMOTE_URL; do vps_require_var "$key"; done
  model_work_root=${MODEL_WORK_ROOT:-$MODEL_RELEASES_ROOT/.staging-work}
  model_stage_min_free=${MODEL_STAGE_MIN_FREE_BYTES:-5368709120}
  # Installed beside the staging helper rather than at its own configured path:
  # both are root-owned executables with the same trust requirements, so one
  # setting cannot drift out of step with the other.
  model_prune_helper=${MODEL_PRUNE_HELPER:-$(dirname "$MODEL_STAGE_HELPER")/prune-model-releases.sh}
  vps_validate_absolute_path MODEL_RELEASES_ROOT "$MODEL_RELEASES_ROOT"
  vps_validate_absolute_path MODEL_STAGE_HELPER "$MODEL_STAGE_HELPER"
  vps_validate_absolute_path MODEL_WORK_ROOT "$model_work_root"
  vps_validate_absolute_path MODEL_PRUNE_HELPER "$model_prune_helper"
  [[ "$model_stage_min_free" =~ ^[0-9]+$ ]] || vps_die "MODEL_STAGE_MIN_FREE_BYTES must be a whole number of bytes."
  [[ "$MODEL_STAGE_REMOTE_URL" =~ ^https://[A-Za-z0-9._~:/?#@!$\&\'*+,\;=-]+$ ]] ||
    vps_die "MODEL_STAGE_REMOTE_URL must be an https URL."
  [[ "${PRIVATE_GATEWAY_PORT:-18080}" == 18080 ]] || vps_die "PRIVATE_GATEWAY_PORT must preserve stable port 18080."
  python3 -c 'import ipaddress,sys; ipaddress.ip_address(sys.argv[1])' "$NIFI_DOCKER_BRIDGE_IP"
  ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$NIFI_DOCKER_BRIDGE_IP" || vps_die "NIFI_DOCKER_BRIDGE_IP is not assigned to a host interface."

  active_slot=$(vps_discover_active_slot "$state_dir" "http://127.0.0.1:18080")
  active_port=$(vps_port_for_slot "$active_slot")

  # Apply deliberately runs preflight twice. Recreate our private render
  # artifacts because the first pass makes the rendered sudoers file 0440.
  rm -f -- "$rendered_server" "$rendered_helper" "$rendered_sudoers" \
    "$rendered_service" "$rendered_timer" "$rendered_ordering" \
    "$rendered_model_stage" "$rendered_model_sudoers" "$rendered_model_prune" \
    "$rendered_model_gc_service" "$rendered_model_gc_timer" "$nginx_test_config"

  python3 - "$deploy_dir/nginx.conf.example" "$rendered_server" <<'PY'
import os, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for old, new in {
    "@NIFI_DOCKER_BRIDGE_IP@": os.environ["NIFI_DOCKER_BRIDGE_IP"],
    "@PRIVATE_GATEWAY_PORT@": "18080",
    "@NGINX_UPSTREAM_FILE@": os.environ["NGINX_UPSTREAM_FILE"],
}.items(): text = text.replace(old, new)
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/pii-masking-nginx-control" "$rendered_helper" "$NGINX_UPSTREAM_FILE" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8").replace("@NGINX_UPSTREAM_FILE@", sys.argv[3])
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/sudoers/pii-masking-nginx-control" "$rendered_sudoers" "$deploy_user" "$NGINX_CONTROL_HELPER" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8").replace("@DEPLOY_USER@", sys.argv[3]).replace("@NGINX_CONTROL_HELPER@", sys.argv[4])
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/systemd/pii-masking-watchdog.service" "$rendered_service" "$deploy_dir" "$deploy_user" "$env_file" "$state_dir" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for old, new in {"@DEPLOY_DIR@":sys.argv[3], "@DEPLOY_USER@":sys.argv[4], "@ENV_FILE@":sys.argv[5], "@STATE_DIR@":sys.argv[6]}.items(): text = text.replace(old, new)
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/pii-masking-model-stage" "$rendered_model_stage" \
    "$MODEL_RELEASES_ROOT" "$model_work_root" "$MODEL_STAGE_REMOTE_URL" "$model_stage_min_free" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for old, new in {
    "@MODEL_RELEASES_ROOT@": sys.argv[3],
    "@MODEL_WORK_ROOT@": sys.argv[4],
    "@GIT_REMOTE_URL@": sys.argv[5],
    "@MIN_FREE_BYTES@": sys.argv[6],
}.items(): text = text.replace(old, new)
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/sudoers/pii-masking-model-stage" "$rendered_model_sudoers" "$deploy_user" "$MODEL_STAGE_HELPER" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8").replace("@DEPLOY_USER@", sys.argv[3]).replace("@MODEL_STAGE_HELPER@", sys.argv[4])
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/prune-model-releases.sh" "$rendered_model_prune" "$MODEL_RELEASES_ROOT" "$env_file" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for old, new in {"@MODEL_RELEASES_ROOT@": sys.argv[3], "@ENV_FILE@": sys.argv[4]}.items(): text = text.replace(old, new)
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  python3 - "$deploy_dir/systemd/pii-masking-model-gc.service" "$rendered_model_gc_service" "$MODEL_RELEASES_ROOT" "$model_prune_helper" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for old, new in {"@MODEL_RELEASES_ROOT@": sys.argv[3], "@MODEL_PRUNE_HELPER@": sys.argv[4]}.items(): text = text.replace(old, new)
Path(sys.argv[2]).write_text(text, encoding="utf-8")
PY
  cp -- "$deploy_dir/systemd/pii-masking-model-gc.timer" "$rendered_model_gc_timer"
  cp -- "$deploy_dir/systemd/pii-masking-watchdog.timer" "$rendered_timer"
  cp -- "$deploy_dir/systemd/nginx-docker-ordering.conf" "$rendered_ordering"
  chmod 755 "$rendered_helper" "$rendered_model_stage" "$rendered_model_prune"; chmod 440 "$rendered_sudoers" "$rendered_model_sudoers"; chmod 644 "$rendered_server" "$rendered_service" "$rendered_timer" "$rendered_ordering" "$rendered_model_gc_service" "$rendered_model_gc_timer"

  sudo visudo -cf "$rendered_sudoers" >/dev/null
  sudo visudo -cf "$rendered_model_sudoers" >/dev/null
  bash -n "$rendered_model_stage"
  bash -n "$rendered_model_prune"
  {
    printf 'events {}\nhttp {\n'
    cat "$rendered_server"
    printf '}\n'
  } > "$nginx_test_config"
  sudo nginx -t -c "$nginx_test_config" >/dev/null
  if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$rendered_service" "$rendered_timer" \
      "$rendered_model_gc_service" "$rendered_model_gc_timer" >/dev/null
  fi
  printf '%s\n' "$active_slot" > "$work_dir/verified-active-slot"
  printf '%s\n' "$active_port" > "$work_dir/verified-active-port"
}

declare -a sources targets modes labels
register_files() {
  sources=("$rendered_server" "$rendered_helper" "$rendered_sudoers" "$rendered_service" "$rendered_timer" "$rendered_ordering" "$rendered_model_stage" "$rendered_model_sudoers" "$rendered_model_prune" "$rendered_model_gc_service" "$rendered_model_gc_timer")
  targets=("$NGINX_SERVER_FILE" "$NGINX_CONTROL_HELPER" /etc/sudoers.d/pii-masking-nginx-control /etc/systemd/system/pii-masking-watchdog.service /etc/systemd/system/pii-masking-watchdog.timer /etc/systemd/system/nginx.service.d/pii-masking.conf "$MODEL_STAGE_HELPER" /etc/sudoers.d/pii-masking-model-stage "$model_prune_helper" /etc/systemd/system/pii-masking-model-gc.service /etc/systemd/system/pii-masking-model-gc.timer)
  modes=(644 755 440 644 644 644 755 440 755 644 644)
  labels=(nginx-server nginx-helper sudoers watchdog-service watchdog-timer nginx-ordering model-stage-helper model-stage-sudoers model-prune model-gc-service model-gc-timer)
}

target_matches() { sudo test -f "$2" && sudo cmp -s "$1" "$2"; }
show_plan() {
  local index current="$work_dir/current"
  echo "Preconditions: explicit private environment, active-slot evidence, stable port 18080, host bridge, sudoers, Nginx, and systemd syntax."
  echo "Verified active slot: $(<"$work_dir/verified-active-slot") on port $(<"$work_dir/verified-active-port")."
  echo "Preserve Nginx upstream without replacement: $NGINX_UPSTREAM_FILE"
  for index in "${!targets[@]}"; do
    if sudo test -L "${targets[$index]}"; then
      echo "Blocker: managed target is a symlink: ${targets[$index]}" >&2
      return 1
    fi
    if target_matches "${sources[$index]}" "${targets[$index]}"; then
      echo "unchanged ${labels[$index]} ${targets[$index]}"
    elif sudo test -e "${targets[$index]}"; then
      echo "replace ${labels[$index]} ${targets[$index]}"
      sudo cat -- "${targets[$index]}" > "$current"
      diff -u --label "current:${labels[$index]}" --label "proposed:${labels[$index]}" "$current" "${sources[$index]}" || true
    else
      echo "create ${labels[$index]} ${targets[$index]}"
      diff -u --label /dev/null --label "proposed:${labels[$index]}" /dev/null "${sources[$index]}" || true
    fi
  done
  echo "Commands on apply when relevant: atomic install; nginx -t; nginx -s reload; systemctl daemon-reload."
  watchdog_enabled=$(sudo systemctl is-enabled pii-masking-watchdog.timer 2>/dev/null || true)
  watchdog_active=$(sudo systemctl is-active pii-masking-watchdog.timer 2>/dev/null || true)
  watchdog_enabled=${watchdog_enabled:-disabled}; watchdog_active=${watchdog_active:-inactive}
  echo "Current watchdog state: enabled=$watchdog_enabled active=$watchdog_active."
  echo "Proposed service change: enable and start watchdog only after watchdog-gateway.sh validate succeeds."
  echo "Watchdog remains disabled unless watchdog-gateway.sh validate succeeds after installation."
  if [[ ! -e "$state_dir/active-slot" ]]; then
    echo "Watchdog enablement readiness: deferred until an active release exists."
  else
    echo "Watchdog enablement readiness: runtime validation will run after host files are installed."
  fi
  echo "Validated sudoers policy before installation: yes."
}

installer_preflight
register_files
show_plan
[[ "$operation" == plan ]] && exit 0
[[ "$approved" == true || "${APPROVE_PRODUCTION_MIGRATION:-}" == 1 ]] || {
  echo "Apply requires --approve or APPROVE_PRODUCTION_MIGRATION=1." >&2; exit 1;
}

# Approval can be separated from execution; re-check all evidence immediately before mutation.
installer_preflight
register_files
if sudo systemctl is-active pii-masking-watchdog.timer >/dev/null 2>&1; then
  echo "Stop the legacy watchdog timer before approved host mutation." >&2
  exit 1
fi
timestamp="$(date -u +%Y%m%dT%H%M%SZ).$$"
backup_dir="${INSTALL_BACKUP_ROOT:-$deploy_dir/.host-migration-backups}/$timestamp"
verified_active_port=$(<"$work_dir/verified-active-port")
watchdog_was_enabled=false; watchdog_was_active=false
sudo systemctl is-enabled pii-masking-watchdog.timer >/dev/null 2>&1 && watchdog_was_enabled=true
sudo systemctl is-active pii-masking-watchdog.timer >/dev/null 2>&1 && watchdog_was_active=true
sudo install -d -m 700 "$backup_dir"
changed=(); nginx_changed=false; systemd_changed=false
rollback() {
  local index target backup
  for target in "${targets[@]}"; do sudo rm -f -- "${target}.next.$$" || true; done
  for (( index=${#changed[@]}-1; index>=0; index-- )); do
    target=${targets[${changed[$index]}]}; backup="$backup_dir/${labels[${changed[$index]}]}"
    if sudo test -f "$backup"; then
      sudo install -D -m "${modes[${changed[$index]}]}" "$backup" "$target"
    else
      sudo rm -f -- "$target"
    fi
  done
  if [[ "$systemd_changed" == true ]]; then sudo systemctl daemon-reload || true; fi
  if [[ "$nginx_changed" == true ]]; then sudo nginx -t && sudo nginx -s reload || true; fi
  if [[ "$watchdog_was_enabled" == false ]]; then sudo systemctl disable pii-masking-watchdog.timer >/dev/null 2>&1 || true; fi
  if [[ "$watchdog_was_active" == false ]]; then sudo systemctl stop pii-masking-watchdog.timer >/dev/null 2>&1 || true; fi
}
trap 'status=$?; (( status == 0 )) || rollback; cleanup; exit "$status"' EXIT

for index in "${!targets[@]}"; do
  target=${targets[$index]}
  target_matches "${sources[$index]}" "$target" && continue
  sudo install -d -m 755 "$(dirname "$target")"
  if sudo test -f "$target"; then sudo install -m "${modes[$index]}" "$target" "$backup_dir/${labels[$index]}"; fi
  next="${target}.next.$$"
  sudo install -m "${modes[$index]}" "${sources[$index]}" "$next"
  sudo mv -f -- "$next" "$target"
  changed+=("$index")
  [[ "${labels[$index]}" == nginx-server ]] && nginx_changed=true
  [[ "${labels[$index]}" == watchdog-* || "${labels[$index]}" == nginx-ordering || "${labels[$index]}" == model-gc-* ]] && systemd_changed=true
done

sudo visudo -cf /etc/sudoers.d/pii-masking-nginx-control >/dev/null
sudo visudo -cf /etc/sudoers.d/pii-masking-model-stage >/dev/null
sudo install -d -m 0755 "$MODEL_RELEASES_ROOT"
sudo install -d -m 0700 "$model_work_root"
sudo nginx -t >/dev/null
if [[ "$systemd_changed" == true ]]; then sudo systemctl daemon-reload; fi
if [[ "$nginx_changed" == true ]]; then sudo nginx -s reload; fi
watchdog_enabled_now=false
if [[ ! -e "$state_dir/active-slot" ]]; then
  sudo systemctl disable --now pii-masking-watchdog.timer >/dev/null 2>&1 || true
  echo "Host files applied; watchdog enablement is deferred until a validated deployment writes matching release state."
elif sudo -u "$deploy_user" -H \
  "$deploy_dir/watchdog-gateway.sh" \
  validate \
  --env-file "$env_file" \
  --state-dir "$state_dir"; then
  sudo systemctl enable --now pii-masking-watchdog.timer
  watchdog_enabled_now=true
else
  echo "Recorded release state failed watchdog validation; rolling back host changes." >&2
  exit 1
fi
# Enabled only once every other step has succeeded, so a rollback never has to
# undo it. The collector is independent of the blue/green machinery: it reclaims
# unreferenced release directories and refuses to act at all if it cannot read
# the environment file.
sudo systemctl enable --now pii-masking-model-gc.timer
trap - EXIT
cleanup
echo "Applied transactional host configuration; the verified upstream remained on $verified_active_port; watchdog_enabled=$watchdog_enabled_now; model release gc enabled."
