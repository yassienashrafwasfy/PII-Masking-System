"""Everything a surrogate needs that varies by the document's language.

The corpus was Belgian only, so every surrogate read as Belgian Dutch and one
module-level `Faker("nl_BE")` was the whole of it. It now carries French and
English documents too, and a pseudonym has to read like the document it
replaced a value in: a Flemish alias in a Dutch note, a French one in a French
note, a British one in an English note.

This lives in `core` rather than beside the router because two callers need
it and neither should own it. `app/engines/masking_router.py` reads a profile
to build its surrogates, and `app/core/config.py` validates the configured
locale against the same set -- the way both already read the taxonomy from
`app/core/label_registry.py` rather than each keeping a copy.

One profile decides every label in a document, including the country-bound
identifiers: a NIR in an `nl_BE` document is replaced with a Belgian national
number and an INSZ in an `fr_FR` document with a French one. That is a
deliberate ruling in favour of one locale per document over per-label
locales. It keeps the router single-locale, at the cost of changing an
identifier's digit count on a cross-border document.

Adding a locale means adding an entry here and nothing else. A label whose
surrogate is locale-independent -- every number redrawn digit by digit, an
age, a URL path -- needs no entry at all.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

DUTCH_MONTHS = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)
DUTCH_MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mrt": 3,
    "maa": 3,
    "apr": 4,
    "mei": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "okt": 10,
    "nov": 11,
    "dec": 12,
}

FRENCH_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
# Accents are stripped before lookup (see `month_number`), so only one
# spelling of each abbreviation is listed here and "fev" also covers "fév".
FRENCH_MONTH_ABBREVIATIONS = {
    "janv": 1,
    "jan": 1,
    "fevr": 2,
    "fev": 2,
    "mars": 3,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7,
    "aout": 8,
    "sept": 9,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Written with a capital, unlike the Dutch and French names: English
# capitalises a month in the middle of a sentence. The router renders through
# `_match_case`, so a source that wrote "february" still gets a lowercased
# surrogate.
ENGLISH_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ENGLISH_MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
GERMAN_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
GERMAN_MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dez": 12,
}
SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
SPANISH_MONTH_ABBREVIATIONS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
ROMANIAN_MONTHS = (
    "ianuarie",
    "februarie",
    "martie",
    "aprilie",
    "mai",
    "iunie",
    "iulie",
    "august",
    "septembrie",
    "octombrie",
    "noiembrie",
    "decembrie",
)
ROMANIAN_MONTH_ABBREVIATIONS = {
    "ian": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mai": 5,
    "iun": 6,
    "iul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "noi": 11,
    "dec": 12,
}

# A gender or a sex is written as a word or as a single letter. A surrogate
# keeps the form it replaced, so a one-letter field stays one letter. The
# letters differ by language as much as the words do: Dutch writes V for
# "vrouw" where French writes F for "femme".
DUTCH_GENDER_WORDS = ("man", "vrouw", "non-binair")
DUTCH_GENDER_LETTERS = ("M", "V", "X")
FRENCH_GENDER_WORDS = ("homme", "femme", "non-binaire")
FRENCH_GENDER_LETTERS = ("M", "F", "X")
ENGLISH_GENDER_WORDS = ("male", "female", "non-binary")
ENGLISH_GENDER_LETTERS = ("M", "F", "X")

GERMAN_GENDER_WORDS = ("männlich", "weiblich", "divers")
GERMAN_GENDER_LETTERS = ("M", "W", "D")
SPANISH_GENDER_WORDS = ("masculino", "femenino", "no binario")
SPANISH_GENDER_LETTERS = ("M", "F", "X")
ROMANIAN_GENDER_WORDS = ("masculin", "feminin", "non-binar")
ROMANIAN_GENDER_LETTERS = ("M", "F", "X")


def _no_ordinal(day: int) -> str:
    """Return what Dutch writes after a day of the month: nothing."""
    return ""


def _french_ordinal(day: int) -> str:
    """Return "er" for the first of the month, nothing for any other day."""
    return "er" if day == 1 else ""


def _english_ordinal(day: int) -> str:
    """Return the English suffix for *day*: 1st, 2nd, 3rd, 4th, 11th, 21st."""
    if day % 100 in {11, 12, 13}:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


@dataclass(frozen=True)
class LocaleProfile:
    """Hold the language-dependent inputs to one document's surrogates."""

    # The Faker locale backing names, addresses, e-mail and organisation
    # names. National numbers are generated in-house per label rather than by
    # Faker (see `surrogate_national_number`), so they need no entry here.
    faker_locale: str
    months: tuple[str, ...]
    month_abbreviations: dict[str, int]
    gender_words: tuple[str, ...]
    gender_letters: tuple[str, ...]
    # A masked URL keeps a plausible national host, so the TLD follows the
    # document rather than staying Belgian in a French note.
    url_tld: str
    # What this language writes after the day of the month. One string was
    # enough while the locales were French, which writes "1er janvier" and
    # then "2 janvier", and Dutch, which writes no suffix at all. English
    # needs a different suffix for nearly every day -- 1st, 2nd, 3rd, 4th,
    # 11th -- so the field carries the rule rather than a single suffix.
    day_ordinal: Callable[[int], str]
    # Whether the language requires the ordinal whatever the source wrote.
    # French does: "1 janvier" is a misspelling, so a surrogate writes "1er"
    # even where the source did not. English does not: "17 February" is
    # ordinary, so a surrogate carries "3rd" only where the source carried an
    # ordinal of its own.
    ordinal_is_mandatory: bool
    # Checked-in organisation-name pools, by `organization_pool` kind. Faker
    # has no generator that produces a plausible national trade name, so each
    # locale carries curated pools scraped from that country's own open
    # registers -- KVK and CIBG for Dutch, SIRENE and FINESS for French, NHS
    # ODS and CMS provider-data for English. The files sit beside this module;
    # see `scripts/build_*_organization_pool.py`.
    #
    # A locale need not carry every kind. English carries a medical pool only,
    # and a request for its general pool falls back to a seeded Faker company
    # name rather than failing the document.
    organization_pools: dict[str, str]
    # A checked-in clinical term pool -- medication, symptom and diagnosis
    # names Faker cannot generate -- read once per process the way an
    # organisation pool is. A pseudonym has to read like the document it
    # replaced a value in, so each locale carries its own file rather than
    # every document drawing the Dutch pool regardless of language. The file
    # sits beside this module; see `scripts/build_clinical_vocabularies.py`.
    vocabulary_file: str


LOCALES: dict[str, LocaleProfile] = {
    "nl_BE": LocaleProfile(
        faker_locale="nl_BE",
        months=DUTCH_MONTHS,
        month_abbreviations=DUTCH_MONTH_ABBREVIATIONS,
        gender_words=DUTCH_GENDER_WORDS,
        gender_letters=DUTCH_GENDER_LETTERS,
        url_tld="be",
        day_ordinal=_no_ordinal,
        ordinal_is_mandatory=False,
        organization_pools={
            "general": "surrogate_organizations_nl.txt",
            "medical": "surrogate_medical_organizations_nl.txt",
        },
        vocabulary_file="surrogate_vocabularies_nl.json",
    ),
    "fr_FR": LocaleProfile(
        faker_locale="fr_FR",
        months=FRENCH_MONTHS,
        month_abbreviations=FRENCH_MONTH_ABBREVIATIONS,
        gender_words=FRENCH_GENDER_WORDS,
        gender_letters=FRENCH_GENDER_LETTERS,
        url_tld="fr",
        day_ordinal=_french_ordinal,
        ordinal_is_mandatory=True,
        organization_pools={
            "general": "surrogate_organizations_fr.txt",
            "medical": "surrogate_medical_organizations_fr.txt",
        },
        vocabulary_file="surrogate_vocabularies_fr.json",
    ),
    # One English rather than a British and an American one. The two
    # populations overlap far too much for a document's language to settle
    # which of them wrote it, and `lingua` reports a single ENGLISH either
    # way. The house style is British -- names, streets and postcodes come
    # from `en_GB` -- while the identifiers of both countries are detected and
    # masked, which is the half that matters for privacy. See
    # `docs/decisions/004-english-locale.md`.
    "en": LocaleProfile(
        faker_locale="en_GB",
        months=ENGLISH_MONTHS,
        month_abbreviations=ENGLISH_MONTH_ABBREVIATIONS,
        gender_words=ENGLISH_GENDER_WORDS,
        gender_letters=ENGLISH_GENDER_LETTERS,
        url_tld="uk",
        day_ordinal=_english_ordinal,
        ordinal_is_mandatory=False,
        organization_pools={
            "medical": "surrogate_medical_organizations_en.txt",
        },
        vocabulary_file="surrogate_vocabularies_en.json",
    ),
    "de_DE": LocaleProfile(
        faker_locale="de_DE",
        months=GERMAN_MONTHS,
        month_abbreviations=GERMAN_MONTH_ABBREVIATIONS,
        gender_words=GERMAN_GENDER_WORDS,
        gender_letters=GERMAN_GENDER_LETTERS,
        url_tld="de",
        day_ordinal=_no_ordinal,
        ordinal_is_mandatory=False,
        organization_pools={},
        vocabulary_file="surrogate_vocabularies_de.json",
    ),
    "es_ES": LocaleProfile(
        faker_locale="es_ES",
        months=SPANISH_MONTHS,
        month_abbreviations=SPANISH_MONTH_ABBREVIATIONS,
        gender_words=SPANISH_GENDER_WORDS,
        gender_letters=SPANISH_GENDER_LETTERS,
        url_tld="es",
        day_ordinal=_no_ordinal,
        ordinal_is_mandatory=False,
        organization_pools={},
        vocabulary_file="surrogate_vocabularies_es.json",
    ),
    "ro_RO": LocaleProfile(
        faker_locale="ro_RO",
        months=ROMANIAN_MONTHS,
        month_abbreviations=ROMANIAN_MONTH_ABBREVIATIONS,
        gender_words=ROMANIAN_GENDER_WORDS,
        gender_letters=ROMANIAN_GENDER_LETTERS,
        url_tld="ro",
        day_ordinal=_no_ordinal,
        ordinal_is_mandatory=False,
        organization_pools={},
        vocabulary_file="surrogate_vocabularies_ro.json",
    ),
}

# The Belgian corpus predates the French one, so an unconfigured deployment
# keeps masking exactly as it did before French support existed.
DEFAULT_LOCALE = "nl_BE"

SUPPORTED_LOCALES = tuple(sorted(LOCALES))

# Every kind of organisation pool some locale carries. Derived rather than
# listed, so a new kind is declared once in a profile. A locale need not carry
# all of them -- English carries `medical` only -- which is why this is the set
# of kinds a caller may *ask* for rather than the set any one locale answers.
POOL_KINDS = frozenset(
    kind for entry in LOCALES.values() for kind in entry.organization_pools
)


def profile(locale: str) -> LocaleProfile:
    """Return the profile for *locale*, or raise ValueError naming the set."""
    try:
        return LOCALES[locale]
    except KeyError as exc:
        raise ValueError(
            f"locale must be one of: {', '.join(SUPPORTED_LOCALES)}."
        ) from exc


def _fold(word: str) -> str:
    """Return *word* lowercased with its accents and trailing dot removed.

    A PDF text layer loses an accent often enough that "fevrier" has to reach
    the same month as "février". Folding both sides of the comparison covers
    every accented month name in every locale at once, rather than listing
    each unaccented spelling and missing one -- which is what happened to
    "fevrier" and "decembre".
    """
    stripped = unicodedata.normalize("NFKD", word.lower().rstrip("."))
    return "".join(c for c in stripped if not unicodedata.combining(c))


@cache
def _months_by_folded_name() -> dict[str, int]:
    """Return every month name and abbreviation, folded, to its number."""
    table: dict[str, int] = {}
    for entry in LOCALES.values():
        for index, name in enumerate(entry.months):
            table.setdefault(_fold(name), index + 1)
        for name, month in entry.month_abbreviations.items():
            table.setdefault(_fold(name), month)
    return table


def month_number(word: str) -> int | None:
    """Return the month *word* names, in any locale this engine supports.

    Parsing stays deliberately locale-independent while rendering does not. A
    Dutch note can quote a French date and a French note a Dutch one, and a
    month name the parser fails to recognise costs the whole date its written
    form: it falls through to a numeric render and comes back as an ISO date,
    changing the shape of the field. Accepting every locale's names costs
    nothing, because a name that is a month in one language is not a
    different month in the other.
    """
    return _months_by_folded_name().get(_fold(word))


@cache
def _full_month_names() -> frozenset[str]:
    """Return every locale's written-out month names, folded."""
    return frozenset(_fold(name) for entry in LOCALES.values() for name in entry.months)


def is_full_month_name(word: str) -> bool:
    """Say whether *word* writes a month out in full rather than abbreviating.

    Length cannot answer this. "mars", "mai", "juin" and "août" are whole
    French months and "mei", "juni" and "juli" whole Dutch ones, yet all seven
    are no longer than the four characters a "sept."-style abbreviation runs
    to. Deciding on length alone rendered a date the source had written out in
    full back as an abbreviation, so the comparison is against the names
    themselves.

    Locale-independent for the same reason `month_number` is: the caller is
    asking about the form the source used, and a French month quoted in a
    Dutch note is written out in full there too.
    """
    return _fold(word) in _full_month_names()
