"""Make room on a line for a box wider than the text it replaces.

A redaction chip is sized by what is drawn inside it, not by the token it
covers. A two-digit age slot holds neither "AGE 97%" nor "AGE", so the fitter
shrank the text and then truncated it to "A..." -- a chip naming neither its
label nor its value. The room it needed was on the line all along, in the
whitespace after it.

This module decides where that room comes from. It never reflows the page,
only a line, and only rightward:

* Each chip that wants more width than its slot pushes everything after it on
  the same line to the right by the surplus.
* A gap between two words absorbs what it can spare, so the disturbance decays
  as it travels right and usually dies within a word or two. That is also what
  keeps a second column, a table cell and a right-aligned field where they
  are: the gutter in front of them is a large gap, so it swallows the shift
  and nothing beyond it moves.
* A line that still cannot absorb the surplus by its own right boundary is
  planned as no shift at all, and the caller falls back to shrinking the text.
  The worst case is therefore exactly the behaviour that was there before.

The module is deliberately geometry-only: no ``MaskSpan``, no glyph type, no
pymupdf page. Callers hand in plain rectangles and get plain offsets back,
which is what lets the whole policy be tested without synthesising a PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What has to survive between two words after a gap has given up room to a
# shift. Below this they read as one word.
MIN_RESIDUAL_GAP = 1.5

# How far apart two items' vertical midpoints may sit and still count as one
# visual line. A line's glyphs share a baseline; this tolerates the small
# differences a superscript or a size change introduces.
_LINE_TOLERANCE = 2.0


@dataclass(frozen=True)
class Slot:
    """A chip that wants room: where it sits now, and how wide it wants to be."""

    key: int
    x0: float
    x1: float
    top: float
    bottom: float
    wanted: float

    @property
    def surplus(self) -> float:
        return max(0.0, self.wanted - (self.x1 - self.x0))


@dataclass(frozen=True)
class Fixed:
    """A glyph that is staying on the page and may have to move aside."""

    key: int
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass
class Plan:
    """How far right each slot and each glyph moves. Absent means unmoved.

    `granted` names the slots whose line was reflowed, which is not the same
    set as `slot_shift`: the leftmost chip on a line gets the width it asked
    for without moving at all, and a chip whose surplus the whitespace after
    it absorbs entirely moves nothing after it either.
    """

    slot_shift: dict[int, float] = field(default_factory=dict)
    glyph_shift: dict[int, float] = field(default_factory=dict)
    granted: set[int] = field(default_factory=set)
    # Lines that wanted room and could not be given it, so their slots fall
    # back to condensing. Reported rather than inferred: a refusal is the one
    # outcome the rendered page cannot be read for, since a condensed
    # pseudonym occupies its slot exactly like a fitting one.
    refused: int = 0


def _midpoint(item: Slot | Fixed) -> float:
    return (item.top + item.bottom) / 2


def _lines(items: list[Slot | Fixed]) -> list[list[Slot | Fixed]]:
    """Group *items* into visual lines by their vertical midpoint."""
    lines: list[list[Slot | Fixed]] = []
    for item in sorted(items, key=_midpoint):
        if lines and abs(_midpoint(item) - _midpoint(lines[-1][0])) <= _LINE_TOLERANCE:
            lines[-1].append(item)
        else:
            lines.append([item])
    return lines


def _plan_line(
    line: list[Slot | Fixed], right_limit: float, clearance: float
) -> Plan | None:
    """Return the shifts for one line, or None when it cannot absorb them.

    Walks the line left to right carrying the shift owed so far. A chip adds
    its surplus to that debt; the whitespace before each following item pays
    down whatever it can spare. Refusing the whole line rather than part of it
    keeps the outcome predictable: either the line reflows or it renders the
    way it always did.
    """
    plan = Plan()
    source_right: float | None = None  # where the item before this one ended
    drawn_right = 0.0  # where it ends once the shifts so far are applied
    after_slot = False

    for item in sorted(line, key=lambda entry: (entry.x0, entry.x1)):
        if source_right is None:
            shift = 0.0
        else:
            # Keep the gap the document wrote, except that a gap wider than a
            # word space may give up everything above one. That is the whole
            # of the policy: letters inside a word move rigidly because the
            # gap between them is nothing, and a gutter absorbs a shift whole
            # because the gap in front of it is large.
            required = min(item.x0 - source_right, MIN_RESIDUAL_GAP)
            if after_slot:
                # A box's chrome paints outside the rectangle it is drawn in,
                # so the glyph after it needs that much clear or the border
                # lands on the glyph. Punctuation is what this is for: a comma
                # sits flush against the value it follows, so the gap the
                # document wrote is nothing and the rule above would preserve
                # exactly nothing.
                required = max(required, clearance)
            shift = max(0.0, drawn_right + required - item.x0)

        source_right = item.x1
        if isinstance(item, Slot):
            if shift > 0:
                plan.slot_shift[item.key] = shift
            plan.granted.add(item.key)
            # The chip is drawn at the width it asked for, so what follows it
            # clears that width rather than the slot's own.
            drawn_right = item.x0 + shift + max(item.x1 - item.x0, item.wanted)
            after_slot = True
        else:
            if shift > 0:
                plan.glyph_shift[item.key] = shift
            drawn_right = item.x1 + shift
            after_slot = False

    if drawn_right > right_limit:
        return None
    return plan


def plan(
    slots: list[Slot],
    fixed: list[Fixed],
    right_limit: float,
    clearance: float = 0.0,
) -> Plan:
    """Return the shifts that give every slot in *slots* the width it wants.

    *fixed* is every glyph that stays on the page -- the ones no span covers.
    A glyph a chip is replacing is not one of them; it is gone by the time
    anything is drawn.

    *right_limit* is the x beyond which a line may not grow. A line that would
    cross it contributes nothing, so the caller's existing shrink-and-truncate
    path handles it unchanged.

    *clearance* is how far the caller's chrome paints outside the box it is
    given, kept clear after every slot so a border is not drawn on top of the
    glyph that follows it.
    """
    if not slots:
        return Plan()
    combined = Plan()
    for line in _lines([*slots, *fixed]):
        if not any(isinstance(item, Slot) and item.surplus > 0 for item in line):
            continue
        line_plan = _plan_line(line, right_limit, clearance)
        if line_plan is None:
            combined.refused += 1
            continue
        combined.slot_shift.update(line_plan.slot_shift)
        combined.glyph_shift.update(line_plan.glyph_shift)
        combined.granted |= line_plan.granted
    return combined
