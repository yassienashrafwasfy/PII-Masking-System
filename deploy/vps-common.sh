#!/usr/bin/env bash

# Shared, side-effect-free validation helpers for VPS deployment commands.

vps_die() {
  echo "$*" >&2
  return 1
}

vps_require_command() {
  command -v "$1" >/dev/null 2>&1 || vps_die "Required command is missing: $1"
}

vps_validate_absolute_path() {
  local name=$1 value=$2
  [[ "$value" =~ ^/[A-Za-z0-9_./-]+$ && "/$value/" != *"/../"* && "/$value/" != *"/./"* ]] ||
    vps_die "$name must be an absolute normalized path without whitespace."
}

vps_validate_env_file() {
  local env_file=$1
  local trusted_user=${2:-}
  local mode owner current_uid trusted_uid

  [[ "$env_file" == /* && -f "$env_file" && -r "$env_file" && ! -L "$env_file" ]] || {
    vps_die "ENV_FILE must be an absolute, readable regular file."
    return
  }

  mode=$(stat -Lc '%a' "$env_file")
  owner=$(stat -Lc '%u' "$env_file")

  (( (8#$mode & 077) == 0 )) || {
    vps_die "ENV_FILE must not be group-readable or world-readable."
    return
  }

  current_uid=$(id -u)

  # Preserve the original trust model for existing callers.
  if [[ "$owner" == 0 || "$owner" == "$current_uid" ]]; then
    return 0
  fi

  # When the installer is run through sudo, also trust the explicitly
  # configured deployment user.
  if [[ -n "$trusted_user" ]]; then
    [[ "$trusted_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || {
      vps_die "Trusted deployment user is invalid."
      return
    }

    if trusted_uid=$(id -u "$trusted_user" 2>/dev/null); then
      [[ "$owner" == "$trusted_uid" ]] && return 0
    fi
  fi

  vps_die "ENV_FILE must be owned by the deployment user or root."
  return
}

vps_load_env_file() {
  local env_file=$1
  local trusted_user=${2:-}

  vps_validate_env_file "$env_file" "$trusted_user"

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

vps_require_var() {
  local name=$1
  [[ -n "${!name:-}" && "${!name}" != __UNRESOLVED__ ]] || vps_die "Missing required environment variable: $name"
}

vps_require_vars() {
  # Report every key the environment file lacks, not only the first one.
  #
  # A release that widens the schema is the case this exists for. The live
  # file predates the release, so it is missing the whole group of new keys at
  # once, and dying on the first name sends the operator round the loop once
  # per key -- and each round is a manual production job that has to be
  # approved. Preflight is the one place that already knows the full list.
  #
  # *hint* names the command that derives the values, because knowing which
  # key is missing is not the same as knowing where it comes from: every key
  # checked through here is a digest or an upstream revision read out of a
  # staged manifest, and none of them is a value to type by hand.
  local hint=$1 name
  local missing=()
  shift
  for name in "$@"; do
    if [[ -z "${!name:-}" || "${!name}" == __UNRESOLVED__ ]]; then
      missing+=("$name")
    fi
  done
  if (( ${#missing[@]} == 0 )); then
    return 0
  fi
  printf 'Environment file is missing %d key(s) this release requires:\n' \
    "${#missing[@]}" >&2
  printf '  %s\n' "${missing[@]}" >&2
  [[ -z "$hint" ]] || printf 'Derive them: %s\n' "$hint" >&2
  # Last statement on purpose: `vps_die` reports by returning non-zero, so
  # anything after it here would swallow the failure.
  vps_die "Environment file does not satisfy this release's schema."
}

vps_raw_sha256() {
  local value=$1
  printf '%s\n' "${value#sha256:}"
}

vps_validate_sha256() {
  local name=$1 value
  value=$(vps_raw_sha256 "$2")
  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || vps_die "$name must contain a lowercase 64-character SHA-256."
}

vps_require_model_file() {
  local model_dir=$1 relative=$2 path mode owner current_uid
  path="$model_dir/$relative"
  [[ -f "$path" && -r "$path" && ! -L "$path" ]] || { vps_die "Required model artifact is missing, unreadable, or a symlink: $relative"; return; }
  mode=$(stat -Lc '%a' "$path"); owner=$(stat -Lc '%u' "$path"); current_uid=$(id -u)
  (( (8#$mode & 022) == 0 )) || { vps_die "Model artifact must not be group-writable or world-writable: $relative"; return; }
  [[ "$owner" == 0 || "$owner" == "$current_uid" ]] || vps_die "Model artifact owner is not trusted: $relative"
}

vps_verify_model_hash() {
  local label=$1 file=$2 expected=$3 actual
  expected=$(vps_raw_sha256 "$expected")
  actual=$(sha256sum "$file" | awk '{print $1}')
  [[ "$actual" == "$expected" ]] || vps_die "$label SHA-256 mismatch."
}

vps_parse_upstream_port() {
  local upstream_file=$1 ports mode owner current_uid
  [[ -f "$upstream_file" && -r "$upstream_file" ]] || {
    vps_die "Configured Nginx upstream file is missing or unreadable."
    return
  }
  [[ ! -L "$upstream_file" ]] || { vps_die "Configured Nginx upstream file must not be a symlink."; return; }
  mode=$(stat -Lc '%a' "$upstream_file"); owner=$(stat -Lc '%u' "$upstream_file"); current_uid=$(id -u)
  (( (8#$mode & 022) == 0 )) || { vps_die "Configured Nginx upstream file must not be group-writable or world-writable."; return; }
  [[ "$owner" == 0 || "$owner" == "$current_uid" ]] || { vps_die "Configured Nginx upstream owner is not trusted."; return; }
  ports=$(sed -nE 's/.*server[[:space:]]+127\.0\.0\.1:(18081|18082)([[:space:];].*)?$/\1/p' "$upstream_file" | sort -u)
  [[ $(printf '%s\n' "$ports" | sed '/^$/d' | wc -l) -eq 1 ]] || {
    vps_die "Nginx upstream must identify exactly one blue/green port."
    return
  }
  printf '%s\n' "$ports"
}

vps_slot_for_port() {
  case "$1" in
    18081) echo blue ;;
    18082) echo green ;;
    *) vps_die "Unsupported blue/green port: $1" ;;
  esac
}

vps_port_for_slot() {
  case "$1" in
    blue) echo 18081 ;;
    green) echo 18082 ;;
    *) vps_die "Invalid slot: $1" ;;
  esac
}

vps_other_slot() {
  case "$1" in
    blue) echo green ;;
    green) echo blue ;;
    *) vps_die "Invalid slot: $1" ;;
  esac
}

vps_listener_present() {
  local port=$1
  ss -H -ltn | awk -v endpoint="127.0.0.1:$port" '$4 == endpoint {found=1} END {exit !found}'
}

vps_project_gateway_running() {
  local slot=$1
  docker ps \
    --filter "label=com.docker.compose.project=pii-masking-$slot" \
    --filter 'label=com.docker.compose.service=gateway' \
    --filter status=running \
    --format '{{.Names}}' | grep -Fxq "pii-masking-$slot-gateway-1"
}

vps_gateway_identity() {
  local url=$1 headers status sha digest
  headers=$(curl --silent --show-error --connect-timeout 5 --max-time 10 -D - -o /dev/null "$url/health") || {
    vps_die "Gateway health evidence is unavailable."
    return
  }
  status=$(printf '%s\n' "$headers" | tr -d '\r' | awk '/^HTTP\// {print $2; exit}')
  sha=$(printf '%s\n' "$headers" | tr -d '\r' | sed -nE 's/^X-Git-SHA:[[:space:]]*([^[:space:]]+).*/\1/Ip' | head -1)
  digest=$(printf '%s\n' "$headers" | tr -d '\r' | sed -nE 's/^X-Image-Digest:[[:space:]]*([^[:space:]]+).*/\1/Ip' | head -1)
  [[ "$status" == 200 && -n "$sha" && "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    vps_die "Gateway release headers are missing or invalid."
    return
  }
  printf '%s|%s\n' "$sha" "$digest"
}

vps_discover_active_slot() {
  local state_dir=$1 stable_url=$2 nginx_port nginx_slot state_slot= direct_identity stable_identity
  nginx_port=$(vps_parse_upstream_port "$NGINX_UPSTREAM_FILE")
  nginx_slot=$(vps_slot_for_port "$nginx_port")

  if [[ -e "$state_dir/active-slot" ]]; then
    [[ -s "$state_dir/active-slot" ]] || {
      vps_die "Active-slot state is empty."
      return
    }
    state_slot=$(<"$state_dir/active-slot")
    [[ "$state_slot" == blue || "$state_slot" == green ]] || {
      vps_die "Active-slot state is invalid."
      return
    }
    [[ "$state_slot" == "$nginx_slot" ]] || {
      vps_die "Active-slot state conflicts with Nginx evidence."
      return
    }
  fi

  vps_project_gateway_running "$nginx_slot" || {
    vps_die "Nginx-selected Compose gateway is not running."
    return
  }
  if vps_project_gateway_running "$(vps_other_slot "$nginx_slot")"; then
    vps_die "Both blue and green gateways are running; active slot is ambiguous."
    return
  fi
  vps_listener_present "$nginx_port" || {
    vps_die "Nginx-selected gateway port is not listening."
    return
  }
  direct_identity=$(vps_gateway_identity "http://127.0.0.1:$nginx_port")
  stable_identity=$(vps_gateway_identity "${stable_url%/}")
  [[ "$direct_identity" == "$stable_identity" ]] || {
    vps_die "Stable and direct gateway release evidence conflicts."
    return
  }
  printf '%s\n' "$nginx_slot"
}
