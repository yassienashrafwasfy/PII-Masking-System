#!/usr/bin/env python3
"""Every written form of a date this service claims, and the ones it must not.

A date is the most common quasi-identifier in a clinical letter and the one
written in the most ways. A form that stops matching leaves a birth date in
the released document; a rule that loosens claims a room number or a dosage.
Both are pinned here against the grammar rather than against a sample letter.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.engines.regex_dates import (
    _MONTHS,
    _date_spans,
    _is_real_date,
    _valid_date,
)


def dates(text: str) -> list[str]:
    return [text[s.start : s.end] for s in _date_spans(text)]


# Each of these is one date and has to be claimed whole.
CLAIMED = (
    "12/03/2024",
    "12-03-2024",
    "12.03.2024",
    "1/3/2024",
    # Day-first is impossible, month-first is real: claimed the American way.
    "12/13/2024",
    "2024-03-12",
    "2024/03/12",
    "2024.3.1",
    "03/2024",
    "3-2024",
    "12 maart 2024",
    "12 March 2024",
    "12 mars 2024",
    "1er janvier 2024",
    "2nd February 2024",
    "12e mars 2024",
    "maart 2024",
    "March 12, 2024",
    "March 2024",
    "mrt. 2024",
    "juillet 2024",
    "2024-03-12T09:30",
    "2024-03-12T09:30:15",
    "2024-03-12 09:30",
)

# Shapes that look like a date and are not one.
REFUSED = (
    "32/03/2024",
    "31/04/2024",
    "29/02/2023",
    "00/03/2024",
    "13/2024",
    "00/2024",
    "12 smarch 2024",
    "12 maart 24",
)


def test_every_written_form_is_claimed_whole() -> None:
    for value in CLAIMED:
        assert dates(f"Op {value} gezien.") == [value], value


def test_a_shape_that_is_not_a_date_is_left_alone() -> None:
    for value in REFUSED:
        assert dates(f"Op {value} gezien.") == [], value


def test_an_american_order_is_read_the_american_way() -> None:
    # "05/30/1968" is a real date read month-first and no date at all read
    # day-first, and a date the rule fails to claim is one left in the letter.
    assert _valid_date("05/30/1968")
    assert _valid_date("30/05/1968")
    # Day-first is tried first, so an ambiguous pair is claimed either way.
    assert _valid_date("05/06/1968")


def test_a_timestamp_is_one_date_and_not_a_date_beside_a_time() -> None:
    text = "Genoteerd 2024-03-12T09:30:15 in het dossier."
    assert dates(text) == ["2024-03-12T09:30:15"]
    # The shorter match inside it is dropped rather than emitted as well.
    assert len(_date_spans(text)) == 1


def test_a_date_beside_a_timestamp_is_still_claimed() -> None:
    text = "Van 2024-03-12T09:30 tot 13/03/2024."
    assert dates(text) == ["2024-03-12T09:30", "13/03/2024"]


def test_a_longer_month_name_is_never_truncated_to_a_shorter_one() -> None:
    # The alternation is ordered longest-first, so "juillet" cannot match as
    # "jul" and leave "let" behind.
    assert dates("Le 3 juillet 2024") == ["3 juillet 2024"]
    assert dates("Le 3 septembre 2024") == ["3 septembre 2024"]


def test_a_month_name_is_read_in_any_of_the_three_languages() -> None:
    for name, number in _MONTHS.items():
        claimed = dates(f"Op 1 {name} 2024 gezien.")
        assert claimed == [f"1 {name} 2024"], name
        assert _valid_date(f"1/{number}/2024"), name


def test_a_date_needs_a_four_digit_year() -> None:
    assert dates("Op 12/03/24 gezien.") == []
    assert dates("Op 12 maart 24 gezien.") == []


def test_a_month_only_date_needs_a_year_in_range() -> None:
    assert _valid_date("3/1899") is False
    assert _valid_date("3/1900") is True
    assert _valid_date("3/2100") is True
    assert _valid_date("3/2101") is False
    assert _valid_date("13/2000") is False
    assert _valid_date("1/2000") is True


def test_something_that_is_not_three_or_two_numbers_is_not_a_date() -> None:
    assert _valid_date("2024") is False
    assert _valid_date("1/2/3/4") is False
    assert _valid_date("a/b/c") is False
    assert _valid_date("") is False


def test_a_real_calendar_date_is_the_only_thing_accepted() -> None:
    assert _is_real_date(2024, 2, 29)  # a leap year
    assert not _is_real_date(2023, 2, 29)
    assert _is_real_date(2024, 12, 31)
    assert not _is_real_date(2024, 13, 1)
    assert not _is_real_date(2024, 0, 1)
    assert not _is_real_date(2024, 1, 0)
    assert not _is_real_date(2024, 4, 31)


def test_a_month_name_date_is_checked_against_the_calendar_too() -> None:
    assert dates("Op 29 februari 2024 gezien.") == ["29 februari 2024"]
    assert dates("Op 29 februari 2023 gezien.") == []
    assert dates("Op 31 april 2024 gezien.") == []


def test_no_date_is_claimed_out_of_a_longer_digit_run() -> None:
    for text in ("Referentie 112/03/20244", "Nummer 20240312", "Code 1203202412"):
        assert dates(text) == [], text


def test_a_date_beside_a_time_survives_the_loose_digit_detectors() -> None:
    """The bug users reported: a timestamped date came back as a random year.

    The phone pattern starts matching at a digit inside the date and runs on
    through the separator into the leading digits of the time, so the
    candidate it hands to arbitration is *longer* than the date. Arbitration
    ranks length before label priority, so the DATE span was dropped and the
    whole run masked by shifting each digit on its own: "05/05/1985 - 14u30"
    came back reading "05/79/5570 - 82u30".

    Asserted through `find_regex_spans` rather than `_date_spans`, because
    `_date_spans` always found the date -- what it lost was the arbitration.
    """
    from masking_engine.app.engines.regex_engine import find_regex_spans

    # The separator between the date and the time is the trigger: where the
    # two are directly adjacent the date ties on length and survived even
    # before the fix, so each of these spellings is its own case.
    for date_text, separator, time_text in (
        ("05/05/1985", " - ", "14u30"),
        ("03-11-2024", " - ", "14:30"),
        ("05/11/1999", " ", "14:30:00"),
        ("27 10 1985", " ", "09:30"),  # ten digits: claimed as an NHS number
        ("01 02 1985", " om ", "09:30"),
        ("02\u00a007\u00a02024", "\u00a0", "09:30"),  # NBSP, as PDF text carries it
        ("15/03/2026", ", ", "09:15"),
        ("12.03.2024", " ", "14:30"),
    ):
        text = f"Genoteerd {date_text}{separator}{time_text} op de dienst."
        spans = find_regex_spans(text)
        labels = {span.label for span in spans}
        assert labels == {"DATE"}, (text, sorted(labels))
        assert [text[s.start : s.end] for s in spans] == [date_text], text


def test_a_dialling_style_is_still_a_phone_number() -> None:
    """The date veto refuses a prefix, so it must not refuse a real number.

    Every one of these contains digit groups a loose reading could mistake
    for a date; none of them opens with one.
    """
    from masking_engine.app.engines.regex_engine import find_regex_spans

    for text in (
        "Tel 03.502.688",
        "Tel 07700.06.40.43",
        "Bel +32 475 12 34 56",
        "Tel: 0475 12 34 56",
        "T\u00e9l. 01 42 34 56 78",
    ):
        labels = {span.label for span in find_regex_spans(text)}
        assert labels == {"PHONE_NUMBER"}, (text, sorted(labels))
