"""Span scoring against a benchmark's gold, in one vocabulary for every team.

Headline (type-agnostic, what masking needs): did the detector cover the sensitive
span, whatever label it gave it?

* ``merged`` is MEDDOCAN sub-track 2B: spans separated only by non-alphanumeric
  characters are joined on both sides, then matched exactly. It is the headline P/R/F1
  because it does not charge a detector for how it splits "Dr. Jansen, Utrecht".
* ``exact`` is sub-track 2A: no merging.
* ``char_recall`` is the share of alphanumeric gold characters a prediction covers, the
  closest number to "how much of the identifier was left readable".
* ``typed`` is strict: exact offsets and a compatible coarse category, over the gold
  spans some team has a label for. Predictions that land on a gold span no team can
  label (a profession, a title) are not charged as false positives there.

Numbers from this scorer are not the benchmarks' official scores: the official MEDDOCAN
script and meddeid-eval differ in details, and the systems published beside those
benchmarks were trained on their train splits while these detectors were not.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from . import labels
from .loaders import Document

Prediction = tuple[str, int, int]  # platform label, start, end


def merge_spans(spans: Iterable[tuple[int, int]], text: str) -> set[tuple[int, int]]:
    """Join spans that overlap or are separated only by non-alphanumeric characters."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and (
            start <= merged[-1][1]
            or not any(ch.isalnum() for ch in text[merged[-1][1] : start])
        ):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return {(start, end) for start, end in merged}


def _chars(spans: Iterable[tuple[int, int]], text: str) -> set[int]:
    return {i for start, end in spans for i in range(start, end) if text[i].isalnum()}


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _prf_counts(gold: set, predicted: set) -> tuple[int, int, int]:
    hits = len(gold & predicted)
    return hits, len(predicted) - hits, len(gold) - hits


Span = tuple[int, int]
Scored = tuple[int, int, str]  # start, end, coarse category


def _scored(
    predictions: Sequence[Prediction],
) -> tuple[list[Scored], Counter[str]]:
    """Predictions as coarse categories; clinical and unscored labels dropped."""
    unmapped: Counter[str] = Counter()
    scored: list[Scored] = []
    for label, start, end in predictions:
        if label in labels.CLINICAL_LABELS or label in labels.ARBITRATION_ONLY:
            continue
        if label not in labels.PLATFORM_COARSE:
            unmapped[label] += 1
            continue
        scored.append((start, end, labels.PLATFORM_COARSE[label]))
    return scored, unmapped


def _span_counts(doc: Document, pred_spans: set[Span]) -> Counter[str]:
    gold_spans = {(g.start, g.end) for g in doc.gold}
    c: Counter[str] = Counter()
    sides = {
        "exact": (gold_spans, pred_spans),
        "merged": (
            merge_spans(gold_spans, doc.text),
            merge_spans(pred_spans, doc.text),
        ),
    }
    for name, (gold_side, pred_side) in sides.items():
        tp, fp, fn = _prf_counts(gold_side, pred_side)
        c[f"{name}_tp"], c[f"{name}_fp"], c[f"{name}_fn"] = tp, fp, fn
    c["gold_spans"] = len(gold_spans)
    c["gold_overlapped"] = sum(
        any(_overlaps(g, p) for p in pred_spans) for g in gold_spans
    )
    c["pred_spans"] = len(pred_spans)
    c["pred_on_gold"] = sum(
        any(_overlaps(p, g) for g in gold_spans) for p in pred_spans
    )
    return c


def _char_counts(doc: Document, pred_spans: set[Span]) -> Counter[str]:
    gold_chars = _chars({(g.start, g.end) for g in doc.gold}, doc.text)
    pred_chars = _chars(pred_spans, doc.text)
    c: Counter[str] = Counter()
    c["gold_chars"], c["gold_chars_covered"] = len(gold_chars), len(
        gold_chars & pred_chars
    )
    c["pred_chars"], c["pred_chars_on_gold"] = len(pred_chars), len(
        pred_chars & gold_chars
    )
    c["other_chars"] = sum(ch.isalnum() for ch in doc.text) - len(gold_chars)
    c["other_chars_redacted"] = len(pred_chars - gold_chars)
    if doc.core:
        core_chars = _chars(doc.core, doc.text)
        c["core_chars"], c["core_chars_covered"] = len(core_chars), len(
            core_chars & pred_chars
        )
    return c


def _typed_counts(doc: Document, scored: list[Scored]) -> Counter[str]:
    typed_gold = [g for g in doc.gold if g.category != labels.OTHER]
    unlabelable = [(g.start, g.end) for g in doc.gold if g.category == labels.OTHER]
    c: Counter[str] = Counter()
    for g in typed_gold:
        exact = any(
            (s, e) == (g.start, g.end) and cat in g.accept for s, e, cat in scored
        )
        c["typed_tp"] += exact
        c[f"type|{g.tag}|exact"] += exact
    for s, e, cat in scored:
        right_type = any(
            (s, e) == (g.start, g.end) and cat in g.accept for g in typed_gold
        )
        if not right_type and not any(_overlaps((s, e), u) for u in unlabelable):
            c["typed_fp"] += 1
    c["typed_fn"] = len(typed_gold) - c["typed_tp"]
    return c


def _per_type_counts(doc: Document, pred_spans: set[Span]) -> Counter[str]:
    pred_chars = _chars(pred_spans, doc.text)
    c: Counter[str] = Counter()
    for g in doc.gold:
        span = (g.start, g.end)
        covered = _chars([span], doc.text)
        c[f"type|{g.tag}|n"] += 1
        c[f"type|{g.tag}|overlapped"] += any(_overlaps(span, p) for p in pred_spans)
        c[f"type|{g.tag}|chars"] += len(covered)
        c[f"type|{g.tag}|chars_covered"] += len(covered & pred_chars)
    return c


def count_document(
    doc: Document, predictions: Sequence[Prediction]
) -> tuple[Counter[str], Counter[str]]:
    """Counts for one document, and the platform labels that had no category."""
    scored, unmapped = _scored(predictions)
    pred_spans = {(s, e) for s, e, _ in scored}
    c: Counter[str] = Counter()
    for part in (
        _span_counts(doc, pred_spans),
        _char_counts(doc, pred_spans),
        _typed_counts(doc, scored),
        _per_type_counts(doc, pred_spans),
    ):
        c.update(part)
    return c, unmapped


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prf(tp: float, fp: float, fn: float) -> dict[str, float]:
    p, r = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"precision": p, "recall": r, "f1": _ratio(2 * p * r, p + r)}


def metrics(c: Counter[str] | dict[str, float]) -> dict[str, Any]:
    get = lambda key: c.get(key, 0)  # noqa: E731
    result: dict[str, Any] = {
        "documents": get("documents"),
        "gold_spans": get("gold_spans"),
        "merged": _prf(get("merged_tp"), get("merged_fp"), get("merged_fn")),
        "exact": _prf(get("exact_tp"), get("exact_fp"), get("exact_fn")),
        "typed": _prf(get("typed_tp"), get("typed_fp"), get("typed_fn")),
        "overlap_recall": _ratio(get("gold_overlapped"), get("gold_spans")),
        "overlap_precision": _ratio(get("pred_on_gold"), get("pred_spans")),
        "char_recall": _ratio(get("gold_chars_covered"), get("gold_chars")),
        "char_precision": _ratio(get("pred_chars_on_gold"), get("pred_chars")),
        "non_pii_redaction_rate": _ratio(
            get("other_chars_redacted"), get("other_chars")
        ),
    }
    if get("core_chars"):
        result["core_pii_char_recall"] = _ratio(
            get("core_chars_covered"), get("core_chars")
        )
    per_type = {}
    for key in sorted(k for k in c if k.startswith("type|") and k.endswith("|n")):
        tag = key.split("|")[1]
        per_type[tag] = {
            "n": get(key),
            "overlap_recall": _ratio(get(f"type|{tag}|overlapped"), get(key)),
            "char_recall": _ratio(
                get(f"type|{tag}|chars_covered"), get(f"type|{tag}|chars")
            ),
            "exact_typed_recall": _ratio(get(f"type|{tag}|exact"), get(key)),
        }
    result["per_type"] = per_type
    return result


def aggregate(counts: Sequence[Counter[str]]) -> Counter[str]:
    total: Counter[str] = Counter()
    for c in counts:
        total.update(c)
    total["documents"] = len(counts)
    return total


def bootstrap(
    counts: Sequence[Counter[str]], rounds: int = 1000, seed: int = 0
) -> dict[str, tuple[float, float]]:
    """95% intervals for the headline numbers, resampling whole documents."""
    rng = random.Random(seed)
    recalls, f1s, char_recalls = [], [], []
    for _ in range(rounds):
        sample = aggregate([counts[rng.randrange(len(counts))] for _ in counts])
        m = metrics(sample)
        recalls.append(m["merged"]["recall"])
        f1s.append(m["merged"]["f1"])
        char_recalls.append(m["char_recall"])

    def interval(values: list[float]) -> tuple[float, float]:
        values.sort()
        return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]

    return {
        "merged_recall": interval(recalls),
        "merged_f1": interval(f1s),
        "char_recall": interval(char_recalls),
    }
