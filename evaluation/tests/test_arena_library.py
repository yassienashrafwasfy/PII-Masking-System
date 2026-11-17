"""The durable file library: runs rebuilt from the manifests NiFi writes.

The behaviour under test is what happens *after* the API process forgets a run.
`runs.RunRegistry` is in-process demo state, so every test here hands the code
a registry that never saw the run and then asserts it is still reachable.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from leaderboard_api import library, runs
from leaderboard_api.config import Settings


@pytest.fixture
def settings(settings):
    """The buckets a run's own documents live in."""
    return replace(
        settings,
        source_bucket="source",
        masked_bucket="destination",
        manifest_bucket="destination",
    )


def upload(settings, fake, team="octo", name="note.txt"):
    """Put a real run through `submit` so its source key is genuine.

    The registry it lands in is thrown away with the call. Every test in this
    module is about a run this process no longer remembers, so the run has to
    be found through storage or not at all.
    """
    return runs.submit(
        settings,
        team,
        name,
        "text/plain",
        b"Jan Peeters",
        registry=runs.RunRegistry(),
    )


def write_manifest(fake, run, *, request_id="r1", vocabulary="schema", masked=True):
    """Write a terminal manifest in one of NiFi's two vocabularies."""
    masked_key = f"masked/team={run.team}/request={request_id}/{run.filename}"
    if masked:
        fake.add("destination", masked_key, "MASKED", "text/plain")
    if vocabulary == "schema":
        document = {
            "status": "completed",
            "correlation_id": request_id,
            "source_object_key": run.source_key,
            "masked_object_key": masked_key,
            "model_version": "17",
            "completed_at": "2026-08-30T10:00:00Z",
        }
    else:
        # The contract prose: different field names, uppercase status.
        document = {
            "status": "SUCCEEDED",
            "request_id": request_id,
            "source_key": run.source_key,
            "destination_key": masked_key,
            "model_version": "17",
            "started_at": "2026-08-30T09:59:00Z",
            "completed_at": "2026-08-30T10:00:00Z",
        }
    fake.add(
        "destination",
        f"manifests/completed/team={run.team}/request={request_id}/status.json",
        json.dumps(document),
        "application/json",
    )
    return masked_key


@pytest.mark.parametrize("vocabulary", ["schema", "prose"])
def test_a_run_is_rebuilt_from_either_manifest_vocabulary(settings, fake, vocabulary):
    run = upload(settings, fake)
    masked_key = write_manifest(fake, run, vocabulary=vocabulary)

    entries = library.build_index(settings).entries
    assert len(entries) == 1
    rebuilt = entries[0]
    # The run id came back out of the source key, which is what keeps every
    # existing URL and endpoint working after a restart.
    assert rebuilt.run_id == run.run_id
    assert rebuilt.team == "octo"
    assert rebuilt.filename == "note.txt"
    assert rebuilt.masked_key == masked_key
    assert rebuilt.status == runs.STATUS_COMPLETED
    assert rebuilt.discovered_by == "library"


def test_rebuilt_timestamps_come_from_the_manifest_not_the_rebuild(settings, fake):
    """`runs._apply_manifest` stamps discovery time; a rebuild must not.

    Otherwise every entry in the library reads as having completed just now,
    and the newest-first ordering the whole view depends on is meaningless.
    """
    run = upload(settings, fake)
    write_manifest(fake, run, vocabulary="prose")

    rebuilt = library.build_index(settings).entries[0]
    assert rebuilt.completed_at.isoformat().startswith("2026-08-30T10:00:00")
    assert rebuilt.submitted_at.isoformat().startswith("2026-08-30T09:59:00")


def test_a_failure_manifest_is_rebuilt_as_a_failed_run(settings, fake):
    run = upload(settings, fake)
    fake.add(
        "destination",
        "manifests/failed/team=octo/request=r9/status.json",
        json.dumps(
            {
                "status": "failed",
                "correlation_id": "r9",
                "source_object_key": run.source_key,
                "error_code": "MODEL_TIMEOUT",
                "message": "gateway did not respond",
                "failed_at": "2026-08-30T10:00:00Z",
            }
        ),
        "application/json",
    )

    rebuilt = library.build_index(settings).entries[0]
    assert rebuilt.status == runs.STATUS_FAILED
    assert rebuilt.error_code == "MODEL_TIMEOUT"


def test_a_manifest_still_in_flight_is_left_to_the_live_registry(settings, fake):
    run = upload(settings, fake)
    fake.add(
        "destination",
        "manifests/team=octo/request=r2/status.json",
        json.dumps({"status": "processing", "source_object_key": run.source_key}),
        "application/json",
    )
    assert library.build_index(settings).entries == []


def test_a_document_the_arena_never_uploaded_is_skipped(settings, fake):
    """Only runs whose source key matches the arena's own template."""
    fake.add(
        "destination",
        "manifests/completed/team=octo/request=r3/status.json",
        json.dumps(
            {
                "status": "completed",
                "source_object_key": "incoming/octo/requests/nifi-batch.pdf",
                "masked_object_key": "masked/team=octo/request=r3/nifi-batch.pdf",
            }
        ),
        "application/json",
    )
    assert library.build_index(settings).entries == []


def test_the_run_id_is_read_through_an_overridden_key_template(fake):
    """The template is configurable, so the parser must be derived from it."""
    settings = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        manifest_bucket="destination",
        masked_bucket="destination",
        incoming_key_template="{prefix}/{team}/requests/{date}/{run_id}-{filename}",
    )
    run = upload(settings, fake)
    assert "/requests/20" in run.source_key  # the date segment really is there
    write_manifest(fake, run)

    assert library.build_index(settings).entries[0].run_id == run.run_id


def test_entries_are_newest_first_and_capped(settings, fake):
    settings = replace(settings, library_max_entries=2)
    for index in range(4):
        run = upload(settings, fake, name=f"note{index}.txt")
        write_manifest(fake, run, request_id=f"r{index}")

    entries = library.build_index(settings).entries
    assert len(entries) == 2
    # FakeS3's clock advances per write, so the last two uploaded win.
    assert [entry.filename for entry in entries] == ["note3.txt", "note2.txt"]


def test_the_same_run_masked_twice_appears_once(settings, fake):
    run = upload(settings, fake)
    write_manifest(fake, run, request_id="first")
    write_manifest(fake, run, request_id="second")

    entries = library.build_index(settings).entries
    assert [entry.run_id for entry in entries] == [run.run_id]
    # The newer manifest is the one kept.
    assert "second" in entries[0].manifest_key


def test_a_second_read_inside_the_ttl_does_not_touch_storage(settings, fake):
    """The TTL cache is what keeps a bucket listing off every page load."""
    run = upload(settings, fake)
    write_manifest(fake, run)

    library.load_index(settings)
    before = fake.list_calls
    library.load_index(settings)
    assert fake.list_calls == before


def test_an_unchanged_manifest_is_read_once_ever(settings, fake):
    """The (key, etag) memo outlives the TTL, so a rebuild pays only for new
    manifests. Without it every refresh re-reads every manifest body."""
    run = upload(settings, fake)
    write_manifest(fake, run)

    library.load_index(settings)
    after_first = fake.get_calls
    # Expire the TTL without clearing the document memo.
    library._index_cache.clear()
    library.load_index(settings)
    assert fake.get_calls == after_first


def test_resolve_finds_a_run_the_process_never_created(settings, fake):
    """The restart case: an empty registry must still answer for the run."""
    run = upload(settings, fake)
    write_manifest(fake, run)
    empty = runs.RunRegistry()

    resolved = library.resolve(settings, run.run_id, registry=empty)
    assert resolved is not None
    assert resolved.run_id == run.run_id
    # Sizes are skipped when listing and filled in only for the run opened.
    assert resolved.masked_size == len(b"MASKED")
    # And it is cached back into the registry so later polls take the hot path.
    assert empty.get(run.run_id) is not None


def test_resolve_returns_none_for_an_unknown_id_without_scanning(settings, fake):
    """An unknown id is caller-controlled; it must not cost a bucket listing."""
    run = upload(settings, fake)
    write_manifest(fake, run)
    library.load_index(settings)

    before = fake.list_calls
    assert (
        library.resolve(settings, "does-not-exist", registry=runs.RunRegistry()) is None
    )
    assert fake.list_calls == before
