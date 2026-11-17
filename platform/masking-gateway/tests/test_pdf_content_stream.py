#!/usr/bin/env python3
"""Pin the content-stream reader that decides where a replacement is written.

`test_pdf_content_order.py` proves the outcome on whole documents. This file
pins the reader underneath it directly, because every refusal in it is a
silent one: a stream this scanner declines to understand leaves the page
exactly as it arrived, which looks identical to a page it handled well.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.services import pdf_content_order as content_order
from masking_engine.app.services.pdf_content_order import (
    _assemble_content,
    _concat,
    _drawn_at,
    _font_of,
    _rows,
    _Seam,
    _skip_literal_string,
    _tokens,
    _Unsupported,
    object_seams,
    scan,
)


def _required[T](value: T | None) -> T:
    """Narrow an Optional the test knows is present, failing loudly if it is not."""
    assert value is not None
    return value


def refused(data: bytes) -> str:
    try:
        scan(data)
    except _Unsupported as error:
        return str(error)
    raise AssertionError(f"scanned {data!r}")


# --------------------------------------------------------------------------- #
# Lexing
# --------------------------------------------------------------------------- #


def test_a_literal_string_ends_at_its_own_closing_bracket() -> None:
    assert _skip_literal_string(b"(abc)rest", 0) == 5
    # Brackets nest...
    assert _skip_literal_string(b"(a(b)c)rest", 0) == 7
    # ...and a backslash escapes whatever follows it, bracket included.
    assert _skip_literal_string(rb"(a\)b)rest", 0) == 6
    assert _skip_literal_string(rb"(a\\)rest", 0) == 5


def test_a_string_that_never_closes_is_refused() -> None:
    try:
        _skip_literal_string(b"(unterminated", 0)
    except _Unsupported as error:
        assert str(error) == "unterminated string"
    else:
        raise AssertionError("read past the end of the stream")


def test_every_token_carries_its_kind_and_its_offsets() -> None:
    tokens = [(t.kind, t.text, t.start, t.end) for t in _tokens(b"BT /F1 12 Tf (a) Tj")]
    assert tokens == [
        ("operator", b"BT", 0, 2),
        ("ws", b" ", 2, 3),
        ("name", b"/F1", 3, 6),
        ("ws", b" ", 6, 7),
        ("number", b"12", 7, 9),
        ("ws", b" ", 9, 10),
        ("operator", b"Tf", 10, 12),
        ("ws", b" ", 12, 13),
        ("string", b"(a)", 13, 16),
        ("ws", b" ", 16, 17),
        ("operator", b"Tj", 17, 19),
    ]


def test_a_byte_the_lexer_cannot_classify_is_refused() -> None:
    try:
        list(_tokens(b"BT \x00 ET"))
    except _Unsupported as error:
        assert str(error) == "unparsable byte b'\\x00'"
    else:
        raise AssertionError("classified an arbitrary byte")


def test_operators_written_inside_a_string_are_not_operators() -> None:
    # The whole point of scanning strings by hand.
    objects, _, _ = scan(b"BT /F1 12 Tf 1 2 Td (ET BT Q q) Tj ET")
    assert len(objects) == 1
    assert scan(b"BT /F1 12 Tf 1 2 Td (a(b)c) Tj ET")[0][0][:1] == (0,)
    assert scan(rb"BT /F1 12 Tf 1 2 Td (a\)b) Tj ET")[0][0][:1] == (0,)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


def test_matrices_concatenate_in_pdf_order() -> None:
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    scale = (2.0, 0.0, 0.0, 2.0, 10.0, 20.0)
    assert _concat(identity, scale) == scale
    assert _concat(scale, identity) == scale
    # A translation applied under a scale is scaled with it.
    assert _concat((1.0, 0.0, 0.0, 1.0, 5.0, 5.0), scale) == (
        2.0,
        0.0,
        0.0,
        2.0,
        20.0,
        30.0,
    )


def test_an_origin_is_carried_into_page_coordinates_with_its_size() -> None:
    assert _drawn_at((5.0, 5.0), (2.0, 0.0, 0.0, 2.0, 10.0, 20.0), 12.0, 1.0) == (
        20.0,
        30.0,
        24.0,
    )
    # The drawn size follows the matrix and the text scale, and is never signed.
    assert _drawn_at((0.0, 0.0), (1.0, 0.0, 0.0, -1.0, 0.0, 0.0), 10.0, 1.0)[2] == 10.0
    assert _drawn_at((0.0, 0.0), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 10.0, 1.5)[2] == 15.0


def test_lines_a_hair_apart_share_a_row_and_a_line_apart_do_not() -> None:
    # Half the size of the line that opens the row, so two columns of one
    # header stay together while consecutive body lines never merge.
    assert _rows([(700.0, 12.0), (699.0, 12.0), (680.0, 12.0)]) == [1, 1, 0]
    # Exactly half the size still counts as the same row; a hair more does not.
    assert _rows([(700.0, 12.0), (694.0, 12.0)]) == [0, 0]
    assert _rows([(700.0, 12.0), (693.9, 12.0)]) == [1, 0]
    # An object that named no font is measured at the assumed size.
    assert _rows([(700.0, 0.0), (698.0, 0.0)]) == [0, 0]
    assert _rows([]) == []


def test_the_font_of_a_show_operator_is_the_name_before_it() -> None:
    assert _font_of(b"BT /F7 11 Tf", 12) == "/F7"
    assert _font_of(b"BT /TT2 8.5 Tf", 14) == "/TT2"
    # No name at all is not a font.
    assert _font_of(b"BT 11 Tf", 8) is None


# --------------------------------------------------------------------------- #
# Scanning a page
# --------------------------------------------------------------------------- #


def test_a_text_object_reports_where_it_draws() -> None:
    objects, neutral, _ = scan(b"BT /F1 12 Tf 100 700 Td (Hi) Tj ET")
    assert objects == [(0, 100.0, 700.0, 12.0, 34, True)]
    # Nothing precedes the object, so there is no seam before it.
    assert neutral == []


def test_the_seam_between_two_objects_is_the_only_one_offered() -> None:
    stream = b"BT /F1 12 Tf 100 700 Td (a) Tj ET BT /F1 12 Tf 100 680 Td (b) Tj ET"
    objects, neutral, _ = scan(stream)
    assert [(start, x, y) for start, x, y, _, _, _ in objects] == [
        (0, 100.0, 700.0),
        (34, 100.0, 680.0),
    ]
    assert neutral == [34]
    assert stream[34:36] == b"BT"


def test_a_seam_is_never_offered_between_an_operator_and_its_operands() -> None:
    # `1.5 0 0 1 -87 0 cm` sits at the identity matrix until its own operator
    # runs; splicing inside it would cut the operand list in half.
    stream = b"1 0 0 1 0 0 cm BT /F1 12 Tf 1 2 Td (a) Tj ET"
    _, neutral, _ = scan(stream)
    # Only the boundary after `cm` has run, never one between its operands.
    assert neutral == [15]
    assert stream[:15] == b"1 0 0 1 0 0 cm "


def test_a_comment_is_a_boundary_like_any_other_whitespace() -> None:
    objects, neutral, _ = scan(b"% hello\nBT /F1 12 Tf 1 2 Td (a) Tj ET")
    assert objects[0][0] == 8
    assert neutral == [7, 8]


def test_a_matrix_is_restored_by_the_state_stack() -> None:
    stream = (
        b"q 2 0 0 2 0 0 cm q 1 0 0 1 5 5 cm "
        b"BT /F1 1 Tf 0 0 Td (a) Tj ET Q "
        b"BT /F1 1 Tf 0 0 Td (b) Tj ET Q"
    )
    objects, _, _ = scan(stream)
    # Inner object: (5,5) under a doubling. Outer: back to (0,0), still doubled.
    assert [(x, y, plain) for _, x, y, _, _, plain in objects] == [
        (10.0, 10.0, False),
        (0.0, 0.0, False),
    ]


def test_a_clip_closes_the_seams_after_it() -> None:
    _, neutral, _ = scan(b"q 0 0 10 10 re W n BT /F1 12 Tf 1 2 Td (a) Tj ET Q")
    # The boundary after `q` and the one before `W`; nothing once clipped.
    assert neutral == [2, 15]
    assert scan(b"q 0 0 10 10 re W* n BT /F1 12 Tf 1 2 Td (a) Tj ET Q")[1] == [2, 15]


def test_tm_carries_its_own_vertical_scale() -> None:
    objects, _, _ = scan(b"BT /F1 12 Tf 2 0 0 3 40 50 Tm (a) Tj ET")
    assert objects[0][1:4] == (40.0, 50.0, 36.0)


def test_every_shape_the_scanner_will_not_reason_about_is_named() -> None:
    assert refused(b"Q BT /F1 12 Tf 1 2 Td (a) Tj ET") == "unbalanced Q"
    assert refused(b"BT BT /F1 12 Tf 1 2 Td (a) Tj ET ET") == "nested BT"
    assert refused(b"ET") == "ET without BT"
    assert (
        refused(b"BT /F1 12 Tf (a) Tj ET") == "text object with no positioning operator"
    )
    assert refused(b"BT /F1 12 Tf 1 2 Td (a) Tj") == "stream ends mid-object"
    assert refused(b"q") == "stream ends mid-object"
    assert refused(b"BI /W 1 ID xx EI") == "inline image"
    assert refused(b"1 2 3 cm") == "malformed cm"
    assert refused(b"BT /F1 12 Tf 5 Td (a) Tj ET") == "malformed Td"
    assert refused(b"BT /F1 12 Tf 5 TD (a) Tj ET") == "malformed TD"
    assert refused(b"BT /F1 12 Tf 1 2 Tm (a) Tj ET") == "malformed Tm"
    # Enough operands, but one of them is not a number. Reading a name as a
    # coordinate would put a whole row somewhere it was never drawn.
    assert refused(b"BT /F1 12 Tf 1 0 0 1 /X 700 Tm (a) Tj ET") == "malformed Tm"
    assert refused(b"BT /F1 12 Tf (x) 700 Td (a) Tj ET") == "malformed Td"
    assert refused(b"BT /F1 12 Tf TL T* (a) Tj ET") == "malformed TL"


def test_an_operator_the_scanner_does_not_know_changes_nothing() -> None:
    # Tolerated rather than refused: it only clears the operands it collected.
    objects, _, _ = scan(b"BT /F1 12 Tf 2 Tc 1 2 Td (a) Tj 0 Tw ET")
    assert objects[0][1:4] == (1.0, 2.0, 12.0)


def test_an_empty_stream_has_no_objects_and_no_seams() -> None:
    assert scan(b"") == ([], [], {})


# --------------------------------------------------------------------------- #
# Reading one object for its seams
# --------------------------------------------------------------------------- #


def test_an_object_redaction_never_opened_has_no_gaps() -> None:
    chunk = b"BT /F1 12 Tf 100 700 Td (Hello world) Tj ET"
    assert object_seams(chunk) == ("/F1", 12.0, 700.0, [], 41)
    assert chunk[41:] == b"ET"


def test_a_kern_only_array_marks_where_a_value_was_taken_out() -> None:
    chunk = b"BT /F1 12 Tf 100 700 Td (Hello ) Tj [ -500 ] TJ ( world) Tj ET"
    font, size, y, seams, trailing = _required(object_seams(chunk))
    assert (font, size, y) == ("/F1", 12.0, 700.0)
    # The seam is the start of the operands *after* the kern, so the kern
    # stays with the text before it, and nothing has to be rewritten there.
    assert seams == [_Seam(48, 48, b"", b"", None)]
    assert chunk[48:56] == b"( world)"
    assert chunk[trailing:] == b"ET"


def test_a_kern_inside_one_array_is_offered_with_the_width_it_displaced() -> None:
    # The commonest shape of all: the producer drew the line as one show, so
    # redaction had no operator boundary to leave the kern at and it landed
    # inside the array. Cutting there needs the array closed and reopened.
    chunk = b"BT /F1 12 Tf 100 700 Td [(Hello )-1000( world)] TJ ET"
    _font, _size, _y, seams, _trailing = _required(object_seams(chunk))
    assert len(seams) == 1
    seam = seams[0]
    assert chunk[seam.start : seam.end] == b"-1000"
    assert (seam.close, seam.reopen) == (b"]TJ", b"[")
    assert seam.width == pytest.approx(12.0)  # 1000/1000 of a 12pt em


def test_a_kern_that_pulls_a_line_together_is_not_a_removal() -> None:
    # Only a positive displacement opened a hole; a positive kern closes one.
    assert _required(object_seams(b"BT /F1 12 Tf 1 2 Td [(a)50(b)] TJ ET"))[3] == []


def test_an_array_holding_anything_but_strings_and_numbers_is_not_read() -> None:
    assert _required(object_seams(b"BT /F1 12 Tf 1 2 Td [(a)/N(b)] TJ ET"))[3] == []


def test_two_show_operators_with_no_kern_between_them_are_not_a_gap() -> None:
    # A producer may split a line for its own reasons; only the kern-only
    # array is evidence that redaction removed something.
    assert _required(object_seams(b"BT /F1 12 Tf 1 2 Td (a) Tj (b) Tj ET"))[3] == []


def test_an_object_that_would_have_to_carry_state_across_a_reopen_is_left_whole() -> (
    None
):
    for chunk in (
        b"BT /F1 12 Tf 1 2 Td 2 Tc (a) Tj ET",  # character spacing
        b"BT /F1 12 Tf 1 2 Td 50 Tz (a) Tj ET",  # horizontal scaling
        b"BT /F1 12 Tf 1 2 Td 3 Ts (a) Tj ET",  # rise
        b"BT /F1 12 Tf 1 2 Td 1 0 0 1 5 5 Tm (a) Tj ET",  # a matrix of its own
    ):
        assert object_seams(chunk) is None, chunk


def test_an_object_missing_what_a_reopen_needs_is_left_whole() -> None:
    assert object_seams(b"BT 100 700 Td (a) Tj ET") is None  # no font
    assert object_seams(b"BT /F1 12 Tf (a) Tj ET") is None  # no line position
    assert object_seams(b"BT /F1 12 Tf 100 700 Td (a) Tj") is None  # no close


def test_an_unreadable_object_is_left_whole_rather_than_refusing_the_page() -> None:
    assert object_seams(b"BT /F1 12 Tf 1 2 Td (unterminated Tj ET") is None
    assert object_seams(b"BT \x00 ET") is None


def test_a_font_operator_needs_a_size_to_be_read() -> None:
    assert object_seams(b"BT /F1 Tf 100 700 Td (a) Tj ET") is None


# --------------------------------------------------------------------------- #
# Putting the page back together
# --------------------------------------------------------------------------- #


def test_a_replacement_is_written_where_its_offset_says() -> None:
    assert _assemble_content(b"AAAABBBB", [(4, 0, b"X")], {}) == b"AAAA\nX\nBBBB"
    assert _assemble_content(b"AAAABBBB", [], {}) == b"AAAABBBB"


def test_several_replacements_at_one_offset_keep_their_order() -> None:
    assembled = _assemble_content(b"AB", [(1, 1, b"Y"), (1, 0, b"X")], {})
    assert assembled == b"A\nX\n\nY\nB"


def test_an_insertion_before_a_rewritten_object_goes_before_it() -> None:
    # Order -1 marks the object rewritten in place; an insertion at the same
    # offset belongs to the line above, so it is emitted first and the
    # rewritten object follows.
    assert _assemble_content(b"AAAABBBB", [(4, -1, b"X"), (4, 0, b"Y")], {4: 8}) == (
        b"AAAA\nY\nX"
    )


def test_no_replacement_is_dropped_when_its_offset_is_already_past() -> None:
    # An insertion landing inside an object that was rewritten is emitted
    # straight after it rather than silently discarded.
    assembled = _assemble_content(b"AAAABBBB", [(0, -1, b"R"), (2, 0, b"X")], {0: 6})
    assert b"R" in assembled and b"X" in assembled


def test_the_module_names_the_operators_it_understands() -> None:
    # The tables are the specification; a name dropping out of one of them is
    # a behaviour change, not a refactor.
    assert set(content_order._KINDS) == {"ws", "comment", "number", "operator"}
    assert set(content_order._OPERATORS) == {
        "BI",
        "q",
        "Q",
        "cm",
        "W",
        "W*",
        "BT",
        "ET",
        "Tf",
        "TL",
        "Td",
        "TD",
        "Tm",
        "T*",
        "Tj",
        "TJ",
        "'",
        '"',
    }
    assert set(content_order._TEXT_BOUNDARIES) == {"BT", "ET"}
    assert set(content_order._INSIDE_OPERATORS) == {
        "Tf",
        "Td",
        "TD",
        "Tj",
        "TJ",
        "'",
        '"',
    }
    assert content_order._SHOWING <= content_order._ALLOWED_INSIDE | {"'", '"'}


# --------------------------------------------------------------------------- #
# Choosing where each replacement goes
# --------------------------------------------------------------------------- #

import pymupdf  # noqa: E402

from masking_engine.app.services.pdf_content_order import (  # noqa: E402
    _in_page_space,
    _outer_splice,
    _placements,
    _rewrite_object_in_place,
    restore,
)


def test_a_replacement_snaps_back_to_the_last_seam_before_its_target() -> None:
    assert _outer_splice([0, 10, 20], {}, 15, 3, b"X", 99) == (10, 3, b"X")
    assert _outer_splice([0, 10, 20], {}, 25, 3, b"X", 99) == (20, 3, b"X")
    # A seam exactly at the target is the one taken.
    assert _outer_splice([10], {}, 10, 3, b"X", 99) == (10, 3, b"X")


def test_with_no_seam_before_it_a_replacement_stays_at_the_end() -> None:
    # Offset zero would bury it under whatever the page paints first, a
    # full-page scan included.
    assert _outer_splice([10, 20], {}, 5, 3, b"X", 99) == (99, 3, b"X")
    assert _outer_splice([], {}, 5, 3, b"X", 99) == (99, 3, b"X")


def test_a_seam_inside_a_text_object_wins_when_it_is_the_nearer_one() -> None:
    """And the blob closes the object, then reopens it where `scan` said."""
    seams = {10: b"BT 1 0 0 1 40 700 Tm", 20: b"BT 1 0 0 1 40 680 Tm"}
    assert _outer_splice([0], seams, 15, 3, b" X ", 99) == (
        10,
        3,
        b"ET X BT 1 0 0 1 40 700 Tm",
    )
    # Only when it is nearer: an outside seam after it is still preferred,
    # because a blob that needs no reopening is the safer of the two.
    assert _outer_splice([0, 12], seams, 15, 3, b"X", 99) == (12, 3, b"X")
    # And an interior seam past the target is not a candidate at all.
    assert _outer_splice([0], {20: b"BT 1 0 0 1 40 680 Tm"}, 15, 3, b"X", 99) == (
        0,
        3,
        b"X",
    )


def test_scanned_objects_are_carried_into_the_caller_s_coordinates() -> None:
    # PDF space has y growing the other way, so the objects are mapped rather
    # than compared directly, and the pair comes back as (down, across).
    to_page = pymupdf.Matrix(1, 0, 0, -1, 0, 842)
    assert _in_page_space([(5, 100.0, 700.0, 12.0, 40, True)], to_page) == [
        (5, 142.0, 100.0, 12.0, 40, True)
    ]


def test_a_replacement_targets_the_seam_before_the_next_object() -> None:
    first = (0, 700.0, 100.0, 12.0, 40, True)
    second = (41, 680.0, 100.0, 12.0, 80, True)
    placed = [first, second]
    # A replacement on the first row goes before the second object, and is
    # owned by the object it came out of.
    unit = [(700.0, 150.0, 12.0, 180.0, b"a")]
    assert _placements(placed, [0, 1], unit, [0], 200) == ([41], {0: [0]})
    # One on the second row has no object after it, so it goes to the end.
    unit = [(680.0, 150.0, 12.0, 180.0, b"a")]
    assert _placements(placed, [0, 1], unit, [1], 200) == ([200], {1: [0]})
    # One on a row of its own is owned by nothing.
    unit = [(600.0, 150.0, 12.0, 180.0, b"a")]
    assert _placements(placed, [0, 1], unit, [2], 200) == ([200], {})


def test_an_object_drawn_under_a_matrix_owns_nothing() -> None:
    # Reopening needs the identity matrix, so a `plain` of False keeps the
    # replacement outside the object.
    under_matrix = [(0, 700.0, 100.0, 12.0, 40, False)]
    unit = [(700.0, 150.0, 12.0, 180.0, b"a")]
    assert _placements(under_matrix, [0], unit, [0], 200) == ([200], {})


CHUNK = b"BT /F1 12 Tf 100 700 Td (Hello ) Tj [ -500 ] TJ ( world) Tj ET"
UNIT = (700.0, 150.0, 12.0, 180.0, b"q BT /F1 12 Tf 150 700 Td (X) Tj ET Q")


def test_a_replacement_is_spliced_into_the_gap_redaction_left() -> None:
    rewritten, inside = _required(_rewrite_object_in_place(CHUNK, 100.0, [0], [UNIT]))
    assert inside == [0]
    # The object is closed, the blob drawn, and the object reopened at the x
    # where the text resumes.
    assert (
        b" ET q BT /F1 12 Tf 150 700 Td (X) Tj ET Q BT/F1 12 Tf 180.00000 700 TD "
        in (rewritten)
    )
    assert rewritten.endswith(b"( world) Tj ET")
    assert rewritten.count(b"(X) Tj") == 1


def test_a_value_that_ended_the_line_takes_the_close_of_the_object() -> None:
    # Redaction left no operator after it, so the extra slot is the `ET`.
    plain = b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET"
    rewritten, inside = _required(_rewrite_object_in_place(plain, 100.0, [0], [UNIT]))
    assert inside == [0]
    assert rewritten.endswith(b"TD ET")
    assert b"(Hello) Tj" in rewritten


def test_a_replacement_at_the_object_s_own_origin_is_not_spliced_inside() -> None:
    # It started the line, so it goes in front of the whole object instead --
    # which needs no surgery, and leaves this object whole.
    at_origin = (700.0, 100.0, 12.0, 120.0, b"blob")
    assert _rewrite_object_in_place(CHUNK, 100.0, [0], [at_origin]) is None


def test_fewer_replacements_than_gaps_leaves_the_object_whole() -> None:
    # Which gap the one replacement belongs in is not knowable from a count,
    # and guessing would move the wrong half of the line.
    two_gaps = (
        b"BT /F1 12 Tf 100 700 Td (a) Tj [ -500 ] TJ (b) Tj [ -500 ] TJ (c) Tj ET"
    )
    assert _rewrite_object_in_place(two_gaps, 100.0, [0], [UNIT]) is None
    assert _rewrite_object_in_place(CHUNK, 100.0, [], [UNIT]) is None


def test_more_replacements_than_gaps_all_take_the_close_of_the_object() -> None:
    # Reflow redraws the glyphs a replacement pushed right as a replacement of
    # its own, so a line's last value arrives with a second blob behind it and
    # nothing in the object to seam either against.
    second = (700.0, 200.0, 12.0, 240.0, b"q BT /F1 12 Tf 200 700 Td (Y) Tj ET Q")
    third = (700.0, 260.0, 12.0, 300.0, b"q BT /F1 12 Tf 260 700 Td (Z) Tj ET Q")
    units = [UNIT, second, third]
    # One gap, three replacements: the two with nothing after them share the
    # close of the object rather than sending all three to the end of the page.
    rewritten, inside = _required(
        _rewrite_object_in_place(CHUNK, 100.0, [0, 1, 2], units)
    )
    assert inside == [0, 1, 2]
    for drawn in (b"(X) Tj", b"(Y) Tj", b"(Z) Tj"):
        assert rewritten.count(drawn) == 1
    # Drawn in x order, and the object's own text is still all there.
    assert rewritten.index(b"(X) Tj") < rewritten.index(b"(Y) Tj")
    assert rewritten.index(b"(Y) Tj") < rewritten.index(b"(Z) Tj")
    assert b"(Hello ) Tj" in rewritten and b"( world) Tj" in rewritten


def test_an_object_with_no_seams_at_all_is_left_whole() -> None:
    unreadable = b"BT /F1 12 Tf 100 700 Td 2 Tc (a) Tj ET"
    assert _rewrite_object_in_place(unreadable, 100.0, [0], [UNIT]) is None


# --------------------------------------------------------------------------- #
# The page-level entry point
# --------------------------------------------------------------------------- #


def one_page_document(stream: bytes):
    document = pymupdf.open()
    page = document.new_page()
    xref = document.get_new_xref()
    document.update_object(xref, "<<>>")
    document.update_stream(xref, stream)
    page.set_contents(xref)
    return document, page, [xref]


def test_a_page_with_nothing_to_place_is_left_as_it_is() -> None:
    document, page, xrefs = one_page_document(CHUNK)
    try:
        assert restore(document, page, xrefs, []) is False
    finally:
        document.close()


def test_a_rotated_page_is_left_as_it_is() -> None:
    document, page, xrefs = one_page_document(CHUNK)
    try:
        page.set_rotation(90)
        assert restore(document, page, xrefs, [UNIT]) is False
    finally:
        document.close()


def test_a_stream_the_scanner_refuses_leaves_the_page_alone(caplog) -> None:
    import logging

    caplog.set_level(
        logging.INFO, logger="masking_engine.app.services.pdf_content_order"
    )
    document, page, xrefs = one_page_document(b"BI /W 1 ID xx EI")
    try:
        assert restore(document, page, xrefs, [UNIT]) is False
    finally:
        document.close()
    assert "content_order_skipped page=0 reason=inline image" in [
        record.getMessage() for record in caplog.records
    ]


def test_a_page_with_no_neutral_seam_is_left_alone() -> None:
    # A single text object with nothing before it offers nowhere to splice.
    document, page, xrefs = one_page_document(CHUNK)
    try:
        assert restore(document, page, xrefs, [UNIT]) is False
    finally:
        document.close()


# --------------------------------------------------------------------------- #
# A page drawn as one text object
# --------------------------------------------------------------------------- #


def test_a_page_drawn_as_one_text_object_reports_a_run_per_row() -> None:
    """The shape mupdf leaves behind, and the one that lost the en-GB header.

    Read as a single object the page sits at its first line, so nothing below
    that line has any base text to be ordered against and every replacement
    on it goes to the foot of the page.
    """
    stream = b"BT /F1 9 Tf 30 700 Td (first) Tj 0 -12 TD (second) Tj T* (third) Tj ET"
    objects, neutral, interior = scan(stream)
    assert [(x, y) for _, x, y, _, _, _ in objects] == [
        (30.0, 700.0),
        (30.0, 688.0),
        (30.0, 676.0),
    ]
    # `T*` moves by the leading the preceding `TD` set, not by its own operands.
    assert neutral == []
    # None of the three is the object entire, so none may be reopened in place.
    assert [whole for *_, whole in objects] == [False, False, False]
    # Each later run may be cut in front of, and the cut reopens the object at
    # the line matrix as it stood there -- the row above, whose relative move
    # then lands where it always did.
    assert list(interior) == [start for start, *_ in objects[1:]]
    assert interior[objects[1][0]] == b"BT 1 0 0 1 30 700 Tm"
    assert interior[objects[2][0]] == b"BT 1 0 0 1 30 688 Tm"


def test_a_move_along_the_line_is_a_column_not_a_row() -> None:
    """Cutting there would cost the row its in-place splice.

    A replacement goes into the gap redaction left inside a line by reopening
    the whole text object at the x where the text resumes. A run out of the
    middle of an object is not a text object, so a line drawn in pieces has to
    stay whole.
    """
    stream = b"BT /F1 9 Tf 30 700 Td (a) Tj 20 0 Td (b) Tj ET"
    objects, _, interior = scan(stream)
    assert [(x, y, whole) for _, x, y, _, _, whole in objects] == [(30.0, 700.0, True)]
    assert interior == {}


def test_a_quote_operator_moves_to_the_next_line_before_it_shows() -> None:
    """`'` is `T*` and a show in one operator; reading it as only a show would
    report both rows at the same baseline."""
    stream = b"BT /F1 9 Tf 14 TL 30 700 Td (first) Tj (second) ' ET"
    objects, _, _ = scan(stream)
    assert [(x, y) for _, x, y, _, _, _ in objects] == [(30.0, 700.0), (30.0, 686.0)]


def test_a_run_that_shows_nothing_is_not_a_row() -> None:
    """Reporting it would invent a row for a replacement to be placed in."""
    stream = b"BT /F1 9 Tf 30 700 Td (first) Tj 0 -12 TD 0 -12 TD (third) Tj ET"
    objects, _, _ = scan(stream)
    assert [(x, y) for _, x, y, _, _, _ in objects] == [(30.0, 700.0), (30.0, 676.0)]


def test_the_leading_a_later_operator_sets_is_the_one_t_star_uses() -> None:
    """`TD` sets the leading as well as moving, and `T*` reads it back."""
    stream = b"BT /F1 9 Tf 30 700 Td 25 TL T* (a) Tj 0 -5 TD T* (b) Tj ET"
    objects, _, _ = scan(stream)
    # 700, moved to 675 by `TL`; then to 670 by the `TD`, and to 665 by the
    # `T*` after it, because that `TD` reset the leading to 5. Only the two
    # rows that showed anything are rows.
    assert [y for _, _, y, _, _, _ in objects] == [675.0, 665.0]
