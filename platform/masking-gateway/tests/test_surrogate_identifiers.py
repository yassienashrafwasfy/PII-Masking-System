#!/usr/bin/env python3
"""A surrogate identifier has to pass the same check its original did.

A masked NHS number that fails `_nhs_ok` is a downgrade a reader can see: the
document says the value was replaced by another of its kind, and the
replacement is not one. Nothing downstream re-validates a surrogate, so the
generators are checked here against the same validators the detectors use.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.engines import masking_router, surrogate_numbers
from masking_engine.app.engines.surrogate_numbers import (
    _nhs_digits,
    _cnp_digits,
    _colegiado_digits,
    _dni_identifier,
    _lanr_digits,
    _nie_identifier,
    _npi_digits,
    _parafa_digits,
    _place_digits,
    _steuer_id_digits,
    _ssn_digits,
)
from masking_engine.app.engines.surrogates import _CHECKED_NUMBER_LABELS
from masking_engine.app.engines.regex_financial import _luhn_ok
from masking_engine.app.engines.regex_uk import _nhs_ok
from masking_engine.app.engines.regex_us import _ssn_ok
from masking_engine.app.engines.regex_germany import _lanr_ok, _steuer_id_ok
from masking_engine.app.engines.regex_romania import _cnp_ok
from masking_engine.app.engines.regex_spain import _dni_ok, _nie_ok

DRAWS = 300


def draws(generate, count: int = DRAWS) -> list[str]:
    generator = random.Random(20260906)
    return [generate(generator) for _ in range(count)]


def test_every_drawn_ssn_is_one_the_ssa_could_have_issued() -> None:
    values = draws(_ssn_digits)
    for value in values:
        assert len(value) == 9, value
        assert value.isdigit(), value
        assert _ssn_ok(value), value
    # And the draw actually varies, rather than returning one constant.
    assert len(set(values)) > DRAWS // 2


def test_the_never_issued_ssn_blocks_are_not_drawn_from() -> None:
    assert 0 not in surrogate_numbers._SSN_AREAS
    assert 666 not in surrogate_numbers._SSN_AREAS
    assert max(surrogate_numbers._SSN_AREAS) == 899
    assert min(surrogate_numbers._SSN_AREAS) == 1


def test_every_drawn_npi_passes_the_luhn_check_over_its_issuer_prefix() -> None:
    values = draws(_npi_digits)
    for value in values:
        assert len(value) == 10, value
        # The NPI check digit is Luhn over 80840 plus the number.
        assert _luhn_ok(f"80840{value}"), value
        # Allocation runs from 1 and 2.
        assert value[0] in "12", value
    assert len(set(values)) > DRAWS // 2


def test_every_drawn_nhs_number_passes_the_check_the_detector_applies() -> None:
    values = draws(_nhs_digits)
    for value in values:
        assert len(value) == 10, value
        assert _nhs_ok(value), value
        # No allocated range opens with a zero, and `_nhs_ok` rejects one.
        assert value[0] != "0", value
    assert len(set(values)) > DRAWS // 2


def test_a_draw_with_no_valid_check_digit_is_discarded_not_coerced() -> None:
    # The generator loops until it draws a body whose remainder leaves a check
    # digit; a coerced one would be a number `_nhs_ok` refuses.
    for value in draws(_nhs_digits, 60):
        body = value[:9]
        total = sum(int(char) * (10 - index) for index, char in enumerate(body))
        assert 11 - total % 11 != 10, value


def test_the_generators_are_deterministic_for_a_given_seed() -> None:
    for generate in (_ssn_digits, _npi_digits, _nhs_digits):
        first = generate(random.Random(7))
        assert generate(random.Random(7)) == first, generate


def test_each_checked_label_names_its_length_and_its_generator() -> None:
    assert _CHECKED_NUMBER_LABELS["SSN"] == (9, _ssn_digits)
    assert _CHECKED_NUMBER_LABELS["NPI"] == (10, _npi_digits)
    assert _CHECKED_NUMBER_LABELS["NHS_NUMBER"] == (10, _nhs_digits)
    for label, (length, generate) in _CHECKED_NUMBER_LABELS.items():
        assert len(generate(random.Random(3))) == length, label


def test_every_new_locale_identifier_passes_its_detector_check() -> None:
    for generate, validate in (
        (_cnp_digits, _cnp_ok),
        (_steuer_id_digits, _steuer_id_ok),
        (_lanr_digits, _lanr_ok),
        (_dni_identifier, _dni_ok),
        (_nie_identifier, _nie_ok),
    ):
        values = draws(generate)
        assert all(validate(value) for value in values)
        assert len(set(values)) > DRAWS // 2
    assert all(len(value) == 9 for value in draws(_colegiado_digits))
    assert all(len(value) == 5 for value in draws(_parafa_digits))


def test_replacing_the_digits_of_a_value_keeps_the_writer_s_separators() -> None:
    assert _place_digits("123-45-6789", "481027735") == "481-02-7735"
    assert _place_digits("123 45 6789", "481027735") == "481 02 7735"
    assert _place_digits("123456789", "481027735") == "481027735"
    assert _place_digits("", "") == ""
    # A non-digit anywhere is kept exactly where it was.
    assert _place_digits("BE 0123.456.749", "9876543210") == "BE 9876.543.210"


def test_a_surrogate_identifier_is_written_the_way_the_original_was() -> None:
    for label, (_, generate) in _CHECKED_NUMBER_LABELS.items():
        digits = generate(random.Random(11))
        original = f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
        placed = _place_digits(original, digits)
        assert placed == original, label
        assert [c.isdigit() for c in placed] == [c.isdigit() for c in original]


# --------------------------------------------------------------------------- #
# Generators are per thread
# --------------------------------------------------------------------------- #


def test_each_thread_builds_its_own_faker_once_per_locale() -> None:
    first = masking_router._faker_for("nl_BE")
    assert masking_router._faker_for("nl_BE") is first
    assert masking_router._faker_for("fr_FR") is not first
    seen: list[bool] = []

    import threading

    def other_thread() -> None:
        seen.append(masking_router._faker_for("nl_BE") is first)

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    # A generator shared across threads could take another document's seed
    # between this document's seed and its draw.
    assert seen == [False]
