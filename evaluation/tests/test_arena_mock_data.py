"""The temporary mock leaderboard generator.

Guards two things: the fabricated data is shaped correctly for the real
ranking/medal pipeline (results.assemble_board), and it actually varies between
calls, since that variation is the point of "randomized" mock data.
"""

from __future__ import annotations

from leaderboard_api import mock_data


def test_every_configured_team_appears_exactly_once():
    board = mock_data.load_mock_results()
    teams = [row["team"] for row in board["models"]]
    assert sorted(teams) == sorted(team for team, _ in mock_data.MOCK_TEAMS)
    assert len(teams) == len(set(teams))


def test_ranks_are_contiguous_starting_at_one():
    board = mock_data.load_mock_results()
    ranks = sorted(row["rank"] for row in board["models"])
    assert ranks == list(range(1, len(board["models"]) + 1))


def test_multiple_historical_runs_produce_a_real_trend():
    """The point of the mock data is several old runs per team, not one point."""
    board = mock_data.load_mock_results()
    for row in board["models"]:
        assert row["run_count"] >= 3
        assert len(row["trend"]) == row["run_count"]


def test_medals_are_awarded_from_the_generated_rows():
    board = mock_data.load_mock_results()
    assert board["awards"]
    assert any(award["awarded"] for award in board["awards"])
    for award in board["awards"]:
        if award["awarded"]:
            assert award["team"] in {team for team, _ in mock_data.MOCK_TEAMS}


def test_metrics_carry_full_available_envelopes():
    board = mock_data.load_mock_results()
    row = board["models"][0]
    for name in ("pii_recall", "pii_precision", "pii_f1", "latency_p95_ms"):
        metric = row["metrics"][name]
        assert metric["available"] is True
        assert isinstance(metric["value"], float)


def test_repeated_calls_are_randomized():
    """Two boards in a row should not be bit-for-bit identical."""
    boards = [mock_data.load_mock_results() for _ in range(5)]
    scores = [tuple(row["score"] for row in board["models"]) for board in boards]
    assert len(set(scores)) > 1, "mock scores never varied across 5 calls"


def test_a_clean_run_is_usually_eligible():
    """Regression guard: eligibility gates require critical_entity_recall >=
    0.98 and document_success_rate >= 1.0 exactly (config/thresholds.yaml).
    Coupling those to the same wide "quality" band used for recall/precision
    made every mock run ineligible, since that band never reached 0.98."""
    boards = [mock_data.load_mock_results() for _ in range(30)]
    boards_with_an_eligible_row = sum(
        1 for board in boards if any(row["eligible"] for row in board["models"])
    )
    assert (
        boards_with_an_eligible_row >= 25
    ), f"only {boards_with_an_eligible_row}/30 boards had any eligible row"
