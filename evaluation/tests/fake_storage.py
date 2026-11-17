"""An in-memory stand-in for the S3 subset the arena API uses."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        # Objects must appear to be written after the run that is being tested,
        # because the API treats a manifest older than its upload as stale.
        self.clock = datetime.now(UTC)
        # Request counters. On R2 a listing is a billed Class A operation and a
        # read a Class B one, so the file library's caching is a cost promise,
        # not just a latency one -- these let a test assert it holds.
        self.list_calls = 0
        self.get_calls = 0

    def _tick(self) -> datetime:
        self.clock += timedelta(seconds=1)
        return self.clock

    def add(
        self, bucket: str, key: str, body, content_type="application/json", when=None
    ):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.objects[(bucket, key)] = {
            "body": body,
            "content_type": content_type,
            "last_modified": when or self._tick(),
            "etag": f"etag-{len(self.objects)}",
        }

    # --- boto3 surface -------------------------------------------------
    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, ContinuationToken=None):
        self.list_calls += 1
        keys = sorted(
            key
            for (bucket, key) in self.objects
            if bucket == Bucket and key.startswith(Prefix)
        )
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start : start + MaxKeys]
        contents = [
            {
                "Key": key,
                "Size": len(self.objects[(Bucket, key)]["body"]),
                "ETag": f'"{self.objects[(Bucket, key)]["etag"]}"',
                "LastModified": self.objects[(Bucket, key)]["last_modified"],
            }
            for key in page
        ]
        truncated = start + MaxKeys < len(keys)
        response = {"Contents": contents, "IsTruncated": truncated}
        if truncated:
            response["NextContinuationToken"] = str(start + MaxKeys)
        return response

    def get_object(self, Bucket, Key):
        self.get_calls += 1
        import io

        record = self.objects.get((Bucket, Key))
        if record is None:
            raise KeyError(f"missing {Bucket}/{Key}")
        return {
            "Body": io.BytesIO(record["body"]),
            "ContentType": record["content_type"],
        }

    def head_object(self, Bucket, Key):
        record = self.objects.get((Bucket, Key))
        if record is None:
            raise KeyError(f"missing {Bucket}/{Key}")
        return {
            "ContentLength": len(record["body"]),
            "ETag": f'"{record["etag"]}"',
            "LastModified": record["last_modified"],
        }

    def put_object(self, Bucket, Key, Body, ContentType="application/octet-stream"):
        self.add(Bucket, Key, Body, ContentType)
        return {"ETag": f'"{self.objects[(Bucket, Key)]["etag"]}"'}


def qa_result(
    team="octo",
    model="octo-pii-masker",
    version="17",
    score=94.21,
    recall=0.9873,
    precision=0.9558,
    f1=0.9714,
    p95=1183.44,
    critical=0.9812,
    doc_success=1.0,
    api_error=0.0,
    throughput=27.06,
):
    """Build a document shaped like qa-automation's qa-result.schema.json v1.0."""

    def envelope(value, reason=None):
        return {"available": value is not None, "value": value, "reason": reason}

    return {
        "schema_version": "1.0",
        "dataset": {
            "version": "validation-r2-v1",
            "manifest_sha256": "a" * 64,
            "case_count": 412,
        },
        "source": {
            "project": "internshipjuly2026/full-pii-data-masking",
            "sha": "c0ffee",
            "pipeline_id": "184920",
            "candidate_image": "registry/gateway@sha256:1a2b",
            "resolved_digest": "sha256:1a2b",
            "qa_sha": "0833543",
            "model_name": model,
            "model_version": version,
            "model_digest": "sha256:9f8e",
        },
        "evaluation": {
            "mode": "release",
            "api_contract_version": "1.2",
            "team_id": team,
            "elapsed_seconds": 913.4,
        },
        "metrics": {
            "leaderboard_score": envelope(score),
            "pii_recall": envelope(recall),
            "pii_precision": envelope(precision),
            "pii_f1": envelope(f1),
            "latency_p95_ms": envelope(p95),
            "latency_p50_ms": envelope(466.2),
            "critical_entity_recall": envelope(critical),
            "document_success_rate": envelope(doc_success),
            "api_error_rate": envelope(api_error),
            "throughput_docs_min": envelope(throughput),
            "leakage_rate": envelope(0.0121),
            "recall__PHONE_NUMBER": envelope(0.9929),
            "recall__INSZ_NISS": envelope(1.0),
            "entity_precision": envelope(
                None, "public API returns masked bytes without entity spans"
            ),
        },
        "documents": [
            {
                "case_id": "doc_000001",
                "contract_pass": True,
                "document_pass": True,
                "expected_entity_count": 7,
                "idempotency_pass": None,
                "latency_ms": 412.8,
                "leaked_entity_count": 0,
                "outcome": "passed",
            }
        ],
        "gate_decisions": [
            {
                "metric": "contract_pass_rate",
                "enforce": True,
                "passed": True,
                "actual": 1.0,
                "minimum": 1.0,
                "maximum": None,
                "scope": "validation-r2-v1",
                "rationale": "Every arena request must satisfy the v1.2 HTTP contract.",
                "reason": None,
            }
        ],
    }


def _overlap_entry(rank, team, f1, recall, precision, p95, size, resolved_by):
    return {
        "rank": rank,
        "team": team,
        "documents_scored": 15,
        "overlapping_f1": f1,
        "overlapping_recall": recall,
        "overlapping_precision": precision,
        "p95_latency_ms": p95,
        "model_size_mb": size,
        "resolved_by": resolved_by,
    }


def cross_team_ranking(
    document_ids=None,
    mode="release",
    source="playwright",
    notes=None,
    ranking_overlap=None,
    ranking_exact=None,
    excluded=None,
):
    """Build a document shaped like qa-automation's scripts/rank_teams.py output.

    The defaults reproduce a real 15-document run: berts wins outright on
    detection quality, while synthify and octo land within `close_threshold`
    of each other and are separated by the latency/model-size tiebreak -- so a
    reader that re-sorted these rows by F1 would visibly disagree with `rank`.
    """
    return {
        "document_ids": document_ids
        or [f"llm{index // 5}-doc{index}" for index in range(15)],
        "mode": mode,
        "source": source,
        "close_threshold": 0.03,
        "notes": (
            notes
            if notes is not None
            else [
                "no JMeter latency data for every team -- "
                "tiebreaks fall through to model_size"
            ]
        ),
        "ranking_overlap": (
            ranking_overlap
            if ranking_overlap is not None
            else [
                _overlap_entry(
                    1,
                    "berts",
                    0.975058,
                    0.966245,
                    0.984946,
                    None,
                    120.3,
                    "detection quality (F1)",
                ),
                _overlap_entry(
                    2,
                    "synthify",
                    0.941799,
                    0.938819,
                    0.944798,
                    None,
                    745.8,
                    "tiebreak (F1 within 0.03, latency/model_size decided it)",
                ),
                _overlap_entry(
                    3,
                    "octo",
                    0.938429,
                    0.932489,
                    0.944444,
                    None,
                    1058.8,
                    "tiebreak (F1 within 0.03, latency/model_size decided it)",
                ),
            ]
        ),
        "ranking_exact": (
            ranking_exact
            if ranking_exact is not None
            else [
                {
                    "rank": 1,
                    "team": "berts",
                    "documents_scored": 15,
                    "exact_f1": 0.8121,
                    "exact_recall": 0.8043,
                    "exact_precision": 0.8201,
                    "p95_latency_ms": None,
                    "model_size_mb": 120.3,
                    "resolved_by": "detection quality (F1)",
                },
                {
                    "rank": 2,
                    "team": "synthify",
                    "documents_scored": 15,
                    "exact_f1": 0.7402,
                    "exact_recall": 0.7311,
                    "exact_precision": 0.7495,
                    "p95_latency_ms": None,
                    "model_size_mb": 745.8,
                    "resolved_by": "detection quality (F1)",
                },
                {
                    "rank": 3,
                    "team": "octo",
                    "documents_scored": 15,
                    "exact_f1": 0.7104,
                    "exact_recall": 0.7002,
                    "exact_precision": 0.7209,
                    "p95_latency_ms": None,
                    "model_size_mb": 1058.8,
                    "resolved_by": "detection quality (F1)",
                },
            ]
        ),
        "excluded": excluded or {},
    }


def team_report(team="berts", document_id="llm0-doc0", mode="release", per_label=None):
    """Build a document shaped like pipeline.spec.ts's per-team report.

    Only the fields the arena reads are filled in; the real report also carries
    false negatives/positives, boundary spillover, and PDF-quality checks.
    """
    return {
        "document_id": document_id,
        "team": team,
        "mode": mode,
        "model_info": {
            "team": team,
            "onnx_file_path": f"/teams/{team}/onnx/model.onnx",
            "onnx_file_size_mb": 120.3,
            "parameter_count": 31_000_000,
            "parameter_count_millions": 31.0,
        },
        "candidate_version": {
            "service": "pii-gateway",
            "api_contract_version": "1.2",
            "service_release": "2026.08.3",
            "git_sha": "c0ffee1",
            "image_digest": "sha256:1a2b",
            "model_name": f"{team}-pii-masker",
            "model_version": "4",
            "model_digest": "sha256:9f8e",
        },
        "detection_metrics": {
            "match_basis": "label+position",
            "exact": {"recall": 0.8043, "precision": 0.8201, "f1": 0.8121},
            "overlapping": {"recall": 0.9662, "precision": 0.9849, "f1": 0.9751},
            "mean_confidence": 0.9312,
            "confidence_sample_count": 210,
            "per_label": per_label,
        },
    }
