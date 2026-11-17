"""Render shared-pipeline PDF spans with their original source geometry."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from io import BytesIO
from typing import NamedTuple, Sequence

import pymupdf

from ..core.error_codes import CorruptInputError
from ..core.schemas import LocaleBadge, MaskSpan
from ..engines.masking_router import MaskingRouter
from ..engines.regex_arbitration import coalesce_spans
from . import (
    pdf_block_reflow,
    pdf_content_order,
    pdf_line_reflow,
    pdf_redaction_styles,
    pdf_text_fitting,
)
from .pdf_extraction import PdfGlyph

log = logging.getLogger(__name__)

# pymupdf's bundled type stubs omit the redaction flags, which exist at run time.
_REDACT_IMAGE_NONE: int = pymupdf.PDF_REDACT_IMAGE_NONE  # type: ignore[attr-defined]
_REDACT_IMAGE_PIXELS: int = pymupdf.PDF_REDACT_IMAGE_PIXELS  # type: ignore[attr-defined]
_REDACT_LINE_ART_NONE: int = pymupdf.PDF_REDACT_LINE_ART_NONE  # type: ignore[attr-defined]
_REDACT_TEXT_REMOVE: int = pymupdf.PDF_REDACT_TEXT_REMOVE  # type: ignore[attr-defined]


def _present(glyphs: Sequence[PdfGlyph | None], index: int) -> PdfGlyph:
    """Return the glyph at *index*, which the caller has already established exists."""
    glyph = glyphs[index]
    if glyph is None:
        raise ValueError(f"no source glyph at index {index}")
    return glyph


@dataclass(frozen=True)
class GlyphRun:
    """Adjacent source glyphs that can share one replacement rectangle."""

    rect: pymupdf.Rect
    style: PdfGlyph
    length: int


@dataclass(frozen=True)
class ResolvedFont:
    """A page font resource and matching metrics."""

    name: str
    metrics: pymupdf.Font


def _same_line(left: PdfGlyph, right: PdfGlyph) -> bool:
    return (
        left.direction == right.direction
        and abs(left.bbox[1] - right.bbox[1]) <= 1.0
        and abs(left.bbox[3] - right.bbox[3]) <= 1.0
    )


def _vertically_overlaps(
    bbox: tuple[float, float, float, float], rect: pymupdf.Rect
) -> bool:
    """Return whether a glyph's y-range shares any space with *rect*'s."""
    return bbox[1] < rect.y1 and bbox[3] > rect.y0


def _safe_expand(
    run: GlyphRun,
    source_glyphs: Sequence[PdfGlyph | None],
    page,
    ink_bleed: float = 0.0,
) -> float:
    """Return how far right *run*'s box may grow without covering real content.

    *ink_bleed* is how far the caller's chrome paints outside the rectangle it
    is given, and it is reserved from the boundary rather than assumed to be
    zero. A stroked border is centred on the edge, so a box grown to exactly
    the bounding glyph's left edge still puts half a border's worth of ink on
    that glyph -- which is what drew a chip's own border over the "j" of the
    word after it. Reserved only against a real glyph: the end-of-line
    fallback below bounds nothing, so there is nothing there to protect.

    Scans every glyph on the page (not just this span's) for the nearest
    one that sits on the same visual line and to the right of the run —
    that glyph's original left edge is the hard boundary. A run's own
    remaining glyphs can never qualify (`run.rect.x1` is already the
    rightmost edge of every glyph inside the run), a wrapped span's other
    lines fail the vertical-overlap check automatically, and a
    same-line neighboring span's original glyphs correctly still bound the
    expansion, because that span's own box will occupy roughly that space
    too. When nothing bounds it (end of line/page), fall back to a
    conservative page-margin heuristic.

    The comparison is `>=`, not `>`: a text layer sets glyphs edge to edge,
    so the character immediately after a span starts at exactly the run's
    right edge. Under `>` that character was never a boundary, and the box
    grew past it to whatever came next -- which is how a chip ended up
    drawn straight over the full stop closing its own sentence.

    Whitespace is skipped because it carries no ink. That is what keeps the
    stricter comparison from costing a long pseudonym its room: the glyph
    after a span is usually a space, and stopping at one would refuse an
    expansion that covers nothing.
    """
    blocking = [
        glyph.bbox[0]
        for glyph in source_glyphs
        if glyph is not None and glyph.text.strip()
        # Only the run's own layer bounds it. A full-page background scan puts
        # an OCR glyph over every text-layer line on the page, and without this
        # each of those would clamp the text layer's expansion to zero -- a
        # live regression on the visible styles, on a page nothing had to
        # change to hit.
        and glyph.ocr == run.style.ocr
        and _vertically_overlaps(glyph.bbox, run.rect)
        and glyph.bbox[0] >= run.rect.x1
    ]
    boundary = min(blocking) - ink_bleed if blocking else page.rect.width - 36
    return max(0.0, boundary - run.rect.x1)


def _runs_for_span(glyphs: Sequence[PdfGlyph | None], span: MaskSpan) -> list[GlyphRun]:
    if not 0 <= span.start < span.end <= len(glyphs):
        raise CorruptInputError("A PDF span is outside the extracted page text.")
    selected = [glyph for glyph in glyphs[span.start : span.end] if glyph is not None]
    if not selected:
        raise CorruptInputError("A PDF span has no source geometry.")

    runs: list[list[PdfGlyph]] = []
    for glyph in selected:
        if not runs or not _same_line(runs[-1][-1], glyph):
            runs.append([glyph])
        else:
            runs[-1].append(glyph)

    return [
        GlyphRun(
            pymupdf.Rect(
                min(glyph.bbox[0] for glyph in run),
                min(glyph.bbox[1] for glyph in run),
                max(glyph.bbox[2] for glyph in run),
                max(glyph.bbox[3] for glyph in run),
            ),
            run[0],
            len(run),
        )
        for run in runs
    ]


def _covers(metrics: pymupdf.Font, text: str) -> bool:
    """Whether *metrics* can map every character *text* needs.

    A PDF may embed a *subset* of a font -- only the glyphs its own words
    used -- and the subsetter is free to drop the character map with them,
    leaving a face addressable by glyph index and by nothing else. Asked to
    write a surrogate through such a face, `insert_text` maps every character
    to glyph 0: the page renders a row of `.notdef` boxes and the text layer
    extracts as NUL. A whole footer set in `AAAAAA+Consolas` left the pipeline
    that way, and nothing upstream could see it, because the font resolved,
    registered, and drew without raising.

    Arial escaped it only by accident -- the built-in table above catches the
    name before the embedded copy is ever considered -- so the check belongs
    here rather than in another row of that table.

    Spacing is normally ignored: plenty of subsets carry no space glyph at all
    because their document spaced words with TJ offsets, and rejecting such a
    face would swap out the whole word for a fallback over a glyph nothing
    draws. A run that is *only* whitespace has nothing else to judge, though,
    and a reflowed line hands those over one glyph at a time -- so there the
    space itself is the character that has to map, and a footer came back with
    two `.notdef` boxes where its two moved spaces had been.
    """
    needed = [char for char in text if not char.isspace()] or list(text)
    return all(metrics.has_glyph(ord(char)) for char in needed)


def _builtin_like(normalized: str, flags: int) -> str:
    """Return the base-14 font closest to the source face named *normalized*.

    Reached when the embedded face cannot write the text, so the choice is
    between a readable near-match and unreadable boxes. Width and rhythm are
    what a reader notices at this size, so a monospaced source stays
    monospaced and a serif stays serifed; the weight and slope come from the
    glyph's own flags rather than from the name, which a subset prefix and a
    vendor suffix both obscure.
    """
    if any(key in normalized for key in ("mono", "consol", "courier", "menlo")):
        family = ("cour", "cobo", "coit", "cobi")
    elif any(
        key in normalized
        for key in ("times", "serif", "roman", "georgia", "garamond", "book")
    ):
        family = ("tiro", "tibo", "tiit", "tibi")
    else:
        family = ("helv", "hebo", "heit", "hebi")
    return family[(1 if flags & 16 else 0) | (2 if flags & 2 else 0)]


def _font_name(
    page,
    document,
    style: PdfGlyph,
    fonts: dict[tuple[int, str], ResolvedFont],
    text: str,
) -> ResolvedFont:
    """Reuse an embedded source font when it can be registered safely.

    *text* is what the caller is about to draw through the returned font, and
    is required: an embedded face that cannot map it is passed over (see
    `_covers`), and a caller that had nothing to declare here would silently
    opt out of that check for whatever it then drew.
    """
    normalized = re.sub(r"^[A-Z]{6}\+", "", style.font).casefold()
    builtin = (
        ("helvetica-boldoblique", "hebi"),
        ("helvetica-oblique", "heit"),
        ("helvetica-bold", "hebo"),
        ("arial-bolditalic", "hebi"),
        ("arial-italic", "heit"),
        ("arial-bold", "hebo"),
        ("times-bolditalic", "tibi"),
        ("times-italic", "tiit"),
        ("times-bold", "tibo"),
        ("courier-boldoblique", "cobi"),
        ("courier-oblique", "coit"),
        ("courier-bold", "cobo"),
        ("helvetica", "helv"),
        ("arial", "helv"),
        ("times", "tiro"),
        ("courier", "cour"),
    )
    for name, replacement in builtin:
        if name in normalized:
            return ResolvedFont(replacement, pymupdf.Font(fontname=replacement))

    wanted_bold = bool(style.flags & 16)
    wanted_italic = bool(style.flags & 2)
    matches = []
    for font in page.get_fonts(full=True):
        xref, base_name, resource_name = int(font[0]), str(font[3]), str(font[4])
        candidates = (base_name, resource_name)
        if not any(normalized in value.casefold() for value in candidates):
            continue
        key = (xref, resource_name)
        if key in fonts:
            metrics = fonts[key].metrics
        else:
            extracted = document.extract_font(xref)
            buffer = extracted[3] if extracted else b""
            if not buffer:
                continue
            try:
                metrics = pymupdf.Font(fontbuffer=buffer)
                registered = f"maskfont{xref}"
                page.insert_font(fontname=registered, fontbuffer=buffer)
            except Exception:  # noqa: BLE001 - try another matching resource
                continue
            fonts[key] = ResolvedFont(registered, metrics)
        if not _covers(metrics, text):
            continue
        flags = metrics.flags
        mismatch = int(bool(flags.get("bold")) != wanted_bold) + int(
            bool(flags.get("italic")) != wanted_italic
        )
        matches.append((mismatch, fonts[key]))
    if matches:
        return min(matches, key=lambda item: item[0])[1]
    fallback = _builtin_like(normalized, style.flags)
    return ResolvedFont(fallback, pymupdf.Font(fontname=fallback))


def _color(value: int) -> tuple[float, float, float]:
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


def _rotation(direction: tuple[float, float]) -> int:
    dx, dy = direction
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 90 if dy < 0 else 270


def _insert_replacement(
    page,
    rect: pymupdf.Rect,
    text: str,
    font: ResolvedFont,
    style: PdfGlyph,
    shift: float = 0.0,
    granted_width: float = 0.0,
    lift: float = 0.0,
) -> str:
    """Render a complete replacement in the exact source slot and style.

    A replacement wider than the room it has is condensed into it, never
    swapped for something else. The swap that used to happen here reached for
    `compact_replacement`, whose fallback for most labels is a random-letter
    string of the same shape -- so a name too long for its slot left the
    document as "Pqj Ekwless" and an e-mail as "vfei.u@jeaauqw.bg". That
    contradicts this module's whole premise, which is that a reader cannot
    tell a released value is a surrogate; `_render_styled_run` had already
    reasoned the same way for the visible styles and refused to use it.

    *shift* and *granted_width* come from `pdf_line_reflow` by way of
    `_reflow_plan`: how far right the line moved this slot, and the width the
    line made room for. Condensing is the answer only once that has been
    tried and the line had nothing to give -- a surrogate half again as long
    as the value it replaced is condensed to two thirds of its natural width
    and reads as crushed, which is what the reflow exists to avoid. Both zero
    means no reflow applied and the slot is the source run's own, exactly as
    before.
    """
    natural = font.metrics.text_length(text, fontsize=style.size)
    available = granted_width or rect.width
    fit = pdf_text_fitting.plan(
        natural,
        available,
        text.count(" "),
        len(text),
        style.size,
        font.metrics.text_length(" ", fontsize=style.size),
    )
    point = pymupdf.Point(style.origin[0] + shift, style.origin[1] - lift)
    try:
        _draw_fitted(page, point, text, font, style, fit)
        return text
    except Exception as exc:
        raise CorruptInputError("A PDF replacement could not be rendered.") from exc


def _draw_fitted(
    page,
    point: pymupdf.Point,
    text: str,
    font: ResolvedFont,
    style: PdfGlyph,
    fit: pdf_text_fitting.Fit,
) -> None:
    """Draw *text* at *point* under the levers *fit* chose.

    The common case by far is `NEUTRAL`, and it is drawn by the one call this
    function has always made -- a run that needs nothing must not pay for the
    machinery that exists for one that does, and must not come out looking
    even slightly different.

    Once a gap has to be closed the glyphs no longer sit at the advances the
    font gives them, so they are placed one piece at a time. The pieces are
    words while only word spacing is in play, and characters once tracking
    is, which keeps a page of ordinary replacements from turning into a page
    of single-character text objects.
    """
    size = style.size * fit.size_scale
    colour = _color(style.color)
    rotate = _rotation(style.direction)
    if fit.plain:
        page.insert_text(
            point,
            text,
            fontname=font.name,
            fontsize=size,
            color=colour,
            rotate=rotate,
            overlay=True,
        )
        return

    morph = (
        None
        if fit.glyph_scale == 1.0
        else (point, pymupdf.Matrix(fit.glyph_scale, 1.0))
    )
    if fit.tracking == 0.0 and fit.word_gap == 0.0:
        page.insert_text(
            point,
            text,
            fontname=font.name,
            fontsize=size,
            color=colour,
            rotate=rotate,
            morph=morph,
            overlay=True,
        )
        return

    pieces = list(text) if fit.tracking else _words_with_spaces(text)
    # `fit` tightens the run at its natural size and scales what is left, so
    # the gaps it closes are measured there too and have to be brought into
    # the drawn size before they can be walked along the baseline.
    shrink = fit.size_scale * fit.glyph_scale
    x = point.x
    for piece in pieces:
        if piece.strip():
            page.insert_text(
                pymupdf.Point(x, point.y),
                piece,
                fontname=font.name,
                fontsize=size,
                color=colour,
                rotate=rotate,
                morph=(
                    (pymupdf.Point(x, point.y), pymupdf.Matrix(fit.glyph_scale, 1.0))
                    if morph is not None
                    else None
                ),
                overlay=True,
            )
        advance = font.metrics.text_length(piece, fontsize=size) * fit.glyph_scale
        x += advance - fit.tracking * len(piece) * shrink
        if piece == " ":
            x -= fit.word_gap * shrink


def _words_with_spaces(text: str) -> list[str]:
    """*text* split so every space is its own piece, order preserved."""
    pieces: list[str] = []
    for index, word in enumerate(text.split(" ")):
        if index:
            pieces.append(" ")
        if word:
            pieces.append(word)
    return pieces


class _ChipText(NamedTuple):
    """What a chip wants to say, and what it settles for in a tight box."""

    preferred: str
    fallback: str
    strict_first: bool


def _chip_text(
    style: pdf_redaction_styles.EntityStyle,
    replacement: str,
    confidence: float | None,
    redaction_mode: str,
) -> _ChipText:
    """Return what a chip draws, decided before any box has been fitted.

    Separate from the drawing because the line planner has to know how wide
    the chip wants to be before anything is rendered, and the two must not be
    able to disagree about what the answer is.

    In `detect`, the score is dropped rather than truncated when both do not
    fit. Fitting the pair as one unit made a narrow date box read "DATE 1..."
    -- a truncated percentage is a different number, and a reader has no way
    to tell 100% from 12% from a digit and a mark. The abbreviation is what
    detect mode is for; the score is the part that can be given up. A regex
    match (BTW/RIZIV/INSZ ids, EMAIL, PHONE_NUMBER, DATE, URL) carries no
    score at all and keeps the bare abbreviation.

    In `pseudonymize` the chip carries the whole pseudonym and nothing
    else -- one chip per span, wrapped or not. The shape-preserving
    alternative `compact_replacement` is deliberately not offered here: for
    labels outside `_COMPACT_PSEUDONYMIZE_LABELS` (NAME chief among them) it
    is `_redraw`'s random-letter fallback, meant to blend invisibly into
    paragraph text, not to be read inside a clearly-labeled box ("Jens
    Meert" -> "Qkpz Wmyjv" is worse than a truncated real name).
    """
    if redaction_mode == "detect":
        if confidence is None:
            return _ChipText(style.abbreviation, style.abbreviation, False)
        return _ChipText(
            f"{style.abbreviation} {round(confidence * 100)}%",
            style.abbreviation,
            True,
        )
    return _ChipText(replacement, replacement, True)


def _chip_max_size(run: GlyphRun) -> float:
    """Return the largest type a chip over *run* should be set in.

    `fit_text`'s 8.5pt default is a readability ceiling chosen for a page set
    in 9 to 11 point. It is not a ceiling for the *box*, and on a document
    whose type runs larger it makes every chip look like a footnote: a scanned
    page rendered at 1861pt wide has ~30pt lines, and an 8.5pt chip inside a
    40pt box is unreadable at the scale the rest of the page is read at.

    Taken from the run's height rather than from `style.size`, which is not
    the stable quantity it looks like. A glyph written under a horizontal
    stretch reports `fontsize * sqrt(stretch)` -- measured: 20pt text comes
    back as 15.5 at 0.6x and 25.3 at 1.6x. The OCR text layer stretches every
    word individually to sit over its own ink, so reading `size` gave a
    different answer for each word of a line and the chips along one paragraph
    came out visibly different sizes. The box height is invariant to that
    stretch, and it is what the chip has to fit anyway.
    """
    height = run.rect.height / pdf_redaction_styles.line_height("helv")
    return max(pdf_redaction_styles.CHIP_MAX_SIZE, height)


def _render_styled_run(
    page,
    document,
    fonts: dict[tuple[int, str], ResolvedFont],
    run: GlyphRun,
    replacement: str,
    source_glyphs: Sequence[PdfGlyph | None],
    label: str,
    confidence: float | None,
    redaction_style: str,
    redaction_mode: str,
    shift: float = 0.0,
    granted_width: float = 0.0,
    lift: float = 0.0,
) -> str:
    """Draw one run as a visible redaction box and return the text drawn.

    *shift* and *granted_width* come from `pdf_line_reflow`: how far right the
    line moved this box, and the width the line made room for. Both zero means
    no reflow applied, and the box is fitted into the space its neighbours
    left it, exactly as before.

    The returned text is what `router.record()` sees as `replaced_text` —
    the audit trail must reflect what actually left the pipeline, not a
    pseudonym that was computed but never rendered (relevant for
    `redaction_mode="detect"`, which never draws a pseudonym at all).
    """
    if _rotation(run.style.direction) != 0:
        # v1 doesn't support rotated/vertical source text. Degrade to the
        # existing invisible-substitution rendering rather than draw
        # something broken.
        font = _font_name(page, document, run.style, fonts, replacement)
        return _insert_replacement(page, run.rect, replacement, font, run.style)

    style = pdf_redaction_styles.resolve_style(label)
    rect = run.rect
    if shift or lift:
        rect = pymupdf.Rect(
            rect.x0 + shift, rect.y0 - lift, rect.x1 + shift, rect.y1 - lift
        )
    if granted_width:
        # The line was reflowed to hold exactly this, so the box takes the
        # room it was given instead of squeezing into whatever its neighbour
        # happened to leave: the planner has already moved that neighbour.
        expand = max(0.0, granted_width - rect.width)
    else:
        expand = _safe_expand(
            run,
            source_glyphs,
            page,
            pdf_redaction_styles.ink_bleed(redaction_style),
        )

    ceiling = _chip_max_size(run)
    chip = _chip_text(style, replacement, confidence, redaction_mode)
    fitted = None
    if chip.strict_first:
        fitted = pdf_redaction_styles.fit_text(
            rect, chip.preferred, expand, max_size=ceiling, allow_truncate=False
        )
    if fitted is None:
        fitted = pdf_redaction_styles.fit_text(
            rect, chip.fallback, expand, max_size=ceiling
        )

    if fitted is None:
        raise ValueError("chip text does not fit its rectangle even when truncated")
    fitted_rect, size, text = fitted
    pdf_redaction_styles.draw_box(
        page,
        fitted_rect,
        style,
        redaction_mode,
        text,
        size,
        style_variant=redaction_style,
    )
    return text


class _PendingRun(NamedTuple):
    """One replacement to draw: a layout slot and the text that goes in it."""

    span: MaskSpan
    original: str
    run: GlyphRun
    replacement: str


def _validate_redaction_options(redaction_style: str, redaction_mode: str) -> None:
    """Reject an unknown style or mode before any page is opened."""
    if (
        redaction_style != "invisible"
        and redaction_style not in pdf_redaction_styles.STYLE_VARIANTS
    ):
        raise ValueError(
            "redaction_style must be invisible or one of: "
            + ", ".join(pdf_redaction_styles.STYLE_VARIANTS)
        )
    if redaction_mode not in ("pseudonymize", "detect"):
        raise ValueError("redaction_mode must be pseudonymize or detect")


# How far in from the page edge a reflowed line may still write. Matches the
# end-of-line fallback `_safe_expand` has always used, so a chip that grows
# and a line that grows agree about where the page stops.
_PAGE_MARGIN = 36.0


class _Reflow(NamedTuple):
    """A line plan plus the widths that were asked for, keyed by pending index.

    The widths live here rather than inside the plan because the planner does
    not own them -- it is handed them. Keeping the pair together is what stops
    the renderer from measuring the chip a second time and disagreeing with
    what the line was laid out for.
    """

    plan: pdf_line_reflow.Plan
    wanted: dict[int, float]
    # How far up a glyph, and a slot, moves because a line above it was
    # emptied. See `_lift_plan` and `pdf_block_reflow`.
    glyph_lift: dict[int, float]
    slot_lift: dict[int, float]

    def shift(self, index: int) -> float:
        return self.plan.slot_shift.get(index, 0.0)

    def lift(self, index: int) -> float:
        return self.slot_lift.get(index, 0.0)

    def granted_width(self, index: int) -> float:
        return self.wanted[index] if index in self.plan.granted else 0.0


_NO_REFLOW = _Reflow(pdf_line_reflow.Plan(), {}, {}, {})


def _reflow_plan(
    page,
    document,
    fonts: dict[tuple[int, str], ResolvedFont],
    pending: Sequence[_PendingRun],
    source_glyphs: Sequence[PdfGlyph | None],
    page_spans: Sequence[MaskSpan],
    redaction_style: str,
    redaction_mode: str,
) -> _Reflow:
    """Ask the line planner for room for every chip that wants more of it.

    Skipped wholesale on a page carrying an OCR layer. Reflow moves a glyph by
    erasing and redrawing it, and erasing an OCR glyph destroys the pixels
    underneath -- so on a scan it would take the image out from under the text
    it was moving.

    The width a slot asks for is measured the way the slot will be drawn.
    A chip is a box laid out by `pdf_redaction_styles`; an invisible
    replacement is the source glyphs' own font at their own size, so it is
    measured through that font -- the same call `_insert_replacement` makes to
    decide whether to condense. Measuring both the same way is what keeps the
    line from being opened to a width the renderer then disagrees with.
    """
    if any(glyph is not None and glyph.ocr for glyph in source_glyphs):
        return _NO_REFLOW

    wanted_by_index: dict[int, float] = {}
    slots: list[pdf_line_reflow.Slot] = []
    for index, item in enumerate(pending):
        run = item.run
        if _rotation(run.style.direction) != 0:
            continue
        if redaction_style == "invisible":
            font = _font_name(page, document, run.style, fonts, item.replacement)
            wanted = font.metrics.text_length(item.replacement, fontsize=run.style.size)
        else:
            style = pdf_redaction_styles.resolve_style(item.span.label)
            chip = _chip_text(
                style, item.replacement, item.span.confidence, redaction_mode
            )
            wanted = pdf_redaction_styles.natural_width(
                run.rect, chip.preferred, max_size=_chip_max_size(run)
            )
        wanted_by_index[index] = wanted
        slots.append(
            pdf_line_reflow.Slot(
                index, run.rect.x0, run.rect.x1, run.rect.y0, run.rect.y1, wanted
            )
        )

    covered: set[int] = set()
    for span in page_spans:
        covered.update(range(span.start, span.end))
    fixed = [
        pdf_line_reflow.Fixed(
            index, glyph.bbox[0], glyph.bbox[2], glyph.bbox[1], glyph.bbox[3]
        )
        for index, glyph in enumerate(source_glyphs)
        # Whitespace neither moves nor holds a line apart: the gap it stands
        # for is already the distance between the glyphs on either side, and
        # counting it as an item would make every word space look full.
        if glyph is not None and glyph.text.strip() and index not in covered
    ]
    line_plan = pdf_line_reflow.plan(
        slots,
        fixed,
        page.rect.x1 - _PAGE_MARGIN,
        pdf_redaction_styles.ink_bleed(redaction_style),
    )
    _carry_whitespace(source_glyphs, line_plan.glyph_shift)
    _extend_to_line_end(source_glyphs, covered, line_plan.glyph_shift)
    if line_plan.refused:
        # The one outcome the output cannot be inspected for: a condensed
        # pseudonym sits in its slot exactly like a fitting one, so a page
        # full of crushed replacements looks the same as a page of correct
        # ones to everything but a reader.
        log.info(
            "reflow_refused page=%d lines=%d slots=%d",
            page.number,
            line_plan.refused,
            len(slots),
        )
    glyph_lift, slot_lift = _lift_plan(
        pending, source_glyphs, covered, line_plan.glyph_shift
    )
    return _Reflow(line_plan, wanted_by_index, glyph_lift, slot_lift)


def _has_rotated_glyph(source_glyphs: Sequence[PdfGlyph | None]) -> bool:
    return any(
        glyph is not None and _rotation(glyph.direction) != 0 for glyph in source_glyphs
    )


def _block_lines(
    pending: Sequence[_PendingRun],
    source_glyphs: Sequence[PdfGlyph | None],
    covered: set[int],
) -> list[pdf_block_reflow.Line]:
    """One `Line` per baseline, blank when nothing on it survives or is drawn."""
    drawn_on = {item.run.style.origin[1] for item in pending}
    by_baseline: dict[float, list[int]] = {}
    for index, glyph in enumerate(source_glyphs):
        if glyph is not None and glyph.text.strip():
            by_baseline.setdefault(glyph.origin[1], []).append(index)

    return [
        pdf_block_reflow.Line(
            key=baseline,
            x0=min(_present(source_glyphs, index).bbox[0] for index in indices),
            x1=max(_present(source_glyphs, index).bbox[2] for index in indices),
            baseline=baseline,
            blank=baseline not in drawn_on
            and all(index in covered for index in indices),
        )
        for baseline, indices in by_baseline.items()
    ]


def _lift_plan(
    pending: Sequence[_PendingRun],
    source_glyphs: Sequence[PdfGlyph | None],
    covered: set[int],
    glyph_shift: dict[int, float],
) -> tuple[dict[int, float], dict[int, float]]:
    """Move the lines under an emptied one up, and say which glyphs moved.

    A value wide enough to be a whole line leaves that line with nothing on
    it: `_plan_page` draws one replacement per span, on the line the value
    ended on, so the lines the value covered end to end have neither source
    glyphs nor a replacement. `pdf_block_reflow` decides which lines may close
    that hole and by how much; this turns its answer into the two maps the
    renderer works from.

    A lifted glyph is added to *glyph_shift* even when it moves nowhere
    horizontally, because that map is also the list of glyphs to erase and
    redraw. Rotated text is left out entirely, for the same reason the line
    reflow leaves it out: v1 does not re-lay-out a rotated run, and moving one
    by its upright geometry would put it somewhere it never was.
    """
    if _has_rotated_glyph(source_glyphs):
        return {}, {}

    lines = _block_lines(pending, source_glyphs, covered)
    lifts = pdf_block_reflow.plan(lines)
    if not lifts:
        return {}, {}

    glyph_lift: dict[int, float] = {}
    for index, glyph in enumerate(source_glyphs):
        if glyph is None or not glyph.text or index in covered:
            continue
        lift = lifts.get(glyph.origin[1])
        if lift:
            glyph_lift[index] = lift
            glyph_shift.setdefault(index, 0.0)
    slot_lift = {
        index: lifts[item.run.style.origin[1]]
        for index, item in enumerate(pending)
        if item.run.style.origin[1] in lifts
    }
    return glyph_lift, slot_lift


def _extend_to_line_end(
    source_glyphs: Sequence[PdfGlyph | None],
    covered: set[int],
    glyph_shift: dict[int, float],
) -> None:
    """Redraw a reflowed line from its first moved glyph to its end.

    A shift decays to nothing part way along a line, so the tail beyond that
    point stayed in the page's own content while everything before it was
    erased and redrawn. That left the line as two fragments, and
    `pdf_content_order` -- which splices a chip in by splitting the text object
    it was redacted out of and resuming the remainder after it -- took the
    stranded tail for that remainder and moved it back to the chip's edge:
    "aan defecatie" was drawn at x 146 on top of "Begin van".

    Taking the whole tail leaves one fragment instead of two. A glyph whose
    shift is zero is redrawn exactly where it already was, so nothing moves
    that was not going to.

    It has to be the *whole* tail, measured from the first glyph the line
    moved rather than from the last. A line with two replacements on it can
    absorb the first one's growth before reaching the second, and the glyphs
    in that slack then belong to neither moved group: taken from the last one,
    they were left in the base content while `_moved_glyph_rects` -- which
    erases one rectangle per line, spanning every glyph the line moved --
    erased them anyway. A French conclusion line came back reading "Votre
    patiente de 60 ans a été vue en" and then 167pt of blank paper where
    "consultation de gastro-entérologie le" had been. Measured from the first,
    the glyphs a line redraws are contiguous, which is exactly what that one
    rectangle assumes.
    """
    if not glyph_shift:
        return
    reflowed = {
        _present(source_glyphs, index).origin[1]
        for index in glyph_shift
        if source_glyphs[index] is not None
    }
    opening = {
        baseline: min(
            _present(source_glyphs, index).bbox[0]
            for index in glyph_shift
            if source_glyphs[index] is not None
            and _present(source_glyphs, index).origin[1] == baseline
        )
        for baseline in reflowed
    }
    for index, glyph in enumerate(source_glyphs):
        if glyph is None or index in covered or index in glyph_shift:
            continue
        if glyph.origin[1] in reflowed and glyph.bbox[0] >= opening[glyph.origin[1]]:
            glyph_shift[index] = 0.0


def _carry_whitespace(
    source_glyphs: Sequence[PdfGlyph | None], glyph_shift: dict[int, float]
) -> None:
    """Move a space along with the word it follows.

    Whitespace is left out of the planning because the gap it stands for is
    already the distance between the glyphs on either side of it, and counting
    it as an item would make every word space look occupied. It still has to
    travel: a space that stays behind while the letters around it move ends up
    inside the word before it, and the masked page then extracts as
    "moretextrightafter" -- correct on screen, unreadable to anything that
    reads the text layer, which is most of what consumes a masked document.

    It takes the shift of the run it trails, so it starts where that run now
    ends; the following run's own shift decides the rest of the gap.

    A space that trails a *replaced* run has no such run to follow: the glyphs
    before it are gone, so the forward pass leaves it behind at the old right
    edge of the value while the word after it moves right. It then extracts
    between the wrong pair, and a pseudonym read as "Maria Van Hovewerd
    gezien". Such a space takes the shift of the word it precedes instead,
    which is what keeps the gap the document wrote on both sides of it.
    """
    if not glyph_shift:
        return
    carried = 0.0
    for index, glyph in enumerate(source_glyphs):
        if glyph is None or not glyph.text:
            carried = 0.0
            continue
        if glyph.text.strip():
            carried = glyph_shift.get(index, 0.0)
        elif carried:
            glyph_shift[index] = carried
    for index, glyph in enumerate(source_glyphs):
        following = glyph_shift.get(index + 1)
        if (
            glyph is not None
            and glyph.text
            and not glyph.text.strip()
            and index not in glyph_shift
            and following is not None
        ):
            glyph_shift[index] = following


def _spans_by_page(spans: list[MaskSpan], page_count: int) -> dict[int, list[MaskSpan]]:
    """Group coalesced spans by page, refusing one that names no real page."""
    by_page: dict[int, list[MaskSpan]] = {}
    for span in coalesce_spans(spans):
        if span.page is None or not 0 <= span.page < page_count:
            raise CorruptInputError("A PDF span has an invalid page index.")
        by_page.setdefault(span.page, []).append(span)
    return by_page


def _source_value(source_glyphs: Sequence[PdfGlyph | None], span: MaskSpan) -> str:
    """Return the value the span covers, as the source page wrote it.

    A glyph the extractor could not point at is whitespace it inserted itself:
    the " " it puts between two separately-drawn fragments, or the "\n" it
    puts between two lines. Dropping both made a wrapped value seed the router
    under a different string than the same value unwrapped -- one letter
    released "Algemeen Ziekenhuis Oost-Vlaanderen" under one name in its
    header and footer and another in its body, because the body copy wrapped
    and arrived as "Algemeen ZiekenhuisOost-Vlaanderen".

    A gap *within* a line is a space the page drew as distance rather than as
    a glyph, so it always comes back. A line break is the ambiguous one: it
    stands for the space a soft wrap did not draw, or for nothing at all when
    the layout hard-broke a token too long for the line. It is read as a hard
    break only where the value is a single unbroken token that breaks between
    two digits -- "2012-0" and "4-24" are one date, and joined with a space it
    stops parsing as one and leaves the page as a date written in words. A
    value that carries a space anywhere is prose and wraps at one, digits or
    not, so a phone number written "0476 03 31 64" keeps its own grouping.

    The residual case is a hard break through a word: "Apr" and "il" join as
    "Apr il". It costs a pseudonym whose length was measured against one
    character too many, which is the smallest of the three failures here.
    """
    glyphs = source_glyphs[span.start : span.end]
    spaced = any(
        glyph is not None and glyph.text and not glyph.text.strip() for glyph in glyphs
    )
    out: list[str] = []
    for position, glyph in enumerate(glyphs):
        if glyph is not None:
            out.append(glyph.text)
            continue
        before = next(
            (item for item in reversed(glyphs[:position]) if item is not None), None
        )
        after = next(
            (item for item in glyphs[position + 1 :] if item is not None), None
        )
        if before is None or after is None:
            continue
        broke = before.origin[1] != after.origin[1]
        if (
            broke
            and not spaced
            and before.text[-1:].isdigit()
            and after.text[:1].isdigit()
        ):
            continue
        out.append(" ")
    return "".join(out)


def _plan_page(
    page_spans: list[MaskSpan],
    source_glyphs: Sequence[PdfGlyph | None],
    router: MaskingRouter,
) -> tuple[list[_PendingRun], list[pymupdf.Rect], list[pymupdf.Rect]]:
    """Return what to draw on a page, and the source rectangles to erase first.

    The erase rectangles are split by provenance, because `apply_redactions`
    takes its flags per page and not per annotation: one call cannot both erase
    the pixels under an OCR box and spare the logo under a text-layer one.
    """
    pending: list[_PendingRun] = []
    layer_rects: list[pymupdf.Rect] = []
    ocr_rects: list[pymupdf.Rect] = []
    for span in page_spans:
        runs = _runs_for_span(source_glyphs, span)
        original = _source_value(source_glyphs, span)
        replacement = router.replacement(span, original).text
        # A run rectangle can cover unrelated glyphs when the source
        # PDF keeps a whole line in one drawing operation. Redact the
        # individual source glyphs instead, while retaining runs only
        # as the replacement layout slots.
        for glyph in source_glyphs[span.start : span.end]:
            if glyph is not None:
                target = ocr_rects if glyph.ocr else layer_rects
                target.append(pymupdf.Rect(glyph.bbox))
        # One replacement per span, drawn in the run on the value's last
        # line. A value that wrapped always *ends* the line it starts on and
        # *starts* the line it ends on, so the room its earlier runs give up
        # falls at a line end, where a shorter line is what every ragged-right
        # paragraph already looks like. The room the last run gives up would
        # fall at a line's head instead, in front of the word that follows --
        # which is the one gap a reader sees. So that is the run that draws,
        # and it draws the whole pseudonym: a wrapped span is now laid out
        # exactly like one that never wrapped, chip reflow and condensing
        # included, rather than having its replacement cut into line-sized
        # pieces that had to be as long as the value to fill them.
        pending.append(_PendingRun(span, original, runs[-1], replacement))
    return pending, layer_rects, ocr_rects


def _moved_glyph_rects(
    source_glyphs: Sequence[PdfGlyph | None],
    glyph_shift: dict[int, float],
    glyph_lift: dict[int, float] | None = None,
) -> list[pymupdf.Rect]:
    """Return one erase rectangle per reflowed line, not one per glyph.

    A shift is applied left to right and only ever decays to zero, so the
    glyphs a line moves are contiguous: everything between the first and the
    last is being redrawn, and one rectangle covers exactly what has to go.

    Per glyph this was thousands of redaction annotations on a dense page, and
    `apply_redactions` costs per annotation -- the part of this pipeline
    already measured to dominate its CPU.
    """
    lifts = glyph_lift or {}
    by_line: dict[float, list[tuple[float, float, float, float]]] = {}
    for index in glyph_shift:
        glyph = source_glyphs[index]
        if glyph is None:
            continue
        by_line.setdefault(glyph.origin[1], []).append(glyph.bbox)
        lift = lifts.get(index)
        if lift:
            # Where it is going as well as where it was. The destination row
            # was emptied by a redaction of its own, but only across the
            # glyphs the value covered -- a line that lands further left or
            # further right than the value did would otherwise be drawn on
            # top of whatever survived there.
            box = glyph.bbox
            by_line.setdefault(glyph.origin[1] - lift, []).append(
                (box[0], box[1] - lift, box[2], box[3] - lift)
            )
    return [
        pymupdf.Rect(
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        for boxes in by_line.values()
    ]


def _erase_text_layer(page, rects: Sequence[pymupdf.Rect]) -> None:
    """Erase the text-layer glyphs under *rects*.

    Image pixels are left alone, so a letterhead under a redacted name
    survives.
    """
    if not rects:
        return
    for rect in rects:
        page.add_redact_annot(rect, fill=False)
    page.apply_redactions(
        images=_REDACT_IMAGE_NONE,
        graphics=_REDACT_LINE_ART_NONE,
        text=_REDACT_TEXT_REMOVE,
    )


def _erase_ocr_layer(page, rects: Sequence[pymupdf.Rect]) -> None:
    """Erase both the pixels and the text under *rects*.

    The value lives in two places here and both have to go. The pixels are
    destroyed, because covering them leaves the original recoverable from the
    image stream with any PDF tool -- and so is the text, because the OCR layer
    *is* a text layer now.

    `TEXT_NONE` here was a leak. It was written when OCR output was synthesised
    geometry with nothing in the file behind it; once that output became real
    invisible text, skipping text removal left every original name, identifier
    and URL sitting in the masked document, fully extractable, under a page
    that looked correctly redacted. Copying the text out returned the patient's
    actual name.
    """
    if not rects:
        return
    for rect in rects:
        page.add_redact_annot(rect, fill=False)
    page.apply_redactions(
        images=_REDACT_IMAGE_PIXELS,
        graphics=_REDACT_LINE_ART_NONE,
        text=_REDACT_TEXT_REMOVE,
    )


def _redraw_moved_glyphs(
    document: pymupdf.Document,
    page,
    fonts: dict[tuple[int, str], ResolvedFont],
    source_glyphs: Sequence[PdfGlyph | None],
    glyph_shift: dict[int, float],
    glyph_lift: dict[int, float],
) -> list[tuple[float, float, float, float, bytes]]:
    """Put every glyph the line moved back down at its new x, and its new y.

    One insertion per glyph rather than one per word: each glyph keeps its own
    origin, so the spacing and kerning the document wrote survive exactly
    rather than being re-laid-out by a font this process picked.

    Returns `pdf_content_order` units rather than leaving the redraw as part
    of the page's base content. Appended to the base it extracted last: a line
    reflowed for a pseudonym came out as "Patient", the pseudonym, the two
    lines below it, and only then "werd gezien op 29-11-2009", because
    `restore` splices units into the base and never reorders the base itself.

    One unit per *contiguous run* of moved glyphs, not one per glyph. Handing
    each glyph over separately gave a line more units than the object it came
    from had gaps; the splice matched them up anyway and reopened the object
    at the wrong x, so "aan defecatie" landed at x 146.3 on top of "Begin
    van". A run is one blob with one position, so it is placed by its own
    geometry or not at all.

    Nor can it be one unit for the whole line. `restore` orders units by where
    they start, and a line with two replacements on it redraws the glyphs
    around *both*: as a single unit that run starts left of the first chip and
    ends right of the second, so the chip in the middle sorted after the whole
    line and a French conclusion extracted as ". À retenir : le tableau"
    followed by the date that belongs before it. The runs break at each
    replaced value -- those glyphs are gone and are drawn by their own unit --
    which is exactly where a chip has to be able to sort in between.
    """
    groups: list[tuple[float, list[tuple[int, float]]]] = []
    previous = None
    for index, shift in sorted(glyph_shift.items()):
        glyph = source_glyphs[index]
        if glyph is None or not glyph.text:
            continue
        # Grouped by where the glyphs are *drawn*. A line that moves up is one
        # group wherever it came from, and it must not merge with whatever
        # already sits at the baseline it is moving on to: that line is being
        # erased, and its glyphs are a separate unit of their own.
        drawn_at = glyph.origin[1] - glyph_lift.get(index, 0.0)
        broken = previous is None or index != previous + 1 or groups[-1][0] != drawn_at
        if broken:
            groups.append((drawn_at, []))
        groups[-1][1].append((index, shift))
        previous = index

    units: list[tuple[float, float, float, float, bytes]] = []
    for baseline, moved in sorted(groups, key=lambda group: group[0]):
        before = set(page.get_contents())
        left = min(
            _present(source_glyphs, index).origin[0] + shift for index, shift in moved
        )
        right = max(
            _present(source_glyphs, index).bbox[2] + shift for index, shift in moved
        )
        height = max(
            _present(source_glyphs, index).bbox[3]
            - _present(source_glyphs, index).bbox[1]
            for index, shift in moved
        )
        for index, shift in moved:
            glyph = _present(source_glyphs, index)
            font = _font_name(page, document, glyph, fonts, glyph.text)
            point = pymupdf.Point(
                glyph.origin[0] + shift,
                glyph.origin[1] - glyph_lift.get(index, 0.0),
            )
            try:
                page.insert_text(
                    point,
                    glyph.text,
                    fontname=font.name,
                    fontsize=glyph.size,
                    color=_color(glyph.color),
                    rotate=_rotation(glyph.direction),
                    overlay=True,
                )
            except Exception as exc:
                raise CorruptInputError("A PDF line could not be reflowed.") from exc
        added = b"\n".join(
            document.xref_stream(xref) or b""
            for xref in page.get_contents()
            if xref not in before
        )
        if added.strip():
            units.append((baseline, left, height, right, added))
    return units


def _draw_replacements(
    document: pymupdf.Document,
    page,
    pending: Sequence[_PendingRun],
    source_glyphs: Sequence[PdfGlyph | None],
    redaction_style: str,
    redaction_mode: str,
    reflow: _Reflow,
    fonts: dict[tuple[int, str], ResolvedFont],
) -> tuple[dict[MaskSpan, list[str]], dict[MaskSpan, str]]:
    """Draw each replacement, then splice the new content back into place.

    Everything drawn here lands in freshly appended content streams, which is
    why a pseudonym extracts at the end of the page rather than where it was
    drawn. Keeping the boundary and each replacement's own bytes is what lets
    `pdf_content_order` splice them back into place afterwards. A glyph the
    line moved aside is redrawn through the same mechanism, so it keeps its
    place in the extracted reading order too.
    """
    applied: dict[MaskSpan, list[str]] = {}
    originals: dict[MaskSpan, str] = {}
    base_xrefs = list(page.get_contents())
    units: list[tuple[float, float, float, float, bytes]] = _redraw_moved_glyphs(
        document, page, fonts, source_glyphs, reflow.plan.glyph_shift, reflow.glyph_lift
    )
    for index, (span, original, run, replacement) in enumerate(pending):
        before = set(page.get_contents())
        if redaction_style == "invisible":
            font = _font_name(page, document, run.style, fonts, replacement)
            drawn = _insert_replacement(
                page,
                run.rect,
                replacement,
                font,
                run.style,
                reflow.shift(index),
                reflow.granted_width(index),
                reflow.lift(index),
            )
        else:
            drawn = _render_styled_run(
                page,
                document,
                fonts,
                run,
                replacement,
                source_glyphs,
                span.label,
                span.confidence,
                redaction_style,
                redaction_mode,
                reflow.shift(index),
                reflow.granted_width(index),
                reflow.lift(index),
            )
        applied.setdefault(span, []).append(drawn)
        originals[span] = original
        # The box and the text of one replacement are one unit: split
        # them and the box could be painted over its own pseudonym.
        added = b"\n".join(
            document.xref_stream(xref) or b""
            for xref in page.get_contents()
            if xref not in before
        )
        if added.strip():
            # Where the replacement was *drawn*, not where the source run
            # stood: a reflowed slot moves right, and the splice places it by
            # this x. Handed the unshifted origin it would be spliced ahead of
            # the glyph it now sits after.
            shift = reflow.shift(index)
            units.append(
                (
                    run.style.origin[1] - reflow.lift(index),
                    run.style.origin[0] + shift,
                    run.rect.height,
                    run.rect.x1 + shift,
                    added,
                )
            )
    # Reading order, so that two units the splice sends to the same seam come
    # out of it the way the page is read rather than the way they were drawn:
    # a reflowed line's tail is written before every pseudonym on the page.
    units.sort(key=lambda unit: (unit[0], unit[1]))
    pdf_content_order.restore(document, page, base_xrefs, units)
    return applied, originals


def _record_page(
    router: MaskingRouter,
    applied: dict[MaskSpan, list[str]],
    originals: dict[MaskSpan, str],
    source_glyphs: Sequence[PdfGlyph | None],
) -> None:
    """Record every applied replacement, attributing what OCR read.

    Marking the OCR layer is what lets a value that later turns out wrong be
    attributed: a misread scan and a detector miss are different failures with
    different fixes, and they are indistinguishable in the output. `record`
    already writes `source_layer or source`, so nothing downstream changes.
    """
    for span, parts in applied.items():
        recorded = span
        if any(
            glyph is not None and glyph.ocr
            for glyph in source_glyphs[span.start : span.end]
        ):
            recorded = replace(span, source_layer="ocr")
        router.record(recorded, originals[span], "".join(parts))


# The document information fields that carry what a person wrote about the
# document rather than what a tool wrote about itself. A clinical letter puts
# the patient's name in Title and the author's in Author as a matter of course.
_PII_METADATA_FIELDS = ("title", "author", "subject", "keywords")


def _strip_document_metadata(document: pymupdf.Document) -> None:
    """Clear the metadata this pipeline cannot mask in place.

    Extraction reads page content, so nothing here ever reached the detector:
    a document masked with a patient's name in its Title left the pipeline
    still carrying it, one `pdftk dump_data` away. Unlike an annotation or a
    form field, metadata holds no layout and nothing downstream reads it, so
    clearing it costs the document nothing -- which is why this is stripped
    where those three are refused.

    Producer, creator and the timestamps are left alone: they name the tool
    and when it ran, and an operator tracing a release needs them.

    XMP goes with it. It is a second copy of the same fields in a stream of
    its own, and clearing only the information dictionary would leave
    `dc:title` sitting in the file holding the value that was just removed.
    """
    metadata = dict(document.metadata or {})
    if any((metadata.get(field) or "").strip() for field in _PII_METADATA_FIELDS):
        log.info("pdf_metadata_stripped fields=%s", ",".join(_PII_METADATA_FIELDS))
    document.set_metadata({**metadata, **{f: "" for f in _PII_METADATA_FIELDS}})
    document.del_xml_metadata()


def mask_pdf(
    pdf_bytes: bytes,
    spans: list[MaskSpan],
    router: MaskingRouter,
    page_glyphs: Sequence[Sequence[PdfGlyph | None]],
    *,
    redaction_style: str = "invisible",
    redaction_mode: str = "pseudonymize",
    chip_reflow: bool = True,
    locale_badge: LocaleBadge | None = None,
) -> bytes:
    """Redact direct glyph spans and redraw their deterministic replacements.

    `redaction_style` selects how a replacement is drawn: `"invisible"`
    (the default) matches the original glyph's font/size/color exactly, so
    a reader cannot tell a value was replaced. Any other value must name a
    visible box style from `pdf_redaction_styles.STYLE_VARIANTS` (currently
    `"clinical_soft"` or `"bold_chips"`), with `redaction_mode` choosing
    whether the box shows the entity-type abbreviation only (`"detect"`) or
    the fitted pseudonym (`"pseudonymize"`). A `"detect"` box also appends
    the span's model confidence as a rounded percent (`"NAME 97%"`) when one
    exists and the box has room for it; a regex match has none, and a box too
    tight for the pair keeps the bare abbreviation.

    `chip_reflow` lets a replacement that wants more width than the token it
    covers take it from the line, moving the rest of that line right (see
    `pdf_line_reflow`). Turning it off restores the older behaviour, where a
    replacement never grew past its neighbour: a visible box shrank and then
    truncated its text -- on a two-digit slot it drew "A..." -- and an
    invisible one was condensed into the slot, which is the same defect
    without the truncation to make it obvious.

    `locale_badge`, when given, stamps the language-detection scores on the
    first page. It is a diagnostic overlay and changes no replacement: the
    surrogate locale is chosen before this function is called.
    """
    _validate_redaction_options(redaction_style, redaction_mode)
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise CorruptInputError("Invalid or unsupported PDF content.") from exc

    total_marked = 0
    try:
        by_page = _spans_by_page(spans, len(document))
        if len(page_glyphs) != len(document):
            raise CorruptInputError("PDF page geometry is incomplete.")

        for page_index, page_spans in by_page.items():
            page = document[page_index]
            source_glyphs = page_glyphs[page_index]
            # One cache per page, shared by the planner and the renderer: a
            # font resolved to measure a slot is the font that slot is drawn
            # in, and registering it twice would put two copies on the page.
            fonts: dict[tuple[int, str], ResolvedFont] = {}
            pending, layer_rects, ocr_rects = _plan_page(
                page_spans, source_glyphs, router
            )
            total_marked += len(layer_rects) + len(ocr_rects)

            reflow = _NO_REFLOW
            if chip_reflow:
                reflow = _reflow_plan(
                    page,
                    document,
                    fonts,
                    pending,
                    source_glyphs,
                    page_spans,
                    redaction_style,
                    redaction_mode,
                )
                # A glyph that moves has to leave its old position, so it is
                # erased with the spans -- in the same pass, because
                # `apply_redactions` is per page and a second call after the
                # replacements were drawn would erase those too.
                layer_rects.extend(
                    _moved_glyph_rects(
                        source_glyphs, reflow.plan.glyph_shift, reflow.glyph_lift
                    )
                )

            # Order matters: the OCR pass destroys pixels and the text-layer
            # pass must not, so they cannot be merged into one call.
            _erase_text_layer(page, layer_rects)
            _erase_ocr_layer(page, ocr_rects)

            applied, originals = _draw_replacements(
                document,
                page,
                pending,
                source_glyphs,
                redaction_style,
                redaction_mode,
                reflow,
                fonts,
            )
            _record_page(router, applied, originals, source_glyphs)

        if locale_badge is not None and len(document):
            pdf_redaction_styles.draw_locale_badge(
                document[0],
                locale_badge.entries,
                locale_badge.resolved,
                detected=locale_badge.detected,
            )

        _strip_document_metadata(document)

        buffer = BytesIO()
        document.save(buffer, deflate=True, garbage=4, no_new_id=True)
        output = buffer.getvalue()
    finally:
        document.close()

    log.info(
        "pdf_masked pages=%d spans=%d redactions=%d out_bytes=%d",
        len(page_glyphs),
        len(spans),
        total_marked,
        len(output),
    )
    return output
