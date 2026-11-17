#!/usr/bin/env python3
"""Pin how a line makes room for a chip, and how a chip stays off its neighbour.

Two defects live here, found by rendering a generated corpus rather than by
reading the code:

* A chip grown to exactly the bounding glyph's left edge still painted its own
  border over that glyph, because `draw_rect` centres a stroke on the edge it
  is given. Measured at 0.45pt of ink on the "j" of "jaar", on every
  clinical_soft page.
* A box too narrow for what it had to say shrank the text and then truncated
  it, so a two-digit age slot drew "A..." -- a chip naming neither its label
  nor its value.

The geometry cases below use the reflow planner directly: it takes plain
rectangles and returns plain offsets, so the policy can be pinned without
synthesising a PDF for every one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import MaskSpan, prepare_document, redact_document
from masking_engine.app.services import pdf_line_reflow as reflow
from masking_engine.app.services import pdf_masking_service as masking
from masking_engine.app.services import pdf_redaction_styles as styles

_RIGHT = 1000.0


def slot(key: int, x0: float, x1: float, wanted: float) -> reflow.Slot:
    return reflow.Slot(key, x0, x1, 100.0, 112.0, wanted)


def fixed(key: int, x0: float, x1: float) -> reflow.Fixed:
    return reflow.Fixed(key, x0, x1, 100.0, 112.0)


# --------------------------------------------------------------- geometry
def test_a_chip_that_already_fits_moves_nothing() -> None:
    plan = reflow.plan([slot(0, 10, 40, 25)], [fixed(1, 43, 60)], _RIGHT)
    assert plan.slot_shift == {}
    assert plan.glyph_shift == {}


def test_a_wider_chip_pushes_the_rest_of_the_line_right() -> None:
    # The chip covers 10-40 and wants 50pt, so it now ends at 60. The word
    # after it started at 43 behind a 3pt gap; that gap may shrink to a word
    # space but no further, so the word lands at 61.5.
    plan = reflow.plan([slot(0, 10, 40, 50)], [fixed(1, 43, 60)], _RIGHT)
    assert plan.granted == {0}
    assert plan.slot_shift == {}
    assert round(43 + plan.glyph_shift[1], 2) == 61.5


def test_letters_inside_a_word_move_together() -> None:
    # Glyphs set edge to edge have no gap to give up, so they travel rigidly
    # and the word does not come apart.
    glyphs = [fixed(1, 40, 46), fixed(2, 46, 52), fixed(3, 52, 58)]
    plan = reflow.plan([slot(0, 10, 40, 60)], glyphs, _RIGHT)
    shifts = {key: round(value, 3) for key, value in plan.glyph_shift.items()}
    assert shifts == {1: 30.0, 2: 30.0, 3: 30.0}, shifts


def test_a_gutter_absorbs_the_shift_and_the_next_column_stays_put() -> None:
    # The second column sits behind a 60pt gap. That gap gives up everything
    # above a word space, so the column never moves -- which is what keeps a
    # two-column page, a ruled table and a right-aligned field intact.
    plan = reflow.plan(
        [slot(0, 10, 40, 60)],
        [fixed(1, 100, 160), fixed(2, 160, 200)],
        _RIGHT,
    )
    assert plan.glyph_shift == {}
    assert plan.granted == {0}


def test_a_line_with_no_room_left_is_refused_whole() -> None:
    # Refusing the line rather than part of it keeps the outcome predictable:
    # either it reflows or it renders exactly the way it did before.
    plan = reflow.plan([slot(0, 10, 40, 400)], [fixed(1, 43, 90)], right_limit=100.0)
    assert plan.granted == set()
    assert plan.slot_shift == {} and plan.glyph_shift == {}


def test_two_tightly_set_chips_on_a_line_accumulate() -> None:
    # Tightly set on purpose: with room between them each surplus would be
    # absorbed on the spot, which is the case the gutter test covers. Here
    # the second chip inherits the first one's push and adds its own.
    plan = reflow.plan(
        [slot(0, 10, 40, 60), slot(1, 42, 72, 150)], [fixed(2, 74, 100)], _RIGHT
    )
    assert plan.granted == {0, 1}
    assert plan.slot_shift[1] > 0
    assert plan.glyph_shift[2] > plan.slot_shift[1]


def test_a_chip_keeps_its_chrome_clear_of_the_glyph_after_it() -> None:
    # A comma sits flush against the value it follows, so the gap the document
    # wrote is nothing and preserving it exactly would put the chip's border
    # on the comma. Only after a chip: the letters of an ordinary word still
    # move rigidly, or a reflowed word would come apart.
    packed = [fixed(1, 40, 44), fixed(2, 44, 50)]
    plan = reflow.plan([slot(0, 10, 40, 60)], packed, _RIGHT, clearance=0.45)
    assert round(plan.glyph_shift[1], 2) == round(30 + 0.45, 2)
    assert round(plan.glyph_shift[2], 2) == round(30 + 0.45, 2)

    without = reflow.plan([slot(0, 10, 40, 60)], packed, _RIGHT)
    assert round(without.glyph_shift[1], 2) == 30.0


def test_a_second_line_is_planned_on_its_own() -> None:
    below = reflow.Fixed(2, 43, 60, 130.0, 142.0)
    plan = reflow.plan([slot(0, 10, 40, 60)], [fixed(1, 43, 60), below], _RIGHT)
    assert 1 in plan.glyph_shift
    assert 2 not in plan.glyph_shift


# ------------------------------------------------------------- ink bleed
def test_a_stroked_style_reserves_the_ink_it_paints_outside_its_box() -> None:
    # `draw_rect` centres a stroke on the edge, so half of it lands beyond the
    # rectangle. A caller fitting a box up against real content has to reserve
    # that much or it paints its own border over that content.
    assert styles.ink_bleed("clinical_soft") > 0
    assert styles.ink_bleed("bold_chips") == 0
    assert styles.ink_bleed("invisible") == 0


def test_the_expansion_budget_stops_short_of_the_glyph_that_bounds_it() -> None:
    source = _pdf("Patient is 81 jaar oud.")
    document = prepare_document(source, "application/pdf")
    glyphs = document.texts[0].glyphs
    text = document.texts[0].text
    start = text.index("81")
    run = masking._runs_for_span(glyphs, MaskSpan(start, start + 2, "AGE", "model"))[0]

    with pymupdf.open(stream=document.content, filetype="pdf") as opened:
        page = opened[0]
        bare = masking._safe_expand(run, glyphs, page)
        bleed = styles.ink_bleed("clinical_soft")
        reserved = masking._safe_expand(run, glyphs, page, bleed)
    assert round(bare - reserved, 3) == round(bleed, 3)


# ----------------------------------------------------------- rendered PDF
def _pdf(*lines: str, fontsize: float = 10) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    y = 120.0
    for line in lines:
        page.insert_text((72, y), line, fontsize=fontsize)
        y += 20
    data = document.tobytes(deflate=True)
    document.close()
    return data


def _mask(source: bytes, spans: list[MaskSpan], **kwargs) -> bytes:
    return redact_document(prepare_document(source, "application/pdf"), spans, **kwargs)


def _span(source: bytes, needle: str, label: str, confidence: float | None):
    document = prepare_document(source, "application/pdf")
    start = document.texts[0].text.index(needle)
    return MaskSpan(
        start, start + len(needle), label, "model", page=0, confidence=confidence
    )


def _page_text(data: bytes) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return document[0].get_text()


def test_a_narrow_detect_box_draws_its_whole_label() -> None:
    source = _pdf("Patient is 81 jaar oud.")
    spans = [_span(source, "81", "AGE", 0.99)]
    kwargs = {"redaction_style": "clinical_soft", "redaction_mode": "detect"}

    with_reflow = _page_text(_mask(source, spans, **kwargs))
    assert "AGE 99%" in with_reflow, with_reflow

    without = _page_text(_mask(source, spans, chip_reflow=False, **kwargs))
    assert "AGE 99%" not in without
    assert "A..." in without or "A·" in without, without


def test_reflow_keeps_the_words_and_the_spaces_between_them() -> None:
    # A space is left out of the planning -- the gap it stands for is already
    # the distance between its neighbours -- but it still has to travel with
    # them. One that stayed behind ended up inside the word before it and the
    # page extracted as "moretextrightafter".
    source = _pdf("Patient is 81 jaar oud en woont in Gent.")
    spans = [_span(source, "81", "AGE", 0.99)]
    masked = _page_text(
        _mask(
            source,
            spans,
            redaction_style="clinical_soft",
            redaction_mode="detect",
        )
    )
    assert "jaar oud en woont in Gent." in " ".join(masked.split()), masked


def test_a_reflowed_chip_does_not_paint_over_the_next_word() -> None:
    source = _pdf("Patient is 81 jaar oud.")
    spans = [_span(source, "81", "AGE", 0.99)]
    drawn: list[tuple] = []
    original = styles.draw_box

    def record(page, rect, style, mode, text, size, style_variant="clinical_soft"):
        drawn.append((tuple(rect), style_variant))
        return original(page, rect, style, mode, text, size, style_variant)

    styles.draw_box = record
    try:
        masked = _mask(
            source,
            spans,
            redaction_style="clinical_soft",
            redaction_mode="detect",
        )
    finally:
        styles.draw_box = original

    assert drawn, "the fixture must actually paint a chip"
    with pymupdf.open(stream=masked, filetype="pdf") as document:
        words = document[0].get_text("words")
    for rect, variant in drawn:
        inked = rect[2] + styles.ink_bleed(variant)
        for word in words:
            if word[4].strip() in {"AGE", "99%"}:
                continue
            starts_after = word[0] >= rect[0]
            overlaps_row = word[1] < rect[3] and word[3] > rect[1]
            if starts_after and overlaps_row:
                assert word[0] >= inked, f"{word[4]!r} at {word[0]} under chip {rect}"


def test_a_reflowed_line_is_redrawn_to_its_end() -> None:
    """The invariant behind a regression a real clinical letter found.

    A shift decays to nothing part way along a long line, so the tail beyond
    that point does not move. Left in the page's own content while everything
    before it was erased and redrawn, that tail became a second fragment of
    the line -- and `pdf_content_order`, which splices a chip in by splitting
    the text object it was redacted out of and resuming the remainder after
    it, took the stranded tail for that remainder. On a Dutch letter "aan
    defecatie" was drawn at x 146 on top of "Begin van" at the start of its
    own line.

    Pinned on `_extend_to_line_end` rather than on rendered output: the
    corruption needs the shift to die at exactly the right point in a line one
    text object wide, and five synthetic pages failed to land in that window.
    The mechanism is the part that has to hold -- once a line reflows, every
    glyph after the chip is redrawn, whether or not it moves, so the line
    leaves one fragment instead of two.
    """
    line = "04-2025: Begin van pijn gerelateerd aan defecatie"
    document = prepare_document(_pdf(line), "application/pdf")
    glyphs = document.texts[0].glyphs
    start = document.texts[0].text.index("04-2025")
    covered = set(range(start, start + len("04-2025")))

    # Only the first word after the chip moves; the shift dies there.
    first_after = start + len("04-2025") + 2
    shift = {first_after: 6.0}
    masking._extend_to_line_end(glyphs, covered, shift)

    baseline = glyphs[first_after].origin[1]
    unredrawn = [
        index
        for index, glyph in enumerate(glyphs)
        if glyph is not None
        and glyph.text.strip()
        and index not in covered
        and glyph.origin[1] == baseline
        and glyph.bbox[0] >= glyphs[first_after].bbox[2]
        and index not in shift
    ]
    assert not unredrawn, "".join(glyphs[i].text for i in unredrawn)
    assert all(value == 0.0 for index, value in shift.items() if index != first_after)


def test_reflow_is_deterministic() -> None:
    source = _pdf("Patient is 81 jaar oud.", "Arts Jens Vandeputte hier.")
    spans = [_span(source, "81", "AGE", 0.99)]
    kwargs = {"redaction_style": "bold_chips", "redaction_mode": "detect"}
    assert _mask(source, spans, **kwargs) == _mask(source, spans, **kwargs)


def test_turning_reflow_off_leaves_the_source_layout_alone() -> None:
    # The escape hatch has to be a real one: with it off, nothing but the
    # replaced value moves.
    source = _pdf("Patient is 81 jaar oud en woont in Gent.")
    spans = [_span(source, "81", "AGE", 0.99)]
    masked = _mask(
        source,
        spans,
        redaction_style="clinical_soft",
        redaction_mode="detect",
        chip_reflow=False,
    )
    original_words = {
        (round(word[0], 2), word[4])
        for word in _words(_pdf("Patient is 81 jaar oud en woont in Gent."))
    }
    for word in _words(masked):
        if word[4].strip() in {"81", "AGE", "A...", "A·"}:
            continue
        assert (round(word[0], 2), word[4]) in original_words, word[4]


def test_an_invisible_pseudonym_is_written_at_its_natural_width() -> None:
    """The default style reflows too, or a long surrogate is drawn crushed.

    `invisible` draws no box, so it was left out of the reflow and condensed
    every over-long replacement into the slot the value had vacated -- on a
    production letter, "De Smet" became "Steffi Desmet Polfliet" squeezed to a
    third of its width, unreadable beside the 10pt line it sits in. It is also
    the style every deployment runs, so the fallback was the whole behaviour.

    The width is measured against the font the replacement is drawn in rather
    than against the slot: condensing is a horizontal scale, so a crushed
    pseudonym still occupies the slot exactly and only its own metrics say it
    was squeezed.
    """
    source = _pdf("Patient Jo Li werd gezien op 29-11-2009.")
    spans = [_span(source, "Jo Li", "NAME", 0.99)]
    masked = _mask(source, spans)

    words = _words(masked)
    tail = [word for word in words if word[4] == "werd"]
    assert len(tail) == 1, words
    pseudonym = [
        word
        for word in words
        if word[4] not in {"Patient", "werd", "gezien", "op", "29-11-2009."}
    ]
    assert pseudonym, words

    helv = pymupdf.Font("helv")
    for word in pseudonym:
        natural = helv.text_length(word[4], fontsize=10)
        assert word[2] - word[0] >= natural - 0.5, f"{word[4]!r} was condensed"

    drawn = max(word[2] for word in pseudonym)
    assert tail[0][0] >= drawn, f"the line did not move aside: {words}"


def test_an_invisible_pseudonym_that_fits_moves_nothing() -> None:
    # The reflow only ever pays out surplus, so a replacement no wider than
    # the value it covers must leave the line exactly where it was. Without
    # this, turning the default style on for reflow would be free to shuffle
    # every page it touches.
    source = _pdf("Patient Jan Janssens werd gezien op 29-11-2009.")
    before = {word[4]: round(word[0], 2) for word in _words(source)}
    spans = [_span(source, "29-11-2009", "DATE", 0.99)]
    after = {word[4]: round(word[0], 2) for word in _words(_mask(source, spans))}
    for word in ("Patient", "Jan", "Janssens", "werd", "gezien", "op"):
        assert after[word] == before[word], (word, before[word], after[word])


def _words(data: bytes):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        return document[0].get_text("words")


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
