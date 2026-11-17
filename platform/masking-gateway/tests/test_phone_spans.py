"""Keep the BE/NL/FR/UK telephone regex's formats and guards pinned."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))


# `_phone_spans` is private: this regression pins it on purpose, so it
# stays a deep import rather than a public-surface name.
from masking_engine.app.engines.regex_contact import _phone_spans

NBSP = "\u00a0"

# Each table is (text, expected matched values). A bare all-space run of
# two-digit groups ("04 72 50 29 61") is accepted by design: the well-grouped
# rule, not the decimal rule, is what keeps measurements out, and it only
# bites when a decimal point or short group is present.

BELGIUM = [
    ("Bel me op 0472 50 29 61 alstublieft.", ["0472 50 29 61"]),
    ("Bel me op +32 472 50 29 61 alstublieft.", ["+32 472 50 29 61"]),
    ("Bel me op 0032 472 50 29 61 alstublieft.", ["0032 472 50 29 61"]),
    ("Bel me op 0472/50.29.61 alstublieft.", ["0472/50.29.61"]),
    ("Bel me op 0472.50.29.61 alstublieft.", ["0472.50.29.61"]),
    ("Bel 0800 12 345 gratis.", ["0800 12 345"]),
    ("Bel 09 234 56 78 tijdens kantooruren.", ["09 234 56 78"]),
]

# A written trunk 0 after the country code, in every spelling. Nobody dials
# both the country code and the trunk prefix, so the 0 is not part of the
# number and must not be counted towards its length.
WRITTEN_TRUNK_ZERO = [
    ("Bel me op +32 (0)472 50 29 61 alstublieft.", ["+32 (0)472 50 29 61"]),
    ("Bel me op +32/0472.50.29.61 alstublieft.", ["+32/0472.50.29.61"]),
    ("Bel me op +32 0 472 50 29 61 alstublieft.", ["+32 0 472 50 29 61"]),
    ("Bel me op +32 0472 50 29 61 alstublieft.", ["+32 0472 50 29 61"]),
    ("Bel me op 0032/0472.50.29.61 alstublieft.", ["0032/0472.50.29.61"]),
    ("Appelez le +33 (0)6 12 34 56 78.", ["+33 (0)6 12 34 56 78"]),
    ("Appelez le +33 06 12 34 56 78.", ["+33 06 12 34 56 78"]),
    ("Call +44 (0) 20 7946 0958 please.", ["+44 (0) 20 7946 0958"]),
    ("Call +44 020 7946 0958 please.", ["+44 020 7946 0958"]),
    ("Bel me op +31 06 12345678 alstublieft.", ["+31 06 12345678"]),
]

NETHERLANDS = [
    ("Bel me op 06 12345678 alstublieft.", ["06 12345678"]),
    ("Bel me op 06-12345678 alstublieft.", ["06-12345678"]),
    ("Bel me op +31 6 12345678 alstublieft.", ["+31 6 12345678"]),
    ("Bel 010 123 45 67 tijdens kantooruren.", ["010 123 45 67"]),
]

FRANCE = [
    ("Appelez le 06 12 34 56 78.", ["06 12 34 56 78"]),
    ("Appelez le 06.12.34.56.78.", ["06.12.34.56.78"]),
    ("Appelez le +33 6 12 34 56 78.", ["+33 6 12 34 56 78"]),
    ("Appelez le 0612345678.", ["0612345678"]),
]

UNITED_KINGDOM = [
    ("Call 020 7946 0958 please.", ["020 7946 0958"]),
    ("Call 02079460958 please.", ["02079460958"]),
    ("Call +44 20 7946 0958 please.", ["+44 20 7946 0958"]),
    ("Call 07911.709.920 please.", ["07911.709.920"]),
    ("Call +44 7700 900123 please.", ["+44 7700 900123"]),
    ("Call 0800 123 4567 free.", ["0800 123 4567"]),
]

# Non-breaking spaces left by PDF text extraction group digits too.
NON_BREAKING_SPACES = [
    (
        f"Bel me op 0472{NBSP}50{NBSP}29{NBSP}61 alstublieft.",
        [f"0472{NBSP}50{NBSP}29{NBSP}61"],
    ),
    (
        f"Bel me op +32{NBSP}472{NBSP}50{NBSP}29{NBSP}61 alstublieft.",
        [f"+32{NBSP}472{NBSP}50{NBSP}29{NBSP}61"],
    ),
    (
        f"Appelez le 06{NBSP}12{NBSP}34{NBSP}56{NBSP}78.",
        [f"06{NBSP}12{NBSP}34{NBSP}56{NBSP}78"],
    ),
    (f"Call 020{NBSP}7946{NBSP}0958 please.", [f"020{NBSP}7946{NBSP}0958"]),
]

# A written-out country code carries no "+" or "00" to mark it as one, so it
# is any other digit run until a phone cue says otherwise.
BARE_COUNTRY_CODE = [
    ("Tel: 32 472 50 29 61.", ["32 472 50 29 61"]),
    ("Hij zei 32 472 50 29 61 toen.", []),
]

# Any country's "+"-prefixed number, validated on full E.164 length. The "+"
# itself is the disambiguator, so an unfamiliar code is still a phone.
FOREIGN_INTERNATIONAL = [
    ("Call +49 30 901820 please.", ["+49 30 901820"]),
    ("Call +49 (0)30 9018 2044 please.", ["+49 (0)30 9018 2044"]),
    ("Call +1 415 555 2671 please.", ["+1 415 555 2671"]),
    ("Call +1 202 555 0132 now.", ["+1 202 555 0132"]),
    ("Call +41 44 123 45 67 please.", ["+41 44 123 45 67"]),
    ("Call +39 06 4998 1234 please.", ["+39 06 4998 1234"]),
    ("Bel +351 21 123 4567 alstublieft.", ["+351 21 123 4567"]),
    ("Call +61 2 9374 4000 please.", ["+61 2 9374 4000"]),
    ("Call +493090182044 please.", ["+493090182044"]),
    # A "+" alone does not make a digit run long enough to be a number.
    ("Call +2 50 29 61 maybe.", []),
    ("Serum Mg2+ 30 1234 noted.", []),
]

# Measurements, identifiers and dates stay out.
GUARDS: list[tuple[str, list[str]]] = [
    ("De dosering is 04.72 50 29 61 mg.", []),
    (f"De dosering is 04.72{NBSP}50 29 61 mg.", []),
    ("Their NHS number is 0676033164.", []),
    ("National ID 0954569369.", []),
    ("Op 17-02-2026 kwam hij binnen.", []),
    ("GB078392443 bleef ongewijzigd.", []),
]

# A "/" is how a packed field separates one identifier from the next, so a
# digit-then-slash in front of a number says nothing about that number. The
# decimal guard used to refuse both "12.03" and "12/03" alike, which hid a
# whole telephone number behind the identifier printed before it. A dot still
# bounds the match, so the measurement guards above are untouched.
PACKED_FIELDS = [
    (
        "78.03.12-123.45/1-23456-78-901/09 245 67 89",
        ["09 245 67 89"],
    ),
    ("dossier 1234/0472 50 29 61", ["0472 50 29 61"]),
]


def _assert_cases(cases: list[tuple[str, list[str]]]) -> None:
    for text, expected in cases:
        found = [text[span.start : span.end] for span in _phone_spans(text)]
        assert found == expected, f"{text!r}: found {found}, expected {expected}"


def test_belgian_formats() -> None:
    _assert_cases(BELGIUM)


def test_a_written_trunk_zero_is_not_part_of_the_number() -> None:
    _assert_cases(WRITTEN_TRUNK_ZERO)


def test_dutch_formats() -> None:
    _assert_cases(NETHERLANDS)


def test_french_formats() -> None:
    _assert_cases(FRANCE)


def test_united_kingdom_formats() -> None:
    _assert_cases(UNITED_KINGDOM)


def test_non_breaking_spaces_group_digits_too() -> None:
    _assert_cases(NON_BREAKING_SPACES)


def test_a_bare_country_code_needs_a_phone_cue() -> None:
    _assert_cases(BARE_COUNTRY_CODE)


def test_any_country_is_accepted_behind_a_plus() -> None:
    _assert_cases(FOREIGN_INTERNATIONAL)


def test_measurements_identifiers_and_dates_stay_out() -> None:
    _assert_cases(GUARDS)


def test_a_slash_separating_packed_fields_does_not_hide_the_number() -> None:
    _assert_cases(PACKED_FIELDS)


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
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


# --------------------------------------------------------------------------- #
# What the prefix and the writing style are read as
# --------------------------------------------------------------------------- #

from masking_engine.app.engines.regex_contact import (  # noqa: E402
    _Prefix,
    _national_length_ok,
    _read_prefix,
    _read_shape,
)

# (value, country, prefix digits, bare country code, foreign international)
PREFIXES = [
    ("+32472502961", "32", 2, False, False),
    ("+31612345678", "31", 2, False, False),
    ("+33512345678", "33", 2, False, False),
    ("+442079460958", "44", 2, False, False),
    # A written trunk zero is a display convention, not part of the number, so
    # it counts as prefix -- parenthesised, separated or glued.
    ("+32 (0)472 50 29 61", "32", 3, False, False),
    ("+33(0)5 12 34 56 78", "33", 3, False, False),
    ("+44 0 20 7946 0958", "44", 3, False, False),
    ("+310612345678", "31", 3, False, False),
    ("0032472502961", "32", 4, False, False),
    ("0032 472 50 29 61", "32", 4, False, False),
    # A bare country code, with no + and no 00.
    ("32472502961", "32", 2, True, False),
    # No prefix at all: a locally written number.
    ("0472502961", "", 0, False, False),
    ("044 20 7946 0958", "", 0, False, False),
    # A "+" carries any country's code, so an unfamiliar one is still a phone.
    ("+1 202 555 0143", "", 0, False, True),
    ("+999 12345678901", "", 0, False, True),
]


def test_every_way_of_writing_a_prefix_is_read_the_same_way() -> None:
    for value, country, digits, bare, foreign in PREFIXES:
        prefix = _read_prefix(value)
        assert prefix == _Prefix(country, digits, bare, foreign), value
        assert prefix.international is bool(country or foreign), value


def test_a_national_length_is_measured_after_the_prefix() -> None:
    belgian = _Prefix("32", 2, False, False)
    # 32 plus a nine-digit national number: eleven digits, nine after the code.
    assert _national_length_ok(11, belgian)
    assert _national_length_ok(10, belgian)
    assert _national_length_ok(9, belgian)
    assert not _national_length_ok(12, belgian)
    assert not _national_length_ok(8, belgian)


def test_a_foreign_international_number_is_checked_on_e164_length_alone() -> None:
    foreign = _Prefix("", 0, False, True)
    assert not _national_length_ok(8, foreign)
    assert _national_length_ok(9, foreign)
    assert _national_length_ok(15, foreign)
    assert not _national_length_ok(16, foreign)


def test_a_number_with_no_prefix_may_take_any_national_length() -> None:
    local = _Prefix("", 0, False, False)
    for count in (7, 8, 9, 10, 11):
        assert _national_length_ok(count, local), count
    assert not _national_length_ok(6, local)
    assert not _national_length_ok(12, local)


# (value, decimal, calendar, dialling, well grouped, separated)
SHAPES = [
    ("12.5", True, False, False, False, True),
    ("1.5", True, False, False, False, True),
    # Five digits before the point is a phone group, not a measurement.
    ("07911.703.920", False, False, False, True, True),
    ("03.502.688", False, False, True, True, True),
    ("12.03.2024", False, True, True, True, True),
    ("12-03-24", False, True, True, True, True),
    ("0472 50 29 61", False, False, True, True, True),
    ("0472/50.29.61", False, False, True, True, True),
    ("0954569369", False, False, False, True, False),
    ("810922-237-91", False, False, False, True, True),
    ("07700.06.40.43", False, False, False, True, True),
]


def test_the_writing_style_of_a_run_is_measured_the_same_way_every_time() -> None:
    for value, decimal, calendar, dialling, grouped, separated in SHAPES:
        shape = _read_shape(value)
        assert shape.decimal_like is decimal, value
        assert shape.calendar_like is calendar, value
        assert shape.dialling_style is dialling, value
        assert shape.well_grouped is grouped, value
        assert shape.separated is separated, value


def test_a_non_breaking_space_separates_like_any_other() -> None:
    assert _read_shape(f"0472{NBSP}50{NBSP}29{NBSP}61").separated
    assert not _read_shape("0472502961").separated


def test_dialling_style_needs_three_short_groups() -> None:
    assert not _read_shape("03.502").dialling_style  # two groups
    assert _read_shape("03.50.26").dialling_style
    assert not _read_shape("03.50.26881").dialling_style  # a group over four
