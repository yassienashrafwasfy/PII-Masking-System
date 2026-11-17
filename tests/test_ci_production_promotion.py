"""Static policy tests for verified-candidate production promotion."""

from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class GitLabLoader(yaml.SafeLoader):
    """Parse GitLab's !reference tag sufficiently for policy assertions."""


def _reference(loader: yaml.SafeLoader, node: yaml.Node) -> list[object]:
    return loader.construct_sequence(node)


GitLabLoader.add_constructor("!reference", _reference)


def load_ci(relative_path: str) -> dict[str, object]:
    return yaml.load((ROOT / relative_path).read_text(), Loader=GitLabLoader)


# --------------------------------------------------------------------------
# A small offline model of GitLab's job selection.
#
# The failure this exists to catch cannot be seen by reading any single file:
# a job whose `needs:` target is excluded by *its own* rules makes the entire
# configuration invalid, and GitLab reports it only by refusing to create the
# pipeline ("The pipeline failed due to an error on the CI/CD configuration
# file"). That is how the default branch broke at commit 0417116, when the
# arena publish jobs were merge-request-only while the arena deploy jobs that
# need them were default-branch-only.
#
# Rules are now change-scoped per lane, which multiplies the number of
# contexts in which that mistake can be made. Simulating selection here turns
# a class of pipeline-creation failure into a unit test.
# --------------------------------------------------------------------------

# Top-level keys that configure the pipeline rather than declare a job.
NOT_A_JOB = frozenset(
    {"stages", "variables", "default", "workflow", "include", "image"}
)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a GitLab `changes:` glob to a regex.

    GitLab matches with Ruby's File.fnmatch under FNM_PATHNAME, where a single
    `*` does not cross a `/` but a `**` path segment matches any number of
    directories (including none). Python's fnmatch has neither property, so
    `deploy/*` would wrongly match `deploy/mlflow-vps/deploy-arena.sh` and the
    lane split would look correct here while failing on the real runner.
    """
    out = []
    for segment in pattern.split("/"):
        if segment == "**":
            out.append("(?:[^/]+/)*")
            continue
        piece = ""
        for char in segment:
            if char == "*":
                piece += "[^/]*"
            elif char == "?":
                piece += "[^/]"
            else:
                piece += re.escape(char)
        out.append(piece + "/")
    return re.compile("".join(out).rstrip("/") + r"\Z")


def _matches_changes(patterns: list[str], changed_paths: list[str]) -> bool:
    compiled = [_glob_to_regex(pattern) for pattern in patterns]
    return any(rx.match(path) for path in changed_paths for rx in compiled)


def _evaluate_if(expression: str, context: dict[str, str]) -> bool:
    """Evaluate the `&&`-joined comparison forms this repo's rules use.

    Supports `$VAR`, `$VAR == "literal"`, `$VAR != "literal"`, the
    variable-to-variable `$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH`, and the
    regex forms `$VAR =~ /pattern/` / `$VAR !~ /pattern/`. Anything else
    raises rather than defaulting to "matched", because a silently unmatched
    expression would make every scenario below vacuously pass.
    """
    for term in expression.split("&&"):
        term = term.strip()
        comparison = re.fullmatch(r'\$(\w+)\s*(==|!=)\s*(?:"([^"]*)"|\$(\w+))', term)
        if comparison:
            name, operator, literal, other = comparison.groups()
            expected = literal if other is None else (context.get(other) or "")
            actual = context.get(name) or ""
            if (actual == expected) is not (operator == "=="):
                return False
            continue
        regex_match = re.fullmatch(r"\$(\w+)\s*(=~|!~)\s*/(.*)/", term)
        if regex_match:
            name, operator, pattern = regex_match.groups()
            actual = context.get(name) or ""
            matched = re.search(pattern, actual) is not None
            if matched is not (operator == "=~"):
                return False
            continue
        reference = re.fullmatch(r"\$(\w+)", term)
        if reference:
            if not context.get(reference.group(1)):
                return False
            continue
        # An unrecognised form would silently evaluate as "matched" and make
        # every scenario below vacuously pass.
        raise AssertionError(f"unsupported rule expression: {term!r}")
    return True


def load_merged_config() -> dict[str, object]:
    """Every top-level key across .gitlab-ci.yml and its includes."""
    root = load_ci(".gitlab-ci.yml")
    merged: dict[str, object] = dict(root)
    for entry in root["include"]:
        merged.update(load_ci(entry["local"]))
    return merged


def resolve_job(name: str, merged: dict[str, object]) -> dict[str, object]:
    """Flatten a job's `extends:` chain the way GitLab does.

    Later parents override earlier ones and the job overrides them all. A
    shallow merge is enough: only `rules`, `needs` and `stage` are read, and
    `rules` is a list, which GitLab replaces rather than merges -- which is
    exactly why the rules-bearing template must come last in an `extends:` list.
    """
    node = merged[name]
    assert isinstance(node, dict), name
    parents = node.get("extends", [])
    if isinstance(parents, str):
        parents = [parents]
    resolved: dict[str, object] = {}
    for parent in parents:
        resolved.update(resolve_job(parent, merged))
    resolved.update({k: v for k, v in node.items() if k != "extends"})
    return resolved


def job_names(merged: dict[str, object]) -> list[str]:
    return [
        name
        for name, value in merged.items()
        if not name.startswith(".")
        and name not in NOT_A_JOB
        and isinstance(value, dict)
    ]


def _rules_select(
    rules: list[dict[str, object]] | None,
    context: dict[str, str],
    changed_paths: list[str],
) -> bool:
    if rules is None:
        # No rules at all means the job always runs; only `default:` and the
        # templates lack them, but be explicit rather than assume.
        return True
    for rule in rules:
        condition = rule.get("if")
        if condition is not None and not _evaluate_if(condition, context):
            continue
        patterns = rule.get("changes")
        if patterns is not None and not _matches_changes(patterns, changed_paths):
            continue
        return rule.get("when", "on_success") != "never"
    return False


def pipeline_is_created(
    merged: dict[str, object], context: dict[str, str], changed_paths: list[str]
) -> bool:
    return _rules_select(merged["workflow"]["rules"], context, changed_paths)


def select_jobs(
    merged: dict[str, object], context: dict[str, str], changed_paths: list[str]
) -> set[str]:
    """The jobs GitLab would put in this pipeline."""
    if not pipeline_is_created(merged, context, changed_paths):
        return set()
    return {
        name
        for name in job_names(merged)
        if _rules_select(resolve_job(name, merged).get("rules"), context, changed_paths)
    }


def needs_of(name: str, merged: dict[str, object]) -> set[str]:
    needs = resolve_job(name, merged).get("needs", [])
    return {
        need["job"] if isinstance(need, dict) else need
        for need in needs
        if not (isinstance(need, dict) and need.get("optional"))
    }


MR = {"CI_PIPELINE_SOURCE": "merge_request_event"}
MAIN_PUSH = {
    "CI_PIPELINE_SOURCE": "push",
    "CI_COMMIT_BRANCH": "main",
    "CI_DEFAULT_BRANCH": "main",
}
MAIN_WEB = {**MAIN_PUSH, "CI_PIPELINE_SOURCE": "web"}
SCHEDULE = {"CI_PIPELINE_SOURCE": "schedule", "SECURITY_SCAN": "weekly"}
TAG_PUSH = {
    "CI_PIPELINE_SOURCE": "push",
    "CI_DEFAULT_BRANCH": "main",
    "CI_COMMIT_TAG": "v1.2.3",
}

PLATFORM_MR_JOBS = {
    "fetch_ocr_artifacts",
    "publish_gateway",
    "publish_model_service",
    "scan_gateway_image",
    "scan_model_service_image",
    "qa_release_gate",
    "record_production_candidate",
}
PLATFORM_MAIN_JOBS = {
    "load_production_candidate",
    "qa_arena_evaluation",
    "preflight_production",
    "deploy_production",
}
ARENA_BUILD_JOBS = {"publish_arena_api", "publish_arena_ui"}
ARENA_MAIN_JOBS = ARENA_BUILD_JOBS | {
    "scan_arena_api_image",
    "scan_arena_ui_image",
    "preflight_arena_mlflow",
    "deploy_arena_mlflow",
}
# The unconditional merge-request safety net: no `changes:` filter, so these
# run for any change at all.
ALWAYS_ON_MR = {
    "build_preflight",
    "pre_commit",
    "fast_checks",
}


class ProductionPromotionPolicyTests(unittest.TestCase):
    def test_main_workflow_is_release_changes_or_controlled_web_only(self) -> None:
        config = load_ci(".gitlab-ci.yml")
        workflow_rules = config["workflow"]["rules"]

        # `arena` carries the advisory leaderboard evaluation. It sits after
        # release because it consumes the candidate manifest that
        # load_production_candidate resolves, and before deploy so that
        # nothing unexpected trails the deployment stage.
        self.assertEqual(config["stages"][-3:], ["release", "arena", "deploy"])
        self.assertIn("ci/release.yml", [entry["local"] for entry in config["include"]])
        self.assertEqual(
            config[".mr_only_rules"]["rules"][0]["if"],
            '$CI_PIPELINE_SOURCE == "merge_request_event"',
        )
        self.assertEqual(
            config[".release_candidate_rules"]["rules"][0]["if"],
            '$CI_PIPELINE_SOURCE == "merge_request_event"',
        )
        self.assertTrue(config[".release_candidate_rules"]["rules"][0]["changes"])
        self.assertTrue(
            any(
                rule.get("if")
                == '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "push"'
                and rule.get("changes")
                for rule in workflow_rules
            )
        )
        self.assertIn(
            {
                "if": '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "web"'
            },
            workflow_rules,
        )
        self.assertIn({"if": '$CI_PIPELINE_SOURCE == "schedule"'}, workflow_rules)

    def test_verification_jobs_are_merge_request_only(self) -> None:
        build = load_ci("ci/build.yml")
        validate = load_ci("ci/validate.yml")
        ml = load_ci("ci/ml.yml")
        container = load_ci("ci/container.yml")
        security = load_ci("ci/security.yml")

        self.assertEqual(build["build_preflight"]["extends"], ".mr_only_rules")
        self.assertEqual(validate["pre_commit"]["extends"], ".mr_only_rules")
        self.assertEqual(validate["fast_checks"]["extends"], ".mr_only_rules")
        self.assertEqual(
            ml["model_artifacts"]["rules"][0]["if"],
            '$CI_PIPELINE_SOURCE == "merge_request_event"',
        )
        self.assertEqual(
            ml["ml_smoke_cpu"]["rules"][0]["if"],
            '$CI_PIPELINE_SOURCE == "merge_request_event"',
        )
        for job in ("publish_gateway", "publish_model_service"):
            self.assertIn(".release_candidate_rules", container[job]["extends"])
        for job in ("scan_gateway_image", "scan_model_service_image"):
            self.assertIn(".release_candidate_rules", security[job]["extends"])
        self.assertEqual(
            security["repository_security"]["rules"],
            [{"if": '$CI_PIPELINE_SOURCE == "schedule" && $SECURITY_SCAN == "weekly"'}],
        )

    def test_candidate_manifest_has_only_verified_image_inputs(self) -> None:
        release = load_ci("ci/release.yml")
        record = release["record_production_candidate"]
        script = "\n".join(record["script"])

        self.assertEqual(record["stage"], "release")
        # qa_release_gate is required, not optional: a candidate must not be
        # recordable without the downstream contract gate having passed.
        self.assertEqual(
            {need["job"] for need in record["needs"]},
            {
                "publish_gateway",
                "publish_model_service",
                "scan_gateway_image",
                "scan_model_service_image",
                "qa_release_gate",
            },
        )
        self.assertEqual(record["extends"], ".release_candidate_rules")
        self.assertIn("JOB-TOKEN: $CI_JOB_TOKEN", script)
        self.assertIn(
            "packages/generic/production-candidate/${CI_COMMIT_SHA}/production-candidate.env",
            script,
        )
        self.assertNotIn("VPS_", script)
        self.assertNotIn("SSH_PRIVATE_KEY", script)
        for key in (
            "CANDIDATE_SHA",
            "GATEWAY_IMAGE_REF",
            "GATEWAY_IMAGE_DIGEST",
            "MODEL_SERVICE_IMAGE_REF",
            "MODEL_SERVICE_IMAGE_DIGEST",
            "CANDIDATE_PIPELINE_ID",
        ):
            self.assertIn(f"{key}=", script)

    def test_downstream_qa_gate_blocks_and_arena_does_not(self) -> None:
        """The gate must be blocking and the arena must never be."""
        qa = load_ci("ci/qa.yml")
        gate = qa["qa_release_gate"]
        arena = qa["qa_arena_evaluation"]

        # The gate runs on the same merge-request condition as the images it
        # validates, so a candidate cannot be published without being gated.
        self.assertEqual(gate["extends"], ".release_candidate_rules")
        self.assertEqual(gate["stage"], "qa")
        self.assertNotIn("allow_failure", gate)
        self.assertEqual(
            {need["job"] for need in gate["needs"]},
            {"publish_gateway", "publish_model_service"},
        )

        # The arena publishes a ranking; a ranking must not gate a deployment.
        self.assertEqual(arena["extends"], ".main_promotion_rules")
        self.assertEqual(arena["stage"], "arena")
        self.assertIs(arena["allow_failure"], True)
        self.assertEqual(
            {need["job"] for need in arena["needs"]}, {"load_production_candidate"}
        )

        for job in (gate, arena):
            # Default variables must not cross the project boundary. A trigger
            # job forwards the top-level `variables:` block downstream as
            # pipeline variables, and qa-automation restricts who may set those,
            # so forwarding them makes the downstream pipeline unconstructable:
            # "Insufficient permissions to set pipeline variables".
            self.assertIs(job["inherit"]["variables"], False)

            trigger = job["trigger"]
            # `mirror` reports the downstream status without holding a runner
            # slot; a polling script would deadlock a `concurrent = 1` runner.
            self.assertEqual(trigger["strategy"], "mirror")
            self.assertEqual(trigger["project"], "internshipjuly2026/qa-automation")

            # Inputs are interpolated downstream, so GitLab passes their values
            # literally - a "$VAR" input silently becomes the string "$VAR".
            # Every input must therefore be a constant.
            for name, value in trigger["inputs"].items():
                self.assertNotIn("$", str(value), f"input {name} cannot expand")

            # Candidate identity is resolved downstream from the registry API
            # without requiring user-defined pipeline variables (which are blocked
            # when minimum role to set pipeline variables is no_one_allowed).

            # No credential may cross the boundary by either route. Inputs are
            # readable from the API by anyone with read access, and QA runs an
            # ephemeral candidate that needs no production secret.
            self.assertNotIn("service_token", trigger["inputs"])
            for value in list(trigger["inputs"].values()):
                self.assertNotIn(
                    "c253e10377bf",
                    str(value),
                    "token must not cross the trigger boundary",
                )

        self.assertEqual(gate["trigger"]["inputs"]["evaluation_mode"], "synthetic")
        self.assertEqual(arena["trigger"]["inputs"]["evaluation_mode"], "release")

    def test_main_loads_and_validates_candidate_without_sourcing_it(self) -> None:
        release = load_ci("ci/release.yml")
        deploy = load_ci("ci/deploy.yml")
        load = release["load_production_candidate"]
        script = "\n".join(load["script"])

        self.assertEqual(load["stage"], "release")
        self.assertEqual(load["extends"], ".main_promotion_rules")
        self.assertIn("JOB-TOKEN: $CI_JOB_TOKEN", script)
        # $CI_COMMIT_SHA on main is not the commit record_production_candidate ran
        # its pipeline against under squash-merge (or any other merge strategy
        # that mints a new commit) -- the candidate must be looked up by the
        # merged MR's own pre-merge source SHA instead.
        self.assertIn("repository/commits/${CI_COMMIT_SHA}/merge_requests", script)
        self.assertIn('.state == "merged"', script)
        self.assertIn(".target_branch == $branch", script)
        self.assertIn(
            "packages/generic/production-candidate/${candidate_lookup_sha}/production-candidate.env",
            script,
        )
        self.assertIn('test "$candidate_sha" = "$candidate_lookup_sha"', script)
        # Regression guard: *reading* the candidate by the raw push SHA is
        # exactly the bug this fixed. The job also republishes the manifest
        # under that SHA for downstream QA, so the check is on the download
        # specifically rather than on any mention of ${CI_COMMIT_SHA} -- a
        # blanket assertion could not tell a read from a write.
        download = next(
            line for line in script.splitlines() if '--output "$candidate_file"' in line
        )
        self.assertIn("${candidate_lookup_sha}", download)
        self.assertNotIn("${CI_COMMIT_SHA}", download)
        self.assertNotIn('test "$candidate_sha" = "$CI_COMMIT_SHA"', script)

        self.assertIn('"$CI_REGISTRY_IMAGE/masking-gateway@"', script)
        self.assertIn('"$CI_REGISTRY_IMAGE/model-service@"', script)
        # The manifest is attacker-influenced input, so it must be parsed, never
        # executed. Check actual shell statements rather than the raw text: the
        # word "source" legitimately appears in prose about the source SHA, and
        # a substring match over comments reports that as a violation.
        statements = [
            stripped
            for line in script.splitlines()
            for stripped in [line.strip()]
            if stripped and not stripped.startswith("#")
        ]
        for statement in statements:
            self.assertFalse(
                statement.startswith(("source ", ". ")),
                f"candidate manifest must not be executed: {statement!r}",
            )
        self.assertEqual(
            load["artifacts"]["reports"]["dotenv"],
            "loaded-production-candidate.env",
        )
        self.assertEqual(
            {need["job"] for need in deploy["preflight_production"]["needs"]},
            {"load_production_candidate"},
        )
        self.assertEqual(
            {need["job"] for need in deploy["deploy_production"]["needs"]},
            {"preflight_production", "load_production_candidate"},
        )


class QaTriggerVariablePolicyTests(unittest.TestCase):
    """Neither QA trigger may carry `variables:`.

    They would become pipeline variables in internshipjuly2026/qa-automation,
    whose "Pipeline variables" setting is `no_one_allowed` (GitLab.com's
    default since 17.7) and is not ours to change. The failure is not a
    silently-empty handover -- GitLab refuses to create the downstream pipeline
    at all: "downstream pipeline can not be created, Insufficient permissions
    to set pipeline variables". That is worse than sending nothing, because the
    downstream never runs and so never reports what it was missing.

    The candidate identity is resolved downstream instead, from the upstream
    bridge job that points at the downstream pipeline's own id. This test
    exists because adding `variables:` here looks like the obvious fix and
    breaks the pipeline the moment it merges.
    """

    TRIGGERS = ("qa_release_gate", "qa_arena_evaluation")

    def setUp(self) -> None:
        self.config = load_ci("ci/qa.yml")

    def test_no_trigger_job_sets_pipeline_variables(self) -> None:
        for job in self.TRIGGERS:
            with self.subTest(job=job):
                self.assertNotIn(
                    "variables",
                    self.config[job],
                    f"{job} sets variables:, which qa-automation's "
                    "no_one_allowed pipeline-variables setting rejects "
                    "outright, blocking downstream pipeline creation",
                )

    def test_global_variables_are_not_inherited_into_the_triggers(self) -> None:
        """Inherited globals would trip the same permission error."""
        for job in self.TRIGGERS:
            with self.subTest(job=job):
                self.assertIs(self.config[job]["inherit"]["variables"], False)

    def test_triggers_still_mirror_so_a_failure_is_visible_upstream(self) -> None:
        for job in self.TRIGGERS:
            with self.subTest(job=job):
                self.assertEqual(self.config[job]["trigger"]["strategy"], "mirror")


class CandidateImageAliasTests(unittest.TestCase):
    """The promoted candidate must gain a tag named after the main commit.

    Downstream QA resolves the candidate by asking the registry for the tag
    named after the triggering pipeline's commit, because neither
    `trigger:variables:` nor `trigger:inputs:` can carry the digests across the
    project boundary. But the publish jobs are merge-request-only, so no
    default-branch commit has ever had a tag and every arena evaluation failed
    with a bare "HTTP Error 404". Without this aliasing step the handover is
    silently broken again.
    """

    def setUp(self) -> None:
        self.script = "\n".join(
            step
            for step in load_ci("ci/release.yml")["load_production_candidate"]["script"]
            if isinstance(step, str)
        )

    def test_both_candidate_images_are_aliased_to_the_commit(self) -> None:
        self.assertIn("alias_digest_to_commit", self.script)
        for repository in ("masking-gateway", "model-service"):
            with self.subTest(repository=repository):
                self.assertRegex(
                    self.script,
                    rf'alias_digest_to_commit "\$\{{registry_path\}}/{repository}"',
                )

    def test_the_alias_targets_the_verified_digest_not_a_rebuild(self) -> None:
        """A rebuild could yield a digest other than the tested candidate's."""
        self.assertIn(
            'alias_digest_to_commit "${registry_path}/masking-gateway" '
            '"$gateway_image_digest"',
            self.script,
        )
        self.assertIn(
            'alias_digest_to_commit "${registry_path}/model-service" '
            '"$model_service_image_digest"',
            self.script,
        )

    def test_the_alias_runs_after_the_manifest_is_validated(self) -> None:
        """Aliasing an unvalidated digest would publish an unchecked image."""
        self.assertLess(
            self.script.index('test "$record_count" -eq 6 || fail'),
            self.script.index("alias_digest_to_commit "),
        )

    def test_the_media_type_is_preserved_when_retagging(self) -> None:
        """A manifest list and a single manifest are not interchangeable."""
        self.assertIn("media_type=", self.script)
        self.assertIn('--header "Content-Type: ${media_type}"', self.script)


class CandidateManifestRepublishTests(unittest.TestCase):
    """The promoted candidate's manifest must be reachable by the main SHA.

    Downstream QA resolves the candidate from the pipeline whose bridge job
    triggered it, which yields a default-branch SHA.
    record_production_candidate keys its upload by the merge request head SHA,
    so without this republish that lookup 404s.

    The registry alias alone cannot replace it. Those tags carry the right
    digests but not the right identity: the gateway bakes GIT_SHA at build time
    from the merge request's commit, so QA verifying /version against a
    default-branch SHA fails with sha_mismatch. Only CANDIDATE_SHA inside this
    manifest is the value /version can match.
    """

    def setUp(self) -> None:
        self.script = "\n".join(
            step
            for step in load_ci("ci/release.yml")["load_production_candidate"]["script"]
            if isinstance(step, str)
        )

    def test_the_manifest_is_republished_under_the_main_sha(self) -> None:
        self.assertIn("--upload-file loaded-production-candidate.env", self.script)
        self.assertIn(
            "packages/generic/production-candidate/${CI_COMMIT_SHA}/"
            "production-candidate.env",
            self.script,
        )

    def test_the_republish_is_skipped_on_a_fast_forward_merge(self) -> None:
        """Main and the MR head are the same commit, so it already exists."""
        self.assertIn(
            'if [ "$CI_COMMIT_SHA" != "$candidate_lookup_sha" ]; then', self.script
        )

    def test_the_republished_manifest_is_the_validated_one(self) -> None:
        """Uploading anything unvalidated would hand QA an unchecked candidate."""
        self.assertLess(
            self.script.index('test "$record_count" -eq 6 || fail'),
            self.script.index("--upload-file loaded-production-candidate.env"),
        )


class GlobSemanticsTests(unittest.TestCase):
    """The lane split is only as trustworthy as the glob translation."""

    def test_a_single_star_does_not_cross_a_directory_separator(self) -> None:
        # This is the whole reason deploy/** was decomposed: `deploy/*` must
        # keep the arena's deploy scripts out of the platform lane.
        self.assertTrue(_matches_changes(["deploy/*"], ["deploy/compose.yml"]))
        self.assertFalse(
            _matches_changes(["deploy/*"], ["deploy/mlflow-vps/deploy-arena.sh"])
        )
        self.assertFalse(
            _matches_changes(["deploy/*"], ["deploy/systemd/vps-docker-gc.timer"])
        )

    def test_a_double_star_segment_matches_zero_or_more_directories(self) -> None:
        for path in (
            "platform/model-service/Dockerfile",
            "platform/model-service/src/service.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    _matches_changes(["platform/model-service/**/*"], [path])
                )

    def test_suffixed_patterns_stay_anchored(self) -> None:
        self.assertTrue(_matches_changes(["teams/**/*.py"], ["teams/octo/adapter.py"]))
        self.assertFalse(
            _matches_changes(["teams/**/*.py"], ["teams/octo/onnx/m.onnx"])
        )
        self.assertTrue(
            _matches_changes(["teams/*/onnx/**/*"], ["teams/octo/onnx/model.onnx"])
        )
        self.assertFalse(_matches_changes(["ci/**/*.yml"], ["ci/ml.yml.bak"]))


class NoNeedsMayDangleTests(unittest.TestCase):
    """No selected job may `need` a job the same rules excluded.

    GitLab does not report this as a failing job. It refuses to create the
    pipeline at all, so the branch simply stops building -- the exact outcome
    of commit 0417116. Every context the rules can produce is checked here.
    """

    def setUp(self) -> None:
        self.merged = load_merged_config()

    def assert_closed_under_needs(
        self, context: dict[str, str], changed_paths: list[str], label: str
    ) -> None:
        selected = select_jobs(self.merged, context, changed_paths)
        for job in sorted(selected):
            for need in sorted(needs_of(job, self.merged)):
                self.assertIn(
                    need,
                    selected,
                    f"{label}: {job} needs {need}, which the rules exclude -- "
                    "GitLab rejects the whole configuration for this",
                )

    def test_every_context_is_closed_under_needs(self) -> None:
        contexts = [
            (MR, ["evaluation/leaderboard/src/App.jsx"], "arena-only MR"),
            (MR, ["evaluation/leaderboard_api/ranking.py"], "arena-api-only MR"),
            (MR, ["teams/octo/adapter.py"], "model-only MR"),
            (MR, ["teams/octo/onnx/model.onnx"], "weights-only MR"),
            (MR, ["platform/masking-gateway/src/gateway.py"], "gateway-only MR"),
            (MR, ["deploy/mlflow-vps/arena-compose.yml"], "arena-deploy MR"),
            (MR, ["deploy/systemd/vps-docker-gc.timer"], "vps-unit MR"),
            (MR, ["deploy/vps-common.sh"], "shared-deploy-helper MR"),
            (MR, ["docs/architecture.md"], "docs-only MR"),
            (MR, ["ci/qa.yml"], "ci-config MR"),
            (MAIN_PUSH, ["evaluation/leaderboard/src/App.jsx"], "arena-only main push"),
            (MAIN_PUSH, ["teams/octo/adapter.py"], "model-only main push"),
            (MAIN_PUSH, ["teams/octo/onnx/model.onnx"], "weights-only main push"),
            (
                MAIN_PUSH,
                ["platform/masking-gateway/src/gateway.py"],
                "gateway-only main push",
            ),
            (
                MAIN_PUSH,
                ["deploy/mlflow-vps/deploy-arena.sh"],
                "arena-deploy main push",
            ),
            (MAIN_PUSH, ["deploy/vps-common.sh"], "shared-helper main push"),
            (
                MAIN_PUSH,
                ["platform/masking-gateway/src/gateway.py", "evaluation/config/x.yml"],
                "both-lanes main push",
            ),
            (MAIN_PUSH, ["docs/architecture.md"], "docs-only main push"),
            (MAIN_WEB, ["docs/architecture.md"], "manual web pipeline"),
            (SCHEDULE, [], "weekly security schedule"),
        ]
        for context, changed, label in contexts:
            with self.subTest(scenario=label):
                self.assert_closed_under_needs(context, changed, label)


class LaneSelectionTests(unittest.TestCase):
    """Each lane runs for its own paths and stays out of the other's."""

    def setUp(self) -> None:
        self.merged = load_merged_config()

    def selected(self, context: dict[str, str], changed: list[str]) -> set[str]:
        return select_jobs(self.merged, context, changed)

    def test_an_arena_merge_request_skips_the_platform_lane(self) -> None:
        """The point of the change: no torch build for a stylesheet edit."""
        selected = self.selected(MR, ["evaluation/leaderboard/src/App.jsx"])
        self.assertTrue(ARENA_BUILD_JOBS <= selected)
        self.assertTrue(ALWAYS_ON_MR <= selected)
        self.assertFalse(PLATFORM_MR_JOBS & selected)
        # The ML jobs carry their own change filters and must stay out too.
        self.assertNotIn("ml_smoke_cpu", selected)
        self.assertNotIn("model_artifacts", selected)

    def test_a_model_merge_request_skips_the_arena_lane(self) -> None:
        selected = self.selected(MR, ["teams/octo/adapter.py"])
        self.assertTrue(PLATFORM_MR_JOBS <= selected)
        self.assertIn("ml_smoke_cpu", selected)
        self.assertFalse(ARENA_BUILD_JOBS & selected)

    def test_a_weights_only_merge_request_republishes_the_model_service(self) -> None:
        """The ONNX artifacts are baked into the image by .dockerignore.

        Without teams/*/onnx in the platform lane this merged with no image
        rebuild and no recorded candidate, so the deployed image drifted.
        """
        selected = self.selected(MR, ["teams/octo/onnx/model.onnx"])
        self.assertIn("publish_model_service", selected)
        self.assertIn("record_production_candidate", selected)
        self.assertFalse(ARENA_BUILD_JOBS & selected)

    def test_an_arena_main_push_runs_no_production_promotion(self) -> None:
        """load_production_candidate would 404: no candidate was recorded."""
        selected = self.selected(MAIN_PUSH, ["evaluation/leaderboard/src/App.jsx"])
        self.assertTrue(ARENA_MAIN_JOBS <= selected)
        self.assertFalse(PLATFORM_MAIN_JOBS & selected)

    def test_a_platform_main_push_runs_no_arena_jobs(self) -> None:
        selected = self.selected(MAIN_PUSH, ["platform/masking-gateway/src/gateway.py"])
        self.assertTrue(PLATFORM_MAIN_JOBS <= selected)
        self.assertFalse(ARENA_MAIN_JOBS & selected)

    def test_the_deploy_directory_is_split_by_consumer(self) -> None:
        gateway_units = self.selected(
            MAIN_PUSH, ["deploy/systemd/pii-masking-watchdog.timer"]
        )
        self.assertTrue(PLATFORM_MAIN_JOBS <= gateway_units)
        self.assertFalse(ARENA_MAIN_JOBS & gateway_units)

        arena_scripts = self.selected(
            MAIN_PUSH, ["deploy/mlflow-vps/arena-compose.yml"]
        )
        self.assertTrue(ARENA_MAIN_JOBS <= arena_scripts)
        self.assertFalse(PLATFORM_MAIN_JOBS & arena_scripts)

        # vps-common.sh is scp'd to both hosts, so it belongs to both lanes.
        shared = self.selected(MAIN_PUSH, ["deploy/vps-common.sh"])
        self.assertTrue((PLATFORM_MAIN_JOBS | ARENA_MAIN_JOBS) <= shared)

    def test_a_ci_edit_still_exercises_both_lanes(self) -> None:
        for path in (".gitlab-ci.yml", "ci/container.yml"):
            with self.subTest(path=path):
                selected = self.selected(MR, [path])
                self.assertTrue(PLATFORM_MR_JOBS <= selected)
                self.assertTrue(ARENA_BUILD_JOBS <= selected)

    def test_the_web_pipeline_remains_the_unfiltered_escape_hatch(self) -> None:
        """A manual main pipeline must offer everything, whatever changed."""
        selected = self.selected(MAIN_WEB, ["README.md"])
        self.assertTrue((PLATFORM_MAIN_JOBS | ARENA_MAIN_JOBS) <= selected)

    def test_a_docs_only_change_creates_no_main_pipeline(self) -> None:
        self.assertFalse(pipeline_is_created(self.merged, MAIN_PUSH, ["README.md"]))
        self.assertEqual(self.selected(MAIN_PUSH, ["README.md"]), set())
        # ...but a docs merge request still gets the unconditional checks.
        self.assertEqual(self.selected(MR, ["README.md"]), ALWAYS_ON_MR)

    def test_a_scheduled_pipeline_is_still_only_the_repository_scan(self) -> None:
        self.assertEqual(self.selected(SCHEDULE, []), {"repository_security"})

    def test_a_semver_tag_push_creates_only_the_release_job(self) -> None:
        self.assertEqual(self.selected(TAG_PUSH, []), {"create_gitlab_release"})

    def test_a_non_semver_tag_creates_no_pipeline(self) -> None:
        # v.1.0.0 (dotted) and a backup/* branch-style tag are both real tags
        # in this repo's history, but neither is the vX.Y.Z release form.
        for tag in ("v.1.0.0", "backup/dev-before-rebase", "v1.0"):
            with self.subTest(tag=tag):
                context = {**TAG_PUSH, "CI_COMMIT_TAG": tag}
                self.assertFalse(pipeline_is_created(self.merged, context, []))


class LaneRuleLockstepTests(unittest.TestCase):
    """Jobs bound by `needs:` must share one rule template, not copies.

    Two templates that happen to agree today drift apart the first time one is
    edited, and the symptom is a default branch that stops building rather than
    a red job. Sharing the template makes the coupling unforgettable.
    """

    def setUp(self) -> None:
        self.merged = load_merged_config()

    def rules_of(self, job: str) -> object:
        return resolve_job(job, self.merged)["rules"]

    # Every job in a `needs:`-bound group must be selected in exactly the same
    # contexts. Literal rule equality is too strong -- the manual jobs restate
    # `when: manual` on each arm -- so compare the selection outcome instead,
    # which is the property GitLab actually enforces.
    MAIN_CONTEXTS = (
        (MAIN_PUSH, ["evaluation/leaderboard/src/App.jsx"]),
        (MAIN_PUSH, ["platform/masking-gateway/src/gateway.py"]),
        (MAIN_PUSH, ["teams/octo/onnx/model.onnx"]),
        (MAIN_PUSH, ["deploy/mlflow-vps/arena-compose.yml"]),
        (MAIN_PUSH, ["deploy/systemd/pii-masking-watchdog.timer"]),
        (MAIN_PUSH, ["deploy/vps-common.sh"]),
        (MAIN_PUSH, ["docs/architecture.md"]),
        (MAIN_WEB, ["README.md"]),
    )
    MR_CONTEXTS = (
        (MR, ["evaluation/leaderboard/src/App.jsx"]),
        (MR, ["platform/masking-gateway/src/gateway.py"]),
        (MR, ["teams/octo/adapter.py"]),
        (MR, ["docs/architecture.md"]),
        (MR, ["ci/qa.yml"]),
    )

    def assert_selected_together(self, group: set[str], label: str, contexts) -> None:
        for context, changed in contexts:
            selected = select_jobs(self.merged, context, changed) & group
            with self.subTest(group=label, changed=changed):
                self.assertIn(
                    selected,
                    (set(), group),
                    f"{label} split apart: only {sorted(selected)} selected",
                )

    def test_the_platform_main_lane_is_selected_as_one_group(self) -> None:
        """load_production_candidate and its three dependants, or none of them."""
        self.assert_selected_together(
            PLATFORM_MAIN_JOBS, "platform main lane", self.MAIN_CONTEXTS
        )

    def test_the_arena_main_lane_is_selected_as_one_group(self) -> None:
        self.assert_selected_together(
            ARENA_MAIN_JOBS, "arena main lane", self.MAIN_CONTEXTS
        )

    def test_the_platform_merge_request_lane_is_selected_as_one_group(self) -> None:
        self.assert_selected_together(
            PLATFORM_MR_JOBS, "platform MR lane", self.MR_CONTEXTS
        )

    def test_the_arena_build_pair_is_selected_as_one_group(self) -> None:
        self.assert_selected_together(
            ARENA_BUILD_JOBS, "arena build pair", self.MR_CONTEXTS + self.MAIN_CONTEXTS
        )

    def test_each_lane_job_uses_the_shared_template_not_a_copy(self) -> None:
        """A copied rule block agrees today and drifts on the next edit."""
        for job, template in (
            ("load_production_candidate", ".main_promotion_rules"),
            ("qa_arena_evaluation", ".main_promotion_rules"),
            ("preflight_production", ".production_deploy_rules"),
            ("deploy_production", ".production_deploy_rules"),
            ("publish_arena_api", ".arena_image_rules"),
            ("publish_arena_ui", ".arena_image_rules"),
            ("scan_arena_api_image", ".arena_scan_rules"),
            ("scan_arena_ui_image", ".arena_scan_rules"),
            ("preflight_arena_mlflow", ".arena_deploy_rules"),
            ("deploy_arena_mlflow", ".arena_deploy_rules"),
        ):
            with self.subTest(job=job):
                self.assertEqual(self.rules_of(job), self.merged[template]["rules"])

    def test_the_arena_deploy_jobs_stay_manual_through_their_rules(self) -> None:
        """`rules:` overrides the job-level `when:`, so each arm must restate it."""
        for rule in self.merged[".arena_deploy_rules"]["rules"]:
            with self.subTest(rule=rule.get("if", "fallback")):
                self.assertIn(rule.get("when"), {"manual", "never"})

    def test_the_web_arms_carry_no_change_filter(self) -> None:
        """The escape hatch stops being one the moment it is filtered."""
        web = '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "web"'
        for template in (
            ".main_promotion_rules",
            ".production_deploy_rules",
            ".arena_image_rules",
            ".arena_scan_rules",
            ".arena_deploy_rules",
        ):
            with self.subTest(template=template):
                arms = [r for r in self.merged[template]["rules"] if r.get("if") == web]
                self.assertEqual(len(arms), 1)
                self.assertNotIn("changes", arms[0])

    def test_both_lanes_can_open_a_main_pipeline(self) -> None:
        """A lane the workflow rule cannot match would never build on main."""
        push = (
            '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "push"'
        )
        filters = [
            rule["changes"]
            for rule in self.merged["workflow"]["rules"]
            if rule.get("if") == push and "changes" in rule
        ]
        self.assertEqual(len(filters), 2, "one workflow rule per lane")
        self.assertIn(
            self.merged[".release_candidate_rules"]["rules"][0]["changes"], filters
        )
        self.assertIn(self.merged[".arena_image_rules"]["rules"][0]["changes"], filters)


if __name__ == "__main__":
    unittest.main()
