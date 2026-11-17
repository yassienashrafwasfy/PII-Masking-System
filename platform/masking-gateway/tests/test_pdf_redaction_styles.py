#!/usr/bin/env python3
"""Exercise the Clinical Soft / Bold Chips redaction box styling in isolation."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from masking_engine import label_registry
from masking_engine import pdf_redaction_styles as styles

# The fit_text cases all measure the same long name against the style's own
# size bounds, so the fixture is shared rather than restated per case.
_LONG_NAME = "Kato Verhelst Van Raemdonck"
_FONT, _MAX_SIZE, _MIN_SIZE = "helv", 8.5, 6.5

_BADGE_ENTRIES = (
    ("FRENCH", 0.99, "fr_FR"),
    ("DUTCH", 0.01, "nl_BE"),
    ("ENGLISH", 0.0, "en"),
)


def _width_at(size: float) -> float:
    return pymupdf.get_text_length(_LONG_NAME, fontname=_FONT, fontsize=size)


def _too_narrow_box() -> pymupdf.Rect:
    """A box too narrow to hold the name even at the floor size."""
    return pymupdf.Rect(0, 0, _width_at(_MIN_SIZE) / 2, 14)


def test_every_canonical_label_resolves_to_its_own_style() -> None:
    # Every canonical label the router can ever produce resolves to a
    # distinct, non-fallback style.
    for label in sorted(label_registry.masked_labels()):
        resolved = styles.resolve_style(label)
        assert resolved is not styles.FALLBACK, f"{label} has no palette entry"
        assert resolved.abbreviation

    # Pin the palette to the taxonomy: a future label addition must fail
    # loudly here instead of silently rendering a grey "PII" box.
    assert set(styles._PALETTE) == label_registry.masked_labels()


def test_an_unknown_label_falls_back_but_keeps_its_own_name() -> None:
    # An unmapped/unknown label falls back gracefully, never raises. It gets
    # the fallback color but keeps its own name as the abbreviation, so a
    # detect-mode render still shows what the source actually picked instead
    # of a generic "PII" box.
    unknown = styles.resolve_style("SOME_FUTURE_LABEL_NOBODY_MAPPED")
    assert unknown.color == styles.FALLBACK.color
    assert unknown.abbreviation == "SOME_FUTURE_LABEL_NOBODY_MAPPED"


def test_a_raw_alias_resolves_like_the_router_canonicalises_it() -> None:
    # A raw (non-canonical) alias still resolves via label_registry, exactly
    # like the router's own canonicalization.
    assert styles.resolve_style("doctor_name") == styles.resolve_style("NAME")


def test_an_ample_box_fits_at_the_maximum_size() -> None:
    # --- fit_text -----------------------------------------------------
    # Ample box: fits at the max size, unchanged text, no growth needed.
    ample = pymupdf.Rect(0, 0, 400, 14)
    fitted_rect, size, text = styles.fit_text(ample, _LONG_NAME, safe_expand=0)
    assert text == _LONG_NAME
    assert size == _MAX_SIZE
    assert fitted_rect.width <= ample.width  # safe_expand=0 never grows the box


def test_a_tight_box_shrinks_the_type_rather_than_cutting_the_text() -> None:
    # A box sized to require shrinking (fits at the floor size, not at the
    # max size): still returns the text unchanged, just smaller.
    width_at_floor = _width_at(_MIN_SIZE)
    width_at_max = _width_at(_MAX_SIZE)
    shrink_box = pymupdf.Rect(0, 0, width_at_floor + 8 + 2, 14)
    assert shrink_box.width < width_at_max + 8  # doesn't fit at max size...
    fitted_rect, size, text = styles.fit_text(shrink_box, _LONG_NAME, safe_expand=0)
    assert text == _LONG_NAME  # ...but still renders in full, just shrunk
    assert _MIN_SIZE <= size < _MAX_SIZE
    assert fitted_rect.width <= shrink_box.width


def test_a_box_that_cannot_fit_signals_rather_than_overflowing() -> None:
    # A box too narrow even at the floor size, with no room to grow: the
    # overflow-prevention property. `allow_truncate=False` must signal
    # "cannot fit" rather than silently overwrite neighboring content.
    assert (
        styles.fit_text(
            _too_narrow_box(), _LONG_NAME, safe_expand=0, allow_truncate=False
        )
        is None
    )


def test_a_last_resort_truncation_marks_the_text_and_stays_in_the_box() -> None:
    # The same box truncates with an ellipsis as a last resort, and never
    # grows past `rect.width + safe_expand`.
    too_narrow = _too_narrow_box()
    fitted_rect, size, text = styles.fit_text(too_narrow, _LONG_NAME, safe_expand=0)
    assert text.endswith(styles._ELLIPSIS)
    assert text != _LONG_NAME
    assert size == _MIN_SIZE
    assert fitted_rect.width <= too_narrow.width + 1e-6


def test_the_truncation_mark_survives_the_chip_fonts() -> None:
    # The truncation mark has to survive the chip fonts. "…" is absent from
    # the Latin-1 encoding PyMuPDF uses for the base-14 fonts, so it was
    # substituted down to a single dot -- a full em reserved, one dot drawn,
    # and nothing on the page that reads as "there is more here".
    for font in ("helv", "hebo"):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((40, 60), styles._ELLIPSIS, fontsize=24, fontname=font)
        assert page.get_text().strip() == styles._ELLIPSIS, font
        document.close()
    # And the measurement matches what is drawn, so no chip loses a
    # character it had room for.
    assert styles._text_width(styles._ELLIPSIS, "helv", 9) < 8.0


def test_enough_room_grows_the_box_instead_of_truncating() -> None:
    # Given enough safe_expand, it grows instead of truncating.
    too_narrow = _too_narrow_box()
    fitted_rect, size, text = styles.fit_text(too_narrow, _LONG_NAME, safe_expand=500)
    assert text == _LONG_NAME
    assert fitted_rect.width > too_narrow.width


def test_both_variants_draw_a_filled_box_in_both_modes() -> None:
    # --- draw_box -------------------------------------------------------
    # Both style variants paint a filled rect PyMuPDF reports via
    # get_drawings(), for both render modes, without raising.
    for variant in styles.STYLE_VARIANTS:
        for mode in ("detect", "pseudonymize"):
            document = pymupdf.open()
            page = document.new_page()
            style = styles.resolve_style("NAME")
            box = pymupdf.Rect(40, 100, 120, 114)
            fitted_rect, size, text = styles.fit_text(box, "NAME", safe_expand=0)
            styles.draw_box(
                page, fitted_rect, style, mode, text, size, style_variant=variant
            )
            drawings = [d for d in page.get_drawings() if d.get("fill")]
            assert drawings, f"{variant}/{mode} drew no filled box"
            assert any(
                abs(d["rect"].x0 - fitted_rect.x0) < 0.5
                and abs(d["rect"].y0 - fitted_rect.y0) < 0.5
                for d in drawings
            )
            assert text in page.get_text()
            document.close()


def test_an_invalid_style_variant_is_rejected() -> None:
    # An invalid style_variant is rejected rather than silently ignored.
    document = pymupdf.open()
    page = document.new_page()
    try:
        styles.draw_box(
            page,
            pymupdf.Rect(0, 0, 10, 10),
            styles.FALLBACK,
            "detect",
            "PII",
            8,
            style_variant="not_a_style",
        )
    except ValueError as exc:
        assert "style_variant" in str(exc)
    else:
        raise AssertionError("an unknown style_variant should be rejected")
    finally:
        document.close()


def test_accented_text_is_measured_at_the_width_it_draws_at() -> None:
    # --- accented text is measured at the width it draws at ------------
    # The module-level `get_text_length` under-measures anything outside its
    # assumed encoding, so a French chip was fitted to a width its own text
    # then overflowed. French carries accents in names, organisations and
    # half its months, so this is the common case there, not an edge one.
    for sample in ("Gérard Chrétien", "ÉÈÊÀÂÙÛÇ", "août", "Amélie Fontaine"):
        page = pymupdf.open().new_page()
        page.insert_text((50, 100), sample, fontname="helv", fontsize=10)
        edges = [
            value
            for block in page.get_text("rawdict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            for char in span["chars"]
            for value in (char["bbox"][0], char["bbox"][2])
        ]
        drawn = max(edges) - min(edges)
        assert abs(styles._text_width(sample, "helv", 10) - drawn) < 0.01, sample


def test_a_chip_never_sets_type_taller_than_the_box_it_sits_in() -> None:
    # --- a chip never sets type taller than the box it sits in ----------
    # The gate was `size <= rect.height - 1`, which is not the relation: a
    # glyph box is `size * (ascender - descender)` tall, about 1.37x the size.
    # On a small-print document that passed sizes the slot could not hold and
    # the chip ran into the lines above and below it.
    for height in (5.0, 6.0, 6.9, 8.2, 10.0, 12.4, 20.0):
        rect = pymupdf.Rect(0, 0, 200, height)
        fitted_rect, size, text = styles.fit_text(rect, "Jean Moreau", 40.0)
        assert size * styles._line_height("helv") <= rect.height + 0.01, (
            height,
            size,
        )


def test_the_locale_badge_stays_on_the_page() -> None:
    # --- the locale badge stays on the page ----------------------------
    # It hung a fixed 96pt chip off the right edge, which starts left of a
    # narrow page, and never looked at the bottom edge at all.
    # Every loaded language now carries a locale, so no row is drawn as a
    # rejected one: English used to arrive here as `None`.
    for width, height in ((595, 842), (200, 400), (120, 300), (80, 80), (400, 90)):
        document = pymupdf.open()
        page = document.new_page(width=width, height=height)
        styles.draw_locale_badge(page, _BADGE_ENTRIES, "fr_FR", detected=True)
        for drawing in page.get_drawings():
            box = drawing["rect"]
            assert box in page.rect + (-0.5, -0.5, 0.5, 0.5), (width, height, box)
        document.close()


def test_the_locale_badge_is_sized_relative_to_the_page() -> None:
    # --- the badge is sized relative to the page ------------------------
    # Its point sizes were chosen against A4, where 96pt is a sixth of the
    # width. A scanned page can declare a far larger box -- one real sample is
    # 1861pt wide -- and there the same fixed 96pt is a twentieth of the width
    # and needs magnification to read.
    proportions = {}
    for width, height in ((595, 842), (1190, 1684), (1861, 2631)):
        document = pymupdf.open()
        page = document.new_page(width=width, height=height)
        styles.draw_locale_badge(page, _BADGE_ENTRIES, "fr_FR", detected=True)
        boxes = [drawing["rect"] for drawing in page.get_drawings()]
        assert boxes, (width, height)
        drawn = max(box.x1 for box in boxes) - min(box.x0 for box in boxes)
        proportions[width] = drawn / width
        document.close()
    assert max(proportions.values()) - min(proportions.values()) < 0.02, proportions
    # And it still grows: a bigger page gets a bigger badge, not the same one.
    assert proportions[1861] > 0.10


def test_an_empty_locale_report_draws_nothing() -> None:
    # An empty report draws nothing at all: that is the short-text case.
    document = pymupdf.open()
    page = document.new_page()
    styles.draw_locale_badge(page, (), "nl_BE", detected=False)
    assert not page.get_drawings()
    document.close()


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
