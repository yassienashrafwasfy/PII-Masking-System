from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import mlflow
import yaml
from pypdf import PdfReader

from .metrics import evaluate_text, leaderboard_score


def fetch_live_identity(endpoint: str, team_id: str, timeout: float) -> dict[str, str]:
    """Ask the gateway's own /version for the identity it is running now.

    A caller that omits --model-version/--model-digest gets whatever the
    live service reports (the same values evaluation/leaderboard_api/results.py
    falls back to a shortened digest for when this comes back "unknown") --
    rather than the run being tagged with the literal placeholder "unknown"
    just because whatever invoked this script didn't pass a value.
    """
    parsed = urlparse(endpoint)
    version_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/version")
    response = httpx.get(version_url, headers={"X-Team-Id": team_id}, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    return {
        "model_name": str(body.get("model_name", "")),
        "model_version": str(body.get("model_version", "")),
        "model_digest": str(body.get("model_digest", "")),
    }


def extract_pdf_text(payload: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(payload)
        handle.flush()
        return "\n".join(
            page.extract_text() or "" for page in PdfReader(handle.name).pages
        )


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def identity_labels() -> tuple[str, ...]:
    """Labels this benchmark scores: the identity taxonomy, nothing else.

    The masker's clinical-PHI and locale labels are routing-only (ADR 006) and
    have no ground truth here, so asking for them would charge a team with a
    precision it cannot show. Passing the labels here, per request, leaves a
    deployment's default behaviour alone.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "platform/masking-gateway/src/masking_engine/app/core/taxonomy.json"
    )
    return tuple(json.loads(path.read_text(encoding="utf-8"))["entity_labels"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--benchmark-round", default="benchmark-v1-2026-08")
    parser.add_argument("--model-digest", default=None)
    parser.add_argument("--image-digest", default="unknown")
    args = parser.parse_args()

    # --model-name/--model-version/--model-digest are optional: when the
    # caller omits any of them, ask the endpoint's own /version for what it
    # is actually running right now, rather than tagging the run "unknown".
    # An explicitly-passed value always wins -- this only fills gaps.
    if (
        args.model_name is None
        or args.model_version is None
        or args.model_digest is None
    ):
        try:
            live = fetch_live_identity(args.endpoint, args.team_id, args.timeout)
        except Exception as exc:  # noqa: BLE001 - identity lookup is best-effort
            print(
                f"warning: could not fetch live identity from /version: {exc}",
                file=sys.stderr,
            )
            live = {}
        args.model_name = args.model_name or live.get("model_name") or "model"
        args.model_version = (
            args.model_version or live.get("model_version") or "unknown"
        )
        args.model_digest = args.model_digest or live.get("model_digest") or "unknown"

    policy_path = Path(__file__).parents[1] / "config" / "scoring-policy.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    rows = [
        json.loads(line)
        for line in args.dataset.read_text().splitlines()
        if line.strip()
    ]
    dataset_root = args.dataset.parent
    recalls, precisions, latencies, successes = [], [], [], 0
    per_entity_scores: dict[str, list[float]] = {}
    mask_labels = identity_labels()

    with httpx.Client(timeout=args.timeout) as client:
        for row in rows:
            source = dataset_root / row["path"]
            started = time.perf_counter()
            response = client.post(
                args.endpoint,
                content=source.read_bytes(),
                headers={
                    "Content-Type": row["mime_type"],
                    "X-Mask-Labels": ",".join(mask_labels),
                },
            )
            latencies.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                recalls.append(0.0)
                precisions.append(0.0)
                continue
            try:
                source_text = extract_pdf_text(source.read_bytes())
                output_text = extract_pdf_text(response.content)
                result = evaluate_text(source_text, output_text, row["entities"])
                recalls.append(float(result["pii_recall"]))
                precisions.append(float(result["non_pii_preservation_rate"]))
                for entity_type, score in result["per_entity_recall"].items():
                    per_entity_scores.setdefault(entity_type, []).append(float(score))
                successes += 1
            except Exception:
                recalls.append(0.0)
                precisions.append(0.0)

    elapsed_minutes = max(sum(latencies) / 60000, 1 / 60000)
    recall = statistics.fmean(recalls)
    precision = statistics.fmean(precisions) if precisions else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    critical = {"phone", "national_id", "patient_id", "date_of_birth"}
    entity_recall = {
        name: statistics.fmean(values) for name, values in per_entity_scores.items()
    }
    critical_recall = min(
        (entity_recall.get(name, 0.0) for name in critical), default=0.0
    )
    metrics = {
        "pii_recall": recall,
        "pii_precision": precision,
        "pii_f1": f1,
        "leakage_rate": 1 - recall,
        "overmasking_rate": 1 - precision,
        "critical_entity_recall": critical_recall,
        "document_success_rate": successes / len(rows),
        "api_error_rate": (len(rows) - successes) / len(rows),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": percentile(latencies, 0.95),
        "throughput_docs_min": len(rows) / elapsed_minutes,
    }
    metrics["leaderboard_score"] = leaderboard_score(metrics, policy)
    eligible = (
        critical_recall >= 0.98
        and metrics["document_success_rate"] >= 1.0
        and metrics["api_error_rate"] <= 0.01
    )

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(
        os.getenv("MLFLOW_EXPERIMENT_NAME", "pii-masking-leaderboard")
    )
    with mlflow.start_run(
        run_name=f"{args.team_id}-{args.model_name}-v{args.model_version}"
    ):
        mlflow.set_tags(
            {
                "team_id": args.team_id,
                "model_name": args.model_name,
                "model_version": args.model_version,
                "model_digest": args.model_digest,
                "image_digest": args.image_digest,
                "benchmark_version": "1.0",
                "benchmark_round": args.benchmark_round,
                "scoring_policy_version": policy["policy_version"],
                "evaluation_status": "completed",
                "leaderboard_eligible": str(eligible).lower(),
            }
        )
        mlflow.log_metrics(metrics)
        for entity_type, value in entity_recall.items():
            mlflow.log_metric(f"recall.{entity_type}", value)
        mlflow.log_dict(
            {
                "metrics": metrics,
                "per_entity_recall": entity_recall,
                "eligible": eligible,
            },
            "summary.json",
        )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
