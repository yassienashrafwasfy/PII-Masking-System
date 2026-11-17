#!/usr/bin/env python3
"""Exercise an Octo private model service without loading neural weights."""

from __future__ import annotations

import ast
import io
import json
import os
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]

# This module's premise is a service running without neural weights, so it
# needs regex detection. Set at import: other modules in this suite also set
# TEAM_ID and DETECTION_MODE at import time, and pytest imports every module
# during collection before running anything, so this file's import being the
# later one is what makes these stick.
os.environ.update({"TEAM_ID": "octo", "DETECTION_MODE": "regex"})
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "platform"))
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))
sys.path.insert(0, str(ROOT / "platform/model-service/src"))
import service
from masking_engine import (
    CorruptInputError,
    MaskingRouter,
    MaskSpan,
    apply_replacements,
    coalesce_spans,
    locales,
)
from teams.synthify.runtime import _valid_entity


def invoke(
    path: str, payload: bytes = b"", content_type: str = "text/plain", **changes: str
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}
    environ: dict[str, object] = {
        "REQUEST_METHOD": "POST" if path == "/mask" else "GET",
        "PATH_INFO": path,
        "HTTP_X_REQUEST_ID": "model-service-test",
        "HTTP_X_SOURCE_OBJECT_KEY": "incoming/octo/sample.txt",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": io.BytesIO(payload),
    }
    environ.update(changes)
    response = service.application(
        environ,
        lambda status, headers: captured.update(status=status, headers=dict(headers)),
    )
    return captured["status"], captured["headers"], b"".join(response)  # type: ignore[return-value]


def pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


# The model is loaded lazily by the first masking request and can be unloaded
# again, so whether /ready answers 200 or 503 is a property of what ran before.
# Each case that cares states which state it wants rather than inheriting it.


def _unloaded() -> None:
    """Return the service to its just-started state, with no model in memory."""
    service._cancel_idle_unload()
    service._unload_model(service._idle_generation)


def _loaded() -> None:
    """Load the model the way a first masking request does."""
    invoke("/mask", b"Contact synthetic@example.org.")


def test_a_shaped_entity_is_kept_and_a_bare_word_is_not() -> None:
    assert _valid_entity("Address", "Molenweg 8, 2800 Mechelen")
    assert not _valid_entity("Address", "Diltiazemzalf")
    assert not _valid_entity("Name", "Anamnese")
    assert not _valid_entity("URL", "Pagina 3/3")


def test_a_generic_word_is_not_kept_as_a_detection() -> None:
    assert not service._keep_detection("Patient", MaskSpan(0, 7, "NAME", "model"))
    assert service._keep_detection("Alice", MaskSpan(0, 5, "NAME", "model"))


def test_health_is_up_without_a_model() -> None:
    _unloaded()
    status, _, _ = invoke("/health")
    assert status == "200 OK"


def test_ready_is_unavailable_until_a_request_loads_the_model() -> None:
    _unloaded()
    status, _, _ = invoke("/ready")
    assert status == "503 Service Unavailable"


def test_version_reports_the_model_without_loading_it() -> None:
    _unloaded()
    status, headers, body = invoke("/version")
    assert status == "200 OK" and headers["Content-Type"] == "application/json"
    assert b'"model_name":"octo-pii-masker"' in body
    # Which languages this team holds a checkpoint for: octo has one model for
    # every language, so it reports the platform's whole list, and a team with
    # fewer (berts) reports the ones it holds.
    assert b'"supported_locales":[' in body
    # Reporting an identity must not be what loads the weights.
    status, _, _ = invoke("/ready")
    assert status == "503 Service Unavailable"


def test_masking_loads_the_model_and_removes_the_value() -> None:
    _unloaded()
    status, headers, body = invoke("/mask", b"Contact synthetic@example.org.")
    assert status == "200 OK" and headers["X-Model-Name"] == "octo-pii-masker"
    assert headers["X-Model-Format"] == "onnx"
    assert b"synthetic@example.org" not in body
    status, _, _ = invoke("/ready")
    assert status == "200 OK"


def test_a_zero_idle_timeout_schedules_no_unload_timer() -> None:
    _loaded()
    service._cancel_idle_unload()
    configured_idle_timeout = service.IDLE_TIMEOUT_SECONDS
    service.IDLE_TIMEOUT_SECONDS = 0
    try:
        status, _, _ = invoke("/mask", b"Contact synthetic@example.org.")
        assert status == "200 OK" and service._idle_timer is None
    finally:
        service.IDLE_TIMEOUT_SECONDS = configured_idle_timeout


def test_an_unloaded_model_reloads_on_the_next_request() -> None:
    _loaded()
    _unloaded()
    status, _, _ = invoke("/ready")
    assert status == "503 Service Unavailable"
    status, _, _ = invoke("/mask", b"Contact synthetic@example.org.")
    assert status == "200 OK"


def test_a_pdf_is_masked_through_the_shared_pipeline() -> None:
    status, _, body = invoke(
        "/mask", pdf_bytes("Contact synthetic@example.org."), "application/pdf"
    )
    assert status == "200 OK"
    with pymupdf.open(stream=body, filetype="pdf") as document:
        assert "synthetic@example.org" not in document[0].get_text()


def test_a_geometry_failure_is_reported_as_unprocessable() -> None:
    original = service._detect_and_redact
    service._detect_and_redact = lambda *_: (
        (_ for _ in ()).throw(CorruptInputError("synthetic geometry failure"))
    )
    try:
        status, _, body = invoke("/mask", b"x")
    finally:
        service._detect_and_redact = original
    assert status == "422 Unprocessable Content" and b"MODEL_SERVICE_ERROR" in body


def test_an_invalid_model_format_is_refused() -> None:
    status, _, body = invoke("/mask", b"x", HTTP_X_MODEL_FORMAT="invalid")
    assert status == "400 Bad Request" and b"MODEL_SERVICE_ERROR" in body


def test_labels_are_filtered_before_spans_coalesce() -> None:
    # The filter must run before `coalesce_spans`. A NAME inside a longer DATE
    # merges into one DATE span, so filtering afterwards drops the merged span
    # and leaves the NAME text readable in the released document.
    text = "Jan Peeters 24-02-2026 rest"
    name = MaskSpan(0, 11, "NAME", "model")
    date = MaskSpan(8, 22, "DATE", "regex")
    assert [span.label for span in coalesce_spans([name, date])] == ["DATE"]
    kept = [span for span in (name, date) if span.label == "NAME"]
    masked = apply_replacements(text, kept, MaskingRouter(text.encode(), None))
    assert "Jan Peeters" not in masked, masked


def test_absent_or_blank_mask_labels_mean_the_full_taxonomy() -> None:
    # Absent, empty, and whitespace all mean the full taxonomy. The deployment
    # canary sends no such header, so this is what keeps it passing.
    selected = service._selected_labels
    for raw in ("", "   "):
        assert selected({"HTTP_X_MASK_LABELS": raw}) is None, raw
    assert selected({}) is None
    assert selected({"HTTP_X_MASK_LABELS": "NAME, organization_name"}) == frozenset(
        {"NAME", "ORGANIZATION_NAME"}
    )


def test_an_unknown_mask_label_is_refused() -> None:
    # A typo is refused rather than narrowed: masking less than the caller
    # believed must never be the quiet outcome.
    try:
        service._selected_labels({"HTTP_X_MASK_LABELS": "NAEM"})
    except service.ServiceError as exc:
        assert exc.status == 400, exc.status
    else:
        raise AssertionError("an unknown label must be rejected")

    # The same value is refused over HTTP, not only by the helper.
    status, _, body = invoke("/mask", b"x", HTTP_X_MASK_LABELS="NAEM")
    assert status == "400 Bad Request" and b"MODEL_SERVICE_ERROR" in body


def test_a_document_locale_is_revalidated_here() -> None:
    # `X-Document-Locale` is re-validated here rather than trusted, the way
    # the format and the label selection are: the gateway has already checked
    # it, and this service is still correct on its own.
    locale = service._document_locale
    for raw in ("", "   "):
        assert locale({"HTTP_X_DOCUMENT_LOCALE": raw}) is None, raw
    assert locale({}) is None
    assert locale({"HTTP_X_DOCUMENT_LOCALE": "en"}) == "en"
    assert locale({"HTTP_X_DOCUMENT_LOCALE": "NL_be"}) == "nl_BE"


def test_an_unknown_document_locale_is_refused() -> None:
    # `pt_BR` rather than a locale this release might later write: the point is
    # that an unsupported value is refused, and a test that spells its own
    # counter-example as a language on the roadmap stops testing that the day
    # the language lands. This one asserted `de_DE` was unknown until German
    # became supported, and then failed for the wrong reason.
    unsupported = "pt_BR"
    assert unsupported not in locales.SUPPORTED_LOCALES, unsupported
    try:
        service._document_locale({"HTTP_X_DOCUMENT_LOCALE": unsupported})
    except service.ServiceError as exc:
        assert exc.status == 400, exc.status
    else:
        raise AssertionError("an unknown locale must be rejected")
    status, _, body = invoke("/mask", b"x", HTTP_X_DOCUMENT_LOCALE=unsupported)
    assert status == "400 Bad Request" and b"MODEL_SERVICE_ERROR" in body


def test_the_uploaded_audit_carries_no_detected_value() -> None:
    # `MaskingRouter.record` keeps the detected value in the audit entry so a
    # caller can explain one decision while it still holds the document. The
    # upload publishes that entry to object storage, where the value has no
    # business being -- this is the masking service, and `text` is exactly what
    # it was asked to remove.
    entry = {
        "label": "NAME",
        "start": 0,
        "end": 11,
        "text": "Jan Peeters",
        "replaced_text": "Bram Claes",
        "source_layer": "text",
        "confidence": 0.97,
        "page": 0,
    }
    published = service._publishable([entry])

    assert published == [
        {
            "label": "NAME",
            "start": 0,
            "end": 11,
            "source_layer": "text",
            "confidence": 0.97,
            "page": 0,
        }
    ], published
    flattened = json.dumps(published)
    assert "Jan Peeters" not in flattened
    assert "Bram Claes" not in flattened


def test_a_new_audit_field_is_withheld_until_it_is_allowed() -> None:
    # An allow-list, not a blocklist: a field added to the audit later must not
    # reach storage because nobody remembered to exclude it.
    published = service._publishable(
        [{"label": "NAME", "start": 0, "end": 4, "surrounding_sentence": "secret"}]
    )
    assert published == [{"label": "NAME", "start": 0, "end": 4}], published


def test_a_pinned_locale_this_team_has_no_checkpoint_for_is_refused() -> None:
    # A caller that names a language is asking for a specific model, so the
    # answer has to be that model or a refusal. Berts holds three checkpoints
    # while the platform names six locales, and before this the other three
    # were answered by the Dutch checkpoint while the response reported the
    # name of a model the caller had asked for.
    assert (
        service._supported_locales() is None
    ), "octo holds one model for every language"

    checkpoints = frozenset({"nl_BE", "en", "fr_FR"})
    for locale in (None, "en", "fr_FR", "nl_BE"):
        service._require_team_locale(locale, checkpoints)
        service._require_team_locale(locale, None)

    try:
        service._require_team_locale("de_DE", checkpoints)
    except service.ServiceError as exc:
        assert exc.status == 400, exc.status
        assert "en, fr_FR, nl_BE" in exc.message, exc.message
    else:
        raise AssertionError("a pinned locale with no checkpoint must be refused")

    # A team that declares no set keeps the old behaviour end to end: the
    # same pinned value is accepted, because its one model reads it.
    assert service._document_locale({"HTTP_X_DOCUMENT_LOCALE": "de_DE"}) == "de_DE"
    status, _, _ = invoke(
        "/mask", b"Contact synthetic@example.org.", HTTP_X_DOCUMENT_LOCALE="de_DE"
    )
    assert status == "200 OK", status


def test_each_request_header_is_asserted_only_by_the_cases_that_own_it() -> None:
    """Catch a merge folding one case's assertions into a neighbouring one.

    This file used to be a single `main()`. Every merge that meets a branch
    still carrying that older shape resolves the conflict by keeping both
    sides, which puts a whole block of assertions back inside the test above
    while the dedicated cases still hold it. That has now happened three
    times, to the `X-Document-Locale` block each time.

    Nothing catches it, because nothing is wrong with the assertions -- they
    are correct, they simply run twice, inside a test whose name says nothing
    about them. Only the file's shape gives it away, so this reads the shape.
    """
    owners = {
        "HTTP_X_DOCUMENT_LOCALE": {
            "test_a_document_locale_is_revalidated_here",
            "test_a_pinned_locale_this_team_has_no_checkpoint_for_is_refused",
            "test_an_unknown_document_locale_is_refused",
        },
        "HTTP_X_MASK_LABELS": {
            "test_absent_or_blank_mask_labels_mean_the_full_taxonomy",
            "test_an_unknown_mask_label_is_refused",
        },
        "HTTP_X_MODEL_FORMAT": {"test_an_invalid_model_format_is_refused"},
    }

    # This case names every header in order to check them, so it is describing
    # them rather than exercising them and must not judge itself.
    describing = (
        test_each_request_header_is_asserted_only_by_the_cases_that_own_it.__name__
    )

    source = Path(__file__).read_text(encoding="utf-8")
    lines = source.splitlines()
    bodies: dict[str, str] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
            and node.name != describing
        ):
            bodies[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno])

    strays = [
        f"{header} asserted in {name}"
        for header, allowed in owners.items()
        for name, body in bodies.items()
        if header in body and name not in allowed
    ]
    assert not strays, (
        "a request header is exercised outside the cases that own it, which is "
        "what a duplicated merge block looks like -- " + "; ".join(sorted(strays))
    )

    # A guard that passes because the cases were renamed away guards nothing.
    for header, allowed in owners.items():
        for name in allowed:
            assert name in bodies, f"{name} is gone; update this guard"
            assert header in bodies[name], f"{name} no longer exercises {header}"


def main() -> None:
    """Run every test in this module, discovered rather than listed."""
    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for _, test in tests:
        test()
    print(f"Validated {len(tests)} cases in {Path(__file__).name}.")


if __name__ == "__main__":
    main()
