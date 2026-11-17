"""How the board reads the per-label numbers QA publishes.

Two documents carry them in two shapes: a per-team report nests a `per_label`
block (prf2's key names), and a per-run qa-result flattens them into
`<metric>__<LABEL>` metric names because its schema pins every metric value to
the `{available, value, reason}` envelope and so cannot nest. Both have to
arrive at the same board shape.
"""

from __future__ import annotations

from leaderboard_api import ranking, results


def _qa_release_entry(true_positives: int, actual: int) -> dict:
    """One label as qa-automation's release mode emits it."""
    return {
        "true_positives": true_positives,
        "predicted_count": None,
        "actual_count": actual,
        "precision": None,
        "recall": true_positives / actual if actual else None,
        "f1": None,
        "f2": None,
        "false_negatives": actual - true_positives,
        "precision_reason": "release mode infers spans by diffing masked bytes",
    }


def test_precision_f1_and_support_are_kept_not_discarded():
    """The reader used to take `.get("recall")` and drop everything else."""
    block = ranking._per_label_block(
        {
            "NAME": {
                "recall": 0.9,
                "precision": 0.8,
                "f1": 0.85,
                "actual_count": 40,
            }
        },
        basis="detection",
    )

    entry = block["NAME"]
    assert entry["recall"]["value"] == 0.9
    assert entry["precision"]["value"] == 0.8
    assert entry["f1"]["value"] == 0.85
    assert entry["support"]["value"] == 40


def test_support_is_read_from_qas_own_key_name():
    """QA reports counts as prf2 names them; the board speaks of support."""
    block = ranking._per_label_block(
        {"NAME": _qa_release_entry(3, 4)}, basis="detection"
    )

    assert block["NAME"]["support"]["value"] == 4
    assert block["NAME"]["recall"]["value"] == 0.75


def test_release_precision_is_unavailable_with_qas_own_reason():
    """QA explains why better than this module can guess."""
    block = ranking._per_label_block(
        {"NAME": _qa_release_entry(3, 4)}, basis="detection"
    )

    precision = block["NAME"]["precision"]
    assert precision["available"] is False
    assert "diffing masked bytes" in precision["reason"]


def test_a_missing_metric_is_unavailable_with_a_reason_not_absent():
    """A blank cell and a zero look identical and only one of them is true."""
    block = ranking._per_label_block({"NAME": {"recall": 0.9}}, basis="detection")

    support = block["NAME"]["support"]
    assert support["available"] is False
    assert support["value"] is None
    assert support["reason"]


def test_an_alias_is_normalised_and_records_what_it_came_from():
    block = ranking._per_label_block({"PHONE": {"recall": 1.0}}, basis="detection")

    assert "PHONE_NUMBER" in block
    assert block["PHONE_NUMBER"]["aliased_from"] == "PHONE"
    assert block["PHONE_NUMBER"]["canonical"] is True


def test_an_unknown_label_survives_flagged_off_taxonomy():
    block = ranking._per_label_block(
        {"MEDICATION_NAME": {"recall": 0.5}}, basis="detection"
    )

    assert block["MEDICATION_NAME"]["canonical"] is False


def test_two_spellings_of_one_label_do_not_get_averaged_together():
    """They are two measurements; silently blending them would invent a third."""
    block = ranking._per_label_block(
        {"PHONE": {"recall": 1.0}, "PHONE_NUMBER": {"recall": 0.0}},
        basis="detection",
    )

    assert len(block) == 1
    assert block["PHONE_NUMBER"]["recall"]["value"] in {0.0, 1.0}


def test_documents_contributing_travels_with_the_label():
    """A label seen in 2 of 15 documents must not read like one seen in all."""
    block = ranking._per_label_block(
        {"NAME": {"recall": 0.9, "documents_contributing": 2}}, basis="detection"
    )

    assert block["NAME"]["documents_contributing"] == 2


def test_the_legacy_recall_shape_is_derived_from_the_new_one():
    """Derived, not built separately, so the two cannot disagree."""
    block = ranking._per_label_block(
        {"NAME": {"recall": 0.9}, "DATE": {"recall": 0.4}}, basis="detection"
    )

    legacy = ranking._recall_only(block)
    assert legacy["NAME"] is block["NAME"]["recall"]
    assert legacy["DATE"]["value"] == 0.4


# --------------------------------------------------------------------- #
# The flat `<metric>__<LABEL>` shape of a qa-result document
# --------------------------------------------------------------------- #


class _Metric:
    """Stands in for `schemas.Metric`, attribute for attribute.

    `_per_label_from_flat_metrics` reads the three attributes and nothing
    else, so this proves it never depends on the model itself -- which is
    what lets `_metric_payload` keep an int `support` an int.
    """

    def __init__(self, value, available=True, reason=None):
        self.value = value
        self.available = available
        self.reason = reason


def test_flat_metrics_beyond_recall_are_recognised():
    """Only `recall__` was read before, so precision and support were ignored."""
    block = results._per_label_from_flat_metrics(
        {
            "recall__NAME": _Metric(0.9),
            "precision__NAME": _Metric(0.8),
            "support__NAME": _Metric(40),
            "pii_recall": _Metric(0.95),  # not per-label, must be ignored
        }
    )

    assert set(block) == {"NAME"}
    assert block["NAME"]["recall"]["value"] == 0.9
    assert block["NAME"]["precision"]["value"] == 0.8
    assert block["NAME"]["support"]["value"] == 40


def test_flat_metrics_normalise_their_label_too():
    block = results._per_label_from_flat_metrics({"recall__PHONE": _Metric(1.0)})

    assert "PHONE_NUMBER" in block
    assert block["PHONE_NUMBER"]["aliased_from"] == "PHONE"


def test_every_label_offers_the_same_keys_whatever_the_document_reported():
    """So the UI never has to tell "absent" apart from "unavailable"."""
    block = results._per_label_from_flat_metrics({"recall__NAME": _Metric(0.9)})

    entry = block["NAME"]
    for metric in results.PER_LABEL_PREFIXES:
        assert metric in entry
    assert entry["f1"]["available"] is False
    assert entry["basis"] == "qa-result"
