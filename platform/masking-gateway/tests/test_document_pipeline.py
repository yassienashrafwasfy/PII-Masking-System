#!/usr/bin/env python3
"""Exercise shared TXT/PDF extraction and redaction without model weights."""

from __future__ import annotations

import sys
from typing import Any, NoReturn, cast
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskingRouter, MaskSpan, prepare_document, redact_document
from masking_engine.app.core.error_codes import InferenceFailureError

# Private helpers this file pins on purpose: the box-geometry rules are not
# part of the package's public surface, but a regression in them is invisible
# in the rendered bytes until someone looks at a page.
from masking_engine.app.services.pdf_extraction import PdfGlyph, extract_pdf_pages
from masking_engine.app.services.pdf_masking_service import (
    GlyphRun,
    _builtin_like,
    _covers,
    _font_name,
    _redraw_moved_glyphs,
    _render_styled_run,
    _safe_expand,
    _source_value,
)


def pdf_bytes(
    text: str,
    *,
    fontsize: float = 11,
    color: tuple[float, float, float] = (0, 0, 0),
    fontname: str = "helv",
) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text(
        (72, 72), text, fontsize=fontsize, color=color, fontname=fontname
    )
    content = document.tobytes()
    document.close()
    return content


def wrapped_pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_textbox(pymupdf.Rect(40, 40, 145, 150), text, fontsize=11)
    content = document.tobytes()
    document.close()
    return content


def _image_and_text_pdf_bytes() -> bytes:
    """Return a page carrying both a small image and a real text layer."""
    image = pymupdf.open()
    page = image.new_page()
    image_data = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Rect(0, 0, 2, 2), 0).tobytes()
    page.insert_image(pymupdf.Rect(72, 72, 74, 74), stream=image_data)
    page.insert_text((72, 100), "Contact synthetic@example.org.")
    content = image.tobytes()
    image.close()
    return content


def _spans(blocks: list[dict]) -> list[dict]:
    """Return every text span in a rendered page's `get_text("dict")` blocks."""
    return [
        span
        for block in blocks
        if block["type"] == 0
        for line in block["lines"]
        for span in line["spans"]
    ]


def _glyph_at(char: str, x0: float, x1: float) -> PdfGlyph:
    return PdfGlyph(
        text=char,
        bbox=(x0, 100.0, x1, 110.0),
        origin=(x0, 108.0),
        font="helv",
        size=9.0,
        color=0,
        direction=(1.0, 0.0),
        flags=0,
    )


def test_a_text_document_is_masked_and_audited() -> None:
    plain = b"Contact synthetic@example.org."
    document = prepare_document(plain, "text/plain")
    start = document.texts[0].text.index("synthetic@example.org")
    audit: list[dict[str, object]] = []
    masked = redact_document(
        document,
        [
            MaskSpan(
                start,
                start + len("synthetic@example.org"),
                "EMAIL",
                "model",
                confidence=0.99,
                source_layer="sample/model",
            )
        ],
        audit,
    )
    assert b"synthetic@example.org" not in masked
    assert audit == [
        {
            "label": "EMAIL",
            "start": start,
            "end": start + len("synthetic@example.org"),
            "text": "synthetic@example.org",
            "replaced_text": masked.decode()[start:-1],
            "source_layer": "sample/model",
            "confidence": 0.99,
        }
    ]


def test_a_pdf_value_is_gone_from_the_rendered_text() -> None:
    source = pdf_bytes("Contact synthetic@example.org.")
    document = prepare_document(source, "application/pdf")
    assert (
        len(document.texts) == 1 and "synthetic@example.org" in document.texts[0].text
    )
    start = document.texts[0].text.index("synthetic@example.org")
    masked = redact_document(
        document,
        [
            MaskSpan(
                start,
                start + len("synthetic@example.org"),
                "EMAIL",
                "model",
                page=0,
            )
        ],
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        assert "synthetic@example.org" not in result[0].get_text()


def test_only_the_named_occurrence_is_replaced() -> None:
    repeated = pdf_bytes("Contact synthetic@example.org. Copy: synthetic@example.org.")
    document = prepare_document(repeated, "application/pdf")
    start = document.texts[0].text.index("synthetic@example.org")
    masked = redact_document(
        document,
        [
            MaskSpan(
                start,
                start + len("synthetic@example.org"),
                "EMAIL",
                "model",
                page=0,
            )
        ],
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        assert result[0].get_text().count("synthetic@example.org") == 1


def test_two_replacements_on_a_line_keep_every_word_between_them() -> None:
    """The line the reflow erased and then did not redraw.

    Growth from the first replacement is absorbed by the slack before the
    second, so the glyphs in the middle move by nothing and were left out of
    the redraw -- while the single rectangle that erases a reflowed line, which
    assumes the glyphs it redraws are contiguous, took them out anyway. A
    French conclusion came back with 167pt of blank paper where "consultation
    de gastro-enterologie le" had been, and no error anywhere.

    It needs a long line with two values far apart on it, drawn where a
    clinical letter draws one: a short line moves in one piece, and a line
    that starts nearer the right margin has no slack to absorb the growth in,
    so in both cases the hole never opens.
    """
    line = (
        "Uw patient van 71 jaar werd gezien op de raadpleging van "
        "gastro-enterologie op 17-02-2026 wegens klachten"
    )
    letter = pymupdf.open()
    letter.new_page().insert_text((56, 72), line, fontsize=10)
    source = letter.tobytes()
    letter.close()
    document = prepare_document(source, "application/pdf")
    text = document.texts[0].text
    spans = [
        MaskSpan(
            start=text.index("71"),
            end=text.index("71") + 2,
            label="AGE",
            source="regex",
            page=0,
        ),
        MaskSpan(
            start=text.index("17-02-2026"),
            end=text.index("17-02-2026") + 10,
            label="DATE",
            source="regex",
            page=0,
        ),
    ]
    # Both styles: a chip is wider than the value it covers and moves the line,
    # an invisible pseudonym often is not -- and only one of them lost words.
    for style in ("clinical_soft", "invisible"):
        masked = redact_document(document, list(spans), redaction_style=style)
        with pymupdf.open(stream=masked, filetype="pdf") as result:
            extracted = result[0].get_text()
        kept = [word for word in line.split() if word.isalpha() and len(word) > 3]
        for word in kept:
            assert word in extracted, f"{style}: {word!r} was erased, not redrawn"
        at = [extracted.index(word) for word in kept]
        assert at == sorted(at), f"{style}: the line extracts out of order"


def test_a_line_redrawn_around_a_replacement_comes_back_in_two_pieces() -> None:
    """A run has to break where a value was taken out of the line.

    `restore` orders what it splices by where it starts. One unit for a whole
    reflowed line starts left of the first replacement on it and ends right of
    the last, so a replacement in the middle sorted after the entire line: a
    French conclusion extracted as ". A retenir : le tableau" and only then the
    date belonging in front of it.
    """
    letter = pymupdf.open()
    page = letter.new_page()
    page.insert_text((56, 72), "alpha beta gamma", fontsize=10)
    source = letter.tobytes()
    letter.close()
    glyphs = prepare_document(source, "application/pdf").texts[0].glyphs

    with pymupdf.open(stream=source, filetype="pdf") as document:
        # Everything but "beta" moves: the hole is where a chip would go.
        held = range(len("alpha "), len("alpha beta"))
        shift = {
            index: 0.0
            for index, glyph in enumerate(glyphs)
            if glyph is not None and index not in held
        }
        units = _redraw_moved_glyphs(document, document[0], {}, glyphs, shift, {})

    assert len(units) == 2, "the line was redrawn straight through the gap"
    left, right = sorted(units, key=lambda unit: unit[1])
    assert left[3] <= right[1] + 0.1, "the two pieces overlap the value between"


def test_a_replacement_keeps_the_original_size_and_colour() -> None:
    styled = pdf_bytes("Date: 2024-01-01", fontsize=16, color=(1, 0, 0))
    document = prepare_document(styled, "application/pdf")
    start = document.texts[0].text.index("2024-01-01")
    masked = redact_document(
        document,
        [MaskSpan(start, start + 10, "DATE", "model", page=0)],
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        styles = _spans(result[0].get_text("dict")["blocks"])
        assert any(span["color"] == 16711680 for span in styles)
        assert any(abs(span["size"] - 16) < 0.1 for span in styles)


def test_a_replacement_keeps_the_original_font_and_its_own_left_edge() -> None:
    """Same face, same baseline, same start -- but no longer the same width.

    The box used to be the hard bound, and a pseudonym wider than the value it
    covered was condensed into it. It is the line that bounds one now: the
    reflow moves the rest of the line right and the replacement is written at
    its natural width, so what has to hold is where it *starts*, not where it
    ends. The page margin is the outer limit, and beyond it the old condensing
    is still what happens.
    """
    styled = pdf_bytes("Patient: Alice Example", fontsize=14, fontname="hebo")
    document = prepare_document(styled, "application/pdf")
    start = document.texts[0].text.index("Alice Example")
    source_rect = pymupdf.Rect(
        document.texts[0].glyphs[start].bbox,  # type: ignore[union-attr]
    )
    for glyph in document.texts[0].glyphs[start : start + len("Alice Example")]:
        if glyph is not None:
            source_rect.include_rect(glyph.bbox)
    masked = redact_document(
        document,
        [MaskSpan(start, start + len("Alice Example"), "NAME", "model", page=0)],
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        text = result[0].get_text()
        assert "Patient:" in text and "Alice Example" not in text
        styles = _spans(result[0].get_text("dict")["blocks"])
        assert all("Bold" in span["font"] for span in styles)
        replacement_text = text.removeprefix("Patient: ").strip()
        replacement_rect = result[0].search_for(replacement_text)[0]
        assert abs(replacement_rect.y0 - source_rect.y0) < 0.1
        assert abs(replacement_rect.y1 - source_rect.y1) < 0.1
        assert replacement_rect.x0 >= source_rect.x0 - 0.1
        assert replacement_rect.x1 <= result[0].rect.x1 - 36 + 0.1
        natural = pymupdf.Font("hebo").text_length(replacement_text, fontsize=14)
        assert replacement_rect.width >= natural - 0.5, "the pseudonym was condensed"


def test_a_span_wrapped_across_lines_is_fully_removed() -> None:
    wrapped = wrapped_pdf_bytes("Patient Alice Example Longname")
    document = prepare_document(wrapped, "application/pdf")
    text = document.texts[0].text
    start = text.index("Alice")
    end = text.index("Longname") + len("Longname")
    masked = redact_document(
        document,
        [MaskSpan(start, end, "NAME", "model", page=0)],
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        output = result[0].get_text()
        assert "Patient" in output
        assert "Alice" not in output and "Longname" not in output
        assert len(output.strip().splitlines()) >= 2


def test_a_wrapped_span_draws_one_whole_pseudonym_on_its_last_line() -> None:
    """A wrapped value is laid out exactly like one that never wrapped.

    The replacement is not cut into line-sized pieces. `_plan_page` gives the
    span a single slot -- the run on the value's *last* line -- and draws the
    whole pseudonym there. Which run draws is the whole point: a wrapped
    value always ends the line it starts on and starts the line it ends on,
    so the room its earlier runs give up falls at a line end, where a short
    line is what every ragged-right paragraph already looks like. The room
    the last run would give up falls at a line's head, in front of the word
    that follows -- the one gap a reader sees, and the bug this pins.
    """
    source = wrapped_pdf_bytes("Gezien in Algemeen Ziekenhuis Oost-Vlaanderen vandaag")
    document = prepare_document(source, "application/pdf")
    text = document.texts[0].text
    start = text.index("Algemeen")
    end = text.index("Vlaanderen") + len("Vlaanderen")
    assert text[start:end] == "Algemeen\nZiekenhuis\nOost-Vlaanderen"
    span = MaskSpan(start, end, "ORGANIZATION_NAME", "model", page=0)
    masked = redact_document(document, [span])

    def rows(page) -> dict[float, float]:
        """The leftmost x each baseline still carries text at."""
        leftmost: dict[float, float] = {}
        for x0, y0, _x1, _y1, _word, *_ in page.get_text("words"):
            key = round(y0, 1)
            leftmost[key] = min(leftmost.get(key, x0), x0)
        return leftmost

    with pymupdf.open(stream=source, filetype="pdf") as before:
        expected_rows = rows(before[0])
    with pymupdf.open(stream=masked, filetype="pdf") as after:
        released = after[0].get_text()
        actual_rows = rows(after[0])

    for value in ("Algemeen", "Ziekenhuis", "Oost-Vlaanderen"):
        assert value not in released, released
    # What `_plan_page` asks the router about: the value's own characters,
    # with the line break standing in for the space the source text carries
    # there. A wrapped value has to seed the same as an unwrapped one.
    drawn = text[start:end].replace("\n", " ")
    expected = (
        MaskingRouter(document.content, locale="nl_BE").replacement(span, drawn).text
    )
    # Whole, in one piece, and readable as the name it is -- not a redraw,
    # not a slice, not a hyphenation. Spaces are dropped from the comparison
    # only because a condensed chip may set them narrower than the extractor
    # calls a word gap.
    assert expected.replace(" ", "") in released.replace(" ", "").replace("\n", ""), (
        expected,
        released,
    )
    # Every line still starts where a line starts, which is the leading gap
    # this fixes: the erased *head* of a line is the only erased space a
    # reader notices, and the pseudonym is drawn into it.
    source_lines = sorted(expected_rows)
    for row in actual_rows:
        assert abs(actual_rows[row] - expected_rows[source_lines[0]]) < 0.5, released
    # The middle line was the value end to end, so nothing is left to draw on
    # it and `pdf_block_reflow` closes it: the paragraph keeps its leading and
    # ends one line earlier, rather than going out with a hole in it.
    step = source_lines[1] - source_lines[0]
    assert sorted(actual_rows) == pytest.approx(source_lines[:3], abs=0.5), released
    assert step == pytest.approx(source_lines[2] - source_lines[1], abs=0.5)


def test_a_wrapped_value_is_read_back_as_the_page_wrote_it() -> None:
    """What the router is asked about, for each shape a line break can take.

    The extractor writes whitespace of its own where the page drew none: a
    space between two separately-drawn fragments, a newline between two
    lines. Neither is a glyph, so both arrive here as a hole, and what belongs
    in the hole is not the same in every case.
    """
    cases = (
        # A soft wrap: the space is the one the layout chose not to draw, and
        # without it the value seeds under a different string than the same
        # organisation unwrapped in the header.
        (
            "Gezien in Algemeen Ziekenhuis Oost-Vlaanderen vandaag",
            "Algemeen",
            "Vlaanderen",
            "Algemeen Ziekenhuis Oost-Vlaanderen",
        ),
        # Prose wraps at a space even when it is made of digits, so a phone
        # number keeps the grouping the page gave it.
        ("Bel gerust 0476 03 31 64 vandaag", "0476", "31 64", "0476 03 31 64"),
        (
            "Contact info Marie-Claire Vandenberghe vandaag",
            "Marie",
            "Vandenberghe",
            "Marie-Claire Vandenberghe",
        ),
        # A hard break through a single token: nothing was left out, because
        # nothing could have stood there.
        (
            "Datum 20120424333222111000999 gezien",
            "2012",
            "999",
            "20120424333222111000999",
        ),
    )
    for text, first, last, expected in cases:
        pages = extract_pdf_pages(wrapped_pdf_bytes(text))
        page_text = pages[0].text
        start = page_text.index(first)
        end = page_text.index(last, start) + len(last)
        assert "\n" in page_text[start:end], (text, page_text)
        span = MaskSpan(start, end, "ORGANIZATION_NAME", "model", page=0)
        assert _source_value(pages[0].glyphs, span) == expected, text


def test_a_wrapped_span_asks_the_router_about_the_whole_value() -> None:
    """The router sees the value, not a line of it.

    A phone number that wrapped is still one number: `_plan_page` joins the
    glyphs it drew and asks for one replacement, so the surrogate keeps the
    source's own format -- the leading `04` that tells a reader what the
    field is -- instead of each line being redrawn from its own digits.
    """
    source = wrapped_pdf_bytes("Bel gerust 0476 03 31 64 vandaag")
    document = prepare_document(source, "application/pdf")
    text = document.texts[0].text
    start = text.index("0476")
    end = text.index("31 64") + len("31 64")
    lines = text[start:end].split("\n")
    assert lines == ["0476 03", "31 64"], text

    span = MaskSpan(start, end, "PHONE_NUMBER", "regex", page=0)
    drawn = " ".join(lines)
    expected = (
        MaskingRouter(document.content, locale="nl_BE").replacement(span, drawn).text
    )
    assert expected.startswith("04"), expected

    masked = redact_document(document, [span], locale="nl_BE")
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        released = result[0].get_text()
    assert "0476 03" not in released and "31 64" not in released, released
    assert expected.replace(" ", "") in released.replace(" ", "").replace("\n", ""), (
        expected,
        released,
    )


def test_a_visible_style_never_leaks_the_original_value() -> None:
    # --- Clinical Soft / Bold Chips visual styles -----------------------
    for redaction_style in ("clinical_soft", "bold_chips"):
        for redaction_mode in ("detect", "pseudonymize"):
            source = pdf_bytes("Patient: Alice Example, born 1990-01-01.")
            document = prepare_document(source, "application/pdf")
            text = document.texts[0].text
            start = text.index("Alice Example")
            end = start + len("Alice Example")
            date_start = text.index("1990-01-01")
            masked = redact_document(
                document,
                [
                    # A model detection carries a score; a regex match
                    # (DATE is regex-owned in production) never does.
                    MaskSpan(start, end, "NAME", "model", page=0, confidence=0.973),
                    MaskSpan(date_start, date_start + 10, "DATE", "regex", page=0),
                ],
                redaction_style=redaction_style,
                redaction_mode=redaction_mode,
            )
            with pymupdf.open(stream=masked, filetype="pdf") as result:
                page_text = result[0].get_text()
                # The core security property: original PII never survives,
                # in either mode, for either style.
                assert "Alice Example" not in page_text
                assert "1990-01-01" not in page_text
                # A filled, colored box was actually drawn.
                assert any(d.get("fill") for d in result[0].get_drawings())
                if redaction_mode == "detect":
                    # No pseudonym is ever rendered in detect mode — only
                    # the entity-type abbreviation, plus a rounded-percent
                    # score when the span carries one (a scoreless regex
                    # match keeps the bare abbreviation).
                    assert "NAME 97%" in page_text
                    assert "DATE" in page_text and "DATE %" not in page_text
                else:
                    # Some replacement text is rendered, and it must not be
                    # the abbreviation (that's the detect-mode signal, not
                    # a pseudonym).
                    assert page_text.strip() != ""


_TIGHT_SOURCE = "X:ABmore text right after with no gap at all"


def _tight_slot() -> tuple[bytes, MaskSpan, str]:
    """A two-character value in a zero-gap slot, and the pseudonym for it."""
    # pdf_bytes() embeds a fresh random trailer /ID on every call (PyMuPDF's
    # own doing), so the router's content-hash-derived seed -- and therefore
    # which nl_BE first name it draws -- differs each attempt even though
    # _TIGHT_SOURCE never changes. That occasionally draws one of the pool's
    # four two-letter names ("An", "Bo", "El", "Jo"), which fails the length
    # premise below with no masking defect involved. Retry a few times rather
    # than pin a seed, so this keeps exercising the real seeding pipeline.
    for _ in range(10):
        source = pdf_bytes(_TIGHT_SOURCE)
        document = prepare_document(source, "application/pdf")
        start = document.texts[0].text.index("AB")
        span = MaskSpan(start, start + 2, "NAME", "model", page=0)
        router = MaskingRouter(document.content)
        real = router.replacement(span, "AB").text
        if len(real) > 2:  # the premise: it does not fit as-is
            return source, span, real
    raise AssertionError(
        "could not draw a pseudonym longer than the tight slot in 10 attempts"
    )


def _drawn_chip_text(masked: bytes) -> str:
    """What the chip drew, read out of the line it was spliced into.

    The chip lands between the label and the text that follows it, which is
    the whole point of the content-order splice: it is part of that line now,
    not a stray row of its own.
    """
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        page_text = " ".join(result[0].get_text().split())
    _label, _, rest = page_text.partition("X:")
    chip, _, _tail = rest.partition("more text")
    return chip.strip()


def test_a_tight_slot_takes_its_room_from_the_line() -> None:
    # The line makes room rather than the text giving way: the chip is sized
    # by what it draws, and what follows it moves right. Before that, a
    # two-character slot shrank the pseudonym until it truncated.
    source, span, real = _tight_slot()
    masked = redact_document(
        prepare_document(source, "application/pdf"),
        [span],
        redaction_style="bold_chips",
        redaction_mode="pseudonymize",
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        page_text = result[0].get_text()
    assert real in page_text, page_text
    # The text after the slot is still there, and still after it.
    assert "more text right after" in " ".join(page_text.split())


def test_a_tight_slot_truncates_the_real_pseudonym_when_reflow_is_off() -> None:
    # With `PDF_CHIP_REFLOW=off` the older behaviour has to be exactly what it
    # was: truncate the REAL replacement. A shape-preserving redraw would fit
    # every time, and for a label like NAME it is unrelated random letters --
    # worse to show in a visible box than a truncated real name.
    source, span, real = _tight_slot()
    masked = redact_document(
        prepare_document(source, "application/pdf"),
        [span],
        redaction_style="bold_chips",
        redaction_mode="pseudonymize",
        chip_reflow=False,
    )
    # Truncation appends "..."; older outputs used "…", which the base-14
    # fonts substituted down to "·". Strip whatever trailing punctuation
    # truncation left rather than one specific character.
    rendered = _drawn_chip_text(masked).rstrip("…·.")
    assert rendered and real.startswith(rendered)


def test_the_invisible_default_matches_an_explicit_invisible() -> None:
    # `redaction_style="invisible"` (the default) is unaffected by the new
    # parameters — the assertions above this point already exercise it
    # implicitly, this just pins the default explicitly.
    source = pdf_bytes("Patient: Alice Example")
    document = prepare_document(source, "application/pdf")
    start = document.texts[0].text.index("Alice Example")
    masked_default = redact_document(
        document,
        [MaskSpan(start, start + len("Alice Example"), "NAME", "model", page=0)],
    )
    document = prepare_document(source, "application/pdf")
    masked_explicit = redact_document(
        document,
        [MaskSpan(start, start + len("Alice Example"), "NAME", "model", page=0)],
        redaction_style="invisible",
        redaction_mode="pseudonymize",
    )
    with (
        pymupdf.open(stream=masked_default, filetype="pdf") as a,
        pymupdf.open(stream=masked_explicit, filetype="pdf") as b,
    ):
        assert a[0].get_text() == b[0].get_text()


def test_rotated_text_falls_back_to_invisible_rendering() -> None:
    # Rotated source text falls back to the existing invisible-style
    # rendering rather than rendering something broken.
    rotated_document = pymupdf.open()
    rotated_page = rotated_document.new_page()
    rotated_page.insert_text((72, 200), "Alice Example", rotate=90)
    rotated_bytes = rotated_document.tobytes()
    rotated_document.close()
    document = prepare_document(rotated_bytes, "application/pdf")
    start = document.texts[0].text.index("Alice Example")
    masked = redact_document(
        document,
        [MaskSpan(start, start + len("Alice Example"), "NAME", "model", page=0)],
        redaction_style="clinical_soft",
        redaction_mode="detect",
    )
    with pymupdf.open(stream=masked, filetype="pdf") as result:
        assert "Alice Example" not in result[0].get_text()


def test_an_unknown_redaction_style_is_rejected() -> None:
    # An invalid style/mode is rejected rather than silently ignored.
    document = prepare_document(pdf_bytes("Patient: Alice Example"), "application/pdf")
    start = document.texts[0].text.index("Alice Example")
    try:
        redact_document(
            document,
            [MaskSpan(start, start + len("Alice Example"), "NAME", "model", page=0)],
            redaction_style="not_a_style",
        )
    except ValueError as exc:
        assert "redaction_style" in str(exc)
    else:
        raise AssertionError("an unknown redaction_style should be rejected")


def test_a_pdf_with_no_extractable_text_is_rejected() -> None:
    empty = pymupdf.open()
    empty.new_page()
    try:
        prepare_document(empty.tobytes(), "application/pdf")
    except ValueError as exc:
        assert str(exc) == "PDF has no extractable text"
    else:
        raise AssertionError("image-only PDF should be rejected")
    finally:
        empty.close()


def test_an_image_bearing_page_is_refused_without_ocr() -> None:
    # --- an image on the page, with and without OCR ----------------------
    # Without a reader this is refused, and that is the whole of the
    # PDF_OCR=off contract: text inside an image cannot be found by a text
    # detector, so a document carrying one is turned away rather than returned
    # looking masked.
    try:
        prepare_document(_image_and_text_pdf_bytes(), "application/pdf")
    except ValueError as exc:
        assert "image content cannot be masked" in str(exc), exc
    else:
        raise AssertionError("PDFs with images must be rejected without OCR")


def test_an_image_beside_a_text_layer_is_refused_even_with_ocr() -> None:
    # And with a reader it is refused too, because OCR is a *fallback*: it
    # runs on a page that yields no characters, not alongside a text layer
    # that does. This page has one, so the image is never read -- and an
    # unread image is the case the refusal above exists for. Turning it on
    # must not quietly widen what the service accepts beyond scans.
    def _reads_anything(image: object, *, width: int, height: int) -> NoReturn:
        raise AssertionError("a page with a text layer must not reach OCR")

    try:
        prepare_document(
            _image_and_text_pdf_bytes(), "application/pdf", ocr=_reads_anything
        )
    except ValueError as exc:
        assert "image content cannot be masked" in str(exc), exc
    else:
        raise AssertionError("an unread image must be refused even with OCR on")


def test_an_ocr_failure_is_not_blamed_on_the_document() -> None:
    # A page with no text of its own is the fallback case, and there the
    # reader does run. A reader that fails is this side's fault, not the
    # caller's document, so it must not arrive as a 422 blaming the file.
    def _broken(image: object, *, width: int, height: int) -> NoReturn:
        raise RuntimeError("no session")

    big = pymupdf.open()
    big_page = big.new_page()
    big_page.insert_image(
        pymupdf.Rect(72, 72, 272, 172),
        pixmap=pymupdf.Pixmap(pymupdf.csGRAY, 200, 100, bytes(bytearray(20000)), 0),
    )
    big_bytes = big.tobytes()
    big.close()
    try:
        prepare_document(big_bytes, "application/pdf", ocr=_broken)
    except InferenceFailureError as exc:
        assert "OCR could not read" in str(exc)
    else:
        raise AssertionError("an OCR failure must not be reported as a bad document")


def test_a_box_may_not_grow_over_the_character_that_follows_it() -> None:
    # --- a box may not grow over the character that follows the span -----
    # A text layer sets glyphs edge to edge, so the character after a span
    # starts at exactly the span's right edge. The boundary test compared
    # with `>`, so that character never qualified and the box grew past it to
    # whatever came next -- drawing a chip over the full stop closing its own
    # sentence. Whitespace still has to be skipped, or a long pseudonym loses
    # the room it can safely use, because the glyph after a span is usually
    # a space.
    page = pymupdf.open().new_page()
    run = GlyphRun(
        pymupdf.Rect(50.0, 100.0, 100.0, 110.0), _glyph_at("e", 95.0, 100.0), 1
    )
    # Punctuation abutting the span's right edge stops the expansion dead.
    assert _safe_expand(run, [_glyph_at(".", 100.0, 102.5)], page) == 0.0
    # A space does not: it carries no ink, so the next inked glyph bounds it.
    room = _safe_expand(
        run, [_glyph_at(" ", 100.0, 102.5), _glyph_at("C", 102.5, 109.0)], page
    )
    assert room == 2.5, room
    # Nothing to the right at all still falls back to the page margin.
    assert _safe_expand(run, [], page) == page.rect.width - 36 - 100.0


def test_a_narrow_detect_chip_drops_its_score() -> None:
    # --- a detect chip drops its score rather than truncating it ---------
    # "DATE 100%" was fitted as one string, so a narrow date box truncated
    # the pair and drew "DATE 1..." -- a truncated percentage is a different
    # number, and nothing on the page says which one it was. The score is
    # the part that gets given up.
    document = pymupdf.open()
    page = document.new_page()
    narrow = pymupdf.Rect(50.0, 100.0, 78.0, 110.0)
    run = GlyphRun(narrow, _glyph_at("e", 73.0, 78.0), 1)
    blocker = [_glyph_at("C", 78.0, 84.0)]  # abutting: no room to grow
    drawn = _render_styled_run(
        page,
        document,
        {},
        run,
        "2024-05-11",
        blocker,
        "DATE",
        1.0,
        "bold_chips",
        "detect",
    )
    assert drawn == "DATE", drawn
    document.close()


def test_a_wide_detect_chip_keeps_its_score() -> None:
    # Given the room, the score is kept: dropping it is a fallback, not the
    # new default.
    document = pymupdf.open()
    page = document.new_page()
    wide = pymupdf.Rect(50.0, 100.0, 140.0, 110.0)
    drawn = _render_styled_run(
        page,
        document,
        {},
        GlyphRun(wide, _glyph_at("e", 135.0, 140.0), 1),
        "2024-05-11",
        [_glyph_at("C", 140.0, 146.0)],
        "DATE",
        1.0,
        "bold_chips",
        "detect",
    )
    assert drawn == "DATE 100%", drawn
    document.close()


def _embedded_face(name: str = "F9"):
    """A page carrying a real embedded font, and a glyph styled to match it."""
    document = pymupdf.open()
    page = document.new_page()
    face = pymupdf.Font("cour")
    page.insert_font(fontname=name, fontbuffer=face.buffer)
    page.insert_text((72, 100), "Astridlaan", fontname=name, fontsize=8)
    # The name the *page* knows the face by, not the one pymupdf built it
    # under: "Courier" would be caught by the built-in table before the
    # embedded copy is ever considered, and the test would prove nothing.
    embedded = page.get_fonts(full=True)[0][3]
    style = PdfGlyph(
        text="A",
        bbox=(72.0, 92.0, 80.0, 100.0),
        origin=(72.0, 100.0),
        font=embedded,
        size=8.0,
        color=0,
        direction=(1.0, 0.0),
        flags=0,
    )
    return document, page, style


def test_a_font_that_cannot_write_the_replacement_is_not_used_to_draw_it() -> None:
    """The failure this guards has no error and no log line: it just renders.

    A PDF may embed only the glyphs its own words used, and the subsetter may
    drop the character map with them. `insert_text` through such a face maps
    every character to glyph 0, so the page draws a row of .notdef boxes and
    the text layer extracts as NUL -- which is how a whole clinical footer set
    in `AAAAAA+Consolas` left the pipeline unreadable while every test stayed
    green.

    Asserted through `_font_name` rather than through a fixture PDF, because
    pymupdf's own subsetter keeps the cmap and cannot produce the input: the
    check that matters is that a face which cannot map the text is passed
    over, whatever made it that way.
    """
    document, page, style = _embedded_face()
    try:
        fonts: dict = {}
        writable = _font_name(page, document, style, fonts, "Astridlaan 145")
        assert writable.name.startswith("maskfont"), writable.name

        unwritable = _font_name(page, document, style, fonts, "アストリッド")
        assert not unwritable.name.startswith("maskfont"), unwritable.name
        assert unwritable.name == "cour", unwritable.name
    finally:
        document.close()


def test_a_fallback_face_keeps_the_width_and_weight_of_the_one_it_replaces() -> None:
    # Substituting is only better than boxes if the line still reads: a
    # monospaced footer set in Helvetica loses its columns, and a bold
    # signature set in book weight stops looking like one.
    assert _builtin_like("aaaaaa+consolas", 0) == "cour"
    assert _builtin_like("dejavu sans mono", 16) == "cobo"
    assert _builtin_like("liberation serif", 2) == "tiit"
    assert _builtin_like("calibri", 16 | 2) == "hebi"
    assert _builtin_like("calibri", 0) == "helv"


def test_only_the_characters_a_replacement_needs_have_to_be_writable() -> None:
    # A space carries no glyph, so a face without one is not thereby unusable
    # -- and every pseudonym with a space in it would otherwise be refused.
    helv = pymupdf.Font("helv")
    assert _covers(helv, "Steffi Desmet Polfliet")
    assert not _covers(helv, "アストリッド")


def test_a_run_of_pure_whitespace_still_needs_a_face_that_can_draw_it() -> None:
    """A reflowed line hands its moved glyphs over one at a time.

    Some of those glyphs are a single space, and ignoring whitespace the way
    the test above does would let a face that maps nothing through the guard:
    the space drew as a .notdef box, and two of them sat in a masked footer.
    When whitespace is all there is, the space is the character to check.
    """
    document, page, style = _embedded_face()
    try:
        assert _font_name(page, document, style, {}, " ").name.startswith("maskfont")

        class _Blind:
            """A face that maps nothing, the way a cmap-less subset does."""

            @staticmethod
            def has_glyph(_code: int) -> int:
                return 0

        assert not _covers(cast(Any, _Blind()), " ")
    finally:
        document.close()


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
