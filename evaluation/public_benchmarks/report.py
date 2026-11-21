"""Render the benchmark winner from a results file, and keep the docs in step with it.

    uv run python -m evaluation.public_benchmarks.report \
        --results evaluation/public_benchmarks/results/<run>.json \
        --update docs/benchmarks.md --update README.md

Everything shown comes from the ``comparison`` section that `score.py` calculated; the
rule is in `compare.py`. ``--update`` rewrites the text between the winner markers in a
file, and `evaluation/tests/test_benchmark_report.py` fails when a file is out of step.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .compare import CANDIDATES, CLEAR_SHARE, DECLARED_LANGUAGES, in_scope

START, END = "<!-- benchmark-winner:start -->", "<!-- benchmark-winner:end -->"
NAMES = {
    "octo": "Octo",
    "synthify": "Synthify",
    "berts": "Berts",
    "mdeberta": "mDeBERTa",
    "mdeberta-model-only": "mDeBERTa (checkpoint only)",
}
BENCHMARKS = {
    "meddocan-es": "MEDDOCAN (Spanish)",
    "multigrascco-de": "MultiGraSCCo (German)",
    "multigrascco-en": "MultiGraSCCo (English)",
    "multigrascco-fr": "MultiGraSCCo (French)",
    "meddeid-nl": "MedDeID (Dutch)",
}


def _teams(comparison: dict[str, Any]) -> list[str]:
    return [t for t in CANDIDATES if t in comparison["overall"]]


def overall_winner(comparison: dict[str, Any]) -> str:
    overall = comparison["overall"]
    return max(
        _teams(comparison), key=lambda t: (overall[t]["mean_f1"], -CANDIDATES.index(t))
    )


def group_winner(comparison: dict[str, Any], group: str) -> str:
    cells = comparison["groups"][group]
    scope = [t for t in _teams(comparison) if in_scope(t, group)]
    return max(scope, key=lambda t: (cells[t]["f1"], -CANDIDATES.index(t)))


def _group_order(group: str) -> int:
    known = list(BENCHMARKS)
    return known.index(group) if group in known else len(known)


def _verdict(share: float) -> str:
    return "clear" if share >= CLEAR_SHARE else "narrow"


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def one_line(results: dict[str, Any]) -> str:
    c = results["comparison"]
    winner = overall_winner(c)
    o = c["overall"][winner]
    return (
        f"**Best overall on the public benchmarks: {NAMES[winner]}** "
        f"(mean F1 {o['mean_f1']:.2f} over five benchmarks, {_verdict(o['first_share'])} "
        f"win), see [Public benchmark results](docs/benchmarks.md)."
    )


def summary(results: dict[str, Any]) -> str:
    """The block shown at the top of the results page and on the Kaggle model page."""
    c = results["comparison"]
    teams = _teams(c)
    winner = overall_winner(c)
    ranking = sorted(teams, key=lambda t: -c["overall"][t]["mean_f1"])
    o = c["overall"][winner]
    runner = ranking[1]
    lines = [
        f"## Winner: {NAMES[winner]}",
        "",
        f"**{NAMES[winner]}** has the best mean F1 across the five benchmarks "
        f"({o['mean_f1']:.3f}, 95% interval {o['ci95_f1'][0]:.3f}-{o['ci95_f1'][1]:.3f}) "
        f"and comes first in {_pct(o['first_share'])} of document resamples: a "
        f"**{_verdict(o['first_share'])}** result"
        + (
            "."
            if o["first_share"] >= CLEAR_SHARE
            else f", with {NAMES[runner]} ({c['overall'][runner]['mean_f1']:.3f}) close behind."
        ),
        "",
        "| Rank | Team | Mean F1 (95% interval) | Mean recall | Comes first |",
        "| ---: | --- | --- | ---: | ---: |",
    ]
    for rank, team in enumerate(ranking, 1):
        t = c["overall"][team]
        lines.append(
            f"| {rank} | {NAMES[team]} | {t['mean_f1']:.3f} "
            f"({t['ci95_f1'][0]:.3f}-{t['ci95_f1'][1]:.3f}) | {t['mean_recall']:.3f} "
            f"| {_pct(t['first_share'])} |"
        )
    recall_leader = max(
        teams, key=lambda t: (c["overall"][t]["mean_recall"], -CANDIDATES.index(t))
    )
    if recall_leader != winner:
        r = c["overall"][recall_leader]
        lines += [
            "",
            f"Recall alone favours **{NAMES[recall_leader]}** ({r['mean_recall']:.3f} against "
            f"{o['mean_recall']:.3f}): it masks slightly more of the sensitive spans but "
            "also over-masks more, which F1 charges for.",
        ]
    lines += [
        "",
        "**Best for each benchmark**, among the teams built for that language:",
        "",
        "| Benchmark | Best | F1 | Result |",
        "| --- | --- | ---: | --- |",
    ]
    for group in sorted(c["groups"], key=_group_order):
        label = BENCHMARKS.get(group, group)
        best = group_winner(c, group)
        share = c["groups"][group][best]["first_share"]
        scope = [t for t in teams if in_scope(t, group)]
        second = max(
            (t for t in scope if t != best),
            key=lambda t: c["groups"][group][t]["f1"],
            default=None,
        )
        note = _verdict(share) + (
            f", {NAMES[second]} close behind" if share < CLEAR_SHARE and second else ""
        )
        lines.append(
            f"| {label} | {NAMES[best]} | {c['groups'][group][best]['f1']:.3f} | {note} |"
        )
    languages = {
        NAMES[t]: ", ".join(sorted(langs))
        for t, langs in sorted(DECLARED_LANGUAGES.items())
    }
    lines += [
        "",
        "How the winner is calculated: mean type-agnostic F1 over all five benchmarks "
        "(every team is scored on every language), with a paired bootstrap over documents "
        f"for the interval and for how often each team comes first; a win is *clear* at "
        f"{_pct(CLEAR_SHARE)} or more. Per benchmark, only teams built for the language "
        "compete"
        + (
            " (" + "; ".join(f"{n}: {langs}" for n, langs in languages.items()) + ")"
            if languages
            else ""
        )
        + ". Computed by `evaluation/public_benchmarks/report.py` from the results file, "
        "not edited by hand. Zero-shot results with this project's own scorer; they do not "
        "show how a team does on your documents.",
    ]
    return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if len(pattern.findall(text)) != 1:
        raise ValueError(f"expected exactly one {START} ... {END} block")
    return pattern.sub(lambda _: f"{START}\n{block}\n{END}", text)


def block_for(path: Path, results: dict[str, Any]) -> str:
    return one_line(results) if path.name.lower() == "readme.md" else summary(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--update", type=Path, action="append", default=[])
    parser.add_argument("--print", action="store_true", help="print the full block")
    args = parser.parse_args(argv)
    results = json.loads(args.results.read_text())
    for path in args.update:
        path.write_text(replace_block(path.read_text(), block_for(path, results)))
    if args.print:
        print(summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
