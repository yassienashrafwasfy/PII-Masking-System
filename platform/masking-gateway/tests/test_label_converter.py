#!/usr/bin/env python3
"""Pin label conversion: three vocabularies in, one taxonomy out.

Conversion runs before orchestration, so a label that fails to convert is not
merely mislabelled -- the orchestrator arbitrates on canonical labels, and a
span still carrying a team's own name cannot be recognised by the rule that
would have suppressed or kept it.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core import label_registry
from masking_engine.app.core.schemas import MaskSpan
from masking_engine.app.engines.label_converter import LabelConverter, UnknownLabelError

PROFILES = ("octo", "synthify", "berts")


def span(label: str, page: int | None = None) -> MaskSpan:
    return MaskSpan(0, 5, label, "model", page=page)


def convert(profile: str, label: str, text: str = "Peter") -> list[str]:
    return [s.label for s in LabelConverter(profile).convert(span(label), text)]


def test_only_a_profile_the_registry_knows_can_be_asked_for() -> None:
    for profile in PROFILES:
        assert LabelConverter(profile).profile == profile
    try:
        LabelConverter("nobody")
    except ValueError as error:
        assert "Unknown label profile: nobody" == str(error)
    else:
        raise AssertionError("built a converter for an unknown profile")


def test_every_profile_alias_maps_onto_the_taxonomy() -> None:
    registry = label_registry.profiles()["profiles"]
    for profile in PROFILES:
        converter = LabelConverter(profile)
        for native, canonical in registry[profile]["aliases"].items():
            assert converter.canonical_label(native) == canonical.upper(), (
                profile,
                native,
            )


def test_a_taxonomy_name_needs_no_alias() -> None:
    converter = LabelConverter("octo")
    assert converter.canonical_label("PHONE_NUMBER") == "PHONE_NUMBER"
    assert converter.canonical_label("phone") == "PHONE_NUMBER"
    assert converter.canonical_label("Telefoon") == "PHONE_NUMBER"
    # A profile alias is matched on the normalised name, so the separators and
    # the case a team writes it in do not matter.
    assert converter.canonical_label("hospital-name") == "ORGANIZATION_NAME"
    assert converter.canonical_label("Hospital Name") == "ORGANIZATION_NAME"


def test_a_profile_entry_wins_over_the_taxonomy_alias_of_the_same_name() -> None:
    # A team may bind a name this repository already uses to another label.
    octo = LabelConverter("octo")
    assert octo.canonical_label("CITY") == "ADDRESS"
    assert octo.canonical_label("ORGANIZATION_ID") == "BTW_EENHEID"


def test_a_composite_address_stays_one_span() -> None:
    # Synthify and Berts emit a whole address; the QA schema keeps it whole.
    for profile in PROFILES:
        assert convert(profile, "ADDRESS") == ["ADDRESS"]
        assert convert(profile, "address") == ["ADDRESS"]


def test_a_label_outside_the_taxonomy_is_dropped_rather_than_invented() -> None:
    # A legacy checkpoint may still emit a retired class; it must not create a
    # new output label.
    assert convert("octo", "SOMETHING_RETIRED") == []


def test_clinical_phi_survives_conversion_for_every_team_that_emits_it() -> None:
    """MEDICATION/SYMPTOM/DIAGNOSIS mask through routing_only_labels (ADR 006).

    Octo's checkpoint emits the canonical spelling directly. Berts' three
    checkpoints emit `DISEASE`, which the `berts` profile aliases onto the
    taxonomy's `DIAGNOSIS` -- the same per-profile alias pattern as
    `PROVIDER_ID -> RIZIV_INAMI`. PROCEDURE and Octo's LAB_RESULT were
    dropped until ADR 007 gave them canonical targets (PROCEDURE and
    TESTRESULTS) so every team that emits a clinical class masks the same set.
    """
    assert convert("octo", "MEDICATION") == ["MEDICATION"]
    assert convert("octo", "SYMPTOM") == ["SYMPTOM"]
    assert convert("octo", "DIAGNOSIS") == ["DIAGNOSIS"]
    assert convert("octo", "PROCEDURE") == ["PROCEDURE"]
    assert convert("octo", "LAB_RESULT") == ["TESTRESULTS"]

    assert convert("berts", "MEDICATION") == ["MEDICATION"]
    assert convert("berts", "SYMPTOM") == ["SYMPTOM"]
    assert convert("berts", "DISEASE") == ["DIAGNOSIS"]
    assert convert("berts", "PROCEDURE") == ["PROCEDURE"]


def test_berts_artifact_labels_are_exactly_four_phi_classes_and_six_fields() -> None:
    """Drive the mapping off the committed checkpoints, not a hand-kept list.

    Berts is the team whose clinical head decides whether a medication, a
    symptom or a diagnosis leaves the document, and it is also the team whose
    third clinical class (`PROCEDURE`) has no canonical target. Reading
    `id2label` out of every shipped graph is what makes this catch a
    re-exported checkpoint that renamed a class or added a fourth one: the
    assertion is on the artifact's own spellings, one BIO prefix stripped.
    """
    native: set[str] = set()
    for directory in ("onnx", "onnx-en", "onnx-fr"):
        config = json.loads(
            (ROOT / "teams" / "berts" / directory / "config.json").read_text(
                encoding="utf-8"
            )
        )
        native.update(
            label.split("-", 1)[1]
            for label in config["id2label"].values()
            if label != "O"
        )

    converted = {label for value in native for label in convert("berts", value)}
    identity = {
        "ADDRESS",
        "AGE",
        "DATE",
        "NAME",
        "ORGANIZATION_NAME",
        "PHONE_NUMBER",
    }
    phi = {"MEDICATION", "SYMPTOM", "DIAGNOSIS", "PROCEDURE"}
    assert converted == identity | phi, sorted(converted)
    # Not merely "these survive": nothing else may. A fifth clinical class added
    # to a checkpoint later must land here rather than at a masked document.
    assert converted & set(label_registry.profiles()["routing_only_labels"]) == phi


def test_octo_locale_identifiers_survive_conversion() -> None:
    for label in ("CNP", "COLEGIADO", "DNI", "LANR", "NIE", "PARAFA", "STEUER_ID"):
        assert convert("octo", label) == [label]


def test_an_unmapped_label_is_logged_by_name_and_never_by_its_text(caplog) -> None:
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.label_converter")
    LabelConverter("octo").convert(span("SOMETHING_RETIRED"), "Peter Janssens")
    messages = [record.getMessage() for record in caplog.records]
    assert "label_conversion_unmapped profile=octo label=SOMETHINGRETIRED" in messages
    assert "label_conversion_dropped profile=octo label=SOMETHING_RETIRED" in messages
    assert not any("Peter" in message for message in messages)


def test_strict_mode_refuses_what_permissive_mode_logs() -> None:
    strict = LabelConverter("octo", strict=True)
    try:
        strict.canonical_label("SOMETHING_RETIRED")
    except UnknownLabelError as error:
        assert "Profile octo has no entry for label SOMETHINGRETIRED" == str(error)
    else:
        raise AssertionError("accepted a label no profile covers")
    # A label the profile does cover is unaffected by strictness.
    assert strict.canonical_label("CITY") == "ADDRESS"


def test_conversion_keeps_everything_about_a_span_except_its_label() -> None:
    original = MaskSpan(12, 20, "CITY", "model", page=3)
    (converted,) = LabelConverter("octo").convert(original, "Gent")
    assert converted.label == "ADDRESS"
    assert (converted.start, converted.end, converted.source, converted.page) == (
        12,
        20,
        "model",
        3,
    )


def test_a_batch_is_converted_span_by_span_against_its_own_page(caplog) -> None:
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.label_converter")
    spans = [span("CITY", page=0), span("SOMETHING_RETIRED", page=1), span("phone")]
    texts = {0: "Gent", 1: "Retired", None: "0472 50 29 61"}
    converted = LabelConverter("octo").convert_all(spans, texts)
    assert [s.label for s in converted] == ["ADDRESS", "PHONE_NUMBER"]
    assert "label_conversion profile=octo in=3 out=2" in [
        record.getMessage() for record in caplog.records
    ]


def test_an_empty_batch_converts_to_nothing() -> None:
    assert LabelConverter("octo").convert_all([], {}) == []


def test_a_span_on_a_page_with_no_text_still_converts() -> None:
    # `convert` reads the text only for composite addresses; a missing page
    # must not lose the span.
    converted = LabelConverter("octo").convert_all([span("CITY", page=9)], {})
    assert [s.label for s in converted] == ["ADDRESS"]
