#!/usr/bin/env bash
set -Eeuo pipefail

# Interactively builds a .env file for deploy-arena.sh, handling the one step
# that is easy to get subtly wrong by hand: encoding the basic-auth hash so it
# survives both Docker Compose's own .env parsing and this repo's bash-sourced
# validation intact (see README.md's "Generating the basic-auth credential"
# section for exactly what goes wrong otherwise).
#
# Secrets are read with the terminal echo suppressed and are never passed as a
# command-line argument, so they never land in shell history or a process
# listing. Run this ON the VPS, not through any automated tool -- it is
# meant to be typed into an interactive terminal by a human.

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
output=${1:-$deploy_dir/.env}

if [[ -e "$output" ]]; then
  echo "Refusing to overwrite an existing file: $output" >&2
  echo "Move it aside first if you really want to regenerate it." >&2
  exit 1
fi

command -v openssl >/dev/null 2>&1 || { echo "openssl is required." >&2; exit 1; }

prompt() {
  local label=$1 default=${2:-} value
  if [[ -n "$default" ]]; then
    read -r -p "$label [$default]: " value
    printf '%s\n' "${value:-$default}"
  else
    read -r -p "$label: " value
    printf '%s\n' "$value"
  fi
}

prompt_secret() {
  local label=$1 value confirm
  while true; do
    read -r -s -p "$label: " value; echo >&2
    read -r -s -p "Confirm $label: " confirm; echo >&2
    [[ -n "$value" ]] || { echo "  Must not be empty." >&2; continue; }
    [[ "$value" == "$confirm" ]] && { printf '%s\n' "$value"; return; }
    echo "  Did not match; try again." >&2
  done
}

echo "== Public domain and Traefik wiring ==" >&2
echo "The Traefik defaults below match this VPS's existing mlflow.me router." >&2
domain=$(prompt "LEADERBOARD_DOMAIN" "leaderboard.mlflow.me")
traefik_network=$(prompt "MLFLOW_TRAEFIK_NETWORK" "mlflow-auth-gui_mlflow")
traefik_entrypoint=$(prompt "MLFLOW_TRAEFIK_ENTRYPOINT" "websecure")
traefik_certresolver=$(prompt "MLFLOW_TRAEFIK_CERTRESOLVER" "letsEncrypt")

echo >&2
echo "== Object storage (Cloudflare R2) ==" >&2
echo "This must be a NEW, dedicated token -- never the QA dataset token (read-only," >&2
echo "scoped to Validation_Datasets/) and never SERVICE_TOKEN. It needs write on" >&2
echo "source/incoming/ and read on destination/masked/, destination/manifests/," >&2
echo "and data/results/. If Cloudflare's R2 API token UI cannot mix a write scope" >&2
echo "on one bucket with a read scope on others in a single token, the practical" >&2
echo "fallback is one token with Object Read & Write scoped to exactly these three" >&2
echo "buckets (source, destination, data) -- broader than ideal, bounded by what" >&2
echo "the token UI actually supports." >&2
r2_endpoint=$(prompt "R2 endpoint URL (Cloudflare dashboard -> R2 -> Overview)")
r2_key_id=$(prompt_secret "R2 access key ID")
r2_secret=$(prompt_secret "R2 secret access key")

umask 077
{
  echo "LEADERBOARD_DOMAIN=$domain"
  echo "MLFLOW_TRAEFIK_NETWORK=$traefik_network"
  echo "MLFLOW_TRAEFIK_ENTRYPOINT=$traefik_entrypoint"
  echo "MLFLOW_TRAEFIK_CERTRESOLVER=$traefik_certresolver"
  echo
  echo "ARENA_S3_ENDPOINT_URL=$r2_endpoint"
  echo "ARENA_S3_ACCESS_KEY_ID=$r2_key_id"
  echo "ARENA_S3_SECRET_ACCESS_KEY=$r2_secret"
  echo "ARENA_S3_REGION=auto"
  echo
  echo "ARENA_RESULTS_BUCKET=data"
  echo "ARENA_RESULTS_PREFIX=results"
  echo "ARENA_HISTORY_PREFIX=results/history"
  echo "ARENA_ARCHIVE_RANKINGS=true"
  echo "ARENA_SOURCE_BUCKET=source"
  echo "ARENA_INCOMING_PREFIX=incoming"
  echo 'ARENA_INCOMING_KEY_TEMPLATE={prefix}/{team}/requests/{run_id}-{filename}'
  echo "ARENA_MASKED_BUCKET=destination"
  echo "ARENA_MASKED_PREFIX=masked"
  echo "ARENA_MANIFEST_BUCKET="
  echo "ARENA_MANIFEST_PREFIX=manifests"
  echo
  echo "ARENA_TEAMS=octo,synthify,berts"
  echo "ARENA_CHAMPION_FALLBACK_TEAM=berts"
  echo "ARENA_RUN_TIMEOUT_SECONDS=900"
  echo "ARENA_MAX_UPLOAD_BYTES=10485760"
  # False, matching .env.example and the code default. Left true, the mock
  # short-circuits the real reader and hides QA's published results behind
  # randomized preview data -- on a file whose whole purpose is production.
  echo "ARENA_MOCK_LEADERBOARD=false"
} > "$output"
chmod 600 "$output"

unset r2_secret r2_key_id

echo >&2
echo "Wrote $output (mode 0600)." >&2
echo "Review it, then run:" >&2
echo "  ./deploy-arena.sh preflight --env-file '$output' --api-image-ref ... --ui-image-ref ... --git-sha ..." >&2
