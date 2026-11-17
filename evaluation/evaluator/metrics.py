from __future__ import annotations

from collections import Counter
import re
from typing import TypedDict


class TextScores(TypedDict):
    """What `evaluate_text` reports for one document."""

    pii_recall: float
    leakage_rate: float
    non_pii_preservation_rate: float
    overmasking_rate: float
    per_entity_recall: dict[str, float]


def _tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"[A-Za-z0-9]+", text.casefold()))


def evaluate_text(
    source_text: str, masked_text: str, entities: list[dict[str, str]]
) -> TextScores:
    total: Counter[str] = Counter()
    masked: Counter[str] = Counter()
    for entity in entities:
        kind = entity["type"]
        total[kind] += 1
        if entity["value"].casefold() not in masked_text.casefold():
            masked[kind] += 1
    total_count = sum(total.values())
    masked_count = sum(masked.values())
    recall = masked_count / total_count if total_count else 1.0
    per_entity = {kind: masked[kind] / count for kind, count in total.items()}
    safe_source = source_text
    for entity in sorted(entities, key=lambda item: len(item["value"]), reverse=True):
        safe_source = safe_source.replace(entity["value"], " ")
    safe_tokens = _tokens(safe_source)
    output_tokens = _tokens(masked_text)
    preserved = sum(
        min(count, output_tokens[token]) for token, count in safe_tokens.items()
    )
    preservation_rate = preserved / sum(safe_tokens.values()) if safe_tokens else 1.0
    return {
        "pii_recall": recall,
        "leakage_rate": 1 - recall,
        "non_pii_preservation_rate": preservation_rate,
        "overmasking_rate": 1 - preservation_rate,
        "per_entity_recall": per_entity,
    }


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalized_inverse(value: float, good: float, bad: float) -> float:
    return clamp((bad - value) / (bad - good))


def normalized_direct(value: float, bad: float, good: float) -> float:
    return clamp((value - bad) / (good - bad))


def leaderboard_score(metrics: dict[str, float], policy: dict) -> float:
    limits = policy["normalization"]
    parts = {
        "pii_recall": metrics["pii_recall"],
        "macro_f1": metrics["pii_f1"],
        "pii_precision": metrics["pii_precision"],
        "document_success_rate": metrics["document_success_rate"],
        "latency_score": normalized_inverse(
            metrics["latency_p95_ms"],
            limits["latency_p95_good_ms"],
            limits["latency_p95_bad_ms"],
        ),
        "throughput_score": normalized_direct(
            metrics["throughput_docs_min"],
            limits["throughput_bad_docs_min"],
            limits["throughput_good_docs_min"],
        ),
    }
    return round(
        100 * sum(parts[name] * weight for name, weight in policy["weights"].items()), 2
    )
