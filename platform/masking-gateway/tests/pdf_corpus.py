#!/usr/bin/env python3
"""Adversarial PDFs for the redaction pipeline, and what to mask in each.

The unit tests around the PDF path build the one page each of them is about.
This is the other half: a body of pages chosen because they are awkward, run
through every redaction style and mode at once, and judged only on invariants
no masked document may ever break. It is what found the reading-order defect
`pdf_content_order` now repairs -- a page nobody would have thought to write
a unit test for, because the shape that triggers it is what mupdf's own
writer emits.

A builder returns `(name, pdf_bytes, [(needle, label)])`. It names substrings
rather than offsets: the offsets depend on how extraction reads the page it
just built, which is part of what is under test, so the caller resolves them
against the extracted text at run time.

The values are synthetic throughout -- Belgian-shaped identifiers with
checksums that do not validate, hospital names that do not exist, and dates
in no record. Nothing here came from a document.
"""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType

import pymupdf

Case = tuple[str, bytes, list[tuple[str, str]]]


def _doc(
    width: float = 595.0, height: float = 842.0
) -> tuple[pymupdf.Document, pymupdf.Page]:
    document = pymupdf.open()
    return document, document.new_page(width=width, height=height)


def _bytes(document: pymupdf.Document) -> bytes:
    data = document.tobytes()
    document.close()
    return data


# --------------------------------------------------------------------------- #
# A value that wraps, and the paragraph reflow that closes the line it emptied
# --------------------------------------------------------------------------- #


def wrap_two_lines() -> Case:
    document, page = _doc(300, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 150, 300),
        "Gezien in Algemeen Ziekenhuis Oost vandaag en morgen",
        fontsize=11,
    )
    return (
        "wrap_two_lines",
        _bytes(document),
        [("Algemeen Ziekenhuis Oost", "ORGANIZATION_NAME")],
    )


def wrap_three_lines() -> Case:
    document, page = _doc(300, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 145, 300),
        "Gezien in Algemeen Ziekenhuis Oost-Vlaanderen vandaag en morgen weer",
        fontsize=11,
    )
    return (
        "wrap_three_lines",
        _bytes(document),
        [("Algemeen Ziekenhuis Oost-Vlaanderen", "ORGANIZATION_NAME")],
    )


def wrap_four_lines() -> Case:
    document, page = _doc(300, 500)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 130, 400),
        "Gezien in Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen vandaag en"
        " morgen weer en later",
        fontsize=11,
    )
    return (
        "wrap_four_lines",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen", "ORGANIZATION_NAME")],
    )


def wrap_whole_paragraph() -> Case:
    """The value is every line of the paragraph: nothing survives to lift."""
    document, page = _doc(300, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 130, 300),
        "Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen Brugge",
        fontsize=11,
    )
    return (
        "wrap_whole_paragraph",
        _bytes(document),
        [
            (
                "Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen Brugge",
                "ORGANIZATION_NAME",
            )
        ],
    )


def wrap_then_table() -> Case:
    """A blank line immediately above a two-column block."""
    document, page = _doc(400, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 150, 200),
        "Gezien in Universitair Ziekenhuis Sint-Jan vandaag",
        fontsize=11,
    )
    rows = [("Naam", "Waarde"), ("INSZ", "30092743035"), ("RIZIV", "83527339302")]
    for index, (left, right) in enumerate(rows):
        page.insert_text((40, 200 + index * 14), left, fontsize=11)
        page.insert_text((250, 200 + index * 14), right, fontsize=11)
    return (
        "wrap_then_table",
        _bytes(document),
        [
            ("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME"),
            ("30092743035", "INSZ_NISS"),
        ],
    )


def wrap_at_paragraph_end() -> Case:
    """The emptied line is the last of its paragraph: nothing below to lift."""
    document, page = _doc(300, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 130, 200),
        "Verwijzing door Universitair Ziekenhuis Sint-Jan",
        fontsize=11,
    )
    page.insert_text((40, 200), "Volgende alinea staat hier.", fontsize=11)
    return (
        "wrap_at_paragraph_end",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME")],
    )


def wrap_hard_broken_number() -> Case:
    """A digit run broken across lines with no space drawn at the break."""
    document, page = _doc(150, 300)
    page.insert_textbox(
        pymupdf.Rect(20, 20, 120, 200), "Nummer 30092743035123456789 einde", fontsize=11
    )
    return (
        "wrap_hard_broken_number",
        _bytes(document),
        [("30092743035123456789", "INSZ_NISS")],
    )


def wrap_with_chip_below() -> Case:
    """A chip on a line that is itself lifted."""
    document, page = _doc(320, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 140, 300),
        "Gezien in Universitair Ziekenhuis Sint-Jan op 08-11-1996 door de arts",
        fontsize=11,
    )
    return (
        "wrap_with_chip_below",
        _bytes(document),
        [
            ("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME"),
            ("08-11-1996", "DATE"),
        ],
    )


def two_wraps_one_paragraph() -> Case:
    """Two separate values, each emptying a line of the same paragraph."""
    document, page = _doc(300, 500)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 135, 400),
        "Gezien in Algemeen Ziekenhuis Oost-Vlaanderen en ook in "
        "Universitair Ziekenhuis Sint-Jan vandaag en morgen weer",
        fontsize=11,
    )
    return (
        "two_wraps_one_paragraph",
        _bytes(document),
        [
            ("Algemeen Ziekenhuis Oost-Vlaanderen", "ORGANIZATION_NAME"),
            ("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME"),
        ],
    )


def wrap_url_three_lines() -> Case:
    """One unbroken token wide enough to take three lines on its own."""
    document, page = _doc(200, 300)
    page.insert_textbox(
        pymupdf.Rect(20, 20, 120, 200),
        "Zie https://www.ziekenhuis-sint-jan.example.be/patient/dossier voor meer",
        fontsize=11,
    )
    return (
        "wrap_url_three_lines",
        _bytes(document),
        [("https://www.ziekenhuis-sint-jan.example.be/patient/dossier", "URL")],
    )


def value_first_line_of_page() -> Case:
    document, page = _doc(300, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 20, 130, 300),
        "Universitair Ziekenhuis Sint-Jan Oost onderzocht vandaag en morgen",
        fontsize=11,
    )
    return (
        "value_first_line_of_page",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan Oost", "ORGANIZATION_NAME")],
    )


def wrap_across_page_break() -> Case:
    """The value ends one page and the next begins: two spans, not one."""
    document = pymupdf.open()
    first = document.new_page(width=300, height=120)
    first.insert_text((40, 100), "Algemeen Ziekenhuis", fontsize=11)
    second = document.new_page(width=300, height=120)
    second.insert_text((40, 30), "Oost-Vlaanderen vandaag", fontsize=11)
    return (
        "wrap_across_page_break",
        _bytes(document),
        [
            ("Algemeen Ziekenhuis", "ORGANIZATION_NAME"),
            ("Oost-Vlaanderen", "ORGANIZATION_NAME"),
        ],
    )


# --------------------------------------------------------------------------- #
# Shapes chosen to make the paragraph reflow move something it must not
# --------------------------------------------------------------------------- #


def three_columns_middle_wraps() -> Case:
    """A wrap in the middle column must not lift the outer two."""
    document, page = _doc(600, 400)
    for row in range(6):
        page.insert_text((30, 100 + row * 14), f"links {row}", fontsize=11)
        page.insert_text((430, 100 + row * 14), f"rechts {row}", fontsize=11)
    page.insert_textbox(
        pymupdf.Rect(200, 100, 300, 300),
        "Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen einde",
        fontsize=11,
    )
    return (
        "three_columns_middle_wraps",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen", "ORGANIZATION_NAME")],
    )


def same_leading_two_paragraphs() -> Case:
    """Two paragraphs one leading apart: the second must not be pulled up."""
    document, page = _doc(320, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 130, 160),
        "Gezien in Universitair Ziekenhuis Sint-Jan vandaag",
        fontsize=11,
    )
    for row in range(4):
        page.insert_text(
            (40, 160 + row * 14.2), f"tweede alinea regel {row}", fontsize=11
        )
    return (
        "same_leading_two_paragraphs",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME")],
    )


def irregular_leading() -> Case:
    """Mixed sizes, so no two steps agree: nothing may be lifted."""
    document, page = _doc(320, 400)
    baseline = 60.0
    for index, size in enumerate((11, 15, 9, 13, 11)):
        page.insert_text((40, baseline), f"regel {index} met tekst", fontsize=size)
        baseline += size + 4
    page.insert_textbox(
        pymupdf.Rect(40, baseline, 130, baseline + 120),
        "Universitair Ziekenhuis Sint-Jan Oost einde",
        fontsize=11,
    )
    return (
        "irregular_leading",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan Oost", "ORGANIZATION_NAME")],
    )


def blank_line_then_heading() -> Case:
    """A heading below the hole: different left edge and leading, stays put."""
    document, page = _doc(320, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 130, 160),
        "Gezien in Universitair Ziekenhuis Sint-Jan vandaag",
        fontsize=11,
    )
    page.insert_text((60, 180), "BESLUIT", fontsize=14, fontname="hebo")
    page.insert_text((40, 200), "Conclusietekst hier.", fontsize=11)
    return (
        "blank_line_then_heading",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan", "ORGANIZATION_NAME")],
    )


def justified_paragraph() -> Case:
    """Justification kerns every line; only one of them is a removal."""
    document, page = _doc(320, 400)
    page.insert_textbox(
        pymupdf.Rect(40, 40, 280, 300),
        "Gezien in Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen "
        "vandaag en morgen weer en later ook nog eens opnieuw hier",
        fontsize=11,
        align=pymupdf.TEXT_ALIGN_JUSTIFY,
    )
    return (
        "justified_paragraph",
        _bytes(document),
        [("Universitair Ziekenhuis Sint-Jan Oost-Vlaanderen", "ORGANIZATION_NAME")],
    )


# --------------------------------------------------------------------------- #
# Line reflow and chip sizing
# --------------------------------------------------------------------------- #


def tight_slot_at_right_margin() -> Case:
    document, page = _doc()
    page.insert_text((520, 100), "AB", fontsize=11)
    return "tight_slot_at_right_margin", _bytes(document), [("AB", "NAME")]


def two_chips_one_line() -> Case:
    document, page = _doc()
    page.insert_text(
        (40, 100), "Patient AB seen by CD on 01-01-2020 today", fontsize=11
    )
    return (
        "two_chips_one_line",
        _bytes(document),
        [("AB", "NAME"), ("CD", "NAME"), ("01-01-2020", "DATE")],
    )


def right_aligned_column() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "Naam", fontsize=11)
    page.insert_text((480, 100), "AB", fontsize=11)
    page.insert_text((40, 114), "Datum", fontsize=11)
    page.insert_text((480, 114), "01-01-2020", fontsize=11)
    return (
        "right_aligned_column",
        _bytes(document),
        [("AB", "NAME"), ("01-01-2020", "DATE")],
    )


def full_line_no_room() -> Case:
    """The line already reaches the margin: the reflow has nothing to grant."""
    document, page = _doc()
    page.insert_text(
        (30, 100),
        "AB " + "vulling " * 8 + "einde van de regel die helemaal vol staat hier",
        fontsize=11,
    )
    return "full_line_no_room", _bytes(document), [("AB", "NAME")]


def value_at_page_edge() -> Case:
    document, page = _doc()
    page.insert_text((560, 100), "AB", fontsize=11)
    page.insert_text((2, 120), "CD", fontsize=11)
    return "value_at_page_edge", _bytes(document), [("AB", "NAME"), ("CD", "NAME")]


def long_value_short_slot() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "X seen today", fontsize=11)
    return "long_value_short_slot", _bytes(document), [("X", "NAME")]


# --------------------------------------------------------------------------- #
# Styling and geometry extremes
# --------------------------------------------------------------------------- #


def tiny_type() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "Patient AB seen on 01-01-2020", fontsize=3.5)
    return "tiny_type", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


def huge_type() -> Case:
    document, page = _doc()
    page.insert_text((40, 200), "AB 01-01-2020", fontsize=48)
    return "huge_type", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


def rotated_text() -> Case:
    """Rotated and upright text on one page; only the upright half reflows."""
    document, page = _doc()
    page.insert_text(
        (100, 400), "Patient AB seen on 01-01-2020", fontsize=11, rotate=90
    )
    page.insert_text((40, 100), "Patient CD seen today", fontsize=11)
    return (
        "rotated_text",
        _bytes(document),
        [("AB", "NAME"), ("01-01-2020", "DATE"), ("CD", "NAME")],
    )


def page_rotated_90() -> Case:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((40, 100), "Patient AB seen on 01-01-2020", fontsize=11)
    page.set_rotation(90)
    return "page_rotated_90", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


def coloured_and_bold() -> Case:
    document, page = _doc()
    page.insert_text(
        (40, 100), "Patient AB", fontsize=11, color=(1, 0, 0), fontname="hebo"
    )
    return "coloured_and_bold", _bytes(document), [("AB", "NAME")]


def tiny_page() -> Case:
    document, page = _doc(120, 90)
    page.insert_text((10, 30), "AB 01-01-2020", fontsize=8)
    return "tiny_page", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


def superscript_line() -> Case:
    """A superscript sits near enough to a line to be read as part of it."""
    document, page = _doc()
    page.insert_text((40, 100), "Waarde 18 mg/L", fontsize=11)
    page.insert_text((95, 96), "2", fontsize=7)
    page.insert_text((40, 114), "Patient AB", fontsize=11)
    return "superscript_line", _bytes(document), [("AB", "NAME")]


def image_and_text() -> Case:
    """An image on a page that kept its text layer: refused, not masked."""
    document, page = _doc()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Rect(0, 0, 8, 8), 0)
    page.insert_image(pymupdf.Rect(40, 40, 120, 120), stream=pixmap.tobytes())
    page.insert_text((40, 200), "Patient AB seen on 01-01-2020", fontsize=11)
    return "image_and_text", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


# --------------------------------------------------------------------------- #
# How the values sit in the text
# --------------------------------------------------------------------------- #


def repeated_value() -> Case:
    """One value six times over: every copy has to be masked, not the first."""
    document, page = _doc()
    for row in range(6):
        page.insert_text(
            (40, 100 + row * 14), "Contact Algemeen Ziekenhuis Oost today", fontsize=11
        )
    return (
        "repeated_value",
        _bytes(document),
        [("Algemeen Ziekenhuis Oost", "ORGANIZATION_NAME")],
    )


def multi_page() -> Case:
    document = pymupdf.open()
    for index in range(3):
        page = document.new_page(width=595, height=842)
        page.insert_text(
            (40, 100), f"Page {index} patient AB seen on 01-01-2020", fontsize=11
        )
    return "multi_page", _bytes(document), [("AB", "NAME"), ("01-01-2020", "DATE")]


def two_columns() -> Case:
    document, page = _doc()
    for row in range(6):
        page.insert_text((40, 100 + row * 14), "Links AB regel", fontsize=11)
        page.insert_text((330, 100 + row * 14), "Rechts CD regel", fontsize=11)
    return "two_columns", _bytes(document), [("AB", "NAME"), ("CD", "NAME")]


def adjacent_spans() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "AB CD 01-01-2020 einde", fontsize=11)
    return (
        "adjacent_spans",
        _bytes(document),
        [("AB", "NAME"), ("CD", "NAME"), ("01-01-2020", "DATE")],
    )


def overlapping_spans() -> Case:
    """One value inside another: the pipeline has to settle on one of them."""
    document, page = _doc()
    page.insert_text((40, 100), "Patient Jan Peeters seen today", fontsize=11)
    return (
        "overlapping_spans",
        _bytes(document),
        [("Jan Peeters", "NAME"), ("Peeters", "NAME")],
    )


def value_at_text_start_and_end() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "AB midden CD", fontsize=11)
    return (
        "value_at_text_start_and_end",
        _bytes(document),
        [("AB", "NAME"), ("CD", "NAME")],
    )


def value_with_punctuation() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "Contact: AB, seen (01-01-2020).", fontsize=11)
    return (
        "value_with_punctuation",
        _bytes(document),
        [("AB", "NAME"), ("01-01-2020", "DATE")],
    )


def value_hugging_punctuation() -> Case:
    """No space anywhere near the value; erasing it must not eat a bracket."""
    document, page = _doc()
    page.insert_text((40, 100), "(AB),[01-01-2020];einde", fontsize=11)
    return (
        "value_hugging_punctuation",
        _bytes(document),
        [("AB", "NAME"), ("01-01-2020", "DATE")],
    )


def unicode_value() -> Case:
    document, page = _doc()
    page.insert_text((40, 100), "Patient Zoë Müller-Bäcker seen today", fontsize=11)
    return "unicode_value", _bytes(document), [("Zoë Müller-Bäcker", "NAME")]


def very_dense_page() -> Case:
    """Fifty lines, one value on each: every splice competes with a neighbour."""
    document, page = _doc()
    for row in range(50):
        page.insert_text(
            (30, 40 + row * 15),
            f"Regel {row}: patient AB{row} gezien op 01-01-2020 door CD{row}",
            fontsize=9,
        )
    return "very_dense_page", _bytes(document), [("01-01-2020", "DATE")]


# Every builder above, in name order. Collected rather than listed so a case
# added below cannot be silently left out of the sweep -- which is how the
# first fourteen of these ran for a while without being scored at all.
BUILDERS: tuple[Callable[[], Case], ...] = tuple(
    value
    for key, value in sorted(globals().items())
    if isinstance(value, FunctionType)
    and value.__module__ == __name__
    and not key.startswith("_")
)
