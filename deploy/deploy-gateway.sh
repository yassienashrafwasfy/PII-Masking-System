#!/usr/bin/env bash
set -Eeuo pipefail

deploy_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/vps-common.sh
source "$deploy_dir/vps-common.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  deploy-gateway.sh preflight --env-file FILE --state-dir DIR --gateway-image-ref REF
    --gateway-image-digest DIGEST --model-image-ref REF --model-image-digest DIGEST
    --git-sha SHA --stable-url URL
  deploy-gateway.sh apply [same options] --approve
EOF
  exit 2
}

mode=${1:-}
legacy=false
if [[ "$mode" == preflight || "$mode" == apply ]]; then shift; else legacy=true; mode=apply; fi
env_file=${ENV_FILE:-$deploy_dir/.env}
state_dir=${STATE_DIR:-$deploy_dir/state}
gateway_image_ref= gateway_image_digest= model_image_ref= model_image_digest= git_sha= stable_url=
approved=false

if [[ "$legacy" == true ]]; then
  (( $# == 6 )) || usage
  gateway_image_ref=$1; gateway_image_digest=$2; model_image_ref=$3
  model_image_digest=$4; git_sha=$5; stable_url=$6
else
  while (( $# )); do
    case "$1" in
      --env-file) env_file=${2:-}; shift 2 ;;
      --state-dir) state_dir=${2:-}; shift 2 ;;
      --gateway-image-ref) gateway_image_ref=${2:-}; shift 2 ;;
      --gateway-image-digest) gateway_image_digest=${2:-}; shift 2 ;;
      --model-image-ref) model_image_ref=${2:-}; shift 2 ;;
      --model-image-digest) model_image_digest=${2:-}; shift 2 ;;
      --git-sha) git_sha=${2:-}; shift 2 ;;
      --stable-url) stable_url=${2:-}; shift 2 ;;
      --approve) approved=true; shift ;;
      *) usage ;;
    esac
  done
fi
for required_argument in gateway_image_ref gateway_image_digest model_image_ref model_image_digest git_sha stable_url; do
  [[ -n "${!required_argument}" ]] || { echo "Missing required argument: $required_argument" >&2; exit 2; }
done
vps_validate_absolute_path STATE_DIR "$state_dir"

# Every model-identity key preflight requires is derived from the manifest in
# a staged release by this one command, so the remedy is spelled once rather
# than at each check. FILE and SHA are placeholders: the operator runs it with
# the same --env-file preflight was given and the release commit.
derive_model_identity='./stage-model-release.sh apply --env-file FILE --release-sha SHA --approve'
# Two checks have a supported way out that is not staging an artifact, and an
# operator staring at a missing key is exactly who needs to be told.
berts_routing_fallback=', or set BERTS_LANGUAGE_ROUTING=off to keep serving the Dutch checkpoint alone'
ocr_fallback=', or set PDF_OCR=off to leave the OCR fallback out of this deployment'

# The approved Berts artifacts, one per document language. Berts loads the
# checkpoint the document's language resolves to, so a release that stages
# only the Dutch one masks a French document with a model that is not there.
# All three are required, and all three are hashed before promotion.
expected_berts_model_name=berts-pii-medroberta-nl-int8
expected_berts_model_version=07f18eb53e127ed34aa77a64e1e15f24ea622be8
expected_berts_onnx_sha256=e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292
expected_berts_model_version_en=45f397a28f3efc462ec89552a7b8de574f3f61dc
expected_berts_onnx_sha256_en=c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd
expected_berts_model_version_fr=162c057100bb174252303bb350df4c47296ea5bd
expected_berts_onnx_sha256_fr=2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4
profile_args=()
octo_enabled=false; synthify_enabled=false; berts_enabled=false
ACTIVE_SLOT= ACTIVE_PORT= INACTIVE_SLOT= CANDIDATE_PORT= STATE_WAS_MISSING=false

validate_model_directory() {
  local model_dir=$1 mode owner current_uid resolved
  [[ "$model_dir" == /* && -d "$model_dir" && -r "$model_dir" && ! -L "$model_dir" ]] || {
    vps_die "Model directory must be absolute, readable, present, and not a symlink."; return
  }
  resolved=$(realpath -e "$model_dir")
  [[ "$resolved" == "$model_dir" && "$resolved" != */current/* && "$resolved" != */current ]] || {
    vps_die "Model directory must be immutable and must not use a current symlink."; return
  }
  mode=$(stat -Lc '%a' "$model_dir")
  (( (8#$mode & 022) == 0 )) || { vps_die "Model directory must not be group-writable or world-writable."; return; }
  owner=$(stat -Lc '%u' "$model_dir"); current_uid=$(id -u)
  [[ "$owner" == 0 || "$owner" == "$current_uid" ]] || vps_die "Model directory owner is not trusted."
}

validate_octo_artifacts() {
  local relative
  validate_model_directory "$OCTO_MODEL_DIR"
  for relative in onnx/model.onnx onnx/manifest.json onnx/config.json onnx/sentencepiece.bpe.model onnx/special_tokens_map.json onnx/tokenizer.json onnx/tokenizer_config.json; do
    vps_require_model_file "$OCTO_MODEL_DIR" "$relative"
  done
  vps_validate_sha256 OCTO_ONNX_MODEL_SHA256 "$OCTO_ONNX_MODEL_SHA256"
  vps_verify_model_hash "Octo ONNX" "$OCTO_MODEL_DIR/onnx/model.onnx" "$OCTO_ONNX_MODEL_SHA256"
}

validate_synthify_artifacts() {
  local relative
  validate_model_directory "$SYNTHIFY_MODEL_DIR"
  for relative in onnx/model.onnx onnx/manifest.json onnx/gliner_config.json onnx/special_tokens_map.json onnx/tokenizer.json onnx/tokenizer_config.json; do
    vps_require_model_file "$SYNTHIFY_MODEL_DIR" "$relative"
  done
  vps_validate_sha256 SYNTHIFY_ONNX_MODEL_DIGEST "$SYNTHIFY_ONNX_MODEL_DIGEST"
  vps_verify_model_hash "Synthify ONNX" "$SYNTHIFY_MODEL_DIR/onnx/model.onnx" "$SYNTHIFY_ONNX_MODEL_DIGEST"
}

require_approved_value() {
  # Six identical comparisons collapsed into one, so a seventh approved value
  # is a row rather than another line of copied text and message wording.
  local name=$1 expected=$2 noun=$3
  [[ "${!name}" == "$expected" ]] || vps_die "$name does not match the approved $noun."
}

validate_berts_routing_switch() {
  # Deliberately separate from the query below. `vps_die` reports by returning
  # non-zero, and a non-zero return inside an `if` condition is the condition,
  # not a failure -- so a validating predicate would read "of" as "off" and
  # silently leave the rollback un-taken. Validity is checked once, here,
  # where a failure propagates.
  local value=${BERTS_LANGUAGE_ROUTING:-on}
  [[ "$value" == on || "$value" == off ]] || vps_die "BERTS_LANGUAGE_ROUTING must be one of: off, on."
}

berts_language_routing() {
  # Pure query, safe in a condition: never dies, never reports anything but
  # the answer. `validate_berts_routing_switch` has already refused a typo.
  [[ "${BERTS_LANGUAGE_ROUTING:-on}" != off ]]
}

validate_berts_checkpoint() {
  # One staged language directory: every file the runtime opens is present,
  # the graph hashes to the digest this release approved, and the manifest
  # beside it agrees about both. A missing directory is fatal here rather
  # than at the first document in that language.
  local subdir=$1 expected_sha=$2 expected_revision=$3 resolved_model=$4
  local required_file
  for required_file in model.onnx config.json tokenizer.json tokenizer_config.json manifest.json; do
    vps_require_model_file "$resolved_model" "$subdir/$required_file"
  done
  vps_verify_model_hash "BERTS $subdir" "$resolved_model/$subdir/model.onnx" "$expected_sha"
  python3 - "$resolved_model/$subdir/manifest.json" "$expected_sha" "$expected_revision" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest.get("format") != "onnx": raise SystemExit("BERTS manifest format must be onnx.")
if manifest.get("precision") != "int8": raise SystemExit("BERTS manifest precision must be int8.")
if manifest.get("model_sha256") != sys.argv[2]: raise SystemExit("BERTS manifest model_sha256 does not match the configured digest.")
if manifest.get("source_revision") != sys.argv[3]: raise SystemExit("BERTS manifest source_revision does not match the configured revision.")
PY
}

validate_berts_artifacts() {
  local releases_root lexical_root lexical_relative resolved_root resolved_model relative required_file
  releases_root=${MODEL_RELEASES_ROOT:-/srv/pii-models/releases}
  validate_model_directory "$BERTS_MODEL_DIR"
  vps_validate_absolute_path MODEL_RELEASES_ROOT "$releases_root"
  lexical_root=${releases_root%/}; lexical_relative=${BERTS_MODEL_DIR#"$lexical_root"/}
  [[ "$lexical_relative" != "$BERTS_MODEL_DIR" && "$lexical_relative" =~ ^[^/]+/berts$ ]] || { vps_die "BERTS_MODEL_DIR must use $lexical_root/<release-sha>/berts."; return; }
  [[ "${lexical_relative%%/*}" != current ]] || vps_die "BERTS_MODEL_DIR must not use a mutable current release path."
  resolved_root=$(realpath -e "$releases_root"); resolved_model=$(realpath -e "$BERTS_MODEL_DIR")
  relative=${resolved_model#"$resolved_root"/}
  [[ "$relative" != "$resolved_model" && "$relative" =~ ^[^/]+/berts$ ]] || vps_die "BERTS_MODEL_DIR must resolve inside the immutable releases root."
  # Beyond the five files every checkpoint has, Dutch carries a byte-level BPE
  # vocabulary in two more.
  for required_file in onnx/special_tokens_map.json onnx/vocab.json onnx/merges.txt; do
    vps_require_model_file "$resolved_model" "$required_file"
  done
  vps_validate_sha256 BERTS_ONNX_MODEL_SHA256 "$BERTS_ONNX_MODEL_SHA256"
  validate_berts_checkpoint onnx "$BERTS_ONNX_MODEL_SHA256" "$BERTS_MODEL_VERSION" "$resolved_model"
  # With routing off only the default checkpoint is ever opened, so requiring
  # the other two would refuse a rollback for artifacts it will not read.
  berts_language_routing || return 0
  # French adds a SentencePiece special-token map; the English tokenizer is
  # self-contained in tokenizer.json.
  vps_require_model_file "$resolved_model" onnx-fr/special_tokens_map.json
  vps_validate_sha256 BERTS_ONNX_MODEL_SHA256_EN "$BERTS_ONNX_MODEL_SHA256_EN"
  vps_validate_sha256 BERTS_ONNX_MODEL_SHA256_FR "$BERTS_ONNX_MODEL_SHA256_FR"
  validate_berts_checkpoint onnx-en "$BERTS_ONNX_MODEL_SHA256_EN" "$BERTS_MODEL_VERSION_EN" "$resolved_model"
  validate_berts_checkpoint onnx-fr "$BERTS_ONNX_MODEL_SHA256_FR" "$BERTS_MODEL_VERSION_FR" "$resolved_model"
}

validate_ocr_artifacts() {
  local relative
  # Deployment-wide rather than per-team: one PDF_OCR switch serves whichever
  # teams are enabled, so this is gated on the switch and not on a profile.
  validate_model_directory "$OCR_MODEL_DIR"
  for relative in det/model.onnx rec/model.onnx rec/charset.txt; do
    vps_require_model_file "$OCR_MODEL_DIR" "$relative"
  done
  vps_validate_sha256 OCR_DET_SHA256 "$OCR_DET_SHA256"
  vps_validate_sha256 OCR_REC_SHA256 "$OCR_REC_SHA256"
  vps_validate_sha256 OCR_CHARSET_SHA256 "$OCR_CHARSET_SHA256"
  # The hashes are the point of this function. The service's own check asks
  # only whether these paths are files, and a 130-byte Git LFS pointer is a
  # file -- so without this a deployment staged from a checkout that skipped
  # smudge starts clean and fails on its first scanned page.
  vps_verify_model_hash "OCR detection" "$OCR_MODEL_DIR/det/model.onnx" "$OCR_DET_SHA256"
  vps_verify_model_hash "OCR recognition" "$OCR_MODEL_DIR/rec/model.onnx" "$OCR_REC_SHA256"
  vps_verify_model_hash "OCR charset" "$OCR_MODEL_DIR/rec/charset.txt" "$OCR_CHARSET_SHA256"
}

validate_image_identity() {
  local label=$1 reference=$2 digest=$3
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || vps_die "$label digest must be an immutable SHA-256."
  [[ "$reference" == *@"$digest" ]] || vps_die "$label reference must be pinned to its supplied digest."
}

validate_teams_and_models() {
  local enabled_teams=$1 team seen=,
  profile_args=(); octo_enabled=false; synthify_enabled=false; berts_enabled=false
  IFS=',' read -r -a teams <<< "$enabled_teams"
  (( ${#teams[@]} > 0 )) || vps_die "ENABLED_TEAMS must not be empty."
  for team in "${teams[@]}"; do
    [[ "$seen" != *",$team,"* ]] || vps_die "ENABLED_TEAMS contains a duplicate team: $team"
    seen+="$team,"
    case "$team" in
      octo) octo_enabled=true; profile_args+=(--profile octo) ;;
      synthify) synthify_enabled=true; profile_args+=(--profile synthify) ;;
      berts) berts_enabled=true; profile_args+=(--profile berts) ;;
      *) vps_die "Unsupported production team: $team" ;;
    esac
  done
  if [[ "$octo_enabled" == true ]]; then
    vps_require_vars "$derive_model_identity" \
      OCTO_MODEL_DIR OCTO_MODEL_VERSION OCTO_ONNX_MODEL_SHA256
    validate_octo_artifacts
  fi
  if [[ "$synthify_enabled" == true ]]; then
    vps_require_vars "$derive_model_identity" \
      SYNTHIFY_MODEL_DIR SYNTHIFY_MODEL_VERSION \
      SYNTHIFY_ONNX_MODEL_DIGEST
    validate_synthify_artifacts
  fi
  export BERTS_MODEL_URL=
  if [[ "$berts_enabled" == true ]]; then
    validate_berts_routing_switch
    vps_require_vars "$derive_model_identity" \
      BERTS_MODEL_DIR BERTS_MODEL_NAME BERTS_MODEL_VERSION BERTS_ONNX_MODEL_SHA256
    require_approved_value BERTS_MODEL_NAME "$expected_berts_model_name" model
    require_approved_value BERTS_MODEL_VERSION "$expected_berts_model_version" revision
    require_approved_value BERTS_ONNX_MODEL_SHA256 "$expected_berts_onnx_sha256" artifact
    if berts_language_routing; then
      # The group that a single-checkpoint deployment has never held. If
      # this release is the one that introduces per-language routing, all
      # four are missing at once and `BERTS_LANGUAGE_ROUTING=off` is the
      # other way out, so the operator is told about both.
      vps_require_vars "$derive_model_identity$berts_routing_fallback" \
        BERTS_MODEL_VERSION_EN BERTS_ONNX_MODEL_SHA256_EN \
        BERTS_MODEL_VERSION_FR BERTS_ONNX_MODEL_SHA256_FR
      require_approved_value BERTS_MODEL_VERSION_EN "$expected_berts_model_version_en" revision
      require_approved_value BERTS_ONNX_MODEL_SHA256_EN "$expected_berts_onnx_sha256_en" artifact
      require_approved_value BERTS_MODEL_VERSION_FR "$expected_berts_model_version_fr" revision
      require_approved_value BERTS_ONNX_MODEL_SHA256_FR "$expected_berts_onnx_sha256_fr" artifact
    fi
    validate_berts_artifacts
    export BERTS_MODEL_URL=http://berts-model:8080
  fi
}

validate_capacity_policy() {
  local minimum=${MIN_AVAILABLE_MEMORY_PERCENT:-5} meminfo=${MEMINFO_FILE:-/proc/meminfo} total_kib available_kib available_percent
  [[ "$minimum" =~ ^[0-9]+$ ]] && (( minimum >= 5 && minimum < 100 )) || \
    vps_die "MIN_AVAILABLE_MEMORY_PERCENT must remain between 5 and 99."
  [[ -r "$meminfo" ]] || vps_die "Memory information is unavailable."
  total_kib=$(awk '/^MemTotal:/ {print $2}' "$meminfo")
  available_kib=$(awk '/^MemAvailable:/ {print $2}' "$meminfo")
  [[ "$total_kib" =~ ^[1-9][0-9]*$ ]] || vps_die "MemTotal is invalid."
  [[ "$available_kib" =~ ^[1-9][0-9]*$ ]] || vps_die "MemAvailable is invalid."
  available_percent=$((available_kib * 100 / total_kib))
  echo "Preflight memory observation: ${available_percent}% currently available; post-start gate remains ${minimum}%."
  if [[ "$berts_enabled" == true ]] && (( total_kib < 16 * 1024 * 1024 )); then
    [[ "${BERTS_WARM_RSS_MIB:-}" =~ ^[1-9][0-9]*$ ]] || { vps_die "BERTS_WARM_RSS_MIB is required on hosts below 16 GiB; ONNX size is not runtime RSS."; return; }
  elif [[ "$berts_enabled" == true && ! "${BERTS_WARM_RSS_MIB:-}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Warning: BERTS warm RSS is unmeasured; the host-size recommendation does not replace runtime measurement." >&2
  fi
}

run_preflight() {
  local enabled_teams expected_stable_url
  for command_name in awk bash curl docker flock grep id python3 realpath sed sha256sum ss stat sudo; do vps_require_command "$command_name"; done
  vps_load_env_file "$env_file"
  vps_require_var SERVICE_TOKEN; vps_require_var NGINX_UPSTREAM_FILE
  vps_validate_absolute_path NGINX_UPSTREAM_FILE "$NGINX_UPSTREAM_FILE"
  vps_validate_absolute_path NGINX_CONTROL_HELPER "${NGINX_CONTROL_HELPER:-/usr/local/sbin/pii-masking-nginx-control}"
  enabled_teams=${ENABLED_TEAMS:-octo,synthify}
  private_gateway_port=${PRIVATE_GATEWAY_PORT:-18080}
  [[ "$private_gateway_port" == 18080 ]] || vps_die "PRIVATE_GATEWAY_PORT must preserve stable port 18080."
  expected_stable_url="http://127.0.0.1:$private_gateway_port"
  [[ "${stable_url%/}" == "$expected_stable_url" ]] || vps_die "Stable verification URL must be $expected_stable_url."
  validate_image_identity Gateway "$gateway_image_ref" "$gateway_image_digest"
  validate_image_identity Model-service "$model_image_ref" "$model_image_digest"
  validate_teams_and_models "$enabled_teams"
  # Verified here, before any image pull, because the service verifies far
  # later than it looks: load_settings is cached and first runs on a request,
  # and the OCR engine is built on the first page that needs it.
  # Default matches compose.yml. If these two disagree an unset PDF_OCR runs
  # OCR with its artifacts never verified, which is the failure this checks for.
  if [[ "${PDF_OCR:-on}" == on ]]; then
    vps_require_vars "$derive_model_identity$ocr_fallback" \
      OCR_MODEL_DIR OCR_DET_SHA256 OCR_REC_SHA256 OCR_CHARSET_SHA256
    validate_ocr_artifacts
  fi
  validate_capacity_policy
  sudo -n "${NGINX_CONTROL_HELPER:-/usr/local/sbin/pii-masking-nginx-control}" exists
  sudo -n "${NGINX_CONTROL_HELPER:-/usr/local/sbin/pii-masking-nginx-control}" test
  STATE_WAS_MISSING=false; [[ -e "$state_dir/active-slot" ]] || STATE_WAS_MISSING=true
  ACTIVE_SLOT=$(vps_discover_active_slot "$state_dir" "$expected_stable_url")
  ACTIVE_PORT=$(vps_port_for_slot "$ACTIVE_SLOT"); INACTIVE_SLOT=$(vps_other_slot "$ACTIVE_SLOT"); CANDIDATE_PORT=$(vps_port_for_slot "$INACTIVE_SLOT")
  if vps_project_gateway_running "$INACTIVE_SLOT"; then vps_die "Candidate Compose project collides with a running gateway."; fi
  if vps_listener_present "$CANDIDATE_PORT"; then vps_die "Candidate port is already listening without an approved candidate."; fi
  export GATEWAY_IMAGE_REF="$gateway_image_ref" GATEWAY_IMAGE_DIGEST="$gateway_image_digest"
  export MODEL_SERVICE_IMAGE_REF="$model_image_ref" MODEL_SERVICE_IMAGE_DIGEST="$model_image_digest"
  export GIT_SHA="$git_sha" SERVICE_RELEASE="$git_sha" GATEWAY_PORT="$CANDIDATE_PORT"
  docker compose --project-name "pii-masking-$INACTIVE_SLOT" --file "$deploy_dir/compose.yml" "${profile_args[@]}" config --quiet
  if [[ "$STATE_WAS_MISSING" == true ]]; then echo "Inferred active slot: $ACTIVE_SLOT (not persisted during preflight)."; else echo "Verified active slot: $ACTIVE_SLOT."; fi
  echo "Preflight passed for candidate slot $INACTIVE_SLOT on port $CANDIDATE_PORT."
}

[[ "$mode" == preflight ]] && { run_preflight; exit 0; }
[[ "$approved" == true || "${APPROVE_PRODUCTION_MIGRATION:-}" == 1 ]] || { echo "Apply requires --approve or APPROVE_PRODUCTION_MIGRATION=1." >&2; exit 1; }
run_preflight
install -d -m 700 "$state_dir" "$state_dir/releases"
exec 9>"$state_dir/operation.lock"
flock -n 9 || { echo "Another deployment or watchdog repair is running." >&2; exit 1; }
# Close the preflight/lock race before the first approved state mutation.
run_preflight
if [[ "$STATE_WAS_MISSING" == true ]]; then
  printf '%s\n' "$ACTIVE_SLOT" > "$state_dir/active-slot.next"; chmod 600 "$state_dir/active-slot.next"; mv "$state_dir/active-slot.next" "$state_dir/active-slot"
  echo "Persisted verified migration baseline: $ACTIVE_SLOT."
fi

nginx_control_helper=${NGINX_CONTROL_HELPER:-/usr/local/sbin/pii-masking-nginx-control}
nginx_switch_wait_seconds=${NGINX_SWITCH_WAIT_SECONDS:-30}; drain_max_seconds=${DRAIN_MAX_SECONDS:-6600}; drain_poll_seconds=${DRAIN_POLL_SECONDS:-5}
minimum_available_memory_percent=${MIN_AVAILABLE_MEMORY_PERCENT:-5}; enabled_teams=${ENABLED_TEAMS:-octo,synthify}
compose=(docker compose --project-name "pii-masking-$INACTIVE_SLOT" --file "$deploy_dir/compose.yml" "${profile_args[@]}")
active_project="pii-masking-$ACTIVE_SLOT"
release_next=$(mktemp "$state_dir/releases/.${INACTIVE_SLOT}.XXXXXX"); switched=false
cleanup_files() { rm -f "$release_next"; }
rollback() { if [[ "$switched" == true ]]; then sudo -n "$nginx_control_helper" switch "$ACTIVE_PORT" || echo "Rollback could not restore Nginx." >&2; fi; "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true; }
finish() { status=$?; (( status == 0 )) || rollback; cleanup_files; exit "$status"; }
trap finish EXIT

check_memory_headroom() {
  local total available percent meminfo=${MEMINFO_FILE:-/proc/meminfo}
  total=$(awk '/^MemTotal:/ {print $2}' "$meminfo"); available=$(awk '/^MemAvailable:/ {print $2}' "$meminfo"); percent=$((available * 100 / total))
  (( percent >= minimum_available_memory_percent )) || { echo "Candidate leaves ${percent}% memory available; ${minimum_available_memory_percent}% is required." >&2; return 1; }
  echo "Memory gate passed with ${percent}% available."
}
wait_for_stable_candidate() {
  local deadline=$((SECONDS + nginx_switch_wait_seconds)) identity
  while (( SECONDS < deadline )); do
    if identity=$(vps_gateway_identity "http://127.0.0.1:${PRIVATE_GATEWAY_PORT:-18080}") && [[ "$identity" == "$git_sha|$gateway_image_digest" ]]; then
      echo "Stable Nginx endpoint serves candidate $git_sha."
      return 0
    fi
    sleep 1
  done
  echo "Stable endpoint did not expose the candidate release in time." >&2; return 1
}
wait_for_drain() {
  local deadline connections
  (( drain_max_seconds > 0 )) || return 0
  deadline=$((SECONDS + drain_max_seconds))
  while true; do
    connections=$(ss -Htan | awk -v suffix=":$ACTIVE_PORT" '$1 == "ESTAB" && $(NF-1) ~ suffix "$" {count++} END {print count+0}')
    if (( connections == 0 )); then echo "Old slot on port $ACTIVE_PORT has drained."; return 0; fi
    if (( SECONDS >= deadline )); then echo "Drain deadline reached for old slot on port $ACTIVE_PORT."; return 0; fi
    sleep "$drain_poll_seconds"
  done
}

docker pull "$gateway_image_ref"; docker pull "$model_image_ref"
"${compose[@]}" up --detach --wait --wait-timeout 600 --remove-orphans
"$deploy_dir/verify-gateway.sh" "http://127.0.0.1:$CANDIDATE_PORT" "$git_sha" "$gateway_image_digest" "$model_image_digest" "$SERVICE_TOKEN" "$enabled_teams"
check_memory_headroom
vps_listener_present "$CANDIDATE_PORT" || { echo "Candidate port is not listening." >&2; exit 1; }
vps_gateway_identity "http://127.0.0.1:$CANDIDATE_PORT" >/dev/null
switched=true; sudo -n "$nginx_control_helper" switch "$CANDIDATE_PORT"; wait_for_stable_candidate
"$deploy_dir/verify-gateway.sh" "http://127.0.0.1:${PRIVATE_GATEWAY_PORT:-18080}" "$git_sha" "$gateway_image_digest" "$model_image_digest" "$SERVICE_TOKEN" "$enabled_teams"
{
  printf 'SLOT=%q\n' "$INACTIVE_SLOT"; printf 'GATEWAY_PORT=%q\n' "$CANDIDATE_PORT"; printf 'GATEWAY_IMAGE_REF=%q\n' "$gateway_image_ref"
  printf 'GATEWAY_IMAGE_DIGEST=%q\n' "$gateway_image_digest"; printf 'MODEL_SERVICE_IMAGE_REF=%q\n' "$model_image_ref"
  printf 'MODEL_SERVICE_IMAGE_DIGEST=%q\n' "$model_image_digest"; printf 'GIT_SHA=%q\n' "$git_sha"; printf 'SERVICE_RELEASE=%q\n' "$git_sha"
  printf 'STABLE_URL=%q\n' "http://127.0.0.1:${PRIVATE_GATEWAY_PORT:-18080}"; printf 'ENABLED_TEAMS=%q\n' "$enabled_teams"; printf 'ENV_FILE=%q\n' "$env_file"
} > "$release_next"
chmod 600 "$release_next"; mv "$release_next" "$state_dir/releases/$INACTIVE_SLOT.env"
printf '%s\n' "$INACTIVE_SLOT" > "$state_dir/active-slot.next"; chmod 600 "$state_dir/active-slot.next"; mv "$state_dir/active-slot.next" "$state_dir/active-slot"
wait_for_drain
docker compose --project-name "$active_project" --file "$deploy_dir/compose.yml" down --remove-orphans >/dev/null 2>&1 || true
trap - EXIT; cleanup_files
echo "Deployed $git_sha to $INACTIVE_SLOT on port $CANDIDATE_PORT."
