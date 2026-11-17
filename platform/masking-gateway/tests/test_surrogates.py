#!/usr/bin/env python3
"""Pin each surrogate strategy against the one thing it must never do.

A surrogate that returns its own input leaves the value in the released
document while the report says it was replaced, and that is invisible to every
test that only counts spans. `SurrogateContext` is the whole dependency
surface, so every case here is built by hand rather than through the router.
"""

from __future__ import annotations

import logging
import random
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core.locales import SUPPORTED_LOCALES, profile
from masking_engine.app.engines import surrogates
from masking_engine.app.engines.surrogate_dates import parse_date
from masking_engine.app.engines import surrogate_pools
from masking_engine.app.engines.surrogate_pools import VOCABULARY_LABELS, vocabulary_for
from masking_engine.app.engines.surrogates import (
    SurrogateContext,
    from_vocabulary,
    redraw,
    resolve,
    surrogate_age,
    surrogate_checked_number,
    surrogate_national_number,
    surrogate_organization,
)

LOCALE = profile("nl_BE")


def context(
    label: str,
    original: str,
    *,
    seed: int = 3,
    locale: str = "nl_BE",
    pool: tuple[str, ...] = (),
) -> SurrogateContext:
    return SurrogateContext(
        label=label,
        original=original,
        locale=profile(locale),
        organization_pool=pool,
        random=lambda: random.Random(seed),
        faker=lambda: (_ for _ in ()).throw(AssertionError("faker not expected")),
        date_of=lambda text: parse_date(text, profile(locale)),
    )


# --------------------------------------------------------------------------- #
# Redrawing
# --------------------------------------------------------------------------- #


def test_redrawing_keeps_the_separators_and_the_shape() -> None:
    drawn = redraw(context("RIZIV_INAMI", "1-23456-78-901"), letters=False)
    assert drawn != "1-23456-78-901"
    assert [c.isdigit() for c in drawn] == [c.isdigit() for c in "1-23456-78-901"]
    assert drawn.count("-") == 3


def test_redrawing_letters_keeps_their_case_and_leaves_the_rest() -> None:
    drawn = redraw(context("SOMETHING", "Ab-9 cD"), letters=True)
    assert drawn != "Ab-9 cD"
    assert drawn[0].isupper() and drawn[1].islower()
    assert drawn[2] == "-" and drawn[4] == " "
    assert drawn[3].isdigit()
    assert drawn[5].islower() and drawn[6].isupper()


def test_without_letters_only_the_digits_move() -> None:
    drawn = redraw(context("SOMETHING", "AB12cd"), letters=False)
    assert drawn[:2] == "AB" and drawn[4:] == "cd"
    assert drawn[2:4].isdigit()


def test_redrawing_is_deterministic_for_one_seed() -> None:
    assert redraw(context("X", "12345", seed=9), letters=False) == redraw(
        context("X", "12345", seed=9), letters=False
    )
    assert redraw(context("X", "12345", seed=9), letters=False) != redraw(
        context("X", "12345", seed=10), letters=False
    )


# --------------------------------------------------------------------------- #
# Ages
# --------------------------------------------------------------------------- #


def test_an_age_is_redrawn_run_by_run_and_never_returned_unchanged() -> None:
    for seed in range(40):
        for original in ("7", "42", "1-2-3", "104"):
            drawn = surrogate_age(context("AGE", original, seed=seed))
            assert drawn != original, (seed, original)
            # Every run keeps its width, so a wrapped PDF span still slices.
            assert [len(run) for run in re.findall(r"\d+", drawn)] == [
                len(run) for run in re.findall(r"\d+", original)
            ], (seed, original)


def test_no_redrawn_age_run_opens_with_a_zero() -> None:
    for seed in range(60):
        drawn = surrogate_age(context("AGE", "42", seed=seed))
        assert not drawn.startswith("0"), seed
        assert 10 <= int(drawn) <= 99, seed


def test_a_one_digit_age_stays_one_digit_and_stays_non_zero() -> None:
    for seed in range(60):
        drawn = surrogate_age(context("AGE", "7", seed=seed))
        assert drawn.isdigit() and len(drawn) == 1
        assert 1 <= int(drawn) <= 9


def test_an_age_span_with_no_digits_is_a_detector_error_and_is_redrawn() -> None:
    drawn = surrogate_age(context("AGE", "bejaard"))
    assert drawn != "bejaard"
    assert drawn.isalpha() and len(drawn) == len("bejaard")


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #


def test_a_national_number_is_never_returned_unchanged() -> None:
    for seed in range(30):
        drawn = surrogate_national_number(
            context("INSZ_NISS", "50010368687", seed=seed)
        )
        assert drawn != "50010368687", seed


def test_a_national_number_keeps_its_own_scheme_in_every_locale() -> None:
    from masking_engine.app.engines.regex_belgium import _insz_ok
    from masking_engine.app.engines.regex_france import _nir_ok

    # The cue word names the scheme, so the surrogate keeps it whatever the
    # document's language is -- and satisfies the same check the detector did.
    for locale in SUPPORTED_LOCALES:
        assert _insz_ok(
            surrogate_national_number(
                context("INSZ_NISS", "50010368687", locale=locale)
            )
        ), locale
        assert _nir_ok(
            surrogate_national_number(
                context("NIR", "2 69 05 49 588 157 80", locale=locale)
            )
        ), locale


def test_a_checked_number_of_the_wrong_width_is_redrawn_not_replaced() -> None:
    # Not the digit count the label is defined by: a detector error over some
    # other value, so it must not be given a valid identifier it never had.
    drawn = surrogate_checked_number(context("NHS_NUMBER", "12345"))
    assert drawn != "12345" and len(drawn) == 5


def test_every_checked_label_produces_a_number_that_passes_its_own_check() -> None:
    from masking_engine.app.engines.regex_uk import _nhs_ok
    from masking_engine.app.engines.regex_us import _ssn_ok

    for seed in range(20):
        assert _nhs_ok(
            surrogate_checked_number(context("NHS_NUMBER", "9434765919", seed=seed))
        )
        assert _ssn_ok(
            surrogate_checked_number(context("SSN", "123-45-6789", seed=seed))
        )


# --------------------------------------------------------------------------- #
# Organisations and vocabularies
# --------------------------------------------------------------------------- #


POOL = ("Sint-Jan", "Sint-Lucas", "Maria Middelares", "AZ Groeninge")


def test_an_organisation_is_drawn_from_the_configured_pool() -> None:
    for seed in range(20):
        drawn = surrogate_organization(
            context("ORGANIZATION_NAME", "Sint-Rafael", seed=seed, pool=POOL)
        )
        assert drawn in POOL, seed


def test_an_organisation_never_redraws_itself() -> None:
    # The pool has to offer more than one name that fits the slot: with a
    # single fitting entry the rotation has nowhere to go.
    for seed in range(20):
        drawn = surrogate_organization(
            context("ORGANIZATION_NAME", "sint-lucas", seed=seed, pool=POOL)
        )
        assert drawn.casefold() != "sint-lucas", seed


def test_a_name_that_fits_the_slot_is_preferred_to_one_that_does_not() -> None:
    # "Sint-Jan" is the only entry short enough for a nine-character slot.
    for seed in range(20):
        drawn = surrogate_organization(
            context("ORGANIZATION_NAME", "Sint-Piet", seed=seed, pool=POOL)
        )
        assert drawn == "Sint-Jan", seed


def test_a_slot_too_tight_for_any_entry_still_gets_an_organisation() -> None:
    drawn = surrogate_organization(context("ORGANIZATION_NAME", "AB", pool=POOL))
    # An all-caps original takes an all-caps replacement.
    assert drawn in {name.upper() for name in POOL}


def test_a_locale_with_no_register_falls_back_to_a_seeded_company_name() -> None:
    class _Faker:
        @staticmethod
        def company() -> str:
            return "Acme NV"

    ctx = SurrogateContext(
        label="ORGANIZATION_NAME",
        original="Sint-Jan",
        locale=LOCALE,
        organization_pool=(),
        random=lambda: random.Random(1),
        faker=lambda: _Faker(),
        date_of=lambda text: parse_date(text, LOCALE),
    )
    assert surrogate_organization(ctx) == "Acme NV"


def test_a_vocabulary_label_is_drawn_from_its_own_vocabulary() -> None:
    for label in VOCABULARY_LABELS:
        values = vocabulary_for(LOCALE, label)
        drawn = from_vocabulary(context(label, values[0]))
        assert drawn.casefold() in {value.casefold() for value in values}, label


def test_a_vocabulary_label_follows_the_document_locale() -> None:
    """A pseudonym has to read like the document it replaced a value in."""
    for label in ("MEDICATION", "SYMPTOM", "DIAGNOSIS"):
        nl_values = {
            value.casefold() for value in vocabulary_for(profile("nl_BE"), label)
        }
        fr_values = {
            value.casefold() for value in vocabulary_for(profile("fr_FR"), label)
        }
        en_values = {value.casefold() for value in vocabulary_for(profile("en"), label)}
        drawn_nl = resolve(context(label, "x", locale="nl_BE")).casefold()
        drawn_fr = resolve(context(label, "x", locale="fr_FR")).casefold()
        drawn_en = resolve(context(label, "x", locale="en")).casefold()
        assert drawn_nl in nl_values, label
        assert drawn_fr in fr_values, label
        assert drawn_en in en_values, label


def test_a_vocabulary_label_is_deterministic_for_the_same_seed_and_value() -> None:
    """Masking the same value twice in one document returns the same bytes."""
    for label in ("MEDICATION", "SYMPTOM", "DIAGNOSIS"):
        first = resolve(context(label, "same value", locale="fr_FR"))
        second = resolve(context(label, "same value", locale="fr_FR"))
        assert first == second, label


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


def test_a_date_is_replaced_by_another_date_in_the_same_format() -> None:
    for original in ("12/03/2024", "2024-03-12", "12 maart 2024"):
        drawn = surrogates.surrogate_date(context("DATE", original))
        assert drawn != original, original
        assert parse_date(drawn, LOCALE) is not None, (original, drawn)


def test_a_date_this_release_cannot_read_still_leaves_the_document(caplog) -> None:
    caplog.set_level(logging.INFO, logger="masking_engine.app.engines.surrogates")
    drawn = surrogates.surrogate_date(context("DATE", "de derde donderdag"))
    assert "mask_date_not_parsed label=DATE" in [
        record.getMessage() for record in caplog.records
    ]
    # It arrives as an ISO date anchored on a fixed day, so masking the same
    # document twice returns the same bytes.
    assert date.fromisoformat(drawn)
    assert drawn == surrogates.surrogate_date(context("DATE", "de derde donderdag"))


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def test_each_label_reaches_the_strategy_that_owns_it() -> None:
    assert resolve(context("AGE", "42")) != "42"
    assert resolve(context("DATE", "12/03/2024")) != "12/03/2024"
    assert resolve(context("NHS_NUMBER", "9434765919")) != "9434765919"
    assert resolve(context("INSZ_NISS", "50010368687")) != "50010368687"
    assert resolve(context("RIZIV_INAMI", "1-23456-78-901")) != "1-23456-78-901"
    # A label with no rule of its own still leaves the document.
    drawn = resolve(context("SOMETHING_NEW", "Ab12"))
    assert drawn != "Ab12" and len(drawn) == 4


def test_no_label_is_claimed_by_two_arms_of_the_dispatch() -> None:
    assert set(surrogates.BY_LABEL) & VOCABULARY_LABELS == set()
    assert set(surrogates.BY_LABEL) & surrogates._DIGITS_ONLY_LABELS == set()
    assert set(surrogates.BY_LABEL) & set(surrogates._CHECKED_NUMBER_LABELS) == set()
    assert set(surrogates.BY_LABEL) & surrogates._NATIONAL_NUMBER_LABELS == set()
    assert (
        surrogates._DIGITS_ONLY_LABELS & set(surrogates._CHECKED_NUMBER_LABELS) == set()
    )
    assert surrogates._DIGITS_ONLY_LABELS & surrogates._NATIONAL_NUMBER_LABELS == set()


def test_every_deployment_locale_can_mask_a_national_number() -> None:
    for locale in SUPPORTED_LOCALES:
        drawn = resolve(context("INSZ_NISS", "50010368687", locale=locale))
        assert drawn != "50010368687", locale


def test_every_locale_vocabulary_file_is_read_into_tuples_under_its_own_labels() -> (
    None
):
    """Read each vocabulary file through the loader, not through the module.

    Loaders are `@cache`d on path, so mutation testing never sees a test call
    one and reports every mutant of it as surviving -- including ones that
    would empty a file's contents. A label whose values vanish falls back to
    the Dutch pool (`vocabulary_for`), which does not know a French or English
    document needs its own language.
    """
    for locale in SUPPORTED_LOCALES:
        path = surrogate_pools._CORE_DIR / profile(locale).vocabulary_file
        loaded = surrogate_pools._load_vocabularies(path)
        assert loaded, f"{locale}'s vocabulary file yielded no labels"
        for label, values in loaded.items():
            assert isinstance(values, tuple), (locale, label)
            assert values, (locale, label)
            assert all(isinstance(value, str) and value for value in values), (
                locale,
                label,
            )
            assert all("," not in value and ";" not in value for value in values), (
                locale,
                label,
            )
