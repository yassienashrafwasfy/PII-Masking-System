"""Draw visible, entity-colored redaction boxes instead of invisible
in-place substitution.

Two style variants (`STYLE_VARIANTS`) share one palette and one text-fitting
routine, differing only in box chrome:

- ``"clinical_soft"`` — pastel rounded box; dashed border + darker tint for
  a pseudonym.
- ``"bold_chips"`` — solid-color pill chip, white text; darker fill for a
  pseudonym.

Both support two render modes:

- ``"detect"`` draws the entity-type abbreviation only (no PII, no
  pseudonym).
- ``"pseudonymize"`` draws the fitted replacement text, styled to signal
  "this is synthetic".

This module is deliberately PDF-pipeline-agnostic (no ``MaskSpan``/glyph
types): callers resolve a style once per span, compute a per-run
``safe_expand`` from real glyph geometry, and pass plain rectangles/strings
in. That keeps it unit-testable without synthesizing a PDF.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

import pymupdf

from ..core import label_registry

FALLBACK_LABEL = "PII"

# Horizontal breathing room inside a drawn chip. Capped per box in
# `fit_text`, because a one- or two-glyph span is narrower than this.
_CHIP_PADDING = 8.0


@dataclass(frozen=True)
class EntityStyle:
    """The palette entry resolved for one canonical entity label."""

    color: tuple[float, float, float]
    abbreviation: str


# One (r, g, b, abbreviation) entry per canonical taxonomy label,
# ported from the approved "Clinical Soft" prototype
# (assets/pdf-masking-designs/generate_designs.py) and extended to cover
# every label `label_registry.masked_labels()` can ever produce.
_PALETTE: dict[str, tuple[float, float, float, str]] = {
    "NAME": (0.30, 0.43, 0.90, "NAME"),
    "GENDER": (0.55, 0.55, 0.20, "SEX"),
    "DATE": (0.83, 0.55, 0.13, "DATE"),
    "ORGANIZATION_NAME": (0.22, 0.55, 0.36, "ORG"),
    "ADDRESS": (0.11, 0.58, 0.55, "ADDR"),
    "AGE": (0.53, 0.35, 0.75, "AGE"),
    "PHONE_NUMBER": (0.16, 0.44, 0.75, "TEL"),
    "BTW_EENHEID": (0.55, 0.40, 0.15, "VAT"),
    "RIZIV_INAMI": (0.76, 0.18, 0.42, "RIZIV"),
    # The French counterparts sit in the same hue family as the Belgian
    # identifier they parallel -- provider numbers magenta, national numbers
    # red -- so a reviewer reads the two as the same kind of field while
    # still telling them apart.
    "RPPS": (0.60, 0.24, 0.58, "RPPS"),
    "INSZ_NISS": (0.82, 0.20, 0.24, "INSZ"),
    "NIR": (0.90, 0.38, 0.22, "NIR"),
    "URL": (0.35, 0.37, 0.46, "URL"),
    "EMAIL": (0.20, 0.50, 0.60, "MAIL"),
    # The English-language identifiers follow the same two hue families, so a
    # reviewer reads them as the kind of field they are before reading the
    # abbreviation: a national number red, a provider number magenta.
    "SSN": (0.86, 0.28, 0.32, "SSN"),
    "NHS_NUMBER": (0.78, 0.26, 0.18, "NHS"),
    "NPI": (0.52, 0.28, 0.64, "NPI"),
    "GMC": (0.68, 0.30, 0.52, "GMC"),
    "CNP": (0.84, 0.25, 0.22, "CNP"),
    "DNI": (0.84, 0.25, 0.22, "DNI"),
    "NIE": (0.84, 0.25, 0.22, "NIE"),
    "STEUER_ID": (0.84, 0.25, 0.22, "STEUER"),
    "LANR": (0.66, 0.28, 0.56, "LANR"),
    "COLEGIADO": (0.66, 0.28, 0.56, "COL"),
    "PARAFA": (0.66, 0.28, 0.56, "PARAFA"),
    # Clinical PHI, routing-only rather than identity PII (see
    # `core/label_profiles.json:routing_only_labels` and ADR 006): a distinct
    # olive family keeps them out of ORG's green and ADDR's teal.
    "MEDICATION": (0.45, 0.55, 0.16, "MED"),
    "SYMPTOM": (0.58, 0.60, 0.20, "SYMP"),
    "DIAGNOSIS": (0.36, 0.48, 0.14, "DIAG"),
    "ALLERGIES": (0.50, 0.52, 0.12, "ALLERGY"),
    "PRESCRIPTIONINFO": (0.42, 0.58, 0.18, "RX"),
    "TREATMENTINFO": (0.40, 0.52, 0.22, "TREAT"),
    "TESTRESULTS": (0.34, 0.54, 0.26, "TEST"),
    "PROCEDURE": (0.30, 0.46, 0.18, "PROC"),
    # Identifiers only the mDeBERTa checkpoint emits (ADR 007), in the same
    # red/magenta families as the national and provider numbers above.
    "BSN": (0.84, 0.25, 0.22, "BSN"),
    "KVNR": (0.84, 0.25, 0.22, "KVNR"),
    "NATIONAL_ID": (0.84, 0.25, 0.22, "NATID"),
    "IBAN": (0.60, 0.30, 0.45, "IBAN"),
    "MEDICALRECORDNUM": (0.66, 0.28, 0.56, "MRN"),
}

FALLBACK = EntityStyle(color=(0.55, 0.55, 0.55), abbreviation=FALLBACK_LABEL)

_STYLES: dict[str, EntityStyle] = {
    label: EntityStyle(color=(r, g, b), abbreviation=abbreviation)
    for label, (r, g, b, abbreviation) in _PALETTE.items()
}


def resolve_style(label: str) -> EntityStyle:
    """Return the style for *label*, falling back to a grey box.

    Never raises: an entity label the palette doesn't know about (a new
    taxonomy addition, an unmapped raw NER label) still renders, it just
    isn't color-coded. Mirrors the router's own defensive fallback
    (``masking_router.py:302-306``: ``label_registry.canonical_label(label)
    or label.upper()``).

    The fallback box names the label itself rather than a generic "PII" so a
    detect-mode render shows exactly what the model/regex source picked,
    even for a label the palette hasn't been taught yet.
    """
    canonical = label_registry.canonical_label(label) or label.upper()
    return _STYLES.get(
        canonical, EntityStyle(color=FALLBACK.color, abbreviation=canonical)
    )


def _tint(
    color: tuple[float, float, float], amount: float
) -> tuple[float, float, float]:
    r, g, b = color
    return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount)


def _darken(
    color: tuple[float, float, float], amount: float
) -> tuple[float, float, float]:
    r, g, b = color
    return (r * (1 - amount), g * (1 - amount), b * (1 - amount))


@cache
def _font(font: str) -> pymupdf.Font:
    """Return the metrics object for a built-in font name, built once."""
    return pymupdf.Font(fontname=font)


def _text_width(text: str, font: str, size: float) -> float:
    """Return how wide *text* actually renders in *font* at *size*.

    The module-level `get_text_length` is the obvious call and is wrong here:
    it under-measures every character outside its assumed encoding, which for
    this pipeline means every accent. "ÉÈÊÀÂÙÛÇ" measures at half the width it
    draws at, and "Gérard Chrétien" some 8% short. A French document carries
    accents in its names, its organisations and half its months, so a chip got
    fitted to a width its own text then overflowed -- constantly in French and
    almost never in Dutch, which is why it survived the Belgian corpus.
    `Font.text_length` agrees with the rendered glyphs exactly.
    """
    return _font(font).text_length(text, fontsize=size)


def line_height(font: str) -> float:
    """Public alias: the height one point of *font* renders as."""
    return _line_height(font)


def _line_height(font: str) -> float:
    """Return the height one point of *font* renders as, ascender to descender.

    PyMuPDF lays a glyph box out as ``size * (ascender - descender)``, which is
    about 1.37 for the Helvetica the chips are drawn in -- a good deal more
    than the size itself. Anything that reasons about whether type fits a box
    vertically has to go through this rather than compare against the size.
    """
    metrics = _font(font)
    return metrics.ascender - metrics.descender


# No type is legible below this, and a box too short to hold it is better
# served by text that overflows than by text nobody can read. It only binds on
# a box under about 3pt tall, which no real text layer produces.
_ABSOLUTE_MIN_SIZE = 2.0


def _size_ceiling(rect: pymupdf.Rect, font: str, max_size: float) -> float:
    """Return the largest half-point size whose glyphs fit *rect*'s height."""
    fits = rect.height / _line_height(font)
    return max(_ABSOLUTE_MIN_SIZE, math.floor(min(max_size, fits) * 2) / 2)


# Three periods, not "…". The base-14 fonts these chips are drawn with have
# no ellipsis in their Latin-1 encoding: PyMuPDF substitutes, and the page
# gets one dot where the measurement reserved a full em. That is both
# unreadable as a truncation mark and 20% wider than the "..." it stands for,
# so a chip lost a character it had room for.
_ELLIPSIS = "..."


def _truncate_to_fit(text: str, size: float, font: str, max_width: float) -> str:
    """Return *text* shortened with an ellipsis until it fits *max_width*.

    A single character has nothing to shorten -- `range(len(text) - 1, 0, -1)`
    is empty for it -- and an ellipsis is wider than most glyphs anyway, so a
    one-character text is returned whole and left to the caller's box. Dropping
    it for the mark replaced a masked digit with a dot that named neither its
    label nor its surrogate.
    """
    if len(text) <= 1:
        return text
    if _text_width(text, font, size) <= max_width:
        return text
    for i in range(len(text) - 1, 0, -1):
        candidate = text[:i].rstrip() + _ELLIPSIS
        if _text_width(candidate, font, size) <= max_width:
            return candidate
    # Even one character plus the ellipsis can overflow max_width in an
    # extremely tight box (a two-digit age's own slot, say). Overflowing by a
    # marked-truncated form is still legible as "there was more here" -- a
    # bare first character reads as the whole label or surrogate instead
    # ("AGE 96%" truncated to "A" looked like the age itself), which is worse
    # than the overflow this accepts.
    return text[:1].rstrip() + _ELLIPSIS


# The readability ceiling for chip text on a page set in 9 to 11 point.
# A caller covering larger type raises it; see `_chip_max_size`.
CHIP_MAX_SIZE = 8.5


def fit_text(
    rect: pymupdf.Rect,
    text: str,
    safe_expand: float,
    *,
    font: str = "helv",
    max_size: float = CHIP_MAX_SIZE,
    min_size: float = 6.5,
    padding: float | None = None,
    allow_truncate: bool = True,
) -> tuple[pymupdf.Rect, float, str] | None:
    """Return the largest legible rendering of *text* that fits.

    Tries decreasing font sizes at the box's own width first; if nothing
    fits, grows the box rightward by up to `safe_expand` points (never
    more — that's what stops a long replacement from overwriting the next
    word). If it still doesn't fit at the floor size: return `None` when
    `allow_truncate` is `False` (so a caller can fall back to a shorter
    candidate string instead), or truncate with an ellipsis when `True`.

    The chip padding is capped at a third of the room available. A span of one
    or two glyphs is narrower than the flat 8pt this used to add, so every font
    size failed the fit test and a one-character text fell out of
    `_truncate_to_fit` as a bare mark: a masked age rendered as dots that named
    neither its label nor its surrogate.

    `min_size` is a preference, not a floor. A box only as tall as the source
    glyphs it covers cannot hold type taller than itself, so on a small-print
    document the height ceiling wins and the chip is drawn at the size the
    document is set in. Holding the floor there instead drew 6.5pt type into a
    6.8pt slot on a 5pt note and the chip ran into the lines above and below.

    `padding` is chip chrome: the breathing room a coloured pill needs so its
    text does not touch its own border. A caller substituting text in place --
    the OCR path, which draws into a region whose pixels were just erased --
    passes `0.0`, because there is no pill and the padding would only shrink
    the type for nothing.
    """
    available = rect.width + safe_expand
    if padding is None:
        padding = min(_CHIP_PADDING, available / 3)
    else:
        padding = min(padding, available / 3)
    # Never taller than the box: `size <= rect.height - 1` stood in for this
    # and is not the relation. A rendered glyph box is `size * line_height`
    # tall, so that test passed sizes well over what the slot could hold.
    ceiling = _size_ceiling(rect, font, max_size)
    floor = min(min_size, ceiling)
    size = ceiling
    while size >= floor:
        width = _text_width(text, font, size)
        if width + padding <= available:
            new_width = min(available, max(rect.width, width + padding))
            fitted_rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + new_width, rect.y1)
            return fitted_rect, size, text
        size -= 0.5
    if not allow_truncate:
        return None
    truncated = _truncate_to_fit(text, floor, font, available - padding)
    new_width = min(
        available,
        max(
            rect.width,
            _text_width(truncated, font, floor) + padding,
        ),
    )
    fitted_rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + new_width, rect.y1)
    return fitted_rect, floor, truncated


def natural_width(
    rect: pymupdf.Rect,
    text: str,
    *,
    font: str = "helv",
    max_size: float = CHIP_MAX_SIZE,
    padding: float = _CHIP_PADDING,
) -> float:
    """Return the width this chip wants when nothing constrains it.

    The size still comes from the box's height, which is the source glyphs'
    own height and therefore the size the page is set in. Only the width is
    unconstrained -- that is the quantity a caller reflowing the line is
    asking about.
    """
    size = _size_ceiling(rect, font, max_size)
    return _text_width(text, font, size) + padding


def _draw_centered_text(
    page,
    rect: pymupdf.Rect,
    text: str,
    size: float,
    color: tuple[float, float, float],
    *,
    bold: bool = False,
) -> None:
    fontname = "hebo" if bold else "helv"
    width = _text_width(text, fontname, size)
    x = rect.x0 + (rect.width - width) / 2
    y = rect.y0 + (rect.height + size * 0.7) / 2
    page.insert_text((x, y), text, fontname=fontname, fontsize=size, color=color)


_SOFT_BORDER_WIDTH = 0.9


def _draw_clinical_soft(
    page, rect: pymupdf.Rect, style: EntityStyle, mode: str, text: str, size: float
) -> None:
    """Pastel rounded box, dashed border + darker tint for a pseudonym."""
    if mode == "detect":
        page.draw_rect(
            rect,
            color=style.color,
            fill=_tint(style.color, 0.85),
            width=_SOFT_BORDER_WIDTH,
            radius=0.4,
        )
        _draw_centered_text(
            page, rect, text, size, _darken(style.color, 0.15), bold=True
        )
    else:
        page.draw_rect(
            rect,
            color=style.color,
            fill=_tint(style.color, 0.72),
            dashes="[1.5 1.5] 0",
            width=_SOFT_BORDER_WIDTH,
            radius=0.4,
        )
        _draw_centered_text(page, rect, text, size, _darken(style.color, 0.25))


def _draw_bold_chips(
    page, rect: pymupdf.Rect, style: EntityStyle, mode: str, text: str, size: float
) -> None:
    """Solid-color pill chip, white text; darker fill for a pseudonym."""
    if mode == "detect":
        page.draw_rect(rect, color=style.color, fill=style.color, width=0, radius=0.5)
        _draw_centered_text(page, rect, text, size, (1.0, 1.0, 1.0), bold=True)
    else:
        fill = _darken(style.color, 0.18)
        page.draw_rect(
            rect, color=_darken(style.color, 0.1), fill=fill, width=0, radius=0.5
        )
        _draw_centered_text(page, rect, text, size, (1.0, 1.0, 1.0))


_RENDERERS = {
    "clinical_soft": _draw_clinical_soft,
    "bold_chips": _draw_bold_chips,
}

STYLE_VARIANTS = tuple(_RENDERERS)

# How far a variant's ink reaches outside the rectangle it is handed.
# `draw_rect` centres a stroke on the edge, so half a border lands beyond it
# on every side. A caller that fits a box up against real content has to
# reserve this much or the border is painted on top of that content: a chip
# grown to exactly the next glyph's left edge put 0.45pt of ink on the "j" of
# the word after it, on every clinical_soft page. `bold_chips` strokes
# nothing and reserves nothing.
_INK_BLEED = {"clinical_soft": _SOFT_BORDER_WIDTH / 2, "bold_chips": 0.0}


def ink_bleed(style_variant: str) -> float:
    """Return how far *style_variant*'s chrome paints outside its own rect."""
    return _INK_BLEED.get(style_variant, 0.0)


def draw_box(
    page,
    rect: pymupdf.Rect,
    style: EntityStyle,
    mode: str,
    text: str,
    size: float,
    style_variant: str = "clinical_soft",
) -> None:
    """Draw one redaction box at *rect* and center *text* inside it.

    `rect`/`size` are expected to already be the output of `fit_text` —
    this function only paints, it does not re-fit. `style_variant` selects
    which visual language to render (`"clinical_soft"` or `"bold_chips"`);
    both share the same palette, `resolve_style`, and `fit_text` — only the
    box chrome (rounded/pastel/dashed vs. pill/solid/white-text) differs.
    """
    try:
        renderer = _RENDERERS[style_variant]
    except KeyError as exc:
        raise ValueError(
            f"style_variant must be one of: {', '.join(STYLE_VARIANTS)}"
        ) from exc
    renderer(page, rect, style, mode, text, size)


# --- language-detection badge -----------------------------------------
# A diagnostic overlay, not part of masking. The surrogate locale is chosen
# from the document's own text, so a reviewer asking "why is this note masked
# in French?" otherwise has to read a log line.
#
# It reuses the entity chip renderers rather than inventing chrome of its
# own: on a page already covered in `bold_chips` pills, a bordered panel with
# progress bars read as a different tool's output. One chip per language,
# drawn by `draw_box`, puts the verdict in the visual language the rest of
# the page already speaks.

# Deliberately absent from `_PALETTE`: these chips are not entities, and
# every hue an entity uses would imply a label that is not there. A green
# winner was tried first and read as an ORGANIZATION_NAME chip, which sits
# in the same green. Charcoal reads as chrome instead, and the muted grey
# below it keeps a rejected language legible without competing.
# The page width the badge's point sizes were chosen against: A4 portrait.
_BADGE_REFERENCE_WIDTH = 595.0

_BADGE_SELECTED_COLOR = (0.16, 0.18, 0.22)
_BADGE_REJECTED_COLOR = (0.68, 0.70, 0.73)


def draw_locale_badge(
    page,
    entries: Sequence[tuple[str, float, str | None]],
    resolved: str,
    *,
    detected: bool,
    style_variant: str = "bold_chips",
) -> None:
    """Stamp one chip per candidate language on *page*, top-right.

    *entries* is `locale_detection.confidence_report` output, best first, and
    *resolved* is the locale actually used. *detected* says whether that came
    from the detector or the fallback: a fallback colours no chip as a winner,
    because nothing won.

    Chips are drawn with the same `draw_box` renderer as the entity boxes, so
    the overlay matches whichever `style_variant` the page was masked with.
    Draws nothing when *entries* is empty, which is the short-text case.
    """
    if not entries:
        return

    # Sized as a fraction of the page, not in fixed points. These numbers were
    # chosen against A4, where a 96pt badge is a sixth of the width and reads
    # comfortably. A scanned page can declare a far larger box -- one real
    # sample is 1861pt wide -- and there the same 96pt is a twentieth of the
    # width, legible only under magnification. The clamps keep it from
    # shrinking to nothing on a narrow page or ballooning on a poster.
    scale = min(max(page.rect.width / _BADGE_REFERENCE_WIDTH, 1.0), 4.0)
    size = 6.4 * scale
    chip_h = 11.0 * scale
    gap = 2.6 * scale
    margin = 16.0 * scale
    chip_w = 96.0 * scale
    # Anchored to the page's own box, not to zero: a cropped page has a
    # non-zero origin, and a fixed chip hung off a narrow page's right edge
    # started to the left of the page and drew half of itself into nothing.
    # The badge gives up the width before it gives up the margin.
    right = page.rect.x1 - margin
    # Two columns rather than one: six loaded languages (Dutch, French,
    # English, Spanish, German, Romanian -- see `locale_detection.py`) at one
    # chip per row stood twice as tall as the three-language badge this size
    # was chosen against. Each chip keeps the width a single-column badge
    # already used -- narrowing it instead truncated even the winner's own
    # percentage -- so two sit side by side and the block doubles in width
    # rather than each chip shrinking to fit. A page too narrow for two
    # falls back to the original single column, shrunk to fit as it always
    # was.
    columns = 2 if right - (page.rect.x0 + margin) >= 2 * chip_w + gap else 1
    left = max(page.rect.x0 + margin, right - columns * chip_w - (columns - 1) * gap)
    width = right - left
    if width < 24.0 * scale:
        # No room for even one legible chip. A diagnostic overlay is not
        # worth a smear of colour over a page this small.
        return
    if columns == 2:
        chip_w = (width - gap) / 2.0
    else:
        chip_w = width
    bottom = page.rect.y1 - 4.0 * scale
    y = page.rect.y0 + margin

    entries = entries[:6]
    for row_start in range(0, len(entries), columns):
        if y + chip_h > bottom:
            # Out of page before out of languages: draw no row rather than
            # one hanging off the bottom edge.
            break
        for column, (name, score, locale) in enumerate(
            entries[row_start : row_start + columns]
        ):
            x0 = left + column * (chip_w + gap)
            selected = detected and locale == resolved
            color = _BADGE_SELECTED_COLOR if selected else _BADGE_REJECTED_COLOR
            style = EntityStyle(color=color, abbreviation=name)
            rect = pymupdf.Rect(x0, y, x0 + chip_w, y + chip_h)
            label = f"{name.title()} {score * 100:.0f}%"
            text = _truncate_to_fit(label, size, "hebo", chip_w - 8 * scale)
            # `detect` chrome for the winner and `pseudonymize` chrome for
            # the rest, so the ranking survives in a single colour: the
            # winner reads as a solid chip and a loser as the muted variant.
            draw_box(
                page,
                rect,
                style,
                "detect" if selected else "pseudonymize",
                text,
                size,
                style_variant,
            )
        y += chip_h + gap
