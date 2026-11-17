#!/usr/bin/env python3
"""Regression checks for the QA-approved Octo label schema."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import LabelConverter, find_regex_spans
from masking_engine import label_registry as registry

EXPECTED_LABELS = [
    "NAME",
    "GENDER",
    "DATE",
    "ORGANIZATION_NAME",
    "ADDRESS",
    "AGE",
    "PHONE_NUMBER",
    "BTW_EENHEID",
    "RIZIV_INAMI",
    "RPPS",
    "INSZ_NISS",
    "NIR",
    "URL",
    "EMAIL",
    "SSN",
    "NPI",
    "NHS_NUMBER",
    "GMC",
]


def _labelled(text: str) -> set[tuple[str, str]]:
    """The (label, matched value) pairs the regex engine finds in *text*."""
    return {
        (span.label, text[span.start : span.end]) for span in find_regex_spans(text)
    }


def _labels(text: str) -> set[str]:
    return {span.label for span in find_regex_spans(text)}


def test_the_taxonomy_is_exactly_the_approved_list() -> None:
    assert registry.taxonomy()["entity_labels"] == EXPECTED_LABELS


def test_clinical_phi_is_masked_but_not_canonical() -> None:
    """MEDICATION/SYMPTOM/DIAGNOSIS mask through routing_only_labels (ADR 006).

    They must never join `entity_labels` -- that would bump `schema_version`
    and move the six vendored copies of the label list with it -- while still
    reaching `masked_labels()`, which is what stops the converter dropping
    Octo's own MEDICATION/SYMPTOM/DIAGNOSIS spans.
    """
    assert registry.taxonomy()["schema_version"] == 4
    phi_labels = {"MEDICATION", "SYMPTOM", "DIAGNOSIS"}
    assert phi_labels.isdisjoint(registry.taxonomy()["entity_labels"])
    assert phi_labels <= registry.masked_labels()
    assert phi_labels <= set(registry.profiles()["routing_only_labels"])


def test_the_octo_converter_maps_its_legacy_names() -> None:
    converter = LabelConverter("octo")
    assert converter.canonical_label("HOSPITAL_NAME") == "ORGANIZATION_NAME"
    assert converter.canonical_label("TELEFOON") == "PHONE_NUMBER"
    assert converter.canonical_label("CITY") == "ADDRESS"
    assert converter.canonical_label("INSZ") == "INSZ_NISS"


def test_a_mixed_line_reports_only_approved_labels() -> None:
    sample = (
        "BTW BE0123456749 RIZIV 19719628 INSZ 810922-237-91 "
        "tel +32 470 12 34 56 test@example.org https://example.org"
    )
    labels = _labels(sample)
    assert {
        "BTW_EENHEID",
        "RIZIV_INAMI",
        "INSZ_NISS",
        "PHONE_NUMBER",
        "EMAIL",
        "URL",
    } <= labels
    assert labels <= set(EXPECTED_LABELS)


def test_a_keyword_rescues_an_insz_whose_checksum_failed() -> None:
    # Recall-first ruling (2026-08-25): a separated INSZ with a failed
    # checksum is still masked when the explicit keyword sits next to it.
    assert _labelled("INSZ 810922-237-90") == {("INSZ_NISS", "810922-237-90")}


def test_a_bare_insz_still_needs_its_checksum() -> None:
    assert not find_regex_spans("81092223790")
    assert _labelled("81092223791") == {("INSZ_NISS", "81092223791")}


def test_the_english_identifiers_reach_the_output() -> None:
    # NHS and GMC were detected and then dropped by `_UNMAPPED_OUTPUT_LABELS`
    # until taxonomy v4; SSN and NPI are new rules. All four must reach the
    # output, and none may claim a value it has no evidence for.
    english = (
        "SSN 123-45-6789 NPI 1023456789 "
        "NHS number 485 777 3456 GMC reference A1234567"
    )
    found = _labelled(english)
    assert found >= {
        ("SSN", "123-45-6789"),
        ("NPI", "1023456789"),
        ("NHS_NUMBER", "485 777 3456"),
        ("GMC", "A1234567"),
    }, found


def test_an_unallocatable_ssn_area_is_not_claimed() -> None:
    # A bare 3-2-4 run is distinctive enough to claim without a keyword, but
    # the allocation rules still apply: no area 000, 666, or 900-999.
    assert _labels("123-45-6789") == {"SSN"}
    for placeholder in ("000-45-6789", "666-45-6789", "900-45-6789", "123-00-6789"):
        assert "SSN" not in _labels(placeholder), placeholder


def test_npi_is_only_reported_next_to_its_keyword() -> None:
    # A bare ten-digit run is the NHS rule's territory, and a rule without a
    # keyword would spend its life arbitrating with it.
    assert "NPI" not in _labels("1023456789")


def test_an_american_address_line_is_masked_whole() -> None:
    # The house number comes *first* in English, where the Dutch and French
    # registers put it last, so `carries_postcode` has to recognise a ZIP
    # before `_ADDRESS_LEADING_NUMBER_RE` ever runs -- without that, "482"
    # stayed in the released document while the street, city and ZIP around
    # it were masked.
    american = (
        "482 Lakeshore Drive, Chicago, IL 60601        Tel. +1 (312) 555-0142\n"
        "27 Meridian Court, Oak Park, IL 60302          Tel. (708) 555-0176"
    )
    found = _labelled(american)
    assert found >= {
        ("ADDRESS", "482"),
        ("ADDRESS", "60601"),
        ("ADDRESS", "27"),
        ("ADDRESS", "60302"),
        ("PHONE_NUMBER", "+1 (312) 555-0142"),
    }, found


def test_two_capitals_alone_do_not_make_a_postcode() -> None:
    # The state is checked against the real list: five digits are a year or a
    # quantity far more often than a postcode.
    assert "ADDRESS" not in _labels("Ward ER 60601 was refurbished, see note")


def test_separators_do_not_hide_a_belgian_identifier() -> None:
    formatted = "BTW BE 0123.456.749 provider 1.97196.28 INSZ 810922.237-91"
    assert _labelled(formatted) >= {
        ("BTW_EENHEID", "BE 0123.456.749"),
        ("RIZIV_INAMI", "1.97196.28"),
        ("INSZ_NISS", "810922.237-91"),
    }


def test_a_btw_number_needs_its_checksum_and_its_own_prefix() -> None:
    assert "BTW_EENHEID" not in _labels("BTW BE 0123.456.740")
    assert "BTW_EENHEID" not in _labels("XBE0123456749")


def test_riziv_passes_on_shape_in_any_spelling() -> None:
    # Any-separator RIZIV spellings pass on shape alone -- there is no
    # checksum to run.
    assert _labelled("RIZIV 1/21941/78/004") == {("RIZIV_INAMI", "1/21941/78/004")}
    assert ("RIZIV_INAMI", "1.23456.78.901") in _labelled(
        "verzekerde 1.23456.78.901 dossier"
    )


def test_a_calendar_shape_stays_a_date_unless_riziv_is_written_beside_it() -> None:
    assert _labels("geboorte 20/01/1967 en 24/08/2026") == {"DATE"}
    assert ("RIZIV_INAMI", "20/01/1967") in _labelled("RIZIV 20/01/1967")


def test_no_two_different_labels_share_characters() -> None:
    mixed = find_regex_spans(
        "RIZIV 19719628 INSZ 810922-237-90 tel 09-11-67-80 geb 20/01/1967 "
        "test@example.org https://example.org BE 0123.456.749"
    )
    ordered = sorted(mixed, key=lambda span: span.start)
    for first, second in zip(ordered, ordered[1:]):
        if first.label != second.label:
            assert (
                first.end <= second.start
            ), f"cross-label overlap: {first} vs {second}"


def test_the_french_pair_has_a_canonical_target() -> None:
    # Both were detected long before they were reportable: the regex engine
    # dropped them in `_UNMAPPED_OUTPUT_LABELS` and the converter dropped the
    # model's own NIR/RPPS classes for want of a canonical target. Schema 3
    # made them canonical, so both paths now report them.
    converter = LabelConverter("octo")
    assert converter.canonical_label("ADELI") == "RPPS"
    assert converter.canonical_label("INSEE") == "NIR"
    assert converter.canonical_label("NSS") == "NIR"


def test_a_nir_needs_its_key_unless_the_keyword_is_there() -> None:
    # A NIR is fifteen digits carrying a mod-97 key over the first thirteen,
    # so a bare run needs the checksum while an explicit keyword does not.
    nir = "2 19 07 85 191 050 27"
    assert ("NIR", nir) in _labelled(f"NIR {nir}")
    assert _labels("219078519105027") == {"NIR"}
    # One digit changed breaks the key, and a bare run without context stays
    # unmasked rather than being guessed at.
    assert not find_regex_spans("219078519105028")


def test_rpps_is_only_reported_next_to_its_keyword() -> None:
    # RPPS carries no checksum. ADELI is detected by the same rule and
    # reported as RPPS, which is the alias the taxonomy records for it.
    for text in ("RPPS 10003411442", "ADELI 759912345"):
        assert _labels(text) == {"RPPS"}, text
    assert not find_regex_spans("10003411442")


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()
