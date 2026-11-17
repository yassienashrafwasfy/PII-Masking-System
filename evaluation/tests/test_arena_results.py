"""Ranking, eligibility, and medal derivation over QA result documents."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fake_storage import qa_result

from leaderboard_api import results


@pytest.fixture
def settings(settings):
    """The prefix qa-automation publishes result documents to."""
    return replace(settings, results_bucket="data", results_prefix="results")


def test_parse_document_maps_envelopes_and_flags_unavailable_metrics():
    row = results.parse_document(qa_result(), "results/qa-result-octo.json", None)

    assert row["team"] == "octo"
    assert row["model"] == "octo-pii-masker v17"
    assert row["metrics"]["pii_recall"]["value"] == pytest.approx(0.9873)
    # A permanently unavailable metric keeps its explanation rather than becoming 0.
    entity_precision = row["metrics"]["entity_precision"]
    assert entity_precision["available"] is False
    assert entity_precision["value"] is None
    assert "entity spans" in entity_precision["reason"]
    assert row["per_entity_recall"]["PHONE_NUMBER"]["value"] == pytest.approx(0.9929)


def test_document_without_team_is_rejected():
    document = qa_result()
    document["evaluation"]["team_id"] = ""
    assert results.parse_document(document, "results/broken.json", None) is None


def test_eligibility_gates_are_enforced():
    eligible = results.parse_document(qa_result(), "k", None)
    assert eligible["eligible"] is True
    assert eligible["ineligible_reasons"] == []

    # critical_entity_recall_min is 0.98 in config/thresholds.yaml
    below = results.parse_document(qa_result(critical=0.91), "k", None)
    assert below["eligible"] is False
    assert any(
        "critical_entity_recall" in reason for reason in below["ineligible_reasons"]
    )

    over_errors = results.parse_document(qa_result(api_error=0.5), "k", None)
    assert over_errors["eligible"] is False


def test_eligibility_fails_closed_when_a_gate_metric_is_unavailable():
    document = qa_result()
    document["metrics"]["document_success_rate"] = {
        "available": False,
        "value": None,
        "reason": "dataset produced no documents",
    }
    row = results.parse_document(document, "k", None)
    assert row["eligible"] is False
    assert any(
        "document_success_rate" in reason for reason in row["ineligible_reasons"]
    )


def test_missing_critical_types_do_not_make_a_run_ineligible():
    document = qa_result()
    document["metrics"]["critical_entity_recall"] = {
        "available": False,
        "value": None,
        "reason": "dataset has no critical entity types",
    }
    row = results.parse_document(document, "k", None)
    assert row["eligible"] is True


def test_awards_pick_the_best_model_per_category(settings, fake):
    fake.add(
        "data",
        "results/qa-result-octo.json",
        qa_result(team="octo", score=94.2, recall=0.99, precision=0.95, p95=1200.0),
    )
    fake.add(
        "data",
        "results/qa-result-synthify.json",
        qa_result(team="synthify", score=92.0, recall=0.97, precision=0.98, p95=400.0),
    )
    fake.add(
        "data",
        "results/qa-result-berts.json",
        qa_result(team="berts", score=88.0, recall=0.95, precision=0.93, p95=800.0),
    )

    loaded = results.load_results(settings)
    awards = {award["kind"]: award for award in loaded["awards"]}

    assert awards["overall"]["team"] == "octo"
    assert awards["recall"]["team"] == "octo"
    assert awards["precision"]["team"] == "synthify"
    # Latency is a lower-is-better medal.
    assert awards["latency"]["team"] == "synthify"
    assert awards["latency"]["value"] == pytest.approx(400.0)


def test_award_names_every_team_tied_on_the_metric(settings, fake):
    fake.add(
        "data",
        "results/qa-result-octo.json",
        qa_result(team="octo", critical=1.0),
    )
    fake.add(
        "data",
        "results/qa-result-berts.json",
        qa_result(team="berts", critical=1.0),
    )
    fake.add(
        "data",
        "results/qa-result-synthify.json",
        qa_result(team="synthify", critical=0.95),
    )

    awards = {
        award["kind"]: award for award in results.load_results(settings)["awards"]
    }
    critical = awards["critical"]

    assert critical["tied"] is True
    # `team`/`model` keep carrying the first tied team, alphabetically, for any
    # reader that only handles one winner.
    assert critical["team"] == "berts"
    assert [entry["team"] for entry in critical["teams"]] == ["berts", "octo"]
    assert all(entry["value"] == pytest.approx(1.0) for entry in critical["teams"])


def test_tied_award_orders_co_winners_by_latency_not_alphabetically(settings, fake):
    # octo sorts before berts alphabetically, but berts has the lower p95 --
    # the tied "recall" award's single-winner field must name berts, the
    # team that would actually win the fallback tiebreak, not whichever
    # team name happens to come first in the alphabet.
    fake.add(
        "data",
        "results/qa-result-octo.json",
        qa_result(team="octo", recall=0.99, p95=1800.0),
    )
    fake.add(
        "data",
        "results/qa-result-berts.json",
        qa_result(team="berts", recall=0.99, p95=300.0),
    )

    awards = {
        award["kind"]: award for award in results.load_results(settings)["awards"]
    }
    recall_award = awards["recall"]

    assert recall_award["tied"] is True
    assert recall_award["team"] == "berts"
    assert [entry["team"] for entry in recall_award["teams"]] == ["berts", "octo"]


def test_award_is_not_tied_when_only_one_team_has_the_best_value(settings, fake):
    fake.add(
        "data", "results/qa-result-octo.json", qa_result(team="octo", critical=1.0)
    )
    fake.add(
        "data",
        "results/qa-result-synthify.json",
        qa_result(team="synthify", critical=0.95),
    )

    awards = {
        award["kind"]: award for award in results.load_results(settings)["awards"]
    }
    critical = awards["critical"]

    assert critical["tied"] is False
    assert critical["team"] == "octo"
    assert [entry["team"] for entry in critical["teams"]] == ["octo"]


def test_award_reports_itself_unawarded_when_no_run_has_the_metric(settings, fake):
    document = qa_result()
    document["metrics"]["throughput_docs_min"] = {
        "available": False,
        "value": None,
        "reason": "elapsed time unavailable",
    }
    fake.add("data", "results/qa-result-octo.json", document)

    awards = {a["kind"]: a for a in results.load_results(settings)["awards"]}
    assert awards["throughput"]["awarded"] is False
    assert "no eligible run" in awards["throughput"]["reason"]


def test_only_the_newest_document_per_team_is_ranked(settings, fake):
    fake.add(
        "data",
        "results/octo/run-1.json",
        qa_result(team="octo", score=70.0, version="16"),
    )
    fake.add(
        "data",
        "results/octo/run-2.json",
        qa_result(team="octo", score=95.0, version="17"),
    )

    loaded = results.load_results(settings)
    assert len(loaded["models"]) == 1
    row = loaded["models"][0]
    assert row["model_version"] == "17"
    assert row["score"] == pytest.approx(95.0)
    # Superseded runs still feed the trend line.
    assert row["trend"] == [70.0, 95.0]
    assert row["run_count"] == 2


def test_eligible_models_outrank_ineligible_ones_regardless_of_score(settings, fake):
    fake.add(
        "data", "results/a.json", qa_result(team="octo", score=99.0, critical=0.10)
    )
    fake.add("data", "results/b.json", qa_result(team="synthify", score=80.0))

    models = results.load_results(settings)["models"]
    assert [row["team"] for row in models] == ["synthify", "octo"]
    assert models[0]["rank"] == 1


def test_unreadable_documents_are_skipped_not_fatal(settings, fake):
    fake.add("data", "results/broken.json", b"{not json")
    fake.add("data", "results/qa-result-octo.json", qa_result())
    fake.add("data", "results/notes.txt", "ignored")

    loaded = results.load_results(settings)
    assert len(loaded["models"]) == 1
    assert any("broken.json" in item["key"] for item in loaded["skipped"])


def test_empty_prefix_yields_an_empty_board(settings, fake):
    loaded = results.load_results(settings)
    assert loaded["models"] == []
    assert all(award["awarded"] is False for award in loaded["awards"])
