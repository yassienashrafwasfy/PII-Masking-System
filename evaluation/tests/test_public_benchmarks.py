"""The public-benchmark scorer, on invented text (the real corpora are not in the repo).

A scorer nobody checked turns every number into a guess, so the first two tests pin
the extremes: gold fed back as predictions must be perfect, nothing must score zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "platform/masking-gateway/src")
)

from public_benchmarks import labels, loaders, scoring  # noqa: E402
from public_benchmarks.loaders import Document, Gold  # noqa: E402

TEXT = "Jan Peeters (62 jaar) woont in Utrecht, bel 06-12345678. Hij is metselaar."


def _gold(value: str, tag: str, category: str) -> Gold:
    start = TEXT.index(value)
    return Gold(start, start + len(value), tag, category, frozenset({category}))


def _doc() -> Document:
    return Document(
        "d1",
        "test",
        "nl",
        TEXT,
        [
            _gold("Jan Peeters", "Name", labels.NAME),
            _gold("62", "Age", labels.AGE),
            _gold("Utrecht", "City", labels.ADDRESS),
            _gold("06-12345678", "Phone", labels.PHONE),
            _gold("metselaar", "Job", labels.OTHER),
        ],
    )


def _as_predictions(doc: Document) -> list[tuple[str, int, int]]:
    platform = {v: k for k, v in labels.PLATFORM_COARSE.items() if k != "SSN"}
    platform[labels.ORG] = "ORGANIZATION_NAME"
    platform[labels.OTHER] = "NAME"  # a team that happens to mask the profession
    return [(platform[g.category], g.start, g.end) for g in doc.gold]


def test_gold_fed_back_as_predictions_scores_perfectly() -> None:
    doc = _doc()
    counts, unmapped = scoring.count_document(doc, _as_predictions(doc))
    m = scoring.metrics(scoring.aggregate([counts]))
    assert not unmapped
    assert m["merged"]["f1"] == m["exact"]["f1"] == m["typed"]["f1"] == 1.0
    assert m["char_recall"] == m["overlap_recall"] == 1.0
    assert m["non_pii_redaction_rate"] == 0.0


def test_no_predictions_leak_everything() -> None:
    counts, _ = scoring.count_document(_doc(), [])
    m = scoring.metrics(scoring.aggregate([counts]))
    assert m["merged"]["recall"] == m["char_recall"] == m["overlap_recall"] == 0.0
    assert m["typed"]["recall"] == 0.0 and m["non_pii_redaction_rate"] == 0.0


def test_an_unlabelable_gold_span_is_a_leak_but_not_a_typed_miss() -> None:
    doc = _doc()
    predictions = [p for p in _as_predictions(doc) if p[1] != TEXT.index("metselaar")]
    counts, _ = scoring.count_document(doc, predictions)
    m = scoring.metrics(scoring.aggregate([counts]))
    # "Jan Peeters (62" joins into one merged span, so 3 of the 4 merged gold spans are found
    assert m["merged"]["recall"] == pytest.approx(3 / 4)
    assert m["overlap_recall"] == pytest.approx(4 / 5)  # the profession is a leak
    assert m["typed"]["recall"] == 1.0  # no team has a label for it


def test_a_prediction_on_an_unlabelable_span_is_not_a_typed_false_positive() -> None:
    doc = _doc()
    counts, _ = scoring.count_document(doc, _as_predictions(doc))
    assert counts["typed_fp"] == 0


def test_clinical_and_arbitration_labels_are_not_scored() -> None:
    doc = _doc()
    extra = [("DIAGNOSIS", 0, 3), ("HEIGHT", 10, 12), *_as_predictions(doc)]
    counts, unmapped = scoring.count_document(doc, extra)
    assert not unmapped and counts["merged_fp"] == 0


def test_an_unknown_platform_label_is_reported_not_guessed() -> None:
    _, unmapped = scoring.count_document(_doc(), [("MYSTERY", 0, 3)])
    assert unmapped == {"MYSTERY": 1}


def test_wrong_type_is_a_typed_miss_but_a_merged_hit() -> None:
    doc = _doc()
    city = TEXT.index("Utrecht")
    counts, _ = scoring.count_document(
        doc, [("PHONE_NUMBER", city, city + 7)]
    )  # a city, called a phone
    assert counts["merged_tp"] == 1 and counts["typed_tp"] == 0


def test_spans_split_around_punctuation_merge_but_around_words_do_not() -> None:
    text = "Dr. Jansen, Utrecht en Gent"
    assert scoring.merge_spans({(0, 10), (12, 19)}, text) == {(0, 19)}
    assert scoring.merge_spans({(12, 19), (23, 27)}, text) == {(12, 19), (23, 27)}


def test_every_label_the_platform_masks_has_a_category() -> None:
    from masking_engine import label_registry

    uncovered = (
        set(label_registry.masked_labels())
        - set(labels.PLATFORM_COARSE)
        - labels.CLINICAL_LABELS
    )
    assert not uncovered, "a masked label the scorer would silently drop"


def test_core_recall_counts_only_identifying_subannotations() -> None:
    doc = _doc()
    doc.core = [(0, 3), (4, 11)]  # "Jan", "Peeters"
    counts, _ = scoring.count_document(doc, [("NAME", 4, 11)])
    assert scoring.metrics(scoring.aggregate([counts]))[
        "core_pii_char_recall"
    ] == pytest.approx(7 / 10)


def test_brat_offsets_survive_crlf_and_a_shifted_offset_fails_loudly(
    tmp_path: Path,
) -> None:
    brat = tmp_path / "test" / "brat"
    brat.mkdir(parents=True)
    text = "Linea uno\r\nPaciente Ana Ruiz\r\n"
    (brat / "a.txt").write_bytes(text.encode())
    start = text.index("Ana")
    (brat / "a.ann").write_bytes(
        f"T1\tNOMBRE_SUJETO_ASISTENCIA {start} {start + 8}\tAna Ruiz\r\n".encode()
    )
    [doc] = loaders.load_meddocan(tmp_path)
    assert doc.text[doc.gold[0].start : doc.gold[0].end] == "Ana Ruiz"
    (brat / "a.ann").write_bytes(
        f"T1\tNOMBRE_SUJETO_ASISTENCIA {start - 1} {start + 7}\tAna Ruiz\r\n".encode()
    )
    with pytest.raises(ValueError, match="do not hold the gold text"):
        loaders.load_meddocan(tmp_path)


def test_an_unmapped_gold_tag_stops_the_load(tmp_path: Path) -> None:
    path = tmp_path / "test.jsonl"
    row = {
        "document_id": "x",
        "text": "Jan",
        "spans": [{"begin": 0, "end": 3, "text": "Jan", "label": "Brand:New"}],
    }
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="no mapping"):
        loaders.load_meddeid(path)


def test_meddocan_kinship_words_are_not_typed_as_names() -> None:
    # MEDDOCAN's FAMILIARES spans are "madre", "familia", ...: charging a team a typed
    # miss for not calling them a NAME would understate every team's typed score.
    assert labels.MEDDOCAN["FAMILIARES_SUJETO_ASISTENCIA"][0] == labels.OTHER
