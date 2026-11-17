"""The rule engine the pattern-table detectors share.

Several detectors are the same program written out many times: run a
pattern, take a group, decide whether to keep the hit, append a span. The
only things that differ between one of those loops and the next are data —
which pattern, which label, what has to hold, and whether an already
accepted span blocks it. This module holds the loop; the detector modules
hold the table. A new detection rule is a new row, not a new branch.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from ..core.schemas import MaskSource, MaskSpan
from .regex_shared import _trim_right

_SOURCE: MaskSource = "regex"


@dataclass(frozen=True)
class Candidate:
    """One regex hit, with everything a guard is allowed to ask about it."""

    text: str
    match: re.Match[str]
    start: int
    end: int

    @property
    def value(self) -> str:
        return self.text[self.start : self.end]


Guard = Callable[[Candidate], bool]


def every(*guards: Guard) -> Guard:
    """A guard that holds when all of `guards` hold."""
    return lambda candidate: all(guard(candidate) for guard in guards)


def either(*guards: Guard) -> Guard:
    """A guard that holds when any of `guards` holds."""
    return lambda candidate: any(guard(candidate) for guard in guards)


def value_ok(ok: Callable[[str], bool]) -> Guard:
    """The matched value passes a validator of its own -- a checksum, a parse."""
    return lambda candidate: ok(candidate.value)


class Overlap(Enum):
    """What an already accepted span does to a later rule's match."""

    ALLOW = "allow"
    """Emit regardless — the rules cannot collide, or both spans are wanted."""

    ANY_LABEL = "any_label"
    """Drop the hit if any accepted span covers it, whatever it is labelled."""

    SAME_LABEL = "same_label"
    """Drop the hit only if an accepted span with this label covers it."""

    DUPLICATE = "duplicate"
    """Drop the hit only when an accepted span already claims exactly it.

    For the pair of rules where a cue-led pattern and a bare one describe the
    same value: the cued hit is the better evidence, and re-emitting the
    identical range from the bare rule says nothing new.
    """


def _blocked(
    accepted: Iterable[MaskSpan], start: int, end: int, label: str, policy: Overlap
) -> bool:
    if policy is Overlap.ALLOW:
        return False
    if policy is Overlap.DUPLICATE:
        return any(
            span.start == start and span.end == end and span.label == label
            for span in accepted
        )
    return any(
        span.start < end
        and start < span.end
        and (policy is Overlap.ANY_LABEL or span.label == label)
        for span in accepted
    )


@dataclass(frozen=True)
class Rule:
    """A pattern, the label it carries, and what has to hold to accept it."""

    pattern: re.Pattern[str]
    label: str
    group: str | int = 0
    guard: Guard | None = None
    overlap: Overlap = Overlap.ALLOW
    hint: re.Pattern[str] | None = None
    """A cheap pre-scan: skip this rule entirely when the hint does not match."""

    trim: str = ""
    """Trailing characters to drop from the match before guarding and emitting."""

    def _span_of(self, text: str, match: re.Match[str]) -> tuple[int, int]:
        start, end = match.span(self.group)
        if self.trim:
            end = _trim_right(text, start, end, self.trim)
        return start, end

    def accepts(self, candidate: Candidate, accepted: Iterable[MaskSpan]) -> bool:
        if self.guard is not None and not self.guard(candidate):
            return False
        return not _blocked(
            accepted, candidate.start, candidate.end, self.label, self.overlap
        )

    def collect_into(self, text: str, spans: list[MaskSpan]) -> None:
        """Append this rule's spans, deciding against the spans accepted so far.

        `spans` grows as the rule runs, so a rule's own earlier hits block its
        later ones under an overlap policy, exactly as sequential loops did.
        """
        for match in self.pattern.finditer(text):
            start, end = self._span_of(text, match)
            if self.accepts(Candidate(text, match, start, end), spans):
                spans.append(MaskSpan(start, end, self.label, _SOURCE))


class _HintCache:
    """One scan per distinct hint pattern, however many rules share it."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._hits: dict[re.Pattern[str], bool] = {}

    def permits(self, hint: re.Pattern[str] | None) -> bool:
        if hint is None:
            return True
        if hint not in self._hits:
            self._hits[hint] = hint.search(self._text) is not None
        return self._hits[hint]


def collect(rules: Iterable[Rule], text: str) -> list[MaskSpan]:
    """Run `rules` in order and return the spans they accept.

    Order is part of a table's contract: a rule sees every span the rules
    above it accepted, so the better evidence is listed first.
    """
    spans: list[MaskSpan] = []
    hints = _HintCache(text)
    for rule in rules:
        if hints.permits(rule.hint):
            rule.collect_into(text, spans)
    return spans
