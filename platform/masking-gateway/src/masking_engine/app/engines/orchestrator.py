"""Combine model and regex spans according to label ownership.

``model`` mode returns model spans. ``regex`` mode returns regex spans.
``model_regex`` mode combines both sources: it drops model spans for labels
regex owns outright and unions the rest with every regex span.
The merge uses character offsets and does not modify text.
"""

from __future__ import annotations

import logging
from bisect import bisect_right

from ..core.schemas import DetectionMode, MaskSpan
from . import span_shape

log = logging.getLogger(__name__)

_MODE = DetectionMode

# Regex owns these structured labels. PHONE_NUMBER stays additive: the
# model's recall on it is measured at or above regex recall, so ownership
# would only suppress correct model spans when a real-world spelling missed
# the regex (2026-08-25 rescored boards).
#
# Empty means ownership suppression is not implemented: ``merge`` unions the
# two sources instead of arbitrating between them, and `regex_engine` relies on
# that (see its ORGANIZATION_NAME note). Refilling this set is not enough --
# restore the per-span overlap arbitration too, keyed by a dict of regex spans
# per label so the scan stays linear rather than model x regex.
_REGEX_OWNERS: frozenset[str] = frozenset()

# Labels regex outranks *where regex spoke*. Each carries a checksum or a fixed
# grammar a regex validates outright, so a model detection adds guesswork
# where a rule already gives a certain answer -- and a validated regex miss
# on the free digit run or address is likelier than the model correctly
# reasoning past what the checksum will not accept (2026-08-25 clinical
# board: model RIZIV FPs on page-footer junk; the same overconfidence shows
# up on INSZ, BTW/organization IDs, e-mails and URLs). DATE stays out of
# this set: bare years ("sinds eind 2020", "In 2003") carry no separators,
# never match a date regex, and only the model recovers them.
# NIR and RPPS (French locale) sit here on the same rationale as INSZ/RIZIV,
# even though the deployed Octo checkpoint has no NIR/RPPS class to emit --
# this only guards a future retrained checkpoint that might.
#
# BMI, HEIGHT, WEIGHT: unit-anchored, so regex is reliable while the model
# over-triggers on similar bare numerics nearby.
_REGEX_EXCLUSIVE: frozenset[str] = frozenset(
    {
        "RIZIV_INAMI",
        "INSZ_NISS",
        "BTW_EENHEID",
        "EMAIL",
        "URL",
        # The French pair joins for the same reason as its Belgian
        # counterparts. NIR carries a mod-97 checksum the regex validates
        # outright, and RPPS/ADELI only ever appears next to its own keyword,
        # so both give a certain answer where a model detection would add
        # guesswork. Octo's checkpoint does carry B-NIR/I-NIR/B-RPPS/I-RPPS
        # (ids 20/26/52/58) and those predictions are dropped here, not by
        # the converter -- retraining is not required to change that.
        "NIR",
        "RPPS",
        "BMI",
        "HEIGHT",
        "WEIGHT",
        # The English-language identifiers join on the same ruling. SSN and
        # NHS carry a rule the regex validates outright -- allocation blocks
        # and a mod-11 checksum -- and NPI and GMC only ever appear next to
        # their own keyword. All four therefore give a certain answer where a
        # model detection would add guesswork, and Octo's checkpoint carries
        # no head for any of them in any case.
        "SSN",
        "NPI",
        "NHS_NUMBER",
        "GMC",
    }
)

# Labels regex may never contribute for, even additively -- the opposite of
# _REGEX_EXCLUSIVE. Empty means unused; kept as a lever, like _REGEX_OWNERS.
_REGEX_MODEL_ONLY: frozenset[str] = frozenset()


def _position(span: MaskSpan) -> tuple[int, int, int]:
    return (span.page if span.page is not None else -1, span.start, span.end)


class _Claims:
    """The character ranges regex actually claimed, for an overlap test.

    Both span lists describe one text unit, so a plain interval index is
    enough. It is an index rather than a scan because a dense page yields
    thousands of regex spans and every model span would otherwise walk all of
    them -- the same reason `regex_arbitration` keeps its accepted spans
    sorted.
    """

    def __init__(self, spans: list[MaskSpan]) -> None:
        ordered = sorted((span.start, span.end) for span in spans)
        self._starts = [start for start, _ in ordered]
        # The furthest end at or before each start, so one bisect answers
        # whether anything reaches into the candidate.
        self._reach: list[int] = []
        furthest = 0
        for _, end in ordered:
            furthest = max(furthest, end)
            self._reach.append(furthest)

    def overlaps(self, span: MaskSpan) -> bool:
        index = bisect_right(self._starts, span.start)
        reaches_in = index > 0 and self._reach[index - 1] > span.start
        starts_within = index < len(self._starts) and self._starts[index] < span.end
        return reaches_in or starts_within


class Orchestrator:
    """Combine model and regex spans into one span list."""

    def __init__(
        self,
        mode: _MODE = "model_regex",
        *,
        shape_rule: span_shape.ShapeRule | None = span_shape.admits,
    ) -> None:
        if mode not in ("model", "model_regex", "regex"):
            raise ValueError(f"Unknown detection mode: {mode}")
        self.mode = mode
        # Injected rather than imported at the point of use so a caller can
        # turn the gate off (`None`) or substitute one, which is what
        # MODEL_ID_SHAPE_GATE does through `runtime`. The default is the real
        # rule: a deployment that says nothing gets the gate.
        self.shape_rule = shape_rule

    def _shaped(
        self, model_spans: list[MaskSpan], texts: dict[int | None, str]
    ) -> list[MaskSpan]:
        """Drop model spans whose text cannot be the class they claim.

        Runs before the mode dispatch, because a detection that is the wrong
        length for its own label is wrong in `model` mode too -- nothing about
        it is a question of which source to believe.

        The filtering itself is `span_shape`'s, shared with the teams that
        merge their own spans without an orchestrator at all.
        """
        return span_shape.admitted(model_spans, texts, rule=self.shape_rule)

    def merge(
        self,
        model_spans: list[MaskSpan],
        regex_spans: list[MaskSpan],
    ) -> list[MaskSpan]:
        """Apply ownership rules and return the final span list."""
        kept_model: list[MaskSpan] = []
        claims = _Claims(regex_spans)

        for span in model_spans:
            # Exclusivity is a ruling about which source to believe, so it can
            # only apply where both sources spoke. Applied unconditionally it
            # answered "regex is right" for text regex never looked at, and a
            # value only regex may claim left the document unmasked whenever
            # the rule missed it: a RIZIV number packed behind a "/" is
            # detected by the model at 1.00 and by no pattern at all.
            if span.label in _REGEX_EXCLUSIVE and claims.overlaps(span):
                log.info(
                    "orchestration_suppression accepted_source=none "
                    "accepted_label=none suppressed_source=model "
                    "suppressed_label=%s page=%s start=%d end=%d "
                    "reason=exclusive_regex_label",
                    span.label,
                    span.page,
                    span.start,
                    span.end,
                )
                continue
            kept_model.append(span)

        kept_regex = [
            span for span in regex_spans if span.label not in _REGEX_MODEL_ONLY
        ]
        for span in regex_spans:
            if span.label in _REGEX_MODEL_ONLY:
                log.info(
                    "orchestration_suppression accepted_source=none "
                    "accepted_label=none suppressed_source=regex "
                    "suppressed_label=%s page=%s start=%d end=%d "
                    "reason=model_only_label",
                    span.label,
                    span.page,
                    span.start,
                    span.end,
                )

        final = kept_model + kept_regex
        log.info(
            "orchestrate mode=%s model_in=%d regex_in=%d suppressed=%d kept_model=%d "
            "regex_suppressed=%d kept_regex=%d final=%d",
            self.mode,
            len(model_spans),
            len(regex_spans),
            len(model_spans) - len(kept_model),
            len(kept_model),
            len(regex_spans) - len(kept_regex),
            len(kept_regex),
            len(final),
        )
        return sorted(final, key=_position)

    def execute(
        self,
        model_spans: list[MaskSpan],
        regex_spans: list[MaskSpan],
        texts: dict[int | None, str] | None = None,
    ) -> list[MaskSpan]:
        """Apply the configured detection mode.

        *texts* carries the text each span was found in, keyed by page the way
        `LabelConverter.convert_all` already takes it. Without it the shape
        gate has nothing to read and is skipped.
        """
        if self.shape_rule is not None and texts is not None:
            model_spans = self._shaped(model_spans, texts)
        if self.mode == "model":
            return sorted(model_spans, key=_position)
        if self.mode == "regex":
            return sorted(regex_spans, key=_position)
        return self.merge(model_spans, regex_spans)
