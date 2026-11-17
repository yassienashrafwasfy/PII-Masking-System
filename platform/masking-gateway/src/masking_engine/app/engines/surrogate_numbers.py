"""Generate the digits of a number that has to satisfy its own scheme."""

from __future__ import annotations

import random
import re

_DIGIT_RUN_RE = re.compile(r"\d+")

# Every area the SSA issues: not 000, not 666, and nothing from 900 up.
_SSN_AREAS = tuple(number for number in range(1, 900) if number != 666)


def _ssn_digits(generator: random.Random) -> str:
    """Return nine digits that form an allocatable US SSN.

    Area 000, area 666, the 900-999 block, group 00 and serial 0000 are never
    issued, so a surrogate drawn from them would be recognisable as a fake
    rather than as a person's number.
    """
    return (
        f"{generator.choice(_SSN_AREAS):03d}"
        f"{generator.randrange(1, 100):02d}"
        f"{generator.randrange(1, 10000):04d}"
    )


def _npi_digits(generator: random.Random) -> str:
    """Return ten digits that form a Luhn-valid NPI.

    The NPI check digit is a Luhn check taken over the number prefixed with
    the 80840 issuer identifier, which is why the prefix appears here and not
    in the value. Allocation runs from 1 and 2, so the surrogate opens with
    one of those rather than with any digit.
    """
    body = f"{generator.choice((1, 2))}{generator.randrange(0, 10**8):08d}"
    total = 0
    for index, character in enumerate(reversed(f"80840{body}")):
        digit = int(character)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return f"{body}{(10 - total % 10) % 10}"


def _nhs_digits(generator: random.Random) -> str:
    """Return ten digits that form a mod-11-valid NHS number.

    Nine digits weighted ten down to two, and the check digit completes the
    sum to a multiple of eleven. A remainder of one leaves no valid check
    digit, so that draw is discarded rather than coerced -- which is exactly
    the case `_nhs_ok` rejects. No allocated range opens with a zero, and
    `_nhs_ok` rejects that too, so the first digit starts at one.
    """
    while True:
        body = f"{generator.randrange(1, 10)}{generator.randrange(0, 10**8):08d}"
        total = sum(int(char) * (10 - index) for index, char in enumerate(body))
        check = 11 - total % 11
        if check == 11:
            check = 0
        elif check == 10:
            continue
        return f"{body}{check}"


def _place_digits(original: str, digits: str) -> str:
    """Return *original* with its digits replaced by *digits*, in order.

    The separators are the writer's choice and carry nothing, so they stay:
    "123-45-6789" masks to "481-02-7735" and "123 45 6789" to the same digits
    spaced the same way, which keeps the field readable as what it is.
    """
    replacement = iter(digits)
    return "".join(
        next(replacement) if character.isdigit() else character
        for character in original
    )


def _dialling_prefix_length(original: str, digits: str) -> int:
    """Return how many leading digits of a phone number survive masking.

    A masked phone keeps its dialling prefix so the field still reads as a
    phone number. That is safe for a prefix -- a country, area or operator
    code identifies nobody -- but it is only safe for a *prefix*. A value that
    reached this label without being a phone number used to keep its first two
    digits all the same, and for a national number those two digits are the
    patient's year of birth, printed in the clear inside a field that looks
    masked. So the head survives only where it is really a dialling prefix: an
    international number writes one after `+` or `0032`, and a Belgian one
    starts with a trunk `0` or the bare country code.
    """
    if digits.startswith("0032"):
        return 4
    if original.lstrip().startswith("+") or digits.startswith(("32", "0")):
        return 2
    return 0


# A birth date drawn from a fixed window. Deliberately not anchored on today:
# `faker.ssn()` builds its date with `generator.date()`, whose range ends
# *now*, so the same seed produced a different number the next day and a
# document masked twice returned different bytes -- the same hazard the
# unparsed-date anchor was fixed for.
_BIRTH_YEAR_RANGE = (1930, 2021)
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _birth_date(generator: random.Random) -> tuple[int, int, int]:
    """Return a plausible year, month and day from the fixed window."""
    year = generator.randrange(*_BIRTH_YEAR_RANGE)
    month = generator.randrange(1, 13)
    # February takes 29 unconditionally, matching what `_insz_ok` accepts: the
    # number carries two year digits, so the century is unknown either way.
    return year, month, generator.randrange(1, _MONTH_DAYS[month - 1] + 1)


def _insz_digits(generator: random.Random) -> str:
    """Return a Belgian national number that satisfies `_insz_ok`.

    Eleven digits: the birth date, a non-zero order number, and the mod-97 key
    of the first nine. A number for a birth year in or after 2000 keys off the
    body prefixed with a 2, which is the alternate formula `_insz_ok` accepts.
    """
    year, month, day = _birth_date(generator)
    body = f"{year % 100:02d}{month:02d}{day:02d}{generator.randrange(1, 999):03d}"
    base = int(body) if year < 2000 else int("2" + body)
    return f"{body}{97 - (base % 97):02d}"


def _nir_digits(generator: random.Random) -> str:
    """Return a French NIR that satisfies `_nir_ok`.

    Fifteen digits: sex, the birth year and month, the department and commune
    of registration, an order number, and the mod-97 key of the first thirteen.
    """
    year, month, _ = _birth_date(generator)
    body = (
        f"{generator.choice('12')}"
        f"{year % 100:02d}{month:02d}"
        f"{generator.randrange(1, 96):02d}"
        f"{generator.randrange(1, 1000):03d}"
        f"{generator.randrange(1, 1000):03d}"
    )
    return f"{body}{97 - (int(body) % 97):02d}"


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CNP_WEIGHTS = (2, 7, 9, 1, 4, 6, 3, 5, 8, 2, 7, 9)


def _cnp_digits(generator: random.Random) -> str:
    year, month, day = _birth_date(generator)
    sex = generator.choice((1, 2)) if year < 2000 else generator.choice((5, 6))
    body = f"{sex}{year % 100:02d}{month:02d}{day:02d}{generator.choice((1, 10, 22, 26, 32, 40, 51)):02d}{generator.randrange(1, 1000):03d}"
    check = (
        sum(int(char) * weight for char, weight in zip(body, _CNP_WEIGHTS, strict=True))
        % 11
    )
    return f"{body}{1 if check == 10 else check}"


def _steuer_id_digits(generator: random.Random) -> str:
    while True:
        digits = list(range(10))
        generator.shuffle(digits)
        body = digits[:9]
        body.append(generator.choice(body))
        generator.shuffle(body)
        if body[0] == 0:
            continue
        text = "".join(map(str, body))
        product = 10
        for char in text:
            total = (int(char) + product) % 10
            product = (10 if total == 0 else total) * 2 % 11
        return f"{text}{(11 - product) % 10}"


def _dni_identifier(generator: random.Random) -> str:
    number = generator.randrange(10_000_000, 100_000_000)
    return f"{number:08d}{_DNI_LETTERS[number % 23]}"


def _nie_identifier(generator: random.Random) -> str:
    prefix = generator.choice(("X", "Y", "Z"))
    number = generator.randrange(1_000_000, 10_000_000)
    full = int({"X": "0", "Y": "1", "Z": "2"}[prefix] + str(number))
    return f"{prefix}{number:07d}{_DNI_LETTERS[full % 23]}"


def _lanr_digits(generator: random.Random) -> str:
    body = f"{generator.randrange(100_000, 1_000_000):06d}"
    check = (
        sum(
            int(char) * weight
            for char, weight in zip(body, (4, 9, 4, 9, 4, 9), strict=True)
        )
        % 10
    )
    return (
        f"{body}{check}{generator.choice(('01', '02', '05', '07', '10', '21', '33'))}"
    )


def _colegiado_digits(generator: random.Random) -> str:
    current = generator.randrange(1, 53)
    first = current if generator.random() < 0.8 else generator.randrange(1, 53)
    return f"{current:02d}{first:02d}{generator.randrange(100_000):05d}"


def _parafa_digits(generator: random.Random) -> str:
    return f"{generator.randrange(10_000, 100_000):05d}"


def _place_identifier(original: str, identifier: str) -> str:
    replacement = iter(identifier)
    return "".join(
        next(replacement) if character.isalnum() else character
        for character in original
    )
