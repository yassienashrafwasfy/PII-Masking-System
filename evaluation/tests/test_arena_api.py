"""HTTP behaviour of the arena API."""

from __future__ import annotations

import builtins

import pytest
from fake_storage import cross_team_ranking, qa_result


@pytest.fixture
def storage(fake, monkeypatch):
    """The in-memory store, plus the environment that makes the app use it.

    Distinct from `fake`: these routes read their settings from the
    environment on each request, so a store alone would be configured out.
    """
    monkeypatch.setenv("ARENA_S3_ENDPOINT_URL", "https://example.invalid")
    monkeypatch.setenv("ARENA_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("ARENA_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("ARENA_RESULTS_BUCKET", "data")
    monkeypatch.setenv("ARENA_SOURCE_BUCKET", "source")
    monkeypatch.setenv("ARENA_MASKED_BUCKET", "destination")
    return fake


def test_health_is_unauthenticated(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_reports_storage_as_unconfigured_by_default(client, monkeypatch):
    monkeypatch.delenv("ARENA_S3_ENDPOINT_URL", raising=False)
    body = client.get("/api/v1/config").json()
    assert body["storage_configured"] is False
    assert body["teams"] == ["octo", "synthify", "berts"]
    assert "application/pdf" in body["allowed_content_types"]


def test_leaderboard_falls_back_to_demo_without_any_source(client, monkeypatch):
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    monkeypatch.delenv("ARENA_S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    body = client.get("/api/v1/leaderboard").json()
    assert body["source"] == "demo"
    assert len(body["models"]) == 5
    assert body["awards"]


def test_leaderboard_serves_results_and_medals_from_storage(
    client, storage, monkeypatch
):
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add(
        "data",
        "results/qa-result-octo.json",
        qa_result(team="octo", score=94.2, recall=0.99, p95=1200.0),
    )
    storage.add(
        "data",
        "results/qa-result-synthify.json",
        qa_result(team="synthify", score=92.0, recall=0.97, p95=400.0),
    )

    body = client.get("/api/v1/leaderboard").json()

    assert body["source"] == "results"
    assert [row["team"] for row in body["models"]] == ["octo", "synthify"]
    top = body["models"][0]
    # Flat fields the table renders, alongside the full metric envelopes.
    assert top["recall"] == pytest.approx(0.99)
    assert top["p95"] == 1200
    assert top["metrics"]["pii_recall"]["available"] is True
    assert top["badge"] == "Overall champion"
    awards = {award["kind"]: award for award in body["awards"]}
    assert awards["latency"]["team"] == "synthify"
    # The published weights and gates travel with the payload.
    assert body["weights"]["pii_recall"] == pytest.approx(0.40)
    assert body["gates"]["critical_entity_recall_min"] == pytest.approx(0.98)


def test_tied_teams_both_get_the_badge(client, storage, monkeypatch):
    """A genuine exact tie names every co-winner, not just the alphabetically first."""
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add(
        "data",
        "results/qa-result-octo.json",
        qa_result(team="octo", critical=1.0),
    )
    storage.add(
        "data",
        "results/qa-result-berts.json",
        qa_result(
            team="berts",
            score=88.0,
            recall=0.90,
            precision=0.90,
            f1=0.90,
            p95=2000.0,
            throughput=10.0,
            critical=1.0,
        ),
    )

    body = client.get("/api/v1/leaderboard").json()
    rows = {row["team"]: row for row in body["models"]}

    # octo wins outright everywhere except critical recall, where the two tie --
    # so "Critical Guard" is the one badge berts earns, and it must not be
    # dropped just because it isn't also the alphabetically-first winner.
    assert rows["octo"]["badge"] == "Overall champion"
    assert rows["berts"]["badge"] == "Critical Guard"


def test_leaderboard_serves_the_cross_team_ranking_document(
    client, storage, monkeypatch
):
    """The document qa-automation actually publishes drives the board."""
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(),
    )

    body = client.get("/api/v1/leaderboard").json()

    assert body["source"] == "ranking"
    assert [row["team"] for row in body["models"]] == ["berts", "synthify", "octo"]
    top = body["models"][0]
    # The metric the QA team asked for, on the flat field the table renders and
    # in its own envelope under QA's own name.
    assert top["recall"] == pytest.approx(0.966245)
    assert top["metrics"]["overlapping_recall"]["value"] == pytest.approx(0.966245)
    assert top["precision"] == pytest.approx(0.984946)
    assert top["f1"] == pytest.approx(0.975058)
    # No JMeter run, so latency is unavailable-with-a-reason rather than zero.
    assert top["p95"] is None
    assert "JMeter" in top["metrics"]["latency_p95_ms"]["reason"]
    assert body["diagnostics"]["results_source"]["kind"] == "cross-team-ranking"
    assert body["diagnostics"]["results_source"]["basis"] == "overlapping"


def test_overall_champion_follows_qa_rank_not_a_second_tie_computation(
    client, storage, monkeypatch
):
    # Same overlapping_recall for both -- a naive max(leaderboard_score)
    # tie rule would call this a dead heat. But scripts/rank_teams.py
    # already resolved it by latency and published berts as rank 1: the
    # "Overall champion" award must follow that, not re-declare a tie.
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    tied_recall = 0.92
    ranking_overlap = [
        {
            "rank": 1,
            "team": "berts",
            "documents_scored": 15,
            "overlapping_f1": 0.95,
            "overlapping_recall": tied_recall,
            "overlapping_precision": 0.98,
            "p95_latency_ms": 300.0,
            "model_size_mb": 120.3,
            "resolved_by": "tiebreak (recall within 0.03, p95 latency decided it)",
        },
        {
            "rank": 2,
            "team": "octo",
            "documents_scored": 15,
            "overlapping_f1": 0.94,
            "overlapping_recall": tied_recall,
            "overlapping_precision": 0.96,
            "p95_latency_ms": 1800.0,
            "model_size_mb": 1058.8,
            "resolved_by": "tiebreak (recall within 0.03, p95 latency decided it)",
        },
    ]
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(ranking_overlap=ranking_overlap),
    )

    body = client.get("/api/v1/leaderboard").json()
    rows = {row["team"]: row for row in body["models"]}
    assert rows["berts"]["score"] == pytest.approx(rows["octo"]["score"])

    awards = {award["kind"]: award for award in body["awards"]}
    overall = awards["overall"]
    assert overall["tied"] is False
    assert overall["team"] == "berts"
    assert [entry["team"] for entry in overall["teams"]] == ["berts"]


def test_ranking_document_wins_over_per_run_qa_documents(client, storage, monkeypatch):
    """Both kinds in one prefix: the cross-team ranking is what gets shown."""
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add("data", "results/qa-result-octo.json", qa_result(team="octo"))
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(),
    )

    body = client.get("/api/v1/leaderboard").json()
    assert body["source"] == "ranking"
    assert len(body["models"]) == 3


def test_config_reports_the_ranking_basis(client):
    body = client.get("/api/v1/config").json()
    assert body["ranking"]["basis"] == "overlapping"
    assert body["ranking"]["mode"] == "release"
    assert body["ranking"]["pinned_key"] is None


def test_run_submission_rejects_an_unknown_team(client, storage):
    response = client.post(
        "/api/v1/runs",
        data={"team": "ghost"},
        files={"file": ("note.txt", b"Jan Peeters", "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported team" in response.json()["detail"]


def test_run_submission_requires_configured_storage(client, monkeypatch):
    monkeypatch.delenv("ARENA_S3_ENDPOINT_URL", raising=False)
    response = client.post(
        "/api/v1/runs",
        data={"team": "octo"},
        files={"file": ("note.txt", b"Jan Peeters", "text/plain")},
    )
    assert response.status_code == 503


def test_full_run_flow_reports_processing_then_serves_both_documents(client, storage):
    created = client.post(
        "/api/v1/runs",
        data={"team": "octo"},
        files={"file": ("note.txt", b"Contact Jan Peeters", "text/plain")},
    )
    assert created.status_code == 200
    run = created.json()
    run_id = run["run_id"]
    assert run["status"] == "uploaded"
    assert run["source"]["key"].startswith("incoming/octo/requests/")

    # Nothing has been produced yet, so the run is still in flight.
    pending = client.get(f"/api/v1/runs/{run_id}").json()
    assert pending["status"] == "processing"
    assert pending["masked"] is None
    assert client.get(f"/api/v1/runs/{run_id}/content/masked").status_code == 409

    # The original is readable immediately -- while the run is live, the
    # before/after view is intact. Once it is only reachable by rebuilding it
    # from storage, it is not; test_arena_library covers that half.
    original = client.get(f"/api/v1/runs/{run_id}/content/original")
    assert original.status_code == 200
    assert original.content == b"Contact Jan Peeters"
    assert original.headers["cache-control"] == "no-store"

    masked_key = "masked/team=octo/request=nifi-cid/note.txt"
    storage.add("destination", masked_key, "Contact [PERSON]", "text/plain")
    storage.add(
        "destination",
        "manifests/team=octo/request=nifi-cid/status.json",
        {
            "status": "completed",
            "correlation_id": "nifi-cid",
            "source_object_key": run["source"]["key"],
            "masked_object_key": masked_key,
            "model_version": "17",
            "completed_at": "2026-08-23T10:00:12.300Z",
        },
    )

    done = client.get(f"/api/v1/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["masked"]["key"] == masked_key
    assert done["correlation_id"] == "nifi-cid"

    masked = client.get(f"/api/v1/runs/{run_id}/content/masked")
    assert masked.status_code == 200
    assert masked.content == b"Contact [PERSON]"
    # A terminal run's masked object never changes and is now refetched on
    # every navigation into the library, so it is cacheable. An in-flight run
    # is not: its masked side appears partway through, and a cached miss would
    # stick. The original, fetched above while the run was still processing,
    # asserts the other half of that rule.
    assert masked.headers["cache-control"] == "private, max-age=60"
    assert masked.headers["x-robots-tag"] == "noindex, nofollow"


def test_unknown_run_and_unknown_side_are_not_found(client, storage):
    assert client.get("/api/v1/runs/does-not-exist").status_code == 404
    created = client.post(
        "/api/v1/runs",
        data={"team": "octo"},
        files={"file": ("note.txt", b"x", "text/plain")},
    ).json()
    response = client.get(f"/api/v1/runs/{created['run_id']}/content/sideways")
    assert response.status_code == 404


def test_recent_runs_are_listed_newest_first(client, storage):
    for index in range(3):
        client.post(
            "/api/v1/runs",
            data={"team": "octo"},
            files={"file": (f"note-{index}.txt", b"x", "text/plain")},
        )
    listed = client.get("/api/v1/runs").json()["runs"]
    assert [run["filename"] for run in listed] == [
        "note-2.txt",
        "note-1.txt",
        "note-0.txt",
    ]


def test_a_missing_mlflow_package_degrades_to_demo_data(client, monkeypatch):
    """MLflow is not installed in the API image, so its absence must not 500."""
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    monkeypatch.delenv("ARENA_S3_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://mlflow.example.invalid")

    real_import = builtins.__import__

    def without_mlflow(name, *args, **kwargs):
        if name == "mlflow":
            raise ImportError("No module named 'mlflow'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_mlflow)

    response = client.get("/api/v1/leaderboard")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "demo"
    assert len(body["models"]) == 5
    assert "mlflow package is not installed" in body["diagnostics"]["error"]


def test_the_api_image_requirements_exclude_the_evaluation_toolchain():
    """The API image must not ship pytest, plotting, or ML libraries."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "leaderboard_api" / "requirements.txt"
    # Only the dependency lines; the comment explains what is excluded and why.
    pinned = "\n".join(
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    for excluded in (
        "mlflow",
        "pytest",
        "matplotlib",
        "scikit-learn",
        "reportlab",
        "pypdf",
    ):
        assert excluded not in pinned, f"{excluded} must not be in the API image"
    for required in ("fastapi", "uvicorn", "boto3", "pyyaml", "python-multipart"):
        assert required in pinned


def test_mock_data_works_with_no_storage_configured_at_all(client, monkeypatch):
    """When switched on, mock works with zero ARENA_S3_* configuration.

    It short-circuits before the storage check, which is what makes it usable
    as a preview on a host with no credentials.
    """
    monkeypatch.delenv("ARENA_S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("ARENA_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ARENA_S3_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "true")

    body = client.get("/api/v1/leaderboard").json()

    assert body["source"] == "mock"
    assert len(body["models"]) == 3
    assert {row["team"] for row in body["models"]} == {"octo", "synthify", "berts"}
    assert any(award["awarded"] for award in body["awards"])
    assert all(len(row["trend"]) >= 3 for row in body["models"])


def test_mock_when_enabled_hides_real_storage_entirely(client, storage, monkeypatch):
    """Switched on, mock wins even when storage is configured and has data.

    This is the trap the flag creates: a deployment that leaves it on shows
    preview data while real published results sit unread in the bucket.
    """
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "true")
    storage.add("data", "results/qa-result-octo.json", qa_result(team="octo"))

    body = client.get("/api/v1/leaderboard").json()

    assert body["source"] == "mock"


def test_real_results_are_served_without_opting_out_of_anything(client, storage):
    """A deployment that sets no mock flag reads the bucket, not preview data."""
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(),
    )

    body = client.get("/api/v1/leaderboard").json()

    assert body["source"] == "ranking"
    assert body["models"][0]["team"] == "berts"


def test_config_reports_the_mock_flag(client, monkeypatch):
    """Mock preview data is opt-in, so a bare deployment reports it off."""
    monkeypatch.delenv("ARENA_MOCK_LEADERBOARD", raising=False)
    body = client.get("/api/v1/config").json()
    assert body["mock_leaderboard"] is False


def test_samples_endpoint_is_empty_without_a_configured_directory(client, monkeypatch):
    monkeypatch.delenv("ARENA_SAMPLE_DIRECTORY", raising=False)
    body = client.get("/api/v1/samples").json()
    assert body["samples"] == []


def test_samples_endpoint_lists_a_configured_directory(client, monkeypatch, tmp_path):
    (tmp_path / "HealthOne NOVA.pdf").write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setenv("ARENA_SAMPLE_DIRECTORY", str(tmp_path))

    body = client.get("/api/v1/samples").json()

    assert body["samples"] == [
        {
            "name": "HealthOne NOVA.pdf",
            "size": len(b"%PDF-1.4 sample"),
            "content_type": "application/pdf",
        }
    ]


def test_config_reports_samples_available(client, monkeypatch, tmp_path):
    monkeypatch.delenv("ARENA_SAMPLE_DIRECTORY", raising=False)
    assert client.get("/api/v1/config").json()["samples_available"] is False

    monkeypatch.setenv("ARENA_SAMPLE_DIRECTORY", str(tmp_path))
    assert client.get("/api/v1/config").json()["samples_available"] is True


def test_run_submission_reads_a_sample_by_name(client, storage, monkeypatch, tmp_path):
    (tmp_path / "HealthOne NOVA.pdf").write_bytes(b"%PDF-1.4 patient Jan Peeters")
    monkeypatch.setenv("ARENA_SAMPLE_DIRECTORY", str(tmp_path))

    response = client.post(
        "/api/v1/runs", data={"team": "octo", "sample": "HealthOne NOVA.pdf"}
    )

    assert response.status_code == 200
    run = response.json()
    # Filenames go through the same sanitizer a browser upload gets.
    assert run["filename"] == "HealthOne_NOVA.pdf"
    assert run["content_type"] == "application/pdf"
    original = client.get(f"/api/v1/runs/{run['run_id']}/content/original")
    assert original.content == b"%PDF-1.4 patient Jan Peeters"


def test_run_submission_rejects_an_unknown_sample_name(
    client, storage, monkeypatch, tmp_path
):
    monkeypatch.setenv("ARENA_SAMPLE_DIRECTORY", str(tmp_path))
    response = client.post(
        "/api/v1/runs", data={"team": "octo", "sample": "does-not-exist.pdf"}
    )
    assert response.status_code == 404


def test_run_submission_rejects_both_file_and_sample_together(
    client, storage, monkeypatch, tmp_path
):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 x")
    monkeypatch.setenv("ARENA_SAMPLE_DIRECTORY", str(tmp_path))
    response = client.post(
        "/api/v1/runs",
        data={"team": "octo", "sample": "a.pdf"},
        files={"file": ("b.pdf", b"%PDF-1.4 y", "application/pdf")},
    )
    assert response.status_code == 400


def test_run_submission_requires_a_file_or_a_sample(client, storage):
    response = client.post("/api/v1/runs", data={"team": "octo"})
    assert response.status_code == 400


# --- the champion the masking console follows ---------------------------


def test_leaderboard_names_the_champion(client, storage, monkeypatch):
    """QA's rank 1, resolved once on the server.

    The masking console routes to whoever this names, so it must agree with the
    podium rather than being worked out a second time in the browser.
    """
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(),
    )

    body = client.get("/api/v1/leaderboard").json()
    assert body["champion"]["team"] == "berts"
    assert body["champion"]["rank"] == 1


def test_the_champion_follows_a_change_of_rank(client, storage, monkeypatch):
    """Nothing about the console is pinned to berts.

    The whole point of resolving this from the board is that a new winner takes
    over the console without an operator editing an env file and redeploying.
    """
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    document = cross_team_ranking()
    for entry in document["ranking_overlap"]:
        entry["rank"] = {"berts": 3, "synthify": 2, "octo": 1}[entry["team"]]
    storage.add("data", "results/leaderboard-aggregate-15docs-release.json", document)

    body = client.get("/api/v1/leaderboard").json()
    assert body["champion"]["team"] == "octo"


def test_an_excluded_team_is_never_the_champion(client, storage, monkeypatch):
    """Excluded means "not comparable on this document set", not "won".

    A board whose only rows are exclusions has no champion at all, and the
    console must say so rather than routing to an arbitrary team.
    """
    monkeypatch.setenv("ARENA_MOCK_LEADERBOARD", "false")
    storage.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(
            ranking_overlap=[],
            ranking_exact=[],
            excluded={"berts": "no playwright report found for llm0-doc0/release"},
        ),
    )

    body = client.get("/api/v1/leaderboard").json()
    assert body["champion"] is None


def test_config_names_the_team_to_mask_with_when_the_board_is_unreadable(client):
    body = client.get("/api/v1/config").json()
    assert body["champion_fallback_team"] == "berts"


def test_every_configured_team_can_be_masked_with(client, storage):
    """The team console offers all three, and the API accepts all three.

    `ARENA_TEAMS` is not decoration: runs.py validates against it, so a picker
    offering a team the API rejects would fail only at submit time.
    """
    for team in ("octo", "synthify", "berts"):
        response = client.post(
            "/api/v1/runs",
            data={"team": team},
            files={"file": (f"{team}.txt", b"Jan Peeters", "text/plain")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["team"] == team
        uploaded = [
            key
            for (bucket, key) in storage.objects
            if bucket == "source" and key.startswith(f"incoming/{team}/requests/")
        ]
        assert uploaded, f"{team} upload did not land under its own prefix"


def _complete_a_run(client, storage, filename="note.txt"):
    """Submit a run and have NiFi finish it, returning its id."""
    run = client.post(
        "/api/v1/runs",
        data={"team": "octo"},
        files={"file": (filename, b"Contact Jan Peeters", "text/plain")},
    ).json()
    masked_key = f"masked/team=octo/request=cid/{filename}"
    storage.add("destination", masked_key, "Contact [PERSON]", "text/plain")
    storage.add(
        "destination",
        "manifests/completed/team=octo/request=cid/status.json",
        {
            "status": "completed",
            "correlation_id": "cid",
            "source_object_key": run["source"]["key"],
            "masked_object_key": masked_key,
            "model_version": "17",
            "completed_at": "2026-08-30T10:00:00Z",
        },
    )
    return run["run_id"]


def test_a_masked_document_survives_an_api_restart(client, storage, registry):
    """The whole point of the library: the registry is process state, the
    manifest is the record. Emptying the registry stands in for a restart."""
    run_id = _complete_a_run(client, storage)
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200

    registry.restart()

    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200
    masked = client.get(f"/api/v1/runs/{run_id}/content/masked")
    assert masked.status_code == 200
    assert masked.content == b"Contact [PERSON]"


def test_the_original_is_not_served_once_a_run_is_only_in_storage(
    client, storage, registry
):
    """There is no authentication, so the unmasked document must not be
    durably addressable -- while a live run keeps its before/after view."""
    run_id = _complete_a_run(client, storage)
    assert client.get(f"/api/v1/runs/{run_id}/content/original").status_code == 200

    registry.restart()

    refused = client.get(f"/api/v1/runs/{run_id}/content/original")
    assert refused.status_code == 403
    # And it stays refused: resolving caches the run back into the registry,
    # so a naive "is it live?" check would start letting it through.
    assert client.get(f"/api/v1/runs/{run_id}/content/masked").status_code == 200
    assert client.get(f"/api/v1/runs/{run_id}/content/original").status_code == 403


def test_the_library_lists_completed_runs(client, storage, registry):
    run_id = _complete_a_run(client, storage)
    registry.restart()

    body = client.get("/api/v1/library").json()
    assert body["total"] == 1
    assert body["entries"][0]["run_id"] == run_id
    assert body["entries"][0]["status"] == "completed"
    assert body["truncated"] is False


def test_the_library_can_be_turned_off(client, storage, monkeypatch):
    monkeypatch.setenv("ARENA_LIBRARY_ENABLED", "false")
    assert client.get("/api/v1/library").status_code == 404
    assert client.get("/api/v1/config").json()["library"]["enabled"] is False


def test_a_document_missing_from_the_bucket_reads_as_gone_not_broken(
    client, storage, monkeypatch
):
    """A lifecycle rule can delete the object while its manifest remains."""
    run_id = _complete_a_run(client, storage)
    # Let the run discover its masked key first -- otherwise it 409s for the
    # ordinary "not produced yet" reason and never reaches the read.
    assert client.get(f"/api/v1/runs/{run_id}").json()["masked"] is not None
    storage.objects.pop(("destination", "masked/team=octo/request=cid/note.txt"))

    response = client.get(f"/api/v1/runs/{run_id}/content/masked")
    assert response.status_code == 410
    assert "no longer in the bucket" in response.json()["detail"]
