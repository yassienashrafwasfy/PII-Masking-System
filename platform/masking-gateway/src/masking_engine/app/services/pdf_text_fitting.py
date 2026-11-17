"""Decide how to draw a replacement that is wider than the room it has.

`pdf_line_reflow` opens the line up first, and on most pages that is the whole
answer. This module is what happens when the line had nothing left to give:
a surrogate still wider than its slot has to be made narrower somehow, and
*how* is a typographic question with a well-established answer.

The answer this module did not use, and the reason it exists: squeezing the
glyphs horizontally. Scaling x and not y is anamorphic distortion -- the
letterforms stop being the typeface and start being a smear, and the eye
catches it instantly next to undistorted text on the same line. Measured
across the corpus before this was written, plain mode drew replacements at
horizontal scales down to **0.194**: a name at a fifth of its proper width,
in a clinical document, where the reader has to be able to read it.

Professional typesetting reaches for the levers in a fixed order, and Adobe
publishes the limits it holds them to when justifying a line. Those limits
are the constants below. Each lever takes only what it needs and hands the
rest on:

  1. **Word spacing** -- the spaces between words carry the most give and
     show it the least. InDesign's justification minimum is 85%.
  2. **Tracking** -- the space between letters. Adobe caps this at 3% of an
     em, past which it costs readability rather than buying room.
  3. **Glyph scaling**, to 97%. The recommended InDesign setting is
     97/100/103 exactly because at 3% nobody sees it.
  4. **Uniform scaling** of the whole run, to 80%. Past the 97% floor the
     distortion becomes visible, and *smaller text is not distorted text*:
     a name set a fifth smaller reads as a name, where a name a fifth as
     wide does not read at all. This is also what PyMuPDF's own
     `insert_htmlbox` does when content overflows -- it scales down, and it
     scales both axes.
  5. Whatever is still missing, anamorphically, as the last resort it always
     should have been. A value genuinely wider than its line -- a URL that
     took three lines, a name where the source held one initial -- can reach
     here, and drawing it narrow beats drawing it over the next word.

The width arithmetic composes so that the drawn run is never wider than the
room it was given, whichever levers were used. That matters beyond looks:
the corpus suite asserts no two words on a baseline overlap, and an overflow
here is what that assertion would catch.

Geometry only. Nothing here opens a document, and every number in and out is
in points.
"""

from __future__ import annotations

from typing import NamedTuple

# The share of a space a line may take back before the gaps read as too
# tight. InDesign's justification default is 85% minimum word spacing.
MIN_WORD_SCALE = 0.85

# How far letters may be pulled together, as a fraction of the em. Adobe's
# guidance is to stay inside 3%; past it, readability goes before room does.
MAX_TRACKING = 0.03

# The floor on scaling glyph widths alone. 97% is the recommended InDesign
# minimum, and the point of it is that 3% is invisible.
MIN_GLYPH_SCALE = 0.97

# The floor on scaling the whole run, both axes. A fifth smaller is plainly
# smaller and still perfectly legible; it is the last lever that keeps the
# letterforms intact, so it is given the widest range of the four.
MIN_SIZE_SCALE = 0.80


class Fit(NamedTuple):
    """How to draw one replacement: the levers, in the order they applied.

    `word_gap` and `tracking` are points to *remove* per space and per gap
    between glyphs. `size_scale` multiplies the font size. `glyph_scale`
    multiplies advances only -- it is the anamorphic one, and it is 1.0
    unless everything else was spent.
    """

    word_gap: float
    tracking: float
    size_scale: float
    glyph_scale: float

    @property
    def undistorted(self) -> bool:
        """True when the run keeps the typeface's own proportions."""
        return self.glyph_scale >= MIN_GLYPH_SCALE

    @property
    def plain(self) -> bool:
        """True when nothing had to be done at all: draw it as it comes."""
        return (
            self.word_gap == 0.0
            and self.tracking == 0.0
            and self.size_scale == 1.0
            and self.glyph_scale == 1.0
        )


NEUTRAL = Fit(0.0, 0.0, 1.0, 1.0)


def plan(
    natural: float,
    available: float,
    spaces: int,
    glyphs: int,
    size: float,
    space_width: float,
) -> Fit:
    """Return the levers that fit *natural* points of text into *available*.

    *spaces* is how many spaces the text holds and *glyphs* how many
    characters; both decide how much give the first two levers have. *size*
    is the font size the run is drawn at and *space_width* the width of one
    space in that font at that size.

    Fits that need nothing return `NEUTRAL`, which is the overwhelming
    majority once the line reflow has run -- 123 of 129 across the corpus.

    Two invariants hold from the first lever on, and they are worth stating
    because several of the guards below look like dead defensiveness without
    them:

    * every lever takes at most the room still needed, so `remaining` walks
      down to exactly zero and never below it;
    * each lever therefore leaves `tightened >= available`, and *available*
      is positive, so nothing the scaling levers divide by can reach zero.

    Mutation testing makes the same point from the other side: loosening
    `> 0.0` to `>= 0.0` on any of those guards produces a mutant that cannot
    be killed, because at the only value that distinguishes them the lever
    takes nothing and returns the identity. They are kept as `> 0.0` because
    they say what the code means -- do this only if there is still room to
    find -- not because the boundary itself is load-bearing.
    """
    if natural <= 0.0 or available <= 0.0 or natural <= available:
        return NEUTRAL

    remaining = natural - available
    word_gap = tracking = 0.0
    size_scale = glyph_scale = 1.0

    # What the first two levers leave to scale. They tighten the run at its
    # own size, and the scaling levers then apply to the tightened result --
    # the other order collapses the spaces: 15% of a full-size space taken
    # out of a space already drawn at a quarter of its width closes the gap
    # completely, and the words run together. That is what the corpus render
    # showed, and it is the reason the composition is written out here.
    tightened = natural

    if spaces > 0:
        take = min(remaining, spaces * space_width * (1.0 - MIN_WORD_SCALE))
        word_gap = take / spaces
        tightened -= take
        remaining -= take

    gaps = max(glyphs - 1, 0)
    if remaining > 0.0 and gaps > 0:
        take = min(remaining, gaps * size * MAX_TRACKING)
        tracking = take / gaps
        tightened -= take
        remaining -= take

    if remaining > 0.0 and tightened > 0.0:
        take = min(remaining, tightened * (1.0 - MIN_GLYPH_SCALE))
        glyph_scale = (tightened - take) / tightened
        remaining -= take

    if remaining > 0.0:
        drawn = tightened * glyph_scale
        take = min(remaining, drawn * (1.0 - MIN_SIZE_SCALE))
        if drawn > 0.0:
            size_scale = (drawn - take) / drawn
        remaining -= take

    if remaining > 0.0:
        # The last resort. Whatever is still missing comes out of the glyph
        # widths, however much that is -- the alternative is drawing over
        # the word that follows, and a distorted name is recoverable where
        # two names on top of each other are not.
        drawn = tightened * glyph_scale * size_scale
        glyph_scale *= max(0.0, (drawn - remaining) / drawn) if drawn > 0.0 else 0.0

    return Fit(word_gap, tracking, size_scale, glyph_scale)


def width(
    fit: Fit,
    natural: float,
    spaces: int,
    glyphs: int,
) -> float:
    """The width *fit* actually draws, for a run whose natural width is given.

    The inverse of `plan`, and the reason both are here: the guarantee that
    matters is that this never exceeds what `plan` was given, and a guarantee
    nothing computes is a guarantee nothing tests. Read it as the two stages
    the ladder actually has -- tighten the run at its own size, then scale
    what is left of it.
    """
    gaps = max(glyphs - 1, 0)
    tightened = natural - fit.tracking * gaps - fit.word_gap * spaces
    return tightened * fit.size_scale * fit.glyph_scale
