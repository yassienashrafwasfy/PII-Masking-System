#!/usr/bin/env python3
"""Run every corpus page through every redaction setting, on invariants only.

The rest of the PDF tests pin behaviour: this line moves by that much, that
box holds that text. This one pins nothing about layout. It asserts only what
no masked document may ever break, whatever the page looked like going in:

  PAGES    the page count is unchanged
  LEAK     no masked value survives in the text layer
  BOUNDS   nothing is drawn outside the page rectangle
  LOST     every word no span covered is still on the page
  OVERLAP  two words on one baseline do not sit on top of each other
  ORDER    within a baseline, the text layer still extracts left to right

ORDER is the one that earns the suite. A replacement drawn at the right place
but *written* at the end of its line looks perfect on screen and is wrong to
everything that reads the file -- search, indexing, a downstream model, a
screen reader. No rendered comparison catches it and no unit test here did:
it took running the awkward pages against the invariant to find that the
commonest line shape mupdf emits was never being spliced at all.

Every page is synthetic and every value is invented. See `pdf_corpus`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdf_corpus  # noqa: E402
from masking_engine import (  # noqa: E402
    MaskSpan,
    prepare_document,
    redact_document,
)
from masking_engine.app.services.pdf_extraction import (  # noqa: E402
    UnmaskableContentError,
)

# Every combination the deployment can be set to. `invisible` has no box to
# put a label in, so `detect` is meaningless with it and `config.py` refuses
# the pair outright; the sweep skips it for the same reason.
SETTINGS = [
    (style, mode, reflow)
    for style in ("invisible", "clinical_soft", "bold_chips")
    for mode in ("pseudonymize", "detect")
    for reflow in (True, False)
    if not (style == "invisible" and mode == "detect")
]

# Two words on one baseline may sit this close before they are read as
# overlapping, and a word may start this far left of its predecessor before
# the reading order is read as broken. Both absorb the sub-point noise of
# measuring a glyph box rather than an advance.
_TOUCH = 0.6

# A baseline is rounded to this many places to group a row. One place, not
# none: a superscript sits a third of a point off the line it belongs to, and
# rounding it onto that line makes it look like text out of order.
_ROW_PLACES = 1

# Where the reading-order splice does not reach, and why. Each entry is
# asserted to *still* fail below -- an exemption that has quietly become
# unnecessary is worse than no exemption, because nothing would say so.
_ORDER_UNRESTORED: dict[str, str | None] = {
    # `pdf_content_order.restore` refuses a rotated page outright: the page
    # matrix it would have to splice under is not the one the operators were
    # written in. Nothing is spliced, so every replacement stays last.
    "rotated_text": None,
    "page_rotated_90": None,
    # A value inside a paragraph mupdf drew as one text object of many runs.
    # The kern standing in for the value sits inside a `TJ` array, and
    # closing the array there would move the line matrix that the runs
    # *below* it derive their position from -- so the replacement takes the
    # end of its run instead. Only with reflow off: a reflowed line is
    # redrawn, which gives the splice a seam of its own to take.
    "wrap_with_chip_below": "invisible/pseudonymize/False",
}


def _flat(text: str) -> tuple[str, list[int]]:
    """*text* with whitespace removed, and each kept character's index."""
    kept = [(index, char) for index, char in enumerate(text) if not char.isspace()]
    return "".join(char for _index, char in kept), [index for index, _char in kept]


def _spans(text: str, page: int, wanted: list[tuple[str, str]]) -> list[MaskSpan]:
    """Every occurrence of every wanted value on *page*, whitespace-blind.

    A value that wrapped carries a newline the page never drew, and one drawn
    as two fragments carries a space the extractor invented, so the match has
    to ignore both. It also has to find *every* occurrence: masking only the
    first is a leak, and a corpus page repeats its value on purpose.
    """
    flat, index = _flat(text)
    found: list[MaskSpan] = []
    for needle, label in wanted:
        target = "".join(needle.split())
        if not target:
            continue
        at = flat.find(target)
        while at >= 0:
            found.append(
                MaskSpan(
                    index[at],
                    index[at + len(target) - 1] + 1,
                    label,
                    "model",
                    page=page,
                    confidence=0.9,
                )
            )
            at = flat.find(target, at + 1)
    return found


def _words(page: pymupdf.Page) -> list[tuple[float, float, float, float, str]]:
    return [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def _source_words(data: bytes, values: list[str]) -> list[set[str]]:
    """Per page, the words no value covers -- what masking must not lose."""
    masked = {"".join(value.split()) for value in values}
    kept: list[set[str]] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for page in document:
            keep = set()
            for _x0, _y0, _x1, _y1, word in _words(page):
                flat = "".join(word.split())
                if flat and not any(flat in value or value in flat for value in masked):
                    keep.add(flat)
            kept.append(keep)
    return kept


def _upright_words(page: pymupdf.Page) -> list[tuple[float, float, float, float, str]]:
    """The page's words, minus any drawn on a rotated line.

    Rotated text extracts as a column of words sharing one x range and
    differing only in y, so the two checks that reason about a row -- overlap
    and reading order -- read it as a pile of text on top of itself. Neither
    is a claim about rotated text: v1 draws a rotated value with the
    invisible substitution and reflows nothing there. So it is left out
    rather than measured by a rule written for upright lines.
    """
    turned = [
        pymupdf.Rect(line["bbox"])
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", ())
        if tuple(round(value, 3) for value in line.get("dir", (1.0, 0.0))) != (1.0, 0.0)
    ]
    return [
        word
        for word in _words(page)
        if not any(rect.intersects(pymupdf.Rect(*word[:4])) for rect in turned)
    ]


def _order_break(page: pymupdf.Page) -> str | None:
    """The first word that extracts to the left of one already read, if any.

    Only within a baseline. Across baselines the extraction order is not the
    geometry: a column, a superscript and rotated text all read in an order
    reading down the page would get wrong.
    """
    leftmost: dict[float, float] = {}
    for x0, y0, _x1, _y1, word in _upright_words(page):
        row = round(y0, _ROW_PLACES)
        if row in leftmost and x0 < leftmost[row] - _TOUCH:
            return f"{word!r} at x={x0:.1f} after x={leftmost[row]:.1f} on y={row}"
        leftmost[row] = x0
    return None


@pytest.mark.parametrize(
    "builder", pdf_corpus.BUILDERS, ids=lambda builder: builder.__name__
)
def test_a_masked_page_never_breaks_an_invariant(builder) -> None:
    name, data, wanted = builder()
    try:
        document = prepare_document(data, "application/pdf")
    except UnmaskableContentError:
        # An image on a page that kept its text layer. Refusing it is the
        # rule `_check_image_pages` exists for, and is the answer under test.
        assert name == "image_and_text", name
        return

    spans: list[MaskSpan] = []
    for number, page_text in enumerate(document.texts):
        spans.extend(_spans(page_text.text, number, wanted))
    assert spans, f"{name}: no span matched the page text"
    values = ["".join(needle.split()) for needle, _label in wanted]

    with pymupdf.open(stream=data, filetype="pdf") as source:
        page_count = source.page_count
        rects = [source[index].rect for index in range(page_count)]
    kept = _source_words(data, values)

    for style, mode, reflow in SETTINGS:
        setting = f"{style}/{mode}/{reflow}"
        masked = redact_document(
            document,
            spans,
            redaction_style=style,
            redaction_mode=mode,
            chip_reflow=reflow,
            locale="nl_BE",
        )
        with pymupdf.open(stream=masked, filetype="pdf") as result:
            assert result.page_count == page_count, f"{name} {setting}: PAGES"
            for number in range(page_count):
                page = result[number]
                where = f"{name} {setting} p{number}"
                stripped = "".join(page.get_text().split())
                for value in values:
                    assert value not in stripped, f"{where}: LEAK {value!r}"

                rect = rects[number]
                for x0, y0, x1, y1, word in _words(page):
                    assert (
                        x0 >= rect.x0 - 1 and x1 <= rect.x1 + 1 and y1 <= rect.y1 + 1
                    ), f"{where}: BOUNDS {word!r} at {x0:.1f},{y0:.1f}"

                survivors = "".join(page.get_text().split())
                for word in kept[number]:
                    assert word in survivors, f"{where}: LOST {word!r}"

                rows: dict[float, list[tuple[float, float, str]]] = {}
                for x0, y0, x1, _y1, word in _upright_words(page):
                    rows.setdefault(round(y0, _ROW_PLACES), []).append((x0, x1, word))
                for row in rows.values():
                    row.sort()
                    for (_ax0, ax1, aword), (bx0, _bx1, bword) in zip(row, row[1:]):
                        assert (
                            bx0 >= ax1 - _TOUCH
                        ), f"{where}: OVERLAP {aword!r} over {bword!r}"

                broken = _order_break(page)
                exempt = name in _ORDER_UNRESTORED and _ORDER_UNRESTORED[name] in (
                    None,
                    setting,
                )
                if exempt:
                    continue
                assert broken is None, f"{where}: ORDER {broken}"


@pytest.mark.parametrize("name", sorted(_ORDER_UNRESTORED))
def test_every_reading_order_exemption_is_still_needed(name: str) -> None:
    """An exemption that has become unnecessary has to be deleted, not kept.

    Nothing else would say so: the sweep above simply stops looking at the
    page. So each entry is held to the failure it claims, and the day the
    splice reaches that shape this test is what fails.
    """
    builder = next(one for one in pdf_corpus.BUILDERS if one.__name__ == name)
    _name, data, wanted = builder()
    document = prepare_document(data, "application/pdf")
    spans: list[MaskSpan] = []
    for number, page_text in enumerate(document.texts):
        spans.extend(_spans(page_text.text, number, wanted))

    setting = _ORDER_UNRESTORED[name]
    combinations = (
        SETTINGS
        if setting is None
        else [one for one in SETTINGS if f"{one[0]}/{one[1]}/{one[2]}" == setting]
    )
    assert combinations, f"{name}: the exemption names no setting the sweep runs"
    breaks: list[str | None] = []
    for style, mode, reflow in combinations:
        masked = redact_document(
            document,
            spans,
            redaction_style=style,
            redaction_mode=mode,
            chip_reflow=reflow,
            locale="nl_BE",
        )
        with pymupdf.open(stream=masked, filetype="pdf") as result:
            breaks.extend(
                _order_break(result[number]) for number in range(result.page_count)
            )
    assert any(
        one is not None for one in breaks
    ), f"{name}: reading order is restored now -- delete this exemption"


if __name__ == "__main__":  # pragma: no cover - the README runs these as scripts
    raise SystemExit(pytest.main([__file__, "-q"]))
