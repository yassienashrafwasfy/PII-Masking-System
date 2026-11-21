"""The winner shown in the docs and on the leaderboard page is the one the committed results give."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from public_benchmarks import report, site

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evaluation/public_benchmarks/results"


def _latest() -> tuple[Path, dict]:
    files = sorted(RESULTS.glob("*.json"))
    assert files, "commit the results file the published winner is calculated from"
    return files[-1], json.loads(files[-1].read_text())


@pytest.mark.parametrize("name", ["docs/benchmarks.md", "README.md"])
def test_published_winner_matches_the_committed_results(name: str) -> None:
    latest, results = _latest()
    path = ROOT / name
    expected = report.replace_block(path.read_text(), report.block_for(path, results))
    assert path.read_text() == expected, (
        f"{name} is out of step with {latest.name}: run "
        "`python -m evaluation.public_benchmarks.report --results <file> --update <doc>`"
    )


def test_the_leaderboard_renders_from_the_committed_results() -> None:
    _, results = _latest()
    page = site.render(results)
    assert report.NAMES[report.overall_winner(results["comparison"])] in page
    assert page.count('class="view"') == 1 + len(results["comparison"]["groups"])
