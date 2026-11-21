"""The benchmark winner is calculated; these tests keep it honest and keep the docs in step."""

from __future__ import annotations

import pytest

from public_benchmarks import compare, report, site

DOCS = 30


def _team(
    per_group: dict[str, tuple[int, int, int]],
) -> dict[str, dict[str, tuple[int, int, int]]]:
    return {
        g: {f"d{i}": triple for i in range(DOCS)} for g, triple in per_group.items()
    }


def _alternating(even: tuple[int, int, int], odd: tuple[int, int, int], flip: bool):
    pair = (odd, even) if flip else (even, odd)
    return {f"d{i}": pair[i % 2] for i in range(DOCS)}


def _data() -> dict:
    good, poor = (9, 1, 1), (5, 5, 5)
    return {
        "mdeberta": _team({"meddocan-es": good, "meddeid-nl": good}),
        # Octo is perfect on Spanish, which it is not built for, and poor on Dutch.
        "octo": _team({"meddocan-es": (10, 0, 0), "meddeid-nl": poor}),
        "synthify": _team({"meddocan-es": poor, "meddeid-nl": poor}),
        "berts": _team({"meddocan-es": poor, "meddeid-nl": (9, 1, 1)}),
        "mdeberta-model-only": _team(
            {"meddocan-es": (10, 0, 0), "meddeid-nl": (10, 0, 0)}
        ),
    }


def test_overall_winner_is_the_best_mean_f1_over_every_benchmark() -> None:
    result = compare.compare(_data(), rounds=200)
    assert report.overall_winner(result) == "mdeberta"
    assert result["overall"]["mdeberta"]["first_share"] == 1.0


def test_the_ablation_is_never_a_candidate_even_when_it_scores_best() -> None:
    result = compare.compare(_data(), rounds=200)
    ablation = result["overall"]["mdeberta-model-only"]
    assert ablation["mean_f1"] > result["overall"]["mdeberta"]["mean_f1"]
    assert ablation["first_share"] is None
    assert report.overall_winner(result) == "mdeberta"


def test_a_team_outside_its_languages_cannot_win_a_benchmark() -> None:
    result = compare.compare(_data(), rounds=200)
    assert result["groups"]["meddocan-es"]["octo"]["first_share"] is None
    assert (
        result["groups"]["meddocan-es"]["octo"]["f1"] == 1.0
    )  # best score, not eligible
    assert report.group_winner(result, "meddocan-es") == "mdeberta"
    # Berts and mDeBERTa tie on Dutch; the earlier candidate wins, deterministically.
    assert report.group_winner(result, "meddeid-nl") == "berts"


def test_equal_teams_on_swapped_documents_are_a_narrow_win_not_a_clear_one() -> None:
    # Same totals, but each team is strong on the documents the other is weak on.
    data = {
        "octo": {"meddeid-nl": _alternating((9, 1, 1), (5, 5, 5), flip=False)},
        "synthify": {"meddeid-nl": _alternating((9, 1, 1), (5, 5, 5), flip=True)},
    }
    cells = compare.compare(data, rounds=400)["groups"]["meddeid-nl"]
    assert cells["octo"]["f1"] == pytest.approx(cells["synthify"]["f1"])
    shares = (cells["octo"]["first_share"], cells["synthify"]["first_share"])
    assert sum(shares) == pytest.approx(1.0)
    assert all(0.2 < share < 0.8 for share in shares)  # nobody reaches CLEAR_SHARE


def test_teams_scored_on_different_documents_are_refused() -> None:
    data = _data()
    data["octo"]["meddeid-nl"] = {"other": (1, 1, 1)}
    with pytest.raises(ValueError, match="same documents"):
        compare.compare(data, rounds=10)


def test_the_summary_names_the_winner_and_marks_the_rule() -> None:
    results = {"comparison": compare.compare(_data(), rounds=200)}
    text = report.summary(results)
    assert text.startswith("## Winner: mDeBERTa")
    assert "clear" in text and "computed by" in text.lower()
    assert report.one_line(results).startswith(
        "**Best overall on the public benchmarks: mDeBERTa**"
    )


def test_replace_block_needs_exactly_one_marker_pair() -> None:
    doc = f"a\n{report.START}\nold\n{report.END}\nz"
    assert "new" in report.replace_block(
        doc, "new"
    ) and "old" not in report.replace_block(doc, "new")
    with pytest.raises(ValueError):
        report.replace_block("no markers", "x")


def _results() -> dict:
    """The smallest results file `site.render` accepts, built from the invented data."""
    data = _data()
    comparison = compare.compare(data, rounds=100)
    configs: dict[str, dict] = {}
    for config, groups in data.items():
        configs[config] = {"groups": {}}
        for group, docs in groups.items():
            tp, fp, fn = (sum(d[i] for d in docs.values()) for i in range(3))
            recall, f1 = (
                comparison["groups"][group][config]["recall"],
                comparison["groups"][group][config]["f1"],
            )
            configs[config]["groups"][group] = {
                "documents": len(docs),
                "gold_spans": tp + fn,
                "merged": {"f1": f1, "recall": recall, "precision": tp / (tp + fp)},
                "char_recall": recall,
                "typed": {"f1": f1},
                "seconds_per_document": 1.5,
                "ci95": {
                    "merged_f1": [f1 - 0.01, f1 + 0.01],
                    "merged_recall": [recall - 0.01, recall + 0.01],
                },
            }
    return {
        "comparison": comparison,
        "configs": configs,
        "provenance": {"git_commit": "abc1234def"},
    }


def test_the_page_is_one_self_contained_file_with_every_view() -> None:
    page = site.render(_results())
    assert "Winner" in page and "mDeBERTa" in page
    assert page.count('class="view"') == 3  # overall and the two invented benchmarks
    assert "<script src" not in page and "@import" not in page and "<link " not in page
    assert "http://" not in page and page.count("https://") == page.count(site.REPO)


def test_only_teams_built_for_the_language_are_ranked_on_a_benchmark() -> None:
    page = site.render(_results())
    spanish = page[
        page.index('<section class="view" data-view="meddocan-es"') : page.index(
            '<section class="view" data-view="meddeid-nl"'
        )
    ]
    assert (
        'Octo <abbr title="Outside' in spanish
    )  # perfect score, marked and not ranked
    assert 'Berts <abbr title="Outside' in spanish  # Berts is built for nl, en, fr
    assert spanish.count('data-ranked="1"') == 2  # Synthify and mDeBERTa
    assert "ablation, not ranked" in spanish


def test_the_page_names_no_winner_the_comparison_did_not() -> None:
    results = _results()
    banner = site._banner(results)
    assert (
        f"<h2 id=\"winner-h\">{report.NAMES[report.overall_winner(results['comparison'])]}</h2>"
        in banner
    )
