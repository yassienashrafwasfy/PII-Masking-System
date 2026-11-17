"""Read QA result documents from object storage and rank them.

Each document follows qa-automation's `schemas/qa-result.schema.json` v1.0, where
every metric is an `{available, value, reason}` envelope rather than a bare
number. "Unavailable with a reason" is therefore a first-class display state, not
an edge case: `entity_precision`, `entity_recall`, and `entity_f1` are
permanently unavailable because the public API returns masked bytes without
entity spans, so the precision shown here is `pii_precision`, which QA derives
from the non-PII preservation rate.

The documents carry no timestamp, so the object's own `LastModified` orders them
and only the newest document per team is ranked. That mirrors qa-automation's
supersede behaviour, where re-running a round replaces a team's earlier run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .schemas import Metric
from . import labels, objectstore

log = logging.getLogger("arena.results")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Medals are derived here rather than stored: neither QA document has a medal,
# badge, or rank field.
#
# Each category lists candidate metric names in preference order and the first
# one any row reports wins the whole category, so the same medal table serves
# both document kinds: `pii_recall` comes from a `qa-result.schema.json`
# document, `overlapping_recall` from a cross-team ranking document, and a
# board never mixes the two. The resolved name travels on the award so the
# frontend formats and labels the value it was actually given.
AWARDS = (
    ("overall", "Overall champion", ("leaderboard_score",), "max"),
    ("recall", "Best recall", ("pii_recall", "overlapping_recall"), "max"),
    ("precision", "Best precision", ("pii_precision", "overlapping_precision"), "max"),
    ("f1", "Best F1", ("pii_f1", "overlapping_f1"), "max"),
    ("latency", "Fastest response", ("latency_p95_ms",), "min"),
    ("critical", "Best critical recall", ("critical_entity_recall",), "max"),
    ("throughput", "Highest throughput", ("throughput_docs_min",), "max"),
    # Size is a real selection criterion here, not trivia: it is the last
    # tiebreak `rank_teams.py` applies when two teams are within
    # `close_threshold` on F1 and neither has latency data.
    ("footprint", "Smallest model", ("model_size_mb",), "min"),
)

ACCENTS = ("ocean", "teal", "sky", "violet", "coral")

# Two document kinds share the results prefix, and this is the predicate that
# partitions it. It lives here rather than in `ranking.py` only to keep the
# dependency one-way: `ranking.py` reuses this module's board primitives
# (`ACCENTS`, `compute_awards`), so this module must not import it back.
#
# `rank_teams.py` names its cross-team output
# `leaderboard-aggregate-<n>docs-<mode>.json` for a multi-document run and
# `leaderboard-<document-id>-<mode>.json` for a single one. Per-team reports sit
# two levels deeper, at `<prefix>/<team>/<document_id>/<report>.json`, so depth
# -- not content -- is what tells them apart.
RANKING_FILENAME_PREFIX = "leaderboard-"


def is_ranking_key(key: str, prefix: str) -> bool:
    """True for a cross-team ranking document, false for a per-team report."""
    head = f"{prefix}/" if prefix else ""
    if not key.startswith(head):
        return False
    name = key[len(head) :]
    if "/" in name:
        return False
    return name.startswith(RANKING_FILENAME_PREFIX) and name.endswith(".json")


def _load_yaml(name: str, fallback: dict) -> dict:
    path = CONFIG_DIR / name
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or fallback
    except OSError:
        log.warning("config_unreadable name=%s", name)
        return fallback


def thresholds() -> dict:
    return _load_yaml(
        "thresholds.yaml",
        {
            "policy_version": "1.0",
            "eligibility": {
                "critical_entity_recall_min": 0.98,
                "document_success_rate_min": 1.0,
                "api_error_rate_max": 0.01,
            },
        },
    )


def scoring_policy() -> dict:
    return _load_yaml("scoring-policy.yaml", {"policy_version": "1.0"})


def eligibility(metrics: dict[str, Metric]) -> tuple[bool, list[str]]:
    """Apply the published gates, failing closed on an unavailable input.

    Reimplements qa-automation's `leaderboard.eligibility()` against
    `config/thresholds.yaml` so the arena never shows a rank the policy would
    not award.
    """
    gates = thresholds().get("eligibility", {})
    reasons: list[str] = []

    checks = (
        ("critical_entity_recall", "critical_entity_recall_min", "min"),
        ("document_success_rate", "document_success_rate_min", "min"),
        ("api_error_rate", "api_error_rate_max", "max"),
    )
    for metric_name, gate_name, direction in checks:
        limit = gates.get(gate_name)
        if limit is None:
            continue
        metric = metrics.get(metric_name)
        if metric is None or not metric.available or metric.value is None:
            detail = (metric.reason if metric else None) or "metric unavailable"
            # A dataset with no critical entity types cannot fail critical recall.
            if (
                metric_name == "critical_entity_recall"
                and "no critical" in detail.lower()
            ):
                continue
            reasons.append(f"{metric_name}: {detail}")
            continue
        if direction == "min" and metric.value < float(limit):
            reasons.append(f"{metric_name} {metric.value:.4g} below minimum {limit}")
        if direction == "max" and metric.value > float(limit):
            reasons.append(f"{metric_name} {metric.value:.4g} above maximum {limit}")

    return (not reasons), reasons


def _metric_payload(metric: Metric | None) -> dict:
    if metric is None:
        return {"available": False, "value": None, "reason": "metric missing"}
    # Read the attributes rather than calling `metric.payload()`: this accepts
    # anything envelope shaped, and the per-label tests stand a plain object in
    # here. Routing it through the model would also float an int `support`.
    return {
        "available": metric.available,
        "value": metric.value,
        "reason": metric.reason,
    }


# A per-run qa-result document carries per-label numbers as flat metric names
# -- `recall__NAME`, `support__NAME` -- because its schema constrains every
# metric value to the `{available, value, reason}` envelope and so cannot nest
# a block. Only `recall__` was ever recognised here, which meant a document
# reporting per-label precision or support had it silently ignored.
PER_LABEL_PREFIXES = ("recall", "precision", "f1", "support")

_NO_SPANS_REASON = (
    "the masking API returns masked bytes without entity spans, so a detected "
    "span cannot be attributed to a label"
)


def _per_label_from_flat_metrics(metrics: dict) -> dict:
    """Collect `<metric>__<LABEL>` keys into the board's per-label block."""
    collected: dict[str, dict] = {}
    for name, metric in metrics.items():
        prefix, separator, raw_label = name.partition("__")
        if not separator or prefix not in PER_LABEL_PREFIXES:
            continue
        canonical, aliased_from, is_canonical = labels.normalize(raw_label)
        if not canonical:
            continue
        entry = collected.setdefault(
            canonical,
            {
                "basis": "qa-result",
                "aliased_from": aliased_from,
                "canonical": is_canonical,
                "documents_contributing": None,
            },
        )
        entry[prefix] = _metric_payload(metric)

    # Fill whatever the document did not report, so every label offers the same
    # keys and the UI never has to distinguish "absent" from "unavailable".
    for entry in collected.values():
        for prefix in PER_LABEL_PREFIXES:
            if prefix in entry:
                continue
            reason = (
                _NO_SPANS_REASON
                if prefix in {"precision", "f1"}
                else f"no {prefix}__<label> metric in this document"
            )
            entry[prefix] = {"available": False, "value": None, "reason": reason}
    return collected


def parse_document(document: dict, source_key: str, observed_at: Any) -> dict | None:
    """Map one QA result document onto a leaderboard row."""
    if not isinstance(document, dict):
        return None
    evaluation = document.get("evaluation") or {}
    source = document.get("source") or {}
    dataset = document.get("dataset") or {}
    raw_metrics = document.get("metrics") or {}
    if not isinstance(raw_metrics, dict):
        return None

    team_id = str(evaluation.get("team_id") or "").strip()
    if not team_id:
        log.warning("result_missing_team key=%s", source_key)
        return None

    metrics = {name: Metric.parse(raw) for name, raw in raw_metrics.items()}
    eligible, ineligible_reasons = eligibility(metrics)

    gate_decisions = [
        {
            "metric": gate.get("metric"),
            "enforce": bool(gate.get("enforce")),
            "passed": bool(gate.get("passed")),
            "actual": gate.get("actual"),
            "minimum": gate.get("minimum"),
            "maximum": gate.get("maximum"),
            "reason": gate.get("reason"),
            "rationale": gate.get("rationale"),
        }
        for gate in document.get("gate_decisions") or []
        if isinstance(gate, dict)
    ]

    documents = document.get("documents") or []
    per_label = _per_label_from_flat_metrics(metrics)
    per_entity = {label: entry["recall"] for label, entry in per_label.items()}

    model_name = str(source.get("model_name") or "model")
    model_version = str(source.get("model_version") or "?")
    model_digest = source.get("model_digest")

    # A report with no MODEL_VERSION env set at deploy time reports the
    # literal string "unknown" here, which used to render as "vunknown" on
    # the board -- indistinguishable from every other undated run. Fall back
    # to a shortened digest so the board always shows which build actually
    # ran, even when the version tag is missing.
    version_label = model_version
    if model_version in ("unknown", "?") and model_digest:
        digest_value = str(model_digest).split(":", 1)[-1]
        version_label = f"sha256:{digest_value[:12]}"

    return {
        "team": team_id,
        "model_name": model_name,
        "model_version": model_version,
        "model": f"{model_name} v{version_label}",
        "model_digest": model_digest,
        "eligible": eligible,
        "ineligible_reasons": ineligible_reasons,
        "metrics": {name: _metric_payload(metric) for name, metric in metrics.items()},
        "per_label": per_label,
        "per_entity_recall": per_entity,
        "gate_decisions": gate_decisions,
        "dataset": {
            "version": dataset.get("version"),
            "case_count": dataset.get("case_count"),
            "manifest_sha256": dataset.get("manifest_sha256"),
        },
        "evaluation": {
            "mode": evaluation.get("mode"),
            "api_contract_version": evaluation.get("api_contract_version"),
            "elapsed_seconds": evaluation.get("elapsed_seconds"),
        },
        "provenance": {
            "pipeline_id": source.get("pipeline_id"),
            "source_sha": source.get("sha"),
            "qa_sha": source.get("qa_sha"),
            "candidate_image": source.get("candidate_image"),
            "resolved_digest": source.get("resolved_digest"),
        },
        "document_count": len(documents) if isinstance(documents, list) else 0,
        "result_key": source_key,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "_observed_sort": observed_at,
    }


def _value(row: dict, metric_name: str) -> float | None:
    return Metric.parse(row.get("metrics", {}).get(metric_name)).number


def _resolve_metric(rows: list[dict], names: tuple[str, ...]) -> str:
    """Pick the first candidate metric any row actually reports.

    Falls back to the first name so an unawarded medal still says which metric
    it was looking for rather than naming nothing.
    """
    for name in names:
        if any(_value(row, name) is not None for row in rows):
            return name
    return names[0]


def _tie_sort_key(row: dict) -> tuple[float, str]:
    """Order co-winners the same way rank_teams.py's own tiebreak would:
    lower p95 latency first, team name only as the final, stable tiebreak
    when latency also ties or is missing for every tied team.

    "team"/"model" (the single-winner field kept for any reader that only
    handles one winner) is the first entry after this sort -- so it names
    whichever tied team would actually win the fallback tiebreak, not
    whichever team name happens to sort first alphabetically.
    """
    latency = _value(row, "latency_p95_ms")
    if latency is None:
        latency = _value(row, "p95_latency_ms")
    return (latency if latency is not None else float("inf"), row["team"])


def _rank_based_overall_award(contenders: list[dict], label: str) -> dict:
    """ "Overall champion" mirrors QA's own published rank 1 exactly -- ties
    included -- instead of re-deriving a winner from leaderboard_score's raw
    value under a second, independent tie rule.

    scripts/rank_teams.py already resolves recall ties by p95 latency (see
    its own docstring) and records the result as each row's "rank" (surfaced
    here as "qa_rank" -- see ranking.py's load_board); a fresh
    max(leaderboard_score) computation here has no idea that resolution
    happened, and an "exact value equality" tie rule on a rounded 0-100
    score is a different, coarser test than the CLOSE_THRESHOLD bucket +
    latency rule that produced it in the first place, so the two can
    disagree. Only "qa_rank" is authoritative for "overall" -- every other
    award still measures its own metric directly, unaffected by this. Rows
    from the plain qa-result.json board (results.py's own rank_rows, which
    assigns a bare index-based "rank" with no tie awareness at all) never
    carry "qa_rank", so this path is never reached for them -- see
    compute_awards' call site.
    """
    ranked = [row for row in contenders if row.get("qa_rank") is not None]
    if not ranked:
        return {
            "kind": "overall",
            "label": label,
            "metric": "leaderboard_score",
            "awarded": False,
            "reason": "no eligible run reported this metric",
        }
    best_rank = min(row["qa_rank"] for row in ranked)
    winners = sorted(
        (row for row in ranked if row["qa_rank"] == best_rank), key=_tie_sort_key
    )
    return {
        "kind": "overall",
        "label": label,
        "metric": "leaderboard_score",
        "awarded": True,
        "team": winners[0]["team"],
        "model": winners[0]["model"],
        "value": _value(winners[0], "leaderboard_score"),
        "teams": [
            {
                "team": row["team"],
                "model": row["model"],
                "value": _value(row, "leaderboard_score"),
            }
            for row in winners
        ],
        "tied": len(winners) > 1,
    }


def compute_awards(rows: list[dict]) -> list[dict]:
    """Award one medal per category among eligible rows.

    A genuine exact tie on the metric itself names every tied team, not one
    arbitrarily chosen winner -- two teams landing on the identical value
    (e.g. both at 0.9958 overlapping_recall, a real case this board has
    actually shown) means the metric itself cannot separate them, so the
    award shouldn't pretend it can. `teams` carries every tied team's
    {team, model, value}, ordered the same way rank_teams.py's own
    tiebreak would (lower p95 latency first -- see _tie_sort_key); `team`/
    `model` still carry that first entry, for any reader that only handles
    one winner, so it names the team that would actually win the fallback
    tiebreak rather than whichever team name sorts first alphabetically.

    "overall" is the one exception to computing all this from the metric
    value directly: whenever rows carry a "qa_rank" (the cross-team ranking
    document QA publishes -- see ranking.py's load_board), it already
    reflects QA's own recall+latency tiebreak, and "overall" defers to it
    entirely -- see _rank_based_overall_award. Rows from a plain
    qa-result.json board never carry "qa_rank", so this never changes
    behavior there.
    """
    contenders = [row for row in rows if row.get("eligible")] or rows
    awards: list[dict] = []
    for kind, label, metric_names, direction in AWARDS:
        if kind == "overall" and any(
            row.get("qa_rank") is not None for row in contenders
        ):
            awards.append(_rank_based_overall_award(contenders, label))
            continue
        metric_name = _resolve_metric(contenders, metric_names)
        scored = [
            (row, _value(row, metric_name))
            for row in contenders
            if _value(row, metric_name) is not None
        ]
        if not scored:
            awards.append(
                {
                    "kind": kind,
                    "label": label,
                    "metric": metric_name,
                    "awarded": False,
                    "reason": "no eligible run reported this metric",
                }
            )
            continue

        best_value = (min if direction == "min" else max)(
            value for _row, value in scored
        )
        winners = sorted(
            (row for row, value in scored if value == best_value), key=_tie_sort_key
        )
        awards.append(
            {
                "kind": kind,
                "label": label,
                "metric": metric_name,
                "awarded": True,
                "team": winners[0]["team"],
                "model": winners[0]["model"],
                "value": best_value,
                "teams": [
                    {"team": row["team"], "model": row["model"], "value": best_value}
                    for row in winners
                ],
                "tied": len(winners) > 1,
            }
        )
    return awards


def rank_rows(rows: list[dict]) -> list[dict]:
    """Keep the newest document per team, then order by score."""
    newest: dict[str, dict] = {}
    for row in rows:
        current = newest.get(row["team"])
        if current is None:
            newest[row["team"]] = row
            continue
        incoming = row.get("_observed_sort")
        existing = current.get("_observed_sort")
        # A row wins when it is newer than the one held, and also when the one
        # held has no observation time at all -- an undated row is the weakest
        # thing that can be there.
        if incoming and (not existing or incoming > existing):
            newest[row["team"]] = row

    ordered = sorted(
        newest.values(),
        key=lambda row: (
            row.get("eligible", False),
            _value(row, "leaderboard_score") or -1.0,
        ),
        reverse=True,
    )
    for index, row in enumerate(ordered):
        score = _value(row, "leaderboard_score")
        row["rank"] = index + 1
        row["accent"] = ACCENTS[index % len(ACCENTS)]
        row["score"] = round(score, 2) if score is not None else None
        row.pop("_observed_sort", None)
    return ordered


def score_history(rows: list[dict], limit: int = 12) -> dict[str, list[float]]:
    """Build a per-team score series from every result document for that team.

    The prefix holds one document per model per run, so a team that has been
    evaluated repeatedly yields a real trend line rather than a single point.
    """
    grouped: dict[str, list[tuple[Any, float]]] = {}
    for row in rows:
        score = _value(row, "leaderboard_score")
        if score is None:
            continue
        grouped.setdefault(row["team"], []).append((row.get("_observed_sort"), score))
    series: dict[str, list[float]] = {}
    for team, points in grouped.items():
        ordered = sorted(points, key=lambda pair: (pair[0] is not None, pair[0]))
        series[team] = [round(score, 2) for _, score in ordered][-limit:]
    return series


def assemble_board(
    rows: list[dict], source: dict, skipped: list[dict] | None = None
) -> dict:
    """Rank parsed rows, build trend lines, and award medals.

    Shared by the object-storage reader below and by `mock_data.py`'s temporary
    generator, so ranking, eligibility, and medal logic is never duplicated: the
    two differ only in where `rows` came from.
    """
    trends = score_history(rows)
    ranked = rank_rows(rows)
    for row in ranked:
        history = trends.get(row["team"]) or []
        # A single run still needs a drawable series, so fall back to its score.
        row["trend"] = history or (
            [row["score"]] if row.get("score") is not None else []
        )
        row["run_count"] = len(history)
    return {
        "models": ranked,
        "awards": compute_awards(ranked),
        "skipped": skipped or [],
        "source": source,
    }


def load_results(settings: Settings) -> dict:
    """Load and rank every QA result document under the configured prefix."""
    objects = objectstore.list_objects(
        settings, settings.results_bucket, settings.results_prefix
    )
    rows: list[dict] = []
    skipped: list[dict] = []
    for stored in objects:
        if not stored.key.lower().endswith(".json"):
            continue
        # Both document kinds share this prefix. A cross-team ranking document
        # reaching this reader means `ranking.load_board` already declined it,
        # so say that rather than calling it "not a QA result document".
        if is_ranking_key(stored.key, settings.results_prefix):
            skipped.append(
                {
                    "key": stored.key,
                    "reason": "cross-team ranking document, not usable as a board",
                }
            )
            continue
        try:
            body, _ = objectstore.get_bytes(
                settings, settings.results_bucket, stored.key
            )
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            skipped.append({"key": stored.key, "reason": f"unreadable JSON: {exc}"})
            continue
        except Exception as exc:  # network / permission failure on one object
            skipped.append({"key": stored.key, "reason": str(exc)})
            continue
        row = parse_document(document, stored.key, stored.last_modified)
        if row is None:
            skipped.append({"key": stored.key, "reason": "not a QA result document"})
            continue
        rows.append(row)

    return assemble_board(
        rows,
        source={
            "bucket": settings.results_bucket,
            "prefix": settings.results_prefix,
            "documents_found": len(rows),
        },
        skipped=skipped,
    )
