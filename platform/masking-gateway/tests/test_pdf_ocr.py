#!/usr/bin/env python3
"""Pin the OCR bridge: geometry, deduplication, and how failures surface.

Every case here runs against a stub `PageReader` -- a plain function -- so none
of it needs a model artifact or ONNX Runtime. That is the point of injecting
the reader rather than importing an engine: the geometry is the part most
likely to be wrong, and it is testable on its own.

Fixtures are synthesised in memory. Nothing binary is checked in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, prepare_document, redact_document
from masking_engine.app.core.error_codes import ModelUnreadyError
from masking_engine.app.services import pdf_ocr


def _grey_block(width: int = 120, height: int = 40) -> pymupdf.Pixmap:
    """A synthetic scan: a black bar on white, standing in for a line of text."""
    buffer = np.full((height, width), 255, np.uint8)
    buffer[height // 4 : 3 * height // 4, 10 : width - 10] = 0
    return pymupdf.Pixmap(pymupdf.csGRAY, width, height, bytes(buffer.tobytes()), 0)


def scanned_page(
    *,
    layer: tuple[str, ...] = (),
    images: tuple[tuple[float, float, float, float], ...] = ((300, 300, 420, 340),),
    rotation: int = 0,
    size: tuple[float, float] = (595, 842),
    pixmap: tuple[int, int] = (120, 40),
) -> bytes:
    """Build a PDF with an optional text layer and any number of image blocks.

    `insert_image` fits the pixmap inside the rect keeping its aspect ratio, so
    the block that lands on the page is not the rect unless the two ratios
    agree. Callers that care about the block's own size pass `pixmap` to match.
    """
    document = pymupdf.open()
    page = document.new_page(width=size[0], height=size[1])
    for index, line in enumerate(layer):
        page.insert_text((72, 72 + index * 20), line, fontsize=11)
    for rect in images:
        page.insert_image(pymupdf.Rect(*rect), pixmap=_grey_block(*pixmap))
    if rotation:
        page.set_rotation(rotation)
    data = document.tobytes()
    document.close()
    return data


def whole_raster(text: str = "Dr. Amelie Fontaine", confidence: float = 0.97):
    """A reader that claims one line covering everything it was handed.

    Mapped back, that line must reproduce the region's own bbox, which makes it
    an exact check on the coordinate transform rather than an approximate one.
    """

    def reader(image, *, width: int, height: int):
        return (
            pdf_ocr.OcrLine(text, (0.0, 0.0, float(width), float(height)), confidence),
        )

    return reader


def _open(data: bytes) -> tuple[pymupdf.Document, pymupdf.Page]:
    document = pymupdf.open(stream=data, filetype="pdf")
    return document, document[0]


def test_blocks_that_touch_become_one_region() -> None:
    """A scan sliced into strips must not be cut at every strip boundary."""
    strips = [(0, 0, 100, 20), (0, 21, 100, 41), (0, 42, 100, 62), (400, 400, 450, 420)]
    assert pdf_ocr.cluster_blocks(strips) == [
        (0, 0, 100, 62),
        (400, 400, 450, 420),
    ]
    # Order in the content stream must not change the outcome.
    assert pdf_ocr.cluster_blocks(list(reversed(strips))) == pdf_ocr.cluster_blocks(
        strips
    )
    assert pdf_ocr.cluster_blocks([]) == []


def test_render_zoom_is_bounded_by_pixels_not_by_dpi() -> None:
    """The real sample declares 1861x2631pt for a 2481x3508px scan."""
    cluster = (0.0, 0.0, 1861.0, 2631.0)
    for dpi in (200, 300, 600):
        zoom = pdf_ocr.render_zoom(cluster, dpi=dpi, max_pixels=2048)
        assert round(2631 * zoom) <= 2048, (dpi, zoom)
    # A fixed 300 DPI would be zoom 4.17 here, i.e. 85 megapixels for one page.
    assert pdf_ocr.render_zoom(cluster, dpi=300, max_pixels=2048) < 1.0

    # The source's own resolution is a bound too: a low-resolution fax is not
    # upsampled into a larger array carrying no more information.
    bounded = pdf_ocr.render_zoom(
        cluster, dpi=600, max_pixels=99999, native_long_edge=3508
    )
    assert abs(bounded - 3508 / 2631) < 1e-6
    assert pdf_ocr.render_zoom((0.0, 0.0, 0.0, 0.0), dpi=200, max_pixels=2048) == 0.0


def test_a_line_maps_back_into_the_block_it_was_read_from() -> None:
    document, page = _open(scanned_page())
    block = pdf_ocr.image_blocks(page)[0]
    lines = pdf_ocr.read_page(page, whole_raster())
    assert len(lines) == 1
    assert max(abs(a - b) for a, b in zip(lines[0].bbox, block)) < 1.0, (
        lines[0].bbox,
        block,
    )
    document.close()


def test_the_mapping_survives_every_page_rotation() -> None:
    """`get_pixmap` is the one API in display space; everything else is not."""
    for rotation in (0, 90, 180, 270):
        document, page = _open(
            scanned_page(
                images=((50, 100, 250, 400),), rotation=rotation, size=(400, 600)
            )
        )
        block = pdf_ocr.image_blocks(page)[0]
        lines = pdf_ocr.read_page(page, whole_raster())
        assert len(lines) == 1, rotation
        assert max(abs(a - b) for a, b in zip(lines[0].bbox, block)) < 1.0, rotation
        document.close()


def test_the_text_layer_is_not_read_a_second_time() -> None:
    """Rendering an image block renders whatever is drawn over it.

    A scanner's invisible text layer under a full-page image is the common
    case; without this the pipeline sees every word twice.
    """
    # The image sits exactly over the text line -- pixmap ratio matched to the
    # rect, so the block lands where it is put instead of being fitted and
    # centred inside it. A reader claiming the whole region is then claiming
    # the same words the text layer already provides.
    document, page = _open(
        scanned_page(
            layer=("Patient: Jean-Baptiste Moreau",),
            images=((72, 60, 220, 75),),
            pixmap=(148, 15),
        )
    )
    assert (
        pdf_ocr._coverage(pdf_ocr.image_blocks(page)[0], pdf_ocr.text_boxes(page)) > 0.9
    )
    assert pdf_ocr.read_page(page, whole_raster()) == []
    document.close()

    # A scan larger than the caption printed over it is NOT a duplicate: only
    # part of the region is text the pipeline already has, and dropping the
    # whole read would lose everything else on it.
    document, page = _open(
        scanned_page(
            layer=("Patient: Jean-Baptiste Moreau",),
            images=((60, 50, 240, 85),),
            pixmap=(180, 35),
        )
    )
    assert len(pdf_ocr.read_page(page, whole_raster())) == 1
    document.close()

    # The same reader on a region the text layer does not cover keeps its line.
    document, page = _open(scanned_page(layer=("Patient: Jean-Baptiste Moreau",)))
    assert len(pdf_ocr.read_page(page, whole_raster())) == 1
    document.close()


def test_coverage_is_clamped_so_overlap_cannot_over_count() -> None:
    box = (0.0, 0.0, 10.0, 10.0)
    assert pdf_ocr._coverage(box, []) == 0.0
    assert pdf_ocr._coverage(box, [(0.0, 0.0, 5.0, 10.0)]) == 0.5
    # Two identical covers would sum to 2.0 without the clamp, and this value
    # gates dropping a line -- over-counting would discard one.
    assert pdf_ocr._coverage(box, [box, box]) == 1.0
    assert pdf_ocr._coverage((0.0, 0.0, 0.0, 5.0), [box]) == 0.0


def test_small_images_are_skipped_rather_than_rendered() -> None:
    """A rule, a bullet and a letterhead icon are image blocks too."""
    document, page = _open(
        scanned_page(images=((300, 300, 308, 308),), pixmap=(40, 40))
    )
    block = pdf_ocr.image_blocks(page)[0]
    assert round(block[2] - block[0]) == 8, block
    assert pdf_ocr.read_page(page, whole_raster(), min_block_points=16.0) == []
    # Lower the floor and the same page is read.
    assert len(pdf_ocr.read_page(page, whole_raster(), min_block_points=4.0)) == 1
    document.close()


def test_a_page_with_no_image_reads_nothing_and_renders_nothing() -> None:
    def explode(image, *, width: int, height: int):
        raise AssertionError("a text-only page must never be rasterised")

    document, page = _open(scanned_page(layer=("Only text here",), images=()))
    assert pdf_ocr.read_page(page, explode) == []
    document.close()


def test_low_confidence_lines_are_kept_by_default() -> None:
    """A dropped line's pixels stay on the page and its text never reaches
    the detector, so the default filter is off, not lenient."""
    document, page = _open(scanned_page())
    assert len(pdf_ocr.read_page(page, whole_raster(confidence=0.01))) == 1
    assert (
        pdf_ocr.read_page(page, whole_raster(confidence=0.01), min_confidence=0.5) == []
    )
    document.close()


def test_blank_and_whitespace_only_reads_are_discarded() -> None:
    document, page = _open(scanned_page())
    assert pdf_ocr.read_page(page, whole_raster(text="   ")) == []
    document.close()


def test_too_many_lines_refuses_the_page_rather_than_truncating() -> None:
    """Truncating drops the least confident boxes, which are the riskiest."""

    def many(image, *, width: int, height: int):
        return tuple(
            pdf_ocr.OcrLine(
                f"line {index}", (0.0, float(index), 10.0, index + 1.0), 0.9
            )
            for index in range(12)
        )

    document, page = _open(scanned_page())
    try:
        pdf_ocr.read_page(page, many, max_lines=5)
    except pdf_ocr.OcrError as exc:
        assert "above the 5 limit" in str(exc)
    else:
        raise AssertionError("an over-long page must be refused")
    assert len(pdf_ocr.read_page(page, many, max_lines=50)) == 12
    document.close()


def test_a_reader_failure_is_an_ocr_error_not_a_bad_document() -> None:
    """`extract_pdf_pages` flattens everything to "invalid PDF content".

    A missing artifact is not the caller's document being wrong, and reporting
    it that way sends an operator looking in the wrong place.
    """

    def broken(image, *, width: int, height: int):
        raise RuntimeError("session is gone")

    document, page = _open(scanned_page())
    try:
        pdf_ocr.read_page(page, broken)
    except pdf_ocr.OcrError as exc:
        assert "session is gone" in str(exc)
    else:
        raise AssertionError("a reader failure must raise OcrError")

    # A typed model error passes through unchanged, so the service can map it
    # to 503 rather than flattening it to a generic OCR fault.
    def unready(image, *, width: int, height: int):
        raise ModelUnreadyError("artifact missing")

    try:
        pdf_ocr.read_page(page, unready)
    except ModelUnreadyError:
        pass
    else:
        raise AssertionError("ModelUnreadyError must not be rewrapped")
    document.close()


def test_reader_for_returns_nothing_when_ocr_is_off() -> None:
    """With OCR off nothing imports the engine, so no artifact is required."""

    class Settings:
        pdf_ocr = False

    assert pdf_ocr.reader_for(Settings()) is None
    assert pdf_ocr.reader_for(object()) is None


def _bar_pixmap(width: int = 300, height: int = 100) -> pymupdf.Pixmap:
    """A scan with a solid dark bar, so ink can be counted before and after."""
    buffer = np.full((height, width), 255, np.uint8)
    buffer[2 * height // 5 : 3 * height // 5, 20 : width - 20] = 0
    return pymupdf.Pixmap(pymupdf.csGRAY, width, height, bytes(buffer.tobytes()), 0)


def _image_ink(pdf_bytes: bytes, where: tuple[float, float, float, float]) -> float:
    """Ink of the embedded image placed at *where*, read from the stream.

    This is the assertion that matters. A box drawn over a scan makes the page
    *look* masked while `pdfimages` still recovers the original, so checking
    the rendered page cannot tell the fix from the bug it replaced.

    Images are found by where they sit on the page rather than by xref order,
    because applying redactions rewrites an image and can renumber it.
    """
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        target = pymupdf.Rect(*where)
        for image in document[0].get_images(full=True):
            for rect in document[0].get_image_rects(image[0]):
                if abs(rect.x0 - target.x0) < 2 and abs(rect.y0 - target.y0) < 2:
                    pixmap = pymupdf.Pixmap(document, image[0])
                    if pixmap.n > 1:
                        pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pixmap)
                    samples = np.frombuffer(pixmap.samples, np.uint8).reshape(
                        pixmap.height, pixmap.width
                    )
                    return float((samples < 128).mean())
        raise AssertionError(f"no embedded image found at {where}")
    finally:
        document.close()


def _scan_bytes(*, layer: tuple[str, ...] = (), logo: bool = False) -> bytes:
    """A page with a scan, optionally a logo, and optionally a text layer.

    A page built with `layer` is no longer an OCR candidate at all -- that is
    the fallback rule -- so it is the fixture for the refusal, not for a page
    read both ways. Built without one it is a plain scan, and a `logo` on it
    is a second image OCR reads nothing from.
    """
    document = pymupdf.open()
    page = document.new_page(width=400, height=280)
    page.insert_image(pymupdf.Rect(*SCAN_RECT), pixmap=_bar_pixmap())
    if logo:
        page.insert_image(pymupdf.Rect(*LOGO_RECT), pixmap=_bar_pixmap(90, 30))
    for index, line in enumerate(layer):
        page.insert_text((55, 192 + index * 18), line, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


# Where the fixture puts each image, so tests name them by position.
SCAN_RECT = (50.0, 50.0, 350.0, 150.0)
LOGO_RECT = (50.0, 170.0, 140.0, 200.0)


def _typed_then_scanned_bytes() -> bytes:
    """One document with a born-digital page and a scanned one.

    Since OCR became a per-page fallback this is the only shape that carries
    both provenances, so it is the fixture wherever a test needs to tell them
    apart.
    """
    document = pymupdf.open()
    typed = document.new_page(width=400, height=280)
    typed.insert_text((55, 100), "Arts: Amelie Fontaine", fontsize=11)
    scanned = document.new_page(width=400, height=280)
    scanned.insert_image(pymupdf.Rect(*SCAN_RECT), pixmap=_bar_pixmap())
    data = document.tobytes()
    document.close()
    return data


def _read_top_bar(image, *, width: int, height: int):
    """Claim the bar in the large scan, and read nothing in the small logo.

    The two are told apart by raster width, not by the page rect: the reader
    only ever sees pixels. At the default density the scan renders about
    830px wide and the logo about 250px.
    """
    if width < 400:
        return ()
    return (
        pdf_ocr.OcrLine(
            "Jean-Baptiste Moreau",
            (0.05 * width, 0.35 * height, 0.95 * width, 0.65 * height),
            0.98,
        ),
    )


def _read_two_words(image, *, width: int, height: int):
    """Read the bar as two words, so the space between them is under test."""
    if width < 400:
        return ()
    return (
        pdf_ocr.OcrLine(
            "Moreau samen",
            (0.05 * width, 0.35 * height, 0.95 * width, 0.65 * height),
            0.98,
            tuple(index / 12 for index in range(13)),
        ),
    )


def test_a_masked_scan_loses_the_pixels_not_just_the_look() -> None:
    """The whole point of the feature, and the one test that proves it."""
    data = _scan_bytes()
    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    text = prepared.texts[0].text
    start = text.index("Jean-Baptiste Moreau")
    span = MaskSpan(
        start,
        start + len("Jean-Baptiste Moreau"),
        "NAME",
        "model",
        page=0,
        confidence=0.98,
    )

    before = _image_ink(data, SCAN_RECT)
    assert before > 0.05, "the fixture must actually carry ink"
    for style, mode in (("invisible", "pseudonymize"), ("bold_chips", "detect")):
        masked = redact_document(
            prepared, [span], redaction_style=style, redaction_mode=mode
        )
        after = _image_ink(masked, SCAN_RECT)
        assert after < before * 0.1, (style, before, after)


def test_an_image_ocr_read_nothing_from_keeps_its_pixels() -> None:
    """Pass two redacts per glyph box, not per page.

    `IMAGE_PIXELS` is applied to the whole page, so what spares an image is
    that no redaction rectangle lands on it. A letterhead beside the text of a
    scan has to come through untouched, or every scanned letter loses its
    letterhead the moment one name on it is masked.
    """
    data = _scan_bytes(logo=True)
    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    text = prepared.texts[0].text
    start = text.index("Jean-Baptiste Moreau")
    span = MaskSpan(
        start,
        start + len("Jean-Baptiste Moreau"),
        "NAME",
        "model",
        page=0,
        confidence=0.99,
    )

    logo_before = _image_ink(data, LOGO_RECT)
    assert logo_before > 0.05, "the logo fixture must carry ink"
    masked = redact_document(prepared, [span], redaction_style="bold_chips")
    assert (
        _image_ink(masked, LOGO_RECT) == logo_before
    ), "an image no redaction touches must not change"
    assert _image_ink(masked, SCAN_RECT) < _image_ink(data, SCAN_RECT) * 0.1


def test_a_page_that_has_a_text_layer_is_never_read_by_ocr() -> None:
    """OCR is a fallback, not a second opinion.

    A page that yields characters is read from its text layer alone. Reading
    it again as a raster would put a lossier transcript of the same words
    beside the exact one, and any image on such a page keeps the refusal that
    predates OCR -- so turning `PDF_OCR` on cannot quietly widen what the
    service accepts beyond pages that are pictures of text.
    """

    def _must_not_run(image, *, width: int, height: int):
        raise AssertionError("a page with a text layer must not reach OCR")

    data = _scan_bytes(layer=("Arts: Amelie Fontaine",), logo=True)
    try:
        prepare_document(data, "application/pdf", ocr=_must_not_run)
    except ValueError as exc:
        assert "image content cannot be masked" in str(exc), exc
    else:
        raise AssertionError("an image the fallback never read must be refused")


def test_the_fallback_is_decided_page_by_page() -> None:
    """One document, one page of each kind, and the right rule on each.

    Whole-document gating would either refuse the scan's neighbour or OCR it,
    and both are wrong. The provenance marks are the proof: they follow the
    regions OCR wrote into, so a born-digital page carries none.
    """
    data = _typed_then_scanned_bytes()
    seen: list[int] = []

    def _reader(image, *, width: int, height: int):
        seen.append(width)
        return _read_top_bar(image, width=width, height=height)

    prepared = prepare_document(data, "application/pdf", ocr=_reader)
    assert len(seen) == 1, "only the page with no text layer may be rasterised"
    assert "Amelie Fontaine" in prepared.texts[0].text
    assert "Jean-Baptiste Moreau" in prepared.texts[1].text
    assert not any(
        glyph.ocr for glyph in prepared.texts[0].glyphs if glyph is not None
    ), "a page read from its own text layer carries no OCR provenance"
    assert all(
        glyph.ocr for glyph in prepared.texts[1].glyphs if glyph is not None
    ), "every glyph on a scanned page came from OCR"


def test_the_audit_records_which_spans_came_from_ocr() -> None:
    """A misread scan and a detector miss look identical in the output."""
    prepared = prepare_document(
        _typed_then_scanned_bytes(), "application/pdf", ocr=_read_top_bar
    )
    spans = []
    for page, value in ((0, "Amelie Fontaine"), (1, "Jean-Baptiste Moreau")):
        text = prepared.texts[page].text
        start = text.index(value)
        spans.append(
            MaskSpan(
                start, start + len(value), "NAME", "model", page=page, confidence=0.99
            )
        )

    audit: list[dict[str, object]] = []
    redact_document(prepared, spans, audit, redaction_style="bold_chips")
    layers = {entry["text"]: entry["source_layer"] for entry in audit}
    assert layers["Jean-Baptiste Moreau"] == "ocr"
    assert (
        layers["Amelie Fontaine"] == "model"
    ), "a text-layer span keeps its own source"


def test_an_ocr_replacement_is_set_at_the_size_it_replaces() -> None:
    """`fit_text`'s 8.5pt default would be a fifth of the type on a big page."""
    data = _scan_bytes()
    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    text = prepared.texts[0].text
    start = text.index("Jean-Baptiste Moreau")
    span = MaskSpan(start, start + 20, "NAME", "model", page=0, confidence=0.98)
    masked = redact_document(prepared, [span], redaction_style="invisible")

    document = pymupdf.open(stream=masked, filetype="pdf")
    try:
        sizes = [
            span_dict["size"]
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span_dict in line["spans"]
        ]
    finally:
        document.close()
    assert sizes, "the pseudonym must be drawn"
    # The OCR line is 30pt tall here, so the synthesised size is ~21pt. The
    # 8.5pt chip default would be unmistakably smaller.
    assert max(sizes) > 12.0, sizes


def test_the_injected_text_layer_makes_the_scan_extractable() -> None:
    """The point of injecting rather than synthesising downstream.

    A masked scan used to be a picture of a masked document: nothing in it
    could be searched, selected or copied, and the only extractable strings
    were the chips drawn on top. Writing a real text layer first makes the
    page behave like any other PDF, and lets every stage after it stay
    ignorant that OCR happened.
    """
    data = _scan_bytes()
    before = pymupdf.open(stream=data, filetype="pdf")
    try:
        assert before[0].get_text().strip() == "", "the fixture must start as raster"
    finally:
        before.close()

    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    assert prepared.content != data, "the prepared bytes carry the new layer"
    after = pymupdf.open(stream=prepared.content, filetype="pdf")
    try:
        assert "Jean-Baptiste Moreau" in after[0].get_text()
        # Searchable, not merely extractable: the glyphs carry real positions.
        assert after[0].search_for("Moreau"), "the injected text must be findable"
    finally:
        after.close()

    # And it is invisible: render mode 3 paints nothing, so the page still
    # looks like the scan it is.
    assert pdf_ocr._INVISIBLE == 3


def test_injected_glyphs_span_the_box_they_were_read_from() -> None:
    """Sizing to height alone leaves the tail of a line unredacted.

    The glyphs are what redaction covers, so if they stop short of the box
    OCR read, the last words of that line stay legible on the scan under the
    chip. Fitting the height and stretching to the width covers both.
    """
    data = _scan_bytes()
    _, regions = pdf_ocr.inject_text_layer(data, _read_top_bar)
    assert regions and regions[0], "the fixture must produce a written region"
    box = regions[0][0]

    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    glyphs = [glyph for glyph in prepared.texts[0].glyphs if glyph is not None]
    assert glyphs
    left = min(glyph.bbox[0] for glyph in glyphs)
    right = max(glyph.bbox[2] for glyph in glyphs)
    assert abs(left - box[0]) < 1.0, (left, box)
    assert abs(right - box[2]) < 1.0, (right, box)
    # Type sized to the line, not to a chip default: this page's lines are
    # ~30pt tall, and 8.5pt glyphs would put every box in the wrong place.
    assert glyphs[0].size > 12.0, glyphs[0].size


def test_only_the_regions_ocr_wrote_into_are_marked() -> None:
    """Provenance comes from what was written, not from what sits on an image.

    Those two agree on the words OCR read and come apart on a second image it
    read nothing from: a logo is inside an image block without being OCR
    output. Marking by geometry would route glyphs near it to the pass that
    erases image pixels.
    """
    data = _scan_bytes(logo=True)
    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    text = prepared.texts[0].text
    glyphs = prepared.texts[0].glyphs
    assert any(glyph is not None for glyph in glyphs), "the page must produce glyphs"

    start = text.index("Jean-Baptiste Moreau")
    marked = {
        glyph.ocr
        for glyph in glyphs[start : start + len("Jean-Baptiste Moreau")]
        if glyph is not None
    }
    assert marked == {True}, "OCR output must be marked"

    written = pdf_ocr.inject_text_layer(data, _read_top_bar)[1][0]
    assert written, "the fixture must have written a region"
    logo = pymupdf.Rect(*LOGO_RECT)
    assert not any(
        logo.intersects(pymupdf.Rect(*box)) for box in written
    ), "the logo must not be inside a written region"


def test_the_original_text_does_not_survive_in_the_layer_that_read_it() -> None:
    """The injected layer is a second copy of the PII, and it must go too.

    This one shipped. Once OCR output became a real text layer, the pass that
    erases image pixels was still passing `PDF_REDACT_TEXT_NONE` -- correct
    when that output was synthesised geometry with nothing in the file behind
    it, and a leak the moment it was not. The page looked properly redacted:
    pixels destroyed, chips drawn, nothing visible. Selecting the text and
    copying it returned the patient's actual name.

    Checking the rendered page cannot catch this, and neither can checking the
    image stream. Only reading the text back does.
    """
    data = _scan_bytes()
    prepared = prepare_document(data, "application/pdf", ocr=_read_top_bar)
    text = prepared.texts[0].text
    assert "Jean-Baptiste Moreau" in text
    start = text.index("Jean-Baptiste Moreau")
    span = MaskSpan(start, start + 20, "NAME", "model", page=0, confidence=0.98)

    for style, mode in (
        ("bold_chips", "detect"),
        ("clinical_soft", "pseudonymize"),
        ("invisible", "pseudonymize"),
    ):
        masked = redact_document(
            prepared, [span], redaction_style=style, redaction_mode=mode
        )
        document = pymupdf.open(stream=masked, filetype="pdf")
        try:
            extracted = "\n".join(page.get_text() for page in document)
            assert "Jean-Baptiste Moreau" not in extracted, (style, mode)
            assert "Moreau" not in extracted, (style, mode)
            # ...and it is not merely invisible: searching finds nothing.
            assert not document[0].search_for("Moreau"), (style, mode)
        finally:
            document.close()


def test_the_separator_survives_the_word_it_followed_being_redacted() -> None:
    """A space carried on the end of its word dies with that word.

    The injected layer has to write spaces, because its runs are stretched to
    cover their ink and leave no gap for an extractor to infer one from. But a
    space written as part of `"71 "` is removed along with `71`, and the
    pseudonym drawn in its place then abuts what follows -- `83ans,` where the
    scan read `71 ans,`. Writing the separator as its own text object is what
    keeps the two words apart when one of them is replaced.
    """
    data = _scan_bytes()
    prepared = prepare_document(data, "application/pdf", ocr=_read_two_words)
    text = prepared.texts[0].text
    assert "Moreau samen" in text, text

    start = text.index("Moreau")
    span = MaskSpan(start, start + len("Moreau"), "NAME", "model", page=0)
    masked = redact_document(prepared, [span], redaction_style="invisible")

    document = pymupdf.open(stream=masked, filetype="pdf")
    try:
        page = document[0].get_text("text")
    finally:
        document.close()
    assert "Moreau" not in page
    assert "samen" in page, page
    joined = "".join(page.split("\n"))
    assert "samen" in joined and not any(
        part.endswith("samen") and part != "samen" and not part[-6:-5].isspace()
        for part in joined.split(" ")
    ), f"the word after the replacement must keep its separator: {page!r}"


def test_a_header_row_reads_across_and_body_lines_do_not_merge() -> None:
    """Detection orders boxes by their top edge, which is not reading order.

    Three labels printed across one row have tops a pixel or two apart, so a
    strict sort by `y` returns them in whatever order those pixels fall. The
    tolerance that fixes it has to be tight enough that two consecutive body
    lines still stay two rows.
    """

    def line(text, x0, y0, x1, y1):
        return pdf_ocr.OcrLine(text, (x0, y0, x1, y1), 0.9)

    header = [
        line("DATE :", 300.0, 40.6, 380.0, 52.6),
        line("PATIENT :", 40.0, 40.0, 120.0, 52.0),
        line("RESPONSABLE :", 170.0, 41.2, 280.0, 53.2),
    ]
    body = [line("tweede regel", 40.0, 70.0, 200.0, 82.0)]
    expected = ["PATIENT :", "RESPONSABLE :", "DATE :", "tweede regel"]
    ordered = pdf_ocr.reading_order(header + body)
    assert [entry.text for entry in ordered] == expected, [e.text for e in ordered]

    # And `read_page` has to apply it, or the layer is written in detection
    # order and the copied text carries the swap into every masked scan.
    document = pymupdf.open(stream=_scan_bytes(), filetype="pdf")
    try:
        page = document[0]
        scale = page.rect.width / 400.0

        def _reader(image, *, width: int, height: int):
            # Map the fixture's page-point boxes into the raster it renders.
            factor = width / (SCAN_RECT[2] - SCAN_RECT[0]) / scale
            return tuple(
                pdf_ocr.OcrLine(
                    entry.text,
                    tuple(value * factor for value in entry.bbox),
                    entry.confidence,
                )
                for entry in header + body
            )

        read = pdf_ocr.read_page(page, _reader)
    finally:
        document.close()
    assert [entry.text for entry in read] == expected, [e.text for e in read]

    stacked = [
        line("eerste regel", 40.0, 40.0, 200.0, 52.0),
        line("tweede regel", 40.0, 54.0, 200.0, 66.0),
    ]
    assert [entry.text for entry in pdf_ocr.reading_order(stacked)] == [
        "eerste regel",
        "tweede regel",
    ], "lines a line apart are two rows, not one"


def test_reading_order_does_not_depend_on_the_order_it_was_given() -> None:
    """Masking has to be byte-reproducible, so ordering must be a function."""

    def line(text, x0, y0):
        return pdf_ocr.OcrLine(text, (x0, y0, x0 + 60.0, y0 + 12.0), 0.9)

    lines = [
        line("a", 40, 40),
        line("b", 120, 40.4),
        line("c", 40, 70),
        line("d", 200, 39.7),
    ]
    expected = [entry.text for entry in pdf_ocr.reading_order(lines)]
    for rotation in range(1, len(lines)):
        rotated = lines[rotation:] + lines[:rotation]
        assert [
            entry.text for entry in pdf_ocr.reading_order(rotated)
        ] == expected, rotation


def test_a_redaction_does_not_take_the_line_under_it() -> None:
    """Two boxes that overlap, and a whole line of a letter deleted.

    A detection box is unclipped and `normalise_heights` then hands a cluster
    its largest box -- both so a chip covers a line's ink completely. On the
    real French scan that made the header's organisation box run 7pt into the
    specialty line under it. `apply_redactions` removes every glyph whose box
    *intersects* the rectangle, so masking the organisation deleted
    "Gastro-entérologie" as well: content gone from a masked document, named
    by no detector and reported by nothing.
    """

    def line(text, x0, y0, x1, y1):
        return pdf_ocr.OcrLine(text, (x0, y0, x1, y1), 0.9)

    # Boxes 51 tall on a 44pt pitch, which is what the real scan produced.
    stacked = [
        line("Centre Hospitalier", 40.0, 584.0, 300.0, 635.0),
        line("Gastro-enterologie", 40.0, 628.0, 300.0, 679.0),
    ]
    separated = pdf_ocr.separate_rows(stacked)
    assert [entry.text for entry in separated] == [
        entry.text for entry in stacked
    ], "separating rows may not reorder or drop them"
    first, second = (entry.bbox for entry in separated)
    assert first[3] <= second[1], f"boxes must not overlap: {first} {second}"
    assert first[1] == 584.0 and second[3] == 679.0, (
        "only the overlap is given up, never the outer edges: " f"{first} {second}"
    )

    # Columns of one row share a centre and are not stacked lines.
    row = [
        line("PATIENT :", 40.0, 40.0, 120.0, 52.0),
        line("DATE :", 300.0, 40.0, 380.0, 52.0),
    ]
    assert [entry.bbox for entry in pdf_ocr.separate_rows(row)] == [
        entry.bbox for entry in row
    ], "boxes side by side are not each other's neighbours"


def test_the_line_below_a_masked_one_survives_end_to_end() -> None:
    """The same defect, through the pipeline rather than at the box level."""
    data = _scan_bytes()

    def _reader(image, *, width: int, height: int):
        scale = width / (SCAN_RECT[2] - SCAN_RECT[0])
        # Two lines whose unclipped boxes overlap, as a real detector's do.
        return (
            pdf_ocr.OcrLine(
                "Sint Lucas", tuple(v * scale for v in (10.0, 6.0, 150.0, 30.0)), 0.9
            ),
            pdf_ocr.OcrLine(
                "Pneumologie", tuple(v * scale for v in (10.0, 26.0, 150.0, 50.0)), 0.9
            ),
        )

    prepared = prepare_document(data, "application/pdf", ocr=_reader)
    text = prepared.texts[0].text
    assert "Sint Lucas" in text and "Pneumologie" in text, text

    start = text.index("Sint Lucas")
    span = MaskSpan(
        start, start + len("Sint Lucas"), "ORGANIZATION_NAME", "model", page=0
    )
    masked = redact_document(prepared, [span], redaction_style="invisible")
    document = pymupdf.open(stream=masked, filetype="pdf")
    try:
        page = document[0].get_text("text")
    finally:
        document.close()
    assert "Sint Lucas" not in page, page
    assert (
        "Pneumologie" in page
    ), "redacting one line must not delete the line beneath it: " + repr(page)


def _recording_reader(seen: list[tuple[int, int]]):
    """A reader that notes the raster it was handed, then reads it whole.

    It has to return a line: a scan OCR finds nothing on is refused outright
    as image content that cannot be masked safely, which would hide the
    measurement this is here to take.
    """

    def reader(image, *, width: int, height: int):
        seen.append((width, height))
        return (
            pdf_ocr.OcrLine(
                "Dr. Amelie Fontaine", (0.0, 0.0, float(width), float(height)), 0.97
            ),
        )

    return reader


def test_a_configured_dpi_reaches_the_rasteriser() -> None:
    # The bounds were validated by `load_settings`, documented in
    # .env.example and set in deploy/compose.yml, while `prepare_document`
    # called `inject_text_layer` with none of them -- so every one of them
    # fell back to a keyword default and `PDF_OCR_DPI=300` rendered at 200.
    # This fails if that connection is broken again.
    data = _scan_bytes()
    rasters: dict[int, list[tuple[int, int]]] = {}
    for dpi in (72, 300):
        seen: list[tuple[int, int]] = []
        prepare_document(
            data,
            "application/pdf",
            ocr=_recording_reader(seen),
            ocr_bounds=pdf_ocr.OcrBounds(dpi=dpi, max_pixels=4096),
        )
        assert seen, f"OCR was never called at dpi={dpi}"
        rasters[dpi] = seen

    assert rasters[72] != rasters[300], (
        "the same raster came back for 72 and 300 dpi, so the configured "
        f"value is not reaching render_zoom: {rasters}"
    )
    # Higher density has to mean more pixels, not merely different ones.
    assert rasters[300][0][0] > rasters[72][0][0], rasters


def test_every_documented_ocr_bound_is_read_from_settings() -> None:
    # A rename on either side silently reinstates the defaults, which is the
    # failure this whole family had. Each field must move when its setting does.
    class Configured:
        pdf_ocr_dpi = 111
        pdf_ocr_max_pixels = 222
        pdf_ocr_min_block_points = 3.5
        pdf_ocr_min_line_confidence = 0.44
        pdf_ocr_max_lines = 555

    bounds = pdf_ocr.OcrBounds.from_settings(Configured())
    assert bounds == pdf_ocr.OcrBounds(
        dpi=111,
        max_pixels=222,
        min_block_points=3.5,
        min_confidence=0.44,
        max_lines=555,
    ), bounds
    # And an object carrying none of them still yields the documented defaults.
    assert pdf_ocr.OcrBounds.from_settings(object()) == pdf_ocr.OcrBounds()


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
