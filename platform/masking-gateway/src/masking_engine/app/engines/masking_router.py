"""Replace each detected span with a pseudonym of its own kind.

Detection finds a span and names it. This router decides what replaces it.
Every canonical label takes the same algorithm, pseudonymization: a Belgian
Dutch alias for a name, a surrogate date for a date, a surrogate value from a
Dutch clinical vocabulary for a diagnosis, a medication, a procedure, or a
symptom, and a shape-preserving surrogate for anything else. No detected value
reaches the released document.

Every replacement is a pure function of the document bytes, the canonical
label, and the original text. Masking one document twice therefore returns the
same bytes, which is what lets NiFi replay an idempotency key. The document
bytes are part of every seed, so two files that name the same patient get
different pseudonyms and a reader cannot link one release to another.

A pseudonym reads like the value it replaced. A reader cannot tell which
diagnosis, date, or address a released document originally carried, and cannot
tell that the value is a surrogate either. Treat every masked document as
carrying no true clinical fact.

Replacements never reach a log line. Only labels, strategies, and counts do.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
from dataclasses import dataclass

from faker import Faker

from ..core import label_registry, locales
from ..core.locales import DEFAULT_LOCALE
from ..core.schemas import MaskSpan
from . import surrogates
from .surrogate_dates import parse_date
from .surrogate_pools import organization_pool_for
from .surrogate_text import _sanitize

log = logging.getLogger(__name__)


# Surrogate inputs that vary by language live in app/core/locales.py.


# A number carries its value in its digits. The separators around them are a
# writer's choice, so they stay out of the seed and two spellings of one number
# reach one surrogate. Every label masked by redrawing digits belongs here, or
# `12,5 mg` and `12.5 mg` would draw two different doses in one document.
_DIGIT_KEYED_LABELS = frozenset(
    {
        "AGE",
        "BTW_EENHEID",
        "INSZ_NISS",
        "NIR",
        "RIZIV_INAMI",
        "RPPS",
        "PHONE_NUMBER",
        # The English-language identifiers, here for the same reason: an NHS
        # number written "485 777 3456" in a header and "4857773456" in a
        # field is one number and has to reach one surrogate.
        "SSN",
        "NPI",
        "NHS_NUMBER",
        "GMC",
    }
)


_generators = threading.local()


def _faker_for(faker_locale: str) -> Faker:
    """Return this thread's generator for *faker_locale*, built once.

    Thread-local rather than process-wide. `_seeded_faker` reseeds the
    generator immediately before every draw, so one instance shared across
    threads could have a seed from one document land between another
    document's seed and its draw -- crossing surrogates between documents.
    One instance per thread makes that impossible without depending on a
    caller to hold a lock: the model service does serialise masking under
    `_lifecycle_lock`, but this is a library and cannot assume its callers do.

    Building one costs ~1 ms and was 12.75% of engine CPU when paid per
    document. Each is ~34 KB, so the eight gunicorn threads across the three
    deployment locales cap this at roughly 816 KB.
    """
    fakers = getattr(_generators, "fakers", None)
    if fakers is None:
        fakers = _generators.fakers = {}
    if faker_locale not in fakers:
        fakers[faker_locale] = Faker(faker_locale)
    return fakers[faker_locale]


@dataclass(frozen=True)
class Replacement:
    """Hold the text that replaces one detected span.

    Deliberately not memoised. `@cache` sat on this class -- interning
    instances by `(span, text)` -- and measurement showed it was retention
    rather than reuse: over 1500 corpus documents it took 36 hits against
    5634 misses, a 0.6% hit rate, while growing to 5634 entries that
    `functools.cache` never evicts. Spans carry document-specific offsets, so
    two documents almost never ask for the same one, and a service that stays
    up holds every replacement it has ever built. Nothing compares these by
    identity, so building a fresh one costs nothing that mattered.
    """

    span: MaskSpan
    text: str


class MaskingRouter:
    """Turn detected spans into their replacements for one document."""

    def __init__(
        self,
        document: bytes,
        audit: list[dict[str, object]] | None = None,
        *,
        organization_pool: str = "medical",
        locale: str = DEFAULT_LOCALE,
    ) -> None:
        digest = hashlib.sha256(document).digest()
        self._seed = int.from_bytes(digest[:8], "big")
        self._locale = locales.profile(locale)
        self.locale = locale
        self.organization_pool_kind = organization_pool.casefold()
        self._faker = _faker_for(self._locale.faker_locale)
        self._dates: dict[str, tuple | None] = {}
        self._pseudonyms: dict[tuple[str, str], str] = {}
        self._organization_pool = organization_pool_for(
            locale, self.organization_pool_kind
        )
        self.replaced = 0
        self._audit = audit

    def record(self, span: MaskSpan, original: str, replaced: str) -> None:
        """Record the final replacement applied to one source span."""
        if self._audit is None:
            return
        entry: dict[str, object] = {
            "label": span.label,
            "start": span.start,
            "end": span.end,
            "text": original,
            "replaced_text": replaced,
            "source_layer": span.source_layer or span.source,
        }
        if span.confidence is not None:
            entry["confidence"] = span.confidence
        if span.page is not None:
            entry["page"] = span.page
        self._audit.append(entry)

    def replacement(self, span: MaskSpan, original: str) -> Replacement:
        """Return the replacement for *span*, whose text is *original*."""
        # A native label such as PATIENT_NAME resolves here as well as in the
        # converter. The router writes the bytes, so it cannot assume a caller
        # already canonicalized what it hands over.
        label = label_registry.canonical_label(span.label)
        if label is None:
            label = span.label.upper()
            # The label alone, never the text it matched.
            log.info("mask_unmapped_label label=%s", label)
        text = self._cached_pseudonymize(label, original)
        self.replaced += 1
        return Replacement(span=span, text=_sanitize(text, label))

    def _cached_pseudonymize(self, label: str, original: str) -> str:
        """Return `_pseudonymize`'s result, reused for a repeated value.

        One document repeats its values -- an organisation in the header, the
        body and the footer, a date in a field and again in a sentence -- so
        the same (label, value) pair is pseudonymized more than once.

        The seed still comes from `_seed_key`, so two spellings of one number
        draw the same digits. The key carries `original` as well, because
        `_surrogate_phone` and `_surrogate_age` render in the source's format:
        with one entry per number, whichever spelling appeared first decided
        the punctuation for every other, so "61-jarige" could be replaced by
        the bare "65".
        """
        key = (label, self._seed_key(label, original), original)
        if key not in self._pseudonyms:
            self._pseudonyms[key] = self._pseudonymize(label, original)
        return self._pseudonyms[key]

    def _seed_key(self, label: str, original: str) -> str:
        """Return the value that decides the surrogate for *original*.

        Two mentions of one value have to reach one surrogate, however the
        document wrote them. The key therefore ignores spacing and case
        everywhere, the separators inside a number, and the format of a date.
        A phone number written `0475 12 34 56` in a header and `0475123456` in
        a field is one number, and both mentions get one replacement.
        """
        normalized = " ".join(original.split()).casefold()
        if label in _DIGIT_KEYED_LABELS:
            return re.sub(r"\D", "", normalized) or normalized
        if label == "DATE":
            parsed = self._date_of(normalized)
            if parsed is not None:
                return parsed[0].isoformat()
        return normalized

    def _seed_for(self, label: str, original: str) -> int:
        material = f"{self._seed}:{label}:{self._seed_key(label, original)}".encode()
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")

    def _seeded_random(self, label: str, original: str) -> random.Random:
        """Return a generator bound to this document, label, and value."""
        # A surrogate has to repeat for the same document and value, so the
        # generator is seeded rather than cryptographic.
        return random.Random(self._seed_for(label, original))

    def _seeded_faker(self, label: str, original: str) -> Faker:
        """Return the shared generator, seeded for this label and value.

        Seeding for each value keeps a pseudonym independent of how many spans
        came before it, so the same name maps to the same alias wherever it
        appears in the document.
        """
        self._faker.seed_instance(self._seed_for(label, original))
        return self._faker

    def _context(self, label: str, original: str) -> surrogates.SurrogateContext:
        """Hand a strategy exactly what it may depend on, and nothing more."""
        return surrogates.SurrogateContext(
            label=label,
            original=original,
            locale=self._locale,
            organization_pool=self._organization_pool,
            random=lambda: self._seeded_random(label, original),
            faker=lambda: self._seeded_faker(label, original),
            date_of=self._date_of,
        )

    def _pseudonymize(self, label: str, original: str) -> str:
        return surrogates.resolve(self._context(label, original))

    def _date_of(self, text: str):
        """Return the cached parse of *text*.

        A date is parsed for its seed and again for its replacement, and one
        document repeats its dates. Parsing walks seven formats before it gives
        up, so the result is kept.
        """
        stripped = text.strip()
        if stripped not in self._dates:
            self._dates[stripped] = parse_date(stripped, self._locale)
        return self._dates[stripped]
