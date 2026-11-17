#!/usr/bin/env bash
set -Eeuo pipefail

# Deploys the arena to the MLflow VPS, behind its existing Traefik reverse
# proxy, at https://<LEADERBOARD_DOMAIN>. A different host and a different
# exposure model from the (superseded) NiFi-VPS design: that one was
# SSH-tunnel-only; this one is public, with the masking-console API paths
# gated by HTTP basic auth enforced by Traefik itself (see arena-compose.yml).
#
# No blue/green, no masking canary, no capacity gate: this is a read-mostly UI
# whose failure must never block anything else. apply is a plain recreate.

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/vps-common.sh
source "$deploy_dir/vps-common.sh"

readonly PROJECT=pii-masking-arena
readonly COMPOSE_FILE="$deploy_dir/arena-compose.yml"

usage() {
  cat >&2 <<'EOF'
Usage:
  deploy-arena.sh preflight --env-file FILE --api-image-ref REF --ui-image-ref REF
    --git-sha SHA
  deploy-arena.sh apply [same options] --approve

Serves at https://$LEADERBOARD_DOMAIN once applied. The leaderboard and the
masking console are both public; uploads are bounded by the API's accepted
content types, ARENA_MAX_UPLOAD_BYTES, and the team prefixes in ARENA_TEAMS.
EOF
  exit 2
}

mode=${1:-}
[[ "$mode" == preflight || "$mode" == apply ]] || usage
shift

env_file=${ENV_FILE:-$deploy_dir/.env}
api_image_ref= ui_image_ref= git_sha=
approved=false

while (( $# )); do
  case "$1" in
    --env-file) env_file=${2:-}; shift 2 ;;
    --api-image-ref) api_image_ref=${2:-}; shift 2 ;;
    --ui-image-ref) ui_image_ref=${2:-}; shift 2 ;;
    --git-sha) git_sha=${2:-}; shift 2 ;;
    --approve) approved=true; shift ;;
    *) usage ;;
  esac
done

for required_argument in api_image_ref ui_image_ref git_sha; do
  [[ -n "${!required_argument}" ]] || { echo "Missing required argument: $required_argument" >&2; exit 2; }
done

# Both images must be pinned by digest, so a redeploy cannot silently pick up a
# different build behind a mutable tag.
validate_image_ref() {
  local name=$1 ref=$2
  [[ "$ref" == *@sha256:* ]] || vps_die "$name must be pinned by digest, not by tag."
  vps_validate_sha256 "$name" "${ref#*@}"
}

# The console can write into the source bucket, so its credential must never
# be the read-only QA dataset token, and never the gateway's service token.
validate_storage_settings() {
  local name
  for name in ARENA_S3_ENDPOINT_URL ARENA_S3_ACCESS_KEY_ID ARENA_S3_SECRET_ACCESS_KEY; do
    vps_require_var "$name"
    [[ "${!name}" != *replace-with* ]] ||
      vps_die "$name still holds an example placeholder."
  done
  [[ "$ARENA_S3_ENDPOINT_URL" == https://* ]] ||
    vps_die "ARENA_S3_ENDPOINT_URL must be an https endpoint."
  [[ "${ARENA_S3_SECRET_ACCESS_KEY}" != "${SERVICE_TOKEN:-}" ]] ||
    vps_die "ARENA_S3_SECRET_ACCESS_KEY must not reuse SERVICE_TOKEN."
}

# A published port failing late during container creation is confusing; the
# shared Traefik network missing entirely is worse; catch it up front instead.
validate_traefik_network() {
  local network=$1
  docker network inspect "$network" >/dev/null 2>&1 ||
    vps_die "Docker network '$network' does not exist. It should already be running as part of the mlflow-auth-gui project; this deployment never creates it."
}

echo "Validating arena deployment inputs."
vps_require_command docker
vps_require_command curl
vps_validate_env_file "$env_file"
vps_load_env_file "$env_file"

vps_require_var LEADERBOARD_DOMAIN
validate_image_ref ARENA_API_IMAGE_REF "$api_image_ref"
validate_image_ref ARENA_UI_IMAGE_REF "$ui_image_ref"
validate_storage_settings
validate_traefik_network "${MLFLOW_TRAEFIK_NETWORK:-mlflow-auth-gui_mlflow}"

[[ -f "$COMPOSE_FILE" && ! -L "$COMPOSE_FILE" ]] ||
  vps_die "arena-compose.yml is missing or is a symlink."

export ARENA_API_IMAGE_REF="$api_image_ref"
export ARENA_UI_IMAGE_REF="$ui_image_ref"

# Renders the file with the real values without starting anything.
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$env_file" config -q ||
  vps_die "arena-compose.yml does not render with the supplied environment."

echo "Preflight checks passed."
if [[ "$mode" == preflight ]]; then
  echo "No image was pulled, no container was started, and no host file changed."
  exit 0
fi

[[ "$approved" == true ]] || { echo "apply requires --approve" >&2; exit 2; }

echo "Authenticating to the container registry."
vps_require_var CI_REGISTRY
vps_require_var CI_REGISTRY_USER
vps_require_var CI_REGISTRY_PASSWORD
printf '%s' "$CI_REGISTRY_PASSWORD" | docker login "$CI_REGISTRY" --username "$CI_REGISTRY_USER" --password-stdin

echo "Pulling pinned arena images."
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$env_file" pull --quiet

echo "Starting the arena."
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$env_file" up -d --remove-orphans

# Verify the real public path: DNS, Traefik, TLS, and the container together.
# A failure here leaves the previous containers already replaced, acceptable
# for a read-mostly UI kept out of the gateway's transactional promotion.
public_url="https://$LEADERBOARD_DOMAIN"
echo "Verifying $public_url."
deadline=$(( SECONDS + 180 ))
until curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
    -o /dev/null "$public_url/health"; do
  (( SECONDS < deadline )) || vps_die "The arena did not become reachable at $public_url within 180s. Check DNS for $LEADERBOARD_DOMAIN, Traefik's routing, and certificate issuance."
  sleep 5
done

# Traefik's Docker provider needs a moment to notice a freshly recreated
# arena-ui container and update its routing table -- usually a second or two,
# occasionally more. The /health loop above already absorbs that window; a
# single unretried curl right after it does not, and has been observed to
# fail a deploy on a race that clears itself within seconds. Give this check
# the same grace, just over a shorter deadline since /health has already
# proven the route works at least once.
deadline=$(( SECONDS + 60 ))
until config_json=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
    "$public_url/api/v1/config"); do
  (( SECONDS < deadline )) || vps_die "The arena API is not reachable through the public UI proxy."
  sleep 3
done

printf '%s\n' "$config_json" | grep -q '"storage_configured":[[:space:]]*true' ||
  vps_die "The arena API reports object storage as unconfigured."

# The inverse of the check this replaces: the console must answer directly.
# A 401 here means a basic-auth middleware has been reattached to the console
# router, which shows up in a browser as an unexpected credential prompt the
# moment someone tries to upload. Same Traefik-registration grace as above.
echo "Verifying the console API answers without an authentication prompt."
deadline=$(( SECONDS + 60 ))
while :; do
  console_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 5 --max-time 15 "$public_url/api/v1/samples") || console_status=000
  [[ "$console_status" == "200" ]] && break
  (( SECONDS < deadline )) ||
    vps_die "Expected 200 from $public_url/api/v1/samples, got $console_status. A 401 means a basic-auth middleware is still attached to the console router."
  sleep 3
done

echo "Arena deployment complete."
echo "Public leaderboard: $public_url"
echo "Console API is open; uploads are bounded by content type, size, and team prefix."
