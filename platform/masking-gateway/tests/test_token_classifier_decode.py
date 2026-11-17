#!/usr/bin/env python3
"""Pin the character offsets a token classifier's predictions turn into.

A token classifier predicts per subword token, and the router replaces exactly
the characters a span names. Nothing between the two had a test, which is how
a live deployment came to write `Armandsingel 969 9727 Avernas-le-BauduinSZ`
where the document said `INSZ`: MedRoBERTa tagged the `IN` as the start of an
address, the router replaced those two characters, and `SZ` was left standing
next to a street that was never in the document. Its French and English
siblings share this decoder, so one wrong offset here is wrong for three
models at once.

No weights and no ONNX Runtime: the offset arithmetic is a static method.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.src.models.xlm_r.model_engine import (
    OnnxTokenClassificationPipeline,
)

widen = OnnxTokenClassificationPipeline._whole_words

# (text, span the model predicted, span that reaches the router, why)
CASES = [
    # The live failures, from a real Dutch document.
    ("Patient met INSZ 80.01.01-001.07 kwam langs.", (12, 14), (12, 16)),
    ("Patient met rijksregisternummer 80.01.01.", (12, 17), (12, 31)),
    ("Patient met rijksregisternummer 80.01.01.", (20, 25), (12, 31)),
    # A span already on both boundaries is left exactly as it is.
    ("Patient Emily Carter kwam langs.", (8, 20), (8, 20)),
    ("Emily Carter", (0, 12), (0, 12)),
    # A mixed-alphanumeric token is one word. These two used to assert the
    # opposite -- that a digit beside letters is its own value and must not be
    # swallowed -- and that is how `12B` came back as `Xy4B`, with the `B`
    # left over from a value that no longer exists. Widening to the whole
    # token is the same repair as `IN` -> `INSZ`, for the same reason.
    ("Kamer 12B ligt naast 4A.", (6, 8), (6, 9)),
    ("Bus A12 vertrekt.", (4, 5), (4, 7)),
    # Punctuation and whitespace end a word.
    ("Saint-Jean is de kliniek.", (0, 5), (0, 5)),
    ("de kliniek Saint-Jean.", (11, 16), (11, 16)),
]

# The house-number failures, from a live run of an English clinical letter:
# the model's address span opened on the second digit of the number, so the
# first digit was printed unchanged beside a replaced address. Widening
# followed letters only and stopped on the digit to its left.
#
# What widening can and cannot repair is the point of these cases. It walks
# out of a token it landed inside; it does not cross a boundary. A number the
# model never entered -- one space, comma or `No.` away from the span -- is
# still missed here, and has to be recovered by the address detector or by
# the span merge, not by this function.
HOUSE_NUMBER_CASES = [
    # Span opens mid-number: the leading digit is pulled in.
    ("12 63 Parkes squares, Belfast", (1, 20), (0, 20)),
    ("28 7 Cooper plaza, Cardiff", (1, 17), (0, 17)),
    # Span opens after the space: unchanged, and deliberately so. Removing
    # the boundary stop would let widening walk back into `Invoice 12` and
    # mask an unrelated number as part of the address.
    ("12 63 Parkes squares, Belfast", (3, 20), (3, 20)),
    ("Invoice 12 63 Parkes squares", (11, 28), (11, 28)),
    # Boundary punctuation stops widening in both directions.
    ("Apt#12 63 Parkes squares", (5, 24), (4, 24)),
    ("Apt-12 63 Parkes squares", (5, 24), (4, 24)),
    ("Apt/12 63 Parkes squares", (5, 24), (4, 24)),
    # The right boundary follows the same rule: a trailing unit number or
    # postcode fragment the span opened inside is pulled in, not cut off.
    ("63 Parkes squares BT12", (0, 20), (0, 22)),
    ("63 Parkes squares, BT12 5AB", (19, 22), (19, 23)),
]


def test_a_subword_span_widens_to_the_token_it_lands_inside() -> None:
    for text, predicted, expected in CASES:
        assert widen(text, *predicted) == expected, (text, predicted)


def test_widening_pulls_in_a_house_number_it_opened_inside() -> None:
    for text, predicted, expected in HOUSE_NUMBER_CASES:
        assert widen(text, *predicted) == expected, (text, predicted)


def test_widening_never_leaves_the_text() -> None:
    text = "INSZ"
    for start in range(len(text)):
        for end in range(start + 1, len(text) + 1):
            new_start, new_end = widen(text, start, end)
            assert 0 <= new_start <= new_end <= len(text), (start, end)
            assert new_start <= start and new_end >= end, (start, end)


def test_a_widened_span_never_stops_in_the_middle_of_a_word() -> None:
    # Alphanumeric, not alphabetic: the invariant has to match the character
    # class the widening follows, or a span cut through `BT12` passes it.
    for text in (
        "Patient met rijksregisternummer 80.01.01 kwam langs.",
        "12 63 Parkes squares, Belfast BT12 5AB, Apt#4B",
    ):
        for start in range(len(text)):
            for end in range(start + 1, len(text) + 1):
                new_start, new_end = widen(text, start, end)
                if text[new_start].isalnum():
                    assert new_start == 0 or not text[new_start - 1].isalnum()
                if text[new_end - 1].isalnum():
                    assert new_end == len(text) or not text[new_end].isalnum()
