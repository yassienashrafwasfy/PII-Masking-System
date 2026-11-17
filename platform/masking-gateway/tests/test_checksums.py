#!/usr/bin/env python3
"""Every national and payment checksum, pinned by arithmetic rather than by example.

These functions decide whether a digit run is an identifier at all. A weakened
one accepts look-alikes -- which costs precision and, for `_insz_ok` and
`_nhs_ok`, suppresses real telephone numbers through `_is_national_identifier`
-- and a strengthened one drops a real identifier into the released document.
Neither shows up in a passing end-to-end test, so each validator is checked
against its own rule: a valid number, the shapes it must refuse, and the
property that no single wrong digit survives it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.engines.regex_belgium import _btw_ok, _insz_ok, _riziv_ok
from masking_engine.app.engines.regex_financial import _luhn_ok
from masking_engine.app.engines.regex_france import _nir_ok
from masking_engine.app.engines.regex_germany import _kvnr_ok
from masking_engine.app.engines.regex_identifiers import _is_national_identifier
from masking_engine.app.engines.regex_uk import _nhs_checksum_ok, _nhs_ok
from masking_engine.app.engines.regex_us import _ssn_ok


def digit_neighbours(value: str):
    """Every value one digit away from *value*."""
    for index, character in enumerate(value):
        if not character.isdigit():
            continue
        for digit in "0123456789":
            if digit != character:
                yield value[:index] + digit + value[index + 1 :]


def refuses_every_neighbour(validator, value: str) -> bool:
    return not any(validator(other) for other in digit_neighbours(value))


# --------------------------------------------------------------------------- #
# Belgium
# --------------------------------------------------------------------------- #

VALID_INSZ = ("50010368687", "80070560997", "41.05.12-509.56", "800705 609 97")


def test_a_national_register_number_is_accepted_however_it_is_written() -> None:
    for number in VALID_INSZ:
        assert _insz_ok(number), number


def test_a_national_register_number_needs_exactly_eleven_digits() -> None:
    assert not _insz_ok("5001036868")
    assert not _insz_ok("500103686870")
    assert not _insz_ok("")


def test_one_wrong_digit_breaks_a_national_register_number() -> None:
    assert refuses_every_neighbour(_insz_ok, "50010368687")
    assert refuses_every_neighbour(_insz_ok, "80070560997")


def test_the_embedded_birth_date_is_checked_as_well_as_the_checksum() -> None:
    # A real day of a real month passes...
    assert _insz_ok("50022968622")  # 29 February, taken unconditionally
    assert _insz_ok("50043068606")  # 30 April
    assert _insz_ok("50123168603")  # 31 December
    # ...and no check digit rescues a day that month never had.
    for nine in ("500230686", "500431686", "500132686", "501303686"):
        assert not any(_insz_ok(nine + f"{c:02d}") for c in range(100)), nine


def test_a_bis_number_offsets_its_month_by_twenty_or_forty() -> None:
    assert _insz_ok("50210368633")  # month 21 -> January
    assert _insz_ok("50410368608")  # month 41 -> January
    assert _insz_ok("50222968636")  # month 22 -> 29 February
    # 33 to 40 is neither a month nor a bis offset.
    assert not any(_insz_ok(f"503303686{c:02d}") for c in range(100))


def test_an_unknown_birth_date_is_written_as_zeroes_and_still_accepted() -> None:
    # A foreign or undocumented patient really does carry one of these, and
    # rejecting them left the leading digits of the number in the clear.
    assert _insz_ok("50000368612")  # month unknown
    assert _insz_ok("50010068612")  # day unknown
    assert _insz_ok("50000068605")  # both unknown


def test_a_zero_serial_is_not_a_register_number() -> None:
    assert not any(_insz_ok(f"500103000{c:02d}") for c in range(100))


def test_a_riziv_number_is_accepted_on_either_of_its_two_formulas() -> None:
    assert _riziv_ok("12345676")  # 89 - base % 89
    assert _riziv_ok("12345625")  # 97 - base % 97
    assert _riziv_ok("83474278")
    assert _riziv_ok("8-34742-78")
    # Eleven digits carry the same check in the same place.
    assert _riziv_ok("83474278981")
    assert not _riziv_ok("834742789")
    assert not _riziv_ok("8347427")
    assert refuses_every_neighbour(_riziv_ok, "83474278")


def test_an_enterprise_number_is_ten_digits_and_a_mod_97_check() -> None:
    assert _btw_ok("BE 0123.456.749")
    assert _btw_ok("0123456749")
    assert not _btw_ok("012345674")
    assert not _btw_ok("01234567490")
    assert refuses_every_neighbour(_btw_ok, "0123456749")


# --------------------------------------------------------------------------- #
# France, the United Kingdom, the United States, Germany
# --------------------------------------------------------------------------- #


def test_a_nir_is_fifteen_digits_and_a_mod_97_check() -> None:
    assert _nir_ok("269054958815780")
    assert _nir_ok("2 69 05 49 588 157 80")
    assert not _nir_ok("26905495881578")
    assert not _nir_ok("2690549588157800")
    assert refuses_every_neighbour(_nir_ok, "269054958815780")


def test_an_nhs_number_is_ten_digits_and_a_mod_11_check() -> None:
    assert _nhs_ok("9434765919")
    assert _nhs_ok("943 476 5919")
    assert not _nhs_ok("943476591")
    assert not _nhs_ok("94347659190")
    assert refuses_every_neighbour(_nhs_ok, "9434765919")


def test_a_remainder_with_no_valid_check_digit_is_refused() -> None:
    # A check of eleven means zero; a check of ten has no digit at all, so the
    # candidate is refused rather than coerced to something.
    assert _nhs_checksum_ok("4485179230")  # check 11 -> 0
    assert not _nhs_checksum_ok("6962892390")  # check 10 -> no such number
    assert not _nhs_checksum_ok("6962892391")
    # The mod-11 check strips its own separators, so it holds whether or not it
    # is reached through `_nhs_ok`, which strips them too.
    assert _nhs_checksum_ok("448 517 9230")
    assert not _nhs_checksum_ok("696 289 2390")


def test_a_leading_zero_is_not_an_allocated_nhs_number() -> None:
    # Without this rule one telephone number in eleven satisfies the checksum.
    assert not _nhs_ok("0252888742")
    for number in ("0000000000", "0123456789"):
        assert not _nhs_ok(number), number
    assert not _nhs_ok("025 288 8742")
    # `_nhs_ok` strips separators of its own before reading the first digit,
    # which is redundant twice over: `_nhs_checksum_ok` strips them again, and
    # every pattern that reaches here opens on a digit. It is kept as a
    # precondition on the argument, not as a rule any input exercises -- so its
    # mutants survive by equivalence and are not a gap to close.


def test_a_social_security_number_is_checked_against_what_was_never_issued() -> None:
    assert _ssn_ok("123-45-6789")
    assert _ssn_ok("123456789")
    assert not _ssn_ok("12345678")
    assert not _ssn_ok("1234567890")
    # The blocks the SSA has never issued.
    assert not _ssn_ok("000456789")  # area 000
    assert not _ssn_ok("666456789")  # area 666
    assert not _ssn_ok("900456789")  # 900-999 is a taxpayer number
    assert not _ssn_ok("999456789")
    assert not _ssn_ok("123006789")  # group 00
    assert not _ssn_ok("123450000")  # serial 0000
    # 899 is still an area the SSA issues.
    assert _ssn_ok("899456789")


def test_there_is_no_ssn_checksum_to_break() -> None:
    # Documented on purpose: the format gives no certainty, which is why an
    # SSN is not one of the schemes that suppress a phone number.
    accepted = sum(1 for other in digit_neighbours("123456789") if _ssn_ok(other))
    assert accepted > 70


def test_an_insurance_number_expands_its_letter_before_the_mod_10_check() -> None:
    assert _kvnr_ok("A123456780")
    assert _kvnr_ok("a123456780")  # the case of the letter does not matter
    assert _kvnr_ok("A 123 456 780")
    assert _kvnr_ok("A000000002")
    # The letter is required, and everything after it must be a digit.
    assert not _kvnr_ok("1123456780")
    assert not _kvnr_ok("AB23456780")
    assert not _kvnr_ok("A12345678")
    assert not _kvnr_ok("A1234567890")
    assert refuses_every_neighbour(_kvnr_ok, "A123456780")


def test_the_insurance_letter_is_part_of_the_sum() -> None:
    # The letter expands to 01 through 26 and is weighted like any other pair,
    # so swapping it usually breaks the check. Three of the twenty-six letters
    # expand to the same weighted residue and so share a check digit -- that is
    # the mod-10 scheme's own resolution, not a gap in this test.
    assert not _kvnr_ok("B123456780")
    letters = [chr(code) for code in range(65, 91) if _kvnr_ok(f"{chr(code)}123456780")]
    assert letters == ["A", "O", "T"]


# --------------------------------------------------------------------------- #
# Payment cards
# --------------------------------------------------------------------------- #


def test_a_card_number_is_checked_with_luhn() -> None:
    for number in ("4111111111111111", "5500005555555559", "371449635398431"):
        assert _luhn_ok(number), number
    assert refuses_every_neighbour(_luhn_ok, "4111111111111111")
    assert refuses_every_neighbour(_luhn_ok, "371449635398431")


def test_luhn_does_not_bound_the_length_it_is_given() -> None:
    # The pattern in front of it decides how many digits a card may have; this
    # function answers only the arithmetic question.
    assert _luhn_ok("00")
    assert _luhn_ok("")


# --------------------------------------------------------------------------- #
# The union that keeps a national number out of the phone detector
# --------------------------------------------------------------------------- #


def test_a_number_any_scheme_accepts_is_a_national_identifier() -> None:
    for number in ("50010368687", "83474278", "269054958815780", "9434765919"):
        assert _is_national_identifier(number), number


def test_a_number_no_scheme_accepts_is_not_one() -> None:
    for number in ("0472502961", "123456789", "00000000000", ""):
        assert not _is_national_identifier(number), number
    # An SSN deliberately stays out of the union: the test is too weak, and
    # including it would suppress genuine phone numbers by the dozen.
    assert _ssn_ok("123456789")
    assert not _is_national_identifier("123456789")


# --------------------------------------------------------------------------- #
# Organisation identifiers, which are a country prefix and a digit count
# --------------------------------------------------------------------------- #

from masking_engine.app.engines.regex_identifiers import (  # noqa: E402
    _VAT_DIGITS,
    _organization_id_ok,
)


def test_each_country_prefix_carries_its_own_digit_count() -> None:
    assert _VAT_DIGITS == {"BE": 10, "GB": 9, "DE": 9, "ES": 9, "FR": 9, "RO": 9}
    for country, count in _VAT_DIGITS.items():
        body = "0123456749" if country == "BE" else "1" * count
        assert _organization_id_ok(country, body), country
        assert not _organization_id_ok(country, body + "1"), country
        assert not _organization_id_ok(country, body[:-1]), country


def test_a_country_this_deployment_does_not_serve_is_refused() -> None:
    for country in ("US", "IT", "NL", ""):
        assert not _organization_id_ok(country, "123456789"), country


def test_the_prefix_is_read_without_regard_to_case() -> None:
    assert _organization_id_ok("be", "0123456749")
    assert _organization_id_ok("gb", "123456789")


def test_only_belgium_carries_a_checksum() -> None:
    assert _organization_id_ok("BE", "0123456749")
    assert not _organization_id_ok("BE", "0123456748")
    # The separators a writer uses carry nothing.
    assert _organization_id_ok("BE", "0123.456.749")
    assert _organization_id_ok("BE", "0123 456 749")
    # Nine digits with no checksum of their own: any run of the right length.
    assert _organization_id_ok("GB", "999999999")


def admits_date(month: int, day: int, serial: int = 686) -> bool:
    """Whether any check digit makes YY=50, *month*, *day* a register number.

    The check value is a function of the nine digits in front of it, so a
    number is refused for its date only when no check digit rescues it.
    """
    nine = f"50{month:02d}{day:02d}{serial:03d}"
    return any(_insz_ok(nine + f"{check:02d}") for check in range(100))


MONTH_LENGTHS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def test_each_month_admits_its_own_last_day_and_no_more() -> None:
    # February takes 29 unconditionally: the year is two digits, so the century
    # is unknown and a leap-year test would reject a real number.
    for month, length in enumerate(MONTH_LENGTHS, start=1):
        assert admits_date(month, length), month
        assert not admits_date(month, length + 1), month


def test_a_bis_month_is_offset_by_exactly_twenty_or_forty() -> None:
    # 22 is February, so it admits 29 days and not 30. Offsetting by 21 or 41
    # would make it January and admit 31.
    assert admits_date(22, 29)
    assert not admits_date(22, 30)
    assert admits_date(42, 29)
    assert not admits_date(42, 30)
    # The far end of each bis range is still a month.
    assert admits_date(32, 31)  # 32 - 20 = December
    assert admits_date(52, 31)  # 52 - 40 = December
    # And the gap between the two ranges is not.
    for month in (13, 20, 33, 40, 53, 99):
        assert not admits_date(month, 1), month


def test_an_unknown_month_still_bounds_the_day_at_thirty_one() -> None:
    assert admits_date(0, 31)
    assert not admits_date(0, 32)
    assert admits_date(0, 0)
