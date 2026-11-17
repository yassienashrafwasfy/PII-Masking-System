#!/usr/bin/env bash
set -Eeuo pipefail

deploy_dir=${PII_MASKING_DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
# shellcheck source=deploy/vps-common.sh
source "$deploy_dir/vps-common.sh"

usage() {
  echo "Usage: $0 {validate|run} --env-file FILE --state-dir DIR" >&2
  exit 2
}

mode=${1:-run}
[[ "$mode" == validate || "$mode" == run ]] || usage
shift || true
env_file=${ENV_FILE:-}
state_dir=${STATE_DIR:-$deploy_dir/state}
while (( $# )); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --state-dir) state_dir=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$env_file" ]] || { echo "An explicit --env-file or ENV_FILE is required." >&2; exit 1; }
vps_validate_absolute_path STATE_DIR "$state_dir"
vps_load_env_file "$env_file"
vps_require_var SERVICE_TOKEN
vps_require_var NGINX_UPSTREAM_FILE
vps_validate_absolute_path NGINX_UPSTREAM_FILE "$NGINX_UPSTREAM_FILE"
nginx_control_helper=${NGINX_CONTROL_HELPER:-/usr/local/sbin/pii-masking-nginx-control}
vps_validate_absolute_path NGINX_CONTROL_HELPER "$nginx_control_helper"
if [[ "$mode" == run ]]; then
  [[ -d "$state_dir" ]] || { echo "Watchdog state directory is missing." >&2; exit 1; }
  exec 8>"$state_dir/operation.lock"
  flock -n 8 || exit 0
fi

active_slot_file="$state_dir/active-slot"
[[ -s "$active_slot_file" ]] || { echo "No active deployment slot is recorded." >&2; exit 1; }
active_slot=$(<"$active_slot_file")
[[ "$active_slot" == blue || "$active_slot" == green ]] || { echo "Active deployment slot is invalid." >&2; exit 1; }
nginx_port=$(vps_parse_upstream_port "$NGINX_UPSTREAM_FILE")
nginx_slot=$(vps_slot_for_port "$nginx_port")
[[ "$nginx_slot" == "$active_slot" ]] || { echo "Active-slot state conflicts with Nginx evidence." >&2; exit 1; }
vps_project_gateway_running "$active_slot" || { echo "Recorded active gateway is not running." >&2; exit 1; }
if vps_project_gateway_running "$(vps_other_slot "$active_slot")"; then
  echo "Both slots are running; watchdog repair is disabled while state is ambiguous." >&2
  exit 1
fi
vps_listener_present "$nginx_port" || { echo "Recorded active gateway port is not listening." >&2; exit 1; }

release_file="$state_dir/releases/$active_slot.env"
vps_validate_env_file "$release_file"
set -a
# shellcheck disable=SC1090
source "$release_file"
set +a
for release_key in GATEWAY_IMAGE_DIGEST MODEL_SERVICE_IMAGE_DIGEST GIT_SHA STABLE_URL ENABLED_TEAMS GATEWAY_PORT ENV_FILE; do
  vps_require_var "$release_key"
done
IFS=',' read -r -a configured_teams <<< "$ENABLED_TEAMS"
seen_teams=,
for configured_team in "${configured_teams[@]}"; do
  case "$configured_team" in octo|synthify|berts) ;; *) echo "Active release contains an unsupported team ID." >&2; exit 1 ;; esac
  [[ "$seen_teams" != *",$configured_team,"* ]] || { echo "Active release contains a duplicate team ID." >&2; exit 1; }
  seen_teams+="$configured_team,"
done
[[ "$ENV_FILE" == "$env_file" ]] || { echo "Watchdog environment does not match active release state." >&2; exit 1; }
[[ "$GATEWAY_PORT" == "$nginx_port" ]] || { echo "Release port conflicts with Nginx evidence." >&2; exit 1; }
[[ "${STABLE_URL%/}" == http://127.0.0.1:18080 ]] || { echo "Watchdog stable URL must remain private port 18080." >&2; exit 1; }
direct_identity=$(vps_gateway_identity "http://127.0.0.1:$GATEWAY_PORT")
[[ "$direct_identity" == "$GIT_SHA|$GATEWAY_IMAGE_DIGEST" ]] || { echo "Active gateway provenance conflicts with release state." >&2; exit 1; }
sudo -n "$nginx_control_helper" exists
sudo -n "$nginx_control_helper" test

repair_cooldown_seconds=${WATCHDOG_REPAIR_COOLDOWN_SECONDS:-600}
repair_wait_seconds=${WATCHDOG_REPAIR_WAIT_SECONDS:-10}
[[ "$repair_cooldown_seconds" =~ ^[0-9]+$ ]] && (( repair_cooldown_seconds >= 60 && repair_cooldown_seconds <= 86400 )) || {
  echo "WATCHDOG_REPAIR_COOLDOWN_SECONDS must be between 60 and 86400." >&2; exit 1;
}
[[ "$repair_wait_seconds" =~ ^[0-9]+$ ]] && (( repair_wait_seconds <= 300 )) || {
  echo "WATCHDOG_REPAIR_WAIT_SECONDS must be between 0 and 300." >&2; exit 1;
}
[[ "$mode" == validate ]] && { echo "Watchdog configuration and active release evidence are valid."; exit 0; }

status_file="$state_dir/watchdog-status"
last_repair_file="$state_dir/watchdog-last-repair"
alert_webhook_url=${ALERT_WEBHOOK_URL:-}

send_alert() {
  local event=$1 detail=$2 team=${3:-} payload
  payload=$(python3 -c \
    'import json,sys; print(json.dumps({"event":sys.argv[1],"detail":sys.argv[2],"team":sys.argv[3] or None,"release":sys.argv[4],"host":sys.argv[5]},separators=(",",":")))' \
    "$event" "$detail" "$team" "$GIT_SHA" "$(hostname)")
  if [[ -z "$alert_webhook_url" ]]; then
    if command -v logger >/dev/null 2>&1; then logger -t pii-masking-watchdog -- "$payload"; fi
    printf '%s\n' "$payload" >&2
    return 0
  fi
  curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
    -H 'Content-Type: application/json' --data-binary "$payload" \
    "$alert_webhook_url" >/dev/null || echo "Watchdog alert delivery failed." >&2
}

mark_degraded() {
  local detail=$1 team=${2:-} previous=
  [[ ! -s "$status_file" ]] || previous=$(<"$status_file")
  printf 'degraded\n' > "$status_file"
  [[ "$previous" == degraded ]] || send_alert unhealthy "$detail" "$team"
}
mark_healthy() {
  local detail=$1 previous=
  [[ ! -s "$status_file" ]] || previous=$(<"$status_file")
  printf 'healthy\n' > "$status_file"
  [[ "$previous" != degraded ]] || send_alert recovered "$detail"
}
probe_json_status() {
  local expected=$1; shift
  curl --fail --silent --show-error --connect-timeout 5 --max-time 15 "$@" |
    python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"] == sys.argv[1]' "$expected"
}

failed_component= failed_team=
if ! probe_json_status UP "$STABLE_URL/health"; then
  failed_component=nginx
else
  for team in "${configured_teams[@]}"; do
    if ! probe_json_status READY -H "X-Team-ID: $team" "$STABLE_URL/ready"; then
      failed_component="$team-model"; failed_team=$team; break
    fi
  done
fi
[[ -n "$failed_component" ]] || { mark_healthy "All private gateway probes passed."; exit 0; }

now=$(date +%s); last_repair=0
if [[ -s "$last_repair_file" ]]; then last_repair=$(<"$last_repair_file"); [[ "$last_repair" =~ ^[0-9]+$ ]] || last_repair=0; fi
if (( now - last_repair < repair_cooldown_seconds )); then
  mark_degraded "Repair cooldown is active for $failed_component." "$failed_team"; exit 1
fi
mark_degraded "Probe failed for $failed_component; attempting one bounded restart." "$failed_team"
printf '%s\n' "$now" > "$last_repair_file"
if [[ "$failed_component" == nginx ]]; then
  repair=(sudo -n "$nginx_control_helper" restart)
else
  repair=(docker compose --project-name "pii-masking-$active_slot" --file "$deploy_dir/compose.yml" restart "$failed_component")
fi
"${repair[@]}" || { mark_degraded "Repair failed for $failed_component." "$failed_team"; exit 1; }
sleep "$repair_wait_seconds"
"$deploy_dir/verify-gateway.sh" "$STABLE_URL" "$GIT_SHA" "$GATEWAY_IMAGE_DIGEST" \
  "$MODEL_SERVICE_IMAGE_DIGEST" "$SERVICE_TOKEN" "$ENABLED_TEAMS" || {
    mark_degraded "Post-repair verification failed for $failed_component." "$failed_team"; exit 1;
  }
mark_healthy "Restarted and verified $failed_component."
