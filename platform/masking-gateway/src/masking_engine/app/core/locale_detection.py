"""Resolve which language a document is written in, per document.

A pseudonym has to read like the document it replaced a value in, so the
surrogate locale is a property of the document, not of the deployment. One
instance therefore masks a Dutch note with Flemish surrogates and a French
note with French ones, without being told which it is holding.

Detection loads Dutch, French, English, Spanish, German and Romanian. Each
language maps to a local surrogate profile, so a recognised document does not
fall back to an unrelated language.

**Determinism.** Masking has to be byte-reproducible for NiFi to replay an
idempotency key, so nothing here may vary between two runs on one input.
`lingua` is deterministic for a fixed version and the version is pinned
exactly in `pyproject.toml`; the detector is built once per process and the
text sample is a fixed prefix. Changing the pinned version can change which
locale a borderline document resolves to, and therefore its masked bytes --
treat a `lingua` upgrade as a change to masking output, not a routine bump.

**Two guards, because a wrong answer is worse than no answer.** A
misdetection changes every surrogate in the document, while the fallback
only keeps the previous behaviour. Text with too few letters to carry any
signal is not put to the detector at all, and a detection the detector is
not confident about is discarded. Both thresholds are deliberately blunt:
a real clinical note scores above 0.99, and the values that need rejecting
are short identifier stubs scoring in the 0.85-0.91 range.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

from lingua import Language, LanguageDetectorBuilder

from .locales import DEFAULT_LOCALE

log = logging.getLogger(__name__)

# Every language the detector considers, mapped to the locale it resolves to
# -- or to `None` for a language this engine recognises but has no profile
# for, which still keeps it out of the other three's confusion matrix.
# Adding a *locale* (as opposed to a recognised-and-rejected language) means
# adding its entry here as well as a `LocaleProfile` in `core/locales.py`, or
# `resolve_locale` has somewhere to send a document that `MaskingRouter` then
# refuses.
_LANGUAGE_LOCALES: dict[Language, str | None] = {
    Language.DUTCH: "nl_BE",
    Language.FRENCH: "fr_FR",
    # One English rather than a British and an American one. `lingua`
    # exposes a single ENGLISH, so nothing here could choose between two
    # dialects even if the profile table carried both, and the population
    # does not divide along that line either. See
    # `docs/decisions/004-english-locale.md`.
    Language.ENGLISH: "en",
    Language.SPANISH: "es_ES",
    Language.GERMAN: "de_DE",
    Language.ROMANIAN: "ro_RO",
}

# A fixed prefix rather than the whole document: a hundred pages carry no
# more language signal than the first few paragraphs, and a bounded sample
# keeps detection off the critical path for a large PDF. Fixed, not sampled,
# so two runs on one document read the same characters.
SAMPLE_LIMIT = 2000

# Below this many letters there is no signal to detect, whatever the
# detector reports. A header and an identifier ("NIR : 219078519105027")
# scores 0.906 on nothing but punctuation and digits.
MIN_LETTERS = 40

# A real note scores above 0.99. This rejects a coin flip without rejecting
# a genuinely mixed-language document.
MIN_CONFIDENCE = 0.70

_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

# How a document's locale was arrived at. Kept apart from the locale itself
# because the two answer different questions: `nl_BE` says how to write a
# surrogate, while "fallback" says nobody actually recognised the language --
# which is the first thing asked of a document masked in the wrong one.
LocaleSource = Literal["pinned", "detected", "fallback"]


@dataclass(frozen=True)
class LocaleVerdict:
    """One document's language, and whether anything recognised it."""

    locale: str
    source: LocaleSource

    @property
    def detected(self) -> bool:
        """True only when the detector chose this locale."""
        return self.source == "detected"


def _sample(text: str) -> tuple[str, int]:
    """Return the fixed prefix put to the detector and its letter count.

    One place decides what "the text" is, so `confidence_report` and
    `resolve_locale` cannot drift into scoring different characters.
    """
    sample = text[:SAMPLE_LIMIT]
    return sample, len(_LETTERS.findall(sample))


@cache
def _detector():
    """Return the process-wide detector, built on first use.

    Building is free; the language models load on the first detection and
    stay loaded, so a service that never masks never pays for them.
    """
    return LanguageDetectorBuilder.from_languages(*_LANGUAGE_LOCALES).build()


def confidence_report(text: str) -> tuple[tuple[str, float, str | None], ...]:
    """Return every candidate language, best first, as (name, score, locale).

    Exposed for diagnostics: `PDF_LOCALE_BADGE` stamps this onto a masked PDF
    so a reviewer can see why a document was masked in the language it was.
    `locale` is None for a loaded language this engine has no profile for --
    Spanish, German and Romanian today -- which is what makes such a
    fallback readable rather than mysterious.

    Never raises. Text with too little signal returns an empty tuple, the
    same input `detect_locale` refuses to put to the detector.
    """
    sample, letters = _sample(text)
    if letters < MIN_LETTERS:
        return ()
    return tuple(
        (value.language.name, value.value, _LANGUAGE_LOCALES.get(value.language))
        for value in _detector().compute_language_confidence_values(sample)
    )


def resolve_locale(
    text: str, *, pinned: str | None = None, default: str = DEFAULT_LOCALE
) -> LocaleVerdict:
    """Decide one document's locale once, and say where the answer came from.

    *pinned* short-circuits detection, which is what a caller supplying
    `X-Document-Locale` asks for. Everything else runs the two guards and
    falls back to *default*.

    This is the only place the verdict is made. A caller that needs the
    locale before masking passes the result on rather than detecting again:
    two detections on one document cost twice and could disagree.
    """
    if pinned is not None:
        return LocaleVerdict(pinned, "pinned")

    sample, letters = _sample(text)
    if letters < MIN_LETTERS:
        log.info("locale_detection_skipped letters=%d locale=%s", letters, default)
        return LocaleVerdict(default, "fallback")

    best = _detector().compute_language_confidence_values(sample)[0]
    locale = _LANGUAGE_LOCALES.get(best.language)
    if locale is None or best.value < MIN_CONFIDENCE:
        log.info(
            "locale_detection_rejected language=%s confidence=%.3f locale=%s",
            best.language.name,
            best.value,
            default,
        )
        return LocaleVerdict(default, "fallback")

    # The text itself never reaches a log line, only the verdict.
    log.info(
        "locale_detection language=%s confidence=%.3f locale=%s",
        best.language.name,
        best.value,
        locale,
    )
    return LocaleVerdict(locale, "detected")


def detect_locale(text: str, *, default: str = DEFAULT_LOCALE) -> str:
    """Return the locale *text* is written in, or *default* when unclear.

    Never raises and never returns a locale this engine has no profile for:
    a caller can pass the result straight to `MaskingRouter`. Prefer
    `resolve_locale` in new code -- it also says whether anything recognised
    the language, which this signature cannot express.
    """
    return resolve_locale(text, default=default).locale
