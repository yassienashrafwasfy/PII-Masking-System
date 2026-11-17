"""Exercise the MLflow-VPS arena deployment without a Docker daemon.

This target differs from the (superseded) NiFi-VPS design: it is reached
publicly through the mlflow-auth-gui project's existing Traefik instance
rather than an SSH tunnel, with only the masking-console API paths gated by
basic auth. These tests pin that split, the network/label wiring Traefik
needs, and the input validation that runs before anything is pulled or
started -- including the basic-auth dollar-escaping check, which guards a
real, empirically-confirmed Docker Compose / bash parsing conflict.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPOSITORY_ROOT / "deploy" / "mlflow-vps"
ARENA_COMPOSE = DEPLOY_DIR / "arena-compose.yml"
CREATE_ENV_SCRIPT = DEPLOY_DIR / "create-env.sh"
UI_DIR = REPOSITORY_ROOT / "evaluation" / "leaderboard"
UI_DOCKERFILE = UI_DIR / "Dockerfile"
UI_NGINX_CONF = UI_DIR / "nginx.conf"
LOCAL_COMPOSE = REPOSITORY_ROOT / "evaluation" / "docker-compose.yml"
API_DOCKERFILE = REPOSITORY_ROOT / "evaluation" / "leaderboard_api" / "Dockerfile"
SAMPLES_DIR = REPOSITORY_ROOT / "evaluation" / "samples"

VALID_API_REF = "registry.example.com/arena-api@sha256:" + "a" * 64
VALID_UI_REF = "registry.example.com/arena-ui@sha256:" + "b" * 64


class ArenaComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(ARENA_COMPOSE.read_text())

    def test_the_ui_runs_unprivileged_on_an_unprivileged_port(self) -> None:
        """The UI must not need a capability `cap_drop: [ALL]` takes away.

        A root nginx master is wedged between two dropped capabilities: it
        needs CAP_DAC_OVERRIDE to create its temp directories inside a tmpfs
        pre-owned by uid 101, and CAP_CHOWN if that tmpfs is left root-owned
        instead. Running as uid 101 avoids both, but then the listen port must
        be >= 1024, because binding lower needs CAP_NET_BIND_SERVICE -- also
        dropped. This crash-looped the container 24 times in production.
        """
        dockerfile = UI_DOCKERFILE.read_text()
        self.assertRegex(dockerfile, r"(?m)^USER\s+nginx\b")

        listen_ports = re.findall(r"(?m)^\s*listen\s+(\d+)", UI_NGINX_CONF.read_text())
        self.assertTrue(listen_ports, "nginx.conf declares no listen port")
        for port in listen_ports:
            self.assertGreaterEqual(
                int(port),
                1024,
                f"nginx listens on privileged port {port} as a non-root user",
            )

    def test_every_reference_to_the_ui_port_agrees_with_nginx(self) -> None:
        """Traefik's port label and both healthchecks must track nginx.conf.

        These four values live in three files and are only ever exercised
        together in production, so a stale one is invisible until Traefik
        routes to a closed port.
        """
        port = re.search(r"(?m)^\s*listen\s+(\d+)", UI_NGINX_CONF.read_text()).group(1)

        labels = "\n".join(self.config["services"]["arena-ui"]["labels"])
        self.assertIn(
            f"traefik.http.services.leaderboard.loadbalancer.server.port={port}", labels
        )

        production_check = " ".join(
            self.config["services"]["arena-ui"]["healthcheck"]["test"]
        )
        self.assertIn(f"127.0.0.1:{port}", production_check)

        self.assertIn(f"127.0.0.1:{port}", UI_DOCKERFILE.read_text())

        local = yaml.safe_load(LOCAL_COMPOSE.read_text())
        published = local["services"]["leaderboard"]["ports"]
        self.assertTrue(
            any(str(entry).endswith(f":{port}") for entry in published),
            f"evaluation/docker-compose.yml does not publish container port {port}",
        )

    def test_console_samples_ship_inside_the_api_image(self) -> None:
        """The picker must not depend on a folder that exists only on a laptop.

        It previously pointed at a host path present on one workstation, so the
        deployed console reported samples_available=false and fell back to a
        plain file dialog. Baking them in is what makes it work anywhere.
        """
        supported = [
            path
            for path in SAMPLES_DIR.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".pdf", ".txt", ".csv", ".json"}
        ]
        self.assertTrue(supported, "evaluation/samples/ ships no usable document")
        self.assertIn("COPY samples ./samples", API_DOCKERFILE.read_text())

        environment = self.config["services"]["leaderboard-api"]["environment"]
        self.assertIn("/app/samples", environment["ARENA_SAMPLE_DIRECTORY"])

    def test_every_team_the_console_can_offer_is_accepted(self) -> None:
        """ARENA_TEAMS also gates runs.py server-side, not just the picker.

        The team console lets a visitor choose the model, so a team missing here
        would render as a pickable option that fails at submit with a 400. The
        masking console still shows one team, but takes it from the board's
        `champion` rather than from this list.
        """
        environment = self.config["services"]["leaderboard-api"]["environment"]
        teams = environment["ARENA_TEAMS"]
        for team in ("octo", "synthify", "berts"):
            self.assertIn(team, teams)

    def test_the_champion_console_has_a_fallback_team(self) -> None:
        """Used only when the board cannot be read; without it there is no
        destination at all when object storage is unreachable."""
        environment = self.config["services"]["leaderboard-api"]["environment"]
        self.assertIn("berts", environment["ARENA_CHAMPION_FALLBACK_TEAM"])

    def test_ranking_history_is_archived(self) -> None:
        """qa-automation overwrites one key per run and R2 has no versioning,
        so without this archive the board can only ever show a single point."""
        environment = self.config["services"]["leaderboard-api"]["environment"]
        self.assertIn("true", environment["ARENA_ARCHIVE_RANKINGS"].lower())
        self.assertIn("results/history", environment["ARENA_HISTORY_PREFIX"])

    def test_mock_preview_data_is_off_in_production(self) -> None:
        """Left on, the mock hides real published results behind fake ones."""
        environment = self.config["services"]["leaderboard-api"]["environment"]
        self.assertIn("false", environment["ARENA_MOCK_LEADERBOARD"].lower())

    def test_only_the_ui_joins_the_shared_traefik_network(self) -> None:
        services = self.config["services"]
        # Named leaderboard-api, not arena-api: evaluation/leaderboard/nginx.conf
        # proxy_passes to that exact hostname.
        self.assertNotIn("mlflow_traefik", services["leaderboard-api"]["networks"])
        self.assertIn("mlflow_traefik", services["arena-ui"]["networks"])

    def test_the_traefik_network_is_external_and_never_created_by_this_project(
        self,
    ) -> None:
        network = self.config["networks"]["mlflow_traefik"]
        self.assertTrue(network["external"])
        self.assertIn("mlflow-auth-gui_mlflow", network["name"])

    def test_no_service_publishes_a_host_port(self) -> None:
        for name, service in self.config["services"].items():
            with self.subTest(service=name):
                self.assertNotIn("ports", service)

    def test_the_ui_declares_traefik_docker_network_to_disambiguate(self) -> None:
        labels = self.config["services"]["arena-ui"]["labels"]
        self.assertTrue(
            any(label.startswith("traefik.docker.network=") for label in labels)
        )

    def test_one_router_serves_the_host_with_no_auth_middleware(self) -> None:
        """The console must never sit behind a browser credential prompt.

        A second, higher-priority router used to match the console's API paths
        and attach a basic-auth middleware. In a browser that surfaced as a
        sign-in dialog the instant anyone uploaded a document -- and the apr1
        hash behind it had to survive both Compose's .env parser and bash's
        `$$` expansion to authenticate at all. Both the router and the
        middleware are gone; this fails if either returns.
        """
        labels = "\n".join(self.config["services"]["arena-ui"]["labels"])
        self.assertIn("traefik.http.routers.leaderboard.rule=", labels)
        self.assertNotIn("leaderboard-console", labels)
        self.assertNotIn("basicauth", labels.lower())
        self.assertNotIn("middlewares=", labels)

    def test_images_and_storage_credentials_are_required_rather_than_defaulted(
        self,
    ) -> None:
        raw = ARENA_COMPOSE.read_text()
        for variable in (
            "ARENA_API_IMAGE_REF",
            "ARENA_UI_IMAGE_REF",
            "ARENA_S3_ENDPOINT_URL",
            "ARENA_S3_ACCESS_KEY_ID",
            "ARENA_S3_SECRET_ACCESS_KEY",
        ):
            with self.subTest(variable=variable):
                self.assertIn(f"${{{variable}:?", raw)

    def test_no_basic_auth_credential_is_required_to_deploy(self) -> None:
        """Deploying must not depend on a credential nobody uses any more."""
        self.assertNotIn("LEADERBOARD_BASIC_AUTH_USERS", ARENA_COMPOSE.read_text())

    def test_this_is_a_separate_project_from_the_gateway_and_the_local_stack(
        self,
    ) -> None:
        gateway = yaml.safe_load(
            (REPOSITORY_ROOT / "deploy" / "compose.yml").read_text()
        )
        self.assertNotIn("arena-api", gateway["services"])
        local = yaml.safe_load(
            (REPOSITORY_ROOT / "evaluation" / "docker-compose.yml").read_text()
        )
        # The local stack has no Traefik labels at all; confirms these are
        # genuinely different deployment targets, not one file reused.
        self.assertNotIn("labels", local["services"]["leaderboard"])


class ArenaPreflightTests(unittest.TestCase):
    """Run deploy-arena.sh preflight with docker and curl stubbed out."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)

        self.deploy_dir = self.root / "deploy" / "mlflow-vps"
        self.deploy_dir.mkdir(parents=True)
        for name in ("arena-compose.yml", "deploy-arena.sh"):
            target = self.deploy_dir / name
            target.write_bytes((DEPLOY_DIR / name).read_bytes())
            target.chmod(0o700 if name.endswith(".sh") else 0o600)
        # Beside deploy-arena.sh, not one level up: CI scps all three files
        # flat into a single staging directory and the script sources
        # "$deploy_dir/vps-common.sh" from its own directory. Staging that file
        # at the repository's own path made every preflight test die on
        # "vps-common.sh: No such file or directory" instead of exercising the
        # validation it is meant to cover.
        common = self.deploy_dir / "vps-common.sh"
        common.write_bytes((REPOSITORY_ROOT / "deploy" / "vps-common.sh").read_bytes())
        common.chmod(0o700)

        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.command_log = self.root / "commands.log"
        for command in ("curl",):
            stub = self.fake_bin / command
            stub.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "{command} $*" >> "{self.command_log}"\n'
                "exit 0\n"
            )
            stub.chmod(0o755)
        # `docker` needs to answer two different things: network inspect
        # (network exists) and compose config (renders fine).
        docker_stub = self.fake_bin / "docker"
        docker_stub.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "docker $*" >> "{self.command_log}"\n'
            'if [ "$1" = "network" ] && [ "$2" = "inspect" ]; then exit 0; fi\n'
            'if [ "$1" = "compose" ]; then exit 0; fi\n'
            "exit 0\n"
        )
        docker_stub.chmod(0o755)

        self.env_file = self.root / "arena.env"

    def write_env(self, **overrides: str) -> None:
        values = {
            "LEADERBOARD_DOMAIN": "leaderboard.mlflow.me",
            "ARENA_S3_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com",
            "ARENA_S3_ACCESS_KEY_ID": "key-id",
            "ARENA_S3_SECRET_ACCESS_KEY": "secret-value",
        }
        values.update(overrides)
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items() if value != "")
        )
        self.env_file.chmod(0o600)

    def run_preflight(self, *extra: str) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        environment["PATH"] = f"{self.fake_bin}:{environment['PATH']}"
        return subprocess.run(
            [
                "bash",
                str(self.deploy_dir / "deploy-arena.sh"),
                "preflight",
                "--env-file",
                str(self.env_file),
                "--api-image-ref",
                VALID_API_REF,
                "--ui-image-ref",
                VALID_UI_REF,
                "--git-sha",
                "c0ffee",
                *extra,
            ],
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_valid_inputs_pass_without_starting_anything(self) -> None:
        self.write_env()
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Preflight checks passed", result.stdout)
        commands = self.command_log.read_text() if self.command_log.exists() else ""
        self.assertIn("network inspect", commands)
        self.assertIn("compose", commands)
        self.assertNotIn("up -d", commands)
        self.assertNotIn("pull", commands)

    def test_a_missing_basic_auth_value_no_longer_blocks_a_deploy(self) -> None:
        """The console is no longer gated, so the credential is not required.

        Four tests here used to pin the validation of that hash -- its colon,
        its placeholder, its emptiness, and the doubled `$$` Compose needs.
        All of it existed to keep a browser sign-in prompt working. With the
        gate removed, preflight must not fail for want of a credential nobody
        presents.
        """
        # write_env omits any value passed as "", so this produces an env file
        # with no LEADERBOARD_BASIC_AUTH_USERS line at all.
        self.write_env(LEADERBOARD_BASIC_AUTH_USERS="")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_tag_pinned_image_is_rejected(self) -> None:
        self.write_env()
        result = subprocess.run(
            [
                "bash",
                str(self.deploy_dir / "deploy-arena.sh"),
                "preflight",
                "--env-file",
                str(self.env_file),
                "--api-image-ref",
                "registry.example.com/arena-api:latest",
                "--ui-image-ref",
                VALID_UI_REF,
                "--git-sha",
                "c0ffee",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{self.fake_bin}:{os.environ['PATH']}"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned by digest", result.stderr)

    def test_reusing_the_gateway_service_token_is_rejected(self) -> None:
        self.write_env(
            SERVICE_TOKEN="shared-secret", ARENA_S3_SECRET_ACCESS_KEY="shared-secret"
        )
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SERVICE_TOKEN", result.stderr)

    def test_a_missing_storage_credential_is_rejected(self) -> None:
        self.write_env(ARENA_S3_SECRET_ACCESS_KEY="")
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARENA_S3_SECRET_ACCESS_KEY", result.stderr)

    def test_a_world_readable_env_file_is_rejected(self) -> None:
        self.write_env()
        self.env_file.chmod(0o644)
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("readable", result.stderr)

    def test_a_missing_traefik_network_is_rejected(self) -> None:
        self.write_env()
        docker_stub = self.fake_bin / "docker"
        docker_stub.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "network" ] && [ "$2" = "inspect" ]; then exit 1; fi\n'
            "exit 0\n"
        )
        docker_stub.chmod(0o755)
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_apply_requires_explicit_approval(self) -> None:
        self.write_env()
        result = subprocess.run(
            [
                "bash",
                str(self.deploy_dir / "deploy-arena.sh"),
                "apply",
                "--env-file",
                str(self.env_file),
                "--api-image-ref",
                VALID_API_REF,
                "--ui-image-ref",
                VALID_UI_REF,
                "--git-sha",
                "c0ffee",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{self.fake_bin}:{os.environ['PATH']}"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--approve", result.stderr)
        commands = self.command_log.read_text() if self.command_log.exists() else ""
        self.assertNotIn("up -d", commands)


class CreateEnvScriptTests(unittest.TestCase):
    """create-env.sh's one real job: encode the basic-auth hash so it passes
    deploy-arena.sh's own dollar-escaping check, without the operator having
    to reason about Docker Compose's .env parsing by hand."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)
        self.output = self.root / ".env"

    def run_script(self, stdin_lines: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(CREATE_ENV_SCRIPT), str(self.output)],
            input="\n".join(stdin_lines) + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )

    def valid_answers(self) -> list[str]:
        # No console username/password: the basic-auth gate is gone, so the
        # script no longer prompts for one.
        return [
            "leaderboard.mlflow.me",
            "mlflow-auth-gui_mlflow",
            "websecure",
            "letsEncrypt",
            "https://account.r2.cloudflarestorage.com",
            "key-id",
            "key-id",  # confirm
            "secret-value",
            "secret-value",  # confirm
        ]

    def test_writes_a_mode_0600_file(self) -> None:
        result = self.run_script(self.valid_answers())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.exists())
        self.assertEqual(oct(self.output.stat().st_mode)[-3:], "600")

    def test_no_console_credential_is_written_or_prompted_for(self) -> None:
        """The console gate is gone, so nothing should ask for a password.

        This previously generated an apr1 hash and pinned the doubled-`$$`
        encoding Compose's .env parser needs. Leaving the prompt in place would
        collect a credential that nothing reads.
        """
        self.run_script(self.valid_answers())
        self.assertNotIn("LEADERBOARD_BASIC_AUTH_USERS", self.output.read_text())
        self.assertNotIn("openssl passwd", CREATE_ENV_SCRIPT.read_text())

    def test_refuses_to_overwrite_an_existing_file(self) -> None:
        self.output.write_text("pre-existing content\n")
        result = self.run_script(["irrelevant"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(self.output.read_text(), "pre-existing content\n")

    # These two used to exercise the console password. prompt_secret still
    # guards the R2 secret access key, so they are retargeted there rather than
    # dropped -- the reprompt behaviour is what matters, not which secret.
    def test_an_empty_secret_is_rejected_and_reprompted(self) -> None:
        answers = self.valid_answers()
        # Index 7 is the R2 secret; insert an empty attempt before the real one.
        answers = answers[:7] + ["", "ignored"] + answers[7:]
        result = self.run_script(answers)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("must not be empty", result.stderr.lower())

    def test_a_mismatched_confirmation_is_rejected_and_reprompted(self) -> None:
        answers = self.valid_answers()
        answers = answers[:7] + ["firstsecret", "differentsecret"] + answers[7:]
        result = self.run_script(answers)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("did not match", result.stderr.lower())


class ArenaCiWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        import yaml as _yaml

        merged: dict = {}
        for path in [
            "ci/container.yml",
            "ci/security.yml",
            "ci/deploy.yml",
            ".gitlab-ci.yml",
        ]:
            merged.update(_yaml.safe_load((REPOSITORY_ROOT / path).read_text()) or {})
        self.pipeline = merged

    def test_arena_jobs_are_allowed_to_fail(self) -> None:
        for job in (
            "publish_arena_api",
            "publish_arena_ui",
            "scan_arena_api_image",
            "scan_arena_ui_image",
            ".arena_deploy_mlflow_base",
        ):
            with self.subTest(job=job):
                self.assertTrue(self.pipeline[job].get("allow_failure") is True)

    def test_mlflow_vps_jobs_use_distinct_variable_names_from_the_gateway_vps(
        self,
    ) -> None:
        base_script = "\n".join(
            self.pipeline[".arena_deploy_mlflow_base"]["before_script"]
        )
        for name in (
            "MLFLOW_VPS_HOST",
            "MLFLOW_VPS_USER",
            "MLFLOW_VPS_SSH_PRIVATE_KEY",
        ):
            self.assertIn(name, base_script)
        gateway_script = "\n".join(self.pipeline["deploy_production"]["script"])
        self.assertNotIn("MLFLOW_VPS_", gateway_script)

    def test_the_arena_images_build_on_both_merge_requests_and_the_default_branch(
        self,
    ) -> None:
        """Regression guard for the exact bug fixed at commit 0417116: a job
        whose `needs` target only runs on merge requests makes a default-branch
        pipeline impossible to create at all."""
        rules = self.pipeline[".arena_image_rules"]["rules"]
        conditions = "\n".join(r.get("if", "") for r in rules)
        self.assertIn("merge_request_event", conditions)
        self.assertIn("CI_DEFAULT_BRANCH", conditions)

    def test_the_pipeline_avoids_gitlab_only_yaml_tags(self) -> None:
        """Several tests in this repository parse ci/deploy.yml with a plain
        yaml.safe_load; a `!reference` tag broke that once already."""
        raw = (REPOSITORY_ROOT / "ci" / "deploy.yml").read_text()
        self.assertNotIn("!reference", raw)


if __name__ == "__main__":
    unittest.main()
