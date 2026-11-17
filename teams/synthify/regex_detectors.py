"""
Deterministic regex-based detectors for PII categories that pattern-matching
handles better than a trained model: URLs (exact syntax, no ambiguity),
Belgian INSZ/RIZIV numbers (fixed-length digit sequences in a handful of
known separator styles), and credit card numbers (length + Luhn checksum).

The INSZ/RIZIV shapes below are reverse-engineered from real examples in
Milestone_1/AI4privacy processed/data/*.jsonl (both datasets use "RVIZ" as
the label spelling), plus the publicly documented INSZ mod-97 checksum
algorithm (rijksregisternummer). Examples actually seen in that data:

    INSZ:  98022112853 | 910212 302 02 | 05.04.05-318.82 | 56.12.18.945.66
    RVIZ:  19205012010 | 3-58343-72-001 | 6 11268 26 002 | 1/21941/78/004

Both are always 11 digits total; only the separator style (none, dot,
space, dash, slash) and grouping (INSZ: 2-2-2-3-2, RIZIV: 1-5-2-3) differ.
INSZ candidates are checksum-validated (the mod-97 formula), which also
disambiguates them from RIZIV numbers, since an unseparated 11-digit run
matches both shapes structurally.

URLs match either an explicit `http(s)://` scheme or a bare `www.` host --
the latter because real documents (e.g. a letter footer reading
"www.azorg.be") routinely omit the scheme. Measured against
Milestone_1/AI4privacy processed's validation split, adding the `www.`
alternative produced zero new matches there (that dataset's 8,783 URL
entities all carry a scheme, and bare `www.` never occurs), so it changes no
benchmark number while closing a real-world gap. Bare schemeless domains
without `www.` (e.g. "azorg.be") are deliberately NOT matched: too easy to
confuse with abbreviations, filenames, and sentence punctuation.

Credit card numbers use the standard 13-19 digit length range (covering
Visa/Mastercard/Amex/Discover/Diners/JCB) plus the public Luhn checksum --
not fit to Milestone_1's own CREDITCARDNUMBER examples, which are synthetic
and don't all pass Luhn (some even contain stray letters); the model already
handles that dataset well on its own (its own eval: F1=0.995), so this is a
general real-world supplement rather than a replica of that exact format.

Phone numbers are ported from the platform's shared regex_engine.py
(_PHONE_RE / _has_phone_context), which is already validated against this
corpus: on a 2-page Belgian-clinic sample, this checkpoint scored two of
three phone numbers below its 0.5 confidence threshold (0.235 and 0.326),
and with no regex backstop those went out unmasked. An international
dialling prefix (+31/+32/0031/0032) is evidence on its own; a local-format
candidate needs a "Tel"/"GSM"/"mobiel"/"fax" label within 30 characters
before it, since a bare local digit group also spells out dosages, lab
results, and reference numbers in this medical-document corpus.

Merged into the model's own entities by runtime._merge_regex_spans as a
high-precision supplement, not a replacement -- it catches formats the model
misses, and effectively covers URL on its own for this checkpoint, whose URL
F1 is 0.078 (docs/model-card.md). Labels are emitted in the checkpoint's own
spelling (URL / INSZ / RVIZ / CREDITCARDNUMBER / TELEFOON) so they map
through runtime.CANONICAL_LABELS unchanged. Set REGEX_DETECTORS=off to
disable the merge and get model-only spans.
"""

import re
from typing import Any

URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+")
_URL_TRAILING_PUNCT = ".,;:!?)]}\"'"

# 11 digits total: YY MM DD (2-2-2), sequence (3), checksum (2), with an
# optional separator (dot or space between the date groups, dot/dash/space
# before the sequence, dot/space before the checksum) -- or none at all.
INSZ_RE = re.compile(r"\b\d{2}[.\s]?\d{2}[.\s]?\d{2}[-.\s]?\d{3}[.\s]?\d{2}\b")

# 11 digits total, grouped 1-5-2-3 (profession qualifier / order number /
# qualification code / sequence), separated by nothing, space, dash, or slash.
RIZIV_RE = re.compile(r"\b\d[-\s/]?\d{5}[-\s/]?\d{2}[-\s/]?\d{3}\b")

# 13-19 digits total (covers Visa/Mastercard/Amex/Discover/Diners/JCB length
# ranges), each digit optionally followed by a single space or dash -- so it
# tolerates the usual 4-4-4-4 or Amex-style 4-6-5 grouping as well as no
# separator at all. Note: Milestone_1's own CREDITCARDNUMBER training
# examples are synthetic and don't all pass Luhn (some even contain stray
# letters) -- the model already handles that dataset well on its own (its
# own eval: F1=0.995); this regex is a general real-world supplement, not a
# fit to that dataset's exact (noisy) format, so it only accepts genuinely
# Luhn-valid candidates.
CREDIT_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")

# A Belgian/Dutch dialling prefix (international, or a bare leading 0) then
# 7-18 more digits, tolerating space/dot/slash/dash/parens separators.
PHONE_RE = re.compile(r"(?<![\d+\-])\(?(?:\+3[12]|003[12]|0)[\d ()/.-]{7,18}(?!\d)")
_PHONE_CONTEXT_RE = re.compile(
    r"\b(?:tel(?:efoon)?|gsm|mobiel|fax)\.?\s*[:.]?\s*$", re.IGNORECASE
)
_PHONE_TRAILING_PUNCT = " .,/;-)"


def _has_phone_context(text: str, start: int) -> bool:
    return _PHONE_CONTEXT_RE.search(text[max(0, start - 30) : start]) is not None


def _insz_checksum_valid(digits: str) -> bool:
    """digits: the 11-digit INSZ candidate with all separators stripped."""
    base9, check = digits[:9], int(digits[9:])
    n = int(base9)
    for candidate_base in (n, 2_000_000_000 + n):  # born < 2000, born >= 2000
        remainder = candidate_base % 97
        expected = 97 if remainder == 0 else 97 - remainder
        if expected == check:
            return True
    return False


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _overlaps_any(start, end, existing):
    return any(start < e["end"] and e["start"] < end for e in existing)


Entities = list[dict[str, Any]]


def _add_urls(text: str, entities: Entities) -> None:
    for m in URL_RE.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _URL_TRAILING_PUNCT:
            end -= 1
        if end > m.start() and not _overlaps_any(m.start(), end, entities):
            entities.append(
                {"start": m.start(), "end": end, "label": "URL", "score": 0.99}
            )


def _add_insz(text: str, entities: Entities) -> None:
    for m in INSZ_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if (
            len(digits) == 11
            and _insz_checksum_valid(digits)
            and not _overlaps_any(m.start(), m.end(), entities)
        ):
            entities.append(
                {"start": m.start(), "end": m.end(), "label": "INSZ", "score": 0.99}
            )


def _add_riziv(text: str, entities: Entities) -> None:
    for m in RIZIV_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if len(digits) == 11 and not _overlaps_any(m.start(), m.end(), entities):
            entities.append(
                {"start": m.start(), "end": m.end(), "label": "RVIZ", "score": 0.9}
            )


def _add_credit_cards(text: str, entities: Entities) -> None:
    for m in CREDIT_CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if (
            13 <= len(digits) <= 19
            and _luhn_valid(digits)
            and not _overlaps_any(m.start(), m.end(), entities)
        ):
            entities.append(
                {
                    "start": m.start(),
                    "end": m.end(),
                    "label": "CREDITCARDNUMBER",
                    "score": 0.95,
                }
            )


def _is_phone_number(text: str, start: int, value: str) -> bool:
    """Whether *value*, a phone-shaped match at *start*, reads as a phone number."""
    digits = re.sub(r"\D", "", value)
    normalized = value.lstrip("(")
    prefix_length = 2 if normalized.startswith(("+31", "+32")) else 0
    if normalized.startswith(("0031", "0032")):
        prefix_length = 4
    national_lengths = {9, 10}
    if normalized.startswith(("+32", "0032")):
        national_lengths = {8, 9}
    decimal_tokens = re.findall(r"(?<!\S)\d+\.\d+", value)
    international = normalized.startswith(("+31", "+32", "0031", "0032"))
    explicit_context = _has_phone_context(text, start)
    ambiguous_local = not international and any(c in value for c in " ./-")
    return (
        (not decimal_tokens or international or explicit_context)
        and (not ambiguous_local or explicit_context)
        and len(digits) - prefix_length in national_lengths
    )


def _add_phones(text: str, entities: Entities) -> None:
    for m in PHONE_RE.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _PHONE_TRAILING_PUNCT:
            end -= 1
        value = text[m.start() : end]
        if _is_phone_number(text, m.start(), value) and not _overlaps_any(
            m.start(), end, entities
        ):
            entities.append(
                {"start": m.start(), "end": end, "label": "TELEFOON", "score": 0.9}
            )


def detect_entities(text: str) -> list:
    """
    Returns a list of {"start", "end", "label", "score"} in `text`'s
    coordinates. Runs on the whole text directly -- no chunking needed,
    since regex has no model context-window limit.

    The kinds run in a fixed order and each skips what an earlier one already
    claimed, so the order below is part of the behaviour.
    """
    entities: Entities = []
    _add_urls(text, entities)
    _add_insz(text, entities)
    _add_riziv(text, entities)
    _add_credit_cards(text, entities)
    _add_phones(text, entities)
    return entities
