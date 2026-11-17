"""Put each replacement back where it belongs in the page's content stream.

A PDF has no notion of reading order. Text extracts in the order the drawing
operators appear, so where a replacement is *written* decides where a reader
copying the masked document sees it.

That order was wrong, and measurably so. `apply_redactions` removes the
original glyphs from the page's existing content stream, and every way of
drawing the replacement afterwards **appends a new stream**:

    redact, then insert_text          -> pseudonym last
    add_redact_annot(text=...)        -> pseudonym last
    either of those, then clean_contents -> merged into one stream, still last

All three were measured on the same page; `clean_contents` concatenates in
array order and reorders nothing. The result is a masked document whose body
has a hole where each value used to be and whose pseudonyms are piled at the
end of the page -- correct on screen, wrong the moment anyone copies the text
out or feeds it to another tool. It affects every masked PDF, scanned or not.

This module splices each replacement's drawing into the position it belongs.

**The base stream is never rewritten.** It is scanned read-only for two
things: where each text object starts, and where that text object draws on
the page. The bytes between splice points are copied verbatim, so a construct
this scanner merely tolerates cannot be corrupted by it.

**A box and its text move together.** The caller hands over one blob per
replacement holding every operator drawn for it. Splitting them could paint a
highlight over its own pseudonym, so they are never separated.

**A splice point has to be neutral.** Only an offset where no clip path is
active and the accumulated matrix is the identity can accept a self-contained
`q ... Q` blob unchanged. That is why the scan tracks the graphics state at
all, and it is why the OCR path works: `inject_text_layer` writes each line
under its own `cm`, so the neutral point is the one before the enclosing `q`
rather than the text object itself.

**A page may be one text object.** Whole pages are drawn inside a single
`BT ... ET`, one positioning operator per line. It is what mupdf's own writer
leaves behind, so it is the shape of every page this service has just
redacted. Scanned as one object the page sits at its first line and there is
no base text below that line for a replacement to be ordered against, so
every one of them went to the foot of the page; the en-GB letter's header
name, NHS number and date extracted after the signature.

Each positioning operator therefore opens a *run* of its own, and the offset
in front of it is a seam a blob may take once it is wrapped in `ET ... BT`.
Reopening costs nothing so long as the line matrix comes back, which is why
the scanner carries that matrix rather than only the first origin: `Td`, `TD`
and `T*` move relative to it and would land somewhere else after a close. The
reopen writes it out as an absolute `Tm`. Everything else a text object holds
-- font, leading, spacing, rise, render mode -- is graphics state and outlives
`ET` on its own; so does the text matrix, which the operator right after the
seam sets anyway.

Only a move that changes the baseline opens a run. A move along the line is
another column of the same row, and cutting there would cost that row the one
thing a run cannot do: a replacement is spliced into the gap redaction left
*inside* a line by reopening the whole text object at the x where the text
resumes, and a slice of an object is not one.

**A line can be opened up, and the removal leaves a mark saying where.**
Removing a value from a line does not always merge that line into one string.
A producer that drew it as several show operators keeps them, and what
redaction leaves behind between the surviving halves is a *kern-only* `TJ`: an
array holding a number and no string, whose displacement holds the rest of the
line in place. That number is the removal, recorded by mupdf at exactly the
offset the value stood at, so gaps are anchored on it rather than on every
boundary between show operators -- a line of five words redacted once has one
slot, not four.

A value at the *end* of a line leaves no following half to kern against, so
the close of the text object is offered as one further slot. Which slots a
line's replacements take is decided by counting: a replacement whose x sits at
the object's own origin started the line and is placed outside it, and the
rest match the gaps one for one, or the gaps plus that trailing slot. Any
other count is a shape this module does not understand and the line is left
alone.

The object is closed at the chosen slot, the replacement drawn, and the object
reopened at the x where the text resumes. That x comes from the glyph geometry
the pipeline already has, so no font metrics are needed and an embedded subset
font with an unknown encoding is no obstacle; measured on a real French
letter, not one word on the page moves.

Where redaction *did* merge the line -- one `TJ` array whose kerning number
sits inside the surviving string, which is what mupdf's own writer produces
for a line it rebuilt -- the cut falls inside that array, and only there is
anything rewritten: the array is closed before the splice and opened again
after it, and the kern is dropped because the reopen positions the rest of
the line absolutely. This is the commonest shape of all, and for a long time
it had no slot at all: `Patient AB seen today` masked to a page reading
`Patient seen today Bianca`, correct on screen and wrong to everything that
copies the text out. A kern is only cut when what it displaced is the width
of a value this line actually lost -- an array is also where a producer puts
ordinary letter spacing, and the pipeline knows what it took out.

One shape is still beyond it. A paragraph mupdf drew as a single text object
of several runs cannot be cut inside its array: closing it there would move
the line matrix that every run *below* derives its own position from, and the
rest of the paragraph would shift. Those replacements take the end of their
run, which is still their own line rather than the foot of the page.

Reopening is refused unless the object draws at the identity matrix and uses
nothing but font, position, and colour operators, because anything else would
have to be carried across the reopen.

**Anything unfamiliar leaves the page alone.** An inline image, unbalanced
`q`/`Q`, a nested `BT`, a rotated page, a token the scanner does not know --
each returns the page exactly as it arrived. Reading order is worth having
and is not worth one corrupted document, so every failure here is a silent
no-op rather than a repair attempt.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from typing import NamedTuple

import pymupdf

log = logging.getLogger(__name__)

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# How far a baseline may sit from the one that opened its row, as a fraction
# of that line's drawn size, and the size assumed when a text object names no
# font of its own.
_ROW_TOLERANCE = 0.5
_DEFAULT_SIZE = 4.0

# How close to an object's own origin a replacement has to be to count as
# starting the line rather than sitting inside it.
_EDGE_TOLERANCE = 0.5

# How far a text object's baseline has to move before the move is read as a
# new row rather than another column of the one it is on.
_BASELINE_EPSILON = 1e-6

_TOKEN = re.compile(
    rb"""
      (?P<ws>\s+)
    | (?P<comment>%[^\r\n]*)
    | (?P<dict_open><<)
    | (?P<dict_close>>>)
    | (?P<hex><[^>]*>)
    | (?P<array_open>\[)
    | (?P<array_close>\])
    | (?P<name>/[^\s/\[\]<>(){}%]*)
    | (?P<number>[+-]?(?:\d+\.\d*|\.\d+|\d+))
    | (?P<operator>[A-Za-z'"][A-Za-z0-9'"*]*|\*)
    """,
    re.VERBOSE,
)


class _Unsupported(Exception):
    """The scanner met something it will not reason about."""


def _skip_literal_string(data: bytes, start: int) -> int:
    """Return the offset just past the `(...)` string beginning at *start*."""
    depth, index = 1, start + 1
    while index < len(data) and depth:
        byte = data[index]
        if byte == 0x5C:  # backslash escapes the next byte, whatever it is
            index += 2
            continue
        if byte == 0x28:
            depth += 1
        elif byte == 0x29:
            depth -= 1
        index += 1
    if depth:
        raise _Unsupported("unterminated string")
    return index


class _Token(NamedTuple):
    """One lexical unit of a content stream, with where it sits in the bytes."""

    kind: str
    text: bytes
    start: int
    end: int


def _tokens(data: bytes) -> Iterator[_Token]:
    """Walk a content stream, yielding every token in order.

    Whitespace and comments are yielded rather than skipped: `scan` records
    its splice offsets at exactly those boundaries, so to this reader they
    are content.

    A literal `(...)` string is scanned by hand and yielded as kind
    `"string"`. It cannot go through `_TOKEN`: its bytes are arbitrary and
    any of them may look like an operator.

    Raises `_Unsupported` on a byte it cannot classify, and on a string that
    never closes. Both callers had this loop written out; keeping one copy is
    what stops the two from drifting apart on a stream neither expected.
    """
    position, size = 0, len(data)
    while position < size:
        if data[position] == 0x28:
            end = _skip_literal_string(data, position)
            yield _Token("string", data[position:end], position, end)
            position = end
            continue
        match = _TOKEN.match(data, position)
        if match is None:
            raise _Unsupported(f"unparsable byte {data[position:position + 1]!r}")
        yield _Token(match.lastgroup or "", match.group(), match.start(), match.end())
        position = match.end()


def _concat(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = left
    A, B, C, D, E, F = right
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _matrix_operands(matrix: tuple[float, ...]) -> bytes:
    """Write a matrix back out as the six operands of a `Tm`.

    Fixed point rather than `repr`: the reopened matrix is the base the
    original relative moves are then applied to, so the same page has to
    produce the same bytes on every run for masking to stay reproducible.
    """
    return b" ".join(
        (f"{value:.6f}".rstrip("0").rstrip(".") or "0").encode("latin-1")
        for value in matrix
    )


def _drawn_at(
    origin: tuple[float, float],
    matrix: tuple[float, ...],
    font_size: float,
    text_scale: float,
) -> tuple[float, float, float]:
    """Carry a text object's origin and size out into page coordinates.

    The origin is written in text space; the accumulated matrix is what puts
    it on the page. The drawn size follows the same scale, which is why two
    columns of one row can be told from two consecutive rows at all.
    """
    x, y = origin
    a, b, c, d, e, f = matrix
    return x * a + y * c + e, x * b + y * d + f, abs(font_size * text_scale * d)


class _Scanner:
    """The little stack machine a content stream drives.

    One method per operator that changes the graphics state, and a table
    naming them. An operator not in that table changes nothing here and only
    clears the operands it collected, which is what makes the scanner
    tolerant of constructs it merely passes over.
    """

    def __init__(self) -> None:
        self.matrix: tuple[float, ...] = _IDENTITY
        self.stack: list[tuple[float, ...]] = []
        self.clipped = False
        self.clip_stack: list[bool] = []
        self.operands: list[float | None] = []
        self.in_text = False
        self.text_start = 0
        self.font_size = 0.0
        self.leading = 0.0
        self.line: tuple[float, ...] | None = None
        self.run: tuple[int, tuple[float, ...], bytes | None] | None = None
        self.runs: list[tuple[int, tuple[float, ...], float, bool, bytes | None]] = []
        self.drew = False
        self.seam: int | None = None
        self.objects: list[tuple[int, float, float, float, int, bool]] = []
        self.neutral: list[int] = []
        self.interior: dict[int, bytes] = {}

    # -- one method per token kind ------------------------------------------

    def _opaque(self, token: _Token) -> None:
        """A string, a name, a delimiter: an operand whose value is not a number."""
        self.operands.append(None)

    def _number(self, token: _Token) -> None:
        self.operands.append(float(token.text))

    def _boundary(self, token: _Token) -> None:
        # A boundary is only usable when an operator has just finished.
        # Operands are collected *before* their operator, so whitespace
        # inside `1.5 0 0 1 -87 0 cm` also sits at the identity matrix --
        # splicing there cuts the operand list in half and the text object
        # it belonged to renders somewhere else or not at all. Requiring
        # an empty operand stack is what makes the offset a real seam.
        if self.operands or self.matrix != _IDENTITY or self.clipped:
            return
        if self.in_text:
            # Not neutral -- a blob spliced here would nest `BT` inside `BT`.
            # It is where the object can be *closed and reopened* instead,
            # which only a following absolute `Tm` makes safe. `_set_origin`
            # decides that; all this records is the last offer.
            self.seam = token.end
            return
        self.neutral.append(token.end)

    def _operator(self, token: _Token) -> None:
        operator = token.text.decode("latin-1")
        handler = _OPERATORS.get(operator)
        if handler is not None:
            handler(self, operator, token)
        self.operands = []

    # -- one method per operator that moves the state -----------------------

    def _inline_image(self, operator: str, token: _Token) -> None:
        # Inline image data is not tokenisable by this scanner: the bytes
        # between ID and EI are arbitrary and would be read as operators.
        raise _Unsupported("inline image")

    def _push_state(self, operator: str, token: _Token) -> None:
        self.stack.append(self.matrix)
        self.clip_stack.append(self.clipped)

    def _pop_state(self, operator: str, token: _Token) -> None:
        if not self.stack:
            raise _Unsupported("unbalanced Q")
        self.matrix = self.stack.pop()
        self.clipped = self.clip_stack.pop()

    def _concat_matrix(self, operator: str, token: _Token) -> None:
        if len(self.operands) < 6 or any(value is None for value in self.operands[-6:]):
            raise _Unsupported("malformed cm")
        self.matrix = _concat(tuple(self.operands[-6:]), self.matrix)  # type: ignore[arg-type]

    def _clip(self, operator: str, token: _Token) -> None:
        self.clipped = True

    def _begin_text(self, operator: str, token: _Token) -> None:
        if self.in_text:
            raise _Unsupported("nested BT")
        self.in_text, self.text_start, self.line = True, token.start, None
        self.run, self.runs, self.drew, self.seam = None, [], False, None

    def _close_run(self) -> None:
        """Bank the run in progress with the size it was drawn at.

        A run's `Tf` may follow the operator that positions it -- mupdf's own
        writer emits `]TJ/helv 9 Tf 0 -13.29 TD[` -- so the size is read when
        the run ends, not when it opens.
        """
        if self.run is None:
            return
        splice, line, reopen = self.run
        self.runs.append((splice, line, self.font_size, self.drew, reopen))
        self.run, self.drew = None, False

    def _end_text(self, operator: str, token: _Token) -> None:
        if not self.in_text:
            raise _Unsupported("ET without BT")
        if self.line is None:
            raise _Unsupported("text object with no positioning operator")
        self._close_run()
        runs = [run for run in self.runs if run[3]]
        # One text object holding one run is the object itself, and only then
        # may it be reopened in place: `_rewrite_object_in_place` is handed
        # `base[start:end]` and reads it as a whole `BT ... ET`, which a run
        # out of the middle of a larger object is not.
        whole = (
            self.matrix == _IDENTITY
            and len(runs) == 1
            and runs[0][0] == self.text_start
        )
        for splice, line, font_size, _drew, reopen in runs:
            page_x, page_y, drawn = _drawn_at(
                (line[4], line[5]), self.matrix, font_size, abs(line[3])
            )
            self.objects.append((splice, page_x, page_y, drawn, token.end, whole))
            if reopen is not None:
                self.interior[splice] = reopen
        self.in_text = False

    def _set_font(self, operator: str, token: _Token) -> None:
        # The size the text is drawn at, before the matrices scale it.
        self.font_size = (
            float(self.operands[-1]) if self.operands and self.operands[-1] else 0.0
        )

    def _set_leading(self, operator: str, token: _Token) -> None:
        (self.leading,) = self._numbers(1, operator)

    def _numbers(self, count: int, operator: str) -> list[float]:
        """Return an operator's last *count* operands, refusing anything else.

        One reader for every operator that takes numbers, so a count and a
        `None` among them are refused the same way wherever they turn up --
        two spellings of the same check drift, and the one that reads a name
        as a coordinate would put a line somewhere it was never drawn.
        """
        values = self.operands[-count:] if len(self.operands) >= count else []
        if len(values) != count or any(value is None for value in values):
            raise _Unsupported(f"malformed {operator}")
        return [float(value) for value in values if value is not None]

    def _move_line(self, operator: str, token: _Token) -> None:
        """Carry the text line matrix through one positioning operator.

        Every one of them is tracked, not only the first. Whole pages are
        drawn inside a single `BT ... ET` -- it is what mupdf's own writer
        leaves behind, so it is the shape of every page this service has just
        redacted -- and read as one object the page sat at its first line.
        Every replacement below that line then had no base text to be ordered
        against and went to the foot of the page: the en-GB letter extracted
        its header name, NHS number and date after the signature.
        """
        if not self.in_text:
            return
        if operator == "Tm":
            line = tuple(self._numbers(6, operator))
        else:
            shift: tuple[float, ...]
            if operator == "T*":
                shift = (0.0, -self.leading)
            else:
                shift = tuple(self._numbers(2, operator))
                if operator == "TD":
                    self.leading = -shift[1]
            line = _concat((1.0, 0.0, 0.0, 1.0, *shift), self.line or _IDENTITY)
        self._open_run(line)

    def _open_run(self, line: tuple[float, ...]) -> None:
        """Start a run at *line*, if the object can be cut open in front of it.

        Cutting means closing the object and reopening it, and the reopen has
        to leave the state the operators after it expect. An absolute `Tm`
        restores the line matrix exactly, and every other text parameter --
        font, leading, spacing, rise, render mode -- is graphics state that
        outlives `ET`, so nothing else has to be carried across. Nor does the
        text matrix: whatever follows the seam is this run's own positioning
        operator, and that operator derives the text matrix from the line
        matrix rather than from where the last glyph left it.

        Only a move that changes the *baseline* opens a run. A move along the
        line is another column of the same row, and rows are what this module
        orders by; cutting there would also cost the row the one thing a run
        cannot do -- `_rewrite_object_in_place` splices a replacement into the
        gap redaction left *inside* a line, and it needs the whole object.

        With no seam on offer the run simply continues under the origin it
        opened with, which is the answer this scanner has always given.
        """
        if self.line is None:
            self.line, self.run = line, (self.text_start, line, None)
            return
        if self.seam is not None and abs(line[5] - self.line[5]) > _BASELINE_EPSILON:
            reopen = b"BT " + _matrix_operands(self.line) + b" Tm"
            self._close_run()
            self.run = (self.seam, line, reopen)
        self.line = line

    def _show_text(self, operator: str, token: _Token) -> None:
        # `'` and `"` position before they show, exactly as `T*` does.
        if operator in ("'", '"'):
            self._move_line("T*", token)
        # A run that shows nothing is a position, not text on the page, and
        # reporting it would invent a row for a replacement to be placed in.
        self.drew = True

    # -- driving ------------------------------------------------------------

    def feed(self, token: _Token) -> None:
        _KINDS.get(token.kind, _Scanner._opaque)(self, token)

    def finish(
        self,
    ) -> tuple[
        list[tuple[int, float, float, float, int, bool]], list[int], dict[int, bytes]
    ]:
        if self.in_text or self.stack:
            raise _Unsupported("stream ends mid-object")
        return self.objects, sorted(set(self.neutral)), self.interior


_KINDS: dict[str, Callable[[_Scanner, _Token], None]] = {
    "ws": _Scanner._boundary,
    "comment": _Scanner._boundary,
    "number": _Scanner._number,
    "operator": _Scanner._operator,
}

_OPERATORS: dict[str, Callable[[_Scanner, str, _Token], None]] = {
    "BI": _Scanner._inline_image,
    "q": _Scanner._push_state,
    "Q": _Scanner._pop_state,
    "cm": _Scanner._concat_matrix,
    "W": _Scanner._clip,
    "W*": _Scanner._clip,
    "BT": _Scanner._begin_text,
    "ET": _Scanner._end_text,
    "Tf": _Scanner._set_font,
    "TL": _Scanner._set_leading,
    **{operator: _Scanner._move_line for operator in ("Td", "TD", "Tm", "T*")},
    **{operator: _Scanner._show_text for operator in ("Tj", "TJ", "'", '"')},
}


def scan(
    data: bytes,
) -> tuple[
    list[tuple[int, float, float, float, int, bool]], list[int], dict[int, bytes]
]:
    """Return where text is drawn, and where a blob may be spliced in.

    The first list is one `(start, x, y, size, end, whole)` per *positioned
    run* of text, in stream order, with the origin and the drawn size already
    carried through the graphics state into PDF page coordinates. `size` is
    what tells two columns of one row apart from two consecutive rows. A text
    object that positions itself once is one run and `start` is its `BT`;
    an object that repositions with a further absolute `Tm` reports a run per
    `Tm`, and `start` is the offset the object may be closed and reopened at.

    `whole` says the run is a text object entire, drawn at the identity
    matrix -- both preconditions for reopening it in place.

    The second list is the sorted offsets that are safe to splice at as they
    stand. The third maps each offset that lies *inside* a text object to the
    `BT ... Tm` that reopens it there, which a blob spliced at that offset
    has to close with `ET` and end with.

    Raises `_Unsupported` rather than guessing.
    """
    scanner = _Scanner()
    for token in _tokens(data):
        scanner.feed(token)
    return scanner.finish()


# Text-state operators that may appear inside a splittable object. Anything
# else -- character spacing, word spacing, horizontal scaling, rise, render
# mode -- would have to be carried across the reopen, so its presence refuses
# the split instead.
_ALLOWED_INSIDE = frozenset(
    {
        "Tf",
        "Td",
        "TD",
        "Tj",
        "TJ",
        "g",
        "G",
        "rg",
        "RG",
        "k",
        "K",
        "cs",
        "CS",
        "sc",
        "scn",
        "SC",
        "SCN",
        "gs",
        "w",
        "M",
        "d",
        "j",
        "J",
        "i",
        "ri",
    }
)
_SHOWING = frozenset({"Tj", "TJ", "'", '"'})


class _Refused(Exception):
    """A shape this module does not understand; the object stays whole."""


class _Seam(NamedTuple):
    """Where an object may be cut open, and what to write at the cut.

    A cut drops `chunk[start:end]`, writes `close` before the replacement and
    `reopen` after it. Between two show operators there is nothing to drop and
    nothing to write: the text before the cut is already a finished operator,
    so `start == end` and both are empty.

    Inside one `TJ` array there is. The array has to be closed before the
    splice and opened again after it, and the kerning number that held the
    rest of the line in place is dropped -- the reopen positions that text
    absolutely, so the displacement is no longer carrying anything.

    `width` is what that number displaced, in points, and it is the evidence
    that the number is a removal rather than ordinary letter spacing: the
    pipeline knows how wide the value it took out was, and a candidate is cut
    only when the two agree. `None` marks a seam that needs no such proof.
    """

    start: int
    end: int
    close: bytes
    reopen: bytes
    width: float | None


# How far, in points, a kern inside an array may sit from the width of the
# value redaction removed and still be read as that removal. The two are
# computed by different code from different inputs -- glyph geometry here,
# mupdf's own arithmetic there -- so they agree closely rather than exactly.
_KERN_TOLERANCE = 0.1


class _SeamReader:
    """Reads one text object, collecting the slots a replacement may take.

    Written as a state machine for the same reason `_Scanner` is: what an
    operator does to the reading is a fact about that operator, and belongs
    next to its name rather than inside a chain of branches.
    """

    def __init__(self, chunk: bytes) -> None:
        self.chunk = chunk
        self.font: str | None = None
        self.size = 0.0
        self.y_position: float | None = None
        self.operands: list[_Token] = []
        self.operand_start: int | None = None
        self.seams: list[_Seam] = []
        self.drawn = False
        self.kerned = False
        self.has_string = False
        self.trailing = -1

    def _clear(self) -> None:
        self.operands, self.operand_start, self.has_string = [], None, False

    def _collect(self, token: _Token) -> None:
        if self.operand_start is None:
            self.operand_start = token.start
        self.operands.append(token)
        self.has_string = self.has_string or token.kind in ("string", "hex")

    def _last_number(self) -> float:
        if len(self.operands) < 2:
            raise _Refused("too few operands")
        token = self.operands[-1]
        if token.kind != "number":
            raise _Refused("non-numeric operand")
        try:
            return float(token.text)
        except ValueError:
            raise _Refused("non-numeric operand") from None

    def _interior_kerns(self) -> list[_Seam]:
        """Candidate seams for the kerns sitting between two strings of a `TJ`.

        Redaction does not always leave a kern-only operator behind. Where the
        producer drew the line as a single show -- which is what mupdf's own
        writer emits, so it is the commonest shape there is -- the number
        standing in for the value lands *inside* the rebuilt array, and there
        is no boundary between operators to cut at. Every replacement on such
        a line went to the end of that line instead of into its own gap.

        Only a plain `[ ... ]` of strings and numbers is read, and only a
        number with a string on both sides is offered. Whether an offered
        number really is a removal is not decided here: an array is also where
        a producer puts ordinary letter spacing, and the two are told apart by
        width, in `_rewrite_object_in_place`, which knows what was taken out.
        """
        body = self.operands
        if len(body) < 5 or body[0].kind != "array_open":
            return []
        if body[-1].kind != "array_close":
            return []
        body = body[1:-1]
        if any(token.kind not in ("string", "hex", "number") for token in body):
            return []
        seams: list[_Seam] = []
        for index in range(1, len(body) - 1):
            token = body[index]
            if token.kind != "number":
                continue
            if body[index - 1].kind == "number" or body[index + 1].kind == "number":
                continue
            width = -float(token.text) * self.size / 1000.0
            if width <= 0:
                # A positive kern pulls the line together; nothing was removed.
                continue
            seams.append(_Seam(token.start, token.end, b"]TJ", b"[", width))
        return seams

    # -- one method per operator that moves the reading ---------------------

    def _begin_text(self, token: _Token) -> None:
        self._clear()

    def _end_text(self, token: _Token) -> None:
        self.trailing = token.start
        self._clear()

    def _set_font(self, operator: str, token: _Token) -> None:
        self.font = _font_of(self.chunk, token.start)
        self.size = self._last_number()

    def _set_line(self, operator: str, token: _Token) -> None:
        self.y_position = self._last_number()

    def _show(self, operator: str, token: _Token) -> None:
        if self.has_string:
            if self.drawn and self.kerned and self.operand_start is not None:
                start = self.operand_start
                self.seams.append(_Seam(start, start, b"", b"", None))
            if operator == "TJ":
                self.seams.extend(self._interior_kerns())
            self.drawn, self.kerned = True, False
        elif self.drawn:
            # A `TJ` of numbers only draws nothing; it is the displacement
            # standing in for what was redacted away.
            self.kerned = True

    # -- driving ------------------------------------------------------------

    def _operator(self, token: _Token) -> None:
        operator = token.text.decode("latin-1")
        boundary = _TEXT_BOUNDARIES.get(operator)
        if boundary is not None:
            boundary(self, token)
            return
        if operator not in _ALLOWED_INSIDE:
            raise _Refused(f"operator {operator}")
        handler = _INSIDE_OPERATORS.get(operator)
        if handler is not None:
            handler(self, operator, token)
        self._clear()

    def feed(self, token: _Token) -> None:
        if token.kind in ("ws", "comment"):
            return
        if token.kind != "operator":
            self._collect(token)
            return
        self._operator(token)

    def result(self) -> tuple[str, float, float, list[_Seam], int] | None:
        if self.font is None or self.y_position is None or self.trailing < 0:
            return None
        seams = sorted(self.seams, key=lambda seam: seam.start)
        return self.font, self.size, self.y_position, seams, self.trailing


_TEXT_BOUNDARIES: dict[str, Callable[[_SeamReader, _Token], None]] = {
    "BT": _SeamReader._begin_text,
    "ET": _SeamReader._end_text,
}

_INSIDE_OPERATORS: dict[str, Callable[[_SeamReader, str, _Token], None]] = {
    "Tf": _SeamReader._set_font,
    "Td": _SeamReader._set_line,
    "TD": _SeamReader._set_line,
    **{operator: _SeamReader._show for operator in _SHOWING},
}


def object_seams(chunk: bytes) -> tuple[str, float, float, list[_Seam], int] | None:
    """Return `(font, size, y, seams, trailing)` for a reopenable text object.

    A *seam* is where redaction took a value out of the middle of the line,
    and it comes in two shapes. Where the line was drawn as several show
    operators, mupdf leaves a kerning-only `TJ` in the value's place: that
    number is what holds the rest of the line where it was, so it marks a
    removal exactly, unlike a mere boundary between two show operators, which
    a producer may emit for its own reasons. The seam is the start of the
    operands of the show operator after the kern, so the kern itself stays
    with the text before it.

    Where the line was drawn as one show, there is no boundary to cut at and
    the number lands inside the rebuilt array. Those are offered too, and the
    caller settles by width which of them are removals -- see `_Seam`.

    *trailing* is the offset just before the closing `ET`. A value at the end
    of a line -- "par le Dr. <name> le <date>", where the date is last --
    leaves nothing after it to seam against, and without this slot both
    replacements on that line fall back to the end of the page. This is what
    the first cut of the split missed.

    Returns `None` whenever anything about the object is not understood, which
    keeps it whole.
    """
    reader = _SeamReader(chunk)
    try:
        for token in _tokens(chunk):
            reader.feed(token)
    except _Refused:
        return None
    except _Unsupported:
        # An unreadable byte or an unterminated string. `scan` refuses the
        # whole page for these; here the object simply stays whole.
        return None
    return reader.result()


def _font_of(chunk: bytes, before: int) -> str | None:
    """Return the resource name of the `Tf` whose operator starts at *before*."""
    head = chunk[:before].rstrip()
    cut = head.rfind(b"/")
    if cut < 0:
        return None
    name = head[cut:].split()[0]
    return name.decode("latin-1")


def _rows(entries: list[tuple[float, float]]) -> list[int]:
    """Return a row index per entry, given its `(baseline, size)`.

    Grown from a canonical sort rather than by comparing neighbours, so the
    answer is a pure function of the input. Half the size of the line that
    opens a row: two columns of one header row have baselines a point or two
    apart and belong together, while consecutive body lines sit a full line
    apart and must not merge.
    """
    order = sorted(range(len(entries)), key=lambda index: entries[index])
    rows: list[tuple[float, float]] = []
    assigned = [0] * len(entries)
    for index in order:
        baseline, size = entries[index]
        for row, (centre, tolerance) in enumerate(rows):
            if abs(baseline - centre) <= tolerance:
                assigned[index] = row
                break
        else:
            rows.append((baseline, _ROW_TOLERANCE * (size or _DEFAULT_SIZE)))
            assigned[index] = len(rows) - 1
    return assigned


def _rewrite_object_in_place(
    chunk: bytes,
    object_x: float,
    orders: list[int],
    units: list[tuple[float, float, float, float, bytes]],
) -> tuple[bytes, list[int]] | None:
    """Return *chunk* with each owned replacement spliced into its own gap.

    Returns the rewritten object and the orders placed inside it, or `None`
    when the object cannot be split safely -- in which case the caller leaves
    it alone and those replacements fall back to the seam before the next text
    object, which is always available.
    """
    parsed = object_seams(chunk)
    if parsed is None:
        return None
    font, size, y_text, seams, trailing = parsed
    orders = sorted(orders, key=lambda order: units[order][1])
    # A value at the very start of a line has nothing before it inside the
    # object; it goes in front of the whole object instead, which is the
    # same place and needs no surgery.
    while orders and units[orders[0]][1] <= object_x + _EDGE_TOLERANCE:
        orders.pop(0)
    gaps = _matched_seams(seams, orders, units)
    if len(orders) == len(gaps):
        slots = list(gaps)
    elif len(orders) > len(gaps):
        # The extras ran to the end of the line: redaction left no operator
        # after them, so their slot is the close of the object. More than one
        # can land there. Reflow redraws the glyphs between two replacements
        # as a replacement of its own, and on a line whose last value sits
        # mid-sentence that is a second thing with nothing after it -- the
        # pseudonym, then the words it pushed right. Refusing the object over
        # the count sent both to the end of the page instead, and the French
        # conclusion line extracted with its date after the clause that
        # follows it.
        close = _Seam(trailing, trailing, b"", b"", None)
        slots = list(gaps) + [close] * (len(orders) - len(gaps))
    else:
        return None
    pieces: list[bytes] = []
    placed_inside: list[int] = []
    cursor = 0
    for order, seam in zip(orders, slots):
        pieces.append(chunk[cursor : seam.start])
        pieces.append(seam.close)
        pieces.append(
            b" ET "
            + units[order][4].strip()
            + b" BT"
            + f"{font} {size:g} Tf {units[order][3]:.5f} {y_text:g} TD ".encode(
                "latin-1"
            )
        )
        pieces.append(seam.reopen)
        cursor = seam.end
        placed_inside.append(order)
    pieces.append(chunk[cursor:])
    return b"".join(pieces), placed_inside


def _matched_seams(
    seams: list[_Seam],
    orders: list[int],
    units: list[tuple[float, float, float, float, bytes]],
) -> list[_Seam]:
    """Keep the seams between operators, and the interior kerns that fit.

    A seam between two show operators is a removal by construction and is
    always kept. A kern inside an array is only a candidate: the same place
    holds ordinary letter spacing, and cutting there would open a line the
    producer meant to stay closed. So a candidate is kept only when what it
    displaced is the width of a value this object actually lost, measured
    from the glyph geometry the pipeline already carries -- and only as many
    as there are replacements the operator seams cannot seat. Matching none
    leaves the object exactly as it was read before this was possible.
    """
    between = [seam for seam in seams if seam.width is None]
    extra = len(orders) - len(between)
    if extra <= 0:
        return between
    wanted = [units[order][3] - units[order][1] for order in orders]
    kept: list[_Seam] = []
    for seam in seams:
        if seam.width is None:
            continue
        fit = next(
            (
                width
                for width in wanted
                if abs(width - (seam.width or 0.0)) <= _KERN_TOLERANCE
            ),
            None,
        )
        if fit is None:
            continue
        wanted.remove(fit)
        kept.append(seam)
        if len(kept) == extra:
            break
    return sorted(between + kept, key=lambda seam: seam.start)


def _assemble_content(
    base: bytes,
    splices: list[tuple[int, int, bytes]],
    rewritten_end: dict[int, int],
) -> bytes:
    """Return *base* with every splice applied, dropping none of them.

    An insertion whose offset falls inside an object that was rewritten in
    place is emitted straight after that object rather than skipped -- silently
    discarding it would lose a pseudonym from the page while leaving the
    redaction that removed the original.

    At one offset, an insertion goes before the object rewritten there: it
    belongs to the line above, and the rewrite is that line's successor.
    """
    splices.sort(key=lambda item: (item[0], item[1] == -1, item[1]))
    pieces: list[bytes] = []
    cursor = 0
    for offset, order, blob in splices:
        if order == -1:
            if offset >= cursor:
                pieces.append(base[cursor:offset])
                cursor = rewritten_end[offset]
            pieces.append(blob)
            continue
        if offset > cursor:
            pieces.append(base[cursor:offset])
            cursor = offset
        pieces.append(b"\n" + blob.strip() + b"\n")
    pieces.append(base[cursor:])
    return b"".join(pieces)


def _read_base(
    document: pymupdf.Document, page: pymupdf.Page, base_xrefs: list[int]
) -> (
    tuple[
        bytes,
        list[tuple[int, float, float, float, int, bool]],
        list[int],
        dict[int, bytes],
    ]
    | None
):
    """Return the page's pre-replacement stream and its scan, or None to skip."""
    try:
        base = b"\n".join(document.xref_stream(xref) or b"" for xref in base_xrefs)
        objects, neutral, interior = scan(base)
    except _Unsupported as exc:
        log.info("content_order_skipped page=%d reason=%s", page.number, exc)
        return None
    except Exception as exc:  # pragma: no cover - pymupdf raises many types
        log.info("content_order_skipped page=%d reason=%r", page.number, exc)
        return None
    if not neutral:
        return None
    return base, objects, neutral, interior


def _in_page_space(
    objects: list[tuple[int, float, float, float, int, bool]],
    to_page: pymupdf.Matrix,
) -> list[tuple[int, float, float, float, int, bool]]:
    """Carry the scanned objects into the coordinates the caller measured in.

    Reading order is down the page first, then across. The base objects come
    out of `scan` in PDF space, where y grows the other way, so they are
    mapped rather than compared directly.
    """
    placed: list[tuple[int, float, float, float, int, bool]] = []
    for offset, x, y, size, end, plain in objects:
        point = pymupdf.Point(x, y) * to_page
        placed.append((offset, point.y, point.x, size, end, plain))
    return placed


def _placements(
    placed: list[tuple[int, float, float, float, int, bool]],
    base_rows: list[int],
    units: list[tuple[float, float, float, float, bytes]],
    unit_rows: list[int],
    stream_end: int,
) -> tuple[list[int], dict[int, list[int]]]:
    """Find, per replacement, where it goes and which object it came out of.

    Two placements per replacement. The target is the seam before the next
    text object -- always available, and what the whole page falls back to.
    `owner` maps a text object to the replacements redacted out of it, which
    can often be split at the gap the redaction left inside it.
    """
    outer_target: list[int] = []
    owner: dict[int, list[int]] = {}
    for order, (_baseline, x, _size, _resume, _blob) in enumerate(units):
        key = (unit_rows[order], x)
        home = -1
        target = stream_end
        for index, entry in enumerate(placed):
            if (base_rows[index], entry[2]) >= key:
                target = entry[0]
                break
            if base_rows[index] == unit_rows[order]:
                home = index
        outer_target.append(target)
        if home >= 0 and placed[home][5]:
            owner.setdefault(home, []).append(order)
    return outer_target, owner


def _rewritten_objects(
    base: bytes,
    placed: list[tuple[int, float, float, float, int, bool]],
    owner: dict[int, list[int]],
    units: list[tuple[float, float, float, float, bytes]],
) -> tuple[list[tuple[int, int, bytes]], set[int]]:
    """Rewrite each text object that can carry its own replacements inside it."""
    rewrites: list[tuple[int, int, bytes]] = []
    placed_inside: set[int] = set()
    for index, orders in sorted(owner.items()):
        object_start, _y, object_x, _size, object_end, _plain = placed[index]
        rewritten = _rewrite_object_in_place(
            base[object_start:object_end], object_x, orders, units
        )
        if rewritten is None:
            continue
        chunk, inside = rewritten
        placed_inside.update(inside)
        rewrites.append((object_start, object_end, chunk))
    return rewrites, placed_inside


def _outer_splice(
    neutral: list[int],
    interior: dict[int, bytes],
    target: int,
    order: int,
    blob: bytes,
    stream_end: int,
) -> tuple[int, int, bytes]:
    """Place a replacement outside the object it came from.

    Snap back to the last point where a self-contained blob renders the way it
    was drawn. For a text object under its own `cm` -- every OCR line -- that
    is the offset before the `q` that opens it. With no seam before it, leave
    the blob where it already was: the end of the stream is today's behaviour,
    and offset zero would bury it under whatever the page paints first, a
    full-page scan included.

    A seam *inside* a text object beats one before it whenever it is nearer,
    which on a page drawn as a single `BT ... ET` is every seam there is: the
    only offsets outside that object are ahead of its first line and behind
    its last, so without these the whole page's replacements pile up at one
    end or the other. The blob then closes the object and reopens it with the
    line matrix `scan` recorded for that seam.
    """
    outside = [point for point in neutral if point <= target]
    inside = sorted(point for point in interior if point <= target)
    if inside and (not outside or inside[-1] > outside[-1]):
        seam = inside[-1]
        return (seam, order, b"ET " + blob.strip() + b" " + interior[seam])
    return (outside[-1] if outside else stream_end, order, blob)


def restore(
    document: pymupdf.Document,
    page: pymupdf.Page,
    base_xrefs: list[int],
    units: list[tuple[float, float, float, float, bytes]],
) -> bool:
    """Rewrite *page*'s contents with each unit spliced into reading order.

    *base_xrefs* are the page's content streams as they stood before any
    replacement was drawn; *units* are `(baseline, x, size, bytes)` per
    replacement, in unrotated PyMuPDF coordinates.

    Returns whether the page was rewritten. `False` means it was left exactly
    as it was, which is always a safe answer.
    """
    if not units or page.rotation:
        return False
    read = _read_base(document, page, base_xrefs)
    if read is None:
        return False
    base, objects, neutral, interior = read

    placed = _in_page_space(objects, page.transformation_matrix)
    # Rows are decided over the base text and the replacements together, or a
    # replacement could not be compared against the line it belongs to. Doing
    # it by raw baseline is what put a date before the clinician beside it:
    # three columns of one header row had baselines 1.4pt apart, and a strict
    # sort by y read them right-to-left.
    rows = _rows(
        [(item[1], item[3]) for item in placed]
        + [(baseline, size) for baseline, _x, size, _resume, _blob in units]
    )
    base_rows, unit_rows = rows[: len(placed)], rows[len(placed) :]

    outer_target, owner = _placements(placed, base_rows, units, unit_rows, len(base))
    rewrites, placed_inside = _rewritten_objects(base, placed, owner, units)

    splices = [
        _outer_splice(
            neutral, interior, outer_target[order], order, units[order][4], len(base)
        )
        for order in range(len(units))
        if order not in placed_inside
    ]
    rewritten_end = {start: end for start, end, _bytes in rewrites}
    splices.extend((start, -1, replacement) for start, _end, replacement in rewrites)

    merged = _assemble_content(base, splices, rewritten_end)
    xref = document.get_new_xref()
    document.update_object(xref, "<<>>")
    document.update_stream(xref, merged)
    page.set_contents(xref)
    return True
