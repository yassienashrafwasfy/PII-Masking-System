#!/usr/bin/env python3
"""Pin what happens to PDF text this pipeline never reads.

Extraction reads the page content stream. A PDF can carry text in four other
places, and every one of them was confirmed leaving a masked document still
holding the original patient name and national number, fully extractable:

    a_form_field         survives in: page, widget, rawbytes
    a_text_in_annotation survives in: annot, rawbytes
    a_metadata           survives in: metadata, rawbytes
    a_embedded_file      survives in: embedded

Three of them refuse the document, on the same ruling as the image check that
predates them: a document this pipeline cannot fully mask must not be released
looking as though it had been. Metadata is stripped instead, because it holds
no layout and nothing downstream reads it -- and because refusing on it would
reject every sample in this repository, all of which carry a placeholder Title
that holds nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, prepare_document, redact_document

_PATIENT = "Els Wouters"
_NATIONAL_NUMBER = "78.03.12-123.45"


def _page(text: str = "Verslag cardiologie.") -> tuple[pymupdf.Document, pymupdf.Page]:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 120), text, fontsize=10)
    return document, page


def _serialise(document: pymupdf.Document) -> bytes:
    data = document.tobytes(deflate=True)
    document.close()
    return data


def _refused(data: bytes) -> str:
    try:
        prepare_document(data, "application/pdf")
    except ValueError as exc:
        return str(exc)
    raise AssertionError("the document should have been refused")


def test_an_annotation_carrying_text_refuses_the_document() -> None:
    document, page = _page()
    annotation = page.add_text_annot((300, 110), f"{_PATIENT}, {_NATIONAL_NUMBER}")
    annotation.update()
    assert "annotation content cannot be masked" in _refused(_serialise(document))


def test_an_empty_annotation_does_not_refuse_the_document() -> None:
    # Emptiness is the test, not presence: a stamp or a highlight with nothing
    # written on it carries nothing to leak, and an ordinary annotated
    # document must not be turned away for the annotation alone.
    document, page = _page()
    page.add_highlight_annot(pymupdf.Rect(72, 110, 200, 125)).update()
    prepared = prepare_document(_serialise(document), "application/pdf")
    assert "Verslag" in prepared.texts[0].text


def test_a_filled_form_field_refuses_the_document() -> None:
    document, page = _page()
    widget = pymupdf.Widget()
    widget.field_name = "patient"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT  # type: ignore[attr-defined]
    widget.rect = pymupdf.Rect(72, 140, 320, 160)
    widget.field_value = f"{_PATIENT} {_NATIONAL_NUMBER}"
    page.add_widget(widget)
    assert "form field content cannot be masked" in _refused(_serialise(document))


def test_an_unfilled_form_field_does_not_refuse_the_document() -> None:
    document, page = _page()
    widget = pymupdf.Widget()
    widget.field_name = "patient"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT  # type: ignore[attr-defined]
    widget.rect = pymupdf.Rect(72, 140, 320, 160)
    widget.field_value = ""
    page.add_widget(widget)
    prepared = prepare_document(_serialise(document), "application/pdf")
    assert "Verslag" in prepared.texts[0].text


def test_an_embedded_file_refuses_the_document() -> None:
    document, _ = _page()
    document.embfile_add(
        "notes.txt",
        f"{_PATIENT} {_NATIONAL_NUMBER}".encode(),
        filename="notes.txt",
    )
    assert "embedded files cannot be masked" in _refused(_serialise(document))


def test_the_refusal_reason_survives_the_extraction_catch_all() -> None:
    # `extract_pdf_pages` flattens anything it does not recognise into
    # "invalid or unsupported PDF content", which sent an operator looking for
    # a corrupt file when the document was merely carrying a form field.
    document, page = _page()
    annotation = page.add_text_annot((300, 110), _PATIENT)
    annotation.update()
    assert "invalid or unsupported" not in _refused(_serialise(document))


def test_metadata_is_stripped_rather_than_refused() -> None:
    document, _ = _page(f"Patient {_PATIENT} gezien.")
    document.set_metadata(
        {
            "title": f"Dossier {_PATIENT}",
            "author": "Jens Vandeputte",
            "subject": _NATIONAL_NUMBER,
            "keywords": _PATIENT,
        }
    )
    data = _serialise(document)

    prepared = prepare_document(data, "application/pdf")
    start = prepared.texts[0].text.index(_PATIENT)
    masked = redact_document(
        prepared,
        [MaskSpan(start, start + len(_PATIENT), "NAME", "model", page=0)],
    )

    with pymupdf.open(stream=masked, filetype="pdf") as result:
        remaining = {
            key: value
            for key, value in (result.metadata or {}).items()
            if (value or "").strip()
        }
    assert not {"title", "author", "subject", "keywords"} & set(remaining), remaining
    # And out of the file, not merely out of the dictionary the reader shows.
    raw = masked.decode("latin-1")
    assert _PATIENT not in raw and _NATIONAL_NUMBER not in raw


def test_stripping_metadata_leaves_the_tool_fields_alone() -> None:
    # Producer and creator name the tool and are what an operator traces a
    # release by, so they are not swept up with the rest.
    document, _ = _page()
    document.set_metadata({"producer": "acme-writer 2.1", "title": "Dossier"})
    data = _serialise(document)
    masked = redact_document(prepare_document(data, "application/pdf"), [])
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        metadata = result.metadata or {}
    assert metadata.get("producer") == "acme-writer 2.1"
    assert not (metadata.get("title") or "").strip()


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
