"""Exercise blue/green promotion and rollback without a Docker daemon or Nginx."""

from __future__ import annotations

import ast
import json
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DeploymentScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.deploy_dir = self.root / "deploy"
        self.fake_bin = self.root / "bin"
        self.state_dir = self.root / "state"
        self.command_log = self.root / "commands.log"
        self.upstream = self.root / "upstream.conf"
        self.readiness_counter = self.root / "readiness-counter"
        self.candidate_started = self.root / "candidate-started"
        self.meminfo = self.root / "meminfo"
        self.gateway_digest = "sha256:" + "a" * 64
        self.model_digest = "sha256:" + "b" * 64
        self.model_releases_root = self.root / "model-releases"
        self.berts_model = self.model_releases_root / "release-test" / "berts"
        self.original_upstream = (
            "upstream masking_gateway { server 127.0.0.1:18081; keepalive 32; }\n"
        )
        self.meminfo.write_text(
            "MemTotal:       12582912 kB\nMemAvailable:    6291456 kB\n"
        )

        shutil.copytree(REPOSITORY_ROOT / "deploy", self.deploy_dir)
        self.fake_bin.mkdir()
        self._write_executable(
            self.fake_bin / "docker",
            """#!/bin/sh
printf 'docker-env BERTS_MODEL_URL=%s\n' "${BERTS_MODEL_URL:-}" >> "$COMMAND_LOG"
printf 'docker %s\n' "$*" >> "$COMMAND_LOG"
if [ "${1:-}" = ps ]; then
  case "$*" in
    *pii-masking-blue*) printf 'pii-masking-blue-gateway-1\n' ;;
  esac
  exit 0
fi
if [ "${1:-}" = compose ]; then
  case " $* " in *' up '*) touch "$CANDIDATE_STARTED" ;; esac
  exit 0
fi
case "${1:-}" in pull) exit 0 ;; esac
""",
        )
        self._write_executable(
            self.fake_bin / "nginx",
            """#!/bin/sh
printf 'nginx %s\n' "$*" >> "$COMMAND_LOG"
if [ "${FAIL_NGINX_TEST:-}" = 1 ] && [ "${1:-}" = -t ]; then
  exit 43
fi
""",
        )
        self._write_executable(self.fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
        self._write_executable(
            self.fake_bin / "sha256sum",
            """#!/bin/sh
case "${1:-}" in
  */berts/onnx/model.onnx)
    printf '%s  %s\n' "${BERTS_TEST_ACTUAL_SHA:-e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292}" "$1"
    ;;
  */berts/onnx-en/model.onnx)
    printf '%s  %s\n' "${BERTS_TEST_ACTUAL_SHA_EN:-c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd}" "$1"
    ;;
  */berts/onnx-fr/model.onnx)
    printf '%s  %s\n' "${BERTS_TEST_ACTUAL_SHA_FR:-2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4}" "$1"
    ;;
  *) exec /usr/bin/sha256sum "$@" ;;
esac
""",
        )
        self._write_executable(
            self.fake_bin / "ss",
            """#!/bin/sh
printf 'ss %s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  '-H -ltn')
    printf 'LISTEN 0 128 127.0.0.1:18081 0.0.0.0:*\n'
    [ ! -f "$CANDIDATE_STARTED" ] || printf 'LISTEN 0 128 127.0.0.1:18082 0.0.0.0:*\n'
    ;;
  '-Htan')
    if [ "${SS_BUSY_ONCE:-}" = 1 ] && [ ! -f "$SS_COUNTER" ]; then
      touch "$SS_COUNTER"
      printf 'ESTAB 0 0 127.0.0.1:18081 127.0.0.1:42000\n'
    fi
    ;;
esac
""",
        )
        self._write_executable(
            self.fake_bin / "curl",
            """#!/bin/sh
printf 'curl %s\n' "$*" >> "$COMMAND_LOG"
url=
team=
headers=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dump-header) headers=$2; shift 2 ;;
    -D) headers=$2; shift 2 ;;
    -o) shift 2 ;;
    --output) shift 2 ;;
    --connect-timeout|--max-time|--data-binary) shift 2 ;;
    -H)
      case "$2" in
        'X-Team-ID: '*) team=${2#X-Team-ID: } ;;
      esac
      shift 2
      ;;
    --fail|--silent|--show-error) shift ;;
    *) url=$1; shift ;;
  esac
done
if [ -n "$headers" ]; then
  readiness_sha=${ACTIVE_RELEASE_SHA:-active-release}
  readiness_digest=${ACTIVE_RELEASE_DIGEST:-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc}
  if [ "$url" = http://127.0.0.1:18082/health ] || grep -q 18082 "$NGINX_UPSTREAM_FILE"; then
    attempts=1
    if [ -f "$READINESS_COUNTER" ]; then
      attempts=$(( $(cat "$READINESS_COUNTER") + 1 ))
    fi
    printf '%s\n' "$attempts" > "$READINESS_COUNTER"
    readiness_sha=$EXPECTED_GIT_SHA
    readiness_digest=$EXPECTED_GATEWAY_DIGEST
    if [ "${READINESS_ALWAYS_FAIL:-}" = 1 ] || [ "$attempts" -le "${READINESS_FAILURES:-0}" ]; then
      readiness_sha=stale-release
      readiness_digest=sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
    fi
  fi
  response=$(printf 'HTTP/1.1 200 OK\r\nX-Git-SHA: %s\r\nX-Image-Digest: %s\r\n\r\n' "$readiness_sha" "$readiness_digest")
  if [ "$headers" = - ]; then
    printf '%s\n' "$response"
  else
    printf '%s\n' "$response" > "$headers"
  fi
  exit 0
fi
case "$url" in
  "$ALERT_WEBHOOK_URL") [ -n "$ALERT_WEBHOOK_URL" ] && exit 0 ;;
  http://127.0.0.1:18080/health)
    case "${FAIL_PROBE_COMPONENT:-}" in nginx|gateway) exit 22 ;; esac
    printf '{"status":"UP"}\n'
    ;;
  */health)
    [ "${FAIL_PROBE_COMPONENT:-}" != gateway ] || exit 22
    printf '{"status":"UP"}\n'
    ;;
  */ready)
    [ "${FAIL_PROBE_COMPONENT:-}" != "$team-model" ] || exit 22
    printf '{"status":"READY"}\n'
    ;;
  *) exit 22 ;;
esac
""",
        )
        self._write_executable(
            self.fake_bin / "sudo",
            """#!/bin/sh
printf 'sudo %s\n' "$*" >> "$COMMAND_LOG"
[ "${1:-}" != -n ] || shift
exec "$@"
""",
        )
        self.nginx_control_helper = self.fake_bin / "pii-masking-nginx-control"
        self._write_executable(
            self.nginx_control_helper,
            """#!/bin/sh
printf 'nginx-control %s\n' "$*" >> "$COMMAND_LOG"
case "${1:-}" in
  exists) test -f "$NGINX_UPSTREAM_FILE" ;;
  test) exit 0 ;;
  restart) exit 0 ;;
  switch)
    if [ "${FAIL_NGINX_TEST:-}" = 1 ] && [ "${2:-}" = 18082 ]; then
      exit 43
    fi
    case "${2:-}" in 18081|18082) ;; *) exit 2 ;; esac
    printf 'upstream masking_gateway { server 127.0.0.1:%s; keepalive 32; }\n' \
      "$2" > "$NGINX_UPSTREAM_FILE"
    ;;
  *) exit 2 ;;
esac
""",
        )
        self._write_executable(
            self.deploy_dir / "verify-gateway.sh",
            """#!/bin/sh
printf 'verify %s teams=%s\\n' "$1" "$6" >> "$COMMAND_LOG"
if [ "${FAIL_URL:-}" = "$1" ]; then
  exit 42
fi
if [ "${FAIL_BERTS_CANARY:-}" = 1 ]; then
  case ",$6," in *,berts,*) exit 42 ;; esac
fi
""",
        )

        octo_model = self.root / "octo-model"
        synthify_model = self.root / "synthify-model"
        for base, artifacts in (
            (
                octo_model,
                (
                    "onnx/model.onnx",
                    "onnx/manifest.json",
                    "onnx/config.json",
                    "onnx/sentencepiece.bpe.model",
                    "onnx/special_tokens_map.json",
                    "onnx/tokenizer.json",
                    "onnx/tokenizer_config.json",
                ),
            ),
            (
                synthify_model,
                (
                    "onnx/model.onnx",
                    "onnx/manifest.json",
                    "onnx/gliner_config.json",
                    "onnx/special_tokens_map.json",
                    "onnx/tokenizer.json",
                    "onnx/tokenizer_config.json",
                ),
            ),
        ):
            for artifact in artifacts:
                path = base / artifact
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(artifact + "\n")
                path.chmod(0o644)
            base.chmod(0o755)
        # Berts stages one checkpoint per document language under a single
        # BERTS_MODEL_DIR. The extra tokenizer files differ by architecture:
        # the Dutch RoBERTa carries a byte-level BPE vocabulary, the French
        # CamemBERT a SentencePiece token map, the English BERT neither.
        self.berts_digest = (
            "e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292"
        )
        self.berts_digest_en = (
            "c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd"
        )
        self.berts_digest_fr = (
            "2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4"
        )
        berts_checkpoints = (
            (
                "onnx",
                self.berts_digest,
                "07f18eb53e127ed34aa77a64e1e15f24ea622be8",
                ("special_tokens_map.json", "vocab.json"),
            ),
            (
                "onnx-en",
                self.berts_digest_en,
                "45f397a28f3efc462ec89552a7b8de574f3f61dc",
                (),
            ),
            (
                "onnx-fr",
                self.berts_digest_fr,
                "162c057100bb174252303bb350df4c47296ea5bd",
                ("special_tokens_map.json",),
            ),
        )
        for subdir, digest, revision, extra in berts_checkpoints:
            checkpoint = self.berts_model / subdir
            checkpoint.mkdir(parents=True)
            (checkpoint / "model.onnx").write_bytes(
                f"synthetic BERTS {subdir} test artifact\n".encode()
            )
            (checkpoint / "manifest.json").write_text(
                json.dumps(
                    {
                        "format": "onnx",
                        "precision": "int8",
                        "model_sha256": digest,
                        "source_revision": revision,
                    }
                )
            )
            for artifact in (
                "config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                *extra,
            ):
                (checkpoint / artifact).write_text("{}\n")
            if subdir == "onnx":
                (checkpoint / "merges.txt").write_text("#version: 0.2\n")
            for written in checkpoint.iterdir():
                written.chmod(0o644)
        self.berts_model.chmod(0o755)
        octo_onnx_hash = hashlib.sha256(
            (octo_model / "onnx/model.onnx").read_bytes()
        ).hexdigest()
        synthify_onnx_hash = hashlib.sha256(
            (synthify_model / "onnx/model.onnx").read_bytes()
        ).hexdigest()
        (self.deploy_dir / ".env").write_text(
            "\n".join(
                (
                    "SERVICE_TOKEN=test-token",
                    f"OCTO_MODEL_DIR={octo_model}",
                    f"SYNTHIFY_MODEL_DIR={synthify_model}",
                    "OCTO_MODEL_NAME=octo-test",
                    f"OCTO_MODEL_VERSION=sha256:{octo_onnx_hash}",
                    f"OCTO_ONNX_MODEL_SHA256={octo_onnx_hash}",
                    "SYNTHIFY_MODEL_NAME=synthify-test",
                    f"SYNTHIFY_MODEL_VERSION=sha256:{synthify_onnx_hash}",
                    f"SYNTHIFY_ONNX_MODEL_DIGEST=sha256:{synthify_onnx_hash}",
                    "ENABLED_TEAMS=octo,synthify",
                    f"NGINX_UPSTREAM_FILE={self.upstream}",
                    f"NGINX_CONTROL_HELPER={self.nginx_control_helper}",
                    "PRIVATE_GATEWAY_PORT=18080",
                    "NGINX_SWITCH_WAIT_SECONDS=1",
                    "DRAIN_MAX_SECONDS=0",
                    "DRAIN_POLL_SECONDS=1",
                    "MIN_AVAILABLE_MEMORY_PERCENT=25",
                    "ALERT_WEBHOOK_URL=https://alerts.example.invalid/test",
                    "WATCHDOG_REPAIR_WAIT_SECONDS=0",
                    "",
                )
            )
        )
        self.ocr_model_dir = self.model_releases_root / "release-test" / "ocr"
        (self.ocr_model_dir / "det").mkdir(parents=True)
        (self.ocr_model_dir / "rec").mkdir(parents=True)
        ocr_artifacts = {
            "det/model.onnx": b"synthetic OCR detection artifact\n",
            "rec/model.onnx": b"synthetic OCR recognition artifact\n",
            "rec/charset.txt": b"synthetic OCR charset\n",
        }
        self.ocr_digests = {}
        for relative, payload in ocr_artifacts.items():
            path = self.ocr_model_dir / relative
            path.write_bytes(payload)
            path.chmod(0o644)
            self.ocr_digests[relative] = hashlib.sha256(payload).hexdigest()
        for directory in (
            self.ocr_model_dir,
            self.ocr_model_dir / "det",
            self.ocr_model_dir / "rec",
        ):
            directory.chmod(0o755)
        # PDF_OCR defaults to on in deploy/compose.yml, so the baseline
        # deployment is one with OCR enabled and its artifacts staged.
        self._set_deploy_environment("OCR_MODEL_DIR", str(self.ocr_model_dir))
        for name, relative in (
            ("OCR_DET_SHA256", "det/model.onnx"),
            ("OCR_REC_SHA256", "rec/model.onnx"),
            ("OCR_CHARSET_SHA256", "rec/charset.txt"),
        ):
            self._set_deploy_environment(name, self.ocr_digests[relative])
        (self.deploy_dir / ".env").chmod(0o600)
        self.upstream.write_text(self.original_upstream)
        self.upstream.chmod(0o644)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o755)

    def _run_deployment(
        self,
        *,
        fail_url: str = "",
        fail_nginx_test: bool = False,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if extra_environment:
            environment.update(extra_environment)
        environment.update(
            {
                "COMMAND_LOG": str(self.command_log),
                "FAIL_URL": fail_url,
                "FAIL_NGINX_TEST": "1" if fail_nginx_test else "0",
                "READINESS_COUNTER": str(self.readiness_counter),
                "EXPECTED_GIT_SHA": "0123456789abcdef",
                "EXPECTED_GATEWAY_DIGEST": self.gateway_digest,
                "ACTIVE_RELEASE_SHA": "0123456789abcdef",
                "ACTIVE_RELEASE_DIGEST": self.gateway_digest,
                "CANDIDATE_STARTED": str(self.candidate_started),
                "MEMINFO_FILE": str(self.meminfo),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "STATE_DIR": str(self.state_dir),
            }
        )
        return subprocess.run(
            [
                str(self.deploy_dir / "deploy-gateway.sh"),
                "apply",
                "--env-file",
                str(self.deploy_dir / ".env"),
                "--state-dir",
                str(self.state_dir),
                "--gateway-image-ref",
                f"registry.example/gateway@{self.gateway_digest}",
                "--gateway-image-digest",
                self.gateway_digest,
                "--model-image-ref",
                f"registry.example/model@{self.model_digest}",
                "--model-image-digest",
                self.model_digest,
                "--git-sha",
                "0123456789abcdef",
                "--stable-url",
                "http://127.0.0.1:18080",
                "--approve",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def test_container_images_use_shared_threaded_gunicorn_runtime(self) -> None:
        gateway_dockerfile = (
            REPOSITORY_ROOT / "platform/masking-gateway/Dockerfile"
        ).read_text()
        model_dockerfile = (
            REPOSITORY_ROOT / "platform/model-service/Dockerfile"
        ).read_text()
        gunicorn_config = (
            REPOSITORY_ROOT / "platform/masking-gateway/gunicorn.conf.py"
        ).read_text()
        production_compose = (REPOSITORY_ROOT / "deploy/compose.yml").read_text()

        self.assertIn('"gunicorn"', gateway_dockerfile)
        self.assertIn('"gunicorn"', model_dockerfile)
        self.assertIn('worker_class = "gthread"', gunicorn_config)
        self.assertIn("GATEWAY_WEB_THREADS:-8", production_compose)
        self.assertIn("MODEL_SERVICE_WEB_THREADS:-2", production_compose)
        self.assertIn("REQUEST_TIMEOUT_SECONDS:-6600", production_compose)
        self.assertIn("MAX_CONCURRENT_REQUESTS_PER_TEAM:-1", production_compose)
        self.assertIn("MODEL_IDLE_TIMEOUT_SECONDS:-0", production_compose)

    def test_production_compose_wires_private_read_only_berts_service(self) -> None:
        config = yaml.safe_load((REPOSITORY_ROOT / "deploy/compose.yml").read_text())
        gateway = config["services"]["gateway"]
        berts = config["services"]["berts-model"]

        self.assertEqual(berts["profiles"], ["berts"])
        self.assertEqual(berts["environment"]["TEAM_ID"], "berts")
        self.assertEqual(berts["environment"]["ONNX_MODEL_PATH"], "/models/berts/onnx")
        self.assertIn(
            "BERTS_ONNX_MODEL_SHA256", berts["environment"]["ONNX_MODEL_SHA256"]
        )
        # One checkpoint per document language, all three mounted. A single
        # MODEL_NAME/MODEL_VERSION is deliberately absent: the response names
        # the checkpoint that read the document, and one variable cannot.
        self.assertEqual(
            berts["environment"]["ONNX_MODEL_PATH_EN"], "/models/berts/onnx-en"
        )
        self.assertEqual(
            berts["environment"]["ONNX_MODEL_PATH_FR"], "/models/berts/onnx-fr"
        )
        self.assertIn(
            "BERTS_ONNX_MODEL_SHA256_EN", berts["environment"]["ONNX_MODEL_SHA256_EN"]
        )
        self.assertIn(
            "BERTS_ONNX_MODEL_SHA256_FR", berts["environment"]["ONNX_MODEL_SHA256_FR"]
        )
        self.assertNotIn("MODEL_NAME", berts["environment"])
        self.assertNotIn("MODEL_VERSION", berts["environment"])
        # Berts reaches the gateway over the internal `models` network and the
        # R2 logs bucket over `egress`. Neither may publish a port to the host.
        self.assertEqual(berts["networks"], ["models", "egress"])
        self.assertTrue(config["networks"]["models"]["internal"])
        self.assertNotIn("ports", config["networks"].get("egress") or {})
        self.assertNotIn("ports", berts)
        self.assertTrue(berts["read_only"])
        self.assertEqual(berts["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", berts["security_opt"])
        self.assertNotIn("privileged", berts)
        self.assertNotIn("/var/run/docker.sock", " ".join(berts["volumes"]))
        self.assertTrue(
            any(volume.endswith(":/models/berts:ro") for volume in berts["volumes"])
        )
        self.assertEqual(
            gateway["environment"]["BERTS_MODEL_URL"], "${BERTS_MODEL_URL:-}"
        )
        self.assertEqual(
            gateway["environment"]["OCTO_MODEL_URL"], "http://octo-model:8080"
        )
        self.assertEqual(
            gateway["environment"]["SYNTHIFY_MODEL_URL"],
            "http://synthify-model:8080",
        )

    def test_production_compose_exposes_every_demo_model_service_toggle(self) -> None:
        # The demo template and this one have drifted twice now. Both times an
        # opt-in setting landed in `masking_engine` config with tests behind it,
        # reached `compose.yaml` so the demo could show it off, and was never
        # added here -- so no real deployment could reach the knob at all, and
        # the gap surfaced as a bug report against the model rather than as a
        # missing line of deployment config.
        # Any `${VAR}` a model service takes in the demo must be reachable in
        # production too. A literal (TEAM_ID, MODEL_PATH) is deployment identity
        # rather than an operator toggle, so only interpolations are compared.
        def model_service_toggles(relative_path: str) -> set[str]:
            config = yaml.safe_load((REPOSITORY_ROOT / relative_path).read_text())
            per_service = [
                {
                    name
                    for name, value in (service.get("environment") or {}).items()
                    if isinstance(value, str) and value.startswith("${")
                }
                for service_name, service in config["services"].items()
                if service_name != "gateway"
            ]
            return set.intersection(*per_service)

        demo = model_service_toggles("compose.yaml")
        production = model_service_toggles("deploy/compose.yml")

        self.assertTrue(demo, "expected the demo compose to carry model toggles")
        self.assertEqual(
            demo - production,
            set(),
            "reachable in the demo but not in production: wire these through "
            "deploy/compose.yml and document them in deploy/.env.example",
        )

    def test_an_optional_setting_is_not_required_by_compose(self) -> None:
        # `.env.example` documents the audit upload as optional -- "Leave both
        # empty to disable" -- and `_upload_audit` checks for exactly that.
        # deploy/compose.yml marked the same two variables `:?`, which Compose
        # rejects when unset *or empty*, so following the instruction stopped
        # the stack from starting and the only way up was to enable the upload.
        # A setting documented as optional must not be mandatory here.
        compose = (REPOSITORY_ROOT / "deploy/compose.yml").read_text()
        example = (REPOSITORY_ROOT / "deploy/.env.example").read_text()

        optional = {
            name
            for name in re.findall(r"^([A-Z][A-Z0-9_]*)=\s*$", example, re.MULTILINE)
        }
        self.assertIn(
            "PROD_LOGS_S3_ACCESS_KEY",
            optional,
            "expected the audit credentials to be documented as optional",
        )

        required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", compose))
        contradiction = sorted(optional & required)
        self.assertEqual(
            contradiction,
            [],
            "documented in deploy/.env.example as safe to leave empty, but "
            "deploy/compose.yml requires them non-empty: " + ", ".join(contradiction),
        )

    def test_berts_canary_requires_onnx_and_complete_provenance(self) -> None:
        verifier = (REPOSITORY_ROOT / "deploy/verify-gateway.sh").read_text()

        self.assertIn("berts)", verifier)
        self.assertIn('-H "X-Team-ID: $team"', verifier)
        self.assertIn("-H 'X-Model-Format: onnx'", verifier)
        for header in (
            "X-Git-SHA",
            "X-Image-Digest",
            "X-Model-Name",
            "X-Model-Version",
            "X-Model-Digest",
            "X-Model-Format",
        ):
            self.assertIn(header, verifier)
        self.assertIn("incoming/$team/$request_id/canary.txt", verifier)
        self.assertIn("Idempotency-Key: $request_id", verifier)
        self.assertIn("alice@example.org", verifier)

    def test_every_canary_proves_the_model_masked_a_name(self) -> None:
        # A checkpoint can load, report its identity and detect nothing at
        # all. The email each payload carries is a regex hit in every team,
        # so a canary asserting only on the email passes a release whose NER
        # is dead -- and the sentence this gate used to send ("Contact Alice
        # Example at ...") was detected by none of the three checkpoints,
        # because it is not clinical prose. Every canary therefore plants a
        # name, which no regex in any team matches, and proves it is gone.
        verifier = (REPOSITORY_ROOT / "deploy/verify-gateway.sh").read_text()

        self.assertIn('canary_name="Sanne De Vries"', verifier)
        self.assertIn('payload="Patient $canary_name werd op', verifier)
        self.assertIn(
            'assert_masked "$response" "$team" \'alice@example.org\' "$canary_name"',
            verifier,
        )
        self.assertIn(
            'assert_masked "$response" "berts $locale" \'alice@example.org\' "$name"',
            verifier,
        )
        for locale, name in (("en", "James Whitfield"), ("fr_FR", "Claire Dubois")):
            with self.subTest(locale=locale):
                row = next(
                    line
                    for line in verifier.splitlines()
                    if line.strip().startswith(f'"{locale}|')
                )
                self.assertIn(f"|{name}|", row)
                # The name is asserted on, so the payload has to contain it.
                self.assertIn(name, row.split(f"|{name}|", 1)[1])

    def test_berts_source_identity_matches_immutable_manifest(self) -> None:
        # Every language's checkpoint is pinned in three places that have to
        # agree: the runtime table the service loads from, the manifest
        # committed beside the artifact, and the approved values the
        # promotion gate refuses to deploy without.
        runtime = (REPOSITORY_ROOT / "teams/berts/runtime.py").read_text()
        deployer = (REPOSITORY_ROOT / "deploy/deploy-gateway.sh").read_text()
        expected = {
            "nl_BE": (
                "onnx",
                "berts-pii-medroberta-nl-int8",
                "07f18eb53e127ed34aa77a64e1e15f24ea622be8",
                "e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292",
            ),
            "en": (
                "onnx-en",
                "berts-pii-bert-en-int8",
                "45f397a28f3efc462ec89552a7b8de574f3f61dc",
                "c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd",
            ),
            "fr_FR": (
                "onnx-fr",
                "berts-pii-camembert-bio-fr-int8",
                "162c057100bb174252303bb350df4c47296ea5bd",
                "2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4",
            ),
        }
        for locale, (subdir, name, revision, digest) in expected.items():
            with self.subTest(locale=locale):
                manifest = json.loads(
                    (
                        REPOSITORY_ROOT / "teams/berts" / subdir / "manifest.json"
                    ).read_text()
                )
                self.assertEqual(manifest["source_revision"], revision)
                self.assertEqual(manifest["model_sha256"], digest)
                self.assertEqual(manifest["locale"], locale)
                self.assertIn(f'model_name="{name}"', runtime)
                self.assertIn(f'revision="{revision}"', runtime)
                self.assertIn(f'sha256="{digest}"', runtime)
                self.assertIn(revision, deployer)
                self.assertIn(digest, deployer)

    def test_nginx_template_is_private_and_does_not_buffer_pii(self) -> None:
        nginx = (REPOSITORY_ROOT / "deploy/nginx.conf.example").read_text()

        self.assertIn("listen 127.0.0.1:@PRIVATE_GATEWAY_PORT@", nginx)
        self.assertIn("listen @NIFI_DOCKER_BRIDGE_IP@:@PRIVATE_GATEWAY_PORT@", nginx)
        self.assertNotIn("listen 443", nginx)
        self.assertIn("client_max_body_size 10m", nginx)
        self.assertIn("proxy_request_buffering off", nginx)
        self.assertIn("proxy_buffering off", nginx)

    def test_success_promotes_inactive_slot_and_drains_previous_slot(self) -> None:
        result = self._run_deployment()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "green\n")
        self.assertIn("127.0.0.1:18082", self.upstream.read_text())
        command_log = self.command_log.read_text()
        self.assertIn("--project-name pii-masking-green", command_log)
        self.assertIn("--project-name pii-masking-blue", command_log)
        self.assertIn("verify http://127.0.0.1:18080", command_log)
        self.assertIn(f"sudo -n {self.nginx_control_helper} switch 18082", command_log)
        self.assertLess(
            command_log.index("http://127.0.0.1:18080/health"),
            command_log.index("verify http://127.0.0.1:18080"),
        )
        release = (self.state_dir / "releases" / "green.env").read_text()
        self.assertIn(f"GATEWAY_IMAGE_DIGEST={self.gateway_digest}", release)
        self.assertIn(f"MODEL_SERVICE_IMAGE_DIGEST={self.model_digest}", release)
        self.assertIn("GIT_SHA=0123456789abcdef", release)

    def test_local_canary_failure_keeps_previous_upstream(self) -> None:
        result = self._run_deployment(fail_url="http://127.0.0.1:18082")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.upstream.read_text(), self.original_upstream)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "blue\n")
        command_log = self.command_log.read_text()
        self.assertNotIn("verify http://127.0.0.1:18080", command_log)
        self.assertIn("down --remove-orphans", command_log)

    def test_nginx_validation_failure_restores_previous_upstream(self) -> None:
        result = self._run_deployment(fail_nginx_test=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.upstream.read_text(), self.original_upstream)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "blue\n")
        command_log = self.command_log.read_text()
        self.assertNotIn("verify http://127.0.0.1:18080", command_log)
        self.assertIn("down --remove-orphans", command_log)

    def test_private_verification_failure_restores_upstream_and_removes_candidate(
        self,
    ) -> None:
        result = self._run_deployment(fail_url="http://127.0.0.1:18080")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.upstream.read_text(), self.original_upstream)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "blue\n")
        command_log = self.command_log.read_text()
        self.assertIn("nginx-control switch 18082", command_log)
        self.assertIn("nginx-control switch 18081", command_log)
        self.assertIn("--project-name pii-masking-green", command_log)
        self.assertIn("down --remove-orphans", command_log)

    def test_switch_readiness_retries_until_candidate_headers_are_visible(
        self,
    ) -> None:
        result = self._run_deployment(extra_environment={"READINESS_FAILURES": "1"})

        self.assertEqual(result.returncode, 0, result.stderr)
        command_log = self.command_log.read_text()
        self.assertGreaterEqual(command_log.count("http://127.0.0.1:18080/health"), 2)
        self.assertIn(
            "Stable Nginx endpoint serves candidate 0123456789abcdef.",
            result.stdout,
        )

    def test_switch_readiness_timeout_rolls_back_candidate(self) -> None:
        result = self._run_deployment(extra_environment={"READINESS_ALWAYS_FAIL": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.upstream.read_text(), self.original_upstream)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "blue\n")
        command_log = self.command_log.read_text()
        self.assertIn("nginx-control switch 18082", command_log)
        self.assertIn("nginx-control switch 18081", command_log)
        self.assertNotIn("verify http://127.0.0.1:18080", command_log)
        self.assertIn("down --remove-orphans", command_log)

    def test_nginx_helper_and_sudoers_are_least_privilege(self) -> None:
        helper = (REPOSITORY_ROOT / "deploy/pii-masking-nginx-control").read_text()
        sudoers = (
            REPOSITORY_ROOT / "deploy/sudoers/pii-masking-nginx-control"
        ).read_text()
        watchdog = (REPOSITORY_ROOT / "deploy/watchdog-gateway.sh").read_text()
        installer = (REPOSITORY_ROOT / "deploy/install-vps.sh").read_text()

        for command in (
            "exists",
            "test",
            "restart",
            "switch 18081",
            "switch 18082",
        ):
            self.assertIn(f"@NGINX_CONTROL_HELPER@ {command}", sudoers)
        self.assertNotIn("*", sudoers)
        self.assertNotIn("systemctl", sudoers)
        self.assertIn("18081|18082", helper)
        self.assertNotIn("sudo systemctl restart nginx", watchdog)
        self.assertIn('sudo -n "$nginx_control_helper" restart', watchdog)
        self.assertNotIn("ALERT_WEBHOOK_URL:?", watchdog + installer)

    def test_blue_green_switch_ports_remain_fixed(self) -> None:
        deploy_script = (REPOSITORY_ROOT / "deploy/deploy-gateway.sh").read_text()
        helper = (REPOSITORY_ROOT / "deploy/pii-masking-nginx-control").read_text()

        common = (REPOSITORY_ROOT / "deploy/vps-common.sh").read_text()
        self.assertIn("18081) echo blue", common)
        self.assertIn("18082) echo green", common)
        self.assertIn("blue) echo 18081", common)
        self.assertIn("green) echo 18082", common)
        self.assertIn("18081|18082", helper)
        self.assertNotIn("18083", deploy_script + helper + common)

    def test_production_deploy_is_manual_default_branch_only(self) -> None:
        config = yaml.safe_load((REPOSITORY_ROOT / "ci/deploy.yml").read_text())
        preflight_job = config["preflight_production"]
        deploy_job = config["deploy_production"]

        self.assertEqual(preflight_job["when"], "manual")
        self.assertFalse(preflight_job["allow_failure"])
        self.assertEqual(preflight_job["resource_group"], "production")
        self.assertEqual(deploy_job["when"], "manual")
        self.assertFalse(deploy_job["allow_failure"])
        self.assertEqual(deploy_job["environment"]["name"], "production")
        self.assertEqual(deploy_job["resource_group"], "production")
        self.assertEqual(
            {need["job"] for need in preflight_job["needs"]},
            {"load_production_candidate"},
        )
        self.assertEqual(
            {need["job"] for need in deploy_job["needs"]},
            {
                "preflight_production",
                "load_production_candidate",
            },
        )
        # Both jobs take their rules from .production_deploy_rules in
        # .gitlab-ci.yml rather than declaring them inline. The template is
        # shared because both `need` load_production_candidate: once that job
        # gained a platform-lane `changes:` filter, an inline copy here stayed
        # unfiltered and dangled the need on arena-only main pushes, which
        # makes GitLab reject the entire configuration.
        self.assertEqual(preflight_job["extends"], ".production_deploy_rules")
        self.assertEqual(deploy_job["extends"], ".production_deploy_rules")
        self.assertNotIn("rules", preflight_job)
        self.assertNotIn("rules", deploy_job)

        rules = yaml.safe_load((REPOSITORY_ROOT / ".gitlab-ci.yml").read_text())[
            ".production_deploy_rules"
        ]["rules"]
        self.assertEqual(
            rules[0]["if"],
            '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "push"',
        )
        self.assertEqual(rules[0]["when"], "manual")
        self.assertTrue(rules[0]["changes"], "the push arm must be lane-scoped")
        self.assertEqual(
            rules[1],
            {
                "if": '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "web"',
                "when": "manual",
            },
        )
        self.assertEqual(rules[-1], {"when": "never"})

    def test_old_slot_waits_for_established_connection_to_drain(self) -> None:
        env_file = self.deploy_dir / ".env"
        env_file.write_text(
            env_file.read_text().replace("DRAIN_MAX_SECONDS=0", "DRAIN_MAX_SECONDS=10")
        )
        result = self._run_deployment(
            extra_environment={
                "SS_BUSY_ONCE": "1",
                "SS_COUNTER": str(self.root / "ss-counter"),
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(self.command_log.read_text().count("ss -Htan"), 2)
        self.assertIn("Old slot on port 18081 has drained.", result.stdout)

    def test_berts_is_accepted_and_adds_only_its_compose_profile(self) -> None:
        self._enable_berts()

        result = self._run_deployment()

        self.assertEqual(result.returncode, 0, result.stderr)
        command_log = self.command_log.read_text()
        self.assertIn("--profile berts", command_log)
        self.assertIn("teams=octo,synthify,berts", command_log)
        self.assertIn("docker-env BERTS_MODEL_URL=http://berts-model:8080", command_log)

    def test_berts_aliases_and_unknown_teams_remain_invalid(self) -> None:
        for team in ("onnx", "team-3", "team3", "unknown"):
            with self.subTest(team=team):
                self._set_deploy_environment("ENABLED_TEAMS", team)
                self.command_log.unlink(missing_ok=True)

                result = self._run_deployment()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Unsupported production team: {team}", result.stderr)
                self.assertFalse(self.command_log.exists())

    def test_berts_variables_are_required_only_when_enabled(self) -> None:
        result = self._run_deployment()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker-env BERTS_MODEL_URL=\n", self.command_log.read_text())
        self.assertNotIn("http://berts-model:8080", self.command_log.read_text())

        self._set_deploy_environment("ENABLED_TEAMS", "berts")
        self.command_log.unlink(missing_ok=True)
        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BERTS_MODEL_DIR", result.stderr)
        self.assertIn("does not satisfy this release's schema", result.stderr)
        self.assertFalse(self.command_log.exists())

    def test_ocr_artifacts_are_verified_when_pdf_ocr_is_on(self) -> None:

        result = self._run_deployment()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ocr_artifacts_are_not_required_when_pdf_ocr_is_off(self) -> None:
        # Opting out must not require a staged tree, so point the directory at
        # nothing and prove preflight never looks.
        self._disable_ocr()
        self._set_deploy_environment("OCR_MODEL_DIR", "/nonexistent/disabled-ocr")

        result = self._run_deployment()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_ocr_artifact_fails_before_candidate_start(self) -> None:
        (self.ocr_model_dir / "rec" / "charset.txt").unlink()

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Required model artifact is missing, unreadable, or a symlink",
            result.stderr,
        )
        self.assertFalse(self.candidate_started.exists())

    def test_ocr_git_lfs_pointer_fails_before_candidate_start(self) -> None:
        # The regression this whole path exists for. A pointer is a 130-byte
        # text file where a model should be, so every existence check in the
        # chain counts it as present -- including the service's own startup
        # check. Only the digest catches it.
        (self.ocr_model_dir / "det" / "model.onnx").write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:049d5107f028a88efb9c96857ab72af847f0be7aeb413285669a84cf00b8f973\n"
            "size 9893452\n"
        )
        (self.ocr_model_dir / "det" / "model.onnx").chmod(0o644)

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OCR detection SHA-256 mismatch", result.stderr)
        self.assertFalse(self.candidate_started.exists())

    def test_ocr_directory_must_be_immutable(self) -> None:
        self._set_deploy_environment(
            "OCR_MODEL_DIR", str(self.model_releases_root / "current" / "ocr")
        )

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.candidate_started.exists())

    def test_missing_berts_artifact_fails_before_candidate_start(self) -> None:
        self._enable_berts()
        (self.berts_model / "onnx" / "tokenizer.json").unlink()

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Required model artifact is missing, unreadable, or a symlink",
            result.stderr,
        )
        self.assertFalse(self.command_log.exists())
        self.assertEqual(self.upstream.read_text(), self.original_upstream)

    def test_incorrect_berts_digest_fails_before_candidate_start(self) -> None:
        self._enable_berts()

        result = self._run_deployment(
            extra_environment={"BERTS_TEST_ACTUAL_SHA": "0" * 64}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BERTS onnx SHA-256 mismatch", result.stderr)
        self.assertFalse(self.command_log.exists())
        self.assertEqual(self.upstream.read_text(), self.original_upstream)

    def test_berts_language_routing_off_stages_only_the_default_checkpoint(
        self,
    ) -> None:
        # The rollback has to be reachable: with routing off the service opens
        # only the Dutch checkpoint, so a release that no longer stages the
        # other two must still promote. Remove them outright and drop their
        # variables, which is what backing a bad checkpoint out looks like.
        self._enable_berts()
        self._set_deploy_environment("BERTS_LANGUAGE_ROUTING", "off")
        for name in (
            "BERTS_MODEL_VERSION_EN",
            "BERTS_ONNX_MODEL_SHA256_EN",
            "BERTS_MODEL_VERSION_FR",
            "BERTS_ONNX_MODEL_SHA256_FR",
        ):
            self._set_deploy_environment(name, "")
        shutil.rmtree(self.berts_model / "onnx-en")
        shutil.rmtree(self.berts_model / "onnx-fr")

        result = self._run_deployment()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("http://berts-model:8080", self.command_log.read_text())

    def test_berts_language_routing_on_still_requires_every_checkpoint(self) -> None:
        # The other half of the switch: leaving it at the default must not let
        # a half-staged release through.
        self._enable_berts()
        shutil.rmtree(self.berts_model / "onnx-fr")

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.command_log.exists())

    def test_a_legacy_environment_is_told_every_key_it_lacks_at_once(self) -> None:
        # What production actually looks like the first time a release widens
        # the schema: the live file was written before per-language routing
        # existed, so it holds none of the four keys. Preflight ran, named
        # BERTS_MODEL_VERSION_EN, and stopped -- and since preflight is a
        # manual production job, finding the other three that way costs three
        # more approved runs. It has the whole list in hand, so it reports it.
        self._enable_berts()
        absent = (
            "BERTS_MODEL_VERSION_EN",
            "BERTS_ONNX_MODEL_SHA256_EN",
            "BERTS_MODEL_VERSION_FR",
            "BERTS_ONNX_MODEL_SHA256_FR",
        )
        for name in absent:
            self._set_deploy_environment(name, "")

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        for name in absent:
            self.assertIn(name, result.stderr)
        self.assertIn("missing 4 key(s)", result.stderr)
        # Naming the key is half an answer: these are digests and upstream
        # revisions read out of a staged manifest, so the message has to say
        # where they come from, and that the rollback switch is the other way
        # out of exactly this state.
        self.assertIn("stage-model-release.sh apply", result.stderr)
        self.assertIn("BERTS_LANGUAGE_ROUTING=off", result.stderr)
        self.assertFalse(self.command_log.exists())

    def test_a_misspelled_berts_language_routing_value_is_refused(self) -> None:
        # An operator reaching for this switch is backing a bad checkpoint out
        # of production. "of" quietly leaving routing on is the one outcome
        # that must not happen, so it fails the same way a bad locale does.
        self._enable_berts()
        self._set_deploy_environment("BERTS_LANGUAGE_ROUTING", "of")

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BERTS_LANGUAGE_ROUTING must be one of: off, on", result.stderr)
        self.assertFalse(self.command_log.exists())

    def test_berts_model_directory_rejects_mutable_current_layout(self) -> None:
        current = self.model_releases_root / "current"
        current.symlink_to("release-test", target_is_directory=True)
        self._enable_berts()
        self._set_deploy_environment("BERTS_MODEL_DIR", str(current / "berts"))

        result = self._run_deployment()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not use a current symlink", result.stderr)
        self.assertFalse(self.command_log.exists())

    def test_berts_candidate_failure_preserves_rollback(self) -> None:
        self._enable_berts()

        result = self._run_deployment(extra_environment={"FAIL_BERTS_CANARY": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.upstream.read_text(), self.original_upstream)
        self.assertEqual((self.state_dir / "active-slot").read_text(), "blue\n")
        self.assertIn("down --remove-orphans", self.command_log.read_text())

    def _write_active_release(self) -> None:
        release_dir = self.state_dir / "releases"
        release_dir.mkdir(parents=True)
        (self.state_dir / "active-slot").write_text("blue\n")
        release_file = release_dir / "blue.env"
        release_file.write_text(
            "\n".join(
                (
                    "SLOT=blue",
                    "GATEWAY_PORT=18081",
                    f"GATEWAY_IMAGE_REF=registry.example/gateway@{self.gateway_digest}",
                    f"GATEWAY_IMAGE_DIGEST={self.gateway_digest}",
                    f"MODEL_SERVICE_IMAGE_REF=registry.example/model@{self.model_digest}",
                    f"MODEL_SERVICE_IMAGE_DIGEST={self.model_digest}",
                    "GIT_SHA=0123456789abcdef",
                    "SERVICE_RELEASE=0123456789abcdef",
                    "STABLE_URL=http://127.0.0.1:18080",
                    "ENABLED_TEAMS=octo,synthify",
                    f"ENV_FILE={self.deploy_dir / '.env'}",
                    "",
                )
            )
        )
        release_file.chmod(0o600)

    def _set_deploy_environment(self, name: str, value: str) -> None:
        env_file = self.deploy_dir / ".env"
        lines = env_file.read_text().splitlines()
        prefix = f"{name}="
        found = any(line.startswith(prefix) for line in lines)
        updated = [
            f"{prefix}{value}" if line.startswith(prefix) else line for line in lines
        ]
        if not found:
            updated.append(f"{prefix}{value}")
        env_file.write_text("\n".join(updated) + "\n")
        env_file.chmod(0o600)

    def _disable_ocr(self) -> None:
        self._set_deploy_environment("PDF_OCR", "off")

    def _enable_berts(self) -> None:
        self._set_deploy_environment("ENABLED_TEAMS", "octo,synthify,berts")
        self._set_deploy_environment(
            "MODEL_RELEASES_ROOT", str(self.model_releases_root)
        )
        self._set_deploy_environment("BERTS_MODEL_DIR", str(self.berts_model))
        self._set_deploy_environment("BERTS_MODEL_NAME", "berts-pii-medroberta-nl-int8")
        self._set_deploy_environment(
            "BERTS_MODEL_VERSION",
            "07f18eb53e127ed34aa77a64e1e15f24ea622be8",
        )
        self._set_deploy_environment("BERTS_ONNX_MODEL_SHA256", self.berts_digest)
        self._set_deploy_environment(
            "BERTS_MODEL_VERSION_EN",
            "45f397a28f3efc462ec89552a7b8de574f3f61dc",
        )
        self._set_deploy_environment("BERTS_ONNX_MODEL_SHA256_EN", self.berts_digest_en)
        self._set_deploy_environment(
            "BERTS_MODEL_VERSION_FR",
            "162c057100bb174252303bb350df4c47296ea5bd",
        )
        self._set_deploy_environment("BERTS_ONNX_MODEL_SHA256_FR", self.berts_digest_fr)
        self._set_deploy_environment("BERTS_WARM_RSS_MIB", "512")

    def _run_watchdog(
        self, *, failed_component: str = ""
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "COMMAND_LOG": str(self.command_log),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "STATE_DIR": str(self.state_dir),
                "PII_MASKING_DEPLOY_DIR": str(self.deploy_dir),
                "ENV_FILE": str(self.deploy_dir / ".env"),
                "FAIL_PROBE_COMPONENT": failed_component,
                "EXPECTED_GIT_SHA": "0123456789abcdef",
                "EXPECTED_GATEWAY_DIGEST": self.gateway_digest,
                "CANDIDATE_STARTED": str(self.candidate_started),
                "ACTIVE_RELEASE_SHA": "0123456789abcdef",
                "ACTIVE_RELEASE_DIGEST": self.gateway_digest,
            }
        )
        return subprocess.run(
            ["bash", str(self.deploy_dir / "watchdog-gateway.sh")],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def test_watchdog_accepts_healthy_private_gateway(self) -> None:
        self._write_active_release()

        result = self._run_watchdog()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state_dir / "watchdog-status").read_text(), "healthy\n")
        command_log = self.command_log.read_text()
        self.assertNotIn("docker compose", command_log)
        self.assertNotIn("https://alerts.example.invalid/test", command_log)

    def test_watchdog_restarts_failed_team_and_sends_recovery(self) -> None:
        self._write_active_release()

        result = self._run_watchdog(failed_component="octo-model")

        self.assertEqual(result.returncode, 0, result.stderr)
        command_log = self.command_log.read_text()
        self.assertIn("restart octo-model", command_log)
        self.assertIn("verify http://127.0.0.1:18080", command_log)
        self.assertGreaterEqual(
            command_log.count("https://alerts.example.invalid/test"), 2
        )
        self.assertEqual((self.state_dir / "watchdog-status").read_text(), "healthy\n")

    def test_watchdog_empty_webhook_logs_structured_alerts(self) -> None:
        self._set_deploy_environment("ALERT_WEBHOOK_URL", "")
        self._write_active_release()

        result = self._run_watchdog(failed_component="octo-model")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"event":"unhealthy"', result.stderr)
        self.assertIn('"event":"recovered"', result.stderr)
        self.assertIn('"release":"0123456789abcdef"', result.stderr)
        command_log = self.command_log.read_text()
        self.assertNotIn("alerts.example.invalid", command_log)

    def test_watchdog_nginx_repair_uses_restricted_helper(self) -> None:
        self._write_active_release()

        result = self._run_watchdog(failed_component="nginx")

        self.assertEqual(result.returncode, 0, result.stderr)
        command_log = self.command_log.read_text()
        self.assertIn(f"sudo -n {self.nginx_control_helper} restart", command_log)
        self.assertNotIn("sudo systemctl restart nginx", command_log)

    def test_watchdog_cooldown_suppresses_repeated_restart(self) -> None:
        self._write_active_release()
        (self.state_dir / "watchdog-last-repair").write_text(f"{int(time.time())}\n")

        result = self._run_watchdog(failed_component="gateway")

        self.assertNotEqual(result.returncode, 0)
        command_log = self.command_log.read_text()
        self.assertNotIn("docker compose", command_log)
        self.assertIn("https://alerts.example.invalid/test", command_log)
        self.assertEqual((self.state_dir / "watchdog-status").read_text(), "degraded\n")


class OperatorSettingReachabilityTests(unittest.TestCase):
    """Every setting the masking engine reads must be reachable in production.

    `test_production_compose_exposes_every_demo_model_service_toggle` catches a
    toggle that reached the demo compose file and never reached this one. It
    cannot see the step before that: a setting added to `masking_engine` config
    and wired into neither compose file is unreachable in every deployment, and
    nothing fails to say so. That is the same drift one link earlier in the
    chain, so it is closed the same way -- by deriving the expected set from the
    code rather than from a second hand-maintained list.
    """

    # Read per team and set on the services it applies to, rather than shared.
    # Each team's graph sits at its own ONNX_MODEL_PATH; synthify pins
    # ONNX_MODEL_DIGEST rather than ONNX_MODEL_SHA256; DETECTION_MODE and
    # DETECTOR_LABEL_PROFILE follow the team's own detector. These are
    # deployment identity, not operator toggles.
    PER_TEAM_SETTINGS = frozenset(
        {
            "DETECTION_MODE",
            "DETECTOR_LABEL_PROFILE",
            "ONNX_MODEL_PATH",
            "ONNX_MODEL_SHA256",
            # Berts reports the checkpoint the document's language selected,
            # so it takes its name and revision from its own table rather
            # than from one variable that could only name all three at once.
            "MODEL_NAME",
            "MODEL_VERSION",
        }
    )

    # A shared toggle whose production default deliberately differs from the
    # code default, and the value production is meant to hold. Each entry is a
    # decision someone made once; a later change to the code default fails here
    # and asks whether this deployment meant to follow it.
    INTENTIONAL_PRODUCTION_DEFAULTS = {
        # ADR 003 keeps OCR off in code. This deployment stages the artifacts
        # and runs with it on, so an existing .env without the line keeps OCR
        # rather than losing it silently on the next release.
        "PDF_OCR": "on",
        # Production is CPU-only. "auto" would select CUDA on a host that
        # happens to expose it, which no team's artifact is validated against.
        "ACCELERATOR": "cpu",
    }

    # Empty in code means "do not check", which is what lets a developer run
    # against a locally exported artifact. Production pins the staged digests
    # instead, so a Git LFS pointer file cannot pass as a model. The value
    # itself lives in compose.yml; only the requirement to pin one lives here.
    PINNED_DIGEST_SETTINGS = frozenset(
        {"OCR_DET_SHA256", "OCR_REC_SHA256", "OCR_CHARSET_SHA256"}
    )

    @staticmethod
    def _settings_read_from_environment() -> set[str]:
        """Return every variable ``load_settings`` reads, recorded as it runs.

        Recorded rather than listed, so a setting added to config.py is covered
        the moment it is added and cannot be forgotten here.
        """
        import sys
        from unittest import mock

        source = REPOSITORY_ROOT / "platform" / "masking-gateway" / "src"
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        from masking_engine.app.core import config

        recorded: set[str] = set()
        real_getenv = os.getenv

        def record(key, default=None):
            recorded.add(key)
            return real_getenv(key, default)

        # An empty environment so the recording reflects config.py's own
        # defaults rather than whatever the run happens to export.
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(os, "getenv", record):
                config.load_settings(require_service_token=False)

        # The gateway holds the bearer token; no model service is given one.
        return recorded - {"SERVICE_TOKEN"}

    @staticmethod
    def _model_service_environments() -> dict[str, dict[str, object]]:
        compose = yaml.safe_load((REPOSITORY_ROOT / "deploy/compose.yml").read_text())
        return {
            name: (service.get("environment") or {})
            for name, service in compose["services"].items()
            if name != "gateway"
        }

    @classmethod
    def _shared_toggles(cls) -> dict[str, str]:
        """Return each shared toggle in production, mapped to its default.

        A shared toggle is an entry whose key and interpolated variable are the
        same name and which carries a `:-` default: `PDF_OCR: ${PDF_OCR:-on}`.
        An entry that renames (`MODEL_NAME: ${OCTO_MODEL_NAME:-...}`) is that
        team's identity, and one that is required (`${...:?...}`) is injected by
        the deploy job; neither is an operator toggle.
        """
        environments = cls._model_service_environments()
        shared = set.intersection(*(set(env) for env in environments.values()))
        first = next(iter(environments.values()))
        toggles = {}
        for key in shared:
            value = first[key]
            if not isinstance(value, str):
                continue
            match = re.fullmatch(r"\$\{" + re.escape(key) + r":-(.*)\}", value)
            if match:
                toggles[key] = match.group(1)
        return toggles

    def test_every_engine_setting_is_reachable_in_production(self) -> None:
        environments = self._model_service_environments()
        reachable = set.intersection(*(set(env) for env in environments.values()))

        unreachable = self._settings_read_from_environment() - reachable
        unreachable -= self.PER_TEAM_SETTINGS

        self.assertEqual(
            unreachable,
            set(),
            "masking_engine reads these but no model service in "
            "deploy/compose.yml passes them, so no deployment can reach the "
            "setting at all: add each to the x-model-toggles anchor and "
            "document it in deploy/.env.example, or add it to "
            "PER_TEAM_SETTINGS if it belongs to one team's identity",
        )

    def test_per_team_settings_are_set_where_they_apply(self) -> None:
        # PER_TEAM_SETTINGS is an exemption from the check above, so it may not
        # quietly grow to cover a setting no service sets at all.
        environments = self._model_service_environments()
        somewhere = set().union(*(set(env) for env in environments.values()))

        self.assertEqual(
            self.PER_TEAM_SETTINGS - somewhere,
            set(),
            "exempted as per-team but set by no model service, so it is "
            "unreachable rather than per-team",
        )

    def test_production_toggle_defaults_match_the_code_defaults(self) -> None:
        import sys

        source = REPOSITORY_ROOT / "platform" / "masking-gateway" / "src"
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        from masking_engine.app.core import config

        defaults = config.Settings()
        read_by_engine = self._settings_read_from_environment()

        for name, compose_default in sorted(self._shared_toggles().items()):
            with self.subTest(setting=name):
                if name in self.PINNED_DIGEST_SETTINGS:
                    self.assertRegex(
                        compose_default,
                        r"^[0-9a-f]{64}$",
                        "production must pin a real digest so a Git LFS "
                        "pointer cannot pass as a model artifact",
                    )
                    continue
                if name in self.INTENTIONAL_PRODUCTION_DEFAULTS:
                    self.assertEqual(
                        self.INTENTIONAL_PRODUCTION_DEFAULTS[name],
                        compose_default,
                        "recorded as a deliberate production override; change "
                        "the recorded value with the reason if this is meant",
                    )
                    continue
                if name not in read_by_engine:
                    # A model-service or gunicorn setting rather than a
                    # masking_engine one; config.py is not its source of truth.
                    self.assertFalse(
                        hasattr(defaults, name.lower()),
                        f"{name} has a Settings field but load_settings never "
                        "reads it, so the deployment value goes nowhere",
                    )
                    continue

                code_default = getattr(defaults, name.lower())
                if isinstance(code_default, bool):
                    code_default = "on" if code_default else "off"
                elif isinstance(code_default, dict):
                    code_default = ""
                self.assertEqual(
                    str(code_default),
                    compose_default,
                    "deploy/compose.yml would become a second, quieter source "
                    "of defaults: make it match config.py, or record the "
                    "divergence in INTENTIONAL_PRODUCTION_DEFAULTS with why",
                )

    def test_every_shared_toggle_is_documented_for_operators(self) -> None:
        # Reachable but undocumented is only half a toggle: nothing tells an
        # operator it exists, what it costs, or what a safe value looks like.
        documented = set(
            re.findall(
                r"^#?\s*([A-Z][A-Z0-9_]*)=",
                (REPOSITORY_ROOT / "deploy/.env.example").read_text(),
                re.MULTILINE,
            )
        )

        self.assertEqual(
            set(self._shared_toggles()) - documented,
            set(),
            "reachable in deploy/compose.yml but absent from "
            "deploy/.env.example; document each one there, commented out, so "
            "the compose default stays the single source of truth",
        )

    # Keys preflight requires that no manifest can supply, each with why.
    # Everything else is a digest or an upstream revision and belongs in
    # `MODEL_IDENTITY`; deriving those is what keeps a mistyped one from
    # being possible at all, so an entry here has to earn its place.
    OPERATOR_SET_KEYS = {
        # The name the deployment serves Berts under, pinned by value in
        # deploy-gateway.sh against the release's approved name. The manifest
        # records the upstream repository it was exported from
        # (`source_model`), which is a different string, so there is nothing
        # here to derive it from -- .env.example carries it instead.
        "BERTS_MODEL_NAME": "a deployment-local service name, not a manifest field",
    }

    def test_every_model_key_preflight_requires_can_be_derived(self) -> None:
        """A required key with no derivation path is an operator dead end.

        This is the shape of the failure that reached production: the release
        that added per-language Berts routing made four keys required, the
        live environment file predated it and held none of them, and preflight
        refused it. That refusal is correct -- but the only thing that can put
        those keys into the file is `migrate-env.py`, run from the host by
        `stage-model-release.sh`, and nothing in the repository checked that
        the two agree about the key list.

        They can only agree by hand: preflight's list is bash words and the
        derivation table is a Python dict in another file. So the check is
        here rather than in either of them, and it fails in a merge request
        instead of in a manual production job.

        The environment file itself is out of reach -- it lives on the host and
        holds the service token, so no test can know what it contains. What is
        reachable is the guarantee that the remedy exists for every key that
        can be demanded.
        """
        required = self._model_keys_preflight_requires()
        self.assertTrue(required, "no model identity keys were parsed at all")
        derivable = self._model_keys_migrate_env_derives()

        self.assertEqual(
            required - derivable - set(self.OPERATOR_SET_KEYS),
            set(),
            "deploy-gateway.sh preflight requires these keys, and "
            "migrate-env.py cannot derive them, so an environment file that "
            "predates them has no way to gain them: add each one to "
            "MODEL_IDENTITY in deploy/migrate-env.py, or to "
            "OPERATOR_SET_KEYS here with the reason it is typed by hand",
        )

    def test_every_hand_typed_exemption_is_still_one(self) -> None:
        """An exemption that has stopped being true has to go, not linger.

        The list above stops looking at these keys, so nothing else would say
        that one of them had gained a derivation and could be dropped from
        here -- and a key sitting in both places reads as reviewed when it is
        only listed twice.
        """
        derivable = self._model_keys_migrate_env_derives()
        for key, reason in self.OPERATOR_SET_KEYS.items():
            with self.subTest(key=key):
                self.assertNotIn(
                    key,
                    derivable,
                    f"migrate-env.py now derives {key}; delete the exemption "
                    f"claiming {reason!r}",
                )
                self.assertIn(
                    key,
                    (REPOSITORY_ROOT / "deploy/.env.example").read_text(
                        encoding="utf-8"
                    ),
                    f"{key} is typed by hand and is not in .env.example, so "
                    "an operator has nothing to copy it from",
                )

    def test_the_derivation_table_carries_nothing_preflight_ignores(self) -> None:
        """The other direction, so the table cannot quietly rot.

        A key derived into the environment file and required by nothing is a
        digest nobody checks: it stays at whatever the last release wrote,
        reads as verified, and is not.
        """
        self.assertEqual(
            self._model_keys_migrate_env_derives()
            - self._model_keys_preflight_requires(),
            set(),
            "migrate-env.py derives these keys and preflight never requires "
            "them; either check them in deploy-gateway.sh or drop them from "
            "MODEL_IDENTITY",
        )

    @staticmethod
    def _model_keys_preflight_requires() -> set[str]:
        """Every model-identity key named in a `vps_require_vars` call.

        The names sit on continuation lines that carry no quotes, which is
        what separates them from the remedy text in the first argument -- that
        text mentions `BERTS_LANGUAGE_ROUTING=off`, which is an operator
        switch and not a derived value.
        """
        script = (REPOSITORY_ROOT / "deploy/deploy-gateway.sh").read_text(
            encoding="utf-8"
        )
        pattern = re.compile(r"\b(?:OCTO|SYNTHIFY|BERTS)_[A-Z0-9_]+\b")
        keys: set[str] = set()
        collecting = False
        for line in script.splitlines():
            if "vps_require_vars" in line:
                collecting = True
            elif not collecting:
                continue
            if '"' not in line:
                keys.update(pattern.findall(line))
            if not line.rstrip().endswith("\\"):
                collecting = False
        return keys

    @staticmethod
    def _model_keys_migrate_env_derives() -> set[str]:
        """Every key `MODEL_IDENTITY` knows how to read out of a manifest.

        Parsed rather than imported: `migrate-env.py` is a hyphenated script
        that runs on the host, and importing it here would be a second way to
        load it that has to keep working.
        """
        source = (REPOSITORY_ROOT / "deploy/migrate-env.py").read_text(encoding="utf-8")

        def names(node: ast.AST) -> list[ast.expr]:
            # `X = {...}` and the annotated `X: T = {...}` bind the same table.
            if isinstance(node, ast.Assign):
                return list(node.targets)
            if isinstance(node, ast.AnnAssign):
                return [node.target]
            return []

        table = next(
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "MODEL_IDENTITY"
                for target in names(node)
            )
        )
        assert isinstance(table, ast.Dict)
        keys: set[str] = set()
        for team in table.values:
            assert isinstance(team, ast.Dict)
            keys.update(
                key.value
                for key in team.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
        return keys


if __name__ == "__main__":
    unittest.main()
