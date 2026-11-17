"""Validate date pseudonymization stays within the requested year range."""

from __future__ import annotations

import calendar
import os
import random
import sys
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, MaskingRouter, coalesce_spans

# Private knobs the pool and date-shift regressions pin on purpose: not
# part of the package's public surface.
from masking_engine.app.core.locales import (
    DUTCH_GENDER_LETTERS,
    DUTCH_GENDER_WORDS,
    DUTCH_MONTHS,
    ENGLISH_GENDER_LETTERS,
    ENGLISH_GENDER_WORDS,
    ENGLISH_MONTHS,
    FRENCH_GENDER_LETTERS,
    FRENCH_GENDER_WORDS,
    FRENCH_MONTHS,
)
from masking_engine.app.core import locales
from masking_engine.app.engines import surrogate_dates
from masking_engine.app.engines.surrogate_dates import parse_date
from masking_engine.app.engines.surrogate_dates import (
    DATE_MAX_YEARS,
    DATE_MIN_YEARS,
    _year_offsets,
    _year_only,
    unparsed_date_anchor,
)
from masking_engine.app.engines.surrogate_pools import organization_pool_for
from masking_engine.app.engines.surrogate_numbers import _insz_digits, _nir_digits
from masking_engine.app.engines.regex_belgium import _insz_ok
from masking_engine.app.engines.regex_france import _nir_ok
from masking_engine.app.engines.surrogate_text import _match_organization_case

_ORGANIZATION_POOL = organization_pool_for("nl_BE", "general")
_MEDICAL_ORGANIZATION_POOL = organization_pool_for("nl_BE", "medical")


def test_date_surrogate_stays_within_four_years_of_the_source() -> None:
    original = "2000-02-29"
    replacement = (
        MaskingRouter(b"test document")
        .replacement(MaskSpan(0, len(original), "DATE", "test"), original)
        .text
    )
    moved = datetime.strptime(replacement, "%Y-%m-%d").date()
    years = abs(moved.year - 2000)
    assert DATE_MIN_YEARS <= years <= DATE_MAX_YEARS
    # The month and the day are redrawn on purpose (see `_redraw_date`):
    # holding them fixed would leave a birthday recognisable as itself after
    # only its year had moved. What a leap day has to prove is that the
    # redraw never hands a month a day it does not have -- 29 February is the
    # value that catches it -- and that the written format survives.
    assert replacement == moved.isoformat()
    assert moved.day <= calendar.monthrange(moved.year, moved.month)[1]


def test_a_bare_year_is_replaced_by_a_bare_year() -> None:
    # "sinds eind 2020" carries no separator, so it matched no numeric pattern
    # and fell to the unparsed fallback, which renders a complete date: the
    # sentence came back reading "sinds eind 2010-01-22". DATE is kept out of
    # the orchestrator's regex-exclusive set precisely so the model can catch
    # a bare year, which is what makes this reachable.
    router = MaskingRouter(b"bare year document")
    for original in ("2020", "2003"):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        assert replacement.isdigit() and len(replacement) == 4, replacement
        assert replacement != original
        years = abs(int(replacement) - int(original))
        assert DATE_MIN_YEARS <= years <= DATE_MAX_YEARS


def test_only_four_digits_are_read_as_a_bare_year() -> None:
    # `strptime` accepts "45" as the year 45, and a two-digit span the model
    # labelled DATE is far likelier to be an age. Anything but four digits
    # keeps whatever the parser did before, rather than being promoted to a
    # year.
    profile = locales.profile("nl_BE")
    assert _year_only("2020") is not None
    for text in ("45", "1", "12345", "2020.", " 2020", "20 20"):
        assert _year_only(text) is None, text
        assert parse_date(text, profile) is None or "-" in text or "/" in text


def test_datetime_surrogate_preserves_time_and_shifts_only_the_date() -> None:
    original = "15-01-2024 14:21"
    router = MaskingRouter(b"test document")
    replacement = router.replacement(
        MaskSpan(0, len(original), "DATE", "test"), original
    ).text
    moved = datetime.strptime(replacement, "%d-%m-%Y %H:%M")
    assert moved.strftime("%H:%M") == "14:21"
    assert DATE_MIN_YEARS <= abs(moved.year - 2024) <= DATE_MAX_YEARS


def test_a_timestamp_keeps_its_clock_in_every_spelling_the_detector_emits() -> None:
    """`_ISO_DATETIME_RE` claims four timestamp shapes; all four must parse.

    Two of them -- the `T` separator and the seconds -- matched no format, so
    they fell through to the unparsed fallback: the time of day was silently
    deleted, the shape came back as a bare ISO date, and the year was shifted
    off the anchor instead of off the value the document actually carried.
    """
    router = MaskingRouter(b"timestamp document")
    for original, pattern, clock_format, clock in (
        ("2026-03-15 14:21", "%Y-%m-%d %H:%M", "%H:%M", "14:21"),
        ("2026-03-15T14:21", "%Y-%m-%dT%H:%M", "%H:%M", "14:21"),
        ("2026-03-15 14:21:30", "%Y-%m-%d %H:%M:%S", "%H:%M:%S", "14:21:30"),
        ("2026-03-15T09:05:00", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S", "09:05:00"),
        ("15-03-2026 14:30:00", "%d-%m-%Y %H:%M:%S", "%H:%M:%S", "14:30:00"),
    ):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        moved = datetime.strptime(replacement, pattern)
        assert moved.strftime(clock_format) == clock, replacement
        assert abs(moved.year - 2026) <= DATE_MAX_YEARS, replacement


def test_a_date_moves_in_both_directions() -> None:
    """Four offsets, not eight, would halve an already small keyspace.

    The range used to be past-only, with the future reachable solely as a
    rescue for a date too near year 1 to shift back. At fifty years that was
    a hundred offsets either way; at four it is the difference between eight
    candidates and four.
    """
    years = set()
    for document in range(40):
        years.add(
            int(
                MaskingRouter(b"document %d" % document)
                .replacement(MaskSpan(0, 10, "DATE", "test"), "15-03-2026")
                .text[6:]
            )
        )
    assert years <= {2022, 2023, 2024, 2025, 2027, 2028, 2029, 2030}, sorted(years)
    assert any(year > 2026 for year in years), sorted(years)
    assert any(year < 2026 for year in years), sorted(years)


def test_a_two_digit_year_keeps_the_century_its_source_was_written_in() -> None:
    """The far-future years users saw in two-digit fields were the old range.

    A "%y" field renders two digits because its source did, so a fifty-year
    shift moved "98" (1998) to "48" -- which every reader downstream takes
    for 2048. The bound is what fixes this: the renderer is faithful to the
    source and forcing four digits would only overflow the field.
    """
    router = MaskingRouter(b"two digit document")
    for original, source_year in (("12/03/94", 1994), ("15/03/26", 2026)):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        moved = datetime.strptime(replacement, "%d/%m/%y").date()
        assert abs(moved.year - source_year) <= DATE_MAX_YEARS, replacement


def test_a_space_separated_date_keeps_its_shape_and_its_era() -> None:
    """The detector reads "01 02 1985" as a date; the surrogate has to as well.

    It did not, so the value took the unparsed fallback: a 1985 birth date
    came back as "2027-07-27" -- rendered in the wrong shape, and shifted
    from today rather than from the year the document carried. PDF text
    carries the same dates written with non-breaking spaces, which `strptime`
    accepts under the ordinary-space pattern.
    """
    router = MaskingRouter(b"spaced date document")
    for original, pattern, source_year in (
        ("27 10 1985", "%d %m %Y", 1985),
        ("2024 03 12", "%Y %m %d", 2024),
        ("02 07 2024", "%d %m %Y", 2024),
    ):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        moved = datetime.strptime(replacement, pattern).date()
        assert abs(moved.year - source_year) <= DATE_MAX_YEARS, replacement


def test_a_mixed_separator_date_keeps_its_shape_and_its_era() -> None:
    """`_DATE_SEP` is a character class, so the two separators need not match.

    The detector claims "02.07 2024" as readily as "02.07.2024" -- which is
    what an OCR pass produces when it reads one dot of a scanned date as a
    space. No `strptime` pattern can express "either separator here", so every
    such value took the unparsed fallback and came back as a full ISO date
    shifted from the anchor: a 1985 birth date landed in the current decade.
    """
    router = MaskingRouter(b"mixed separator document")
    for original, source_year in (
        ("02.07 2024", 2024),
        ("05.05 1985", 1985),
        ("02-07.2024", 2024),
        ("2024-03 12", 2024),
        ("02/07-2024", 2024),
        ("2.7 2024", 2024),
    ):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        # The separators and the field widths are the shape the field has to
        # keep; only the numbers inside them may move.
        assert re.sub(r"\d", "0", replacement) == re.sub(
            r"\d", "0", original
        ), replacement
        year = max(int(part) for part in re.findall(r"\d+", replacement))
        assert abs(year - source_year) <= DATE_MAX_YEARS, replacement


def test_date_surrogate_handles_year_bounds_and_two_digit_years() -> None:
    router = MaskingRouter(b"boundary document")
    for original, expected_year in (("01-01-0001", 1), ("01-01-9999", 9999)):
        replacement = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        moved = datetime.strptime(replacement, "%d-%m-%Y").date()
        assert DATE_MIN_YEARS <= abs(moved.year - expected_year) <= DATE_MAX_YEARS
    assert all(
        abs(offset) != 100
        for offset in _year_offsets(datetime(2026, 5, 16), two_digit_year=True)
    )


def test_age_surrogate_preserves_the_original_unit() -> None:
    original = "29-jarige"
    router = MaskingRouter(b"test document")
    replacement = router.replacement(
        MaskSpan(0, len(original), "AGE", "test"), original
    ).text
    assert replacement.endswith("-jarige")
    assert 10 <= int(replacement.removesuffix("-jarige")) <= 99


def test_age_surrogate_never_returns_the_original() -> None:
    """No age may leave a document unmasked.

    Redrawing only the leading digit run returned the input verbatim in one
    document in nine for a one-digit age, and kept every digit after the first
    run for a value like "1-2-3".
    """
    originals = ("7", "8", "47", "7 jaar", "1-2-3", "40-50", "1-05921-70-004")
    for original in originals:
        span = MaskSpan(0, len(original), "AGE", "test")
        for seed in range(200):
            router = MaskingRouter(f"document {seed}".encode())
            replacement = router.replacement(span, original).text
            assert replacement != original, (original, seed, replacement)

        # Every digit run keeps its width and never opens with a zero, so a
        # wrapped PDF span still slices to its source runs and an age stays an
        # age.
        router = MaskingRouter(b"test document")
        replacement = router.replacement(span, original).text
        source_runs = re.findall(r"\d+", original)
        masked_runs = re.findall(r"\d+", replacement)
        assert [len(run) for run in masked_runs] == [len(run) for run in source_runs], (
            original,
            replacement,
        )
        assert all(run == "0" or not run.startswith("0") for run in masked_runs), (
            original,
            replacement,
        )
        assert re.sub(r"\d", "", replacement) == re.sub(r"\d", "", original)


def test_digit_keyed_surrogate_keeps_each_spelling_of_one_number() -> None:
    """Two spellings of one number share its digits and keep their own format.

    `_seed_key` strips separators for a digit-keyed label, so the cache held one
    entry per number while `_surrogate_age` and `_surrogate_phone` render in the
    source's format. Whichever spelling came first then decided the punctuation
    for every other, and "61-jarige" could be replaced by a bare "65".
    """
    for spellings in (
        ("61", "61-jarige", "61 jaar"),
        ("61 jaar", "61"),
    ):
        router = MaskingRouter(b"test document")
        digits = set()
        for original in spellings:
            replacement = router.replacement(
                MaskSpan(0, len(original), "AGE", "test"), original
            ).text
            assert re.sub(r"\d", "", replacement) == re.sub(r"\d", "", original), (
                original,
                replacement,
            )
            digits.add(re.sub(r"\D", "", replacement))
        assert len(digits) == 1, (spellings, digits)

    router = MaskingRouter(b"test document")
    phones = {
        original: router.replacement(
            MaskSpan(0, len(original), "PHONE_NUMBER", "test"), original
        ).text
        for original in ("0476033164", "0476 03 31 64", "04/76.03.31.64")
    }
    assert len({re.sub(r"\D", "", value) for value in phones.values()}) == 1, phones
    for original, replacement in phones.items():
        assert re.sub(r"\d", "", replacement) == re.sub(r"\d", "", original)


def test_replacement_canonicalizes_a_native_label_first() -> None:
    """A team's own label has to resolve before the surrogate is chosen.

    A span arrives carrying whatever the detecting team calls the label -- a
    Berts `PHONE`, not the canonical `PHONE_NUMBER`. If the canonical lookup
    is skipped or its result discarded, `_pseudonymize` finds no entry for
    the label and the number falls through to a generic redraw instead of the
    phone surrogate, losing the dialling prefix that tells a reader what the
    field is.
    """
    router = MaskingRouter(b"test document")
    original = "0476 03 31 64"
    native = MaskSpan(0, len(original), "PHONE", "test")
    canonical = MaskSpan(0, len(original), "PHONE_NUMBER", "test")
    assert (
        router.replacement(native, original).text
        == router.replacement(canonical, original).text
    )
    # A redraw would scramble the digits in place and keep the spacing byte
    # for byte, so pin the surrogate's shape rather than only comparing the
    # two calls with each other.
    assert re.fullmatch(
        r"04\d\d \d\d \d\d \d\d", router.replacement(native, original).text
    )


def test_gender_replacement_stays_in_the_vocabulary() -> None:
    """A gender is a closed vocabulary, and the surrogate stays inside it.

    `M`, `V`, `vrouw`, `female`: a replacement drawn from anywhere else is a
    word no reader can place and no downstream field can validate. The
    surrogate answers with another value from the same vocabulary, in the
    same case and the same form as the one it replaces.
    """
    router = MaskingRouter(b"test document", locale="nl_BE")
    for original, vocabulary in (
        ("vrouw", DUTCH_GENDER_WORDS),
        ("V", DUTCH_GENDER_LETTERS),
        ("Vrouw", tuple(word.capitalize() for word in DUTCH_GENDER_WORDS)),
    ):
        span = MaskSpan(0, len(original), "GENDER", "test")
        replacement = router.replacement(span, original).text
        assert replacement in vocabulary, (original, replacement)


def test_phone_surrogate_preserves_the_source_format() -> None:
    original = "07/204.21.58"
    router = MaskingRouter(b"test document")
    replacement = router.replacement(
        MaskSpan(0, len(original), "PHONE_NUMBER", "test"), original
    ).text
    assert re.fullmatch(r"07/\d{3}\.\d{2}\.\d{2}", replacement)
    assert replacement[:2] == "07"
    assert replacement != original


def test_phone_surrogate_keeps_no_head_from_a_value_that_is_not_a_phone() -> None:
    """A value with no dialling prefix is redrawn whole.

    The surrogate keeps a dialling prefix so a masked phone still reads as a
    Belgian number. A national number that a detector routed to this label has
    no dialling prefix to keep, and its first two digits are the patient's
    year of birth -- printed in the clear in a field that looks masked.
    """
    original = "78.07.03-456.12"
    replacement = (
        MaskingRouter(b"test document")
        .replacement(MaskSpan(0, len(original), "PHONE_NUMBER", "test"), original)
        .text
    )
    assert re.sub(r"\d", "", replacement) == re.sub(r"\d", "", original)
    assert all(new != old for new, old in zip(replacement, original) if old.isdigit())


def test_url_surrogate_is_deterministic_and_drops_source_host_and_path() -> None:
    original = "https://www.azorg.be/patients/marie?token=secret"
    router = MaskingRouter(b"test document")
    replacement = router.replacement(
        MaskSpan(0, len(original), "URL", "test"), original
    ).text
    assert re.fullmatch(r"https://www\.masked-[0-9a-f]{8}\.be", replacement)
    assert "azorg" not in replacement and "marie" not in replacement


def test_organization_surrogate_can_use_medical_or_general_pool() -> None:
    original = "AZORG"
    # See test_organization_pool_follows_the_document_locale for why the Dutch
    # floor is 9,500 rather than the 10,000 the builder targets.
    assert 9_500 <= len(_ORGANIZATION_POOL) <= 50_000
    assert 9_500 <= len(_MEDICAL_ORGANIZATION_POOL) <= 50_000
    span = MaskSpan(0, len(original), "ORGANIZATION_NAME", "test")
    for pool in ("medical", "general"):
        router = MaskingRouter(b"test document", organization_pool=pool)
        replacement = router.replacement(span, original).text
        assert replacement.casefold() in {
            name.casefold()
            for name in (
                _MEDICAL_ORGANIZATION_POOL if pool == "medical" else _ORGANIZATION_POOL
            )
        }
        assert len(replacement) <= len(original)
        assert replacement != original

    try:
        MaskingRouter(b"test document", organization_pool="other")
    except ValueError as exc:
        assert str(exc) == "organization_pool must be general or medical"
    else:
        raise AssertionError("invalid organisation pool should be rejected")


def test_organization_pool_follows_the_document_locale() -> None:
    """A French note gets a French organisation, not a Flemish one.

    Faker has no generator that produces a plausible national trade name, so
    each locale carries pools scraped from that country's own open registers
    -- KVK and CIBG for Dutch, SIRENE and FINESS for French.
    """
    # Enough distinct names that a surrogate is not traceable by elimination.
    # The Dutch floor sits below the 10,000 the builder targets: filtering the
    # pools for personal names and register artefacts took the medical pool to
    # 9,606, and it cannot be topped back up from the general pool, whose six
    # remaining healthcare-like names are themselves people ("R. Pieters
    # Freelance A-B Verpleegkundige"). Refilling it means re-deriving from the
    # CIBG releases the metadata names.
    minimum = 9_500
    for kind in ("general", "medical"):
        dutch = organization_pool_for("nl_BE", kind)
        french = organization_pool_for("fr_FR", kind)
        assert len(dutch) >= minimum, (kind, len(dutch))
        assert len(french) >= minimum, (kind, len(french))
        # Distinct registers, so the two pools must not be the same file.
        assert not set(dutch) & set(french) or len(set(dutch) & set(french)) < 200

        original = "Centre Hospitalier Régional"
        span = MaskSpan(0, len(original), "ORGANIZATION_NAME", "test")
        for locale, pool in (("nl_BE", dutch), ("fr_FR", french)):
            router = MaskingRouter(
                b"test document", organization_pool=kind, locale=locale
            )
            replacement = router.replacement(span, original).text
            assert replacement.casefold() in {name.casefold() for name in pool}, (
                locale,
                kind,
                replacement,
            )

    # An unknown pool kind is still rejected, in either locale.
    for locale in ("nl_BE", "fr_FR"):
        try:
            MaskingRouter(b"test document", organization_pool="other", locale=locale)
        except ValueError as exc:
            assert "general or medical" in str(exc)
        else:
            raise AssertionError(f"{locale}: invalid pool should be rejected")


def test_locale_decides_every_language_dependent_surrogate() -> None:
    """One document, two locales: each surrogate reads as its own language."""
    document = b"same document bytes"
    dutch = MaskingRouter(document, locale="nl_BE")
    french = MaskingRouter(document, locale="fr_FR")
    english = MaskingRouter(document, locale="en")

    def replace(router: MaskingRouter, label: str, original: str) -> str:
        return router.replacement(
            MaskSpan(0, len(original), label, "test"), original
        ).text

    # A one-letter sex field stays one letter, but Dutch writes V for "vrouw"
    # where French writes F for "femme".
    assert replace(dutch, "GENDER", "V") in DUTCH_GENDER_LETTERS
    assert replace(french, "GENDER", "V") in FRENCH_GENDER_LETTERS
    assert replace(dutch, "GENDER", "vrouw") in DUTCH_GENDER_WORDS
    assert replace(french, "GENDER", "vrouw") in FRENCH_GENDER_WORDS
    assert replace(english, "GENDER", "V") in ENGLISH_GENDER_LETTERS
    assert replace(english, "GENDER", "vrouw") in ENGLISH_GENDER_WORDS

    # A written date renders its month in the document's language, and the
    # surrogate is the same date in both -- only the spelling differs.
    dutch_date = replace(dutch, "DATE", "5 februari 2019")
    french_date = replace(french, "DATE", "5 februari 2019")
    english_date = replace(english, "DATE", "5 februari 2019")
    day, dutch_month, year = dutch_date.split()
    assert (day, year) == tuple(french_date.split()[::2])
    assert (day, year) == tuple(english_date.split()[::2])
    assert dutch_month != french_date.split()[1]
    assert english_date.split()[1] in ENGLISH_MONTHS, english_date

    # A masked URL keeps a plausible national host.
    assert replace(dutch, "URL", "https://www.uzgent.be").endswith(".be")
    assert replace(french, "URL", "https://www.uzgent.be").endswith(".fr")
    assert replace(english, "URL", "https://www.uzgent.be").endswith(".uk")

    # The added European locales are accepted; an unknown locale is still
    # rejected rather than silently falling back.
    for locale in ("de_DE", "es_ES", "ro_RO"):
        assert MaskingRouter(document, locale=locale).locale == locale
    try:
        MaskingRouter(document, locale="pt_BR")
    except ValueError as exc:
        assert "fr_FR" in str(exc) and "nl_BE" in str(exc)
    else:
        raise AssertionError("an unsupported locale should be rejected")


def test_french_month_names_parse_in_either_locale() -> None:
    """A French written date gets a date surrogate, not a digit redraw.

    `_WRITTEN_DATE_RE` used to allow only `[A-Za-zé]`, so "août" and
    "février" never parsed and the date kept its real month. Parsing accepts
    every supported locale's month names whatever the document's locale is,
    because a Dutch note can quote a French date.
    """
    # An accent lost by a PDF text layer must not cost the date its written
    # form: "5 fevrier 2019" used to fail to parse and came back as an ISO
    # date, changing the shape of the field.
    accented = MaskingRouter(b"test document", locale="fr_FR").replacement(
        MaskSpan(0, 14, "DATE", "test"), "5 février 2019"
    )
    folded = MaskingRouter(b"test document", locale="fr_FR").replacement(
        MaskSpan(0, 14, "DATE", "test"), "5 fevrier 2019"
    )
    assert accented.text == folded.text, (accented.text, folded.text)

    for original in (
        "5 février 2019",
        "5 fevrier 2019",
        "5 décembre 2019",
        "5 decembre 2019",
        "5 août 2019",
        "5 aout 2019",
        "12 mars 2001",
    ):
        for locale in ("nl_BE", "fr_FR"):
            router = MaskingRouter(b"test document", locale=locale)
            replacement = router.replacement(
                MaskSpan(0, len(original), "DATE", "test"), original
            ).text
            day, month, year = replacement.split()
            assert day.isdigit() and year.isdigit(), replacement
            assert not month.isdigit(), replacement
            # The year moved, so the real date did not survive.
            assert year != original.split()[-1], replacement


def test_nir_surrogate_is_a_valid_french_national_number() -> None:
    """A masked NIR passes the same mod-97 check the detector applies.

    `fr_FR.ssn()` returns a real 15-digit NIR, so the surrogate survives a
    downstream validator the way a masked INSZ already does.
    """
    original = "2 19 07 85 191 050 27"
    replacement = (
        MaskingRouter(b"french document", locale="fr_FR")
        .replacement(MaskSpan(0, len(original), "NIR", "test"), original)
        .text
    )
    digits = re.sub(r"\D", "", replacement)
    assert len(digits) == 15, replacement
    assert 97 - (int(digits[:13]) % 97) == int(digits[13:]), replacement
    assert digits != re.sub(r"\D", "", original)


def test_rpps_surrogate_redraws_digits_and_keeps_separators() -> None:
    """RPPS carries no checksum the detector validates, so digits are redrawn.

    Two spellings of one number reach one surrogate, because RPPS is keyed on
    its digits like every other number label.
    """
    router = MaskingRouter(b"french document", locale="fr_FR")

    def replace(original: str) -> str:
        return router.replacement(
            MaskSpan(0, len(original), "RPPS", "test"), original
        ).text

    compact = replace("10003411442")
    assert compact.isdigit() and len(compact) == 11
    assert compact != "10003411442"
    # Separators are the writer's choice, so they survive and do not change
    # which surrogate is drawn.
    spaced = replace("100 034 114 42")
    assert re.sub(r"\D", "", spaced) == compact
    assert spaced == f"{compact[:3]} {compact[3:6]} {compact[6:9]} {compact[9:]}"


def test_written_month_keeps_the_form_the_source_used() -> None:
    """A month written out in full comes back written out in full.

    The form was decided by `len(month) <= 4`, which is not a test of
    anything: "mars", "mai", "juin" and "août" are whole French months and
    "mei", "juni" and "juli" whole Dutch ones, and every one of them came
    back abbreviated.
    """
    for locale, months in (
        ("fr_FR", FRENCH_MONTHS),
        ("nl_BE", DUTCH_MONTHS),
    ):
        router = MaskingRouter(b"document", locale=locale)
        for month in months:
            original = f"14 {month} 1962"
            replaced = router.replacement(
                MaskSpan(0, len(original), "DATE", "test"), original
            ).text
            written = replaced.split()[1]
            assert written in months, f"{locale} {original!r} -> {replaced!r}"


def test_written_month_keeps_an_abbreviation_abbreviated() -> None:
    """An abbreviated source month stays abbreviated, dot and all."""
    router = MaskingRouter(b"document", locale="fr_FR")
    for original, dotted in (("14 sept. 2025", True), ("14 sept 2025", False)):
        replaced = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        written = replaced.split()[1]
        # Abbreviated to three letters, and a month that short already ("mai")
        # is its own abbreviation.
        assert len(written.rstrip(".")) <= 3, replaced
        assert written.endswith(".") is dotted, replaced


def test_written_month_keeps_the_case_the_source_used() -> None:
    """A date in a caps header comes back in caps.

    Only the first letter was inspected, so an all-caps month came back
    merely capitalized and lost the case of the line it sat on.
    """
    router = MaskingRouter(b"document", locale="fr_FR")
    for original, expected in (
        ("14 MARS 1962", str.isupper),
        ("14 Mars 1962", lambda value: value[:1].isupper() and not value.isupper()),
        ("14 mars 1962", str.islower),
    ):
        replaced = router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text
        assert expected(replaced.split()[1]), replaced


def test_french_ordinal_date_parses_and_renders() -> None:
    """ "1er janvier" is how French writes the first, not an edge case.

    It failed to parse, so the whole date fell through to the numeric
    fallback and came back as an ISO date in the middle of French prose.
    """
    original = "1er janvier 2025"
    router = MaskingRouter(b"document", locale="fr_FR")
    replaced = router.replacement(
        MaskSpan(0, len(original), "DATE", "test"), original
    ).text
    assert re.fullmatch(r"\d{1,2}(er)? [^\W\d_]+ \d{4}", replaced), replaced
    assert replaced != original

    # The suffix belongs to the first of the month and follows the redrawn
    # day, not the source one -- and Dutch, which writes no suffix, gets none.
    for locale, expected in (("fr_FR", "1er"), ("nl_BE", "1")):
        days = set()
        for seed in range(60):
            router = MaskingRouter(f"document {seed}".encode(), locale=locale)
            days.add(
                router.replacement(
                    MaskSpan(0, len(original), "DATE", "test"), original
                ).text.split()[0]
            )
        assert expected in days, (locale, sorted(days))
        # Every other day is written plain, in both languages.
        assert {"1er", "1"} & days == {expected}, (locale, sorted(days))


def _replace_unparsed_date() -> str:
    """Mask a value no date format fits, through the fallback path."""
    original = "14 mars"  # a day and a month with no year: no format fits
    assert (
        parse_date(original, locales.profile("fr_FR")) is None
    ), "sample must exercise the fallback"
    return (
        MaskingRouter(b"document", locale="fr_FR")
        .replacement(MaskSpan(0, len(original), "DATE", "test"), original)
        .text
    )


class _ClockForwardOneYear:
    """Stand in for `surrogate_dates.date` with today a year from now."""

    def __enter__(self):
        self._real = surrogate_dates.date
        real = self._real

        class _FixedToday(real):
            @classmethod
            def today(cls):  # noqa: D102 - stands in for date.today
                return real(real.today().year + 1, 6, 15)

        surrogate_dates.date = _FixedToday
        return self

    def __exit__(self, *_exc) -> None:
        surrogate_dates.date = self._real


def test_unparsed_date_lands_beside_the_dates_it_stands_in_for() -> None:
    """The fallback anchors on today, so its output is a plausible date.

    A fixed anchor put every unreadable value in one settled year regardless
    of when the document was written; with the range now four years rather
    than fifty, that year had to be the current one for the stand-in to sit
    anywhere near the dates around it.
    """
    baseline = _replace_unparsed_date()
    anchor = unparsed_date_anchor().year
    assert abs(int(baseline[:4]) - anchor) <= DATE_MAX_YEARS, baseline


def test_a_pinned_anchor_keeps_an_unparsed_date_byte_reproducible() -> None:
    """NiFi replays an idempotency key, so a deployment can settle the anchor.

    Unpinned, the fallback follows today and therefore moves at a year
    boundary -- the same document, the same bytes in, different bytes out.
    `MASKING_DATE_ANCHOR` is what a deployment that replays keys sets to get
    the settled behaviour back.
    """
    os.environ["MASKING_DATE_ANCHOR"] = "2020-01-01"
    try:
        baseline = _replace_unparsed_date()
        assert abs(int(baseline[:4]) - 2020) <= DATE_MAX_YEARS, baseline
        with _ClockForwardOneYear():
            assert (
                _replace_unparsed_date() == baseline
            ), "a pinned anchor drifted with the clock"
    finally:
        del os.environ["MASKING_DATE_ANCHOR"]


def test_an_unpinned_anchor_follows_the_clock() -> None:
    """The documented cost of defaulting to today, asserted rather than implied."""
    baseline = _replace_unparsed_date()
    with _ClockForwardOneYear():
        assert _replace_unparsed_date() != baseline


def test_month_and_year_without_a_day_keeps_that_shape() -> None:
    """ "04-2025" is a month, and its surrogate has to be one too.

    No pattern matched a month and a year, so a clinical history's dates fell
    through to the unparsed fallback and came back as full ISO dates: a day
    the source never recorded, in a value too long for the slot it had to fit,
    which then reached the page truncated to "1997-01" plus a truncation mark.
    """
    router = MaskingRouter(b"french document", locale="fr_FR")

    def replace(original: str) -> str:
        return router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text

    for original, pattern in (
        ("04-2025", r"\d{2}-\d{4}"),
        ("04/2025", r"\d{2}/\d{4}"),
        ("04.2025", r"\d{2}\.\d{4}"),
        ("2025-04", r"\d{4}-\d{2}"),
        ("mars 1962", r"[^\W\d_]+ \d{4}"),
        ("MARS 1962", r"[^\W\d_]+ \d{4}"),
    ):
        replaced = replace(original)
        assert re.fullmatch(pattern, replaced), (original, replaced)
        assert replaced != original, original

    # A full date still keeps its day: the month-only patterns are tried last
    # and must never swallow one.
    assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", replace("15-01-2024"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", replace("2024-01-15"))
    # And a written month keeps the case the source used.
    assert replace("MARS 1962").isupper()


def test_organization_surrogate_keeps_the_register_capitalisation() -> None:
    """A masked organisation reads as a proper name, not one long word.

    Both pools are register exports -- the French one written in capitals
    almost throughout -- and capitalizing the whole string as `_match_case`
    does turned "SOSAN IME" into "Sosan ime" and "Zorg & Welzijn 010 (B.V.)"
    into "Zorg & welzijn 010 (b.v.)".
    """
    sentence = "Centre Hospitalier Régional de Lille"
    # A register export in capitals is title-cased, never folded into one word.
    assert _match_organization_case("SOSAN IME", sentence) == "Sosan Ime"
    assert (
        _match_organization_case("AAIR UAD DD SAINT REMY", sentence)
        == "Aair Uad Dd Saint Remy"
    )
    # A name that carries its own mixed case is the register's own spelling
    # and is left exactly as written.
    for stored in ("Zorg & Welzijn 010 (B.V.)", "D'Stress praktijk", "AZ Sint-Jan"):
        assert _match_organization_case(stored, sentence) == stored
    # A source written in capitals still gets capitals back.
    assert _match_organization_case("Sosan Ime", "CENTRE HOSPITALIER") == "SOSAN IME"

    # And the router uses it: what it writes is the pool's own entry, cased by
    # this rule and no other.
    for locale in ("fr_FR", "nl_BE"):
        router = MaskingRouter(b"document", locale=locale, organization_pool="medical")
        replaced = router.replacement(
            MaskSpan(0, len(sentence), "ORGANIZATION_NAME", "test"), sentence
        ).text
        stored = {
            name.casefold(): name for name in organization_pool_for(locale, "medical")
        }[replaced.casefold()]
        assert replaced == _match_organization_case(stored, sentence), (
            locale,
            stored,
            replaced,
        )


def test_identical_model_and_regex_spans_keep_the_scored_one() -> None:
    """A phone found by both layers keeps the detection that carries a score.

    The two spans agree exactly, so the winner came down to the order they
    happened to arrive in, and three identical phone numbers in one footer
    rendered with a confidence on some boxes and not on others.
    """
    model = MaskSpan(10, 24, "PHONE_NUMBER", "model", page=0, confidence=1.0)
    regex = MaskSpan(10, 24, "PHONE_NUMBER", "regex", page=0)
    for ordering in ((model, regex), (regex, model)):
        merged = coalesce_spans(list(ordering))
        assert len(merged) == 1, merged
        assert merged[0].source == "model", ordering
        assert merged[0].confidence == 1.0, ordering

    # A longer span still wins outright, whichever layer found it.
    longer = MaskSpan(10, 30, "ADDRESS", "regex", page=0)
    merged = coalesce_spans([model, longer])
    assert len(merged) == 1 and merged[0].label == "ADDRESS", merged


def test_a_numeric_date_is_read_in_either_order() -> None:
    """A month-first date keeps its shape instead of becoming an ISO date.

    "02/17/2026" matched no pattern while the numeric list was day-first only,
    so it fell through to the unparsed fallback and came back as "1994-11-19"
    -- a shape the source never wrote, and longer than the slot it had to fit.
    """
    router = MaskingRouter(b"test document", locale="en")

    def replace(original: str) -> str:
        return router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text

    for original, pattern in (
        ("02/17/2026", r"^\d{2}/\d{2}/\d{4}$"),
        ("02-17-2026", r"^\d{2}-\d{2}-\d{4}$"),
        ("17/02/2026", r"^\d{2}/\d{2}/\d{4}$"),
        ("2026-02-17", r"^\d{4}-\d{2}-\d{2}$"),
    ):
        masked = replace(original)
        assert re.fullmatch(pattern, masked), (original, masked)
        assert masked != original

    # Parsing is locale-independent, exactly as `locales.month_number` is: a
    # Dutch note can quote an American date, and the order that parses is
    # decided by the value rather than by the document.
    dutch = MaskingRouter(b"test document", locale="nl_BE").replacement(
        MaskSpan(0, 10, "DATE", "test"), "02/17/2026"
    )
    assert re.fullmatch(r"^\d{2}/\d{2}/\d{4}$", dutch.text), dutch.text


def test_an_english_written_date_keeps_its_layout_and_its_ordinal() -> None:
    """Month-first, comma and ordinal all come back the way they went in."""
    router = MaskingRouter(b"test document", locale="en")

    def replace(original: str) -> str:
        return router.replacement(
            MaskSpan(0, len(original), "DATE", "test"), original
        ).text

    # The comma the source wrote is matched but not written back: `_sanitize`
    # turns a comma into a space so no replacement can end a CSV field, so
    # both spellings render the same way. What has to survive is the
    # month-first order and the written month.
    month_first = replace("February 17, 2026")
    assert re.fullmatch(r"[A-Z][a-z]+ \d{1,2} \d{4}", month_first), month_first
    assert month_first.split()[0] in ENGLISH_MONTHS, month_first
    assert "," not in month_first, month_first

    no_comma = replace("February 17 2026")
    assert re.fullmatch(r"[A-Z][a-z]+ \d{1,2} \d{4}", no_comma), no_comma

    day_first = replace("17 February 2026")
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]+ \d{4}", day_first), day_first

    # An ordinal in the source earns one in the surrogate, for the surrogate's
    # own day: "17th" can become "3rd" but never "3th".
    ordinal = replace("17th February 2026")
    assert re.fullmatch(r"\d{1,2}(?:st|nd|rd|th) [A-Z][a-z]+ \d{4}", ordinal), ordinal
    day = int(re.match(r"\d+", ordinal)[0])
    assert ordinal.split()[0] == f"{day}{locales._english_ordinal(day)}", ordinal

    # None in the source, none in the surrogate.
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]+ \d{4}", day_first), day_first


def test_french_still_writes_its_mandatory_ordinal() -> None:
    """`day_ordinal_suffix` became a per-locale rule; French must not move.

    French is the locale whose ordinal is mandatory: "1 janvier" is a
    misspelling, so a surrogate landing on the first of a month writes "1er"
    whether or not the source did. Every other day writes a bare number, and
    Dutch writes a bare number always.
    """
    landed = {"fr_FR": [], "nl_BE": []}
    for locale in landed:
        for seed in range(400):
            router = MaskingRouter(f"document {seed}".encode(), locale=locale)
            for original in ("1 janvier 2019", "1er janvier 2019"):
                masked = router.replacement(
                    MaskSpan(0, len(original), "DATE", "test"), original
                ).text
                day = masked.split()[0]
                if locale == "nl_BE":
                    assert day.isdigit(), masked
                elif day.startswith("1") and not day[1:2].isdigit():
                    # A surrogate on the first of the month: French must not
                    # write a bare "1" here.
                    assert day == "1er", masked
                else:
                    assert day.isdigit(), masked
                if day in {"1", "1er"}:
                    landed[locale].append(masked)
    # The assertions above are vacuous unless some seed actually lands on the
    # first, so prove one did before trusting them.
    assert landed["fr_FR"], "no seed produced a first-of-the-month surrogate"
    assert landed["nl_BE"], "no seed produced a first-of-the-month surrogate"
    assert all(masked.split()[0] == "1er" for masked in landed["fr_FR"])
    assert all(masked.split()[0] == "1" for masked in landed["nl_BE"])


def test_every_locale_month_table_agrees_with_the_shared_parser() -> None:
    """One union table serves every locale, so a clash would be silent.

    `month_number` folds every locale's names and abbreviations into one dict
    with `setdefault`, so the first locale to claim a spelling keeps it. Dutch
    "mei", French "mai" and English "may" are all month five, which is why
    this works -- but it works by agreement, not by construction, and a future
    locale could disagree without anything failing.
    """
    for code, profile in locales.LOCALES.items():
        for index, name in enumerate(profile.months):
            assert locales.month_number(name) == index + 1, (code, name)
        for abbreviation, month in profile.month_abbreviations.items():
            assert locales.month_number(abbreviation) == month, (code, abbreviation)


def test_a_locale_without_a_general_pool_falls_back_to_faker() -> None:
    """English carries a medical register only; the general ask still masks."""
    assert organization_pool_for("en", "general") == ()
    assert len(organization_pool_for("en", "medical")) > 1000

    original = "Riverside Regional Medical Center"
    span = MaskSpan(0, len(original), "ORGANIZATION_NAME", "test")
    masked = {
        MaskingRouter(b"test document", locale="en", organization_pool="general")
        .replacement(span, original)
        .text
        for _ in range(3)
    }
    assert len(masked) == 1, masked
    replacement = masked.pop()
    assert replacement and replacement != original
    # Still an organisation, and drawn from the pool for the medical ask.
    from_pool = (
        MaskingRouter(b"test document", locale="en", organization_pool="medical")
        .replacement(span, original)
        .text
    )
    assert from_pool.casefold() in {
        name.casefold() for name in organization_pool_for("en", "medical")
    }

    # A kind no locale defines at all is still a caller error.
    try:
        organization_pool_for("en", "dental")
    except ValueError as exc:
        assert "general or medical" in str(exc)
    else:
        raise AssertionError("an unknown pool kind should be rejected")


def test_the_checked_identifiers_mask_to_values_that_still_validate() -> None:
    """A surrogate that failed the detector's own check is a downgrade."""
    from masking_engine.app.engines.regex_uk import _nhs_ok
    from masking_engine.app.engines.regex_us import _ssn_ok

    def luhn_over_npi_prefix(value: str) -> bool:
        total = 0
        for index, character in enumerate(reversed(f"80840{value}")):
            digit = int(character)
            if index % 2 == 1:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        return total % 10 == 0

    for locale in ("en", "nl_BE"):
        router = MaskingRouter(b"test document", locale=locale)

        def replace(label: str, original: str) -> str:
            return router.replacement(
                MaskSpan(0, len(original), label, "test"), original
            ).text

        ssn = replace("SSN", "123-45-6789")
        assert re.fullmatch(r"\d{3}-\d{2}-\d{4}", ssn), ssn
        assert _ssn_ok(ssn) and ssn != "123-45-6789", ssn

        nhs = replace("NHS_NUMBER", "485 777 3456")
        assert re.fullmatch(r"\d{3} \d{3} \d{4}", nhs), nhs
        assert _nhs_ok(nhs), nhs

        npi = replace("NPI", "1023456789")
        assert re.fullmatch(r"\d{10}", npi), npi
        assert luhn_over_npi_prefix(npi), npi
        assert npi[0] in "12", npi

        # GMC has no checksum, so its digits are redrawn and an associate's
        # letter prefix survives -- the same treatment RPPS gets.
        gmc = replace("GMC", "A1234567")
        assert re.fullmatch(r"A\d{7}", gmc), gmc
        assert gmc != "A1234567"

        # A value that is not the shape the label is defined by is a detector
        # error, and is redrawn rather than replaced with a valid identifier.
        stub = replace("SSN", "12-34")
        assert re.fullmatch(r"\d{2}-\d{2}", stub), stub


def test_a_belgian_number_keeps_its_scheme_in_every_locale() -> None:
    """An INSZ is replaced by an INSZ-shaped number, not the document's own."""
    original = "81.09.22-237.91"
    span = MaskSpan(0, len(original), "INSZ_NISS", "test")
    for locale in locales.SUPPORTED_LOCALES:
        masked = (
            MaskingRouter(b"test document", locale=locale)
            .replacement(span, original)
            .text
        )
        assert _insz_ok(masked), (locale, masked)
        assert masked != original


def test_masking_an_english_document_twice_gives_the_same_bytes() -> None:
    """NiFi replays an idempotency key against these bytes."""
    values = [
        ("NAME", "James Mitchell"),
        ("ADDRESS", "482 Lakeshore Drive, Chicago, IL 60601"),
        ("DATE", "February 17, 2026"),
        ("SSN", "123-45-6789"),
        ("NPI", "1023456789"),
        ("ORGANIZATION_NAME", "Riverside Regional Medical Center"),
        ("URL", "https://www.riverside-regional-fictif.org"),
        ("GENDER", "male"),
    ]

    def run() -> list[str]:
        router = MaskingRouter(b"one english letter", locale="en")
        return [
            router.replacement(MaskSpan(0, len(text), label, "test"), text).text
            for label, text in values
        ]

    first, second = run(), run()
    assert first == second, [
        (a, b) for a, b in zip(first, second, strict=True) if a != b
    ]
    # And nothing came back unmasked -- except GENDER, which is allowed to.
    # The field holds one of three values, so a surrogate draws its own value
    # about a third of the time, and forcing a different one would leak more
    # than the collision does: a reader who knew the output could never equal
    # the input would have narrowed the original from three values to two.
    for (label, original), masked in zip(values, first, strict=True):
        if label == "GENDER":
            continue
        assert masked != original, label


def test_national_number_surrogates_are_pure_functions_of_their_seed() -> None:
    """A national number must not move with the calendar.

    `faker.ssn()` built the embedded birth date with `generator.date()`, whose
    window ends *now*, so one document masked on two days returned different
    bytes -- which defeats the idempotency-key replay the module docstring
    promises. Generated in-house from a fixed window instead. These constants
    pin that: a date lookup creeping back in changes them.
    """
    assert _insz_digits(random.Random(0)) == "79070226624"
    assert _insz_digits(random.Random(1)) == "47102882274"
    assert _nir_digits(random.Random(0)) == "279076649841553"
    assert _nir_digits(random.Random(1)) == "147103312150853"

    # and each one still satisfies the check its detector claimed it with
    for seed in range(500):
        assert _insz_ok(_insz_digits(random.Random(seed)))
        assert _nir_ok(_nir_digits(random.Random(seed)))


def main() -> None:
    """Run every test in this module.

    Discovered rather than listed by hand. This file had no runner at all and
    was named by no job, so its cases had never once executed; a hand-written
    list is the same failure one step later, because the next test added is
    the one nobody remembers to add to it.
    """
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
