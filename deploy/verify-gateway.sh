#!/usr/bin/env bash
set -Eeuo pipefail

base_url=${1:?base URL is required}
expected_sha=${2:?expected Git SHA is required}
expected_gateway_digest=${3:?expected gateway image digest is required}
expected_model_digest=${4:?expected model-service image digest is required}
service_token=${5:?service token is required}
enabled_teams=${6:-octo,synthify}
base_url=${base_url%/}
max_seconds=${VERIFY_MAX_SECONDS:-900}

curl_flags=(--fail --silent --show-error --connect-timeout 5 --max-time "$max_seconds")

# The synthetic name every canary below plants and then proves is gone. It is
# the model's own job: no regex in any team matches a person's name, so this
# is the only assertion here that fails when a checkpoint loads but does not
# detect. Keep it a name a Dutch clinical model recognises as one.
canary_name="Sanne De Vries"

assert_masked() {
  # Exit rather than return: every caller is a plain statement, and a
  # non-zero return read as a condition somewhere would turn a leak into a
  # skipped check. See the note on `vps_die` in deploy/vps-common.sh.
  local body=$1 label=$2 value
  shift 2
  for value in "$@"; do
    if grep -Fq "$value" "$body"; then
      echo "$label canary response still contains synthetic PII." >&2
      exit 1
    fi
  done
}
response=
headers=
version=
gateway_headers=
cleanup() {
  [[ -z "$response" ]] || rm -f "$response"
  [[ -z "$headers" ]] || rm -f "$headers"
  [[ -z "$version" ]] || rm -f "$version"
  [[ -z "$gateway_headers" ]] || rm -f "$gateway_headers"
}
trap cleanup EXIT

gateway_headers=$(mktemp)
curl "${curl_flags[@]}" --dump-header "$gateway_headers" "$base_url/health" \
  | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "UP"'
grep -Eiq "^X-Git-SHA:[[:space:]]*${expected_sha}[[:space:]]*$" "$gateway_headers"
grep -Eiq "^X-Image-Digest:[[:space:]]*${expected_gateway_digest}[[:space:]]*$" "$gateway_headers"

IFS=',' read -r -a teams <<< "$enabled_teams"
for team in "${teams[@]}"; do
  canary_locale=
  case "$team" in
    octo)
      expected_model_name=${OCTO_MODEL_NAME:-octo-pii-masker}
      expected_model_version=${OCTO_MODEL_VERSION:?OCTO_MODEL_VERSION is required}
      expected_model_identity="sha256:${OCTO_ONNX_MODEL_SHA256:?OCTO_ONNX_MODEL_SHA256 is required}"
      ;;
    synthify)
      expected_model_name=${SYNTHIFY_MODEL_NAME:-synthify-gliner-belgian-pii-ner}
      expected_model_version=${SYNTHIFY_MODEL_VERSION:?SYNTHIFY_MODEL_VERSION is required}
      expected_model_identity=${SYNTHIFY_ONNX_MODEL_DIGEST:?SYNTHIFY_ONNX_MODEL_DIGEST is required}
      ;;
    berts)
      expected_model_name=${BERTS_MODEL_NAME:?BERTS_MODEL_NAME is required}
      expected_model_version=${BERTS_MODEL_VERSION:?BERTS_MODEL_VERSION is required}
      expected_model_identity="sha256:${BERTS_ONNX_MODEL_SHA256:?BERTS_ONNX_MODEL_SHA256 is required}"
      # Berts picks its checkpoint from the document's language. The
      # payload below is Dutch and detects as such, but pin the locale
      # anyway: the checkpoint the variables above name has to be the one
      # that answers, whatever a future `lingua` scores the sentence at.
      # The other two are canaried on their own, after the loop.
      canary_locale=nl_BE
      ;;
    *)
      echo "Unsupported verification team: $team" >&2
      exit 1
      ;;
  esac
  request_id="deploy-${expected_sha:0:12}-$team"
  response=$(mktemp)
  headers=$(mktemp)
  version=$(mktemp)

  # Clinical prose with a plausible Dutch name, not a synthetic string.
  # These are clinical de-identification models: none of them detected
  # anything at all in the "Contact Alice Example at ..." sentence this
  # canary used to send, because that is not the kind of text they were
  # fine-tuned on. The email below is matched by regex in every team, so
  # a canary asserting only on the email passes with the model finding
  # nothing -- which is the one failure a promotion gate exists to catch.
  # Verified against octo, synthify and the Berts Dutch checkpoint: all
  # three mask the name.
  payload="Patient $canary_name werd op 14 maart 2025 op de dienst gezien; bericht naar alice@example.org."
  locale_header=()
  [[ -n "$canary_locale" ]] && locale_header=(-H "X-Document-Locale: $canary_locale")
  curl "${curl_flags[@]}" \
    --dump-header "$headers" \
    -H "Authorization: Bearer $service_token" \
    -H "X-Request-ID: $request_id" \
    -H "Idempotency-Key: $request_id" \
    -H "X-Team-ID: $team" \
    -H 'X-Model-Format: onnx' \
    "${locale_header[@]}" \
    -H "X-Source-Object-Key: incoming/$team/$request_id/canary.txt" \
    -H 'X-Source-ETag: deployment-canary' \
    -H 'Content-Type: text/plain' \
    -H 'Accept: text/plain' \
    --data-binary "$payload" \
    "$base_url/v1/mask" > "$response"

  assert_masked "$response" "$team" 'alice@example.org' "$canary_name"
  grep -Eiq "^X-Git-SHA:[[:space:]]*${expected_sha}[[:space:]]*$" "$headers"
  grep -Eiq "^X-Image-Digest:[[:space:]]*${expected_model_digest}[[:space:]]*$" "$headers"
  grep -Fiq "X-Model-Name: $expected_model_name" "$headers"
  grep -Fiq "X-Model-Version: $expected_model_version" "$headers"
  grep -Fiq "X-Model-Digest: $expected_model_identity" "$headers"
  grep -Eiq '^X-Model-Format:[[:space:]]*onnx[[:space:]]*$' "$headers"

  curl "${curl_flags[@]}" -H "X-Team-ID: $team" -H 'X-Model-Format: onnx' "$base_url/ready" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "READY"'
  curl "${curl_flags[@]}" -H "X-Team-ID: $team" -H 'X-Model-Format: onnx' "$base_url/version" > "$version"
  EXPECTED_SHA="$expected_sha" EXPECTED_IMAGE_DIGEST="$expected_model_digest" \
    EXPECTED_MODEL_NAME="$expected_model_name" EXPECTED_MODEL_VERSION="$expected_model_version" \
    EXPECTED_MODEL_IDENTITY="$expected_model_identity" \
    python3 -c 'import json,os,sys; data=json.load(sys.stdin); assert data["git_sha"] == os.environ["EXPECTED_SHA"]; assert data["image_digest"] == os.environ["EXPECTED_IMAGE_DIGEST"]; assert data["model_name"] == os.environ["EXPECTED_MODEL_NAME"]; assert data["model_version"] == os.environ["EXPECTED_MODEL_VERSION"]; assert data["model_digest"] == os.environ["EXPECTED_MODEL_IDENTITY"]; assert data["model_format"] == "onnx"' \
    < "$version"

  rm -f "$response" "$headers" "$version"
  response=
  headers=
  version=
done

# Berts holds one checkpoint per language and loads each on first use, so a
# release can pass every check above with two of the three artifacts broken,
# unreadable or absent -- nothing would touch them until the first French or
# English document arrived in production. One canary per language makes the
# promotion prove all three load, answer under their own identity, and mask
# a name in the language they were fine-tuned on. Each payload is clinical
# prose carrying a name that language's checkpoint recognises, for the same
# reason as the in-loop canary above.
# With BERTS_LANGUAGE_ROUTING=off every document goes to the Dutch checkpoint
# and the other two are never opened, so canarying them would fail the very
# rollback they exist to make safe. The Dutch canary above still runs.
if [[ ",$enabled_teams," == *,berts,* && "${BERTS_LANGUAGE_ROUTING:-on}" != off ]]; then
  berts_canaries=(
    "en|${BERTS_MODEL_NAME_EN:-berts-pii-bert-en-int8}|${BERTS_MODEL_VERSION_EN:?BERTS_MODEL_VERSION_EN is required}|${BERTS_ONNX_MODEL_SHA256_EN:?BERTS_ONNX_MODEL_SHA256_EN is required}|James Whitfield|Mr James Whitfield was reviewed in the clinic on 14 March 2025; send the letter to alice@example.org."
    "fr_FR|${BERTS_MODEL_NAME_FR:-berts-pii-camembert-bio-fr-int8}|${BERTS_MODEL_VERSION_FR:?BERTS_MODEL_VERSION_FR is required}|${BERTS_ONNX_MODEL_SHA256_FR:?BERTS_ONNX_MODEL_SHA256_FR is required}|Claire Dubois|Madame Claire Dubois a consulte le 14 mars 2025 en consultation; courriel alice@example.org."
  )
  for canary in "${berts_canaries[@]}"; do
    IFS='|' read -r locale model_name model_version model_sha name payload <<< "$canary"
    request_id="deploy-${expected_sha:0:12}-berts-${locale}"
    headers=$(mktemp)
    response=$(mktemp)
    curl "${curl_flags[@]}" \
      --dump-header "$headers" \
      -H "Authorization: Bearer $service_token" \
      -H "X-Request-ID: $request_id" \
      -H "Idempotency-Key: $request_id" \
      -H 'X-Team-ID: berts' \
      -H 'X-Model-Format: onnx' \
      -H "X-Document-Locale: $locale" \
      -H "X-Source-Object-Key: incoming/berts/$request_id/canary.txt" \
      -H 'X-Source-ETag: deployment-canary' \
      -H 'Content-Type: text/plain' \
      -H 'Accept: text/plain' \
      --data-binary "$payload" \
      "$base_url/v1/mask" > "$response"

    assert_masked "$response" "berts $locale" 'alice@example.org' "$name"
    grep -Fiq "X-Model-Name: $model_name" "$headers"
    grep -Fiq "X-Model-Version: $model_version" "$headers"
    grep -Fiq "X-Model-Digest: sha256:$model_sha" "$headers"
    rm -f "$headers" "$response"
    headers=
    response=
  done
  echo "Verified all three Berts language checkpoints through $base_url."
fi

echo "Verified ${enabled_teams} through $base_url at $expected_sha."
