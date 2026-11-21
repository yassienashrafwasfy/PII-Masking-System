"""Which team does best, from per-document counts, with a paired bootstrap.

The rule is fixed here so the published winner is calculated, not chosen:

* **Overall:** the team with the highest mean type-agnostic F1 (merged-span, the
  headline metric) over the five benchmarks. Every team is scored on every benchmark,
  because a corpus in five languages does not skip the languages a team was not built
  for; the per-benchmark view below shows who is built for what.
* **Per benchmark:** the best F1 among the teams built for that benchmark's language
  (``DECLARED_LANGUAGES``); a team outside its languages is not a candidate there.
* **Confidence:** each bootstrap round resamples the same documents for every team
  (paired), so ``first_share`` is how often a team ranks first on documents that could
  have been drawn instead. A winner is "clear" at 95% or more, otherwise "narrow".

``mdeberta-model-only`` is an ablation of ``mdeberta`` and never a candidate.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

CANDIDATES = ("octo", "synthify", "berts", "mdeberta")
# Languages a team is built for. A team absent from this table declares no scope.
DECLARED_LANGUAGES: dict[str, frozenset[str]] = {
    "octo": frozenset({"nl"}),
    "berts": frozenset({"nl", "en", "fr"}),
}
CLEAR_SHARE = 0.95

Triple = tuple[int, int, int]  # merged-span true positives, false positives, misses
# config -> group -> doc_id -> counts
PerDocument = Mapping[str, Mapping[str, Mapping[str, Triple]]]


def language_of(group: str) -> str:
    return group.rsplit("-", 1)[1]


def in_scope(team: str, group: str) -> bool:
    languages = DECLARED_LANGUAGES.get(team)
    return languages is None or language_of(group) in languages


def _recall_f1(tp: float, fp: float, fn: float) -> tuple[float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return recall, f1


def _first(scores: Mapping[str, float]) -> str:
    # Ties go to the earlier candidate, so the result never depends on dict order.
    return max(scores, key=lambda team: (scores[team], -CANDIDATES.index(team)))


def _interval(values: list[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(0.025 * len(ordered))], ordered[int(0.975 * len(ordered)) - 1]]


def _check_same_documents(per_document: PerDocument) -> dict[str, list[str]]:
    configs = sorted(per_document)
    docs = {
        g: sorted(per_document[configs[0]][g]) for g in sorted(per_document[configs[0]])
    }
    for config in configs:
        for group, ids in docs.items():
            if sorted(per_document[config].get(group, {})) != ids:
                raise ValueError(f"{config}/{group}: not scored on the same documents")
    return docs


def _score(rows: Sequence[Triple], picks: Sequence[int] | None) -> tuple[float, float]:
    chosen = rows if picks is None else [rows[i] for i in picks]
    return _recall_f1(
        sum(t[0] for t in chosen), sum(t[1] for t in chosen), sum(t[2] for t in chosen)
    )


def _mean(per_group: Mapping[str, tuple[float, float]]) -> tuple[float, float]:
    n = len(per_group)
    return (
        sum(v[0] for v in per_group.values()) / n,
        sum(v[1] for v in per_group.values()) / n,
    )


def compare(
    per_document: PerDocument, rounds: int = 1000, seed: int = 0
) -> dict[str, Any]:
    """Point estimates, intervals and first-place shares for every configuration."""
    docs = _check_same_documents(per_document)
    configs, groups = sorted(per_document), sorted(docs)
    teams = [c for c in CANDIDATES if c in per_document]
    rows = {
        c: {g: [per_document[c][g][d] for d in docs[g]] for g in groups}
        for c in configs
    }
    point = {c: {g: _score(rows[c][g], None) for g in groups} for c in configs}

    rng = random.Random(seed)
    boot: dict[str, list[tuple[float, float]]] = {c: [] for c in configs}
    group_first = {g: dict.fromkeys(teams, 0) for g in groups}
    overall_first = dict.fromkeys(teams, 0)
    for _ in range(rounds):
        by_config: dict[str, dict[str, tuple[float, float]]] = {c: {} for c in configs}
        for group in groups:
            n = len(docs[group])
            picks = [rng.randrange(n) for _ in range(n)]  # shared by every team
            for config in configs:
                by_config[config][group] = _score(rows[config][group], picks)
            contenders = {
                t: by_config[t][group][1] for t in teams if in_scope(t, group)
            }
            if contenders:
                group_first[group][_first(contenders)] += 1
        for config in configs:
            boot[config].append(_mean(by_config[config]))
        overall_first[_first({t: boot[t][-1][1] for t in teams})] += 1
    return _summary(point, boot, group_first, overall_first, teams, rounds, seed)


def _summary(
    point: Mapping[str, Mapping[str, tuple[float, float]]],
    boot: Mapping[str, list[tuple[float, float]]],
    group_first: Mapping[str, Mapping[str, int]],
    overall_first: Mapping[str, int],
    teams: Sequence[str],
    rounds: int,
    seed: int,
) -> dict[str, Any]:
    overall = {}
    for config, per_group in point.items():
        recall, f1 = _mean(per_group)
        overall[config] = {
            "mean_f1": f1,
            "mean_recall": recall,
            "ci95_f1": _interval([v[1] for v in boot[config]]),
            "ci95_recall": _interval([v[0] for v in boot[config]]),
            "first_share": overall_first[config] / rounds if config in teams else None,
        }
    groups = {
        group: {
            config: {
                "recall": per_group[group][0],
                "f1": per_group[group][1],
                "first_share": (
                    group_first[group][config] / rounds
                    if config in teams and in_scope(config, group)
                    else None
                ),
            }
            for config, per_group in point.items()
        }
        for group in sorted(next(iter(point.values())))
    }
    return {"rounds": rounds, "seed": seed, "overall": overall, "groups": groups}
