#!/usr/bin/env python3
"""Pin the email and web-address guards, and the date veto on phone numbers.

Every one of these is a private guard behind a permissive regex: the pattern
finds a shape, the guard decides whether it is really an address. None of them
had a test of its own, and mutation testing showed why that matters -- the
whole malformed-local-part clause of `_email_ok` could be inverted without a
single one of the 552 tests noticing, and so could the date veto that stops a
dotted calendar date being reported as a telephone number.

Only inputs the detectors' own regexes can actually produce are used here. A
leading dot in the local part and a second `@` are unreachable through
`_EMAIL_RE` -- it trims the dot and starts the match after the first `@` -- so
the guard clauses covering them are dead by construction, not untested.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

# These are private on purpose: the guards are the unit worth pinning, and the
# public entry point hides them behind arbitration.
from masking_engine.app.engines.regex_contact import (
    _domain_ok,
    _email_ok,
    _EMAIL_RE,
    _phone_spans,
    _url_ok,
)

# (address as the regex yields it, is it really an address)
EMAILS = [
    ("alice@example.org", True),
    ("alice.example@example.org", True),
    ("alice+tag@example.co.uk", True),
    # A local part may not end on a dot, nor carry two in a row.
    ("alice.@example.org", False),
    ("ali..ce@example.org", False),
    # The domain needs two labels, an alphabetic top label of at least two
    # letters, and no label that opens or closes on a hyphen.
    ("alice@example.o", False),
    ("alice@example.12", False),
    ("alice@-example.org", False),
    ("alice@example-.org", False),
]

HOSTS = [
    ("example.org", True),
    ("sub.example.org", True),
    ("x-1.example.org", True),
    ("example", False),
    ("example.o", False),
    ("example.12", False),
    ("-example.org", False),
    ("example-.org", False),
    ("example..org", False),
]

URLS = [
    ("https://example.org/a/b", True),
    ("HTTP://example.org", True),
    ("www.example.org/path", True),
    ("example.org", True),
    ("example", False),
    ("example.12/path", False),
]

# A dotted or slashed calendar date is the one digit run the phone table has to
# refuse on shape alone: it is the right length, it is grouped like a dialling
# style, and a phone cue often sits right in front of it. Arbitration would
# award the span to the date detector anyway, so this is invisible from
# `find_regex_spans` -- which is exactly why it is pinned here.
CALENDAR_DATES = [
    "Tel 03.02.2024 voor de uitslag.",
    "Gsm 07.09.2026 doorgeven.",
    "Bel 03/02/2024 terug.",
    "op 07.09.2026",
]


def test_every_email_case_is_one_the_regex_can_actually_produce() -> None:
    for value, _ in EMAILS:
        assert [m.group() for m in _EMAIL_RE.finditer(value)] == [value], value


def test_an_email_is_accepted_only_with_a_well_formed_local_part_and_domain() -> None:
    for value, expected in EMAILS:
        assert _email_ok(value) is expected, value


def test_a_host_needs_two_labels_and_an_alphabetic_top_label() -> None:
    for host, expected in HOSTS:
        assert _domain_ok(host) is expected, host


def test_a_web_address_is_judged_on_its_host_with_or_without_a_scheme() -> None:
    for value, expected in URLS:
        assert _url_ok(value) is expected, value


def test_a_calendar_date_is_never_reported_as_a_telephone_number() -> None:
    for text in CALENDAR_DATES:
        assert _phone_spans(text) == [], text
