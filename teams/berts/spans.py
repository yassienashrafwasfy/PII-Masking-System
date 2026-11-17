"""Berts-owned span policy: how model and regex detections are combined.

Berts used to carry its own regex detectors here -- INSZ, RIZIV, BTW plus a
per-language table of month names, date anchors and age words. They are gone.
The platform's `masking_engine.find_regex_spans` is now the only structured
detector this team runs, the same one Octo runs, for two reasons that both
showed up in a live run rather than in review:

* **It found strictly more.** A differential A/B over three real clinical
  letters put the platform detector as a superset of everything this module
  actually contributed: the only spans it produced that the platform did not
  were AGE forms and single-digit noise. Meanwhile the platform found
  a French NIR, a social-security number, every phone number, postcodes and
  house numbers that this module was blind to -- and printed them masked in
  the same document where this team printed them in the clear.
* **Belgian-only was not a decision anyone took.** This module validated
  identifiers for one country while the models read three languages. The
  platform carries `regex_france`, `regex_uk` and `regex_us` already, so the
  gap was this team's table, not the repository's.

What is left here is the part that is genuinely Berts' own: which detector
wins when the model and the regex layer disagree, and two span repairs that
exist because of how these checkpoints tag text.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import replace

from masking_engine import MaskSpan, span_shape

# Canonical labels the model and the regex layer both speak for, and neither
# owns. A span carrying one of these is worth the same from either source, so
# `_priority` reads it off the table below by label alone and the length
# tiebreak in `merge_drop_all` decides -- whichever of the two covers more of
# the value wins.
#
# They used to be dropped from the regex list unconditionally, not on
# overlap, so an empty model span list still swallowed every regex date, age
# and phone and the model was alone on all three. A checkpoint that tags part
# of a value then released the rest in the clear with nothing left to catch
# it: the Dutch model read "- 04-2025:" as "-2025" on two lines of one
# letter, the platform regex had all of "04-2025" on both, and the document
# went out reading "- 04 <chip>". This is what the platform `Orchestrator`
# does for the same three labels -- it lists none of them as regex-owned or
# regex-exclusive, so both sources reach the document there.
HYBRID_LABELS = frozenset({"AGE", "DATE", "PHONE_NUMBER"})

# What a span is worth when it overlaps another, by canonical label. The
# lookup is by label and not by source, which is what makes the hybrid labels
# above tie: a regex DATE and a model DATE both score 8 and the longer one
# is kept. Every other regex label is a checksum- or format-validated
# identifier that the platform engine has already arbitrated against its own
# siblings, so it outranks any model guess and takes the default below. That
# also means the relative order of two regex labels does not matter here --
# the platform guarantees its output has no cross-label overlaps.
_VALIDATED_REGEX_PRIORITY = 9
_MODEL_LABEL_PRIORITY = {
    "DATE": 8,
    "ADDRESS": 6,
    "AGE": 5,
    "NAME": 4,
    "ORGANIZATION_NAME": 4,
}


def _priority(span: MaskSpan) -> int:
    return _MODEL_LABEL_PRIORITY.get(span.label, _VALIDATED_REGEX_PRIORITY)


def merge_drop_all(
    model_spans: list[MaskSpan], regex_spans: list[MaskSpan]
) -> list[MaskSpan]:
    """Combine both span lists, keeping the strongest claim on each character.

    A span is scored by `_priority` and, within a score, by how much of the
    text it covers; the highest-scoring span takes its characters and every
    later span that touches them is dropped. Exact ties go to the model,
    because `sorted` is stable and the model list is passed first.

    Both lists must already carry canonical labels: the platform detector
    emits them, and model spans have to pass through LabelConverter before
    they get here, or a model DATE arrives as `DATA` and is treated as an
    identifier.
    """
    candidates = list(model_spans) + list(regex_spans)
    covered: set[int] = set()
    kept: list[MaskSpan] = []
    for span in sorted(
        candidates,
        key=lambda item: (_priority(item), item.end - item.start),
        reverse=True,
    ):
        if any(index in covered for index in range(span.start, span.end)):
            continue
        kept.append(span)
        covered.update(range(span.start, span.end))
    return sorted(kept, key=lambda span: (span.start, span.end, span.label))


# Canonical labels whose span is one number written as one token. A DATE or an
# AGE is deliberately absent: "01/01/2020-31/12/2020" is two dates joined by a
# dash, and growing either one over the whole range would mask both as a single
# unparseable value.
_WHOLE_NUMBER_LABELS = frozenset(
    {"INSZ_NISS", "RIZIV_INAMI", "BTW_EENHEID", "PHONE_NUMBER"}
)
_NUMBER_SEPARATORS = ".-/"


def _whole_number_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return *start*, *end* grown to the digit run the span sits inside."""
    while start > 0 and (
        text[start - 1].isdigit()
        or (
            text[start - 1] in _NUMBER_SEPARATORS
            and start > 1
            and text[start - 2].isdigit()
            and start < len(text)
            and text[start].isdigit()
        )
    ):
        start -= 1
    while end < len(text) and (
        text[end].isdigit()
        or (
            text[end] in _NUMBER_SEPARATORS
            and end + 1 < len(text)
            and text[end + 1].isdigit()
            and text[end - 1].isdigit()
        )
    ):
        end += 1
    return start, end


def cover_whole_numbers(spans: list[MaskSpan], text: str) -> list[MaskSpan]:
    """Grow every numeric span to the whole number it sits inside.

    A detector that matches part of a longer digit run produces the worst
    possible output: the document looks masked, and the digits outside the
    span are printed unchanged. That is how a national number whose checksum
    the INSZ pattern rejected still lost only its tail -- the phone pattern
    took `.07.03-456.12` out of `78.07.03-456.12` and left the patient's year
    of birth standing. Detection will keep being imperfect, so the invariant
    is enforced on the final spans instead: whatever a detector found inside a
    number, the whole number is masked. Growing a span only ever masks more.

    Call this on the FINAL, canonical-labeled span list, the way
    trim_title_prefix() is called -- model spans do not carry canonical labels
    before LabelConverter runs.
    """
    grown: list[MaskSpan] = []
    for span in spans:
        if span.label in _WHOLE_NUMBER_LABELS:
            start, end = _whole_number_bounds(text, span.start, span.end)
            if (start, end) != (span.start, span.end):
                span = replace(span, start=start, end=end)
        grown.append(span)
    # Two spans inside one run grow onto each other. Keep the longer one, so
    # the number is replaced once rather than twice from overlapping offsets.
    kept: list[MaskSpan] = []
    for span in sorted(grown, key=lambda item: (item.start, -item.end)):
        if kept and span.start >= kept[-1].start and span.end <= kept[-1].end:
            continue
        kept.append(span)
    return sorted(kept, key=lambda span: (span.start, span.end, span.label))


_TITLE_PREFIX = re.compile(r"^(?:dr|prof)\.\s+", re.I)


def trim_title_prefix(spans: list[MaskSpan], text: str) -> list[MaskSpan]:
    """Exclude a leading professional title ("dr. ", "prof. ") from NAME
    spans, so the masking router never touches it.

    The model detects the title as part of the person's name span (e.g.
    "dr. Khadija Chakiri", source=model) -- reasonable on its own, since the
    title and name usually do belong together as one mention. But the
    pseudonymizer overwrites a span's characters uniformly, so a title
    caught inside a NAME span gets scrambled into meaningless text right
    along with the name ("dr." -> "vv.") instead of staying legible or
    being replaced with an equally-titled fake name. A title alone doesn't
    identify anyone, so simply excluding it from the span removes it from
    what gets masked at all -- the reader keeps the "this is a doctor"
    context, only the actual name is hidden. Call this on the FINAL,
    canonical-labeled span list (after LabelConverter), not on
    merge_drop_all()'s output -- model-sourced spans don't carry the
    "NAME" label yet at that point, only their native model label."""
    trimmed: list[MaskSpan] = []
    for span in spans:
        if span.label == "NAME":
            match = _TITLE_PREFIX.match(text[span.start : span.end])
            if match:
                span = replace(span, start=span.start + match.end())
        trimmed.append(span)
    return trimmed


def shape_gate() -> bool:
    """Whether an impossible-shape model detection is refused.

    Read per call from the environment, the way `language_routing` in
    `runtime` is, and spelled the same as the platform's own switch so one
    deployment variable governs every team. A typo raises rather than
    silently leaving the gate on: an operator reaching for this is letting a
    detection through that the gate is currently eating, and finding out by
    reading masked output is too late.
    """
    value = os.getenv("MODEL_ID_SHAPE_GATE", "on").strip().lower()
    if value not in ("on", "off"):
        raise ValueError("MODEL_ID_SHAPE_GATE must be one of: off, on.")
    return value == "on"


def drop_impossible_shapes(
    model_spans: Sequence[MaskSpan], text: str
) -> list[MaskSpan]:
    """Refuse model spans whose text cannot be the class they claim.

    This team reaches the platform's gate by hand because it has no
    `Orchestrator`: `runtime.detect` merges with `merge_drop_all` below, and
    the gate lives on the path Berts does not take. Nothing about the rule is
    Berts' own -- `span_shape` decides, and logs -- so the only thing here is
    the call the orchestrator would otherwise have made.

    Without it, an AGE prediction over a word instead of a number reaches the
    surrogate, which has no number to reformat and redraws letters instead:
    an English letter released reading "Rrovz woman presenting" where the
    source said "Young woman", and a Dutch one "Sinds trisrztc plen maanden".
    The regex layer cannot be relied on to have covered the same characters
    with an AGE span of its own -- it finds a number, and the whole point of
    these detections is that there is no number there.
    """
    rule = span_shape.admits if shape_gate() else None
    return span_shape.admitted(model_spans, {None: text}, rule=rule)
