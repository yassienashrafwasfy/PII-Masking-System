#!/usr/bin/env python3
"""Pin which lines close a hole a masked value left, and which never move.

`pdf_line_reflow` makes room along a line; this is the other axis. A value
wide enough to be a whole line leaves that line with nothing on it, and a
blank line inside a paragraph is a hole rather than a short line, so the
lines under it come up.

Moving text vertically is the one reflow that can put a line where it does
not belong -- on a footer, in the next paragraph, in the other column of a
table -- so most of what follows pins the cases where nothing moves at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, prepare_document, redact_document
from masking_engine.app.services import pdf_block_reflow
from masking_engine.app.services.pdf_block_reflow import Line, plan


def paragraph(
    count: int, *, blank: set[int] = frozenset(), step: float = 14.2, x0: float = 84.0
) -> list[Line]:
    """A run of *count* evenly-led lines sharing one left edge."""
    return [
        Line(
            key=float(index),
            x0=x0,
            x1=x0 + 300.0,
            baseline=100.0 + step * index,
            blank=index in blank,
        )
        for index in range(count)
    ]


def test_every_line_under_a_blank_one_comes_up_by_the_leading() -> None:
    assert plan(paragraph(5, blank={2})) == pytest.approx({3.0: 14.2, 4.0: 14.2})


def test_two_blank_lines_lift_twice() -> None:
    lifted = plan(paragraph(6, blank={1, 2}))
    assert lifted == pytest.approx({3.0: 28.4, 4.0: 28.4, 5.0: 28.4})


def test_only_the_lines_below_move() -> None:
    """A hole is closed from underneath, never by pushing the text above down.

    Everything above the value is where the reader has already read it, and
    moving it would reflow a page that had nothing wrong with it.
    """
    lifted = plan(paragraph(5, blank={3}))
    assert set(lifted) == {4.0}


def test_a_blank_last_line_moves_nothing() -> None:
    assert plan(paragraph(4, blank={3})) == {}


def test_a_paragraph_with_no_blank_line_is_untouched() -> None:
    assert plan(paragraph(6)) == {}


def test_a_single_line_has_no_leading_to_move_by() -> None:
    lone = Line(key=0.0, x0=84.0, x1=200.0, baseline=100.0, blank=True)
    below = Line(key=1.0, x0=84.0, x1=200.0, baseline=300.0, blank=False)
    assert plan([lone, below]) == {}


def test_an_indented_line_is_a_different_paragraph() -> None:
    """A left edge is what says "this line continues the one above".

    A list marker, a quotation and a second column all start further in, and
    every one of them is a block of its own that a hole above must not drag
    up.
    """
    lines = paragraph(3, blank={1})
    lines.append(
        Line(
            key=9.0,
            x0=84.0 + 3 * pdf_block_reflow.MAX_LEFT_DRIFT,
            x1=400.0,
            baseline=lines[-1].baseline + 14.2,
            blank=False,
        )
    )
    assert 9.0 not in plan(lines)


def test_a_change_of_leading_ends_the_paragraph() -> None:
    """The next paragraph sits further down than one line, and stays there."""
    lines = paragraph(3, blank={1})
    lines.append(
        Line(
            key=9.0, x0=84.0, x1=400.0, baseline=lines[-1].baseline + 26.0, blank=False
        )
    )
    assert 9.0 not in plan(lines)


def test_a_gap_wider_than_a_leading_is_never_crossed() -> None:
    """Two lines separated by white space are not one paragraph with a step.

    Without the ceiling, a heading and the paragraph under it read as a
    two-line block whose "leading" is the whole gap between them, and a hole
    in the heading would pull the body up over the white space a designer put
    there.
    """
    top = Line(key=0.0, x0=84.0, x1=400.0, baseline=100.0, blank=True)
    far = Line(
        key=1.0,
        x0=84.0,
        x1=400.0,
        baseline=100.0 + pdf_block_reflow.MAX_STEP + 1.0,
        blank=False,
    )
    assert plan([top, far]) == {}


def test_lines_arrive_in_any_order() -> None:
    lines = paragraph(4, blank={1})
    assert plan(list(reversed(lines))) == plan(lines)


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #


def _wrapped_paragraph_pdf(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_textbox(pymupdf.Rect(40, 40, 145, 200), text, fontsize=11)
    # A footer, far below and outside the paragraph: it must not move.
    page.insert_text((40, 340), "Footer line", fontsize=11)
    content = document.tobytes()
    document.close()
    return content


def _rows(data: bytes) -> list[float]:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return sorted({round(word[1], 1) for word in document[0].get_text("words")})


def test_a_paragraph_closes_the_line_its_value_emptied() -> None:
    source = _wrapped_paragraph_pdf(
        "Gezien in Algemeen Ziekenhuis Oost-Vlaanderen vandaag en morgen weer"
    )
    document = prepare_document(source, "application/pdf")
    text = document.texts[0].text
    start = text.index("Algemeen")
    end = text.index("Vlaanderen") + len("Vlaanderen")
    assert text[start:end].count("\n") == 2, text

    before = _rows(source)
    masked = redact_document(
        document, [MaskSpan(start, end, "ORGANIZATION_NAME", "model", page=0)]
    )
    after = _rows(masked)

    # One line fewer, the same leading between the rest, and the footer
    # exactly where it was: the paragraph ended earlier, the page did not
    # reflow.
    assert len(after) == len(before) - 1, (before, after)
    assert after[:-1] == pytest.approx(before[: len(after) - 1], abs=0.5)
    assert after[-1] == pytest.approx(before[-1], abs=0.5), "the footer moved"


def test_the_text_below_a_closed_line_survives_the_move() -> None:
    """Lifting a line erases and redraws it, so its words have to come back."""
    source = _wrapped_paragraph_pdf(
        "Gezien in Algemeen Ziekenhuis Oost-Vlaanderen vandaag en morgen weer"
    )
    document = prepare_document(source, "application/pdf")
    text = document.texts[0].text
    start = text.index("Algemeen")
    end = text.index("Vlaanderen") + len("Vlaanderen")
    masked = redact_document(
        document, [MaskSpan(start, end, "ORGANIZATION_NAME", "model", page=0)]
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        released = " ".join(result[0].get_text().split())
    for word in ("Gezien", "in", "vandaag", "en", "morgen", "weer", "Footer", "line"):
        assert word in released, (word, released)
    for value in ("Algemeen", "Ziekenhuis", "Oost-Vlaanderen"):
        assert value not in released, released
