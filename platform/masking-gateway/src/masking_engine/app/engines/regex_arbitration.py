"""Which label wins an overlap, and how a label is renamed on the way out.

Detectors are independent and deliberately allowed to overlap, so the
arbitration is the last word rather than a tie-breaker."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace

from ..core.schemas import MaskSpan

# Cross-label arbitration: the detectors above are deliberately independent,
# so two rules can claim overlapping text ("09-11-67-80" is both a DOSAGE
# schedule and a phone shape; an NHS number can sit inside an
# ORGANIZATION_ID run). Downstream consumers union these spans additively and
# must never receive two different labels for the same characters, so
# find_regex_spans arbitrates: checksummed identifiers outrank strict-format
# labels, which outrank loose shape labels; within a class the longer span
# wins.
_LABEL_PRIORITY: dict[str, int] = {
    label: index
    for index, label in enumerate(
        [
            "INSZ",
            "RIZIV",
            "NIR",
            # Both are ten digits, and an NPI is only ever detected next to
            # its own keyword, so where the two rules claim the same run the
            # keyword is the better evidence.
            "NPI",
            "NHS_NUMBER",
            "SSN",
            "BTW_EENHEID",
            "ORGANIZATION_ID",
            "CREDITCARDNUMBER",
            "GMC",
            "RPPS",
            "DATE",
            "EMAIL",
            "URL",
            "MRN",
            "TELEFOON",
            "PHONE_NUMBER",
            "DOSAGE",
            "BLOOD_TYPE",
            "BMI",
            "HEIGHT",
            "WEIGHT",
            "AGE",
            "SEX",
            "ZIPCODE",
            "BUILDINGNUM",
        ]
    )
}

# Regex spans bypass LabelConverter (only model spans are converted), so
# this module maps its native labels onto qa-pii-v1 itself. Labels without
# a canonical target stay out of the output until the taxonomy grows;
# they still participate in arbitration above.
#
# NIR and RPPS need no entry in either list: taxonomy schema 3 made them
# canonical labels under their own names, so they pass through unrenamed.
# ADELI is detected by the RPPS rule and reported as RPPS, which is the
# alias the taxonomy records for it.
_OUTPUT_RENAMES = {
    "INSZ": "INSZ_NISS",
    "RIZIV": "RIZIV_INAMI",
    "TELEFOON": "PHONE_NUMBER",
    "SEX": "GENDER",
    "ZIPCODE": "ADDRESS",
    "BUILDINGNUM": "ADDRESS",
    "STREET": "ADDRESS",
    "CITY": "ADDRESS",
    "ORGANIZATION_ID": "BTW_EENHEID",
}

_UNMAPPED_OUTPUT_LABELS = frozenset(
    {
        "BLOOD_TYPE",
        "CREDITCARDNUMBER",
        "DOSAGE",
        "HEALTH_INSURANCE_ID",
        "MRN",
    }
)


def _to_output_spans(spans: list[MaskSpan]) -> list[MaskSpan]:
    kept = [span for span in spans if span.label not in _UNMAPPED_OUTPUT_LABELS]
    return [
        replace(span, label=_OUTPUT_RENAMES.get(span.label, span.label))
        for span in kept
    ]


def _arbitrate_overlaps(spans: list[MaskSpan]) -> list[MaskSpan]:
    """Return spans with no cross-label overlaps, deterministically.

    Greedy accept in order of (-length, priority, start); a span is dropped
    when it overlaps any already-accepted span. Same-label duplicates are
    resolved the same way, keeping the longer span.
    """

    def rank(span: MaskSpan) -> tuple[int, int, int, str]:
        return (
            -(span.end - span.start),
            _LABEL_PRIORITY.get(span.label, len(_LABEL_PRIORITY)),
            span.start,
            span.label,
        )

    # Accepted spans never overlap each other, so keeping them ordered by start
    # means a candidate can only collide with the neighbour on either side of
    # its insertion point: the span starting before it that reaches past its
    # start, or the span starting after it that begins before its end. Scanning
    # the whole accepted list instead cost n(n-1)/2 comparisons -- 12.5 million
    # on a 10 MiB body yielding 5000 spans, where matches are spread out and
    # the scan therefore runs to the end every time.
    accepted: list[MaskSpan] = []
    starts: list[int] = []
    for span in sorted(spans, key=rank):
        index = bisect_left(starts, span.start)
        if index > 0 and accepted[index - 1].end > span.start:
            continue
        if index < len(accepted) and accepted[index].start < span.end:
            continue
        starts.insert(index, span.start)
        accepted.insert(index, span)
    return sorted(accepted, key=lambda s: (s.start, s.end, s.label))


def coalesce_spans(spans: list[MaskSpan]) -> list[MaskSpan]:
    """Return non-overlapping spans that cover every detected character.

    Detection can return two spans that overlap, such as a model NAME inside a
    regex URL. A replacement no longer has the length of the text it covers, so
    one region has to become one replacement. The merged span keeps the label
    of its longest contributor, and the region covers the union, so no detected
    character survives.

    Where a model and a regex detection agree exactly -- same label, same
    extent, which is the ordinary case for a phone number -- the model's wins,
    because it carries a confidence and the regex one does not. Left to the
    order the two happened to arrive in, three identical phone numbers in one
    footer came out with a score on some and not on others.
    """
    ordered = sorted(spans, key=lambda span: (span.page or -1, span.start, -span.end))
    merged: list[MaskSpan] = []
    for span in ordered:
        last = merged[-1] if merged else None
        if last is None or last.page != span.page or span.start >= last.end:
            merged.append(span)
            continue
        winner = max(
            (last, span),
            key=lambda item: (
                item.end - item.start,
                item.label,
                item.confidence is not None,
            ),
        )
        merged[-1] = MaskSpan(
            last.start,
            max(last.end, span.end),
            winner.label,
            winner.source,
            page=last.page,
            confidence=winner.confidence,
            source_layer=winner.source_layer,
        )
    return merged
