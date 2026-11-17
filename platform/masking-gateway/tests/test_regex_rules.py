#!/usr/bin/env python3
"""The rule engine the pattern-table detectors share.

Every detector table runs through `collect`, so an overlap policy that stops
biting, a guard that stops being consulted or a hint that skips a rule it
should have run changes what a dozen detectors emit at once. None of that is
visible from any single detector's own tests.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core.schemas import MaskSpan
from masking_engine.app.engines.regex_identifiers import _checksum, _digit_count
from masking_engine.app.engines.regex_rules import (
    Candidate,
    Overlap,
    Rule,
    _blocked,
    _HintCache,
    collect,
    either,
    every,
    value_ok,
)
from masking_engine.app.engines.regex_shared import _digit_run

DIGITS = re.compile(r"\d+")


def found(rules, text: str) -> list[tuple[str, str]]:
    return [(text[s.start : s.end], s.label) for s in collect(rules, text)]


# --------------------------------------------------------------------------- #
# Candidates and guards
# --------------------------------------------------------------------------- #


def test_a_candidate_exposes_the_text_it_covers() -> None:
    text = "abc 123 def"
    match = DIGITS.search(text)
    candidate = Candidate(text, match, match.start(), match.end())
    assert candidate.value == "123"
    assert candidate.text is text
    # A trimmed span reports the trimmed value, not the whole match.
    assert Candidate(text, match, match.start(), match.end() - 1).value == "12"


def test_guards_combine_the_way_their_names_say() -> None:
    yes, no = (lambda c: True), (lambda c: False)
    candidate = Candidate("x", DIGITS.search("1"), 0, 1)
    assert every(yes, yes)(candidate)
    assert not every(yes, no)(candidate)
    assert not either(no, no)(candidate)
    assert either(no, yes)(candidate)
    # An empty combinator is the identity of its operation.
    assert every()(candidate)
    assert not either()(candidate)


def test_a_value_guard_is_asked_about_the_value_alone() -> None:
    seen: list[str] = []

    def validator(value: str) -> bool:
        seen.append(value)
        return value == "123"

    text = "a 123 b 456"
    assert found((Rule(DIGITS, "N", guard=value_ok(validator)),), text) == [
        ("123", "N")
    ]
    assert seen == ["123", "456"]


# --------------------------------------------------------------------------- #
# Overlap policies
# --------------------------------------------------------------------------- #


ACCEPTED = [MaskSpan(10, 20, "A", "regex")]


def test_allow_never_blocks() -> None:
    for start, end, label in ((10, 20, "A"), (12, 15, "B"), (0, 5, "A")):
        assert not _blocked(ACCEPTED, start, end, label, Overlap.ALLOW)


def test_any_label_blocks_on_overlap_whatever_the_label() -> None:
    for start, end in ((10, 20), (12, 15), (5, 12), (19, 30), (0, 100)):
        assert _blocked(ACCEPTED, start, end, "B", Overlap.ANY_LABEL), (start, end)
    for start, end in ((0, 10), (20, 30)):
        assert not _blocked(ACCEPTED, start, end, "B", Overlap.ANY_LABEL), (start, end)


def test_same_label_blocks_only_its_own_label() -> None:
    assert _blocked(ACCEPTED, 12, 15, "A", Overlap.SAME_LABEL)
    assert not _blocked(ACCEPTED, 12, 15, "B", Overlap.SAME_LABEL)
    assert not _blocked(ACCEPTED, 20, 30, "A", Overlap.SAME_LABEL)


def test_duplicate_blocks_only_an_identical_claim() -> None:
    assert _blocked(ACCEPTED, 10, 20, "A", Overlap.DUPLICATE)
    # A different range, a different label, or a partial cover is not a repeat.
    assert not _blocked(ACCEPTED, 10, 19, "A", Overlap.DUPLICATE)
    assert not _blocked(ACCEPTED, 11, 20, "A", Overlap.DUPLICATE)
    assert not _blocked(ACCEPTED, 10, 20, "B", Overlap.DUPLICATE)
    assert not _blocked(ACCEPTED, 12, 15, "A", Overlap.DUPLICATE)


def test_nothing_accepted_yet_blocks_nothing() -> None:
    for policy in Overlap:
        assert not _blocked([], 10, 20, "A", policy), policy


# --------------------------------------------------------------------------- #
# Running a table
# --------------------------------------------------------------------------- #


def test_a_rule_takes_the_group_it_names() -> None:
    cued = re.compile(r"nr[:\s]+(?P<value>\d+)")
    assert found((Rule(cued, "N", "value"),), "nr: 4821") == [("4821", "N")]
    assert found((Rule(cued, "N"),), "nr: 4821") == [("nr: 4821", "N")]


def test_a_rule_trims_the_characters_it_declares() -> None:
    trailing = re.compile(r"https?://\S+")
    text = "See https://az.be/page."
    assert found((Rule(trailing, "URL"),), text) == [("https://az.be/page.", "URL")]
    assert found((Rule(trailing, "URL", trim="."),), text) == [
        ("https://az.be/page", "URL")
    ]


def test_a_rule_sees_the_spans_the_rules_above_it_accepted() -> None:
    # Order is the contract: the better evidence is listed first.
    cued = re.compile(r"nr (?P<value>\d+)")
    bare = re.compile(r"\d+")
    rules = (Rule(cued, "N", "value"), Rule(bare, "N", overlap=Overlap.ANY_LABEL))
    assert found(rules, "nr 4821 and 99") == [("4821", "N"), ("99", "N")]
    # Reversed, the bare rule claims the run first and the cued one is blocked.
    reversed_rules = (
        Rule(bare, "N"),
        Rule(cued, "N", "value", overlap=Overlap.ANY_LABEL),
    )
    assert found(reversed_rules, "nr 4821 and 99") == [("4821", "N"), ("99", "N")]


def test_a_rule_blocks_its_own_later_hits_under_an_overlap_policy() -> None:
    overlapping = re.compile(r"(?=(\d\d))")
    rules = (Rule(overlapping, "N", 1, overlap=Overlap.ANY_LABEL),)
    assert found(rules, "1234") == [("12", "N"), ("34", "N")]


def test_a_hint_that_does_not_match_skips_its_rule_entirely() -> None:
    asked: list[str] = []

    def guard(candidate: Candidate) -> bool:
        asked.append(candidate.value)
        return True

    hint = re.compile(r"bloedgroep")
    rules = (Rule(DIGITS, "N", guard=guard, hint=hint),)
    assert found(rules, "waarde 123") == []
    assert asked == []
    assert found(rules, "bloedgroep 123") == [("123", "N")]
    assert asked == ["123"]


def test_a_hint_is_scanned_once_however_many_rules_share_it() -> None:
    scans = 0
    real = re.compile(r"cue")

    class Counting:
        def search(self, text: str):
            nonlocal scans
            scans += 1
            return real.search(text)

    cache = _HintCache("no cue here")
    counting = Counting()
    assert cache.permits(counting)
    assert cache.permits(counting)
    assert scans == 1
    # No hint at all is always permitted, and costs no scan.
    assert cache.permits(None)
    assert scans == 1


def test_an_empty_table_and_an_empty_text_produce_nothing() -> None:
    assert collect((), "anything") == []
    assert collect((Rule(DIGITS, "N"),), "") == []


def test_every_emitted_span_is_labelled_as_coming_from_the_regex_engine() -> None:
    (span,) = collect((Rule(DIGITS, "N"),), "x 42")
    assert (span.start, span.end, span.label, span.source) == (2, 4, "N", "regex")


# The three builders below run once, while the country tables are being built at
# import time, and never again -- so mutation testing attributes them to
# whichever test module happened to trigger the import and reports every mutant
# as surviving. Calling them from a test is what makes that attribution true,
# and each is worth pinning on its own: a guard that stops being consulted, or
# a digit run that stops being bounded, silently narrows a dozen detectors.
def test_a_checksum_guard_asks_the_scheme_about_the_matched_value_only() -> None:
    asked: list[str] = []

    def ok(value: str) -> bool:
        asked.append(value)
        return value == "12"

    text = "x 12 y"
    match = DIGITS.search(text)
    guard = _checksum(ok)
    assert guard(Candidate(text, match, match.start(), match.end()))
    assert asked == ["12"]
    # It reads `value`, not the line the hit came from.
    assert not guard(Candidate(text, match, 0, len(text)))
    assert asked == ["12", text]


def test_a_digit_count_guard_counts_digits_and_ignores_the_separators() -> None:
    text = "80.01.01-001.07"
    match = re.compile(re.escape(text)).search(text)
    candidate = Candidate(text, match, 0, len(text))
    assert _digit_count(11)(candidate)
    assert _digit_count(9, 11, 13)(candidate)
    assert not _digit_count(10, 13)(candidate)


def test_a_digit_run_matches_exactly_that_many_digits_however_they_are_broken() -> None:
    pattern = re.compile(_digit_run(4))
    for run in ("1234", "12 34", "12-34", "1.2/3 4"):
        assert pattern.fullmatch(run), run
    # A longer run is not a run of four: the lookarounds refuse every offset.
    assert pattern.search("12345") is None
    assert pattern.search("123") is None
