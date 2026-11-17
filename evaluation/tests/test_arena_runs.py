"""Interactive masking-run lifecycle: upload, discovery, and terminal states."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from leaderboard_api import runs
from leaderboard_api.config import Settings


@pytest.fixture
def settings(settings):
    """The buckets an upload travels through, and how long it may take."""
    return replace(
        settings,
        source_bucket="source",
        masked_bucket="destination",
        manifest_bucket="destination",
        run_timeout_seconds=900,
    )


def submit(
    settings,
    fake,
    team="octo",
    name="note.txt",
    ctype="text/plain",
    body=b"Jan Peeters",
    registry=None,
):
    return runs.submit(
        settings, team, name, ctype, body, registry=registry or runs.RunRegistry()
    )


def test_upload_lands_under_the_team_scoped_incoming_prefix(settings, fake):
    run = submit(settings, fake)

    # NiFi's ListS3 watches `incoming/<team>/requests/`, and the gateway
    # independently requires the key to start with `incoming/<team-id>/`.
    assert run.source_key.startswith("incoming/octo/requests/")
    assert run.source_key.endswith("-note.txt")
    assert run.run_id in run.source_key
    assert ("source", run.source_key) in fake.objects
    assert run.status == runs.STATUS_UPLOADED
    assert run.source_etag


def test_unsupported_team_is_refused_before_any_upload(settings, fake):
    with pytest.raises(runs.RunError, match="Unsupported team"):
        submit(settings, fake, team="ghost")
    assert fake.objects == {}


def test_unsupported_media_type_is_refused(settings, fake):
    with pytest.raises(runs.RunError, match="Unsupported content type"):
        submit(settings, fake, name="x.png", ctype="image/png", body=b"\x89PNG")
    assert fake.objects == {}


def test_empty_and_oversized_files_are_refused(settings, fake):
    with pytest.raises(runs.RunError, match="empty"):
        submit(settings, fake, body=b"")
    small = Settings(**{**settings.__dict__, "max_upload_bytes": 4})
    with pytest.raises(runs.RunError, match="above the"):
        submit(small, fake, body=b"way too long")


def test_filename_is_sanitized_into_the_object_key(settings, fake):
    run = submit(settings, fake, name="../../etc/pa ss wd.txt")
    assert "/.." not in run.source_key
    assert run.filename == "pa_ss_wd.txt"


def test_schema_shaped_manifest_completes_the_run(settings, fake):
    run = submit(settings, fake)
    masked_key = "masked/team=octo/request=nifi-cid/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    fake.add(
        "destination",
        "manifests/team=octo/request=nifi-cid/status.json",
        {
            "status": "completed",
            "correlation_id": "nifi-cid",
            "source_object_key": run.source_key,
            "masked_object_key": masked_key,
            "model_version": "17",
            "model_name": "berts-pii-camembert-bio-fr-int8",
            "completed_at": "2026-08-23T10:00:12.300Z",
        },
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_COMPLETED
    assert run.masked_key == masked_key
    assert run.correlation_id == "nifi-cid"
    assert run.model_version == "17"
    # Which checkpoint answered is its own field: a team holding one model per
    # language answers the same version with different names.
    assert run.model_name == "berts-pii-camembert-bio-fr-int8"
    assert run.payload()["model_version"] == "17"
    assert run.payload()["model_name"] == "berts-pii-camembert-bio-fr-int8"
    assert run.discovered_by == "manifest"
    assert run.masked_size == len(b"REDACTED")


def test_the_checkpoint_is_read_off_the_masked_key_when_no_manifest_says_it(
    settings, fake
):
    """The destination key is `.../model=<name>/version=<version>/...`.

    A manifest that predates the field, or a deployment that writes none,
    still has to let the console name the checkpoint that read the document.
    """
    run = submit(settings, fake)
    masked_key = (
        "masked/team=berts/model=berts-pii-bert-en-int8/version=17/"
        "request=041ce3f5/note.txt"
    )
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    fake.add(
        "destination",
        "manifests/team=berts/request=041ce3f5/status.json",
        {
            "status": "completed",
            "request_id": "041ce3f5",
            "source_key": run.source_key,
            "destination_key": masked_key,
            "model_version": "17",
        },
    )

    runs.refresh(settings, run)

    assert run.masked_key == masked_key
    assert run.model_name == "berts-pii-bert-en-int8"


def test_an_undiscoverable_checkpoint_stays_unset_rather_than_guessed(settings, fake):
    run = submit(settings, fake)
    masked_key = "masked/team=octo/request=nifi-cid/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    fake.add(
        "destination",
        "manifests/team=octo/request=nifi-cid/status.json",
        {
            "status": "completed",
            "correlation_id": "nifi-cid",
            "source_object_key": run.source_key,
            "masked_object_key": masked_key,
            "model_version": "17",
        },
    )

    runs.refresh(settings, run)

    assert run.model_name is None


def test_contract_prose_manifest_variant_also_completes_the_run(settings, fake):
    """The v1.2 contract prose uses different field names and uppercase statuses."""
    run = submit(settings, fake)
    masked_key = "masked/team=octo/request=041ce3f5/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    fake.add(
        "destination",
        "manifests/team=octo/request=041ce3f5/status.json",
        {
            "status": "SUCCEEDED",
            "request_id": "041ce3f5",
            "source_key": run.source_key,
            "destination_key": masked_key,
            "model_version": "17",
        },
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_COMPLETED
    assert run.masked_key == masked_key
    assert run.correlation_id == "041ce3f5"


def test_failure_manifest_surfaces_the_error_code(settings, fake):
    run = submit(settings, fake)
    fake.add(
        "destination",
        f"manifests/failed/octo/{run.submitted_at:%Y-%m-%d}/cid.json",
        {
            "status": "failed",
            "correlation_id": "cid",
            "source_object_key": run.source_key,
            "error_code": "API_HTTP_422",
            "message": "Masking API could not process the supplied document",
            "failed_at": "2026-08-23T10:00:12.300Z",
        },
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_FAILED
    assert run.error_code == "API_HTTP_422"
    assert "could not process" in run.message
    assert run.masked_key is None


def test_a_manifest_for_another_file_is_ignored(settings, fake):
    run = submit(settings, fake)
    fake.add(
        "destination",
        f"manifests/completed/octo/{run.submitted_at:%Y-%m-%d}/other.json",
        {
            "status": "completed",
            "correlation_id": "other",
            "source_object_key": "incoming/octo/2026-08-23/someone-else/note.txt",
            "masked_object_key": "masked/octo/2026-08-23/other/note.txt",
            "model_version": "17",
        },
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_PROCESSING
    assert run.masked_key is None


def test_masked_object_is_discovered_when_no_manifest_exists(settings, fake):
    run = submit(settings, fake)
    masked_key = f"masked/team=octo/request=x/{run.run_id}/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_COMPLETED
    assert run.masked_key == masked_key
    assert run.discovered_by == "masked-object-run-id"


def test_an_older_masked_object_with_the_same_name_is_not_claimed(settings, fake):
    run = submit(settings, fake)
    stale = run.submitted_at - timedelta(hours=3)
    fake.add(
        "destination",
        "masked/team=octo/request=previous/note.txt",
        "OLD",
        "text/plain",
        when=stale,
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_PROCESSING
    assert run.masked_key is None


def test_a_run_times_out_when_nothing_ever_appears(settings, fake):
    run = submit(settings, fake)
    run.submitted_at = datetime.now(UTC) - timedelta(
        seconds=settings.run_timeout_seconds + 5
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_TIMEOUT
    assert "NiFi" in run.message


def test_refresh_is_idempotent_once_terminal(settings, fake):
    run = submit(settings, fake)
    masked_key = f"masked/team=octo/request=x/{run.run_id}/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    runs.refresh(settings, run)
    history_length = len(run.history)

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_COMPLETED
    assert len(run.history) == history_length


def test_history_does_not_repeat_the_same_status(settings, fake):
    run = submit(settings, fake)
    for _ in range(4):
        runs.refresh(settings, run)
    statuses = [entry["status"] for entry in run.history]
    assert statuses == [runs.STATUS_UPLOADED, runs.STATUS_PROCESSING]


def test_a_same_named_masked_object_from_another_team_is_not_claimed(settings, fake):
    """The broad fallback must not cross team boundaries."""
    run = submit(settings, fake, team="octo")
    fake.add(
        "destination",
        "masked/team=synthify/request=other-run/note.txt",
        "REDACTED",
        "text/plain",
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_PROCESSING
    assert run.masked_key is None


def test_manifests_are_read_from_the_manifest_bucket(fake):
    split = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        source_bucket="landing",
        masked_bucket="outputs",
        manifest_bucket="audit",
    )
    run = runs.submit(
        split,
        "octo",
        "note.txt",
        "text/plain",
        b"Jan Peeters",
        registry=runs.RunRegistry(),
    )
    masked_key = "masked/team=octo/request=cid/note.txt"
    fake.add("outputs", masked_key, "REDACTED", "text/plain")
    fake.add(
        "audit",
        f"manifests/completed/octo/{run.submitted_at:%Y-%m-%d}/cid.json",
        {
            "status": "completed",
            "correlation_id": "cid",
            "source_object_key": run.source_key,
            "masked_object_key": masked_key,
            "model_version": "17",
        },
    )

    runs.refresh(split, run)

    assert ("landing", run.source_key) in fake.objects
    assert run.status == runs.STATUS_COMPLETED
    assert run.masked_bucket == "outputs"
    assert run.masked_size == len(b"REDACTED")


def test_the_default_key_layout_matches_the_watched_nifi_prefix(settings, fake):
    """Every key NiFi has processed is flat at incoming/<team>/requests/<file>."""
    run = submit(
        settings, fake, name="referral.pdf", ctype="application/pdf", body=b"%PDF-1.4 x"
    )
    assert run.source_key == f"incoming/octo/requests/{run.run_id}-referral.pdf"
    assert run.source_key.count("/") == 3


def test_the_key_layout_is_configurable(fake):
    """A recursive watch can use a sub-folder instead of a filename prefix."""
    nested = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        incoming_key_template="{prefix}/{team}/requests/{run_id}/{filename}",
    )
    run = runs.submit(
        nested,
        "octo",
        "note.txt",
        "text/plain",
        b"Jan Peeters",
        registry=runs.RunRegistry(),
    )
    assert run.source_key == f"incoming/octo/requests/{run.run_id}/note.txt"
    assert ("source", run.source_key) in fake.objects


def test_a_template_escaping_the_team_prefix_is_refused(fake):
    """The gateway rejects a key outside incoming/<team-id>/, so refuse it here."""
    bad = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        incoming_key_template="uploads/{run_id}/{filename}",
    )
    with pytest.raises(runs.RunError, match="must produce a key under"):
        runs.submit(
            bad,
            "octo",
            "note.txt",
            "text/plain",
            b"Jan Peeters",
            registry=runs.RunRegistry(),
        )
    assert fake.objects == {}


def test_a_template_that_drops_the_filename_is_refused(fake):
    bad = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        incoming_key_template="{prefix}/{team}/requests/{run_id}",
    )
    with pytest.raises(runs.RunError, match="must end with"):
        runs.submit(
            bad,
            "octo",
            "note.txt",
            "text/plain",
            b"Jan Peeters",
            registry=runs.RunRegistry(),
        )


def test_the_production_default_buckets_are_separate():
    """Uploads go to `source`; masked output and manifests to `destination`."""
    from leaderboard_api.config import load_settings

    defaults = load_settings()
    assert defaults.source_bucket == "source"
    assert defaults.masked_bucket == "destination"
    assert defaults.manifest_bucket == "destination"
    assert defaults.results_bucket == "data"


def test_the_production_manifest_layout_is_found(settings, fake):
    """Observed layout: manifests/completed/team=<id>/request=<id>/status.json."""
    run = submit(settings, fake)
    masked_key = "masked/team=octo/request=1645a4e5/note.txt"
    fake.add("destination", masked_key, "REDACTED", "text/plain")
    fake.add(
        "destination",
        "manifests/completed/team=octo/request=1645a4e5/status.json",
        {
            "status": "SUCCEEDED",
            "request_id": "1645a4e5",
            "team_id": "octo",
            "source_key": run.source_key,
            "destination_key": masked_key,
            "model_version": "17",
        },
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_COMPLETED
    assert run.masked_key == masked_key
    assert run.discovered_by == "manifest"


def test_a_manifest_older_than_the_upload_is_not_claimed(settings, fake):
    """A flat source key repeats across runs, so an old manifest must not match."""
    run = submit(settings, fake)
    stale = run.submitted_at - timedelta(hours=2)
    fake.add(
        "destination",
        "manifests/completed/team=octo/request=previous/status.json",
        {
            "status": "SUCCEEDED",
            "request_id": "previous",
            "source_key": run.source_key,
            "destination_key": "masked/team=octo/request=previous/note.txt",
            "model_version": "16",
        },
        when=stale,
    )

    runs.refresh(settings, run)

    assert run.status == runs.STATUS_PROCESSING
    assert run.masked_key is None
