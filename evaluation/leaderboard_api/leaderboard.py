"""The leaderboard endpoint and the board shaping it owns.

Split out of `main` because every name below serves `GET /api/v1/leaderboard`
and nothing else: the demo fallback rows, the award-to-badge mapping, the
metric flattening the frontend table needs, and the optional MLflow source.
`main` keeps the application wiring and the run-scoped routes.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from . import mock_data, objectstore, ranking, results
from .config import load_settings
from .schemas import Metric

log = logging.getLogger("arena.api")

router = APIRouter()


DEMO = [
    {
        "rank": 1,
        "team": "Team Orion",
        "model": "Orion Masker v2.3",
        "score": 96.4,
        "recall": 0.991,
        "precision": 0.974,
        "f1": 0.982,
        "p95": 210,
        "eligible": True,
        "badge": "Privacy Shield",
        "accent": "ocean",
        "trend": [91.2, 92.8, 93.1, 94.9, 94.2, 95.6, 96.4],
    },
    {
        "rank": 2,
        "team": "Team Atlas",
        "model": "Atlas Guard 1.8",
        "score": 94.7,
        "recall": 0.987,
        "precision": 0.956,
        "f1": 0.971,
        "p95": 238,
        "eligible": True,
        "badge": "Precision Pro",
        "accent": "teal",
        "trend": [90.4, 91.1, 92.8, 92.1, 93.5, 93.9, 94.7],
    },
    {
        "rank": 3,
        "team": "Team Nova",
        "model": "NovaMask Lightning 3.1",
        "score": 92.1,
        "recall": 0.982,
        "precision": 0.931,
        "f1": 0.956,
        "p95": 142,
        "eligible": True,
        "badge": "Efficiency",
        "accent": "sky",
        "trend": [87.5, 89.2, 88.9, 90.4, 91.0, 90.8, 92.1],
    },
    {
        "rank": 4,
        "team": "Team Helix",
        "model": "Helix Shield 2.0",
        "score": 89.3,
        "recall": 0.975,
        "precision": 0.901,
        "f1": 0.937,
        "p95": 205,
        "eligible": False,
        "badge": "Gate review",
        "accent": "violet",
        "trend": [86.1, 86.8, 87.2, 88.4, 87.9, 88.8, 89.3],
    },
    {
        "rank": 5,
        "team": "Team Sentinel",
        "model": "Sentinel Mask v1.4",
        "score": 87.6,
        "recall": 0.968,
        "precision": 0.887,
        "f1": 0.926,
        "p95": 256,
        "eligible": False,
        "badge": "Gate review",
        "accent": "coral",
        "trend": [84.8, 85.5, 85.1, 86.2, 86.9, 86.7, 87.6],
    },
]

DEMO_AWARDS = [
    {
        "kind": "overall",
        "label": "Overall champion",
        "metric": "leaderboard_score",
        "awarded": True,
        "team": "Team Orion",
        "model": "Orion Masker v2.3",
        "value": 96.4,
    },
    {
        "kind": "recall",
        "label": "Best recall",
        "metric": "pii_recall",
        "awarded": True,
        "team": "Team Orion",
        "model": "Orion Masker v2.3",
        "value": 0.991,
    },
    {
        "kind": "precision",
        "label": "Best precision",
        "metric": "pii_precision",
        "awarded": True,
        "team": "Team Atlas",
        "model": "Atlas Guard 1.8",
        "value": 0.974,
    },
    {
        "kind": "f1",
        "label": "Best F1",
        "metric": "pii_f1",
        "awarded": True,
        "team": "Team Orion",
        "model": "Orion Masker v2.3",
        "value": 0.982,
    },
    {
        "kind": "latency",
        "label": "Fastest response",
        "metric": "latency_p95_ms",
        "awarded": True,
        "team": "Team Nova",
        "model": "NovaMask Lightning 3.1",
        "value": 142,
    },
]

BADGE_BY_AWARD = {
    "overall": "Overall champion",
    "recall": "Privacy Shield",
    "precision": "Precision Pro",
    "f1": "Balanced Best",
    "latency": "Speed Champion",
    "critical": "Critical Guard",
    "throughput": "Efficiency",
    "footprint": "Featherweight",
}


def _flat(metric: dict | None) -> float | None:
    """The number a metric envelope reports, or None when it reports none."""
    return Metric.parse(metric).number


def _first(metrics: dict, *names: str) -> float | None:
    """First of several metric names that this row actually reports.

    Callers list the span-based name first: `overlapping_recall` is what the
    rank is actually sorted by, while a `qa-result.schema.json` document's
    `pii_recall` is leak-based (does the literal PII text still appear in the
    output) and only stands in for it on documents published before QA started
    reporting span-based metrics.
    """
    for name in names:
        value = _flat(metrics.get(name))
        if value is not None:
            return value
    return None


def _to_frontend_row(row: dict, badges: dict[str, str]) -> dict:
    """Add the flat fields the frontend table renders, keeping the rich payload."""
    metrics = row.get("metrics", {})
    p95 = _first(metrics, "latency_p95_ms", "p95_latency_ms")
    enriched = dict(row)
    enriched.update(
        {
            "recall": _first(metrics, "overlapping_recall", "pii_recall"),
            "precision": _first(metrics, "overlapping_precision", "pii_precision"),
            "f1": _first(metrics, "overlapping_f1", "pii_f1"),
            "p95": round(p95) if p95 is not None else None,
            "critical_recall": _flat(metrics.get("critical_entity_recall")),
            "leakage_rate": _flat(metrics.get("leakage_rate")),
            "throughput": _flat(metrics.get("throughput_docs_min")),
            "document_success_rate": _flat(metrics.get("document_success_rate")),
            "model_size_mb": _flat(metrics.get("model_size_mb")),
            "badge": badges.get(row["team"], "Official run"),
        }
    )
    return enriched


def _champion(models: list[dict]) -> dict | None:
    """The team the masking console should route to: QA's rank 1.

    The first eligible row *is* rank 1 -- `ranking.load_board` orders rows by
    QA's published `rank` and never recomputes it, and an ineligible row is one
    QA excluded rather than one that scored badly. This is the same rule
    `Arena.jsx` applies for the crown and the confetti; resolving it here means
    the podium and the console cannot disagree about who is winning.
    """
    for row in models:
        if row.get("eligible"):
            return {
                "team": row["team"],
                "rank": row.get("rank"),
                "model": row.get("model") or row.get("model_name") or row["team"],
                "score": row.get("score"),
            }
    return None


def _per_label_coverage(models: list[dict], results_source: object) -> dict:
    """Who reported per-label numbers, and if nobody did, why not.

    The per-label view renders this reason instead of hiding itself. An empty
    section is how the gap stayed invisible: nobody could tell whether the
    models had no weak labels or whether the data had never arrived.
    """
    reporting = [row["team"] for row in models if row.get("per_label")]
    seen: set[str] = set()
    for row in models:
        seen.update(row.get("per_label") or {})

    reason = None
    if not reporting:
        kind = (
            (results_source or {}).get("kind")
            if isinstance(results_source, dict)
            else None
        )
        mode = (
            (results_source or {}).get("mode")
            if isinstance(results_source, dict)
            else None
        )
        if kind == "cross-team-ranking" and mode == "release":
            reason = (
                "This board comes from a release-mode ranking published before "
                "QA reported per-label numbers. Newer runs carry per-label "
                "recall; re-run the benchmark to populate it."
            )
        elif kind == "cross-team-ranking":
            reason = "The published ranking document carries no per-label block."
        else:
            reason = (
                "No published document for these models reported per-label " "metrics."
            )

    return {
        "teams_reporting": reporting,
        "labels_seen": len(seen),
        "reason": reason,
    }


def _board_payload(loaded: dict, source: str) -> dict:
    """Shape one loaded board into the leaderboard response's model fields.

    Shared by every source so the mock, the cross-team ranking, and the
    per-run QA documents cannot drift apart in how badges are assigned or
    diagnostics are reported.
    """
    awards = loaded["awards"]
    badges = {
        winner["team"]: BADGE_BY_AWARD.get(award["kind"], "Official run")
        for award in reversed(awards)
        if award.get("awarded")
        for winner in award.get("teams") or [{"team": award["team"]}]
    }
    models = [_to_frontend_row(row, badges) for row in loaded["models"]]
    return {
        "source": source,
        "models": models,
        "awards": awards,
        "champion": _champion(models),
        "diagnostics": {
            "results_source": loaded["source"],
            "skipped": loaded.get("skipped") or [],
            "per_label_coverage": _per_label_coverage(models, loaded["source"]),
        },
    }


class MlflowUnavailable(RuntimeError):
    """Raised when the optional MLflow client is not installed."""


def load_mlflow_runs(round_name: str) -> list[dict]:
    """Legacy MLflow source, retained as a fallback when no results prefix is set.

    MLflow is optional: it is not installed in the API image, because pulling it
    in also pulls pandas, scipy, scikit-learn, matplotlib, Flask, and more into a
    service that does not use them. A deployment that still wants this path must
    install `mlflow` alongside the API.
    """
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        return DEMO
    try:
        import mlflow
    except ImportError as exc:
        raise MlflowUnavailable(
            "MLFLOW_TRACKING_URI is set but the mlflow package is not installed "
            "in this image. Publish QA results to the object-storage results "
            "prefix, or install mlflow alongside the API."
        ) from exc

    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.get_experiment_by_name(
        os.getenv("MLFLOW_EXPERIMENT_NAME", "pii-masking-leaderboard")
    )
    if not experiment:
        return []
    runs_frame = mlflow.search_runs(
        experiment_ids=[experiment.experiment_id],
        filter_string=(
            f"tags.benchmark_round = '{round_name}' "
            "and tags.evaluation_status = 'completed'"
        ),
        order_by=["metrics.leaderboard_score DESC"],
    )
    accents = results.ACCENTS
    collected = []
    for rank, (_, run) in enumerate(runs_frame.iterrows(), 1):
        run_version = str(run.get("tags.model_version", "?"))
        run_digest = run.get("tags.model_digest")
        if run_version in ("", "?", "unknown") and run_digest:
            run_version = f"sha256:{str(run_digest).split(':', 1)[-1][:12]}"
        collected.append(
            {
                "rank": rank,
                "team": run.get("tags.team_id", "Unknown team"),
                "model": f"{run.get('tags.model_name', 'model')} v{run_version}",
                "score": round(float(run.get("metrics.leaderboard_score", 0)), 1),
                "recall": float(run.get("metrics.pii_recall", 0)),
                "precision": float(run.get("metrics.pii_precision", 0)),
                "f1": float(run.get("metrics.pii_f1", 0)),
                "p95": round(float(run.get("metrics.latency_p95_ms", 0))),
                "eligible": str(run.get("tags.leaderboard_eligible", "true")).lower()
                == "true",
                "badge": run.get("tags.badge", "Official run"),
                "accent": accents[(rank - 1) % len(accents)],
                "trend": [round(float(run.get("metrics.leaderboard_score", 0)), 1)],
            }
        )
    return collected


@router.get("/api/v1/leaderboard")
def leaderboard(round: str = Query(default="benchmark-v1-2026-08")) -> dict:
    settings = load_settings()
    policy = results.scoring_policy()
    payload = {
        "round": {
            "id": round,
            "label": "Benchmark Round 04",
            "period": "August 2026",
            "policy": str(policy.get("policy_version", "1.0")),
        },
        "updated_at": datetime.now(UTC).isoformat(),
        "weights": policy.get("weights", {}),
        "gates": results.thresholds().get("eligibility", {}),
    }

    if settings.mock_leaderboard:
        # TEMPORARY: randomized preview data, see mock_data.py. This branch
        # short-circuits before the storage check below on purpose, so the
        # preview works even with no ARENA_S3_* credentials configured. Set
        # ARENA_MOCK_LEADERBOARD=false to fall through to the real chain.
        payload.update(_board_payload(mock_data.load_mock_results(), "mock"))
        return payload

    if settings.storage_configured:
        try:
            # The cross-team ranking document is what qa-automation publishes
            # today, so it is tried first; `load_board` returns None when the
            # prefix holds none, which falls through to the per-run
            # `qa-result.schema.json` reader rather than showing an empty board.
            loaded = ranking.load_board_cached(settings)
            if loaded is not None:
                payload.update(_board_payload(loaded, "ranking"))
                return payload
            payload.update(_board_payload(results.load_results(settings), "results"))
            return payload
        except objectstore.StorageUnavailable as exc:
            log.warning("results_unavailable error=%s", exc)
        except Exception as exc:
            log.exception("results_load_failed")
            payload["diagnostics"] = {"error": str(exc)}

    try:
        models = load_mlflow_runs(round)
    except MlflowUnavailable as exc:
        # Degrade to demo data and say why, rather than returning a 500.
        log.warning("mlflow_unavailable error=%s", exc)
        diagnostics = payload.setdefault("diagnostics", {})
        diagnostics["error"] = str(exc)
        payload.update(
            {
                "source": "demo",
                "models": DEMO,
                "awards": DEMO_AWARDS,
                "champion": _champion(DEMO),
            }
        )
        return payload
    payload.update(
        {
            "source": "demo" if models is DEMO else "mlflow",
            "models": models,
            "awards": DEMO_AWARDS if models is DEMO else [],
            "champion": _champion(models),
        }
    )
    return payload
