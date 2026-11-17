"""Regression tests for the transactional VPS migration interfaces."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class TransactionalDeploymentTests(unittest.TestCase):
    def test_ci_requires_manual_preflight_before_manual_live_promotion(self) -> None:
        config = yaml.safe_load((ROOT / "ci/deploy.yml").read_text())
        preflight = config["preflight_production"]
        deploy = config["deploy_production"]
        preflight_script = "\n".join(preflight["before_script"] + preflight["script"])
        deploy_script = "\n".join(deploy["before_script"] + deploy["script"])

        self.assertEqual(preflight["when"], "manual")
        self.assertFalse(preflight["allow_failure"])
        self.assertEqual(preflight["resource_group"], "production")
        self.assertIn("VPS_STAGING_DIR", preflight_script)
        self.assertIn("$CI_COMMIT_SHA/deploy", preflight_script)
        self.assertIn("VPS_ENV_FILE", preflight_script)
        self.assertIn("deploy-gateway.sh preflight", preflight_script)
        self.assertNotIn("promote-staged-bundle.sh apply", preflight_script)
        self.assertNotIn("deploy-gateway.sh apply", preflight_script)
        self.assertNotIn("install-vps.sh plan", preflight_script)
        self.assertNotIn("install-vps.sh apply", preflight_script)

        self.assertEqual(deploy["when"], "manual")
        self.assertFalse(deploy["allow_failure"])
        self.assertEqual(deploy["resource_group"], "production")
        self.assertEqual(deploy["environment"]["name"], "production")
        self.assertIn("preflight_production", {need["job"] for need in deploy["needs"]})
        self.assertEqual(
            {need["job"] for need in preflight["needs"]},
            {"load_production_candidate"},
        )
        self.assertEqual(
            {need["job"] for need in deploy["needs"]},
            {"preflight_production", "load_production_candidate"},
        )
        self.assertNotIn("deploy-gateway.sh preflight", deploy_script)
        self.assertNotIn("scp ", deploy_script)
        self.assertNotIn("install-vps.sh", deploy_script)
        self.assertIn("promote-staged-bundle.sh apply", deploy_script)
        self.assertIn("deploy-gateway.sh apply", deploy_script)
        self.assertLess(
            deploy_script.index("promote-staged-bundle.sh apply"),
            deploy_script.index("deploy-gateway.sh apply"),
        )
        self.assertNotIn("$VPS_DEPLOY_DIR/.env", preflight_script + deploy_script)

    def test_transactional_public_entrypoints_exist(self) -> None:
        for relative in (
            "deploy/promote-staged-bundle.sh",
            "deploy/migrate-env.py",
            "deploy/vps-common.sh",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_every_deployment_shell_entrypoint_parses(self) -> None:
        scripts = sorted((ROOT / "deploy").glob("*.sh"))
        scripts.append(ROOT / "deploy/pii-masking-nginx-control")
        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_preflight_infers_blue_without_mutating_state(self) -> None:
        with PreflightFixture() as fixture:
            before = fixture.snapshot()
            result = fixture.run_preflight()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Inferred active slot: blue", result.stdout)
            self.assertEqual(before, fixture.snapshot())
            self.assertFalse(fixture.state_dir.exists())
            commands = fixture.command_log.read_text()
            self.assertIn("docker compose", commands)
            self.assertIn("config --quiet", commands)
            self.assertNotIn(" pull ", commands)
            self.assertNotIn(" up ", commands)
            self.assertNotIn(" down ", commands)

    def test_preflight_fails_closed_when_nginx_and_docker_conflict(self) -> None:
        with PreflightFixture() as fixture:
            result = fixture.run_preflight({"FAKE_ACTIVE_SLOT": "green"})

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "Nginx-selected Compose gateway is not running", result.stderr
            )
            self.assertFalse(fixture.state_dir.exists())

    def test_preflight_infers_green_from_complete_evidence(self) -> None:
        with PreflightFixture() as fixture:
            fixture.set_upstream("green")
            result = fixture.run_preflight(
                {"FAKE_ACTIVE_SLOT": "green", "FAKE_LISTENERS": "green"}
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Inferred active slot: green", result.stdout)
            self.assertIn("candidate slot blue", result.stdout)
            self.assertFalse(fixture.state_dir.exists())

    def test_preflight_rejects_missing_active_listener(self) -> None:
        with PreflightFixture() as fixture:
            result = fixture.run_preflight({"FAKE_LISTENERS": "none"})

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("port is not listening", result.stderr)

    def test_preflight_rejects_both_slots_running(self) -> None:
        with PreflightFixture() as fixture:
            result = fixture.run_preflight({"FAKE_ACTIVE_SLOT": "both"})

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Both blue and green gateways are running", result.stderr)

    def test_preflight_rejects_invalid_state_file(self) -> None:
        with PreflightFixture() as fixture:
            fixture.state_dir.mkdir()
            (fixture.state_dir / "active-slot").write_text("purple\n")
            result = fixture.run_preflight()

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Active-slot state is invalid", result.stderr)

    def test_preflight_never_selects_active_project_as_candidate(self) -> None:
        with PreflightFixture() as fixture:
            result = fixture.run_preflight()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "--project-name pii-masking-green", fixture.command_log.read_text()
            )
            self.assertNotIn(
                "compose --project-name pii-masking-blue",
                fixture.command_log.read_text(),
            )

    def test_candidate_port_collision_fails_closed(self) -> None:
        with PreflightFixture() as fixture:
            result = fixture.run_preflight({"FAKE_LISTENERS": "both"})

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Candidate port is already listening", result.stderr)

    def test_failed_bundle_validation_does_not_touch_live_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            live = root / "live"
            shutil.copytree(ROOT / "deploy", staged)
            live.mkdir()
            marker = live / "deploy-gateway.sh"
            marker.write_text("known-good\n")
            (staged / "compose.yml").unlink()

            result = subprocess.run(
                [
                    str(staged / "promote-staged-bundle.sh"),
                    "apply",
                    "--source",
                    str(staged),
                    "--target",
                    str(live),
                    "--approve",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "known-good\n")

    def test_bundle_promotion_never_overwrites_legacy_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged"
            live = root / "live"
            shutil.copytree(ROOT / "deploy", staged)
            live.mkdir()
            legacy = live / ".env"
            legacy.write_text("SERVICE_TOKEN=legacy-secret\n")
            legacy.chmod(0o600)

            result = subprocess.run(
                [
                    str(staged / "promote-staged-bundle.sh"),
                    "apply",
                    "--source",
                    str(staged),
                    "--target",
                    str(live),
                    "--approve",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(legacy.read_text(), "SERVICE_TOKEN=legacy-secret\n")

    def test_environment_migration_is_private_and_never_replaces_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / ".env"
            values = root / "operator-values"
            output = root / ".env.next"
            legacy.write_text("SERVICE_TOKEN=legacy-secret\nACCELERATOR=cpu\n")
            values.write_text(
                "ENABLED_TEAMS=octo\nNIFI_DOCKER_BRIDGE_IP=172.18.0.1\nNGINX_UPSTREAM_FILE=/etc/nginx/snippets/pii-masking-gateway-upstream.inc\nOCTO_MODEL_DIR=/srv/pii-models/releases/release/octo\nOCTO_MODEL_VERSION=sha256:version\nOCTO_ONNX_MODEL_SHA256="
                + "b" * 64
                + "\nOCR_MODEL_DIR=/srv/pii-models/releases/release/ocr"
                + "\n"
            )
            legacy.chmod(0o600)
            values.chmod(0o600)

            result = subprocess.run(
                [
                    str(ROOT / "deploy/migrate-env.py"),
                    "--legacy",
                    str(legacy),
                    "--values-file",
                    str(values),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                legacy.read_text(), "SERVICE_TOKEN=legacy-secret\nACCELERATOR=cpu\n"
            )
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("legacy-secret", result.stdout + result.stderr)

            retry = subprocess.run(
                [
                    str(ROOT / "deploy/migrate-env.py"),
                    "--legacy",
                    str(legacy),
                    "--values-file",
                    str(values),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(retry.returncode, 0)
            self.assertIn("never overwritten", retry.stderr)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_ocr_directory_is_required_only_when_pdf_ocr_is_on(self) -> None:
        # The digests carry real values in .env.example, so OCR_MODEL_DIR is the
        # one OCR key an operator must actually resolve. Leaving the shipped
        # `replace-with-release-sha` placeholder in place must not pass.
        base = (
            "ENABLED_TEAMS=octo\n"
            "NIFI_DOCKER_BRIDGE_IP=172.18.0.1\n"
            "NGINX_UPSTREAM_FILE=/etc/nginx/snippets/pii-masking-gateway-upstream.inc\n"
            "OCTO_MODEL_DIR=/srv/pii-models/releases/release/octo\n"
            "OCTO_MODEL_VERSION=sha256:version\n"
            "OCTO_ONNX_MODEL_SHA256=" + "b" * 64 + "\n"
        )

        for supplied, expected_returncode in (
            (base + "PDF_OCR=off\n", 0),
            (base + "PDF_OCR=on\n", 2),
            (base + "PDF_OCR=on\nOCR_MODEL_DIR=/srv/pii-models/releases/r/ocr\n", 0),
        ):
            with self.subTest(supplied=supplied.splitlines()[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    legacy = root / ".env"
                    values = root / "operator-values"
                    output = root / ".env.next"
                    legacy.write_text("SERVICE_TOKEN=legacy-secret\n")
                    values.write_text(supplied)
                    legacy.chmod(0o600)
                    values.chmod(0o600)

                    result = subprocess.run(
                        [
                            str(ROOT / "deploy/migrate-env.py"),
                            "--legacy",
                            str(legacy),
                            "--values-file",
                            str(values),
                            "--output",
                            str(output),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertEqual(
                        result.returncode, expected_returncode, result.stderr
                    )
                    if expected_returncode != 0:
                        self.assertIn("OCR_MODEL_DIR", result.stderr)

    def test_unresolved_environment_migration_leaves_no_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / ".env"
            output = root / ".env.next"
            legacy.write_text("SERVICE_TOKEN=legacy-secret\nACCELERATOR=cpu\n")
            legacy.chmod(0o600)

            result = subprocess.run(
                [
                    str(ROOT / "deploy/migrate-env.py"),
                    "--legacy",
                    str(legacy),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unresolved required keys", result.stderr)
            self.assertFalse(output.exists())
            self.assertNotIn("legacy-secret", result.stdout + result.stderr)

    def test_nginx_helper_refuses_non_listening_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.conf"
            upstream.write_text(
                "upstream masking_gateway { server 127.0.0.1:18081; keepalive 32; }\n"
            )
            upstream.chmod(0o644)
            helper = root / "helper"
            helper.write_text(
                (ROOT / "deploy/pii-masking-nginx-control")
                .read_text()
                .replace("@NGINX_UPSTREAM_FILE@", str(upstream))
            )
            helper.chmod(0o700)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (fake_bin / "ss").write_text("#!/bin/sh\nexit 0\n")
            (fake_bin / "ss").chmod(0o700)
            before = upstream.read_bytes()

            result = subprocess.run(
                [str(helper), "switch", "18082"],
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(upstream.read_bytes(), before)

    def test_installer_plan_is_non_mutating(self) -> None:
        with PreflightFixture() as fixture:
            fixture.add_installer_environment()
            before = fixture.snapshot()
            result = fixture.run_installer_plan()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, fixture.snapshot())
            self.assertIn("Preserve Nginx upstream without replacement", result.stdout)
            self.assertIn("Watchdog remains disabled", result.stdout)

    def test_installer_orders_validation_and_rollback_safeguards(self) -> None:
        installer = (ROOT / "deploy/install-vps.sh").read_text()
        self.assertLess(
            installer.index('sudo visudo -cf "$rendered_sudoers"'),
            installer.index('sudo install -m "${modes[$index]}" "${sources[$index]}"'),
        )
        self.assertIn("rollback()", installer)
        self.assertIn("(( status == 0 )) || rollback", installer)
        watchdog_validation = installer.index('"$deploy_dir/watchdog-gateway.sh"')
        watchdog_enable = installer.index(
            "systemctl enable --now pii-masking-watchdog.timer"
        )

        self.assertIn(
            'sudo -u "$deploy_user" -H',
            installer[:watchdog_validation],
        )
        self.assertLess(
            watchdog_validation,
            watchdog_enable,
        )

    def test_installer_apply_failure_rolls_back_managed_files(self) -> None:
        with PreflightFixture() as fixture:
            fixture.add_installer_environment()
            fixture.configure_virtual_sudo()
            original_helper = (fixture.root / "nginx-control").read_bytes()
            virtual_sudoers = (
                fixture.virtual_root / "etc/sudoers.d/pii-masking-nginx-control"
            )
            virtual_sudoers.parent.mkdir(parents=True)
            virtual_sudoers.write_text("known-good-sudoers\n")

            result = fixture.run_installer_apply()

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                (fixture.root / "nginx-control").read_bytes(), original_helper
            )
            self.assertFalse((fixture.root / "server.conf").exists())
            self.assertEqual(virtual_sudoers.read_text(), "known-good-sudoers\n")
            self.assertFalse(
                (
                    fixture.virtual_root
                    / "etc/systemd/system/pii-masking-watchdog.service"
                ).exists()
            )

    def test_installer_apply_defers_watchdog_without_release_state(self) -> None:
        with PreflightFixture() as fixture:
            fixture.add_installer_environment()
            fixture.configure_virtual_sudo()

            result = fixture.run_installer_apply(fail_installed_nginx=False)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("watchdog enablement is deferred", result.stdout)
            self.assertIn("watchdog_enabled=false", result.stdout)
            self.assertTrue((fixture.root / "server.conf").is_file())
            self.assertTrue(
                (
                    fixture.virtual_root
                    / "etc/systemd/system/pii-masking-watchdog.service"
                ).is_file()
            )

    def test_model_integrity_precedes_candidate_start(self) -> None:
        deployer = (ROOT / "deploy/deploy-gateway.sh").read_text()
        self.assertLess(
            deployer.index('validate_teams_and_models "$enabled_teams"'),
            deployer.index('docker pull "$gateway_image_ref"'),
        )
        self.assertIn("BERTS_WARM_RSS_MIB is required", deployer)


class PreflightFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.deploy_dir = self.root / "deploy"
        self.fake_bin = self.root / "bin"
        self.state_dir = self.root / "state"
        self.command_log = self.root / "commands.log"
        shutil.copytree(ROOT / "deploy", self.deploy_dir)
        self.fake_bin.mkdir()
        self._write_models_and_environment()
        self._write_fakes()

    def __enter__(self) -> "PreflightFixture":
        return self

    def __exit__(self, *_: object) -> None:
        self.temporary.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def write_file(path: Path, content: bytes = b"fixture\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)

    @staticmethod
    def executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o700)

    def _write_models_and_environment(self) -> None:
        octo = self.root / "models" / "release-a" / "octo"
        synthify = self.root / "models" / "release-a" / "synthify"
        for relative in (
            "onnx/model.onnx",
            "onnx/manifest.json",
            "onnx/config.json",
            "onnx/sentencepiece.bpe.model",
            "onnx/special_tokens_map.json",
            "onnx/tokenizer.json",
            "onnx/tokenizer_config.json",
        ):
            self.write_file(octo / relative, relative.encode())
        for relative in (
            "onnx/model.onnx",
            "onnx/manifest.json",
            "onnx/gliner_config.json",
            "onnx/special_tokens_map.json",
            "onnx/tokenizer.json",
            "onnx/tokenizer_config.json",
        ):
            self.write_file(synthify / relative, relative.encode())

        # Production model releases are immutable to the deployment group.
        octo.chmod(0o755)
        synthify.chmod(0o755)

        self.upstream = self.root / "nginx-upstream.conf"
        self.upstream.write_text(
            "upstream masking_gateway { server 127.0.0.1:18081; keepalive 32; }\n"
        )
        self.upstream.chmod(0o644)
        self.env_file = self.deploy_dir / ".env.next"
        self.env_file.write_text(
            "\n".join(
                (
                    "SERVICE_TOKEN=fixture-secret-never-log",
                    "ENABLED_TEAMS=octo,synthify",
                    # These cases are about slot inference, ports and Nginx.
                    # PDF_OCR defaults on and would then demand a staged
                    # artifact tree; the OCR path itself is covered in
                    # tests/test_deployment_scripts.py.
                    "PDF_OCR=off",
                    "ACCELERATOR=cpu",
                    "NIFI_DOCKER_BRIDGE_IP=172.18.0.1",
                    "PRIVATE_GATEWAY_PORT=18080",
                    f"NGINX_UPSTREAM_FILE={self.upstream}",
                    f"NGINX_CONTROL_HELPER={self.root / 'nginx-control'}",
                    f"OCTO_MODEL_DIR={octo}",
                    "OCTO_MODEL_NAME=octo-pii-masker",
                    "OCTO_MODEL_VERSION=sha256:octo",
                    f"OCTO_ONNX_MODEL_SHA256={self.digest(octo / 'onnx/model.onnx')}",
                    f"SYNTHIFY_MODEL_DIR={synthify}",
                    "SYNTHIFY_MODEL_NAME=synthify-gliner-belgian-pii-ner",
                    "SYNTHIFY_MODEL_VERSION=sha256:synthify",
                    f"SYNTHIFY_ONNX_MODEL_DIGEST=sha256:{self.digest(synthify / 'onnx/model.onnx')}",
                    "MIN_AVAILABLE_MEMORY_PERCENT=25",
                    "NGINX_SWITCH_WAIT_SECONDS=1",
                    "DRAIN_MAX_SECONDS=0",
                    "DRAIN_POLL_SECONDS=1",
                    "",
                )
            )
        )
        self.env_file.chmod(0o600)
        self.executable(self.root / "nginx-control", "#!/bin/sh\nexit 0\n")

    def _write_fakes(self) -> None:
        self.executable(
            self.fake_bin / "docker",
            """#!/bin/sh
printf 'docker %s\n' "$*" >> "$COMMAND_LOG"
if [ "${1:-}" = ps ]; then
  case "$*:${FAKE_ACTIVE_SLOT:-blue}" in
    *pii-masking-blue*:blue|*pii-masking-blue*:both) echo pii-masking-blue-gateway-1 ;;
    *pii-masking-green*:green|*pii-masking-green*:both) echo pii-masking-green-gateway-1 ;;
  esac
  exit 0
fi
if [ "${1:-}" = compose ]; then exit 0; fi
exit 97
""",
        )
        self.executable(
            self.fake_bin / "ss",
            """#!/bin/sh
case "${FAKE_LISTENERS:-blue}" in
  blue) printf 'LISTEN 0 128 127.0.0.1:18081 0.0.0.0:*\n' ;;
  green) printf 'LISTEN 0 128 127.0.0.1:18082 0.0.0.0:*\n' ;;
  both) printf 'LISTEN 0 128 127.0.0.1:18081 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:18082 0.0.0.0:*\n' ;;
esac
""",
        )
        self.executable(
            self.fake_bin / "curl",
            """#!/bin/sh
printf 'HTTP/1.1 200 OK\r\nX-Git-SHA: active-release\r\nX-Image-Digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\r\n\r\n'
""",
        )
        self.executable(
            self.fake_bin / "sudo",
            '#!/bin/sh\n[ "${1:-}" != -n ] || shift\nexec "$@"\n',
        )

    def snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            str(path.relative_to(self.deploy_dir)): (
                path.read_bytes(),
                path.stat().st_mode,
            )
            for path in self.deploy_dir.rglob("*")
            if path.is_file()
        }

    def set_upstream(self, slot: str) -> None:
        port = {"blue": 18081, "green": 18082}[slot]
        self.upstream.write_text(
            f"upstream masking_gateway {{ server 127.0.0.1:{port}; keepalive 32; }}\n"
        )
        self.upstream.chmod(0o644)

    def add_installer_environment(self) -> None:
        with self.env_file.open("a") as output:
            output.write(
                f"NIFI_DOCKER_BRIDGE_IP=172.18.0.1\nNGINX_SERVER_FILE={self.root / 'server.conf'}\nDEPLOY_USER=fixture\n"
                f"MODEL_RELEASES_ROOT={self.root / 'pii-models'}\n"
                f"MODEL_STAGE_HELPER={self.root / 'model-stage'}\n"
                "MODEL_STAGE_REMOTE_URL=https://gitlab.example/fixture/repo.git\n"
            )
        self.env_file.chmod(0o600)
        self.executable(
            self.fake_bin / "ip",
            "#!/bin/sh\nprintf '1: bridge inet 172.18.0.1/16 scope global bridge\\n'\n",
        )
        self.executable(self.fake_bin / "nginx", "#!/bin/sh\nexit 0\n")
        self.executable(self.fake_bin / "visudo", "#!/bin/sh\nexit 0\n")
        self.executable(self.fake_bin / "systemd-analyze", "#!/bin/sh\nexit 0\n")
        self.executable(
            self.fake_bin / "systemctl",
            '#!/bin/sh\ncase "${1:-}" in is-enabled) echo disabled; exit 1;; is-active) echo inactive; exit 3;; esac\nexit 0\n',
        )
        self.executable(
            self.fake_bin / "sudo",
            '#!/bin/sh\n[ "${1:-}" != -n ] || shift\nexec "$@"\n',
        )

    def configure_virtual_sudo(self) -> None:
        self.virtual_root = self.root / "virtual-root"
        self.virtual_root.mkdir()
        self.executable(
            self.fake_bin / "sudo",
            """#!/usr/bin/env bash
[[ ${1:-} != -n ]] || shift
arguments=()
for argument in "$@"; do
  [[ $argument != /etc/* ]] || argument="$VIRTUAL_ROOT$argument"
  arguments+=("$argument")
done
exec "${arguments[@]}"
            """,
        )
        self.executable(
            self.fake_bin / "nginx",
            """#!/bin/sh
if [ "${FAIL_INSTALLED_NGINX:-}" = 1 ] && [ "$*" = "-t" ]; then exit 44; fi
exit 0
""",
        )

    def run_installer_plan(self) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "COMMAND_LOG": str(self.command_log),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
            }
        )
        return subprocess.run(
            [
                str(self.deploy_dir / "install-vps.sh"),
                "plan",
                "--env-file",
                str(self.env_file),
                "--state-dir",
                str(self.state_dir),
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def run_installer_apply(
        self, *, fail_installed_nginx: bool = True
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "COMMAND_LOG": str(self.command_log),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "VIRTUAL_ROOT": str(self.virtual_root),
                "FAIL_INSTALLED_NGINX": "1" if fail_installed_nginx else "0",
            }
        )
        return subprocess.run(
            [
                str(self.deploy_dir / "install-vps.sh"),
                "apply",
                "--env-file",
                str(self.env_file),
                "--state-dir",
                str(self.state_dir),
                "--approve",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def run_preflight(
        self, extra_environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "COMMAND_LOG": str(self.command_log),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
            }
        )
        if extra_environment:
            environment.update(extra_environment)
        a = "a" * 64
        b = "b" * 64
        return subprocess.run(
            [
                str(self.deploy_dir / "deploy-gateway.sh"),
                "preflight",
                "--env-file",
                str(self.env_file),
                "--state-dir",
                str(self.state_dir),
                "--gateway-image-ref",
                f"registry.example/gateway@sha256:{a}",
                "--gateway-image-digest",
                f"sha256:{a}",
                "--model-image-ref",
                f"registry.example/model@sha256:{b}",
                "--model-image-digest",
                f"sha256:{b}",
                "--git-sha",
                "0123456789abcdef",
                "--stable-url",
                "http://127.0.0.1:18080",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
