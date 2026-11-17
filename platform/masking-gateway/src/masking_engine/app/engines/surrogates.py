"""One function per surrogate kind, and the registry that chooses between them.

Each function receives a `SurrogateContext` carrying everything it is allowed
to use: the label, the original text, the document's locale, its organisation
pool, and the two seeded generators. Nothing here reaches into the router, so a
strategy can be read and tested on its own, and adding a label means adding one
function and one registry entry.

The generators arrive as callables rather than values on purpose. Seeding the
shared Faker instance mutates it, and building both for every span would pay
for a generator no strategy asked for.
"""

from __future__ import annotations

import logging
import random
import re
import string
from collections.abc import Callable
from dataclasses import dataclass

from faker import Faker

from ..core.locales import LocaleProfile
from .surrogate_dates import (
    ParsedDate,
    _redraw_date,
    _year_offsets,
    unparsed_date_anchor,
)
from .surrogate_numbers import (
    _DIGIT_RUN_RE,
    _insz_digits,
    _nir_digits,
    _dialling_prefix_length,
    _cnp_digits,
    _colegiado_digits,
    _dni_identifier,
    _nhs_digits,
    _lanr_digits,
    _nie_identifier,
    _npi_digits,
    _parafa_digits,
    _place_digits,
    _place_identifier,
    _ssn_digits,
    _steuer_id_digits,
)
from .surrogate_pools import VOCABULARY_LABELS, vocabulary_for
from .surrogate_text import _match_case, _match_organization_case

log = logging.getLogger(__name__)

_URL_SCHEME_RE = re.compile(r"^(https?://)?(www\.)?", re.I)

_ANY_DIGIT_RE = re.compile(r"\d")

# A provider number, a dose, and a measured result carry their meaning in a
# unit or a separator, so only the digits are redrawn. RPPS joins RIZIV as
# the French provider register's number: Faker has no generator for it and
# the detector applies no checksum to it, so redrawing the digits produces a
# surrogate exactly as valid as the values the engine recognises.
# GMC joins them for a third time over: the register publishes a seven-digit
# reference carrying an optional "A" prefix for an associate, Faker has no
# generator for it, and the detector applies no checksum -- so redrawing the
# digits and leaving the prefix produces a surrogate exactly as valid as the
# values the engine recognises.
_DIGITS_ONLY_LABELS = frozenset(
    {"RIZIV_INAMI", "RPPS", "HEIGHT", "WEIGHT", "BMI", "GMC"}
)

# A national number generated in full rather than by redrawing digits, so the
# surrogate satisfies the same validator the detector claimed the original
# with -- `_nir_ok` for a NIR, `_insz_ok` for an INSZ. Generated here rather
# than by Faker: `faker.ssn()` builds the embedded birth date with
# `generator.date()`, whose window ends *now*, so the same document masked on
# two different days returned different bytes.
_NATIONAL_NUMBER_LABELS = frozenset({"INSZ_NISS", "NIR"})

# An identifier whose surrogate has to satisfy the same check the detector
# validated the original with, mapped to its digit count and its generator. A
# masked NHS number that failed `_nhs_ok` would be a downgrade a reader could
# spot, the way a masked NIR that failed `_nir_ok` would be. Faker has a
# provider for none of the three -- and `en_GB.ssn()` returns a National
# Insurance number, "ZZ 099603 T", which would put letters where an SSN field
# had nine digits -- so each carries a generator of its own.
#
# Locale-independent, unlike `_NATIONAL_NUMBER_LABELS`: an NHS number is a
# mod-11 ten-digit value in a Dutch note as much as in an English one, so the
# surrogate follows the identifier rather than the document it sits in.
_CHECKED_NUMBER_LABELS = {
    "SSN": (9, _ssn_digits),
    "NPI": (10, _npi_digits),
    "NHS_NUMBER": (10, _nhs_digits),
    "CNP": (13, _cnp_digits),
    "STEUER_ID": (11, _steuer_id_digits),
    "LANR": (9, _lanr_digits),
    "COLEGIADO": (9, _colegiado_digits),
    "PARAFA": (5, _parafa_digits),
}

_CHECKED_IDENTIFIER_LABELS = {"DNI": _dni_identifier, "NIE": _nie_identifier}


@dataclass(frozen=True)
class SurrogateContext:
    """Everything a surrogate is allowed to depend on, and nothing else."""

    label: str
    original: str
    locale: LocaleProfile
    organization_pool: tuple[str, ...]
    random: Callable[[], random.Random]
    faker: Callable[[], Faker]
    date_of: Callable[[str], ParsedDate | None]


def redraw(ctx: SurrogateContext, *, letters: bool) -> str:
    """Return the original with its digits redrawn, separators kept.

    Faker has no RIZIV/INAMI provider, so keeping the punctuation of the source
    keeps the field recognizable as what it is. A label with no generator at
    all redraws its letters as well, which keeps a field readable as a field
    without keeping its value.
    """
    generator = ctx.random()
    characters = []
    for character in ctx.original:
        if character.isdigit():
            characters.append(generator.choice(string.digits))
        elif letters and character.isalpha():
            pool = (
                string.ascii_uppercase
                if character.isupper()
                else string.ascii_lowercase
            )
            characters.append(generator.choice(pool))
        else:
            characters.append(character)
    return "".join(characters)


def from_vocabulary(ctx: SurrogateContext) -> str:
    """Return a surrogate drawn from the clinical vocabulary for this label."""
    values = vocabulary_for(ctx.locale, ctx.label)
    chosen = ctx.random().choice(values)
    return _match_case(chosen, ctx.original)


def surrogate_date(ctx: SurrogateContext) -> str:
    parsed = ctx.date_of(ctx.original)
    generator = ctx.random()
    if parsed is None:
        # A date this release cannot read still leaves the document. The log
        # names the label, so an operator can see which formats need work.
        log.info("mask_date_not_parsed label=%s", ctx.label)
        anchor = unparsed_date_anchor()
        offset = generator.choice(_year_offsets(anchor, two_digit_year=False))
        return _redraw_date(anchor, offset, generator).isoformat()
    value, render, two_digit_year = parsed
    offset = generator.choice(_year_offsets(value, two_digit_year=two_digit_year))
    return render(_redraw_date(value, offset, generator))


def surrogate_checked_number(ctx: SurrogateContext) -> str:
    """Return a surrogate that passes the same check the original did."""
    width, draw = _CHECKED_NUMBER_LABELS[ctx.label]
    if len(re.sub(r"\D", "", ctx.original)) != width:
        # Not the digit count the label is defined by, so a detector error
        # over some other value. Redrawn rather than replaced with a valid
        # identifier the document never carried.
        return redraw(ctx, letters=True)
    generator = ctx.random()
    # A draw can land on the value it replaced, which would return the
    # identifier unmasked. Each pass draws afresh, so a retry is a new draw
    # rather than the same one.
    for _ in range(8):
        candidate = _place_digits(ctx.original, draw(generator))
        if candidate != ctx.original:
            return candidate
    return candidate


def surrogate_checked_identifier(ctx: SurrogateContext) -> str:
    """Return a DNI or NIE that keeps the source's separators."""
    draw = _CHECKED_IDENTIFIER_LABELS[ctx.label]
    generator = ctx.random()
    for _ in range(8):
        candidate = _place_identifier(ctx.original, draw(generator))
        if candidate != ctx.original:
            return candidate
    return candidate


def surrogate_age(ctx: SurrogateContext) -> str:
    """Return an age with every digit run redrawn to the same width.

    Redrawing only the leading run left the rest of the value in the document:
    "1-2-3" kept "-2-3", and a one-digit age redrew itself in one document in
    nine, so the age left the document unmasked. Each run keeps its digit
    count, so a wrapped PDF span still slices to its source runs, and no run
    opens with a zero because an age does not.
    """
    generator = ctx.random()
    if not _ANY_DIGIT_RE.search(ctx.original):
        # A span with no digits at all is a detector error over real text,
        # so its letters are redrawn rather than trusted.
        return redraw(ctx, letters=True)

    def redraw_run(match: re.Match[str]) -> str:
        width = len(match[0])
        low = 1 if width == 1 else 10 ** (width - 1)
        return str(generator.randrange(low, 10**width))

    # A redraw can land on the value it replaced, which would return the age
    # unmasked. Each pass draws afresh, so a retry is a new draw.
    for _ in range(8):
        candidate = _DIGIT_RUN_RE.sub(redraw_run, ctx.original)
        if candidate != ctx.original:
            return candidate
    return candidate


def surrogate_phone(ctx: SurrogateContext) -> str:
    """Return a deterministic phone surrogate in the source's format."""
    generator = ctx.random()
    original = ctx.original
    prefix_digits = _dialling_prefix_length(original, re.sub(r"\D", "", original))
    digit_index = 0
    replacement = []
    for character in original:
        if not character.isdigit():
            replacement.append(character)
        elif digit_index < prefix_digits:
            replacement.append(character)
        else:
            replacement.append(str((int(character) + generator.randrange(1, 10)) % 10))
        digit_index += character.isdigit()
    return "".join(replacement)


def surrogate_organization(ctx: SurrogateContext) -> str:
    """Return a deterministic organisation from the configured pool."""
    if not ctx.organization_pool:
        # A locale with no curated register of this kind. A seeded Faker
        # company name reads less like a real trade name than a register
        # entry does, but it is deterministic and it is an organisation,
        # which is what the field has to carry.
        return ctx.faker().company()
    width = len(ctx.original.strip())
    fitting = tuple(name for name in ctx.organization_pool if len(name) <= width)
    candidates = fitting or ctx.organization_pool
    index = ctx.random().randrange(len(candidates))
    candidate = candidates[index]
    if candidate.casefold() == ctx.original.strip().casefold():
        candidate = candidates[(index + 1) % len(candidates)]
    return _match_organization_case(candidate, ctx.original)


def surrogate_url(ctx: SurrogateContext) -> str:
    """Return a deterministic plausible national URL with no source path."""
    generator = ctx.random()
    match = _URL_SCHEME_RE.match(ctx.original.strip())
    scheme, www = match.groups() if match is not None else (None, None)
    host = f"masked-{generator.getrandbits(32):08x}.{ctx.locale.url_tld}"
    return f"{scheme.lower() if scheme else ''}{www.lower() if www else ''}{host}"


def surrogate_gender(ctx: SurrogateContext) -> str:
    """Return a gender or sex surrogate in the form the original used."""
    generator = ctx.random()
    stripped = ctx.original.strip()
    if len(stripped) == 1:
        return generator.choice(ctx.locale.gender_letters)
    return _match_case(generator.choice(ctx.locale.gender_words), stripped)


def surrogate_email(ctx: SurrogateContext) -> str:
    return ctx.faker().email()


def surrogate_name(ctx: SurrogateContext) -> str:
    # One token is a given name or a surname on its own. Replacing it with a
    # full name would add a person the document never named.
    faker = ctx.faker()
    return faker.first_name() if len(ctx.original.split()) == 1 else faker.name()


def surrogate_address(ctx: SurrogateContext) -> str:
    faker = ctx.faker()
    return f"{faker.street_address()} {faker.postcode()} {faker.city()}"


# An identifier keeps its own scheme in every document: an INSZ is replaced
# by an INSZ-shaped number and a NIR by a NIR-shaped one, whatever language
# the document around them is written in. The cue word beside the value names
# the scheme ("NIR :", "rijksregisternummer"), so arriving as another
# country's shape would read as wrong and could fail a downstream format
# check -- the same reason the checked-number labels below are
# locale-independent. Generated in-house rather than by Faker for the reason
# `_insz_digits`/`_nir_digits` document: determinism across calendar days.
_NATIONAL_NUMBER_DIGITS = {"INSZ_NISS": _insz_digits, "NIR": _nir_digits}


def surrogate_national_number(ctx: SurrogateContext) -> str:
    draw = _NATIONAL_NUMBER_DIGITS[ctx.label]
    generator = ctx.random()
    # A draw can land on the value it replaced, which would return the
    # identifier unmasked, so each pass draws afresh.
    for _ in range(8):
        candidate = draw(generator)
        if candidate != re.sub(r"\D", "", ctx.original):
            return candidate
    return candidate


# Exact-label strategies. Safe as a mapping rather than an ordered chain
# because these labels are disjoint from every set consulted below, so no
# label could ever match two arms.
BY_LABEL: dict[str, Callable[[SurrogateContext], str]] = {
    "ADDRESS": surrogate_address,
    "AGE": surrogate_age,
    "DATE": surrogate_date,
    "EMAIL": surrogate_email,
    "GENDER": surrogate_gender,
    "NAME": surrogate_name,
    "ORGANIZATION_NAME": surrogate_organization,
    "PHONE_NUMBER": surrogate_phone,
    "URL": surrogate_url,
}


def resolve(ctx: SurrogateContext) -> str:
    """Return the surrogate for this context, choosing how to make it."""
    label = ctx.label
    if label in VOCABULARY_LABELS:
        return from_vocabulary(ctx)
    strategy = BY_LABEL.get(label)
    if strategy is not None:
        return strategy(ctx)
    if label in _DIGITS_ONLY_LABELS:
        return redraw(ctx, letters=False)
    if label in _CHECKED_NUMBER_LABELS:
        return surrogate_checked_number(ctx)
    if label in _CHECKED_IDENTIFIER_LABELS:
        return surrogate_checked_identifier(ctx)
    if label in _NATIONAL_NUMBER_LABELS:
        return surrogate_national_number(ctx)
    # A label with no rule of its own still leaves the document.
    return redraw(ctx, letters=True)
