"""Close a line a masked value emptied, by lifting the lines under it.

`pdf_line_reflow` makes room *along* a line. This module answers the other
half: what to do when a value was wide enough to occupy a whole line, so that
erasing it leaves nothing on that line at all.

One replacement is drawn per span, on the line the value ended on (see
`_plan_page`), which is what keeps the erased room at line ends where a
ragged-right paragraph already puts it. A value spanning three or more lines
is the case that has no line end to hide in: its middle lines were the value
and nothing else, and a blank line inside a paragraph is not a shorter line,
it is a hole.

So the lines under it move up. The rules are deliberately narrow, because
moving text vertically is the one reflow that can put a line somewhere it does
not belong:

* Only lines of the same paragraph move, and a paragraph here is a run of
  consecutive baselines with one step between them and one left edge. A
  heading, a footer, the next paragraph and the second column of a table all
  fail one of those two tests, so none of them is ever lifted.
* A line moves by exactly the paragraph's own step, once per blank line above
  it. The paragraph therefore keeps its leading and ends earlier; the room it
  gives up lands in the gap before whatever follows, which is where a reader
  expects to find blank paper.
* Anything that cannot be measured is left alone. A paragraph of one line has
  no step, so nothing in it moves.

Geometry-only, like `pdf_line_reflow`: no ``MaskSpan``, no glyph type, no
pymupdf page. Callers hand in plain lines and get plain lifts back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# How far two baseline steps may differ and still count as one paragraph's
# leading. Typesetters write a constant leading; the slack is for the rounding
# a PDF's own coordinates carry.
MAX_STEP_DRIFT = 0.75

# How far two lines' left edges may differ and still count as one paragraph.
# Tight on purpose: an indent, a list marker and a second column are all
# larger than this, and each of them means the line below is not a
# continuation of the line above.
MAX_LEFT_DRIFT = 1.0

# The largest leading a lift will believe. Two paragraphs separated by a blank
# line have a "step" of twice the leading, and lifting across that gap would
# pull the next paragraph into the previous one's white space.
MAX_STEP = 32.0


@dataclass(frozen=True)
class Line:
    """One visual line of a page: where it sits, and whether anything is left.

    *blank* is the caller's judgement, not a geometric one: a line is blank
    when every glyph on it is being redacted away and no replacement is drawn
    there. The geometry still comes from the glyphs that were erased, which is
    the only place it exists once they are gone.
    """

    key: float
    x0: float
    x1: float
    baseline: float
    blank: bool


def _paragraphs(lines: Sequence[Line]) -> list[list[Line]]:
    """Split *lines* into runs of one leading and one left edge."""
    ordered = sorted(lines, key=lambda line: line.baseline)
    groups: list[list[Line]] = []
    step: float | None = None
    for line in ordered:
        if groups:
            previous = groups[-1][-1]
            gap = line.baseline - previous.baseline
            aligned = abs(line.x0 - groups[-1][0].x0) <= MAX_LEFT_DRIFT
            regular = 0 < gap <= MAX_STEP and (
                step is None or abs(gap - step) <= MAX_STEP_DRIFT
            )
            if aligned and regular:
                if step is None:
                    step = gap
                groups[-1].append(line)
                continue
        groups.append([line])
        step = None
    return groups


def plan(lines: Sequence[Line]) -> dict[float, float]:
    """Return how far up each line moves, keyed by `Line.key`.

    Only lines that actually move are named; a lift is a positive number of
    points, to be subtracted from a y that grows downward.
    """
    lifts: dict[float, float] = {}
    for paragraph in _paragraphs(lines):
        if len(paragraph) < 2 or not any(line.blank for line in paragraph):
            continue
        step = paragraph[1].baseline - paragraph[0].baseline
        lift = 0.0
        for line in paragraph:
            if line.blank:
                lift += step
            elif lift:
                lifts[line.key] = lift
    return lifts
