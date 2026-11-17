"""Refuse a model detection that cannot be the identifier class it claims.

A national identifier has a fixed length. The detector does not know that:
trained on a corpus where all but one of 571 bare digit runs carried an
identifier label, it has no route to "this number is nothing" and parks any
unexplained run on whichever identifier head is nearest in length. Measured on
the deployed checkpoint, a run of nine digits reads as RIZIV at 0.999 whether
the sentence says "RIZIV-nummer" or "the machine turned 535581078 times", and
the checkpoint it replaced did the same at 310 false positives per 500 records
against this one's 313. It is a property of the training data, not of either
checkpoint, so it cannot be tuned away with a confidence floor.

Length is the part of that guess a rule can check without adjudicating the
number itself. This module answers one question -- can this text be that class
-- and deliberately stops short of the checksum: a validated number is one the
regex engines already claim, and rejecting everything they would not claim
would re-create the blanket suppression `Orchestrator.merge` was changed to
stop applying, taking the genuine detections regex misses down with it.

The counts are the ones this repository's own validators enforce, not a second
opinion about the formats: `regex_belgium._riziv_ok` (eight or eleven, the
legacy form being the eight), `regex_belgium._insz_ok` (eleven),
`regex_france._NIR_RE` (fifteen) and `regex_uk._NHS_RE` (ten).

AGE is gated on a weaker shape -- it has to carry a digit -- for the same
reason and with the same limit. The detector reads the words around a number
as much as the number, and on a Dutch clinical letter it labelled "ongeveer"
and "drie" ("approximately" and "three", in "since approximately three
months") AGE at 0.82 and 0.93 while every true detection on that page sat
above 0.9998. A confidence floor cannot separate those. The surrogate cannot
render them either: it formats a number the way the source wrote it, and with
no number to parse it falls back to redrawing letters, so the released page
read "Sinds trisrztc plen maanden".

The cost is an age spelled out in words. Nothing else detects one -- the regex
engines match digits -- so gating this leaves such an age in the clear rather
than masked as nonsense. That is the deliberate trade: a spelled-out age is
rare in these documents and reads as a quasi-identifier at worst, and the
alternative was corrupting ordinary prose on every letter that contains it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from ..core.schemas import MaskSpan

log = logging.getLogger(__name__)

# A rule that answers whether *text* can be the class *label* names. `admits`
# is the one this module ships; a caller substitutes its own, or passes None
# to gate nothing at all.
ShapeRule = Callable[[str, str], bool]

# What an identifier is printed with between its groups, taken together from
# the separator classes the grammars accept: `[-./ ]` (RIZIV), `[./ -]`
# (INSZ), `[ .\-/]` (NIR). The no-break space joins them because PDF text
# extraction leaves one wherever the document grouped digits with a space.
_SEPARATORS = "  .-/"

# Digit counts each class is defined to have. A label absent here is not
# gated: the rule is only useful where the format is fixed, and a class whose
# length varies (ADDRESS, NAME, DATE, PHONE_NUMBER) has no shape to check.
_DIGIT_COUNTS: dict[str, frozenset[int]] = {
    "RIZIV_INAMI": frozenset({8, 11}),
    "INSZ_NISS": frozenset({11}),
    "NIR": frozenset({15}),
    "NHS_NUMBER": frozenset({10}),
}


# Labels that have no fixed length but cannot be written without a digit at
# all. Judged separately from `_DIGIT_COUNTS`, which asks for an exact count.
_DIGIT_BEARING = frozenset({"AGE"})


def gated_labels() -> frozenset[str]:
    """Return the labels this rule can judge."""
    return frozenset(_DIGIT_COUNTS) | _DIGIT_BEARING


def admits(label: str, text: str) -> bool:
    """Return whether *text* could be a *label*, judged on shape alone.

    True for every label the rule does not cover, so a caller can apply it to
    a whole span list without first filtering: an unjudged label is admitted,
    never silently dropped.
    """
    if label in _DIGIT_BEARING:
        return any(character.isdigit() for character in text)
    counts = _DIGIT_COUNTS.get(label)
    if counts is None:
        return True
    digits = 0
    for character in text:
        if character.isdigit():
            digits += 1
        elif character not in _SEPARATORS:
            # A currency sign, a bracket or a letter is not part of any of
            # these numbers. An invoice total of EUR 377249721033083 carries
            # fifteen digits and would otherwise pass as a NIR.
            return False
    return digits in counts


def admitted(
    spans: Sequence[MaskSpan],
    texts: Mapping[int | None, str],
    *,
    rule: ShapeRule | None = admits,
) -> list[MaskSpan]:
    """Drop the spans in *spans* whose text cannot be the class they claim.

    Lives here rather than in the caller because there are two callers now --
    the platform `Orchestrator` and Berts, which builds its span list without
    one -- and a gate applied in two places would eventually be applied two
    ways. The log line is part of what is shared: a suppression that shows up
    under one team and not the other is worse than no log at all.

    *texts* maps a span's page to the text its offsets index, which is the
    shape `LabelConverter.convert_all` already takes; a plain-text body is
    keyed `None`. A span whose page is missing, or whose slice is empty, is
    kept: this rule refuses what it can read and never guesses.

    *rule* of None returns the list unchanged, which is what
    `MODEL_ID_SHAPE_GATE=off` resolves to.
    """
    if rule is None:
        return list(spans)
    kept: list[MaskSpan] = []
    for span in spans:
        text = texts.get(span.page, "")[span.start : span.end]
        if text and not rule(span.label, text):
            log.info(
                "orchestration_suppression accepted_source=none "
                "accepted_label=none suppressed_source=model "
                "suppressed_label=%s page=%s start=%d end=%d "
                "reason=impossible_identifier_shape",
                span.label,
                span.page,
                span.start,
                span.end,
            )
            continue
        kept.append(span)
    return kept
