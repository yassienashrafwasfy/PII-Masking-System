"""TEMPORARY: fabricated leaderboard data for the preview UI.

Nothing has been published to the results prefix yet (see `results.py`, which
reads it and remains the real, permanent code path). This module exists only so
the leaderboard has something to show in the meantime. It generates plausible,
randomized QA-result-shaped documents and feeds them through
`results.parse_document` and `results.assemble_board`, so ranking, eligibility
gates, medal selection, and trend-building are never duplicated — only the
*data* is fake, not the logic that scores it.

Delete this file and its call site in `main.py` (guarded by
`Settings.mock_leaderboard` / `ARENA_MOCK_LEADERBOARD`) once QA publishes real
result documents. Nothing else needs to change: turning the flag off restores
the exact object-storage -> MLflow -> demo fallback chain that predates this.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from . import labels, results

# Real teams and model names, so the preview does not invent identities that
# would need to be reconciled with the real ones later.
MOCK_TEAMS = (
    ("octo", "octo-pii-masker"),
    ("synthify", "synthify-gliner-belgian-pii-ner"),
    ("berts", "berts-pii-medroberta-nl-int8"),
)

# Two labels the mock deliberately scores badly and one it never reports, so a
# preview exercises the cases that actually need designing -- a weak entity
# worth spotting, and an honest gap -- rather than only the happy path.
_WEAK_LABELS = ("ADDRESS", "ORGANIZATION_NAME")
_UNREPORTED_LABEL = "EMAIL"

# Mirrors evaluator/metrics.py's leaderboard_score formula. Not imported from
# there because the API's Dockerfile deliberately ships only leaderboard_api/
# (see its requirements.txt), so evaluator/ is not present in the built image.
_DEFAULT_WEIGHTS = {
    "pii_recall": 0.40,
    "macro_f1": 0.25,
    "pii_precision": 0.10,
    "document_success_rate": 0.10,
    "latency_score": 0.10,
    "throughput_score": 0.05,
}
_DEFAULT_NORMALIZATION = {
    "latency_p95_good_ms": 250,
    "latency_p95_bad_ms": 3000,
    "throughput_good_docs_min": 30,
    "throughput_bad_docs_min": 1,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _available(value: float) -> dict:
    return {"available": True, "value": value, "reason": None}


def _score(metrics: dict, policy: dict) -> float:
    limits = policy.get("normalization") or _DEFAULT_NORMALIZATION
    weights = policy.get("weights") or _DEFAULT_WEIGHTS
    latency_score = _clamp01(
        (limits["latency_p95_bad_ms"] - metrics["latency_p95_ms"]["value"])
        / (limits["latency_p95_bad_ms"] - limits["latency_p95_good_ms"])
    )
    throughput_score = _clamp01(
        (metrics["throughput_docs_min"]["value"] - limits["throughput_bad_docs_min"])
        / (limits["throughput_good_docs_min"] - limits["throughput_bad_docs_min"])
    )
    parts = {
        "pii_recall": metrics["pii_recall"]["value"],
        "macro_f1": metrics["pii_f1"]["value"],
        "pii_precision": metrics["pii_precision"]["value"],
        "document_success_rate": metrics["document_success_rate"]["value"],
        "latency_score": latency_score,
        "throughput_score": throughput_score,
    }
    return round(100 * sum(parts[name] * weight for name, weight in weights.items()), 2)


def _mock_document(
    team: str,
    model_name: str,
    version: int,
    quality: float,
    degrade: bool,
    policy: dict,
) -> dict:
    """One fabricated run at a given quality level, in the QA result schema shape.

    `quality` drives the ranking-relevant metrics (recall/precision/latency/
    throughput) across a realistic spread. Eligibility, by contrast, depends
    only on critical_entity_recall (>= 0.98) and document_success_rate (>=
    1.0 exactly, per config/thresholds.yaml) -- both are near-ceiling gates a
    clean run is expected to clear, so they are generated separately and kept
    just above the gate unless `degrade` deliberately fails it. Coupling them
    to `quality` made every mock run ineligible, since quality's realistic
    0.80-0.97 band never reached 0.98.
    """
    jitter = lambda spread: random.uniform(-spread, spread)  # noqa: E731

    recall = _clamp01(quality + jitter(0.02))
    precision = _clamp01(quality - 0.015 + jitter(0.03))
    f1 = (
        0.0
        if (precision + recall) == 0
        else 2 * precision * recall / (precision + recall)
    )
    api_error = max(0.0, jitter(0.008))
    p95 = max(90.0, random.gauss(650 - quality * 420, 90))
    throughput = max(2.0, random.gauss(12 + quality * 22, 4))
    leakage = max(0.0, jitter(0.015))

    if degrade:
        # Occasionally show a run that fails a safety gate, so the UI's
        # "Gate review" state is exercised rather than only ever eligible rows.
        critical_recall = random.uniform(0.85, 0.965)
        doc_success = random.uniform(0.90, 0.99)
    else:
        critical_recall = _clamp01(random.uniform(0.981, 0.998))
        doc_success = 1.0

    metrics = {
        "pii_recall": _available(round(recall, 4)),
        "pii_precision": _available(round(precision, 4)),
        "pii_f1": _available(round(f1, 4)),
        "latency_p95_ms": _available(round(p95, 1)),
        "latency_p50_ms": _available(round(p95 * 0.58, 1)),
        "critical_entity_recall": _available(round(critical_recall, 4)),
        "document_success_rate": _available(round(doc_success, 4)),
        "api_error_rate": _available(round(api_error, 4)),
        "throughput_docs_min": _available(round(throughput, 2)),
        "leakage_rate": _available(round(leakage, 4)),
    }
    for entity in labels.CANONICAL_LABELS:
        if entity == _UNREPORTED_LABEL:
            continue
        if entity in _WEAK_LABELS:
            # Well below the rest, so "which entity did my change break" has
            # something to find in the preview.
            value = _clamp01(critical_recall - 0.35 + jitter(0.05))
        else:
            value = _clamp01(critical_recall + jitter(0.02))
        metrics[f"recall__{entity}"] = _available(round(value, 4))
        # Support varies by label because a real document set holds far more
        # names than VAT numbers, and the view marks a thin sample as one.
        metrics[f"support__{entity}"] = _available(
            random.randint(3, 12)
            if entity in {"BTW_EENHEID", "URL"}
            else random.randint(60, 400)
        )
    metrics["leaderboard_score"] = _available(_score(metrics, policy))

    return {
        "schema_version": "1.0",
        "dataset": {"version": "mock-preview", "case_count": random.randint(180, 420)},
        "source": {
            "model_name": model_name,
            "model_version": str(version),
            "model_digest": f"sha256:mock{version:04d}" + "0" * 55,
        },
        "evaluation": {
            "mode": "release",
            "api_contract_version": "1.2",
            "team_id": team,
            "elapsed_seconds": round(random.uniform(300, 1400), 1),
        },
        "metrics": metrics,
        "documents": [],
        "gate_decisions": [],
    }


def load_mock_results() -> dict:
    """Build a full mock board: several historical runs per team, freshly
    randomized on every call, ranked and awarded through the real pipeline."""
    policy = results.scoring_policy()
    now = datetime.now(UTC)
    rows: list[dict] = []

    for team, model_name in MOCK_TEAMS:
        run_count = random.randint(3, 6)
        quality = random.uniform(0.80, 0.92)
        # One team in four gets a struggling latest run, so eligibility gating
        # and the "Gate review" status are visible sometimes, not never.
        degrade_latest = random.random() < 0.25
        elapsed_days = 0.0
        for index in range(run_count):
            quality = _clamp01(quality + random.uniform(-0.01, 0.03))
            elapsed_days += random.uniform(2.0, 5.0)
            observed_at = now - timedelta(days=(run_count - index) + elapsed_days * 0.1)
            is_latest = index == run_count - 1
            document = _mock_document(
                team,
                model_name,
                version=10 + index,
                quality=quality,
                degrade=is_latest and degrade_latest,
                policy=policy,
            )
            row = results.parse_document(
                document, f"results/mock/{team}/run-{10 + index}.json", observed_at
            )
            if row is not None:
                rows.append(row)

    return results.assemble_board(
        rows,
        source={"bucket": "(mock)", "prefix": "(mock)", "documents_found": len(rows)},
    )
