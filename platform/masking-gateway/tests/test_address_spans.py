#!/usr/bin/env python3
"""Postcodes and house numbers, pinned against the slot each one lives in.

A bare four-digit run is a year, a quantity and a reference number far more
often than a postcode, and a bare small number is a room or a ward. Both are
claimed only for where they sit, so the tests here are about the shape of the
line rather than about the number.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.engines.regex_address import (
    _US_STATES,
    _building_number_spans,
    _postcode_spans,
)


def postcodes(text: str) -> list[str]:
    return [text[s.start : s.end] for s in _postcode_spans(text)]


def buildings(text: str) -> list[str]:
    return [text[s.start : s.end] for s in _building_number_spans(text)]


# (address line, postcodes, house numbers)
ADDRESSES = (
    ("Kerkplein 59, 3271 Averbode", ["3271"], ["59"]),
    ("62 allee du Marche, 95190 Goussainville", ["95190"], ["62"]),
    ("482 Lakeshore Drive, Chicago, IL 60601", ["60601"], ["482"]),
    ("Baker Street 221B, London NW1 6XE", ["NW1 6XE"], ["221B"]),
    ("Rue Neuve 12bis, 1000 Bruxelles", ["1000"], ["12bis"]),
    ("Straat 12 ter, 9000 Gent", ["9000"], ["12 ter"]),
    # A long address wraps and leaves the postcode on the next line.
    (
        "Springfield Street 43, Preston upon the Weald Moors\nTF6 6WH",
        ["TF6 6WH"],
        ["43"],
    ),
)

# A number that carries no postcode anywhere near it is not an address.
NOT_ADDRESSES = (
    "Room 214, second floor",
    "Procedure code 145.30, urgent",
    "Bed 12, ward 3",
    "Referentie 4821, dossier gesloten",
)


def test_an_address_line_yields_its_postcode_and_its_house_number() -> None:
    for line, expected_postcodes, expected_numbers in ADDRESSES:
        assert postcodes(line) == expected_postcodes, line
        assert buildings(line) == expected_numbers, line


def test_a_number_with_no_postcode_beside_it_is_not_a_house_number() -> None:
    for line in NOT_ADDRESSES:
        assert buildings(line) == [], line
        assert postcodes(line) == [], line


def test_a_uk_postcode_stands_alone() -> None:
    assert postcodes("Address is NW1 6XE indeed") == ["NW1 6XE"]
    # Both the standalone rule and the cue rule claim it; the engine dedupes.
    assert set(postcodes("Postcode SW1A1AA")) == {"SW1A1AA"}
    # The inward code excludes the letters the specification forbids.
    assert postcodes("Reference NW1 6CE here") == []
    assert postcodes("Reference NW1 6IE here") == []


def test_a_european_postcode_needs_the_slot_an_address_puts_it_in() -> None:
    # After a comma, or at the start of a line, and before a place name.
    assert postcodes("Kerkstraat 12, 9000 Gent") == ["9000"]
    assert postcodes("Vestiging\n2018 Antwerpen") == ["2018"]
    assert postcodes("9000 Gent") == ["9000"]
    # Not in the middle of a sentence.
    assert postcodes("Er waren 9000 patienten dat jaar.") == []
    assert postcodes("Bestelbon 2019 werd afgesloten") == []


def test_a_year_written_after_a_comma_is_not_a_postcode() -> None:
    # "April 14th, 2023" puts a bare four-digit year exactly where a Belgian
    # postcode sits, and every one of them was being masked as an address.
    for text in (
        "Vergaderd op April 14th, 2023 in Gent",
        "Signed March 1, 2019 Brussels",
    ):
        assert postcodes(text) == [], text


def test_a_us_zip_needs_a_real_state_in_front_of_it() -> None:
    assert postcodes("Chicago, IL 60601") == ["60601"]
    assert postcodes("Chicago, IL 60601-1234") == ["60601-1234"]
    # A two-letter prefix alone would have claimed "ER 60601" out of prose.
    assert postcodes("Seen in ER 60601 today") == []
    assert len(_US_STATES) == 56
    assert "IL" in _US_STATES and "PR" in _US_STATES


def test_a_cue_word_claims_whatever_shape_follows_it() -> None:
    assert postcodes("Postcode: 9000") == ["9000"]
    assert postcodes("ZIP code 90210-1234") == ["90210-1234"]
    assert postcodes("Code postal 75008") == ["75008"]
    assert postcodes("Postal code B-1000") == ["B-1000"]
    # Trailing separators are trimmed off the claim.
    assert postcodes("Postcode: 9000 - ") == ["9000"]


def test_a_house_number_is_read_from_whichever_end_holds_it() -> None:
    # nl-BE and en-GB write it after the street, fr-FR before it.
    assert buildings("Kerkplein 59, 3271 Averbode") == ["59"]
    assert buildings("62 allee du Marche, 95190 Goussainville") == ["62"]
    # Only one is claimed even when both ends carry a number.
    assert buildings("12 Kerkplein 59, 3271 Averbode") == ["12"]


def test_a_house_number_may_carry_one_letter_or_a_bis_marker() -> None:
    assert buildings("Straat 12A, 9000 Gent") == ["12A"]
    assert buildings("Straat 12bis, 9000 Gent") == ["12bis"]
    assert buildings("Straat 12 ter, 9000 Gent") == ["12 ter"]
    # Two letters is not a house-number suffix.
    assert buildings("Straat 12AB, 9000 Gent") == []


def test_a_line_that_is_not_an_address_line_yields_nothing() -> None:
    # No comma at all, so there is no street part and no postcode part.
    assert buildings("Kerkplein 59 3271 Averbode") == []
    # A comma inside prose is bounded out by the length limits.
    assert buildings("x" * 80 + ", 9000 Gent") == []
