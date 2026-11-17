"""Detect structured identifiers and contextual entities with regular expressions.

Structured formats carry a checksum or constrained structure; contextual
entities (names, addresses, clinical values) use validated slot patterns.
Detectors are independent, so ``find_regex_spans`` arbitrates cross-label
overlaps before returning ``MaskSpan(source='regex')`` items and never logs
matched text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from ..core.schemas import MaskSpan
from .regex_address import _building_number_spans, _postcode_spans
from .regex_arbitration import _arbitrate_overlaps, _to_output_spans
from .regex_clinical import _clinical_spans
from .regex_contact import (
    _EMAIL_RE,
    _URL_CONTEXT_RE,
    _URL_RE,
    _email_ok,
    _phone_spans,
    _url_ok,
)
from .regex_dates import _date_spans
from .regex_demographic import _demographic_spans
from .regex_financial import _CREDITCARD_CONTEXT_RE, _CREDITCARD_RE, _luhn_ok
from .regex_identifiers import _identifier_spans, _is_national_identifier
from .regex_organization import _organization_name_spans
from .regex_rules import Candidate, Overlap, Rule, collect, value_ok

log = logging.getLogger(__name__)


_ANY_DIGIT_RE = re.compile(r"\d")


# Compile detection patterns.


# Validate candidates that need structural checks.


# Detect each family of labels.


def _card_ok(candidate: Candidate) -> bool:
    """A Luhn-valid card number that is not really a national identifier."""
    stripped = re.sub(r"[ -]", "", candidate.value)
    return _luhn_ok(stripped) and not _is_national_identifier(stripped)


# Contact and payment patterns, as a rule table. The cue-led card pattern is
# listed before the bare one so the bare rule can recognise -- and skip -- a
# range the cue already claimed.
_CONTACT_RULES: tuple[Rule, ...] = (
    Rule(_EMAIL_RE, "EMAIL", guard=value_ok(_email_ok)),
    Rule(_URL_RE, "URL", trim=".,;:", guard=value_ok(_url_ok)),
    Rule(_URL_CONTEXT_RE, "URL", "value", trim=").,;:", guard=value_ok(_url_ok)),
    Rule(_CREDITCARD_CONTEXT_RE, "CREDITCARDNUMBER", "value", trim=" -"),
    Rule(
        _CREDITCARD_RE,
        "CREDITCARDNUMBER",
        trim=" -",
        guard=_card_ok,
        overlap=Overlap.DUPLICATE,
    ),
)

# Every identifier, postcode, house number, phone and date detector needs at
# least one digit, so a body without one cannot match any of them.
_DIGIT_BOUND_DETECTORS = (
    _identifier_spans,
    _postcode_spans,
    _building_number_spans,
    _phone_spans,
    _date_spans,
)

# Blood groups, a bare sex, an organisation name, e-mail and URLs are not
# digit-bound, so these always run.
_UNCONDITIONAL_DETECTORS = (
    _clinical_spans,
    _demographic_spans,
    _organization_name_spans,
)


def _detected_spans(text: str) -> list[MaskSpan]:
    """Run every detector that could match *text*, in table order.

    The digit scan is an optimisation, not a rule: the service accepts up to
    10 MiB and each digit-bound detector otherwise walks all of it, so one scan
    here replaces roughly twenty-five.
    """
    digit_bound = _DIGIT_BOUND_DETECTORS if _ANY_DIGIT_RE.search(text) else ()
    spans: list[MaskSpan] = []
    for detect in (*digit_bound, *_UNCONDITIONAL_DETECTORS):
        spans.extend(detect(text))
    spans.extend(collect(_CONTACT_RULES, text))
    return spans


def find_regex_spans(text: str) -> list[MaskSpan]:
    """Return all validated regex matches in *text*."""
    spans = _detected_spans(text)
    unique = {(span.label, span.start, span.end): span for span in spans}
    result = _to_output_spans(_arbitrate_overlaps(list(unique.values())))
    log.info(
        "regex_spans count=%d (raw=%d, arbitrated away %d cross-label overlaps)",
        len(result),
        len(spans),
        len(spans) - len(result),
    )
    return result


def find_regex_spans_pages(texts: list[str]) -> list[MaskSpan]:
    """Detect spans on each page and record the page index."""
    return [
        replace(span, page=page_index)
        for page_index, text in enumerate(texts)
        for span in find_regex_spans(text)
    ]
