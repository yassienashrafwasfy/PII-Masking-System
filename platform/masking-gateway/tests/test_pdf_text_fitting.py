#!/usr/bin/env python3
"""Pin the order the typographic levers apply in, and the floors they stop at.

The order is the whole point of the module, and it is not arbitrary: word
spacing is invisible, tracking is nearly so, glyph scaling is visible at 3%
and uniform scaling is honest. Applying them in any other order costs
readability sooner than it needs to. Nothing but a test says which order the
code actually runs in -- the output is four numbers, and a wrong order still
produces four plausible numbers that add up to the right width.

The floors matter the same way. `MIN_SIZE_SCALE` is what stops a name being
drawn at a fifth of its width, which is what plain mode did before this
module existed; a regression there is a document a clinician cannot read,
and it would not fail any other test in the suite.
"""

from __future__ import annotations

import sys
from typing import Any
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine.app.services import pdf_text_fitting as fitting

# One ordinary run to vary: 20 characters with 3 spaces, set at 10pt in a
# font whose space is 2.5pt wide, measuring 100pt in total.
NATURAL = 100.0
SPACES = 3
GLYPHS = 20
SIZE = 10.0
SPACE = 2.5


def plan(available: float, **kwargs) -> fitting.Fit:
    fields: dict[str, Any] = {
        "natural": NATURAL,
        "available": available,
        "spaces": SPACES,
        "glyphs": GLYPHS,
        "size": SIZE,
        "space_width": SPACE,
    }
    fields.update(kwargs)
    return fitting.plan(**fields)


def drawn(fit: fitting.Fit, **kwargs) -> float:
    return fitting.width(
        fit,
        kwargs.get("natural", NATURAL),
        kwargs.get("spaces", SPACES),
        kwargs.get("glyphs", GLYPHS),
    )


# How much each lever can give on the run above, in points. Kept as
# arithmetic rather than literals so a change to a constant moves the
# expectations with it and only the behaviour under test can fail.
WORD_ROOM = SPACES * SPACE * (1.0 - fitting.MIN_WORD_SCALE)
TRACK_ROOM = (GLYPHS - 1) * SIZE * fitting.MAX_TRACKING
# The scaling levers apply to what the tightening levers left, not to the
# natural width -- see `width`.
TIGHT = NATURAL - WORD_ROOM - TRACK_ROOM
GLYPH_ROOM = TIGHT * (1.0 - fitting.MIN_GLYPH_SCALE)
SIZE_ROOM = (TIGHT - GLYPH_ROOM) * (1.0 - fitting.MIN_SIZE_SCALE)


def test_a_run_that_already_fits_is_left_completely_alone() -> None:
    """The overwhelming majority of replacements, and the ones a reader would
    notice being touched: they sit on a line of undisturbed text."""
    assert plan(NATURAL + 1.0) is fitting.NEUTRAL
    assert plan(NATURAL) is fitting.NEUTRAL
    assert fitting.NEUTRAL.plain


@pytest.mark.parametrize("natural,available", [(0.0, 50.0), (50.0, 0.0), (-1.0, 50.0)])
def test_a_degenerate_measurement_asks_for_nothing(natural, available) -> None:
    """An empty replacement and a zero-width slot both reach here from real
    documents -- a run clipped to the page edge measures zero. Neither is a
    fitting problem, and dividing by either would be a crash."""
    assert plan(available, natural=natural) is fitting.NEUTRAL


def test_the_smallest_overflow_comes_out_of_the_spaces_only() -> None:
    """Word spacing first, because it is the lever nobody sees."""
    fit = plan(NATURAL - WORD_ROOM / 2)
    assert fit.word_gap == pytest.approx(WORD_ROOM / 2 / SPACES)
    assert (fit.tracking, fit.size_scale, fit.glyph_scale) == (0.0, 1.0, 1.0)
    assert fit.undistorted


def test_spaces_are_never_squeezed_past_the_justification_minimum() -> None:
    """85% is InDesign's floor; past it the words start running together and
    the line reads as damaged rather than as tight."""
    fit = plan(NATURAL - WORD_ROOM * 3)
    tightest = SPACE * fitting.MIN_WORD_SCALE
    assert SPACE - fit.word_gap == pytest.approx(tightest)


def test_tracking_only_starts_once_the_spaces_are_spent() -> None:
    fit = plan(NATURAL - WORD_ROOM - TRACK_ROOM / 2)
    assert fit.word_gap == pytest.approx(WORD_ROOM / SPACES)
    assert fit.tracking == pytest.approx(TRACK_ROOM / 2 / (GLYPHS - 1))
    assert (fit.size_scale, fit.glyph_scale) == (1.0, 1.0)


def test_letters_are_never_pulled_closer_than_three_percent_of_the_em() -> None:
    fit = plan(NATURAL - WORD_ROOM - TRACK_ROOM * 3)
    assert fit.tracking <= SIZE * fitting.MAX_TRACKING + 1e-9


def test_glyph_scaling_is_the_third_lever_and_stops_at_ninety_seven() -> None:
    """3% is chosen because it is the point nobody can see. The run is still
    reported as undistorted there, and that is the claim being pinned."""
    fit = plan(NATURAL - WORD_ROOM - TRACK_ROOM - GLYPH_ROOM)
    assert fit.glyph_scale == pytest.approx(fitting.MIN_GLYPH_SCALE)
    assert fit.size_scale == 1.0
    assert fit.undistorted


def test_the_whole_run_shrinks_before_the_letterforms_are_distorted() -> None:
    """The lever this module was written for. A name a fifth smaller is still
    a name; a name a fifth as wide is a smear, and the old code drew one."""
    fit = plan(NATURAL - WORD_ROOM - TRACK_ROOM - GLYPH_ROOM - SIZE_ROOM / 2)
    assert fit.size_scale < 1.0
    assert fit.glyph_scale == pytest.approx(fitting.MIN_GLYPH_SCALE)
    assert fit.undistorted


def test_a_uniform_shrink_stops_at_four_fifths_of_the_size() -> None:
    fit = plan(1.0)
    assert fit.size_scale == pytest.approx(fitting.MIN_SIZE_SCALE)


def test_a_value_wider_than_its_line_is_distorted_only_as_a_last_resort() -> None:
    """A URL that took three lines in the source, or a name where the source
    held one initial, genuinely cannot fit. Drawing it narrow is worse than
    drawing it small and better than drawing it over the next word."""
    fit = plan(20.0)
    assert not fit.undistorted
    assert fit.size_scale == pytest.approx(fitting.MIN_SIZE_SCALE)
    assert drawn(fit) == pytest.approx(20.0)


def test_a_slot_of_nothing_asks_for_no_width_rather_than_a_negative_one() -> None:
    """`available` is clamped at zero by the caller, but the arithmetic that
    divides the shortfall over the glyphs must not go through zero itself."""
    fit = plan(1e-9)
    assert fit.glyph_scale >= 0.0
    assert drawn(fit) >= 0.0


def test_a_run_with_no_spaces_skips_straight_to_tracking() -> None:
    """Most of what this module draws is one token: an e-mail, a national
    number, a URL. The first lever has nothing to work with there."""
    fit = plan(NATURAL - TRACK_ROOM / 2, spaces=0)
    assert fit.word_gap == 0.0
    assert fit.tracking > 0.0


def test_a_single_glyph_has_no_gap_to_take_anything_out_of() -> None:
    """One character has no interior gaps, so tracking cannot apply and the
    shortfall has to fall through to the scaling levers."""
    fit = plan(NATURAL / 2, spaces=0, glyphs=1)
    assert fit.tracking == 0.0
    assert fit.size_scale < 1.0


@pytest.mark.parametrize("shortfall", [0.5, 2.0, 5.0, 12.0, 30.0, 60.0, 95.0, 99.9])
@pytest.mark.parametrize("spaces", [0, 1, 3, 9])
@pytest.mark.parametrize("glyphs", [1, 2, 20, 80])
def test_the_drawn_width_never_exceeds_the_room_it_was_given(
    shortfall: float, spaces: int, glyphs: int
) -> None:
    """The invariant the whole module exists to hold. The corpus suite asserts
    that no two words on a baseline overlap, and every way of breaking that
    assertion from here shows up first as a run drawn wider than its slot --
    so it is worth checking directly, across every shape of run, rather than
    only through a rendered page."""
    available = NATURAL - shortfall
    fit = plan(available, spaces=spaces, glyphs=glyphs)
    assert drawn(fit, spaces=spaces, glyphs=glyphs) <= available + 1e-9


# The shortfall at which each lever runs out, cumulatively. A shortfall just
# past one of these leaves a residual of well under a point for the next
# lever to absorb, which is the case a `> 0.0` guard written as `> 1.0`
# quietly drops -- mutation testing found exactly that, three times.
RUNGS = (WORD_ROOM, WORD_ROOM + TRACK_ROOM, WORD_ROOM + TRACK_ROOM + GLYPH_ROOM)


@pytest.mark.parametrize(
    "shortfall",
    [0.5, 5.0, 30.0, 99.0] + [rung + 0.4 for rung in RUNGS],
)
def test_every_fit_takes_the_full_shortfall_and_no_more(shortfall: float) -> None:
    """A lever that stopped early would leave the run overflowing; one that
    overshot would leave a visible gap before the text that follows.

    The shortfalls just past each rung are the sharp cases: a fraction of a
    point has to be handed to the next lever down, and a guard that treats
    "some room still needed" as "at least a point still needed" leaves the
    run wider than its slot by that fraction."""
    available = NATURAL - shortfall
    assert drawn(plan(available)) == pytest.approx(available)


# Each rung, and the lever that has to absorb a fraction of a point landing
# just past it. The width alone does not pin this: a lever that skips its
# turn hands the residual to the next one down, and the run still comes out
# exactly as wide as its slot -- while being drawn smaller, or narrower, than
# the ladder's order says it should be. Six mutants lived in that gap.
_NEXT_LEVER = (
    (WORD_ROOM, "tracking", "glyph_scale"),
    (WORD_ROOM + TRACK_ROOM, "glyph_scale", "size_scale"),
    (WORD_ROOM + TRACK_ROOM + GLYPH_ROOM, "size_scale", None),
)


@pytest.mark.parametrize("rung,lever,untouched", _NEXT_LEVER, ids=lambda x: str(x))
def test_a_fraction_of_a_point_is_taken_by_the_next_lever_in_turn(
    rung: float, lever: str, untouched: str | None
) -> None:
    """The residual goes to the lever whose turn it is, not further down."""
    fit = plan(NATURAL - rung - 0.4)
    assert getattr(fit, lever) != getattr(fitting.NEUTRAL, lever)
    if untouched is not None:
        assert getattr(fit, untouched) == getattr(fitting.NEUTRAL, untouched)


def test_the_order_holds_for_a_run_smaller_than_a_point() -> None:
    """The same attribution on a run whose whole width is under a point.

    Worth its own case because every lever's room is a fraction of that
    width there, so a guard comparing one against a literal point -- rather
    than against zero -- skips its turn on exactly these runs. A one-glyph
    field in a small font, or a value clipped by the page edge, is this run.
    """
    fit = fitting.plan(1.0, 0.69, 0, 2, 10.0, SPACE)
    assert fit.glyph_scale < 1.0
    assert fit.size_scale == 1.0


def test_a_run_only_a_point_wide_is_fitted_like_any_other() -> None:
    """A one-character run in a small font measures well under a point, and a
    slot at the page edge can be narrower still. Nothing about the ladder
    depends on the run being big, and a guard that quietly treated a point as
    "too small to bother with" would leave these overflowing -- which is the
    page-edge case the corpus already draws."""
    fit = fitting.plan(1.0, 0.5, 0, 2, 10.0, SPACE)
    assert fitting.width(fit, 1.0, 0, 2) == pytest.approx(0.5)
    assert fit.glyph_scale > 0.0


def test_a_single_space_is_still_a_lever() -> None:
    """Two words is the commonest shape a replacement has -- a first name and
    a surname -- so the one space it holds is where the ladder starts."""
    fit = plan(NATURAL - 0.2, spaces=1)
    assert fit.word_gap > 0.0
    assert fit.tracking == 0.0


def test_two_glyphs_have_one_gap_and_that_gap_counts() -> None:
    """The smallest run tracking can apply to at all."""
    fit = plan(NATURAL - WORD_ROOM - 0.2, glyphs=2)
    assert fit.tracking > 0.0


def test_a_run_of_one_glyph_is_measured_as_having_no_gaps() -> None:
    """`width` has to agree with `plan` about how many gaps a run has, or the
    guarantee it exists to check is checking the wrong number. One glyph has
    no interior gap, so a tracking value must not be subtracted from it."""
    only = fitting.Fit(0.0, 1.0, 1.0, 1.0)
    assert fitting.width(only, 10.0, 0, 1) == pytest.approx(10.0)
    assert fitting.width(only, 10.0, 0, 0) == pytest.approx(10.0)


def test_the_levers_apply_strictly_in_order_as_the_slot_tightens() -> None:
    """Read down the table: each lever moves off its neutral only after the
    one above it has stopped moving. This is the property that a refactor
    reordering the branches would break while every width still added up."""
    seen: list[fitting.Fit] = [
        plan(available) for available in range(int(NATURAL), 0, -5)
    ]
    for lever, above in (
        ("tracking", "word_gap"),
        ("size_scale", "tracking"),
    ):
        first_below = next(
            index
            for index, fit in enumerate(seen)
            if getattr(fit, lever) != getattr(fitting.NEUTRAL, lever)
        )
        assert getattr(seen[first_below], above) != getattr(fitting.NEUTRAL, above)


@pytest.mark.parametrize("shortfall", [1.0, 20.0, 60.0, 95.0, 99.9])
def test_a_heavily_scaled_run_still_has_a_space_between_its_words(
    shortfall: float,
) -> None:
    """The defect this composition was written for, found by rendering rather
    than by reading: an organization name came out as one long word.

    The levers tighten the run at its natural size and the scales then apply
    to what is left, so a space ends up at `(space - word_gap) * scales`. Take
    the gap out the other way round -- after the scales -- and 15% of a
    full-size space is subtracted from a space already drawn at a quarter of
    its width, which closes it and then some. So the claim here is not about
    a floor on the gap; it is that the gap keeps its share of the run.
    """
    fit = plan(NATURAL - shortfall)
    scales = fit.size_scale * fit.glyph_scale
    space = (SPACE - fit.word_gap) * scales
    assert space >= SPACE * fitting.MIN_WORD_SCALE * scales - 1e-9
    assert space > 0.0


def test_letters_keep_their_share_of_the_run_however_hard_it_is_scaled() -> None:
    """The same claim for tracking. A gap taken out after the scaling would
    be a multiple of the advance it is subtracted from, and the letters would
    be drawn on top of one another."""
    fit = plan(1.0)
    scales = fit.size_scale * fit.glyph_scale
    per_glyph = NATURAL / GLYPHS
    assert (per_glyph - fit.tracking) * scales > 0.0


def test_a_fit_that_did_something_is_not_reported_as_plain() -> None:
    """`plain` is what routes a run to the single fast `insert_text` call, so
    a false positive there silently draws the run at its natural width."""
    assert not plan(NATURAL - 1.0).plain
    assert not fitting.Fit(0.0, 0.0, 1.0, 0.9).plain
    assert not fitting.Fit(0.0, 0.5, 1.0, 1.0).plain


if __name__ == "__main__":  # pragma: no cover - the README runs these as scripts
    raise SystemExit(pytest.main([__file__, "-q"]))
