"""The arena's vendored copy of the canonical PII taxonomy.

`labels.py` duplicates the gateway's `taxonomy.json` because the API image
does not ship `platform/` or `evaluation/config/`. Duplication is only safe
while something notices it drifting, which is what this file is for -- the
same job `platform/masking-gateway/tests/test_qa_label_schema.py` does for the
source file itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from leaderboard_api import labels

TAXONOMY_PATH = (
    Path(__file__).resolve().parents[2]
    / "platform"
    / "masking-gateway"
    / "src"
    / "masking_engine"
    / "app"
    / "core"
    / "taxonomy.json"
)


@pytest.fixture(scope="module")
def taxonomy():
    if not TAXONOMY_PATH.exists():
        pytest.skip("gateway taxonomy is not present in this checkout")
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def test_the_canonical_labels_match_the_gateway_exactly(taxonomy):
    """Order matters as well as membership -- it is the display order."""
    assert list(labels.CANONICAL_LABELS) == taxonomy["entity_labels"]


def test_the_vendored_copy_names_the_schema_it_copied(taxonomy):
    payload = labels.taxonomy_payload()
    assert payload["schema_name"] == taxonomy["schema_name"]
    assert payload["schema_version"] == taxonomy["schema_version"]


def test_routing_only_phi_labels_are_not_canonical(taxonomy):
    """MEDICATION/SYMPTOM/DIAGNOSIS are masked but not scored (ADR 006).

    They are masked through `routing_only_labels`, deliberately kept out of
    `entity_labels`, so promoting one to canonical -- an arena column, QA
    ground truth -- has to be a deliberate taxonomy change later, not a side
    effect of this one landing.
    """
    for label in ("MEDICATION", "SYMPTOM", "DIAGNOSIS"):
        assert label not in taxonomy["entity_labels"]
        assert label not in labels.CANONICAL_LABELS
        resolved, _, is_canonical = labels.normalize(label)
        assert is_canonical is False, resolved


def test_every_gateway_alias_resolves_the_same_way_here(taxonomy):
    """The arena may know extra spellings, but it must not disagree on one."""
    for native, canonical in taxonomy["aliases"].items():
        resolved, _, is_canonical = labels.normalize(native)
        assert is_canonical, f"{native} is an alias upstream but unknown here"
        assert resolved == canonical.upper(), f"{native} resolves differently"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PHONE_NUMBER", "PHONE_NUMBER"),
        ("phone", "PHONE_NUMBER"),
        ("phone number", "PHONE_NUMBER"),
        ("Telefoon", "PHONE_NUMBER"),
        # The legacy lowercase vocabulary in evaluation/config/entities.yaml
        # and the generated dataset manifests.
        ("national_id", "INSZ_NISS"),
        ("person_name", "NAME"),
        # The spelling the arena's own masking preview used before it was
        # corrected -- kept mapped so an older payload still resolves.
        ("PERSON", "NAME"),
    ],
)
def test_known_spellings_resolve_to_a_canonical_label(raw, expected):
    resolved, aliased_from, is_canonical = labels.normalize(raw)
    assert resolved == expected
    assert is_canonical


def test_an_exact_canonical_label_is_not_reported_as_aliased():
    """Otherwise the UI annotates every ordinary label as aliased from itself."""
    _, aliased_from, _ = labels.normalize("PHONE_NUMBER")
    assert aliased_from is None


def test_an_unknown_label_survives_flagged_rather_than_being_dropped():
    """A view whose purpose is showing weak entities must not hide a new one."""
    resolved, aliased_from, is_canonical = labels.normalize("MEDICATION_NAME")
    assert resolved == "MEDICATION_NAME"
    assert is_canonical is False
    assert aliased_from is None


def test_patient_id_is_not_silently_mapped_onto_the_prescriber_number():
    """entities.yaml marks patient_id critical and the taxonomy has no match.

    RIZIV_INAMI is the prescriber's number. Mapping one to the other would
    report the wrong entity as gated, so it stays unmapped until a human says
    what it should be.
    """
    resolved, _, is_canonical = labels.normalize("patient_id")
    assert resolved == "PATIENT_ID"
    assert is_canonical is False
    assert "PATIENT_ID" not in labels.CRITICAL_LABELS


def test_critical_labels_are_all_canonical():
    assert labels.CRITICAL_LABELS <= set(labels.CANONICAL_LABELS)


def test_display_names_do_not_mangle_acronyms():
    assert labels.display_name("INSZ_NISS") == "INSZ / NISS"
    assert labels.display_name("URL") == "URL"
    # An unknown label still gets a readable form rather than raising.
    assert labels.display_name("MEDICATION_NAME") == "Medication name"
