#!/usr/bin/env python3
"""Pin every runtime setting: what it accepts, what it refuses, and in what order.

`load_settings` is the only place a deployment's environment is checked, and
a setting that silently keeps its default is indistinguishable from one that
was never wired up. So each row below states the variable, a value that is not
the default, and the field it has to land in -- and `test_every_setting_is_covered`
refuses to pass while a row in `config._LOAD_ORDER` has no row here.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.core import config
from masking_engine.app.core.config import Settings, load_settings

HEX64 = "0123456789abcdef" * 4
SHA40 = "0123456789abcdef" * 2 + "01234567"


def _ocr_artifacts(*present: str, base: Path | None = None) -> str:
    """A directory holding exactly *present*.

    Given a *base* -- a test's `tmp_path` -- pytest owns the directory and
    removes it after the run. Without one it is cleaned up at process exit
    instead, which is all the module-level tables below can ask for: they are
    built at import time, before any fixture exists.
    """
    if base is None:
        directory = Path(tempfile.mkdtemp(prefix="masking-ocr-"))
        atexit.register(shutil.rmtree, directory, True)
    else:
        directory = base
        directory.mkdir(parents=True, exist_ok=True)
    for name in present:
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(b"")
    return str(directory)


# Named here rather than imported from config: this is the list the loader has
# to keep asking for, so it may not move when config's own list does.
OCR_ARTIFACTS = ("det/model.onnx", "rec/model.onnx", "rec/charset.txt")
COMPLETE_OCR_DIR = _ocr_artifacts(*OCR_ARTIFACTS)

# Every variable the loader reads. Named here so a test presents exactly the
# environment it means to; `test_every_setting_is_covered` keeps it honest.
READ_BY_LOADER = frozenset(
    {
        "SERVICE_TOKEN",
        "CONFIDENCE_THRESHOLD",
        "ENTITY_CONFIDENCE_THRESHOLDS",
        "MODEL_ID_SHAPE_GATE",
        "STRUCTURED_FIELD_THRESHOLD",
        "ACCELERATOR",
        "DETECTION_MODE",
        "DETECTOR_LABEL_PROFILE",
        "ORGANIZATION_SURROGATE_POOL",
        "PDF_LOCALE_BADGE",
        "PDF_REDACTION_STYLE",
        "PDF_REDACTION_MODE",
        "PDF_CHIP_REFLOW",
        "PDF_OCR",
        "PDF_OCR_DPI",
        "PDF_OCR_MAX_PIXELS",
        "PDF_OCR_MIN_BLOCK_POINTS",
        "PDF_OCR_MAX_LINES",
        "PDF_OCR_MIN_LINE_CONFIDENCE",
        "PDF_OCR_MIN_BOX_FILL",
        "OCR_INTRA_OP_THREADS",
        "OCR_MODEL_PATH",
        "OCR_DET_SHA256",
        "OCR_REC_SHA256",
        "OCR_CHARSET_SHA256",
        "MAX_MODEL_TOKENS",
        "MODEL_STRIDE_TOKENS",
        "MAX_UPLOAD_SIZE_BYTES",
        "MAX_MODEL_INPUT_BYTES",
        "MAX_CONCURRENT_MASKS",
        "ONNX_MODEL_SHA256",
        "ONNX_MODEL_PATH",
        "APP_NAME",
        "APP_VERSION",
        "LOG_LEVEL",
        "SERVICE_NAME",
        "SERVICE_RELEASE",
        "GIT_SHA",
        "IMAGE_DIGEST",
        "MODEL_NAME",
        "MODEL_VERSION",
    }
)


def isolated(environ: dict[str, str]):
    """Present exactly *environ* to the loader, leaving the rest of the process alone.

    Unsetting the variables the loader reads rather than emptying `os.environ`:
    a cleared environment also takes away whatever the surrounding tool put
    there, and a run under a coverage or mutation harness then measures the
    harness rather than the code.
    """
    patch = mock.patch.dict(os.environ, environ)
    patch.start()
    for name in READ_BY_LOADER - set(environ):
        os.environ.pop(name, None)
    return patch


def settings(**environ: str) -> Settings:
    """Load settings from exactly *environ* and nothing the run happens to export."""
    patch = isolated(environ)
    try:
        return load_settings(require_service_token=False)
    finally:
        patch.stop()


def refused(**environ: str) -> str:
    """Return the message `load_settings` refuses *environ* with."""
    try:
        settings(**environ)
    except ValueError as error:
        return str(error)
    raise AssertionError(f"accepted {environ!r}")


# (environment, field, value that field must take). Every value differs from
# the shipped default, so a row passing means the variable was actually read.
ACCEPTED: tuple[tuple[dict[str, str], str, object], ...] = (
    ({"CONFIDENCE_THRESHOLD": "0.75"}, "confidence_threshold", 0.75),
    (
        {"ENTITY_CONFIDENCE_THRESHOLDS": "AGE:0.9"},
        "entity_confidence_thresholds",
        {"ORGANIZATION_NAME": 0.1, "AGE": 0.9},
    ),
    ({"MODEL_ID_SHAPE_GATE": "off"}, "model_id_shape_gate", False),
    ({"STRUCTURED_FIELD_THRESHOLD": "0.25"}, "structured_field_threshold", 0.25),
    ({"ACCELERATOR": "cuda"}, "accelerator", "cuda"),
    ({"DETECTION_MODE": "model_regex"}, "detection_mode", "model_regex"),
    ({"DETECTOR_LABEL_PROFILE": "berts"}, "detector_label_profile", "berts"),
    (
        {"ORGANIZATION_SURROGATE_POOL": "general"},
        "organization_surrogate_pool",
        "general",
    ),
    ({"PDF_LOCALE_BADGE": "off"}, "pdf_locale_badge", False),
    ({"PDF_REDACTION_STYLE": "bold_chips"}, "pdf_redaction_style", "bold_chips"),
    (
        {"PDF_REDACTION_MODE": "detect", "PDF_REDACTION_STYLE": "clinical_soft"},
        "pdf_redaction_mode",
        "detect",
    ),
    ({"PDF_CHIP_REFLOW": "off"}, "pdf_chip_reflow", False),
    ({"PDF_OCR": "on", "OCR_MODEL_PATH": COMPLETE_OCR_DIR}, "pdf_ocr", True),
    ({"PDF_OCR_DPI": "150"}, "pdf_ocr_dpi", 150),
    ({"PDF_OCR_MAX_PIXELS": "1024"}, "pdf_ocr_max_pixels", 1024),
    ({"PDF_OCR_MIN_BLOCK_POINTS": "8"}, "pdf_ocr_min_block_points", 8),
    ({"PDF_OCR_MAX_LINES": "50"}, "pdf_ocr_max_lines", 50),
    ({"PDF_OCR_MIN_LINE_CONFIDENCE": "0.4"}, "pdf_ocr_min_line_confidence", 0.4),
    ({"PDF_OCR_MIN_BOX_FILL": "0.9"}, "pdf_ocr_min_box_fill", 0.9),
    ({"OCR_INTRA_OP_THREADS": "2"}, "ocr_intra_op_threads", 2),
    ({"OCR_MODEL_PATH": "/artifacts/ocr"}, "ocr_model_path", "/artifacts/ocr"),
    ({"OCR_DET_SHA256": HEX64}, "ocr_det_sha256", HEX64),
    ({"OCR_REC_SHA256": HEX64}, "ocr_rec_sha256", HEX64),
    ({"OCR_CHARSET_SHA256": HEX64}, "ocr_charset_sha256", HEX64),
    ({"MAX_MODEL_TOKENS": "256"}, "max_model_tokens", 256),
    ({"MODEL_STRIDE_TOKENS": "64"}, "model_stride_tokens", 64),
    ({"MAX_UPLOAD_SIZE_BYTES": "4096"}, "max_upload_size_bytes", 4096),
    ({"MAX_MODEL_INPUT_BYTES": "8192"}, "max_model_input_bytes", 8192),
    ({"MAX_CONCURRENT_MASKS": "5"}, "max_concurrent_masks", 5),
    ({"ONNX_MODEL_SHA256": HEX64}, "onnx_model_sha256", HEX64),
    ({"APP_NAME": "Another Service"}, "app_name", "Another Service"),
    ({"APP_VERSION": "2.5.0"}, "app_version", "2.5.0"),
    ({"LOG_LEVEL": "DEBUG"}, "log_level", "DEBUG"),
    ({"SERVICE_NAME": "berts-pii-masker"}, "service_name", "berts-pii-masker"),
    ({"SERVICE_RELEASE": "2026.09.1"}, "service_release", "2026.09.1"),
    ({"GIT_SHA": SHA40}, "git_sha", SHA40),
    ({"IMAGE_DIGEST": f"sha256:{HEX64}"}, "image_digest", f"sha256:{HEX64}"),
    ({"MODEL_NAME": "medroberta"}, "model_name", "medroberta"),
    ({"MODEL_VERSION": "v7"}, "model_version", "v7"),
    ({"ONNX_MODEL_PATH": "/artifacts/onnx"}, "onnx_model_path", "/artifacts/onnx"),
)

# The values a deployment must be refused for, and the exact refusal. Exact,
# not a fragment: a message that merely *contains* the words is a message this
# suite cannot tell from a corrupted one.
REFUSED: tuple[tuple[dict[str, str], str], ...] = (
    ({"CONFIDENCE_THRESHOLD": "1.01"}, "Set CONFIDENCE_THRESHOLD from 0 to 1."),
    ({"CONFIDENCE_THRESHOLD": "-0.01"}, "Set CONFIDENCE_THRESHOLD from 0 to 1."),
    (
        {"STRUCTURED_FIELD_THRESHOLD": "2"},
        "Set STRUCTURED_FIELD_THRESHOLD from 0 to 1.",
    ),
    (
        {"PDF_OCR_MIN_LINE_CONFIDENCE": "1.5"},
        "Set PDF_OCR_MIN_LINE_CONFIDENCE from 0 to 1.",
    ),
    ({"PDF_OCR_MIN_BOX_FILL": "-1"}, "Set PDF_OCR_MIN_BOX_FILL from 0 to 1."),
    (
        {"ENTITY_CONFIDENCE_THRESHOLDS": "AGE"},
        "ENTITY_CONFIDENCE_THRESHOLDS entries must be LABEL:VALUE, got 'AGE'.",
    ),
    (
        {"ENTITY_CONFIDENCE_THRESHOLDS": ":0.5"},
        "ENTITY_CONFIDENCE_THRESHOLDS entries must be LABEL:VALUE, got ':0.5'.",
    ),
    (
        {"ENTITY_CONFIDENCE_THRESHOLDS": "AGE:2"},
        "ENTITY_CONFIDENCE_THRESHOLDS entry for AGE must be from 0 to 1.",
    ),
    (
        {"ENTITY_CONFIDENCE_THRESHOLDS": "NOPE:0.5"},
        "ENTITY_CONFIDENCE_THRESHOLDS names an unknown label: NOPE.",
    ),
    ({"MODEL_ID_SHAPE_GATE": "true"}, "MODEL_ID_SHAPE_GATE must be one of: off, on."),
    ({"PDF_LOCALE_BADGE": "1"}, "PDF_LOCALE_BADGE must be one of: off, on."),
    ({"PDF_CHIP_REFLOW": "yes"}, "PDF_CHIP_REFLOW must be one of: off, on."),
    ({"PDF_OCR": "maybe"}, "PDF_OCR must be one of: off, on."),
    ({"ACCELERATOR": "tpu"}, "ACCELERATOR must be one of: auto, cpu, cuda."),
    (
        {"DETECTION_MODE": "none"},
        "DETECTION_MODE must be one of: model, model_regex, regex.",
    ),
    (
        {"DETECTION_MODE": "REGEX"},
        "DETECTION_MODE must be one of: model, model_regex, regex.",
    ),
    (
        {"DETECTOR_LABEL_PROFILE": "nobody"},
        "DETECTOR_LABEL_PROFILE must be one of: berts, mdeberta, octo, synthify.",
    ),
    (
        {"ORGANIZATION_SURROGATE_POOL": "legal"},
        "ORGANIZATION_SURROGATE_POOL must be one of: general, medical.",
    ),
    (
        {"PDF_REDACTION_STYLE": "outline"},
        "PDF_REDACTION_STYLE must be one of: invisible, clinical_soft, bold_chips.",
    ),
    (
        {"PDF_REDACTION_MODE": "hide"},
        "PDF_REDACTION_MODE must be one of: pseudonymize, detect.",
    ),
    ({"PDF_OCR_DPI": "0"}, "PDF_OCR_DPI must be positive."),
    ({"PDF_OCR_MAX_PIXELS": "0"}, "PDF_OCR_MAX_PIXELS must be positive."),
    ({"PDF_OCR_MIN_BLOCK_POINTS": "-1"}, "PDF_OCR_MIN_BLOCK_POINTS must be positive."),
    ({"PDF_OCR_MAX_LINES": "0"}, "PDF_OCR_MAX_LINES must be positive."),
    ({"OCR_INTRA_OP_THREADS": "0"}, "OCR_INTRA_OP_THREADS must be positive."),
    ({"MAX_UPLOAD_SIZE_BYTES": "0"}, "MAX_UPLOAD_SIZE_BYTES must be positive."),
    ({"MAX_MODEL_INPUT_BYTES": "0"}, "MAX_MODEL_INPUT_BYTES must be positive."),
    ({"MAX_CONCURRENT_MASKS": "0"}, "MAX_CONCURRENT_MASKS must be positive."),
    (
        {"OCR_DET_SHA256": "abc"},
        "OCR_DET_SHA256 must be a 64-character hexadecimal digest.",
    ),
    (
        {"OCR_REC_SHA256": HEX64[:63]},
        "OCR_REC_SHA256 must be a 64-character hexadecimal digest.",
    ),
    (
        {"OCR_CHARSET_SHA256": "g" * 64},
        "OCR_CHARSET_SHA256 must be a 64-character hexadecimal digest.",
    ),
    (
        {"ONNX_MODEL_SHA256": "zz"},
        "ONNX_MODEL_SHA256 must be a 64-character hexadecimal digest.",
    ),
    ({"MAX_MODEL_TOKENS": "1"}, "MAX_MODEL_TOKENS must be at least 2."),
    (
        {"MAX_MODEL_TOKENS": "64", "MODEL_STRIDE_TOKENS": "64"},
        "MODEL_STRIDE_TOKENS must be below MAX_MODEL_TOKENS.",
    ),
    (
        {"MODEL_STRIDE_TOKENS": "-1"},
        "MODEL_STRIDE_TOKENS must be below MAX_MODEL_TOKENS.",
    ),
    (
        {"PDF_REDACTION_MODE": "detect"},
        "PDF_REDACTION_MODE=detect requires a visible PDF_REDACTION_STYLE.",
    ),
    (
        {"SERVICE_RELEASE": "latest"},
        "SERVICE_RELEASE must be an immutable identifier, not a moving label.",
    ),
    (
        {"MODEL_VERSION": "Champion"},
        "MODEL_VERSION must be an immutable identifier, not a moving label.",
    ),
    (
        {"GIT_SHA": "stable"},
        "GIT_SHA must be an immutable identifier, not a moving label.",
    ),
    ({"GIT_SHA": SHA40[:39]}, "GIT_SHA must be a 40-character commit hash."),
    ({"GIT_SHA": "z" * 40}, "GIT_SHA must be a 40-character commit hash."),
    (
        {"IMAGE_DIGEST": HEX64},
        "IMAGE_DIGEST must be sha256 followed by 64 hexadecimal digits.",
    ),
    (
        {"IMAGE_DIGEST": "sha256:zz"},
        "IMAGE_DIGEST must be sha256 followed by 64 hexadecimal digits.",
    ),
    (
        {"IMAGE_DIGEST": "production"},
        "IMAGE_DIGEST must be an immutable identifier, not a moving label.",
    ),
)


def test_every_accepted_setting_reaches_its_field() -> None:
    for environ, field, expected in ACCEPTED:
        assert getattr(settings(**environ), field) == expected, environ


def test_every_refused_setting_names_itself() -> None:
    for environ, message in REFUSED:
        assert refused(**environ) == message, environ


def test_every_setting_is_covered() -> None:
    """A row in `_LOAD_ORDER` with no row above is a setting nothing pins."""
    declared = {
        step.env for step in config._LOAD_ORDER if isinstance(step, config._Setting)
    }
    exercised = {name for environ, _, _ in ACCEPTED for name in environ}
    assert declared - exercised == set()
    # And the reverse: a row here for a variable the loader no longer reads.
    assert exercised - declared == set()
    # `READ_BY_LOADER` is what isolates each case, so it may not fall behind.
    assert declared | {"SERVICE_TOKEN"} == READ_BY_LOADER


def test_every_field_the_loader_fills_is_a_settings_field() -> None:
    fields = set(vars(Settings()))
    filled = {
        step.field for step in config._LOAD_ORDER if isinstance(step, config._Setting)
    }
    assert filled - fields == set()


def test_an_empty_environment_gives_the_shipped_defaults() -> None:
    default = settings()
    assert default.app_name == "PII Masking Service"
    assert default.app_version == "1.0.0"
    assert default.log_level == "INFO"
    assert default.api_contract_version == "v1"
    assert default.service_name == "octo-pii-masker"
    assert default.model_name == "octo-pii-masker"
    assert default.service_release == "unknown"
    assert default.git_sha == "unknown"
    assert default.image_digest == "unknown"
    assert default.model_version == "unknown"
    assert default.confidence_threshold == 0.3
    assert default.entity_confidence_thresholds == {"ORGANIZATION_NAME": 0.1}
    assert default.model_id_shape_gate is True
    assert default.structured_field_threshold == 0.5
    assert default.accelerator == "auto"
    assert default.detection_mode == "regex"
    assert default.detector_label_profile == "octo"
    assert default.organization_surrogate_pool == "medical"
    assert default.pdf_locale_badge is True
    assert default.pdf_redaction_style == "invisible"
    assert default.pdf_redaction_mode == "pseudonymize"
    assert default.pdf_chip_reflow is True
    assert default.pdf_ocr is False
    assert default.pdf_ocr_dpi == 200
    assert default.pdf_ocr_max_pixels == 2048
    assert default.pdf_ocr_min_block_points == 16
    assert default.pdf_ocr_min_line_confidence == 0.0
    assert default.pdf_ocr_min_box_fill == 0.55
    assert default.pdf_ocr_max_lines == 400
    assert default.ocr_intra_op_threads == 4
    assert default.max_model_tokens == 510
    assert default.model_stride_tokens == 192
    assert default.max_upload_size_bytes == 10 * 1024 * 1024
    assert default.max_model_input_bytes == 10 * 1024 * 1024
    assert default.max_concurrent_masks == 2
    assert default.onnx_model_sha256 == ""
    assert default.ocr_det_sha256 == ""
    assert default.ocr_rec_sha256 == ""
    assert default.ocr_charset_sha256 == ""
    assert default.service_token == ""
    assert default.supported_media_types == frozenset(
        {"application/pdf", "text/plain", "text/csv", "application/json"}
    )


def test_a_toggle_reads_either_case_and_only_on_or_off() -> None:
    for value, expected in (("on", True), ("ON", True), ("off", False), ("OFF", False)):
        assert settings(PDF_LOCALE_BADGE=value).pdf_locale_badge is expected
    assert (
        refused(PDF_LOCALE_BADGE="On ") == "PDF_LOCALE_BADGE must be one of: off, on."
    )


def test_only_the_choices_declared_case_insensitive_fold_case() -> None:
    # These fold, because a deployment writes them by hand.
    assert settings(ACCELERATOR="CPU").accelerator == "cpu"
    assert settings(ORGANIZATION_SURROGATE_POOL="MEDICAL").organization_surrogate_pool
    assert (
        settings(PDF_REDACTION_STYLE="BOLD_CHIPS").pdf_redaction_style == "bold_chips"
    )
    assert (
        settings(
            PDF_REDACTION_MODE="DETECT", PDF_REDACTION_STYLE="bold_chips"
        ).pdf_redaction_mode
        == "detect"
    )
    # DETECTION_MODE and DETECTOR_LABEL_PROFILE do not: they name a label
    # vocabulary and a code path, and are copied from the contract as written.
    assert refused(DETECTION_MODE="Regex").startswith("DETECTION_MODE must be one of: ")
    assert refused(DETECTOR_LABEL_PROFILE="Octo").startswith(
        "DETECTOR_LABEL_PROFILE must be one of: "
    )


def test_a_ratio_takes_both_ends_of_its_range() -> None:
    assert settings(CONFIDENCE_THRESHOLD="0").confidence_threshold == 0.0
    assert settings(CONFIDENCE_THRESHOLD="1").confidence_threshold == 1.0


def test_the_smallest_accepted_window_and_stride() -> None:
    narrow = settings(MAX_MODEL_TOKENS="2", MODEL_STRIDE_TOKENS="1")
    assert (narrow.max_model_tokens, narrow.model_stride_tokens) == (2, 1)
    assert settings(MODEL_STRIDE_TOKENS="0").model_stride_tokens == 0


def test_a_digest_is_lowercased_and_empty_means_unchecked() -> None:
    assert settings(ONNX_MODEL_SHA256=HEX64.upper()).onnx_model_sha256 == HEX64
    assert settings(ONNX_MODEL_SHA256="").onnx_model_sha256 == ""
    assert settings(IMAGE_DIGEST=f"SHA256:{HEX64.upper()}").image_digest == (
        f"sha256:{HEX64}"
    )
    assert settings(GIT_SHA=SHA40.upper()).git_sha == SHA40


def test_an_immutable_identifier_is_trimmed_and_empty_falls_back() -> None:
    assert settings(SERVICE_RELEASE="  2026.09.1  ").service_release == "2026.09.1"
    assert settings(SERVICE_RELEASE="   ").service_release == "unknown"
    assert settings(SERVICE_RELEASE="").service_release == "unknown"
    assert settings(GIT_SHA="").git_sha == "unknown"
    assert settings(IMAGE_DIGEST="").image_digest == "unknown"


def test_every_moving_label_is_refused_wherever_a_release_belongs() -> None:
    for label in (
        "latest",
        "final",
        "champion",
        "production",
        "stable",
        "FUSED-XLMR-FINAL",
    ):
        assert refused(SERVICE_RELEASE=label) == (
            "SERVICE_RELEASE must be an immutable identifier, not a moving label."
        ), label


def test_entity_thresholds_merge_over_the_shipped_floors() -> None:
    assert settings(ENTITY_CONFIDENCE_THRESHOLDS="").entity_confidence_thresholds == {
        "ORGANIZATION_NAME": 0.1
    }
    assert settings(
        ENTITY_CONFIDENCE_THRESHOLDS="   "
    ).entity_confidence_thresholds == {"ORGANIZATION_NAME": 0.1}
    # An entry naming a shipped label replaces its floor.
    assert settings(
        ENTITY_CONFIDENCE_THRESHOLDS="organization_name:0.6"
    ).entity_confidence_thresholds == {"ORGANIZATION_NAME": 0.6}
    # Entries are trimmed, empty ones skipped, and several may be given.
    assert settings(
        ENTITY_CONFIDENCE_THRESHOLDS=" age : 0.9 ,, NAME:0"
    ).entity_confidence_thresholds == {
        "ORGANIZATION_NAME": 0.1,
        "AGE": 0.9,
        "NAME": 0.0,
    }
    # The returned mapping is this call's own, not the shipped default itself.
    once = settings(ENTITY_CONFIDENCE_THRESHOLDS="")
    once.entity_confidence_thresholds["AGE"] = 0.5
    assert settings().entity_confidence_thresholds == {"ORGANIZATION_NAME": 0.1}


def test_the_onnx_directory_is_a_plain_setting_with_a_shipped_default() -> None:
    """It used to be derived from MODEL_PATH, which no longer exists.

    Deriving it meant the served artifact's location was a side effect of where
    a checkpoint the service never opened had been mounted. Now the only path
    the service reads is the only path a deployment sets.
    """
    assert settings().onnx_model_path == str(config.DEFAULT_ONNX_MODEL_DIR)
    assert settings(ONNX_MODEL_PATH="/elsewhere").onnx_model_path == "/elsewhere"


def test_ocr_artifacts_are_only_required_when_ocr_is_on(tmp_path: Path) -> None:
    assert settings(PDF_OCR="off", OCR_MODEL_PATH="/nowhere").pdf_ocr is False
    for missing in OCR_ARTIFACTS:
        present = [name for name in OCR_ARTIFACTS if name != missing]
        # One directory per case, under the directory pytest cleans up: each
        # has to be missing exactly one artifact, so they cannot share.
        incomplete = _ocr_artifacts(*present, base=tmp_path / missing.replace("/", "-"))
        message = refused(PDF_OCR="on", OCR_MODEL_PATH=incomplete)
        assert message.startswith(
            "PDF_OCR=on requires the OCR artifacts; missing under "
        )
        assert missing in message
        for name in present:
            assert name not in message


def loaded(**environ: str) -> Settings:
    """Like `settings`, but with the bearer token required, as a service loads it."""
    patch = isolated(environ)
    try:
        return load_settings()
    finally:
        patch.stop()


def test_the_service_token_must_be_a_real_secret() -> None:
    for environ, reason in (
        ({}, "started without a token"),
        (
            {"SERVICE_TOKEN": "replace-with-a-long-random-secret"},
            "accepted the documented placeholder",
        ),
        ({"SERVICE_TOKEN": ""}, "accepted an empty token"),
    ):
        try:
            loaded(**environ)
        except ValueError as error:
            assert str(error) == "SERVICE_TOKEN must contain a runtime secret."
        else:
            raise AssertionError(reason)
    assert loaded(SERVICE_TOKEN="a-real-one").service_token == "a-real-one"
    assert settings(SERVICE_TOKEN="a-real-one").service_token == ""


def test_a_deployment_is_told_the_first_thing_wrong_with_it() -> None:
    """Order is part of the contract, so it is pinned rather than incidental."""
    # A bad threshold outranks every later setting.
    assert "CONFIDENCE_THRESHOLD" in refused(
        CONFIDENCE_THRESHOLD="2", ACCELERATOR="tpu", GIT_SHA="nope"
    )
    # The redaction pair is checked before PDF_OCR is even read.
    assert "PDF_REDACTION_MODE=detect" in refused(
        PDF_REDACTION_MODE="detect", PDF_OCR="maybe"
    )
    # The OCR artifacts are checked after the digests beside them.
    assert "OCR_DET_SHA256" in refused(
        PDF_OCR="on", OCR_MODEL_PATH="/nowhere", OCR_DET_SHA256="bad"
    )
    # ...and before the window, which is read after them.
    assert "OCR artifacts" in refused(
        PDF_OCR="on", OCR_MODEL_PATH="/nowhere", MAX_MODEL_TOKENS="1"
    )
    # The identity fields are last, so a bad size outranks a bad release.
    assert "MAX_CONCURRENT_MASKS" in refused(
        MAX_CONCURRENT_MASKS="0", SERVICE_RELEASE="latest"
    )


def test_unknown_release_fields_names_what_the_deployment_cannot_prove() -> None:
    assert config.unknown_release_fields(settings()) == [
        "git_sha",
        "image_digest",
        "model_version",
        "service_release",
    ]
    proven = settings(
        SERVICE_RELEASE="2026.09.1",
        GIT_SHA=SHA40,
        IMAGE_DIGEST=f"sha256:{HEX64}",
        MODEL_VERSION="v7",
    )
    assert config.unknown_release_fields(proven) == []
    assert config.unknown_release_fields(settings(GIT_SHA=SHA40)) == [
        "image_digest",
        "model_version",
        "service_release",
    ]


def test_a_choice_reader_refuses_a_value_outside_its_own_list() -> None:
    """Pin `_one_of` directly, not only through the settings table.

    Every reader in `_LOAD_ORDER` is built once at import time, so mutation
    testing attributes this factory to an unrelated test and calls each of its
    mutants surviving. It is also the only thing standing between a typo in a
    deployment's environment and a masking mode nobody chose.
    """
    strict = config._one_of("regex", "hybrid")
    folding = config._one_of("regex", "hybrid", ignore_case=True)
    with mock.patch.dict(os.environ, {"MODE": "hybrid"}, clear=False):
        assert strict("MODE", "regex") == "hybrid"
    with mock.patch.dict(os.environ, {"MODE": "Hybrid"}, clear=False):
        assert folding("MODE", "regex") == "hybrid"
        try:
            strict("MODE", "regex")
        except ValueError as error:
            assert "MODE must be one of: regex, hybrid." in str(error)
        else:
            raise AssertionError("a case-sensitive reader accepted 'Hybrid'")
    # Unset falls back to the default, which is checked against the same list.
    with mock.patch.dict(os.environ, {}, clear=True):
        assert strict("MODE", "regex") == "regex"
