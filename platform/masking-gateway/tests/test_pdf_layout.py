#!/usr/bin/env python3
"""Pin the reading-order machinery: fragments in, ordered text out.

`pdf_extraction` decides what the detector ever sees. A layout rule that
quietly stops firing costs recall on the documents it was written for and
nothing anywhere says so, which is why each rule is pinned here against the
shape of page it exists for rather than through a whole-document round trip.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.services import pdf_extraction as extraction
from masking_engine.app.services.pdf_extraction import (
    Fragment,
    PageMetrics,
    PdfGlyph,
    assemble_region,
    assemble_region_with_glyphs,
    clean_lines,
    column_cuts,
    extract_page_lines,
    extract_pdf_pages,
    extract_text_from_pdf,
    find_running_lines,
    group_rows,
    page_metrics,
    xy_cut,
)


def frag(
    x0: float, y0: float, x1: float, y1: float, text: str = "abc", **kw
) -> Fragment:
    return Fragment(x0, y0, x1, y1, text, **kw)


def glyph(character: str, x0: float = 0.0, **kw) -> PdfGlyph:
    return PdfGlyph(
        text=character,
        bbox=(x0, 0.0, x0 + 5.0, 10.0),
        origin=(x0, 10.0),
        font="helv",
        size=11.0,
        color=0,
        direction=(1.0, 0.0),
        flags=0,
        **kw,
    )


METRICS = PageMetrics(line_height=10.0, char_width=5.0, x_gap=12.5, y_gap=5.5)


# --------------------------------------------------------------------------- #
# Orientation
# --------------------------------------------------------------------------- #


def test_a_baseline_direction_rounds_to_a_quarter_turn() -> None:
    assert extraction._quarter_turns((1.0, 0.0)) == 0
    assert extraction._quarter_turns((0.0, 1.0)) == 1
    assert extraction._quarter_turns((-1.0, 0.0)) == 2
    assert extraction._quarter_turns((0.0, -1.0)) == 3
    # A baseline slightly off the axis still reads as that axis.
    assert extraction._quarter_turns((0.99, 0.14)) == 0
    assert extraction._quarter_turns((-0.99, -0.14)) == 2
    # A degenerate direction is upright rather than an error.
    assert extraction._quarter_turns((0.0, 0.0)) == 0


def test_a_rotated_box_is_read_in_its_own_frame() -> None:
    assert extraction._reading_frame((10.0, 20.0, 30.0, 40.0), 0) == (
        10.0,
        20.0,
        30.0,
        40.0,
    )
    assert extraction._reading_frame((10.0, 20.0, 30.0, 40.0), 1) == (
        20.0,
        -30.0,
        40.0,
        -10.0,
    )
    assert extraction._reading_frame((10.0, 20.0, 30.0, 40.0), 2) == (
        -30.0,
        -40.0,
        -10.0,
        -20.0,
    )
    assert extraction._reading_frame((10.0, 20.0, 30.0, 40.0), 3) == (
        -40.0,
        10.0,
        -20.0,
        30.0,
    )


def test_a_region_holds_a_box_by_its_centre() -> None:
    region = frag(0, 0, 100, 100)
    assert extraction._contains(region, 40, 40, 60, 60)
    # A box mostly outside is still held while its centre is in.
    assert extraction._contains(region, 90, 90, 110, 110)
    assert not extraction._contains(region, 95, 95, 120, 120)
    # The edge counts as inside.
    assert extraction._contains(region, 100, 100, 100, 100)
    assert not extraction._contains(region, -20, 40, 0, 60)


# --------------------------------------------------------------------------- #
# Glyph normalisation
# --------------------------------------------------------------------------- #


def test_whitespace_collapses_without_losing_a_glyph_per_character() -> None:
    glyphs = [glyph(c, x0=i * 5.0) for i, c in enumerate(" a \t b\n")]
    text, kept = extraction._normalise_glyphs(glyphs)
    assert text == "a b"
    assert len(kept) == len(text)
    assert [g.text for g in kept] == ["a", " ", "b"]
    # The collapsed space keeps the source glyph's geometry, not a fresh one.
    assert kept[1].origin == glyphs[2].origin


def test_normalising_a_rewritten_glyph_keeps_every_other_field() -> None:
    glyphs = [glyph("a"), glyph(" ", x0=5.0, ocr=True), glyph("b", x0=10.0)]
    text, kept = extraction._normalise_glyphs(glyphs)
    assert text == "a b"
    assert kept[1].text == " "
    assert kept[1].ocr is True
    assert kept[1].font == "helv"


def test_an_all_whitespace_line_normalises_away() -> None:
    assert extraction._normalise_glyphs([glyph(" "), glyph(" ", x0=5.0)]) == ("", [])
    assert extraction._normalise_glyphs([]) == ("", [])


# --------------------------------------------------------------------------- #
# Page metrics
# --------------------------------------------------------------------------- #


def test_metrics_come_from_the_page_s_own_typography() -> None:
    fragments = [
        frag(0, 0, 30, 10, "abcdef"),
        frag(0, 20, 30, 30, "ghijkl"),
        frag(0, 40, 30, 52, "mnopqr"),
    ]
    metrics = page_metrics(fragments, page_width=600.0)
    assert metrics.line_height == 10.0
    assert metrics.char_width == 5.0
    assert metrics.x_gap == 2.5 * 5.0
    assert metrics.y_gap == 0.55 * 10.0


def test_metrics_fall_back_when_the_page_offers_nothing_to_measure() -> None:
    metrics = page_metrics([], page_width=600.0)
    assert metrics.line_height == 12.0
    assert metrics.char_width == 6.0
    # The floors: a gutter is at least 1.5% of the page, a break at least 2pt.
    assert page_metrics([], page_width=2000.0).x_gap == 0.015 * 2000.0
    assert page_metrics([frag(0, 0, 30, 1, "abcdef")], 100.0).y_gap == 2.0


def test_a_table_and_a_short_run_are_not_measured() -> None:
    # An atomic table is excluded from both medians...
    with_table = page_metrics(
        [frag(0, 0, 30, 10, "abcdef"), frag(0, 0, 300, 500, "t", atomic=True)],
        page_width=600.0,
    )
    assert with_table.line_height == 10.0
    # ...and a run under three characters cannot set the character width.
    assert page_metrics([frag(0, 0, 90, 10, "ab")], 600.0).char_width == 5.0


# --------------------------------------------------------------------------- #
# Whitespace projection
# --------------------------------------------------------------------------- #


def test_gaps_are_the_holes_between_merged_intervals() -> None:
    assert extraction._whitespace_gaps([]) == []
    assert extraction._whitespace_gaps([(0.0, 10.0)]) == []
    assert extraction._whitespace_gaps([(0.0, 10.0), (30.0, 40.0)]) == [(10.0, 30.0)]
    # Overlapping and touching intervals merge rather than producing a gap.
    assert extraction._whitespace_gaps([(0.0, 10.0), (5.0, 20.0)]) == []
    assert extraction._whitespace_gaps([(0.0, 10.0), (10.0, 20.0)]) == []
    # A nested interval must not shorten the run it sits inside.
    assert extraction._whitespace_gaps([(0.0, 40.0), (10.0, 20.0), (60.0, 70.0)]) == [
        (40.0, 60.0)
    ]


def test_a_cut_crossed_by_exactly_one_row_is_a_hole_not_a_gutter() -> None:
    # Three rows spanning the same gutter: a genuine two-column layout.
    columns = [frag(0, y, 40, y + 10) for y in (0, 20, 40)] + [
        frag(60, y, 100, y + 10) for y in (0, 20, 40)
    ]
    assert column_cuts(columns, [50.0]) == [50.0]
    # One row spanning it: a hole punched into a single line.
    hole = [frag(0, 0, 40, 10), frag(60, 0, 100, 10), frag(0, 20, 100, 30)]
    assert column_cuts(hole, [50.0]) == []
    # No row spanning it at all is not a hole either.
    assert column_cuts([frag(0, 0, 40, 10)], [50.0]) == [50.0]


# --------------------------------------------------------------------------- #
# Row grouping and assembly
# --------------------------------------------------------------------------- #


def test_rows_are_found_by_overlap_and_sorted_left_to_right() -> None:
    left = frag(50, 0, 90, 12, "value")
    label = frag(0, 2, 40, 10, "label")
    rows = group_rows([left, label])
    assert [[f.text for f in row] for row in rows] == [["label", "value"]]


def test_a_fragment_that_barely_overlaps_starts_a_new_row() -> None:
    # 0.4 of the shorter fragment is the threshold: 4pt of a 10pt line.
    assert len(group_rows([frag(0, 0, 10, 10), frag(0, 6, 10, 16)])) == 1
    assert len(group_rows([frag(0, 0, 10, 10), frag(0, 7, 10, 17)])) == 2


def test_a_table_is_never_merged_into_a_row() -> None:
    table = frag(0, 0, 100, 100, "|a|b|", atomic=True)
    beside = frag(0, 10, 50, 20, "prose")
    assert len(group_rows([table, beside])) == 2
    assert len(group_rows([beside, table])) == 2


def test_a_row_is_spaced_by_geometry_or_by_the_source_padding() -> None:
    # A gap wider than a fifth of a character separates.
    assert assemble_region(
        [frag(0, 0, 40, 10, "left"), frag(42, 0, 80, 10, "right")], METRICS
    ) == ["left right"]
    # A butted pair does not...
    assert assemble_region(
        [frag(0, 0, 40, 10, "left"), frag(40, 0, 80, 10, "right")], METRICS
    ) == ["leftright"]
    # ...unless the source text carried the padding itself.
    assert assemble_region(
        [
            frag(0, 0, 40, 10, "left", pad_right=True),
            frag(40, 0, 80, 10, "right"),
        ],
        METRICS,
    ) == ["left right"]
    assert assemble_region(
        [
            frag(0, 0, 40, 10, "left"),
            frag(40, 0, 80, 10, "right", pad_left=True),
        ],
        METRICS,
    ) == ["left right"]


def test_a_table_fragment_becomes_its_own_lines() -> None:
    table = frag(0, 0, 100, 40, "| a | b |\n| - | - |\n| 1 | 2 |", atomic=True)
    assert assemble_region([table], METRICS) == ["| a | b |", "| - | - |", "| 1 | 2 |"]


def test_the_glyph_map_stays_one_entry_per_character() -> None:
    left = frag(0, 0, 10, 10, "ab", glyphs=(glyph("a"), glyph("b", x0=5.0)))
    right = frag(20, 0, 30, 10, "cd", glyphs=(glyph("c", x0=20.0), glyph("d", x0=25.0)))
    ((text, glyphs),) = assemble_region_with_glyphs([left, right], METRICS)
    assert text == "ab cd"
    assert len(glyphs) == len(text)
    # The inserted separator has no source character.
    assert glyphs[2] is None
    assert [g.text for g in glyphs if g is not None] == ["a", "b", "c", "d"]


def test_a_table_has_no_source_geometry_so_the_glyph_path_refuses_it() -> None:
    table = frag(0, 0, 100, 40, "| a |", atomic=True)
    try:
        assemble_region_with_glyphs([table], METRICS)
    except ValueError as error:
        assert str(error) == "table fragments require a cell geometry map"
    else:
        raise AssertionError("assembled a table into the glyph map")


def test_a_fragment_whose_glyphs_do_not_match_its_text_is_refused() -> None:
    broken = frag(0, 0, 10, 10, "abc", glyphs=(glyph("a"),))
    try:
        assemble_region_with_glyphs([broken], METRICS)
    except ValueError as error:
        assert str(error) == "PDF text and glyph map lengths differ"
    else:
        raise AssertionError("accepted a short glyph map")


# --------------------------------------------------------------------------- #
# Recursive XY-cut
# --------------------------------------------------------------------------- #


def test_a_region_with_nothing_to_cut_is_a_leaf() -> None:
    one = [frag(0, 0, 10, 10)]
    assert xy_cut(one, METRICS) == [one]
    assert xy_cut([], METRICS) == [[]]
    # Two fragments with no qualifying gap on either axis stay together.
    touching = [frag(0, 0, 10, 10), frag(10, 0, 20, 10)]
    assert xy_cut(touching, METRICS) == [touching]


def test_recursion_stops_at_the_depth_limit() -> None:
    stack = [frag(0, y, 10, y + 10) for y in range(0, 20 * 60, 20)]
    assert xy_cut(stack, METRICS, depth=extraction.MAX_CUT_DEPTH) == [stack]
    assert len(xy_cut(stack, METRICS)) > 1


def test_full_height_columns_are_read_left_to_right() -> None:
    # Prose in the cells, so the region is a two-column layout rather than the
    # borderless data grid the aligned-table guard would send back to rows.
    left = [frag(0, y, 40, y + 10, "left column running text") for y in (0, 20, 40)]
    right = [frag(60, y, 100, y + 10, "right column running text") for y in (0, 20, 40)]
    regions = xy_cut(left + right, METRICS)
    read = [f.text for region in regions for f in region]
    assert read[:3] == ["left column running text"] * 3
    assert read[3:] == ["right column running text"] * 3


def test_stacked_bands_keep_the_page_s_top_to_bottom_flow() -> None:
    top = frag(0, 0, 100, 10, "top")
    bottom = frag(0, 40, 100, 50, "bottom")
    assert [f.text for region in xy_cut([bottom, top], METRICS) for f in region] == [
        "top",
        "bottom",
    ]


def test_a_borderless_grid_is_read_row_major() -> None:
    # Three rows of two short cells, equally deep: a data grid, not a layout.
    grid = [
        frag(x, y, x + 30, y + 10, f"c{x}{y}") for y in (0, 20, 40) for x in (0, 60)
    ]
    regions = xy_cut(grid, METRICS)
    # Every region holds a whole row rather than a column of one side.
    assert all(
        len({f.x0 for f in region}) != 1 or len(region) == 1 for region in regions
    )
    assert [f.text for f in regions[0]] == ["c00", "c600"]


def test_full_height_is_measured_against_the_region() -> None:
    fragments = [frag(0, 0, 10, 100)]
    assert extraction._full_height_columns([[frag(0, 0, 10, 100)]], fragments)
    assert not extraction._full_height_columns([[frag(0, 0, 10, 70)]], fragments)
    # Exactly the coverage fraction still counts.
    assert extraction._full_height_columns([[frag(0, 0, 10, 80)]], fragments)
    # A region with no height cannot have full-height columns.
    flat = [frag(0, 5, 10, 5)]
    assert not extraction._full_height_columns([flat], flat)


def test_an_aligned_table_needs_a_grid_enough_rows_and_short_cells() -> None:
    def cells(text: str) -> list[Fragment]:
        return [frag(x, y, x + 30, y + 10, text) for y in (0, 20, 40) for x in (0, 60)]

    grid = cells("v")
    parts = [[f for f in grid if f.x0 == 0], [f for f in grid if f.x0 == 60]]
    assert extraction._aligned_table(parts, grid)
    # One column is not a table.
    assert not extraction._aligned_table(parts[:1], grid)
    # Too few rows is not a grid.
    short = grid[:4]
    assert not extraction._aligned_table(
        [[f for f in short if f.x0 == 0], [f for f in short if f.x0 == 60]], short
    )
    # Prose cells are a multi-column layout, not a data grid.
    prose = cells("one two three four five")
    assert not extraction._aligned_table(
        [[f for f in prose if f.x0 == 0], [f for f in prose if f.x0 == 60]], prose
    )
    # Columns of different depth are stacked blocks, not a grid.
    uneven = grid + [frag(0, 60, 30, 70, "v")]
    assert not extraction._aligned_table(
        [[f for f in uneven if f.x0 == 0], [f for f in uneven if f.x0 == 60]], uneven
    )


# --------------------------------------------------------------------------- #
# OCR provenance
# --------------------------------------------------------------------------- #


def test_only_glyphs_inside_an_ocr_box_are_flagged_as_ocr() -> None:
    inside = glyph("a", x0=10.0)
    outside = glyph("b", x0=500.0)
    marked = extraction._mark_ocr_glyphs([inside, None, outside], ((0, 0, 100, 100),))
    assert marked[0].ocr is True
    assert marked[1] is None
    assert marked[2].ocr is False


def test_without_boxes_the_glyph_map_is_returned_untouched() -> None:
    glyphs = [glyph("a"), None]
    assert extraction._mark_ocr_glyphs(glyphs, ()) is glyphs


def test_a_glyph_already_marked_stays_marked() -> None:
    already = glyph("a", x0=500.0, ocr=True)
    assert extraction._mark_ocr_glyphs([already], ((0, 0, 100, 100),))[0] is already


# --------------------------------------------------------------------------- #
# Cleaning for downstream models
# --------------------------------------------------------------------------- #


def test_a_line_s_shape_ignores_case_padding_and_digits() -> None:
    assert extraction._shape("  Page 3 of 12 ") == "page # of #"
    assert extraction._shape("PAGE 4 OF 12") == "page # of #"


def test_running_lines_need_a_majority_of_pages_and_two_at_least() -> None:
    header = "Ziekenhuis Sint-Jan"
    pages = [[header, body, "Page 1 of 3"] for body in ("een", "twee", "drie")]
    assert find_running_lines(pages) == {"ziekenhuis sint-jan", "page # of #"}
    # A single page has nothing to repeat across.
    assert find_running_lines(pages[:1]) == set()
    assert find_running_lines(pages, min_pages=4) == set()
    # Present on one page of three is below the half-of-pages threshold.
    odd = [[header, "a"], ["b"], ["c"]]
    assert find_running_lines(odd) == set()


def test_only_the_three_lines_at_each_margin_can_run() -> None:
    middle = "buried"
    pages = [["a", "b", "c", middle, "x", "y", "z"] for _ in range(2)]
    assert "buried" not in find_running_lines(pages)
    assert {"a", "b", "c", "x", "y", "z"} <= find_running_lines(pages)


def test_a_short_page_cannot_vote_for_its_own_removal_twice() -> None:
    # Two lines: the head and tail slices overlap, so both would be counted
    # twice per page without the per-page set.
    pages = [["only", "pair"], ["different", "lines"]]
    assert find_running_lines(pages) == set()


def test_cleaning_drops_the_furniture_and_keeps_the_text() -> None:
    lines = [
        "",
        "Ziekenhuis Sint-Jan",
        "Beste dokter,",
        "",
        "",
        "[ADRES]",
        "((( )))",
        "-----",
        "Tot ziens.",
        "",
    ]
    running = {"ziekenhuis sint-jan"}
    assert clean_lines(lines, running) == [
        "Beste dokter,",
        "",
        "-----",
        "Tot ziens.",
    ]


def test_a_rule_is_kept_unless_the_caller_strips_it() -> None:
    assert clean_lines(["a", "-----", "b"], set()) == ["a", "-----", "b"]
    assert clean_lines(["a", "-----", "b"], set(), strip_rules=True) == ["a", "b"]
    # A rule needs three characters to be one.
    assert clean_lines(["a", "--", "b"], set()) == ["a", "b"]


def test_a_stray_joiner_is_folded_back_onto_its_label() -> None:
    assert clean_lines(["Naam", ":", "Jan"], set()) == ["Naam :", "Jan"]
    assert clean_lines(["Naam", "-", "Jan"], set()) == ["Naam -", "Jan"]
    assert clean_lines(["Naam", "|", "Jan"], set()) == ["Naam |", "Jan"]
    # Nothing to join to, and no repeat of a joiner already there.
    assert clean_lines([":", "Jan"], set()) == ["Jan"]
    assert clean_lines(["Naam :", ":", "Jan"], set()) == ["Naam :", "Jan"]


def test_a_run_of_blank_lines_becomes_one_break() -> None:
    assert clean_lines(["a", "", "  ", "", "b"], set()) == ["a", "", "b"]
    assert clean_lines(["", "", "a", "", ""], set()) == ["a"]
    assert clean_lines(["", ""], set()) == []


def test_a_running_line_is_dropped_by_shape_not_by_text() -> None:
    assert clean_lines(["Page 7 of 9", "body"], {"page # of #"}) == ["body"]


# --------------------------------------------------------------------------- #
# Whole documents
# --------------------------------------------------------------------------- #


def pdf_bytes(*pages: list[str]) -> bytes:
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 100 + index * 20), line, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


def test_a_page_is_read_in_reading_order() -> None:
    with pymupdf.open(
        stream=pdf_bytes(["Beste dokter,", "Groeten."]), filetype="pdf"
    ) as doc:
        lines = extract_page_lines(doc[0])
    assert [line for line in lines if line] == ["Beste dokter,", "Groeten."]


def test_flow_order_is_a_plain_top_to_bottom_sort() -> None:
    with pymupdf.open(stream=pdf_bytes(["een", "twee"]), filetype="pdf") as doc:
        assert extract_page_lines(doc[0], reading_order="flow") == ["een", "twee"]


def test_an_empty_page_yields_no_lines() -> None:
    with pymupdf.open(stream=pdf_bytes([]), filetype="pdf") as doc:
        assert extract_page_lines(doc[0]) == []


def test_a_saved_document_carries_a_banner_per_page(tmp_path: Path) -> None:
    path = tmp_path / "letter.pdf"
    path.write_bytes(pdf_bytes(["een"], ["twee"]))
    text = extract_text_from_pdf(path)
    assert "Page 1 of 2" in text
    assert "Page 2 of 2" in text
    assert text.endswith("\n")
    assert "een" in text and "twee" in text


def test_cleaning_for_a_model_drops_the_banner_and_the_running_header(
    tmp_path: Path,
) -> None:
    path = tmp_path / "letter.pdf"
    path.write_bytes(
        pdf_bytes(
            ["Ziekenhuis Sint-Jan", "Beste dokter,"],
            ["Ziekenhuis Sint-Jan", "Groeten."],
        )
    )
    text = extract_text_from_pdf(path, clean_for_model=True)
    assert "Page 1 of 2" not in text
    assert "Ziekenhuis Sint-Jan" not in text
    assert "Beste dokter," in text and "Groeten." in text


def test_the_library_entry_point_refuses_what_it_cannot_read() -> None:
    for content in (b"", b"not a pdf at all"):
        try:
            extract_pdf_pages(content)
        except ValueError as error:
            assert str(error) == "invalid or unsupported PDF content"
        else:
            raise AssertionError(f"accepted {content!r}")


def test_the_library_entry_point_returns_one_page_per_page() -> None:
    pages = extract_pdf_pages(pdf_bytes(["een"], ["twee"]))
    assert [page.index for page in pages] == [0, 1]
    assert "een" in pages[0].text and "twee" in pages[1].text
    assert all(len(page.glyphs) == len(page.text) for page in pages)


def test_the_command_line_saves_the_extracted_text(tmp_path: Path, capsys) -> None:
    source = tmp_path / "letter.pdf"
    source.write_bytes(pdf_bytes(["Beste dokter,"]))
    target = tmp_path / "out.txt"
    argv = sys.argv
    sys.argv = ["pdf_extraction.py", str(source), str(target)]
    try:
        extraction.main()
    finally:
        sys.argv = argv
    assert "Beste dokter," in target.read_text(encoding="utf-8")
    assert str(target) in capsys.readouterr().out


def test_the_command_line_defaults_the_output_beside_the_input(tmp_path: Path) -> None:
    source = tmp_path / "letter.pdf"
    source.write_bytes(pdf_bytes(["een"]))
    argv = sys.argv
    sys.argv = ["pdf_extraction.py", str(source), "--clean-for-model", "--no-tables"]
    try:
        extraction.main()
    finally:
        sys.argv = argv
    assert (tmp_path / "letter.txt").read_text(encoding="utf-8").strip() == "een"


def test_the_command_line_refuses_a_missing_input(tmp_path: Path) -> None:
    argv = sys.argv
    sys.argv = ["pdf_extraction.py", str(tmp_path / "absent.pdf")]
    try:
        extraction.main()
    except FileNotFoundError as error:
        assert str(error) == f"PDF file not found: {tmp_path / 'absent.pdf'}"
    else:
        raise AssertionError("read a file that is not there")
    finally:
        sys.argv = argv


def test_the_command_line_defaults_match_the_library_defaults() -> None:
    argv = sys.argv
    sys.argv = ["pdf_extraction.py"]
    try:
        args = extraction.parse_args()
    finally:
        sys.argv = argv
    assert args.pdf == extraction.DEFAULT_INPUT
    assert args.output is None
    assert args.clean_for_model is False
    assert args.strip_rules is False
    assert args.no_tables is False
    assert args.reading_order == "xycut"


# --------------------------------------------------------------------------- #
# Reading fragments off a real page
# --------------------------------------------------------------------------- #


def drawn(build, skip=()) -> list[Fragment]:
    """Return the line fragments of a page built by *build*."""
    document = pymupdf.open()
    build(document.new_page())
    data = document.tobytes()
    document.close()
    with pymupdf.open(stream=data, filetype="pdf") as opened:
        return extraction._line_fragments(opened[0], skip)


def test_a_line_becomes_one_fragment_with_a_glyph_per_character() -> None:
    (fragment,) = drawn(lambda page: page.insert_text((72, 100), "Hello world"))
    assert fragment.text == "Hello world"
    assert len(fragment.glyphs) == len(fragment.text)
    assert fragment.turns == 0
    assert fragment.x0 == 72.0
    assert [glyph.text for glyph in fragment.glyphs] == list("Hello world")


def test_a_run_painted_twice_to_fake_bold_is_read_once() -> None:
    def build(page) -> None:
        page.insert_text((72, 100), "Bold")
        page.insert_text((72, 100), "Bold")

    assert [f.text for f in drawn(build)] == ["Bold"]


def test_padding_in_the_source_is_recorded_rather_than_kept() -> None:
    (fragment,) = drawn(lambda page: page.insert_text((72, 100), "  spaced   out  "))
    # The text is normalised, but whether it was padded is remembered, because
    # a redacted PDF butts boxes together and the coordinates cannot say.
    assert fragment.text == "spaced out"
    assert fragment.pad_left and fragment.pad_right
    assert len(fragment.glyphs) == len(fragment.text)


def test_an_unpadded_line_is_not_recorded_as_padded() -> None:
    (fragment,) = drawn(lambda page: page.insert_text((72, 100), "tight"))
    assert not fragment.pad_left and not fragment.pad_right


def test_a_line_of_nothing_but_spaces_is_not_a_fragment() -> None:
    assert drawn(lambda page: page.insert_text((72, 100), "   ")) == []


def test_rotated_text_is_measured_in_its_own_reading_frame() -> None:
    (fragment,) = drawn(
        lambda page: page.insert_text((300, 400), "Sideways", rotate=90)
    )
    assert fragment.text == "Sideways"
    assert fragment.turns == 3
    # x runs along the text whichever way the baseline points.
    assert fragment.x1 > fragment.x0


def test_a_fragment_inside_a_skipped_region_is_dropped() -> None:
    whole_page = frag(0, 0, 1000, 1000, "table", atomic=True)
    assert drawn(lambda page: page.insert_text((72, 100), "Hello"), [whole_page]) == []
    elsewhere = frag(0, 0, 10, 10, "table", atomic=True)
    assert (
        len(drawn(lambda page: page.insert_text((72, 100), "Hello"), [elsewhere])) == 1
    )


def ruled_table(page) -> None:
    """Draw a three-row, two-column table with real vector rules."""
    for row in range(4):
        page.draw_line((72, 100 + row * 20), (300, 100 + row * 20))
    for x in (72, 190, 300):
        page.draw_line((x, 100), (x, 160))
    for row in range(3):
        for column in range(2):
            page.insert_text((80 + column * 118, 115 + row * 20), f"c{row}{column}")


def table_page():
    document = pymupdf.open()
    ruled_table(document.new_page())
    data = document.tobytes()
    document.close()
    return pymupdf.open(stream=data, filetype="pdf")


def test_a_ruled_table_becomes_one_atomic_markdown_fragment() -> None:
    with table_page() as document:
        (fragment,) = extraction._table_fragments(document[0])
    assert fragment.atomic
    assert fragment.text.startswith("|c00|c01|")
    assert "|---|---|" in fragment.text


def test_a_detected_table_takes_its_cells_out_of_the_line_fragments() -> None:
    with table_page() as document:
        page = document[0]
        with_tables = extraction.collect_fragments(page)
        without = extraction.collect_fragments(page, detect_tables=False)
    assert [f.atomic for f in with_tables] == [True]
    assert [f.text for f in without] == ["c00", "c01", "c10", "c11", "c20", "c21"]
    assert all(not f.atomic for f in without)


def test_the_glyph_path_never_detects_tables() -> None:
    # Table Markdown has no source geometry, so a model span in it could not
    # be rendered back into the original cells.
    with table_page() as document:
        page = extraction.extract_page_with_glyphs(document[0], 0)
    assert "|" not in page.text
    assert "c00" in page.text
    assert len(page.glyphs) == len(page.text)


def test_a_page_with_no_text_is_named_on_stderr(tmp_path: Path, capsys) -> None:
    # A scanned page yields nothing, and the caller has to be told which one:
    # silently returning an empty page reads as a document with no content.
    path = tmp_path / "letter.pdf"
    path.write_bytes(pdf_bytes(["een"], [], ["drie"]))
    extract_text_from_pdf(path)
    warning = capsys.readouterr().err
    assert warning == (
        "Warning: no extractable text on page(s) 2 " "(scanned images need OCR).\n"
    )


def test_a_document_that_reads_everywhere_warns_about_nothing(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "letter.pdf"
    path.write_bytes(pdf_bytes(["een"], ["twee"]))
    extract_text_from_pdf(path)
    assert capsys.readouterr().err == ""


def test_stripping_the_rules_is_carried_through_to_the_cleaner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "letter.pdf"
    path.write_bytes(pdf_bytes(["Beste dokter,", "-----", "Groeten."]))
    kept = extract_text_from_pdf(path, clean_for_model=True)
    assert "-----" in kept
    stripped = extract_text_from_pdf(path, clean_for_model=True, strip_rules=True)
    assert "-----" not in stripped
    assert "Beste dokter," in stripped and "Groeten." in stripped


def test_an_encrypted_document_is_refused_by_name() -> None:
    document = pymupdf.open()
    document.new_page().insert_text((72, 100), "geheim")
    data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    document.close()
    try:
        extract_pdf_pages(data)
    except ValueError as error:
        assert str(error) == "invalid or unsupported PDF content"
    else:
        raise AssertionError("read an encrypted document")


def test_each_orientation_on_a_page_is_its_own_reading_flow() -> None:
    def build(page) -> None:
        page.insert_text((72, 100), "upright")
        page.insert_text((400, 400), "sideways", rotate=90)

    document = pymupdf.open()
    build(document.new_page())
    data = document.tobytes()
    document.close()
    with pymupdf.open(stream=data, filetype="pdf") as opened:
        lines = extract_page_lines(opened[0])
        page = extraction.extract_page_with_glyphs(opened[0], 0)
    # Upright text comes first, whatever order the operators appear in.
    assert [line for line in lines if line] == ["upright", "sideways"]
    assert page.text.index("upright") < page.text.index("sideways")
    assert len(page.glyphs) == len(page.text)


def test_a_page_that_draws_nothing_extracts_to_nothing() -> None:
    (page,) = extract_pdf_pages(pdf_bytes([]))
    assert page.text == "" and page.glyphs == ()
    assert page.index == 0


def test_an_ocr_region_marks_only_the_glyphs_inside_it() -> None:
    document = pymupdf.open()
    document.new_page().insert_text((72, 100), "scanned")
    data = document.tobytes()
    document.close()
    inside = ((0.0, 0.0, 600.0, 800.0),)
    (page,) = extract_pdf_pages(data, ocr_regions=(inside,))
    assert all(glyph.ocr for glyph in page.glyphs if glyph is not None)
    (untouched,) = extract_pdf_pages(data)
    assert not any(glyph.ocr for glyph in untouched.glyphs if glyph is not None)
