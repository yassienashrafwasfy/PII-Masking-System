"""E-mail addresses, telephone numbers and URLs."""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlsplit
from ..core.schemas import MaskSpan
from .regex_dates import _FULL_OR_MONTH_DATE_RE, starts_with_calendar_date
from .regex_identifiers import _is_national_identifier
from .regex_shared import _has_identifier_context, _trim_right

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")

# A phone number never follows a letter directly, and never begins just after
# a separator that itself follows a digit. Without the first guard the
# subscriber-length tail of a VAT number, as in "GB078392443", reads as one;
# without the second, the tail of a dotted INSZ does.
#
# A written-out country code with neither a "+" nor a "00" ahead of it ("op
# 31/46/88/26/66/3", the "31" being the Netherlands) is otherwise
# indistinguishable from any other digit run, so that branch is only ever
# accepted downstream when an explicit phone cue precedes it.
#
# A "+" carries any country's code (1-3 digits, E.164): nothing but a
# telephone number is written that way, so an unfamiliar code is still a
# phone and is validated downstream on total length alone. The "00" form
# stays limited to the four deployment locales -- without a "+" a generic
# "00..." prefix is exactly the shape of a zero-padded identifier.
#
# Group separators include the non-breaking and thin spaces PDF text
# extraction leaves behind in place of ordinary spaces.
# The second lookbehind refuses a number carved out of a decimal or a date
# ("12.03", "1.234"). It used to refuse a "/" after a digit on the same
# rationale and that was too broad: a "/" is how a packed field separates one
# identifier from the next, so "...-78-901/09 245 67 89" hid a whole phone
# number behind it. A dot still bounds the match; "/" no longer does.
#
# `_NOT_A_LIST_SEPARATOR` is why the group class below is tempered rather than
# plain. A "/" belongs inside a number -- "02/512.34.56" is how Belgium writes
# an area code -- but a "/" with space around it is punctuation *between* two
# numbers, and the run was allowed to cross it: "03.502.688 / 0475 12 34 56"
# matched as the single candidate "03.502.688 / 0475", whose twelve digits are
# no national length, so the candidate was rejected and *both* numbers were
# released unmasked. A spaced "-" separates a pair the same way. Refusing to
# consume either ends the first number where it really ends, and leaves the
# scan free to find the second.
_NOT_A_LIST_SEPARATOR = r"(?![ \u00a0\u202f\u2009][/-])(?![/-][ \u00a0\u202f\u2009])"

_TELEFOON_RE = re.compile(
    r"(?<![\d+\-A-Za-z])(?<!\d[.])"
    r"\(?(?:\+\d{1,3}|00(?:31|32|33|44)|(?:31|32|33|44)|0)"
    r"(?:" + _NOT_A_LIST_SEPARATOR + r"[\d \u00a0\u202f\u2009()/.-]){7,18}(?!\d)"
)

_URL_RE = re.compile(r"\b(?:https?://|www\.)[\w.-]+(?:/[\w./?%&=+#-]*)?", re.IGNORECASE)

_URL_CONTEXT_RE = re.compile(
    r"\b(?:URL|website|web)\b[\s:=]{0,10}"
    r"(?P<value>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"\.[A-Za-z]{2,}(?:/[^\s]*)?)",
    re.IGNORECASE,
)


def _email_ok(value: str) -> bool:
    local, domain = value.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    return _domain_ok(domain)


def _domain_ok(host: str) -> bool:
    labels = host.split(".")
    return (
        len(labels) >= 2
        and len(labels[-1]) >= 2
        and labels[-1].isalpha()
        and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        )
    )


def _url_ok(value: str) -> bool:
    parsed = urlsplit(
        value if value.lower().startswith(("http://", "https://")) else f"//{value}"
    )
    host = parsed.hostname or ""
    return _domain_ok(host)


def _has_phone_context(text: str, start: int) -> bool:
    prefix = text[max(0, start - 30) : start]
    return (
        re.search(
            r"\b(?:tel(?:efoon|ephone)?|t[eé]l(?:[eé]phone)?|phone|gsm|mobiel"
            r"|mobile|portable|fax)\.?\s*[:.]?\s*$"
            # A named line ("noodlijn", "hotline") reads its number after a verb
            # ("op", "at"), not a colon, so it needs a wider gap than the
            # direct-label cues above.
            r"|\b(?:noodlijn|hotline|helpline)\b[^\r\n\d]{0,10}$",
            prefix,
            re.IGNORECASE,
        )
        is not None
    )


# Lengths are of the national number as written locally, i.e. including the
# leading trunk 0 where the locale writes one. An internationally-prefixed
# number drops that 0, so `+32 9 734394` is seven digits where the same number
# written locally ("09 734394") is eight -- hence the extra shorter length on
# each international entry rather than a separate table.
_NATIONAL_LENGTHS = {
    "31": {9, 10},  # Ten includes the optional written (0).
    "32": {7, 8, 9},
    "33": {9},
    "44": {9, 10, 11},
}

# A bare national number gives no country away, so any of the four national
# lengths is accepted for it.
_ANY_NATIONAL_LENGTH = frozenset().union(*_NATIONAL_LENGTHS.values())


class _Prefix(NamedTuple):
    """What the digits ahead of the subscriber number say about the country.

    `digits` is how many leading digits are prefix rather than national
    number, so the national length is measured by subtracting it. It counts
    digits rather than characters, and already accounts for a written trunk 0.
    """

    country: str
    digits: int
    bare_country_code: bool
    foreign_international: bool

    @property
    def international(self) -> bool:
        return self.country in _NATIONAL_LENGTHS or self.foreign_international


def _read_prefix(normalized: str) -> _Prefix:
    """Read the country prefix, if any, from a number as it was written."""
    country = ""
    digits = 0
    bare_country_code = False
    foreign_international = False
    if normalized[:1] == "+":
        candidate = normalized[1:3]
        if candidate in _NATIONAL_LENGTHS:
            country, digits = candidate, 2
        else:
            # A "+" carries any country's code, so an unfamiliar one is still
            # a phone; it is checked on E.164 length alone instead.
            foreign_international = True
        prefix_chars = 3
    elif normalized[:2] == "00":
        # The regex only matches "00" for the four deployment codes, so an
        # unknown "00..." country cannot reach here.
        country, digits = normalized[2:4], 4
        prefix_chars = 4
    elif normalized[:2] in _NATIONAL_LENGTHS:
        country, digits, bare_country_code = normalized[:2], 2, True
        prefix_chars = 2
    else:
        prefix_chars = 0

    # A written trunk prefix -- "+33(0)5...", "+32/0472..." or "+44 0 20..."
    # -- is a display convention showing what a domestic caller would dial
    # instead of the country code. Nobody dials both, so it is not part of the
    # number and must not be counted. In none of the four locales does a
    # subscriber number open with 0 once the trunk is dropped, so a 0 sitting
    # directly after the country code is the trunk in any of its spellings --
    # parenthesised, separated or glued. It reaches the digit run as a 0, so
    # drop one digit. Indexing is by characters here, not by digits: in
    # "+32..." the country code spans three characters but two digits.
    if country in _NATIONAL_LENGTHS and re.match(
        r"[^\d]{0,3}0", normalized[prefix_chars:]
    ):
        digits += 1
    return _Prefix(country, digits, bare_country_code, foreign_international)


def _national_length_ok(digit_count: int, prefix: _Prefix) -> bool:
    """Is this the right number of digits for the country the prefix names?

    A foreign "+" number has no per-country table here, so it is validated on
    its full E.164 length instead: 9 to 15 digits covers every assigned
    numbering plan.
    """
    if prefix.foreign_international:
        return 9 <= digit_count <= 15
    allowed = _NATIONAL_LENGTHS.get(prefix.country, _ANY_NATIONAL_LENGTH)
    return digit_count - prefix.digits in allowed


class _Shape(NamedTuple):
    """How the value is written out.

    A phone number has almost no structure beyond its length, so how it is
    grouped and punctuated is most of what separates one from a measurement,
    a date, or an identifier that happens to be the same digits.
    """

    decimal_like: bool
    calendar_like: bool
    dialling_style: bool
    well_grouped: bool
    separated: bool


def _read_shape(value: str) -> _Shape:
    """Measure the writing style of one matched digit run."""
    # A measurement writes few digits either side of the point. Five digits
    # before it is a phone group, as in "07911.703.920".
    decimal_like = bool(
        re.findall(r"(?<!\S)\d{1,3}\.\d{1,2}(?!\d)", value)
        if value.count(".") < 2
        else []
    )
    # A dot-grouped run such as "03.502.688" (2.3.3) is a legitimate dialling
    # style, but the broad month/date veto cannot tell it from a dotted date.
    # Only veto when the value has a strict calendar shape (1-2 . 1-2 .
    # 2-or-4); anything else keeps its phone chance.
    #
    # The separator set carries the space and the non-breaking space too, so
    # "01 02 1985" is vetoed the way "01.02.1985" already was. Without them
    # that form reached the phone detector and was masked as a telephone
    # number. No real number takes this shape: of 1,200 gold phone numbers
    # across six locales, none is written as two short groups followed by a
    # 2-or-4 digit group, because a dialling pattern's trailing group is itself
    # short.
    calendar_like = (
        re.fullmatch(r"\d{1,2}[./\-  ]\d{1,2}[./\-  ](?:\d{4}|\d{2})", value)
        is not None
    )
    # Dialling style: several short groups ("03.502.688", "07700.06.40.43").
    # National identifiers are either one bare run ("0954569369", "0676033164")
    # or carry a long leading group (INSZ "810922-237-91"). The identifier
    # guard therefore only applies to values that are NOT obviously
    # dialling-styled -- otherwise every dotted landline whose digits happen to
    # pass a checksum is lost to the phone detector.
    groups = re.findall(r"\d+", value)
    return _Shape(
        decimal_like=decimal_like,
        calendar_like=calendar_like,
        dialling_style=len(groups) >= 3 and max(len(group) for group in groups) <= 4,
        well_grouped=all(len(group) >= 2 for group in groups),
        separated=any(char in value for char in " \u00a0./-"),
    )


class _PhoneCandidate(NamedTuple):
    """One matched digit run, and everything the tests below ask about it."""

    text: str
    start: int
    value: str
    digits: str
    prefix: _Prefix
    shape: _Shape
    explicit_context: bool


def _not_a_decimal(candidate: _PhoneCandidate) -> bool:
    return (
        not candidate.shape.decimal_like
        or candidate.prefix.international
        or candidate.explicit_context
    )


def _not_ambiguously_local(candidate: _PhoneCandidate) -> bool:
    """A locally written number carrying separators is ambiguous.

    It reads as easily as a measurement, so it is accepted only with a cue
    word or when every digit group is at least two long -- a grouping no
    clinical measurement in the corpus produces.
    """
    ambiguous = not candidate.prefix.international and candidate.shape.separated
    return not ambiguous or candidate.explicit_context or candidate.shape.well_grouped


def _national_length(candidate: _PhoneCandidate) -> bool:
    return _national_length_ok(len(candidate.digits), candidate.prefix)


def _not_a_national_identifier(candidate: _PhoneCandidate) -> bool:
    """The digits are not a bare identifier that satisfies a national checksum.

    An internationally-prefixed number is unambiguously a phone -- no
    identifier scheme is ever written "+44..." -- and running the checksum over
    the country code as though it were part of the body made 12 valid UK
    numbers collide by coincidence.
    """
    return (
        candidate.prefix.international
        or candidate.shape.dialling_style
        or not _is_national_identifier(candidate.digits)
    )


def _not_a_date(candidate: _PhoneCandidate) -> bool:
    """The run is neither a date nor a date with something stuck to it.

    Two tests, because the whole-value one cannot see the case that matters
    most. `calendar_like` asks whether the entire value is a date, which
    catches "01.02.1985" written alone; but the pattern above starts matching
    at a digit inside a date and runs on through the separator into the
    leading digits of a time written beside it, so what actually arrives here
    is "05/05/1985 - 14" -- not a calendar shape, and therefore not vetoed.
    It then beat the DATE span on length in arbitration and was masked by
    shifting each digit on its own, which is how an appointment in 1985 came
    back reading 5570.

    So a value that merely *opens* with a real calendar date is refused too.
    No dialling style takes that shape: of 1,200 gold phone numbers across six
    locales, none begins with two short groups followed by a four-digit one.
    """
    if starts_with_calendar_date(candidate.value) is not None:
        return False
    return (
        candidate.shape.calendar_like is False
        or _FULL_OR_MONTH_DATE_RE.fullmatch(candidate.value) is None
    )


def _no_identifier_cue(candidate: _PhoneCandidate) -> bool:
    return (
        not _has_identifier_context(candidate.text, candidate.start)
        or candidate.explicit_context
    )


# Every one of these has to hold. Each is a separate reason a digit run that
# looks like a phone number is not one, so each carries its own name and its
# own evidence rather than being one clause of a single expression.
_PHONE_TESTS = (
    _not_a_decimal,
    _not_ambiguously_local,
    _national_length,
    _not_a_national_identifier,
    _not_a_date,
    _no_identifier_cue,
)


def _is_phone_number(
    text: str,
    start: int,
    value: str,
    digits: str,
    prefix: _Prefix,
    shape: _Shape,
    explicit_context: bool,
) -> bool:
    """Decide whether one matched digit run is really a telephone number."""
    candidate = _PhoneCandidate(
        text, start, value, digits, prefix, shape, explicit_context
    )
    return all(test(candidate) for test in _PHONE_TESTS)


def _phone_spans(text: str) -> list[MaskSpan]:
    """Detect Belgian, Dutch, French, United Kingdom and other "+"-prefixed numbers.

    Each deployment country's national digit count is checked after the prefix
    is removed, which is the only structure a phone number has. Any other
    country's "+"-prefixed number is accepted on its full E.164 length, the
    "+" itself being the disambiguator. Everything else is a question of how
    the value is written, which `_read_shape` measures and `_is_phone_number`
    weighs against the cue words around it.
    """
    spans: list[MaskSpan] = []
    for match in _TELEFOON_RE.finditer(text):
        end = _trim_right(text, match.start(), match.end(), " .,/;-)\u00a0")
        value = text[match.start() : end]
        prefix = _read_prefix(value.lstrip("("))
        explicit_context = _has_phone_context(text, match.start())
        # A written-out country code carries no "+" or "00" to mark it as one,
        # so on its own it is any other digit run -- accept it only when a
        # phone cue precedes it.
        if prefix.bare_country_code and not explicit_context:
            continue
        if _is_phone_number(
            text,
            match.start(),
            value,
            re.sub(r"\D", "", value),
            prefix,
            _read_shape(value),
            explicit_context,
        ):
            spans.append(MaskSpan(match.start(), end, "TELEFOON", "regex"))
    return spans
