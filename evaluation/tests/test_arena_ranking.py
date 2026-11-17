"""Reading qa-automation's cross-team ranking document onto the board.

The document under test is `scripts/rank_teams.py`'s output -- the file the QA
team actually publishes -- not the per-run `qa-result.schema.json` document that
`test_arena_results.py` covers.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fake_storage import cross_team_ranking, qa_result, team_report

from leaderboard_api import history, objectstore, ranking, results
from leaderboard_api.config import Settings, load_settings

AGGREGATE_KEY = "results/leaderboard-aggregate-15docs-release.json"


@pytest.fixture
def settings(settings):
    """The prefix qa-automation publishes ranking documents to."""
    return replace(settings, results_bucket="data", results_prefix="results")


# --- prefix partitioning -------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("results/leaderboard-aggregate-15docs-release.json", True),
        ("results/leaderboard-llm0-gastro-local.json", True),
        # A per-team report is two levels deeper and must never be mistaken
        # for the cross-team ranking.
        ("results/berts/llm0-gastro/pipeline-test-pw-berts-local.json", False),
        ("results/berts/leaderboard-aggregate-15docs-release.json", False),
        ("results/qa-result-octo.json", False),
        ("results/leaderboard-notes.txt", False),
        ("other/leaderboard-aggregate-15docs-release.json", False),
    ],
)
def test_only_flat_leaderboard_keys_are_ranking_documents(key, expected):
    assert results.is_ranking_key(key, "results") is expected


# --- the metric the QA team asked for ------------------------------------


def test_overlapping_recall_is_read_for_every_ranked_team(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(settings)
    recall = {
        row["team"]: row["metrics"]["overlapping_recall"] for row in board["models"]
    }

    assert recall["berts"]["available"] is True
    assert recall["berts"]["value"] == pytest.approx(0.966245)
    assert recall["synthify"]["value"] == pytest.approx(0.938819)
    assert recall["octo"]["value"] == pytest.approx(0.932489)


def test_both_rankings_are_exposed_side_by_side(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    berts = ranking.load_board(settings)["models"][0]
    assert berts["metrics"]["overlapping_f1"]["value"] == pytest.approx(0.975058)
    # The exact ranking's entry for the same team is attached, not discarded:
    # the gap between the two is the boundary-precision story.
    assert berts["metrics"]["exact_f1"]["value"] == pytest.approx(0.8121)
    assert berts["metrics"]["exact_recall"]["value"] == pytest.approx(0.8043)


# --- QA's rank order is authoritative ------------------------------------


def test_qa_rank_order_is_preserved_through_a_tiebreak(settings, fake):
    """synthify's F1 beats octo's by 0.003 -- inside `close_threshold`.

    QA treats that as a tie and awards the place on model size. Any reader that
    sorted by F1 could land on the same order by luck, so the real assertion is
    that the published rank is carried through verbatim.
    """
    document = cross_team_ranking()
    # Invert the F1 order of the two tied teams while keeping QA's ranks, so
    # sorting by F1 would produce a different board than the one published.
    document["ranking_overlap"][1]["overlapping_f1"] = 0.938429
    document["ranking_overlap"][2]["overlapping_f1"] = 0.941799
    fake.add("data", AGGREGATE_KEY, document)

    board = ranking.load_board(settings)
    assert [row["team"] for row in board["models"]] == ["berts", "synthify", "octo"]
    assert [row["rank"] for row in board["models"]] == [1, 2, 3]
    assert "tiebreak" in board["models"][1]["resolved_by"]


def test_score_is_the_ranking_recall_rescaled_not_f1(settings, fake):
    """QA asked for the headline number to be recall, not F1.

    F1 folds in the release-mode precision proxy (diffing masked bytes with no
    true spans to compare against), which runs low enough to bury a strong
    recall number. Unlike F1, this score can disagree with `rank` for a
    tiebroken pair: QA's own tiebreak (close_threshold, then latency, then
    model size) is a rule over F1, not over this number.
    """
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(settings)
    assert board["models"][0]["score"] == pytest.approx(96.62, abs=0.01)


# --- unavailable inputs stay explanatory ---------------------------------


def test_null_latency_carries_the_documents_own_note(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    latency = ranking.load_board(settings)["models"][0]["metrics"]["latency_p95_ms"]
    assert latency["available"] is False
    assert latency["value"] is None
    assert "JMeter" in latency["reason"]


def test_excluded_teams_are_unranked_with_their_reason_not_scored_zero(settings, fake):
    document = cross_team_ranking(
        excluded={"octo": "no playwright report found for llm2-urologie/release"}
    )
    document["ranking_overlap"] = document["ranking_overlap"][:2]
    document["ranking_exact"] = document["ranking_exact"][:2]
    fake.add("data", AGGREGATE_KEY, document)

    board = ranking.load_board(settings)
    octo = next(row for row in board["models"] if row["team"] == "octo")
    assert octo["eligible"] is False
    assert octo["score"] is None
    assert "llm2-urologie" in octo["ineligible_reasons"][0]
    # Excluded teams sort after every ranked one.
    assert [row["team"] for row in board["models"]][:2] == ["berts", "synthify"]


# --- choosing which document to show ------------------------------------


def test_a_newer_local_document_never_displaces_a_release_one(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking(mode="release"))
    fake.add(
        "data",
        "results/leaderboard-aggregate-15docs-local.json",
        cross_team_ranking(mode="local"),
    )

    board = ranking.load_board(settings)
    assert board["source"]["key"] == AGGREGATE_KEY
    assert board["source"]["mode"] == "release"


def test_a_larger_document_set_outranks_a_single_document_run(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    fake.add(
        "data",
        "results/leaderboard-llm0-gastro-release.json",
        cross_team_ranking(document_ids=["llm0-gastro"]),
    )

    assert ranking.load_board(settings)["source"]["key"] == AGGREGATE_KEY


def test_an_exact_key_pin_overrides_the_automatic_choice(fake):
    pinned = "results/leaderboard-aggregate-15docs-local.json"
    settings = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        results_bucket="data",
        results_prefix="results",
        leaderboard_key=pinned,
    )
    fake.add("data", AGGREGATE_KEY, cross_team_ranking(mode="release"))
    fake.add("data", pinned, cross_team_ranking(mode="local"))

    board = ranking.load_board(settings)
    assert board["source"]["key"] == pinned
    assert board["source"]["mode"] == "local"


def test_all_modes_are_considered_when_the_mode_filter_is_empty(fake):
    settings = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        results_bucket="data",
        results_prefix="results",
        leaderboard_mode="",
    )
    fake.add(
        "data",
        "results/leaderboard-aggregate-15docs-release.json",
        cross_team_ranking(mode="release"),
    )
    fake.add(
        "data",
        "results/leaderboard-aggregate-15docs-local.json",
        cross_team_ranking(mode="local"),
    )

    # Same document count, so recency decides -- the local file was written last.
    assert ranking.load_board(settings)["source"]["mode"] == "local"


def test_release_filter_falls_back_rather_than_showing_nothing(settings, fake):
    """A local-only prefix should still render, with the mode named."""
    fake.add(
        "data",
        "results/leaderboard-aggregate-15docs-local.json",
        cross_team_ranking(mode="local"),
    )

    board = ranking.load_board(settings)
    assert board is not None
    assert board["source"]["mode"] == "local"


# --- trend lines --------------------------------------------------------


def test_trend_spans_comparable_documents_only(settings, fake):
    ids = ["a", "b"]
    older = cross_team_ranking(document_ids=ids)
    older["ranking_overlap"][0]["overlapping_recall"] = 0.90
    newer = cross_team_ranking(document_ids=ids)
    # A different document set is not a later point on the same line. Kept
    # smaller than the pair above so selection still lands on a 2-document
    # document and this one can only ever appear via the trend filter.
    unrelated = cross_team_ranking(document_ids=["a"])
    unrelated["ranking_overlap"][0]["overlapping_recall"] = 0.10

    fake.add("data", "results/leaderboard-aggregate-2docs-release.json", older)
    fake.add("data", "results/leaderboard-a-release.json", unrelated)
    fake.add("data", "results/leaderboard-aggregate-2docs-v2-release.json", newer)

    board = ranking.load_board(settings)
    berts = next(row for row in board["models"] if row["team"] == "berts")
    assert berts["trend"] == [90.0, 96.62]
    assert berts["run_count"] == 2


# --- enrichment from the per-team reports -------------------------------


def test_model_identity_comes_from_the_per_team_report(settings, fake):
    document = cross_team_ranking(document_ids=["llm0-gastro"])
    fake.add("data", "results/leaderboard-llm0-gastro-release.json", document)
    fake.add(
        "data",
        "results/berts/llm0-gastro/pipeline-test-pw-berts-release.json",
        team_report(team="berts", document_id="llm0-gastro"),
    )

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["model"] == "berts-pii-masker v4"
    assert berts["model_digest"] == "sha256:9f8e"
    assert berts["evaluation"]["api_contract_version"] == "1.2"
    assert berts["metrics"]["parameter_count_millions"]["value"] == pytest.approx(31.0)
    assert berts["metrics"]["mean_confidence"]["value"] == pytest.approx(0.9312)


def test_per_entity_recall_is_read_when_local_mode_reports_labels(settings, fake):
    document = cross_team_ranking(document_ids=["llm0-gastro"], mode="local")
    fake.add("data", "results/leaderboard-llm0-gastro-local.json", document)
    fake.add(
        "data",
        "results/berts/llm0-gastro/pipeline-test-pw-berts-local.json",
        team_report(
            team="berts",
            document_id="llm0-gastro",
            mode="local",
            per_label={
                "PHONE_NUMBER": {"recall": 0.9929, "precision": 1.0, "f1": 0.9964},
                "INSZ_NISS": {"recall": 1.0, "precision": 1.0, "f1": 1.0},
            },
        ),
    )

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["per_entity_recall"]["PHONE_NUMBER"]["value"] == pytest.approx(0.9929)
    assert berts["per_entity_recall"]["INSZ_NISS"]["value"] == pytest.approx(1.0)


def test_a_missing_per_team_report_leaves_the_board_intact(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(settings)
    assert [row["team"] for row in board["models"]] == ["berts", "synthify", "octo"]
    # Enrichment is decoration; recall still came through.
    assert board["models"][0]["metrics"]["overlapping_recall"]["available"] is True


# --- medals -------------------------------------------------------------


def test_medals_fall_back_to_the_ranking_metric_names(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    awards = {award["kind"]: award for award in ranking.load_board(settings)["awards"]}
    assert awards["recall"]["metric"] == "overlapping_recall"
    assert awards["recall"]["team"] == "berts"
    assert awards["precision"]["metric"] == "overlapping_precision"
    assert awards["precision"]["team"] == "berts"
    # Smallest model is a real medal here: it is QA's final tiebreak.
    assert awards["footprint"]["team"] == "berts"
    assert awards["footprint"]["value"] == pytest.approx(120.3)
    # Nothing in this document reports these, and an unawarded medal still
    # names the metric it wanted.
    assert awards["critical"]["awarded"] is False
    assert awards["critical"]["metric"] == "critical_entity_recall"
    assert awards["latency"]["awarded"] is False


def test_medals_still_prefer_the_qa_result_names_when_present(settings, fake):
    fake.add("data", "results/qa-result-octo.json", qa_result(team="octo"))

    awards = {a["kind"]: a for a in results.load_results(settings)["awards"]}
    assert awards["recall"]["metric"] == "pii_recall"


# --- falling through ----------------------------------------------------


def test_no_ranking_document_returns_none_so_the_other_reader_runs(settings, fake):
    fake.add("data", "results/qa-result-octo.json", qa_result())
    fake.add(
        "data",
        "results/berts/llm0-gastro/pipeline-test-pw-berts-local.json",
        team_report(),
    )

    assert ranking.load_board(settings) is None


def test_an_unreadable_ranking_document_is_skipped_not_fatal(settings, fake):
    fake.add("data", "results/leaderboard-broken-release.json", b"{not json")
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(settings)
    assert board is not None
    assert board["source"]["key"] == AGGREGATE_KEY


def test_a_file_without_a_ranking_array_is_not_treated_as_one(settings, fake):
    fake.add("data", "results/leaderboard-notes-release.json", {"mode": "release"})

    assert ranking.load_board(settings) is None


def test_source_diagnostics_describe_what_was_read(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    source = ranking.load_board(settings)["source"]
    assert source["kind"] == "cross-team-ranking"
    assert source["basis"] == "overlapping"
    assert source["evaluation_source"] == "playwright"
    assert source["close_threshold"] == 0.03
    assert len(source["document_ids"]) == 15
    assert source["rankings_available"] == ["exact", "overlapping"]
    assert source["notes"]


# --- the exact basis ----------------------------------------------------


def test_the_exact_ranking_can_drive_the_board_instead(fake):
    settings = Settings(
        endpoint_url="https://example.invalid",
        access_key_id="key",
        secret_access_key="secret",
        results_bucket="data",
        results_prefix="results",
        ranking_basis="exact",
    )
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(settings)
    assert board["source"]["basis"] == "exact"
    # The score now tracks exact recall, not overlap.
    assert board["models"][0]["score"] == pytest.approx(80.43, abs=0.01)


# --- settings -----------------------------------------------------------


def test_ranking_settings_default_without_any_environment(monkeypatch):
    for name in (
        "ARENA_RANKING_BASIS",
        "ARENA_LEADERBOARD_MODE",
        "ARENA_LEADERBOARD_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()
    assert settings.ranking_basis == "overlapping"
    assert settings.leaderboard_mode == "release"
    assert settings.leaderboard_key == ""


def test_an_empty_mode_means_every_mode_not_the_default(monkeypatch):
    """Compose can only express "all modes" by passing an empty string.

    `_clean` would be wrong here: stripping is fine, but the empty result must
    survive as empty rather than falling back to "release", or the setting
    becomes unreachable from a container.
    """
    monkeypatch.setenv("ARENA_LEADERBOARD_MODE", "")
    assert load_settings().leaderboard_mode == ""

    monkeypatch.setenv("ARENA_LEADERBOARD_MODE", "  local  ")
    assert load_settings().leaderboard_mode == "local"


def test_an_unknown_basis_falls_back_rather_than_emptying_the_board(settings, fake):
    unknown = Settings(**{**settings.__dict__, "ranking_basis": "nonsense"})
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    board = ranking.load_board(unknown)
    assert board["source"]["basis"] == "overlapping"
    assert len(board["models"]) == 3


# --- metrics that live outside the ranking document ----------------------


def _qa_result(team, critical=0.95, p95=1200.0, throughput=26.0, recall=0.99):
    """A run_evaluation.py document, trimmed to what the board reads."""

    def envelope(value, reason=None):
        return {"available": value is not None, "value": value, "reason": reason}

    return {
        "source": {"model_name": f"{team}-pii-masker", "model_version": "7"},
        "evaluation": {"team_id": team},
        "metrics": {
            "critical_entity_recall": envelope(critical),
            "latency_p95_ms": envelope(p95),
            "throughput_docs_min": envelope(throughput),
            "pii_recall": envelope(recall),
            "pii_f1": envelope(0.99),
            "leakage_rate": envelope(0.002),
        },
    }


def test_the_four_unawarded_medals_resolve_from_the_evaluation_documents(
    settings, fake
):
    """These metrics never appear in the ranking document itself.

    critical_entity_recall, latency and throughput come from
    run_evaluation.py; model size comes from the model service's own
    /version, which is the only place that can see its weights. Without them
    the board showed four medals as "no eligible run reported this metric".
    """
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    for team, critical, p95, throughput, size in (
        ("berts", 1.0, 1000.0, 30.0, 120.3),
        ("octo", 0.80, 1400.0, 22.0, 1058.8),
        ("synthify", 0.92, 1900.0, 18.0, 745.8),
    ):
        fake.add(
            "data",
            f"results/{team}/qa-result.json",
            _qa_result(team, critical, p95, throughput),
        )
        report = team_report(team=team, document_id="llm0-doc0")
        report["candidate_version"]["model_file_size_mb"] = size
        fake.add(
            "data",
            f"results/{team}/llm0-doc0/pipeline-test-pw-{team}-release.json",
            report,
        )

    awards = {a["kind"]: a for a in ranking.load_board(settings)["awards"]}
    assert awards["critical"]["awarded"] is True
    assert awards["critical"]["team"] == "berts"
    assert awards["latency"]["awarded"] is True
    assert awards["latency"]["team"] == "berts"  # lowest p95 wins
    assert awards["throughput"]["awarded"] is True
    assert awards["throughput"]["team"] == "berts"
    assert awards["footprint"]["awarded"] is True
    assert awards["footprint"]["team"] == "berts"  # smallest model wins
    assert awards["footprint"]["value"] == pytest.approx(120.3)


def test_evaluation_metrics_never_overwrite_the_ranking_s_own(settings, fake):
    """The published rank must stay explainable by the document that set it."""
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    conflicting = _qa_result("berts")
    conflicting["metrics"]["overlapping_recall"] = {
        "available": True,
        "value": 0.1,
        "reason": None,
    }
    fake.add("data", "results/berts/qa-result.json", conflicting)

    berts = next(
        r for r in ranking.load_board(settings)["models"] if r["team"] == "berts"
    )
    assert berts["metrics"]["overlapping_recall"]["value"] == pytest.approx(0.966245)
    assert berts["metrics"]["pii_recall"]["value"] == pytest.approx(0.99)


def test_a_missing_evaluation_document_leaves_the_row_untouched(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    board = ranking.load_board(settings)
    assert [r["team"] for r in board["models"]] == ["berts", "synthify", "octo"]
    assert "critical_entity_recall" not in board["models"][0]["metrics"]


def test_the_ranking_s_own_model_size_is_preferred_over_version(settings, fake):
    """A ranking that already carries a size is authoritative for it."""
    document = cross_team_ranking()
    document["ranking_overlap"][0]["model_size_mb"] = 111.1
    fake.add("data", AGGREGATE_KEY, document)
    report = team_report(team="berts", document_id="llm0-doc0")
    report["candidate_version"]["model_file_size_mb"] = 999.9
    fake.add(
        "data", "results/berts/llm0-doc0/pipeline-test-pw-berts-release.json", report
    )

    berts = next(
        r for r in ranking.load_board(settings)["models"] if r["team"] == "berts"
    )
    assert berts["metrics"]["model_size_mb"]["value"] == pytest.approx(111.1)


# --- the archive that makes run history possible ------------------------


@pytest.fixture
def archiving(settings):
    """Settings with the arena's own ranking archive switched on."""
    return replace(settings, archive_rankings=True)


def _archived(document, key, observed_at="2026-08-01T09:00:00+00:00"):
    """One document as history.archive would have stored it."""
    stored = dict(document)
    stored[history.PROVENANCE_FIELD] = {"key": key, "observed_at": observed_at}
    return stored


def test_archived_documents_give_a_team_more_than_one_tracked_run(settings, fake):
    """The whole point: QA's key is overwritten, the archive is not.

    Without an archive the prefix holds exactly one document per document set,
    every trend line is a single point, and the board's "Iron model" card reads
    "1 run tracked" no matter how often QA has run.
    """
    older = cross_team_ranking()
    older["ranking_overlap"][0]["overlapping_recall"] = 0.90
    fake.add(
        "data",
        "results/history/release-15docs-aaaaaaaaaaaa.json",
        _archived(older, AGGREGATE_KEY),
    )
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["trend"] == [90.0, 96.62]
    assert berts["run_count"] == 2


def test_an_archived_copy_is_not_counted_beside_its_original(settings, fake):
    """One run must land on the trend line once, however many keys hold it.

    An archived copy is byte-identical to the document it was taken from, and
    qa-automation publishes a stable "latest" key alongside its timestamped one,
    so a reader that trusted keys would count a single run two or three times.
    """
    document = cross_team_ranking()
    fake.add("data", AGGREGATE_KEY, document)
    fake.add(
        "data",
        "results/history/release-15docs-aaaaaaaaaaaa.json",
        _archived(document, AGGREGATE_KEY),
    )
    fake.add(
        "data", "results/leaderboard-aggregate-15docs-release-20260801.json", document
    )

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["run_count"] == 1
    assert berts["trend"] == [96.62]


def test_an_archived_copy_is_dated_when_qa_published_it(settings, fake):
    """Not when this service happened to copy it.

    The archived object's own mtime is an artefact of whoever loaded the page
    first; ordering trends by it would shuffle history by traffic pattern.
    """
    older = cross_team_ranking()
    older["ranking_overlap"][0]["overlapping_recall"] = 0.90
    # Written to storage last, but recorded as the earlier observation.
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    fake.add(
        "data",
        "results/history/release-15docs-aaaaaaaaaaaa.json",
        _archived(older, AGGREGATE_KEY, "2020-01-01T00:00:00+00:00"),
    )

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["trend"] == [90.0, 96.62]


def test_document_ids_in_a_different_order_stay_comparable(settings, fake):
    """`--document-id` is an ordered argument list, not a set.

    An operator listing the same documents in another order describes the same
    benchmark, so treating the orders as different datasets would break every
    trend line in two for no real reason.
    """
    older = cross_team_ranking(document_ids=["a", "b", "c"])
    older["ranking_overlap"][0]["overlapping_recall"] = 0.90
    newer = cross_team_ranking(document_ids=["c", "a", "b"])

    fake.add("data", "results/leaderboard-aggregate-3docs-release.json", older)
    fake.add("data", "results/leaderboard-aggregate-3docs-v2-release.json", newer)

    berts = next(
        row for row in ranking.load_board(settings)["models"] if row["team"] == "berts"
    )
    assert berts["run_count"] == 2


def test_the_selected_document_is_archived_under_a_content_key(archiving, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    ranking.load_board(archiving)

    archived = [
        key
        for (bucket, key) in fake.objects
        if bucket == "data" and key.startswith("results/history/")
    ]
    assert len(archived) == 1
    assert archived[0].startswith("results/history/release-15docs-")
    stored = json.loads(fake.objects[("data", archived[0])]["body"])
    assert stored[history.PROVENANCE_FIELD]["key"] == AGGREGATE_KEY


def test_archiving_the_same_document_twice_writes_once(archiving, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    ranking.load_board(archiving)
    ranking.load_board(archiving)

    archived = [
        key
        for (bucket, key) in fake.objects
        if bucket == "data" and key.startswith("results/history/")
    ]
    assert len(archived) == 1


def test_archiving_is_off_unless_asked_for(settings, fake):
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())
    ranking.load_board(settings)
    assert not [
        key
        for (bucket, key) in fake.objects
        if bucket == "data" and key.startswith("results/history/")
    ]


def test_a_read_only_results_bucket_still_serves_the_board(
    archiving, fake, monkeypatch
):
    """The archive is the one place the arena writes to the results bucket.

    A credential with read but not write is a perfectly ordinary deployment, and
    it must degrade to the previous behaviour rather than break the leaderboard.
    """
    fake.add("data", AGGREGATE_KEY, cross_team_ranking())

    def refuse(*_args, **_kwargs):
        raise PermissionError("AccessDenied")

    monkeypatch.setattr(objectstore, "put_bytes", refuse)

    board = ranking.load_board(archiving)
    assert [row["team"] for row in board["models"]] == ["berts", "synthify", "octo"]


def test_history_objects_are_never_mistaken_for_per_team_reports(settings, fake):
    """The flat-key rule still partitions the prefix.

    `is_ranking_key` excludes everything nested so a per-team report cannot be
    read as a board; the history prefix is admitted by its own predicate, not by
    weakening that one.
    """
    assert not results.is_ranking_key(
        "results/history/release-15docs-aaaaaaaaaaaa.json", "results"
    )
    assert ranking.is_history_key(
        "results/history/release-15docs-aaaaaaaaaaaa.json", "results/history"
    )
    assert not ranking.is_history_key(
        "results/berts/llm0-gastro/pipeline-test-pw-berts-local.json", "results/history"
    )
