"""Every identifier detector, and the cross-country questions.

A country's own patterns live in its `regex_<country>` module. What is
left here is what has no single country: a record number no country
standardises, a VAT-style organisation id that spans six of them, an
insurance-number cue written in four languages, and the union asking
whether a digit run satisfies *any* national checksum."""

from __future__ import annotations

import re
from collections.abc import Callable

from ..core.schemas import MaskSpan
from .regex_belgium import (
    _BTW_RE,
    _INSZ_CONTEXT_RE,
    _INSZ_RE,
    _RIZIV_COMPACT_RE,
    _RIZIV_CONTEXT_RE,
    _RIZIV_GROUPED_RE,
    _btw_ok,
    _insz_ok,
    _riziv_ok,
)
from .regex_dates import starts_with_calendar_date
from .regex_france import _NIR_CONTEXT_RE, _NIR_RE, _RPPS_CONTEXT_RE, _nir_ok
from .regex_germany import (
    _HEALTH_INSURANCE_DE_RE,
    _LANR_CONTEXT_RE,
    _STEUER_ID_CONTEXT_RE,
    _STEUER_ID_RE,
    _kvnr_ok,
    _lanr_ok,
    _steuer_id_ok,
)
from .regex_rules import Candidate, Guard, Rule, collect, either, every
from .regex_romania import _CNP_CONTEXT_RE, _CNP_RE, _PARAFA_CONTEXT_RE, _cnp_ok
from .regex_shared import _after_phone_prefix
from .regex_spain import (
    _COLEGIADO_CONTEXT_RE,
    _DNI_CONTEXT_RE,
    _DNI_RE,
    _NIE_RE,
    _dni_ok,
    _nie_ok,
)
from .regex_uk import _GMC_CONTEXT_RE, _NHS_CONTEXT_RE, _NHS_RE, _nhs_ok
from .regex_us import _NPI_CONTEXT_RE, _SSN_CONTEXT_RE, _SSN_RE, _ssn_ok

# Three letters and seven digits. The taxonomy records this shape as invented,
# because no country standardises a hospital record number, so it is a shape
# check against this deployment's own convention and not a national rule.
_MRN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}\d{7}(?![A-Za-z0-9])")

_MRN_CONTEXT_RE = re.compile(
    r"\b(?:MRN|dossier(?:nummer)?|patient(?:en)?nummer|record\s+number"
    r"|hospital\s+number|num[eé]ro\s+de\s+dossier|n[°o]\s*dossier)\b"
    r"[^\r\n]{0,20}?(?P<value>[A-Za-z]{3}\d{7}|\d(?:[ .\-/]?\d){6})"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

_VAT_DIGITS = {"BE": 10, "GB": 9, "DE": 9, "ES": 9, "FR": 9, "RO": 9}

_ORGANIZATION_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<country>BE|GB|DE|ES|FR|RO)[ .\-/]?"
    r"(?P<body>\d(?:[ .\-/]?\d){7,9})(?![A-Za-z0-9])"
)

_HEALTH_INSURANCE_CONTEXT_RE = re.compile(
    r"\b(?:KVNR|versicherten(?:nummer)?|mutualiteit|ziekenfonds"
    r"|health\s+insurance|insurance\s+number|num[eé]ro\s+d'assur[eé]"
    r"|n[uú]mero\s+de\s+afiliaci[oó]n)\b[^\r\n]{0,20}?"
    r"(?P<value>[A-Z]{0,4}[ .\-/]?\d(?:[ .\-/]?\d){8,13})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _organization_id_ok(country: str, body: str) -> bool:
    """A known country prefix with that country's digit count, Belgium checked."""
    digits = re.sub(r"\D", "", body)
    if len(digits) != _VAT_DIGITS.get(country.upper(), -1):
        return False
    if country.upper() == "BE":
        return int(digits[8:]) == 97 - int(digits[:8]) % 97
    return True


def _is_national_identifier(value: str) -> bool:
    """True if a digit run satisfies any national scheme's checksum.

    Used to keep a national number out of the phone detector. An INSZ for a
    birth year in the 2000s opens with a zero and is otherwise shaped exactly
    like a national phone number.
    """
    return (
        _insz_ok(value)
        or _riziv_ok(value)
        or _nir_ok(value)
        or _nhs_ok(value)
        or _cnp_ok(value)
        or _steuer_id_ok(value)
    )


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _checksum(ok: Callable[[str], bool]) -> Guard:
    """The value passes a national scheme's own check."""
    return lambda c: ok(c.value)


def _digit_count(*sizes: int) -> Guard:
    """The value carries one of the digit counts the scheme allows."""
    return lambda c: len(_digits(c.value)) in sizes


def _not_after_phone_prefix(c: Candidate) -> bool:
    """The run is not the tail of a phone number written with a country code."""
    return not _after_phone_prefix(c.text, c.start)


def _not_date_led(c: Candidate) -> bool:
    """The run does not open with a real calendar date.

    A date written beside a time reaches the bare digit-run rules as one
    value -- "27 10 1985 09" is ten digits and passed the NHS checksum by
    coincidence, which replaced the whole thing, date included, with a freshly
    generated identifier. No national identifier is written as a calendar date
    followed by more digits, so the shape is evidence against every rule that
    reads a bare run rather than a cued one.
    """
    return starts_with_calendar_date(c.value) is None


def _is_separated(c: Candidate) -> bool:
    """The value is spelled with separators, which is evidence on its own."""
    return any(sep in c.value for sep in "./- ")


def _not_bare_steuer_id(c: Candidate) -> bool:
    """A bare run passing the Steuer-ID check belongs to that rule.

    The Steuer-ID check (ISO 7064 over ten digits plus the repetition rule)
    is far stricter than mod-97 or mod-89, so a bare eleven-digit run passing
    it is a Steuer-ID that happens to satisfy the looser check, not the
    reverse. Separated spellings keep their own rule: the separators are
    scheme evidence of their own, and a dotted INSZ is not a bare Steuer-ID.
    """
    return _is_separated(c) or not _steuer_id_ok(c.value)


def _organization_id(c: Candidate) -> bool:
    return _organization_id_ok(c.match.group("country"), c.match.group("body"))


# Order is part of the contract: arbitration downstream sees these in the
# order they are listed, so a context-cued rule stays ahead of the bare one.
_IDENTIFIER_RULES: tuple[Rule, ...] = (
    Rule(_INSZ_CONTEXT_RE, "INSZ", "value"),
    # Separated spellings pass on shape alone; a bare digit run needs the
    # mod-97 check (explicit INSZ/NISS context rescues it in the rule above).
    Rule(
        _INSZ_RE,
        "INSZ",
        guard=every(
            _not_after_phone_prefix,
            _not_date_led,
            either(
                _is_separated,
                every(_checksum(_insz_ok), _not_bare_steuer_id),
            ),
        ),
    ),
    Rule(_RIZIV_CONTEXT_RE, "RIZIV", "value", _digit_count(8, 11)),
    # Canonical 1-5-2(-3) structure with any separators; other spellings need
    # the explicit RIZIV/INAMI context path above.
    Rule(
        _RIZIV_GROUPED_RE,
        "RIZIV",
        "value",
        every(_not_after_phone_prefix, _not_date_led),
    ),
    Rule(
        _RIZIV_COMPACT_RE,
        "RIZIV",
        guard=every(
            _checksum(_riziv_ok),
            _not_after_phone_prefix,
            _not_date_led,
            _not_bare_steuer_id,
        ),
    ),
    Rule(_NIR_CONTEXT_RE, "NIR", "value"),
    Rule(
        _NIR_RE,
        "NIR",
        guard=every(_checksum(_nir_ok), _not_after_phone_prefix, _not_date_led),
    ),
    Rule(_NHS_CONTEXT_RE, "NHS_NUMBER", "value"),
    Rule(
        _NHS_RE,
        "NHS_NUMBER",
        guard=every(_checksum(_nhs_ok), _not_after_phone_prefix, _not_date_led),
    ),
    # No `_ssn_ok` on the context rule: the keyword is the evidence, exactly as
    # an explicit INSZ context rescues a run the bare rule would reject.
    Rule(_SSN_CONTEXT_RE, "SSN", "value"),
    Rule(
        _SSN_RE,
        "SSN",
        guard=every(_checksum(_ssn_ok), _not_after_phone_prefix, _not_date_led),
    ),
    Rule(_NPI_CONTEXT_RE, "NPI", "value"),
    Rule(_RPPS_CONTEXT_RE, "RPPS", "value", _digit_count(9, 11)),
    Rule(_GMC_CONTEXT_RE, "GMC", "value"),
    Rule(_CNP_CONTEXT_RE, "CNP", "value"),
    Rule(
        _CNP_RE,
        "CNP",
        guard=every(_checksum(_cnp_ok), _not_after_phone_prefix, _not_date_led),
    ),
    Rule(_STEUER_ID_CONTEXT_RE, "STEUER_ID", "value"),
    Rule(
        _STEUER_ID_RE,
        "STEUER_ID",
        guard=every(_checksum(_steuer_id_ok), _not_after_phone_prefix, _not_date_led),
    ),
    Rule(_DNI_CONTEXT_RE, "DNI", "value"),
    Rule(_DNI_RE, "DNI", guard=_checksum(_dni_ok)),
    Rule(_NIE_RE, "NIE", guard=_checksum(_nie_ok)),
    Rule(_LANR_CONTEXT_RE, "LANR", "value", _checksum(_lanr_ok)),
    Rule(_COLEGIADO_CONTEXT_RE, "COLEGIADO", "value"),
    Rule(_PARAFA_CONTEXT_RE, "PARAFA", "value"),
    Rule(_MRN_RE, "MRN"),
    Rule(_MRN_CONTEXT_RE, "MRN", "value"),
    Rule(_BTW_RE, "BTW_EENHEID", guard=_checksum(_btw_ok)),
    Rule(_ORGANIZATION_ID_RE, "ORGANIZATION_ID", guard=_organization_id),
    Rule(_HEALTH_INSURANCE_DE_RE, "HEALTH_INSURANCE_ID", guard=_checksum(_kvnr_ok)),
    Rule(_HEALTH_INSURANCE_CONTEXT_RE, "HEALTH_INSURANCE_ID", "value"),
)


def _identifier_spans(text: str) -> list[MaskSpan]:
    """Detect every national, provider, record and organisation identifier."""
    return collect(_IDENTIFIER_RULES, text)
