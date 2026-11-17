#!/usr/bin/env python3
"""Pin where a replacement lands in the content stream, and what that costs.

A PDF extracts in the order its drawing operators appear, so this is what
decides whether someone copying a masked document reads the pseudonym in the
sentence it belongs to or finds it piled at the bottom of the page.

Two properties matter and are asserted separately: the order has to change,
and *nothing else* may. The rendered page, the words on it and the bytes of a
repeat run are all held fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, prepare_document, redact_document
from masking_engine.app.services import pdf_content_order


def _serialise(document: pymupdf.Document) -> bytes:
    """Return fixture bytes that do not change from one run to the next.

    The surrogate seed is `sha256` of the document, so a fixture whose bytes
    move produces a different pseudonym on every run -- and a pseudonym of a
    different width lands a chip in a different place. `no_new_id` holds the
    trailer `/ID` still and the dates are pinned, which is what makes a
    failure here reproducible rather than one run in three.
    """
    document.set_metadata(
        {"creationDate": "D:20240101000000Z", "modDate": "D:20240101000000Z"}
    )
    return document.tobytes(no_new_id=True)


def _document(rotation: int = 0, *, per_word: bool = False) -> bytes:
    """A three-line page with a name in the middle of the first line.

    `per_word` writes each word as its own text object, which is what
    `inject_text_layer` does for a scan. The two shapes are kept apart because
    they are what decides how precisely a replacement can be placed.
    """
    document = pymupdf.open()
    page = document.new_page(width=420, height=200)
    lines = (
        "Patient Jan Janssens werd gezien op 29-11-2009.",
        "Vervolgens ging hij naar huis.",
        "Met vriendelijke groet.",
    )
    for index, line in enumerate(lines):
        y = 60 + index * 30
        if not per_word:
            page.insert_text((40, y), line, fontsize=11)
            continue
        font = pymupdf.Font("helv")
        x = 40.0
        for word in line.split(" "):
            page.insert_text((x, y), word, fontsize=11)
            x += font.text_length(word + " ", fontsize=11)
    if rotation:
        page.set_rotation(rotation)
    data = _serialise(document)
    document.close()
    return data


def _run_split_document() -> bytes:
    """A line drawn as several show operators inside one text object.

    This is what a real producer emits and what redaction then leaves behind:
    the removed value's operator goes, and the halves either side stay as
    separate operators with a kern between them. `_document` is the other
    shape -- one show operator for the whole line -- which mupdf's own writer
    produces and which cannot be split at all.
    """
    document = pymupdf.open()
    page = document.new_page(width=420, height=200)
    page.insert_text((40, 60), "seed", fontsize=11)
    resource = page.get_fonts(full=True)[0][4]
    stream = (
        f"q BT/{resource} 11 Tf 40 140 TD (Patient )Tj (Jan Janssens)Tj "
        f"( werd gezien op 29-11-2009.)Tj ET Q "
        f"q BT/{resource} 11 Tf 40 110 TD (Vervolgens ging hij naar huis.)Tj ET Q"
    ).encode("latin-1")
    document.update_stream(page.get_contents()[0], stream)
    data = _serialise(document)
    document.close()
    return data


def _one_object_document() -> bytes:
    """A whole page drawn inside a single `BT ... ET`, value on a later line.

    mupdf's own writer emits this -- it is what every page looks like once
    `apply_redactions` has rewritten it -- and the value deliberately sits
    below the first line, which is the only position that tells the defect
    apart from working code: read as one object the page sits at its *first*
    line, so a replacement anywhere below it finds no base text to be ordered
    against and lands at the foot.
    """
    document = pymupdf.open()
    page = document.new_page(width=420, height=200)
    page.insert_text((40, 60), "seed", fontsize=11)
    resource = page.get_fonts(full=True)[0][4]
    stream = (
        f"q BT/{resource} 11 Tf 40 160 TD (Datum: 29-11-2009)Tj "
        f"0 -30 TD (Patient Jan Janssens werd gezien.)Tj "
        f"0 -30 TD (Met vriendelijke groet.)Tj ET Q"
    ).encode("latin-1")
    document.update_stream(page.get_contents()[0], stream)
    data = _serialise(document)
    document.close()
    return data


def _collision_document() -> bytes:
    """A page where one replacement's insertion point is another's object.

    The first line is a single show operator, so its value falls back to being
    placed before the next object -- and that next object is the second line,
    which *is* splittable and gets rewritten in place. The two therefore claim
    the same byte offset, which is the case that silently lost a pseudonym.
    """
    document = pymupdf.open()
    page = document.new_page(width=460, height=200)
    page.insert_text((40, 60), "seed", fontsize=11)
    resource = page.get_fonts(full=True)[0][4]
    stream = (
        f"q BT/{resource} 11 Tf 40 160 TD (Datum: 29-11-2009)Tj ET Q "
        f"q BT/{resource} 11 Tf 40 130 TD (Patient )Tj (Jan Janssens)Tj "
        f"( werd gezien.)Tj ET Q"
    ).encode("latin-1")
    document.update_stream(page.get_contents()[0], stream)
    data = _serialise(document)
    document.close()
    return data


def _masked(data: bytes, **kwargs) -> bytes:
    prepared = prepare_document(data, "application/pdf")
    text = prepared.texts[0].text
    start = text.index("Jan Janssens")
    span = MaskSpan(
        start, start + len("Jan Janssens"), "NAME", "model", page=0, confidence=0.99
    )
    return redact_document(prepared, [span], **kwargs)


def _lines(data: bytes) -> list[str]:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [
            line for line in document[0].get_text("text").split("\n") if line.strip()
        ]
    finally:
        document.close()


def _render(data: bytes) -> list[np.ndarray]:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        pages = []
        for page in document:
            pixmap = page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
            pages.append(
                np.frombuffer(pixmap.samples, np.uint8)
                .reshape(pixmap.height, pixmap.width)
                .astype(np.int16)
            )
        return pages
    finally:
        document.close()


def _chip_boxes(data: bytes) -> list[list[pymupdf.Rect]]:
    """Return the filled rectangles each page had painted on it.

    `_render` draws at 1:1, and PyMuPDF reports drawings in the same
    top-left page space the pixmap uses, so a rect here indexes the raster
    directly.
    """
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [
            [drawing["rect"] for drawing in page.get_drawings() if drawing.get("fill")]
            for page in document
        ]
    finally:
        document.close()


def _without_reordering(data: bytes, **kwargs) -> bytes:
    original = pdf_content_order.restore
    pdf_content_order.restore = lambda *args, **kw: False
    try:
        return _masked(data, **kwargs)
    finally:
        pdf_content_order.restore = original


def test_a_replacement_lands_inside_its_own_line_when_words_are_separate() -> None:
    """The precise case, and the one a scan always gets.

    `inject_text_layer` writes a text object per word, so there is a seam
    between every pair of them and the pseudonym goes exactly where the value
    it replaces was.
    """
    data = _document(per_word=True)
    text = " ".join(_lines(_masked(data)))
    assert "Jan Janssens" not in text
    head, _, tail = text.partition(" werd gezien")
    assert head.startswith("Patient "), text
    assert head.removeprefix(
        "Patient "
    ).strip(), f"the pseudonym must sit between 'Patient' and 'werd': {text!r}"
    assert tail.strip().startswith("op 29-11-2009."), text


def test_a_replacement_lands_inside_the_line_it_was_cut_out_of() -> None:
    """Redaction leaves a seam, and the replacement goes through it.

    The object is closed at that point, the pseudonym drawn, and the object
    reopened at the x where the text resumes -- which is known from the glyph
    geometry, so no font metrics are needed and an embedded subset font is no
    obstacle.
    """
    text = " ".join(_lines(_masked(_run_split_document())))
    assert "Jan Janssens" not in text
    head, marker, tail = text.partition("werd gezien")
    assert marker, text
    assert head.strip().startswith("Patient"), text
    assert (
        head.strip().removeprefix("Patient").strip()
    ), f"the pseudonym must read inside its own sentence: {text!r}"
    assert tail.strip().startswith("op 29-11-2009."), text


def test_a_line_with_no_seam_still_reads_as_one_sentence() -> None:
    """One show operator for the whole line is the shape that cannot split.

    mupdf's own writer emits it, and redaction turns the removed value into a
    kerning number inside a single `TJ` array rather than an operator
    boundary, so there is nowhere inside the object for a pseudonym to go.

    The reflow is what rescues it. A pseudonym longer than the value pushes
    the rest of the line right, and that tail is redrawn as a unit of its own
    -- so the line arrives here as three pieces the splice can order by
    geometry, and the sentence reassembles whole. Before that it was the
    weaker guarantee of landing merely *beside* the line, and asserting the
    weaker one would let the sentence come apart again unnoticed.
    """
    lines = _lines(_masked(_document()))
    assert "Jan Janssens" not in " ".join(lines)
    sentence = [line for line in lines if line.startswith("Patient")]
    assert len(sentence) == 1, lines
    head, marker, tail = sentence[0].partition(" werd gezien op 29-11-2009.")
    assert marker, sentence
    assert not tail, sentence
    assert head.removeprefix("Patient").strip(), sentence


def test_no_replacement_is_ever_dropped() -> None:
    """Splitting an object in place must not swallow another pseudonym.

    One replacement's insertion point can be the very object rewritten for
    another. Skipping it leaves the page redacted with nothing in the value's
    place -- the original gone and no pseudonym drawn, which reads as a
    masking failure rather than a layout one.
    """
    data = _collision_document()
    prepared = prepare_document(data, "application/pdf")
    text = prepared.texts[0].text
    spans = [
        MaskSpan(
            text.index(value),
            text.index(value) + len(value),
            label,
            "model",
            page=0,
            confidence=0.99,
        )
        for value, label in (("29-11-2009", "DATE"), ("Jan Janssens", "NAME"))
    ]
    masked = redact_document(prepared, spans, redaction_style="invisible")

    def _shape(data: bytes) -> str:
        return "".join(sorted("".join(_lines(data)).replace(" ", "")))

    original = pdf_content_order.restore
    pdf_content_order.restore = lambda *args, **kw: False
    try:
        plain = redact_document(
            prepare_document(data, "application/pdf"),
            spans,
            redaction_style="invisible",
        )
    finally:
        pdf_content_order.restore = original

    assert _shape(masked) == _shape(plain), (
        "the reorder must move characters, never lose them: "
        f"{_lines(masked)} vs {_lines(plain)}"
    )
    assert "Jan Janssens" not in " ".join(_lines(masked))
    assert "29-11-2009" not in " ".join(_lines(masked))


def test_the_page_only_changes_where_a_chip_already_overlapped() -> None:
    """Reordering is a change to the stream. It is almost never one to the page.

    `invisible` substitutes in place and is pixel-identical, always. A chip
    style is identical too, except where a chip's chrome overhangs the text
    beside it: the chip used to be appended last and so painted over that
    text, and a chip spliced back into reading order is painted before it
    instead. The page changes, in the right direction -- the words win over
    the box -- and only ever inside a box that was already sitting on them.

    Measured on the three real samples the difference is nil, because a chip
    there does not reach past the whitespace around the value it replaces.
    """
    for data in (_document(), _run_split_document(), _collision_document()):
        for style, mode in (
            ("invisible", "pseudonymize"),
            ("clinical_soft", "pseudonymize"),
            ("clinical_soft", "detect"),
        ):
            kwargs = {"redaction_style": style, "redaction_mode": mode}
            on = _masked(data, **kwargs)
            off = _without_reordering(data, **kwargs)
            for index, (drawn_on, drawn_off) in enumerate(
                zip(_render(on), _render(off))
            ):
                changed = np.abs(drawn_on - drawn_off) > 0
                if not changed.any():
                    continue
                assert style != "invisible", (
                    "an in-place substitution paints nothing over anything, "
                    f"so it may not move a pixel: {style}/{mode}"
                )
                allowed = np.zeros_like(changed)
                for box in _chip_boxes(on)[index]:
                    # One pixel of slack for the edge the rasteriser shares
                    # between the chip and whatever it was drawn against.
                    allowed[
                        max(int(box.y0) - 1, 0) : int(box.y1) + 2,
                        max(int(box.x0) - 1, 0) : int(box.x1) + 2,
                    ] = True
                outside = changed & ~allowed
                assert not outside.any(), (
                    f"{style}/{mode} changed the page outside every chip, at "
                    f"{list(zip(*np.nonzero(outside)))[:5]}"
                )


def test_a_chip_no_longer_covers_the_words_it_overhangs() -> None:
    """The one page change reordering makes, pinned as a deliberate one.

    A chip is drawn a little wider than the value it replaces, so on a line
    that continues after it the chip reaches over the first character or two.
    Appended last it covered them. Spliced into place it is drawn before the
    half-line that follows, which then paints over the chip -- so the ordering
    fix makes the page slightly more legible rather than less.

    Read with `chip_reflow=False`, because that is the case this ordering
    protects. With reflow on there is no overhang to order against: the words
    move aside instead, and they are redrawn rather than left in the base
    stream, so there is no untouched "werd gezien" in it to compare against.
    Reflow cannot reach every run -- rotated text, a scanned page, a line with
    no room -- and this is what those still rely on.
    """
    data = _collision_document()
    kwargs = {
        "redaction_style": "clinical_soft",
        "redaction_mode": "pseudonymize",
        "chip_reflow": False,
    }
    on = _masked(data, **kwargs)
    off = _without_reordering(data, **kwargs)

    boxes = _chip_boxes(on)[0]
    assert boxes, "the fixture must actually paint a chip"

    def order(data: bytes) -> tuple[int, int]:
        document = pymupdf.open(stream=data, filetype="pdf")
        try:
            stream = b"\n".join(
                document.xref_stream(xref) for xref in document[0].get_contents()
            )
        finally:
            document.close()
        return stream.index(b" B\n"), stream.index(b"werd gezien")

    fill_on, text_on = order(on)
    fill_off, text_off = order(off)
    assert fill_on < text_on, "the chip must be drawn before the words it overhangs"
    assert fill_off > text_off, "and after them when the replacement is appended"


def test_a_value_at_the_end_of_a_line_still_lands_in_place() -> None:
    """The slot that only exists because the line ran out.

    "par le Dr. <name> le <date>" ends with the date, so redaction leaves no
    operator after it to seam against. Without a slot at the close of the
    object both replacements on that line fell back to the foot of the page --
    on the real French letter that was an age, a wrapped organisation and a
    clinician's name, all dumped after the text.
    """
    document = pymupdf.open()
    page = document.new_page(width=460, height=200)
    page.insert_text((40, 60), "seed", fontsize=11)
    resource = page.get_fonts(full=True)[0][4]
    stream = (
        f"q BT/{resource} 11 Tf 40 160 TD (Gezien door Dr. )Tj (Jan Janssens)Tj "
        f"( op )Tj (29-11-2009)Tj ET Q "
        f"q BT/{resource} 11 Tf 40 130 TD (Met vriendelijke groet.)Tj ET Q"
    ).encode("latin-1")
    document.update_stream(page.get_contents()[0], stream)
    data = _serialise(document)
    document.close()

    prepared = prepare_document(data, "application/pdf")
    text = prepared.texts[0].text
    spans = [
        MaskSpan(
            text.index(value),
            text.index(value) + len(value),
            label,
            "model",
            page=0,
            confidence=0.99,
        )
        for value, label in (("Jan Janssens", "NAME"), ("29-11-2009", "DATE"))
    ]
    lines = _lines(redact_document(prepared, spans, redaction_style="invisible"))
    joined = " ".join(lines)
    assert "Jan Janssens" not in joined and "29-11-2009" not in joined
    head, marker, tail = joined.partition(" op ")
    assert marker, joined
    assert head.strip().removeprefix("Gezien door Dr.").strip(), joined
    closing = tail.split("Met vriendelijke")[0].strip()
    assert closing, f"the value that ended the line must stay on it: {lines}"


def test_masking_stays_byte_reproducible() -> None:
    """NiFi replays an idempotency key, so two runs must agree exactly."""
    data = _document()
    assert _masked(data) == _masked(data)


def test_a_seam_is_never_taken_between_an_operator_and_its_operands() -> None:
    """The bug that cost three words on a real scan.

    Operands are collected before their operator, so the whitespace inside
    `2 0 0 1 5 0 cm` sits at the identity matrix too. Splicing there cuts the
    operand list and the text object it belonged to renders elsewhere or not
    at all.
    """
    stream = b"q 2 0 0 1 5 0 cm BT /F1 10 Tf 0 0 Td (word) Tj ET Q\n"
    objects, seams, _ = pdf_content_order.scan(stream)
    assert len(objects) == 1
    inside = stream.index(b"2 0 0 1"), stream.index(b" cm") + 1
    assert not [
        seam for seam in seams if inside[0] < seam < inside[1]
    ], f"a seam landed inside the cm operands: {seams}"
    # The origin is the text position carried through the cm: 0,0 -> 5,0.
    assert objects[0][1:3] == (5.0, 0.0), objects


def test_the_matrix_is_carried_through_nesting() -> None:
    """A text object's page position is its own matrix, not just its `Td`."""
    stream = b"q 1 0 0 1 10 20 cm q 2 0 0 2 0 0 cm BT 3 4 Td (x) Tj ET Q Q\n"
    objects, _, _ = pdf_content_order.scan(stream)
    assert objects[0][1:3] == (16.0, 28.0), objects


def test_anything_unfamiliar_leaves_the_page_alone() -> None:
    """Every refusal is a silent no-op, never a repair attempt."""
    for stream, why in (
        (b"BI /W 2 /H 2 ID \x00\xff\x00\xff EI\n", "inline image"),
        (b"q BT 0 0 Td (x) Tj ET\n", "stream ends mid-object"),
        (b"q BT 0 0 Td (x) Tj ET Q Q\n", "unbalanced Q"),
        (b"BT BT 0 0 Td (x) Tj ET ET\n", "nested BT"),
        (b"BT (x) Tj ET\n", "no positioning operator"),
    ):
        try:
            pdf_content_order.scan(stream)
        except Exception as exc:
            assert why in str(exc), (why, exc)
        else:
            raise AssertionError(f"{why!r} should not have scanned")


def test_a_rotated_page_is_left_as_it_is() -> None:
    """Redaction geometry is unrotated and the raster is not; do not guess."""
    data = _document(rotation=90)
    assert _masked(data) == _without_reordering(data)


def test_a_literal_string_cannot_be_read_as_operators() -> None:
    """`(BT q cm)` is a string. Tokenising it as code would corrupt the scan."""
    stream = b"BT 0 0 Td (BT q 1 0 0 1 9 9 cm \\) ET) Tj ET\n"
    objects, _, _ = pdf_content_order.scan(stream)
    assert len(objects) == 1 and objects[0][1:3] == (0.0, 0.0), objects


def test_columns_of_one_row_are_ordered_across_not_by_a_hair_of_height() -> None:
    """Three labelled columns, baselines a hair apart, must read left to right.

    This is how a masked scan copied out as "PATIENT : DATE : RESPONSABLE :"
    with a date standing where the clinician belongs: the columns' baselines
    differed by about a point, and comparing raw `y` sorted the row
    right-to-left. Each value has its own label beside it here, so a
    replacement has to be placed against the base text around it rather than
    merely kept in the order the spans arrived in.
    """
    document = pymupdf.open()
    page = document.new_page(width=520, height=160)
    # The rightmost column sits a fraction higher, the way a recogniser
    # reports a row it read box by box.
    for text, x, y in (
        ("Naam:", 40, 60.0),
        ("Jan Janssens", 95, 60.0),
        ("Arts:", 210, 60.0),
        ("Dr. Pieters", 250, 60.0),
        ("Datum:", 360, 58.6),
        ("01-01-2020", 410, 58.6),
    ):
        page.insert_text((x, y), text, fontsize=10)
    page.insert_text((40, 110), "Vervolgens ging hij naar huis.", fontsize=10)
    data = _serialise(document)
    document.close()

    prepared = prepare_document(data, "application/pdf")
    text = prepared.texts[0].text
    spans = [
        MaskSpan(
            text.index(value),
            text.index(value) + len(value),
            label,
            "model",
            page=0,
            confidence=0.99,
        )
        for value, label in (
            ("Jan Janssens", "NAME"),
            ("Dr. Pieters", "NAME"),
            ("01-01-2020", "DATE"),
        )
    ]
    masked = redact_document(prepared, spans, redaction_style="invisible")

    lines = _lines(masked)
    labels = {"Naam:": None, "Arts:": None, "Datum:": None}
    for name in labels:
        matches = [index for index, line in enumerate(lines) if line.strip() == name]
        assert matches, (name, lines)
        labels[name] = matches[0]
    assert labels["Naam:"] < labels["Arts:"] < labels["Datum:"], lines
    # Each replacement belongs between its own label and the next one.
    for name, following in (("Naam:", "Arts:"), ("Arts:", "Datum:")):
        between = lines[labels[name] + 1 : labels[following]]
        assert between and any(
            entry.strip() for entry in between
        ), f"{name} lost its value: {lines}"


def test_a_value_from_the_line_above_stays_above() -> None:
    """An insertion at a rewritten object's start belongs before it.

    The first line here is nothing but its value, so after redaction there is
    no object left to split and the replacement is placed at the next object
    instead -- which is the second line, and that one *is* rewritten in place.
    Both claim the same byte offset, and if the rewrite is emitted first the
    date from the line above ends up reading after the line below it.
    """
    document = pymupdf.open()
    page = document.new_page(width=460, height=200)
    page.insert_text((40, 60), "seed", fontsize=11)
    resource = page.get_fonts(full=True)[0][4]
    stream = (
        f"q BT/{resource} 11 Tf 40 160 TD (29-11-2009)Tj ET Q "
        f"q BT/{resource} 11 Tf 40 130 TD (Patient )Tj (Jan Janssens)Tj "
        f"( werd gezien.)Tj ET Q"
    ).encode("latin-1")
    document.update_stream(page.get_contents()[0], stream)
    data = _serialise(document)
    document.close()

    prepared = prepare_document(data, "application/pdf")
    text = prepared.texts[0].text
    spans = [
        MaskSpan(
            text.index(value),
            text.index(value) + len(value),
            label,
            "model",
            page=0,
            confidence=0.99,
        )
        for value, label in (("29-11-2009", "DATE"), ("Jan Janssens", "NAME"))
    ]
    lines = _lines(redact_document(prepared, spans, redaction_style="invisible"))
    body = [index for index, line in enumerate(lines) if "werd gezien" in line]
    assert body, lines
    assert body[0] > 0, f"the date from the first line must come first: {lines}"


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


def test_a_value_below_the_first_line_of_a_one_object_page_stays_in_place() -> None:
    """The en-GB header defect: name, NHS number and date read after the
    signature, because the page is one text object and one text object was one
    position.

    The line above and the line below the pseudonym are what pin it. Landing
    at the foot of the page satisfies "the pseudonym is somewhere on the
    page", which is why that is not what is asserted here.
    """
    lines = _lines(_masked(_one_object_document()))
    assert "Jan Janssens" not in " ".join(lines)
    assert len(lines) == 3, lines
    assert lines[0].startswith("Datum:"), lines
    assert lines[2].startswith("Met vriendelijke"), lines
    head, marker, tail = lines[1].partition(" werd gezien.")
    assert marker and not tail, lines
    assert head.startswith("Patient "), lines
    assert head.removeprefix("Patient ").strip(), lines


def test_a_one_object_page_renders_exactly_as_it_did() -> None:
    """Closing and reopening the object may move no glyph on the page.

    Every relative move after the seam is applied to the line matrix the
    reopen writes back, so an approximation there would walk the rest of the
    page down by a fraction of a point per line.
    """
    data = _one_object_document()
    for before, after in zip(
        _render(_without_reordering(data, redaction_style="invisible")),
        _render(_masked(data, redaction_style="invisible")),
        strict=True,
    ):
        assert np.array_equal(before, after), "reordering moved a glyph"
