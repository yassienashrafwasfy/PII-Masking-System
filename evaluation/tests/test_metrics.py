from evaluator.metrics import evaluate_text, leaderboard_score


def test_masking_metrics_detect_leak_and_preserve_safe_text():
    entities = [{"type": "person_name", "value": "Nadia Farouk"}]
    result = evaluate_text(
        "Patient Nadia Farouk attended routine follow-up",
        "Patient [MASKED] attended routine follow-up",
        entities,
    )
    assert result["pii_recall"] == 1.0
    assert result["non_pii_preservation_rate"] == 1.0


def test_score_is_bounded():
    policy = {
        "weights": {
            "pii_recall": 0.4,
            "macro_f1": 0.25,
            "pii_precision": 0.1,
            "document_success_rate": 0.1,
            "latency_score": 0.1,
            "throughput_score": 0.05,
        },
        "normalization": {
            "latency_p95_good_ms": 250,
            "latency_p95_bad_ms": 3000,
            "throughput_good_docs_min": 30,
            "throughput_bad_docs_min": 1,
        },
    }
    metrics = {
        "pii_recall": 1,
        "pii_f1": 1,
        "pii_precision": 1,
        "document_success_rate": 1,
        "latency_p95_ms": 250,
        "throughput_docs_min": 30,
    }
    assert leaderboard_score(metrics, policy) == 100.0
