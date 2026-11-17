"""Berts int8 ONNX runtime: one checkpoint per document language.

Berts detects with a language-specific encoder rather than one multilingual
one. A Dutch clinical note is read by MedRoBERTa, a French one by
CamemBERT-bio, an English one by a BERT fine-tuned on i2b2 de-identification.
All three were fine-tuned on the same ten entities -- six identity fields
plus DISEASE/MEDICATION/PROCEDURE/SYMPTOM, see ADR 006 -- and quantized to
int8, so they are interchangeable to everything downstream: identical BIO
tag sets, identical label profile, one runtime.

**The language is decided once, for the whole document, by the platform.**
The model service resolves it before detection (`masking_engine.
document_verdict`) and passes it in the detection metadata. This module never
detects a language itself, for two reasons: `detect` is called once per
extracted text, and a single page rarely carries the 40 letters `lingua`
needs, so a per-text verdict would flap between pages of one letter; and the
surrogates are already written in that same resolved locale, so deriving it
twice invites the model and the pseudonyms to disagree.

An unknown or unsupported locale falls back to Dutch -- the same fallback the
surrogates take -- so a document too short to judge is masked exactly as it
was before this module held three checkpoints.

**Memory.** The checkpoints load lazily and separately: a deployment that
only ever sees Dutch never pays for the other two, and `unload` drops all of
them together when the service goes idle. Three resident int8 encoders are
about 350 MB, which is the number the capacity review needs, not the number a
typical instance holds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from common import http
from masking_engine import (
    LabelConverter,
    MaskSpan,
    OnnxTokenClassifierEngine,
    find_regex_spans,
)

from .spans import (
    cover_whole_numbers,
    drop_impossible_shapes,
    merge_drop_all,
    trim_title_prefix,
)

SUPPORTED_MEDIA_TYPES = frozenset({"application/pdf", "text/plain"})
SUPPORTED_MODEL_FORMATS = frozenset({"onnx"})
DEFAULT_MODEL_FORMAT = "onnx"

# The locale a document falls back to when detection could not name one, and
# the checkpoint that answers `GET /version`, which has no document at all.
DEFAULT_LOCALE = "nl_BE"


@dataclass(frozen=True)
class Checkpoint:
    """One language's approved int8 artifact, pinned by digest."""

    locale: str
    directory: str
    path_env: str
    sha_env: str
    model_name: str
    revision: str
    sha256: str


# Keyed by the locale `masking_engine.app.core.locales` resolves to, so a new
# language is one row here plus its artifact directory. `path_env` exists
# because production stages approved artifacts outside the image and mounts
# them; the packaged directory is the default, never the only option, and
# `sha_env` lets an operator pin what that mount is allowed to contain.
#
# The name and the revision have no env override, unlike the single-model
# runtimes: with three artifacts one MODEL_NAME could only rename all of
# them at once, and the point of reporting a name per language is that it
# matches the file that actually answered.
CHECKPOINTS: dict[str, Checkpoint] = {
    "nl_BE": Checkpoint(
        locale="nl_BE",
        directory="onnx",
        path_env="ONNX_MODEL_PATH",
        sha_env="ONNX_MODEL_SHA256",
        model_name="berts-pii-medroberta-nl-int8",
        revision="07f18eb53e127ed34aa77a64e1e15f24ea622be8",
        sha256="e6ebd6471a5fd5b8d8a5e044ba9d3aa4395ec96a35c0f4c8feb803341cb8e292",
    ),
    "en": Checkpoint(
        locale="en",
        directory="onnx-en",
        path_env="ONNX_MODEL_PATH_EN",
        sha_env="ONNX_MODEL_SHA256_EN",
        model_name="berts-pii-bert-en-int8",
        revision="45f397a28f3efc462ec89552a7b8de574f3f61dc",
        sha256="c317162cbc72aee06a4ffd8a903c7b645759da6e0ee2e4b90905bcd75b35f8cd",
    ),
    "fr_FR": Checkpoint(
        locale="fr_FR",
        directory="onnx-fr",
        path_env="ONNX_MODEL_PATH_FR",
        sha_env="ONNX_MODEL_SHA256_FR",
        model_name="berts-pii-camembert-bio-fr-int8",
        revision="162c057100bb174252303bb350df4c47296ea5bd",
        sha256="2b1af7af5f455b5f623b13d0669c8085e7dd0aa20dd0156bd6d28e4cee4d12c4",
    ),
}


def language_routing() -> bool:
    """Report whether a document's language selects its checkpoint.

    `BERTS_LANGUAGE_ROUTING=off` sends every document to the default
    checkpoint, which is exactly the single-model behaviour this team shipped
    before the English and French artifacts existed. It is the rollback: a
    checkpoint that turns out to mask badly is backed out by an operator
    restarting one service, without waiting for a release to remove it from
    the table.

    Read per call rather than at import so a restart is all it takes, and so
    a test can set it without reloading the module.

    A typo raises rather than silently leaving routing on: an operator
    reaching for this switch is backing a bad checkpoint out of production,
    and "BERTS_LANGUAGE_ROUTING=of" quietly doing nothing is the one outcome
    that must not happen.
    """
    value = os.getenv("BERTS_LANGUAGE_ROUTING", "on").strip().lower()
    if value not in ("on", "off"):
        raise ValueError("BERTS_LANGUAGE_ROUTING must be one of: off, on.")
    return value == "on"


def checkpoint_for(locale: str | None) -> Checkpoint:
    """Return the checkpoint that reads a document written in *locale*.

    An unknown locale is not an error. The platform validates the header it
    accepts, and a detected locale is always one this repository knows, so
    the only way here is a language with no Berts model yet -- which the
    Dutch checkpoint handles no worse than refusing the document would.

    With routing off every locale lands on the default, so a deployment that
    staged one checkpoint never opens a file it does not have.
    """
    if not language_routing():
        return CHECKPOINTS[DEFAULT_LOCALE]
    return CHECKPOINTS.get(locale or "", CHECKPOINTS[DEFAULT_LOCALE])


def supported_locales() -> frozenset[str] | None:
    """Return the locales this team holds a checkpoint for, or None.

    With routing on these are exactly the table's keys. A document in any
    other language -- the platform's Spanish, German and Romanian among them,
    which it names and writes surrogates in -- is read by the Dutch
    checkpoint, so a caller that *pins* one of those is refused rather than
    answered by a model that cannot read it.

    With routing off the default checkpoint answers every language, which is
    the documented rollback, so there is no restriction to declare.
    """
    if not language_routing():
        return None
    return frozenset(CHECKPOINTS)


def _model_path(checkpoint: Checkpoint) -> Path:
    override = os.getenv(checkpoint.path_env, "")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / checkpoint.directory


def _expected_sha256(checkpoint: Checkpoint) -> str:
    """Return the digest the artifact on disk has to match.

    The approved digest is in the table above, reviewed with the code. An
    operator staging a rebuilt artifact overrides it per language, which is
    a deliberate fail-closed control rather than a way to skip the check:
    setting it to anything the file does not hash to refuses to load.
    """
    return os.getenv(checkpoint.sha_env, "") or checkpoint.sha256


def _model_format(metadata: dict[str, str]) -> str:
    try:
        return http.model_format(
            metadata.get("model_format", ""), allowed=SUPPORTED_MODEL_FORMATS
        )
    except ValueError as exc:
        raise ValueError("Berts supports only the onnx model format") from exc


@cache
def _engine(locale: str) -> OnnxTokenClassifierEngine:
    """Return the session for *locale*, loading it on first use.

    Cached per locale rather than per process: an instance that only ever
    sees Dutch documents never loads the other two graphs.
    """
    checkpoint = CHECKPOINTS[locale]
    return OnnxTokenClassifierEngine(
        model_path=str(_model_path(checkpoint)),
        model_version=checkpoint.revision,
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.3")),
        accelerator="cpu",
        model_sha256=_expected_sha256(checkpoint),
    )


def detect(text: str, metadata: dict[str, str]) -> list[MaskSpan]:
    """Return Berts-owned model/regex detections in the QA taxonomy.

    The structured detector is the platform's, shared with every other team
    (see `spans.py` for why this team stopped carrying its own). It is
    locale-blind on purpose: the document's language picks the checkpoint
    that reads the prose, not which identifiers are worth finding.

    The model's labels are converted before the merge, not after: both span
    lists have to be in one vocabulary for the policy in `spans.py` to
    compare them at all -- and the shape gate reads canonical labels too, so
    it runs on the converted list rather than on the model's own.
    """
    if metadata.get("content_type") not in SUPPORTED_MEDIA_TYPES:
        raise ValueError("unsupported content type")
    _model_format(metadata)
    locale = checkpoint_for(metadata.get("document_locale")).locale
    model = LabelConverter("berts").convert_all(
        _engine(locale).infer(text), {None: text}
    )
    spans = merge_drop_all(drop_impossible_shapes(model, text), find_regex_spans(text))
    return trim_title_prefix(cover_whole_numbers(spans, text), text)


def ready(model_format: str | None = None) -> bool:
    """Report whether a checkpoint is loaded and able to answer.

    Any one of them counts. The others load when a document in their language
    arrives, and a service that has never seen a French document is not
    unready -- it is merely holding less than its ceiling.
    """
    return model_format in (None, "onnx") and _engine.cache_info().currsize > 0


def unload() -> None:
    """Drop every loaded checkpoint. Idle unloading owns this."""
    _engine.cache_clear()


def release(
    model_format: str = DEFAULT_MODEL_FORMAT, *, locale: str | None = None
) -> tuple[str, str, str]:
    """Return the identity of the checkpoint that read a *locale* document.

    A response has to attribute the bytes to the model that produced them:
    the masked object is written under `model=<name>/version=<version>/`, and
    a bundle name there would leave no way to tell which of three artifacts a
    given output came from, or which one to roll back. Called with no locale
    -- `GET /version`, which has no document -- it reports the default.
    """
    if model_format not in SUPPORTED_MODEL_FORMATS:
        raise ValueError("Berts supports only the onnx model format")
    checkpoint = checkpoint_for(locale)
    return (checkpoint.model_name, checkpoint.revision, "sha256:" + checkpoint.sha256)


def supported_media_types() -> frozenset[str]:
    return SUPPORTED_MEDIA_TYPES


def supported_model_formats() -> frozenset[str]:
    return SUPPORTED_MODEL_FORMATS
