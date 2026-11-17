from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import label_registry

_ROOT = Path(__file__).resolve().parents[3]

_PLACEHOLDER_SERVICE_TOKEN = (  # documented placeholder, not a secret
    "replace-with-a-long-random-secret"
)

# A build or deployment injects each immutable identifier. This value marks the
# ones a local run cannot know. `/version` reports it unchanged rather than
# claim an identity the service cannot prove.
UNKNOWN_IDENTIFIER = "unknown"

# PII Masking API contract v1.2 section 2: a moving label does not identify a
# release, so `load_settings` refuses each one where a release identifier
# belongs.
_MUTABLE_LABELS = frozenset(
    {"latest", "final", "champion", "production", "stable", "fused-xlmr-final"}
)


DEFAULT_ONNX_MODEL_DIR = _ROOT / "models" / "fused_xlmr_final" / "onnx"


@dataclass(frozen=True)
class Settings:
    app_name: str = "PII Masking Service"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # Report the deployed release identity. PII Masking API contract v1.2
    # sections 2 and 7 require every field on `/version` and on each masked
    # response, and require each value to name what this instance runs now.
    api_contract_version: str = "v1"
    service_name: str = "octo-pii-masker"
    service_release: str = UNKNOWN_IDENTIFIER
    git_sha: str = UNKNOWN_IDENTIFIER
    image_digest: str = UNKNOWN_IDENTIFIER
    model_name: str = "octo-pii-masker"

    # Use a shared bearer token. Do not log or commit it.
    service_token: str = ""

    # Configure model inference.
    onnx_model_path: str = str(DEFAULT_ONNX_MODEL_DIR)
    model_version: str = UNKNOWN_IDENTIFIER
    confidence_threshold: float = 0.3
    # Per-label overrides of confidence_threshold. A label absent here uses the
    # global default. Some labels want a different bar than the rest: e.g. a
    # label the model is measurably under-confident but accurate on (like
    # ORGANIZATION_NAME at 0.77 recall / 1.0 precision) can take a lower floor
    # without inheriting a lowered global default's cost on a label that is
    # already over-triggering (like AGE, whose precision is the opposite
    # problem and would only get worse under a lower bar).
    #
    # ORGANIZATION_NAME ships at 0.1: the benchmark misses on it were
    # under-confident (1.0 precision), the model emits the canonical label the
    # engine gate keys on, and no other label inherits the lower bar. An
    # ENTITY_CONFIDENCE_THRESHOLDS entry naming a label listed here replaces
    # its floor; every other entry adds one.
    entity_confidence_thresholds: dict[str, float] = field(
        default_factory=lambda: {"ORGANIZATION_NAME": 0.1}
    )
    # Refuse a model detection whose text is the wrong length to be the
    # national identifier it claims -- a nine-digit run read as an
    # eleven-digit RIZIV, a euro amount read as a fifteen-digit NIR. The
    # detector has no "not an entity" prediction for a digit run and picks the
    # class by length, so this is the one part of that guess a rule can check.
    # `MODEL_ID_SHAPE_GATE=off` restores the ungated behaviour for a release
    # that wants every model detection through as it stands.
    model_id_shape_gate: bool = True
    # Detection scores CSV cells and JSON values on their own and in context.
    # A lone field is far more likely to be an identifier than a word in prose,
    # so it carries a lower bar. Column headers score nothing either way.
    structured_field_threshold: float = 0.5
    accelerator: str = "auto"
    onnx_model_sha256: str = ""
    max_model_tokens: int = 510
    # Overlap between inference windows. An entity near a window edge scores
    # poorly without surrounding context, so a narrow overlap leaves periodic
    # gaps: on a 64 KiB document, 64 tokens leaked 32 of 1598 names and 128
    # leaked 16, while 192 leaked none. Windows cost about 40 percent more at
    # this overlap, which is worth it to close the gap.
    model_stride_tokens: int = 192

    # Limit request size.
    max_upload_size_bytes: int = 10 * 1024 * 1024

    # Bound concurrent masking work. Contract v1.2 section 6 starts the pilot at
    # one or two concurrent NiFi calls per team. A request that arrives while
    # every slot is busy gets 429 with Retry-After instead of a queue that grows
    # past the caller's socket-read timeout.
    max_concurrent_masks: int = 2

    # Cap the body the model will accept, matching the transport limit so the
    # service masks everything it accepts. Inference is slice-by-slice, so peak
    # memory follows the slice rather than the request: a full 10 MiB body
    # measured 2.4 GiB peak RSS on 16 CPU cores, and took 107 minutes at 0.628
    # seconds per KiB. Callers must set their read timeout from that rate.
    # Lower this when a deployment would rather refuse large bodies than wait.
    max_model_input_bytes: int = 10 * 1024 * 1024

    # Select model, model_regex, or regex detection.
    # The model_regex default follows the label ownership table.
    detection_mode: str = "regex"

    # Which team's label vocabulary the loaded detector emits. The profile
    # follows the model, not the caller, so it is read at startup rather than
    # from a request header. Profiles live in app/core/label_profiles.json.
    detector_label_profile: str = "octo"

    # Select the organisation-name surrogate pool used by redaction.
    organization_surrogate_pool: str = "medical"

    # Select the visual redaction style for PDF output. "invisible" (the
    # default) matches the original glyph's font/size/color exactly, so a
    # reader cannot tell a value was replaced. "clinical_soft" and
    # "bold_chips" instead draw a visible, entity-colored box (see
    # app/services/pdf_redaction_styles.py).
    pdf_redaction_style: str = "invisible"

    # Whether a visible box shows the entity-type abbreviation only
    # ("detect") or the fitted pseudonym ("pseudonymize"). Ignored when
    # pdf_redaction_style is "invisible".
    pdf_redaction_mode: str = "pseudonymize"

    # Let a replacement that wants more width than the value it covers take
    # it from the rest of its line, moving what follows to the right
    # (app/services/pdf_line_reflow.py). On by default, and for every style:
    # capped at whatever its neighbour left, a visible box on a two-digit slot
    # shrank "AGE 97%" until it truncated to "A...", and an invisible
    # pseudonym was condensed into the slot instead -- a name replaced by one
    # half again as long came out at two thirds width and read as crushed.
    # `PDF_CHIP_REFLOW=off` restores that older behaviour for a release that
    # wants the source layout untouched.
    pdf_chip_reflow: bool = True

    # Stamp the per-language detection scores on a masked PDF's first page.
    # On by default: a reviewer holding a masked document cannot otherwise
    # tell whether its surrogates are in the language the detector chose or
    # in the fallback's, and that is the first question asked of a document
    # masked in the wrong one. `PDF_LOCALE_BADGE=off` removes it for a
    # release where the overlay would be noise.
    pdf_locale_badge: bool = True

    # Read image-bearing PDF pages with OCR instead of refusing them. Off by
    # default: switching it on changes which documents the service accepts,
    # so it is a deliberate act per deployment rather than a silent widening.
    # With it off, an image on any page still rejects the whole document.
    pdf_ocr: bool = False

    # Target render density for an OCR'd image region. Bounded by
    # pdf_ocr_max_pixels, which is what actually protects memory -- a page box
    # is not required to bear any relation to the image drawn on it, and one
    # real sample declares 1861x2631pt for a 2481x3508px scan, where a fixed
    # 300 DPI would allocate 255 MB for a single page.
    pdf_ocr_dpi: int = 200

    # Hard cap on the long edge, in pixels, of anything handed to OCR.
    pdf_ocr_max_pixels: int = 2048

    # Ignore image blocks smaller than this on their longest side. A rule,
    # a bullet glyph and a letterhead icon are images too, and rasterising
    # each of them costs more than reading them could ever return.
    pdf_ocr_min_block_points: int = 16

    # Drop an OCR line scoring below this. Zero on purpose, and it should stay
    # zero: a dropped line is a line whose pixels stay on the page and whose
    # text never reaches the detector, which is a silent leak rather than a
    # quality trade. The knob exists to measure with, not to ship filtering.
    pdf_ocr_min_line_confidence: float = 0.0

    # Refuse a page whose text boxes are too slanted for axis-aligned
    # redaction, rather than placing the boxes slightly beside the words.
    # Deskew is an explicit non-goal; this is what keeps it a visible one.
    pdf_ocr_min_box_fill: float = 0.55

    # Above this, a page is refused rather than partly read. Truncating to
    # the best N would drop the least confident boxes, which are the hardest
    # to read and so the likeliest to be carrying something worth masking.
    pdf_ocr_max_lines: int = 400

    # Where the OCR artifacts live, and their digests. Empty digests skip the
    # check, matching onnx_model_sha256's contract.
    ocr_model_path: str = str(
        Path(__file__).resolve().parents[2] / "src" / "models" / "ocr" / "artifacts"
    )
    ocr_det_sha256: str = ""
    ocr_rec_sha256: str = ""
    ocr_charset_sha256: str = ""

    # Pinned rather than derived from os.cpu_count(). ONNX Runtime splits some
    # reductions across threads, so the count changes float accumulation order
    # and can flip a near-tie argmax -- one different character, and the
    # masked bytes differ. Byte-reproducibility is what lets NiFi replay an
    # idempotency key, so this may not vary with the host or its cgroup.
    ocr_intra_op_threads: int = 4

    supported_media_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "application/pdf",
                "text/plain",
                "text/csv",
                "application/json",
            }
        )
    )


Reader = Callable[[str, Any], Any]
"""Reads one environment variable into one `Settings` field, or raises."""


def _text(name: str, default: str) -> str:
    """Return the value of *name* unchanged."""
    return os.getenv(name, default)


def _ratio(name: str, default: float) -> float:
    """Return the value of *name* as a score from 0 to 1."""
    value = float(os.getenv(name, str(default)))
    if not 0 <= value <= 1:
        raise ValueError(f"Set {name} from 0 to 1.")
    return value


def _entity_confidence_thresholds(
    name: str, default: dict[str, float]
) -> dict[str, float]:
    """Return the per-label floors: the shipped ones, then *name*'s overrides.

    Format: ``LABEL:0.3,LABEL:0.7``. An entry naming a label that already ships
    a floor replaces it; every other entry adds one. A label named in neither
    falls back to the global confidence_threshold.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return dict(default)
    overrides: dict[str, float] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        label, _, value = entry.partition(":")
        label = label.strip().upper()
        if not label or not value:
            raise ValueError(f"{name} entries must be LABEL:VALUE, got {entry!r}.")
        score = float(value)
        if not 0 <= score <= 1:
            raise ValueError(f"{name} entry for {label} must be from 0 to 1.")
        if label not in label_registry.masked_labels():
            raise ValueError(f"{name} names an unknown label: {label}.")
        overrides[label] = score
    return default | overrides


def _choice(
    name: str, default: str, allowed: tuple[str, ...], *, ignore_case: bool = False
) -> str:
    """Return the value of *name*, which must be one of *allowed*."""
    value = os.getenv(name, default)
    if ignore_case:
        value = value.lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(allowed)}.")
    return value


def _one_of(*allowed: str, ignore_case: bool = False) -> Reader:
    """A reader that accepts only one of *allowed*."""
    return lambda name, default: _choice(
        name, default, allowed, ignore_case=ignore_case
    )


def _label_profile(name: str, default: str) -> str:
    """Return a profile name the label registry knows.

    Asked at load time rather than written into the table: the profiles come
    from a JSON file the registry owns, so the allowed set is only knowable
    once that file has been read.
    """
    return _choice(name, default, label_registry.profile_names())


def _flag(name: str, default: bool) -> bool:
    """Return an on/off toggle.

    `_choice` rather than loose truthiness: a typo must fail at startup, the
    way every other knob here does. Every boolean setting reads through this,
    so a new toggle is one row in `Settings` and one row in `_LOAD_ORDER` --
    there is no second spelling of "on" for it to drift away from.
    """
    return (
        _choice(name, "on" if default else "off", ("off", "on"), ignore_case=True)
        == "on"
    )


def _positive_int(name: str, default: int) -> int:
    """Return the value of *name* as a whole number above zero."""
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive.")
    return value


def _whole(name: str, default: int) -> int:
    """Return the value of *name* as a whole number; bounding it is a rule's job."""
    return int(os.getenv(name, str(default)))


def _service_token() -> str:
    """Return the shared bearer token, or refuse to start without a real one.

    The placeholder from ``.env.example`` counts as missing. A deployment that
    kept it would accept the token printed in the repository.
    """
    token = os.getenv("SERVICE_TOKEN", Settings.service_token)
    if not token or token == _PLACEHOLDER_SERVICE_TOKEN:
        raise ValueError("SERVICE_TOKEN must contain a runtime secret.")
    return token


def _digest(name: str, default: str = "") -> str:
    """Return the digest in *name*, or an empty string when unset.

    Empty means "do not check", which is what lets a developer run against a
    locally exported artifact. A malformed value is not the same thing and
    fails loudly, because a digest that never matches and a digest that is
    never compared look identical from outside.
    """
    digest = os.getenv(name, default).lower()
    if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest.")
    return digest


def _immutable(name: str, default: str) -> str:
    """Return the value of *name*, which must identify one exact release.

    A build or a deployment fills these in. An empty value counts as unset, so
    a `.env` copied from `.env.example` keeps the `unknown` default.
    """
    value = os.getenv(name, "").strip() or default
    if value.lower() in _MUTABLE_LABELS:
        raise ValueError(f"{name} must be an immutable identifier, not a moving label.")
    return value


def _immutable_digest(name: str, default: str) -> str:
    """Return the value of *name* as a ``sha256:`` digest, or ``unknown``."""
    value = _immutable(name, default).lower()
    if value == UNKNOWN_IDENTIFIER:
        return value
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be sha256 followed by 64 hexadecimal digits.")
    return value


def _commit_hash(name: str, default: str) -> str:
    """Return the source commit hash in *name*, or ``unknown`` outside a build."""
    value = _immutable(name, default).lower()
    if value == UNKNOWN_IDENTIFIER:
        return value
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError(f"{name} must be a 40-character commit hash.")
    return value


def _check_redaction(mode: str, style: str) -> None:
    """A detect-mode run has to leave something for a reviewer to see."""
    if mode == "detect" and style == "invisible":
        raise ValueError(
            "PDF_REDACTION_MODE=detect requires a visible PDF_REDACTION_STYLE."
        )


def _check_window(max_model_tokens: int, model_stride_tokens: int) -> None:
    """The overlap between inference windows has to stay below the window.

    An overlap as wide as the window would advance nothing and never reach the
    end of the document.
    """
    if max_model_tokens < 2:
        raise ValueError("MAX_MODEL_TOKENS must be at least 2.")
    if not 0 <= model_stride_tokens < max_model_tokens:
        raise ValueError("MODEL_STRIDE_TOKENS must be below MAX_MODEL_TOKENS.")


_OCR_ARTIFACTS = ("det/model.onnx", "rec/model.onnx", "rec/charset.txt")


def _check_ocr_artifacts(pdf_ocr: bool, ocr_model_path: str) -> None:
    """Fail here rather than on the first scanned page.

    A deployment that asked for OCR and cannot do it should not discover that
    from a 503 in the middle of a batch.
    """
    if not pdf_ocr:
        return
    missing = [
        name for name in _OCR_ARTIFACTS if not (Path(ocr_model_path) / name).is_file()
    ]
    if missing:
        raise ValueError(
            "PDF_OCR=on requires the OCR artifacts; missing under "
            f"{ocr_model_path}: {', '.join(missing)}"
        )


_FIELD_DEFAULTS: dict[str, Any] = {
    f.name: f.default_factory() if f.default is MISSING else f.default
    for f in fields(Settings)
}


@dataclass(frozen=True)
class _Setting:
    """One environment variable, and the `Settings` field it fills."""

    field: str
    env: str
    read: Reader

    def apply(self, values: dict[str, Any]) -> None:
        values[self.field] = self.read(self.env, _FIELD_DEFAULTS[self.field])


@dataclass(frozen=True)
class _Rule:
    """A constraint spanning settings, checked where its inputs are known."""

    check: Callable[..., None]
    reads: tuple[str, ...]

    def apply(self, values: dict[str, Any]) -> None:
        self.check(*(values[name] for name in self.reads))


# The whole of startup validation, in the order a deployment meets it: the
# first thing wrong with an environment is the error it gets. A rule sits at
# the point where every setting it reads has been read. Adding a setting is a
# field on `Settings` and a row here, and nothing else.
_LOAD_ORDER: tuple[_Setting | _Rule, ...] = (
    _Setting("confidence_threshold", "CONFIDENCE_THRESHOLD", _ratio),
    _Setting(
        "entity_confidence_thresholds",
        "ENTITY_CONFIDENCE_THRESHOLDS",
        _entity_confidence_thresholds,
    ),
    _Setting("model_id_shape_gate", "MODEL_ID_SHAPE_GATE", _flag),
    _Setting("structured_field_threshold", "STRUCTURED_FIELD_THRESHOLD", _ratio),
    _Setting(
        "accelerator", "ACCELERATOR", _one_of("auto", "cpu", "cuda", ignore_case=True)
    ),
    _Setting(
        "detection_mode", "DETECTION_MODE", _one_of("model", "model_regex", "regex")
    ),
    _Setting("detector_label_profile", "DETECTOR_LABEL_PROFILE", _label_profile),
    _Setting(
        "organization_surrogate_pool",
        "ORGANIZATION_SURROGATE_POOL",
        _one_of("general", "medical", ignore_case=True),
    ),
    _Setting("pdf_locale_badge", "PDF_LOCALE_BADGE", _flag),
    _Setting(
        "pdf_redaction_style",
        "PDF_REDACTION_STYLE",
        _one_of("invisible", "clinical_soft", "bold_chips", ignore_case=True),
    ),
    _Setting(
        "pdf_redaction_mode",
        "PDF_REDACTION_MODE",
        _one_of("pseudonymize", "detect", ignore_case=True),
    ),
    _Setting("pdf_chip_reflow", "PDF_CHIP_REFLOW", _flag),
    _Rule(_check_redaction, ("pdf_redaction_mode", "pdf_redaction_style")),
    _Setting("pdf_ocr", "PDF_OCR", _flag),
    _Setting("pdf_ocr_dpi", "PDF_OCR_DPI", _positive_int),
    _Setting("pdf_ocr_max_pixels", "PDF_OCR_MAX_PIXELS", _positive_int),
    _Setting("pdf_ocr_min_block_points", "PDF_OCR_MIN_BLOCK_POINTS", _positive_int),
    _Setting("pdf_ocr_max_lines", "PDF_OCR_MAX_LINES", _positive_int),
    _Setting("pdf_ocr_min_line_confidence", "PDF_OCR_MIN_LINE_CONFIDENCE", _ratio),
    _Setting("pdf_ocr_min_box_fill", "PDF_OCR_MIN_BOX_FILL", _ratio),
    _Setting("ocr_intra_op_threads", "OCR_INTRA_OP_THREADS", _positive_int),
    _Setting("ocr_model_path", "OCR_MODEL_PATH", _text),
    _Setting("ocr_det_sha256", "OCR_DET_SHA256", _digest),
    _Setting("ocr_rec_sha256", "OCR_REC_SHA256", _digest),
    _Setting("ocr_charset_sha256", "OCR_CHARSET_SHA256", _digest),
    _Rule(_check_ocr_artifacts, ("pdf_ocr", "ocr_model_path")),
    _Setting("max_model_tokens", "MAX_MODEL_TOKENS", _whole),
    _Setting("model_stride_tokens", "MODEL_STRIDE_TOKENS", _whole),
    _Rule(_check_window, ("max_model_tokens", "model_stride_tokens")),
    _Setting("max_upload_size_bytes", "MAX_UPLOAD_SIZE_BYTES", _positive_int),
    _Setting("max_model_input_bytes", "MAX_MODEL_INPUT_BYTES", _positive_int),
    _Setting("max_concurrent_masks", "MAX_CONCURRENT_MASKS", _positive_int),
    _Setting("onnx_model_path", "ONNX_MODEL_PATH", _text),
    _Setting("onnx_model_sha256", "ONNX_MODEL_SHA256", _digest),
    _Setting("app_name", "APP_NAME", _text),
    _Setting("app_version", "APP_VERSION", _text),
    _Setting("log_level", "LOG_LEVEL", _text),
    _Setting("service_name", "SERVICE_NAME", _text),
    _Setting("service_release", "SERVICE_RELEASE", _immutable),
    _Setting("git_sha", "GIT_SHA", _commit_hash),
    _Setting("image_digest", "IMAGE_DIGEST", _immutable_digest),
    _Setting("model_name", "MODEL_NAME", _text),
    _Setting("model_version", "MODEL_VERSION", _immutable),
)


def load_settings(*, require_service_token: bool = True) -> Settings:
    """Return the runtime settings, or raise on the first invalid value.

    Every setting is validated here rather than where it is used, so a bad
    deployment fails at startup instead of on the first request that reaches
    it. `_LOAD_ORDER` is that procedure; this function only runs it.
    """
    values: dict[str, Any] = {
        "service_token": _service_token() if require_service_token else ""
    }
    for step in _LOAD_ORDER:
        step.apply(values)
    return Settings(**values)


def unknown_release_fields(settings: Settings) -> list[str]:
    """Return the release identifiers this deployment cannot prove."""
    identity = {
        "service_release": settings.service_release,
        "git_sha": settings.git_sha,
        "image_digest": settings.image_digest,
        "model_version": settings.model_version,
    }
    return sorted(
        name for name, value in identity.items() if value == UNKNOWN_IDENTIFIER
    )
