"""Read the image regions of a PDF page, so a scan can be masked not refused.

This module is the seam between the document pipeline and the OCR model. It
rasterises image blocks, maps recognised boxes back into page coordinates, and
hands the result to `pdf_extraction` as ordinary text lines. It imports pymupdf
and the standard library and nothing else: the engine is reached through
`reader_for`, which imports it on first use.

That indirection is load-bearing twice over. It keeps ONNX Runtime out of every
process that merely imports the package -- the gateway among them -- and it
means the whole pipeline can be tested against a hand-written `PageReader` with
no model artifact anywhere. A stub reader is a function; that is the entire
test seam.

Three things here are less obvious than they look.

**OCR is a fallback, and only a fallback.** `inject_text_layer` skips any page
whose text layer already yields characters, so a page is read one way or the
other and never both. That is what keeps a scanner's invisible text layer from
being re-read as a lossier raster transcript of itself, and it is why a page
carrying an image *and* text keeps the refusal that predates this module: the
image is not a fallback case, it is content no text detector can search.

**Rendering a page renders all of it.** `get_pixmap(clip=...)` runs the whole
content stream clipped to a rectangle, not just the image inside it. The gate
above leaves `_drop_duplicates` one job rather than none: a page whose text
layer holds only whitespace is still an OCR candidate, and whatever those
blocks cover would otherwise be read, detected and redacted twice.

**Only the raster lives in display space.** `rawdict` geometry, redaction
annotations and `insert_text` are all in unrotated page coordinates; the pixmap
is rotated. `_page_point` is the one place that difference is handled, and at
``/Rotate 0`` -- every sample seen so far -- it collapses to a scale and an
offset.

**A page box need not describe the image on it.** One real sample declares
1861x2631pt for a 2481x3508px scan. Resolution is therefore derived per
cluster and capped in pixels; a fixed DPI would allocate 255 MB for that page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Protocol

import pymupdf

from ..core.error_codes import InferenceFailureError, ModelUnreadyError

log = logging.getLogger(__name__)

# Below this, the raster is too small to carry a legible glyph and the render
# costs more than the read can return. Rules, bullets and letterhead icons all
# land here.
MIN_CLUSTER_POINTS = 4.0

# Two image blocks nearer than this many multiples of their own height are
# treated as one region. A scan sliced into horizontal strips is common, and
# reading each strip alone cuts every line that crosses a boundary.
CLUSTER_GAP_RATIO = 0.5

# An OCR line covered at least this much by the text layer is dropped as a
# re-read of text the pipeline already has.
DUPLICATE_COVERAGE = 0.6


class OcrError(Exception):
    """Raised when OCR could not be performed, as distinct from a bad PDF.

    `extract_pdf_pages` flattens every exception into "invalid or unsupported
    PDF content", which is the right answer for a corrupt file and exactly the
    wrong one for a missing model artifact: it blames the caller's document for
    a fault on this side. This type is re-raised past that handler.
    """


@dataclass(frozen=True)
class OcrLine:
    """One recognised line, in unrotated page coordinates."""

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float
    # Per-character boundaries across the line, as fractions of its width.
    # Fractions rather than points so they survive the pixel-to-page mapping.
    char_offsets: tuple[float, ...] = ()


@dataclass(frozen=True)
class OcrBounds:
    """The per-page limits OCR runs under, as an operator configured them.

    These existed as keyword defaults on `read_page` and `inject_text_layer`
    long before this type did, and `Settings` has carried matching fields for
    just as long -- but nothing joined the two. `prepare_document` called
    `inject_text_layer(content, ocr)` with no bounds, so the defaults won and
    `PDF_OCR_DPI=300` quietly rendered at 200. Bundling them makes the
    connection one argument rather than five, which is what stops the next
    call site forgetting one.

    The defaults here are the same numbers those keywords had, so a caller
    that passes nothing behaves exactly as before.
    """

    dpi: int = 200
    max_pixels: int = 2048
    min_block_points: float = 16.0
    min_confidence: float = 0.0
    max_lines: int = 400

    @classmethod
    def from_settings(cls, settings: object) -> OcrBounds:
        """Read the bounds an operator set, falling back to each default."""
        return cls(
            dpi=getattr(settings, "pdf_ocr_dpi", cls.dpi),
            max_pixels=getattr(settings, "pdf_ocr_max_pixels", cls.max_pixels),
            min_block_points=getattr(
                settings, "pdf_ocr_min_block_points", cls.min_block_points
            ),
            min_confidence=getattr(
                settings, "pdf_ocr_min_line_confidence", cls.min_confidence
            ),
            max_lines=getattr(settings, "pdf_ocr_max_lines", cls.max_lines),
        )


DEFAULT_BOUNDS = OcrBounds()


class PageReader(Protocol):
    """What the pipeline needs from an OCR engine.

    Structural rather than nominal so a test can pass a plain function, and so
    this module never imports the engine that satisfies it.
    """

    def __call__(
        self, image: "object", *, width: int, height: int
    ) -> tuple[OcrLine, ...]:
        """Return the lines read from *image*, in its own pixel coordinates."""


def image_blocks(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """Return the bbox of every image block on *page*, in page coordinates."""
    return [
        (
            float(block["bbox"][0]),
            float(block["bbox"][1]),
            float(block["bbox"][2]),
            float(block["bbox"][3]),
        )
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 1
    ]


def text_boxes(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """Return the bbox of every text block on *page*, in page coordinates."""
    return [
        (
            float(block["bbox"][0]),
            float(block["bbox"][1]),
            float(block["bbox"][2]),
            float(block["bbox"][3]),
        )
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
    ]


def cluster_blocks(
    blocks: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Merge blocks that overlap or nearly touch into single regions.

    Transitive, and repeated until nothing more merges, so a column of strips
    collapses to one region however they are ordered in the content stream.
    """
    regions = [(b[0], b[1], b[2], b[3]) for b in blocks]
    merged = True
    while merged:
        merged = False
        result: list[tuple[float, float, float, float]] = []
        for region in regions:
            for index, existing in enumerate(result):
                if _near(existing, region):
                    result[index] = (
                        min(existing[0], region[0]),
                        min(existing[1], region[1]),
                        max(existing[2], region[2]),
                        max(existing[3], region[3]),
                    )
                    merged = True
                    break
            else:
                result.append(region)
        regions = result
    return sorted(regions, key=lambda box: (box[1], box[0]))


def _near(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> bool:
    """Say whether two boxes overlap or sit within a line's height of each other."""
    slack = CLUSTER_GAP_RATIO * min(
        max(left[3] - left[1], 1.0), max(right[3] - right[1], 1.0)
    )
    return (
        left[0] - slack <= right[2]
        and right[0] - slack <= left[2]
        and left[1] - slack <= right[3]
        and right[1] - slack <= left[3]
    )


def render_zoom(
    cluster: tuple[float, float, float, float],
    *,
    dpi: int,
    max_pixels: int,
    native_long_edge: float = 0.0,
) -> float:
    """Return the scale at which *cluster* should be rasterised for OCR.

    Three bounds, whichever is smallest:

    * the requested density, ``dpi / 72``;
    * the source's own resolution, so a low-resolution fax is never upsampled
      into a bigger array carrying no more information;
    * ``max_pixels`` on the long edge, which is the bound that actually
      protects memory when a page box does not describe its own contents.
    """
    long_edge = max(cluster[2] - cluster[0], cluster[3] - cluster[1])
    if long_edge <= 0:
        return 0.0
    candidates = [dpi / 72.0, max_pixels / long_edge]
    if native_long_edge > 0:
        candidates.append(native_long_edge / long_edge)
    return max(min(candidates), 0.05)


def _page_point(
    pixmap: pymupdf.Pixmap, page: pymupdf.Page, x: float, y: float, zoom: float
) -> pymupdf.Point:
    """Map a pixel in *pixmap* back to an unrotated page coordinate.

    The pixmap carries its own origin, which already accounts for the clip, so
    the pixel-to-display conversion is a divide and an add. The derotation is
    what puts the result into the space the rest of the pipeline uses, and it
    is the identity on an unrotated page.
    """
    point = pymupdf.Point((pixmap.x + x) / zoom, (pixmap.y + y) / zoom)
    return point * page.derotation_matrix


def _coverage(
    box: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
) -> float:
    """Return how much of *box*'s area is covered by *others*.

    An upper bound rather than an exact union: overlapping text blocks would
    be counted twice, which can only push the answer above the truth. Since
    the result gates *dropping* a line, over-counting risks discarding one --
    so the sum is clamped, and the threshold left well below 1.
    """
    area = max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)
    if area <= 0:
        return 0.0
    covered = 0.0
    for other in others:
        width = min(box[2], other[2]) - max(box[0], other[0])
        height = min(box[3], other[3]) - max(box[1], other[1])
        if width > 0 and height > 0:
            covered += width * height
    return min(covered / area, 1.0)


def _drop_duplicates(
    lines: list[OcrLine], layer: list[tuple[float, float, float, float]]
) -> list[OcrLine]:
    """Drop lines the text layer already provides.

    Rendering an image block renders whatever is drawn over it, so on a mixed
    page OCR returns the text layer's own words a second time. Keeping them
    would double every span in that region.
    """
    if not layer:
        return lines
    return [line for line in lines if _coverage(line.bbox, layer) < DUPLICATE_COVERAGE]


def read_page(
    page: pymupdf.Page,
    reader: PageReader,
    *,
    dpi: int = 200,
    max_pixels: int = 2048,
    min_block_points: float = 16.0,
    min_confidence: float = 0.0,
    max_lines: int = 400,
) -> list[OcrLine]:
    """Return every line OCR can read from *page*'s image regions.

    Callers reach this only for a page with no text layer of its own -- see
    `inject_text_layer`, which owns that decision -- so "the image regions" and
    "the page" are the same thing in practice on a scan.

    Raises `OcrError` when the reader itself fails, so the caller can tell a
    fault on this side from a document it cannot parse.
    """
    blocks = [
        block
        for block in image_blocks(page)
        if max(block[2] - block[0], block[3] - block[1]) >= min_block_points
    ]
    if not blocks:
        return []

    layer = text_boxes(page)
    lines: list[OcrLine] = []
    for cluster in cluster_blocks(blocks):
        if min(cluster[2] - cluster[0], cluster[3] - cluster[1]) < MIN_CLUSTER_POINTS:
            continue
        lines.extend(
            _read_cluster(
                page,
                reader,
                cluster,
                dpi=dpi,
                max_pixels=max_pixels,
                min_confidence=min_confidence,
            )
        )

    lines = reading_order(_drop_duplicates(lines, layer))
    if len(lines) > max_lines:
        # Truncating would drop the least confident boxes, which are the
        # hardest to read and so the likeliest to be carrying a name.
        raise OcrError(
            f"OCR read {len(lines)} lines on one page, above the {max_lines} limit."
        )
    return lines


def reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Return *lines* down the page, then across each row.

    Detection orders boxes by their top edge alone, which is injective and
    deterministic and is not reading order. Three labels printed across one
    header row have top edges a pixel or two apart, so a strict sort by `y`
    returns them in whatever order those pixels fall -- a masked scan whose
    header copied out as "PATIENT : DATE : RESPONSABLE :" where the page, and
    the same document read from its own text layer, both say "PATIENT :
    RESPONSABLE : DATE :". The values under those labels came out swapped the
    same way, which is worse than untidy: it puts a date where a reader
    expects a clinician.

    Rows are grown from a canonical sort rather than by comparing neighbours
    pairwise, so the result stays a pure function of the boxes. Tolerance is
    half the height of the line that opens the row: consecutive body lines sit
    a full line-height apart and cannot merge, while anything sharing a row
    with the opener does.
    """
    canonical = sorted(
        lines, key=lambda line: (line.bbox[1], line.bbox[0], line.bbox[3], line.bbox[2])
    )
    rows: list[tuple[float, float, list[OcrLine]]] = []
    for line in canonical:
        centre = (line.bbox[1] + line.bbox[3]) / 2.0
        height = max(line.bbox[3] - line.bbox[1], _MIN_ROW_HEIGHT)
        for row_centre, tolerance, members in rows:
            if abs(centre - row_centre) <= tolerance:
                members.append(line)
                break
        else:
            rows.append((centre, _ROW_TOLERANCE * height, [line]))

    ordered: list[OcrLine] = []
    for _centre, _tolerance, members in rows:
        ordered.extend(
            sorted(
                members,
                key=lambda line: (
                    line.bbox[0],
                    line.bbox[1],
                    line.bbox[2],
                    line.bbox[3],
                ),
            )
        )
    return ordered


def _read_cluster(
    page: pymupdf.Page,
    reader: PageReader,
    cluster: tuple[float, float, float, float],
    *,
    dpi: int,
    max_pixels: int,
    min_confidence: float,
) -> list[OcrLine]:
    """Rasterise one region, read it, and map the result back to the page."""
    zoom = render_zoom(cluster, dpi=dpi, max_pixels=max_pixels)
    if zoom <= 0:
        return []
    clip = pymupdf.Rect(*cluster) * page.rotation_matrix
    try:
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            colorspace=pymupdf.csGRAY,
            alpha=False,
        )
    except Exception as exc:  # pragma: no cover - pymupdf raises many types
        raise OcrError(f"could not rasterise a page region for OCR: {exc}") from exc

    try:
        read = reader(pixmap.samples, width=pixmap.width, height=pixmap.height)
    except (ModelUnreadyError, InferenceFailureError):
        raise
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(f"the OCR reader failed on a page region: {exc}") from exc

    lines: list[OcrLine] = []
    for line in read:
        if not line.text.strip() or line.confidence < min_confidence:
            continue
        x0, y0, x1, y1 = line.bbox
        first = _page_point(pixmap, page, x0, y0, zoom)
        second = _page_point(pixmap, page, x1, y1, zoom)
        lines.append(
            OcrLine(
                line.text,
                (
                    min(first.x, second.x),
                    min(first.y, second.y),
                    max(first.x, second.x),
                    max(first.y, second.y),
                ),
                line.confidence,
                getattr(line, "char_offsets", ()),
            )
        )
    return lines


# The font every injected glyph is written in. Base-14, so it needs no
# embedding, and its Latin-1 coverage carries the accented characters French
# and Dutch names depend on.
TEXT_LAYER_FONT = "helv"

# How far left a word's start is nudged, in fractions of one of its own
# characters. Measured on a real scan, the recogniser's boundary for a word
# start sits about a quarter of a character right of where the ink begins --
# small, and exactly enough to leave the first letter of a name showing beside
# the box drawn over it. Ends are left alone: they already run slightly long,
# and for a redaction that is the harmless direction.
_WORD_START_BIAS = 0.3

# The narrowest a separator may be stretched to. Words that touch still get a
# space character between them; a zero-width one is a glyph an extractor may
# drop, and dropping it glues the two words together.
_MIN_SEPARATOR = 0.5

# How far a line's centre may sit from the centre of the line that opened its
# row, as a fraction of that line's own height, and the floor that keeps a
# degenerate box from opening a row nothing can join.
_ROW_TOLERANCE = 0.5
_MIN_ROW_HEIGHT = 1e-6

# PDF text render mode 3: fill none, stroke none. The glyphs take part in
# extraction, selection and search exactly as any other text, and paint
# nothing -- so the page still looks like the scan it is.
_INVISIBLE = 3


def inject_text_layer(
    pdf_bytes: bytes,
    reader: PageReader,
    *,
    bounds: OcrBounds = DEFAULT_BOUNDS,
) -> tuple[bytes, tuple[tuple[tuple[float, float, float, float], ...], ...]]:
    """Return *pdf_bytes* with an invisible text layer over its image regions.

    This is what a searchable-PDF tool produces, and doing it here rather than
    synthesising geometry downstream is what lets the rest of the pipeline stay
    ignorant that OCR happened at all. Extraction, reading order, run grouping,
    font resolution, chip sizing and the redaction renderer then work on a
    scanned page exactly as they do on a born-digital one -- and the masked
    output is searchable, which a page of pure raster never is.

    The text is written at the size the OCR line box implies, so a glyph box
    lands on the ink it describes: the pipeline sizes its boxes from glyph
    height, and type written at some fixed size would put every chip on a
    scanned page at the wrong scale.

    Only a page whose own text layer yields no characters is read this way.
    OCR is a fallback for a page that is a picture of text, not a second pass
    over one the pipeline can already read exactly.

    Returns the augmented bytes and, per page, the boxes text was written
    into. Those boxes are how redaction later tells an OCR'd glyph from an
    ordinary one: position alone cannot, because a born-digital caption
    printed over a logo sits inside an image block too, and routing it to the
    pass that erases image pixels would destroy the logo under it.

    Raises `OcrError` if a page cannot be read; a partially-OCR'd document is
    not something this can return, because the pages it failed on would come
    back looking masked while carrying whatever the reader could not see.
    """
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        font = pymupdf.Font(TEXT_LAYER_FONT)
        line_height = font.ascender - font.descender
        written: list[tuple[tuple[float, float, float, float], ...]] = []
        for index in range(document.page_count):
            page = document[index]
            if page.get_text("text").strip():
                # OCR is a fallback, not a second opinion. A page that already
                # yields characters is read by the text layer alone, and an
                # image it also carries keeps the pre-OCR refusal in
                # `extract_pdf_pages` -- turning that page into a raster read
                # would be a second, lossier transcript of text the pipeline
                # can already see exactly.
                written.append(())
                continue
            lines = read_page(
                page,
                reader,
                dpi=bounds.dpi,
                max_pixels=bounds.max_pixels,
                min_block_points=bounds.min_block_points,
                min_confidence=bounds.min_confidence,
                max_lines=bounds.max_lines,
            )
            boxes = [
                line.bbox
                for line in separate_rows(normalise_heights(lines))
                if _write_line(page, line, font, line_height)
            ]
            written.append(tuple(boxes))
        if not any(written):
            return pdf_bytes, tuple(written)
        augmented = document.tobytes(deflate=True, garbage=4, no_new_id=True)
        return augmented, tuple(written)
    finally:
        document.close()


# Two line boxes belong to the same run of type when their heights are within
# this of each other. Wide enough to absorb the detector's quantisation and
# the way a box follows the letters a line happens to contain, narrow enough
# that a heading never merges with the body text under it.
_HEIGHT_CLUSTER_TOLERANCE = 0.18


def normalise_heights(lines: list[OcrLine]) -> list[OcrLine]:
    """Give lines set in the same type the same box height.

    Two things make a raw detection box a poor description of a line's size,
    and both are systematic rather than noise.

    The detector runs on a page scaled to a 960px long edge, where a body line
    is six to eight pixels of kernel. One pixel either way is the whole
    resolution of that measurement, and the unclip step then multiplies it by
    about 2.3 -- so two identical lines can come out two points apart purely
    from rounding.

    And a box follows the letters a line happens to contain. "PATIENT :" has
    no descenders, so its box is shorter than "Dr. Sophie Rousseau" set in
    exactly the same type; measured on a real scan those two differ by 13%.

    Both are visible downstream, because the injected font size comes from the
    box height and the chip size comes from the glyph: neighbouring lines of
    one paragraph end up rendering at different sizes. Clustering the heights
    and giving each cluster one representative removes the jitter and leaves a
    genuine heading its own size.

    The representative is the cluster's **largest** box, not its median. These
    boxes are what a redaction covers, and a box shorter than the line it
    describes leaves ink showing above or below the chip drawn over it.
    """
    if len(lines) < 2:
        return lines
    heights = sorted(line.bbox[3] - line.bbox[1] for line in lines)

    clusters: list[list[float]] = [[heights[0]]]
    for height in heights[1:]:
        if height <= clusters[-1][0] * (1.0 + _HEIGHT_CLUSTER_TOLERANCE):
            clusters[-1].append(height)
        else:
            clusters.append([height])

    def target_for(height: float) -> float:
        for cluster in clusters:
            if cluster[0] <= height <= cluster[-1]:
                return max(cluster)
        return height

    normalised: list[OcrLine] = []
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        grow = (target_for(y1 - y0) - (y1 - y0)) / 2.0
        if grow <= 0:
            normalised.append(line)
            continue
        normalised.append(
            OcrLine(
                line.text,
                (x0, y0 - grow, x1, y1 + grow),
                line.confidence,
                line.char_offsets,
            )
        )
    return normalised


def separate_rows(lines: list[OcrLine]) -> list[OcrLine]:
    """Stop one line's box from reaching into the line under it.

    A detection box is unclipped -- grown by roughly 2.3 times the kernel it
    was found from -- and `normalise_heights` then hands a cluster its largest
    box. Both are deliberate, and both exist so a chip drawn over a line
    covers all of that line's ink. Their joint cost is that consecutive lines
    of one paragraph overlap: on the real French scan the header's
    organisation box ran 7pt into the specialty line beneath it.

    That stays invisible until a redaction lands on one of them.
    `apply_redactions` removes every glyph whose box *intersects* the
    rectangle, not only those inside it, so redacting the organisation took
    the whole line below it away as well -- "Gastro-entérologie" deleted from
    a masked letter, named by no detector and reported by nothing.

    So a box is clamped at the midpoint between its own centre and the centre
    of any line it overlaps both across and down. Only the unclip's
    over-growth is given up: the ink of two lines does not overlap, or they
    would not have been read as two lines.
    """
    if len(lines) < 2:
        return lines
    tops = [line.bbox[1] for line in lines]
    bottoms = [line.bbox[3] for line in lines]
    for first in range(len(lines)):
        ax0, ay0, ax1, ay1 = lines[first].bbox
        above = (ay0 + ay1) / 2.0
        for second in range(first + 1, len(lines)):
            bx0, by0, bx1, by1 = lines[second].bbox
            if min(ax1, bx1) <= max(ax0, bx0):
                continue
            if ay1 <= by0 or by1 <= ay0:
                continue
            below = (by0 + by1) / 2.0
            if above == below:
                # Two boxes on one baseline are columns of a row, not lines
                # stacked on each other, and clamping them would be wrong.
                continue
            boundary = (above + below) / 2.0
            upper, lower = (first, second) if above < below else (second, first)
            bottoms[upper] = min(bottoms[upper], boundary)
            tops[lower] = max(tops[lower], boundary)
    return [
        (
            line
            if (tops[index], bottoms[index]) == (line.bbox[1], line.bbox[3])
            else replace(
                line, bbox=(line.bbox[0], tops[index], line.bbox[2], bottoms[index])
            )
        )
        for index, line in enumerate(lines)
    ]


def _write_line(
    page: pymupdf.Page,
    line: OcrLine,
    font: pymupdf.Font,
    line_height: float,
) -> int:
    """Write one recognised line onto *page* as invisible, fitted text.

    Written a word at a time, each anchored to where the recogniser saw it.
    Laying the whole line out as one run is simpler and wrong: PDF text is
    positioned by the writing font's own advance widths, and this page was set
    in something else, so by the middle of a long line every glyph box has
    drifted off its ink. That is invisible until a redaction lands on it and
    leaves the last digit of a date showing beside the box drawn to cover it.

    Within a word the same drift exists and does not matter -- a word is a
    dozen characters, not a hundred. This is what searchable-PDF tools do, and
    for the same reason.
    """
    text = line.text.strip()
    if not text:
        return 0
    x0, y0, x1, y1 = line.bbox
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return 0

    size = height / line_height
    if size <= 0.1:
        return 0
    # The baseline sits a descender above the box bottom: an OCR box is drawn
    # around the ink, so it includes whatever descends below the baseline.
    baseline = y1 - abs(font.descender) * size

    written = 0
    spans = _word_spans(line, x0, width)
    for position, (word, left, right) in enumerate(spans):
        natural = font.text_length(word, fontsize=size)
        if natural <= 0 or right - left <= 0:
            continue
        origin = pymupdf.Point(left, baseline)
        try:
            page.insert_text(
                origin,
                word,
                fontname=TEXT_LAYER_FONT,
                fontsize=size,
                render_mode=_INVISIBLE,
                morph=(origin, pymupdf.Matrix((right - left) / natural, 1.0)),
                overlay=True,
            )
            # The separator is written as a character, and as a text object of
            # its own. Written at all because PDF geometry has no space: an
            # extractor infers one from the gap between runs, and these runs
            # are stretched to cover their ink, so there is no gap to infer
            # from and a reader copying the page gets "deafdelingPneumologie".
            # Written separately because a space carried on the end of its
            # word disappears with that word when the word is redacted, and
            # the pseudonym drawn in its place then abuts the next word --
            # "83ans," where the scan read "71 ans,". Nothing else on the page
            # keeps those two words apart.
            if position < len(spans) - 1:
                # Stretched into the gap it stands in, so it stays clear of
                # the boxes on either side. Written at the line's own size it
                # is far wider than the gap on a dense line, and whichever
                # neighbour is redacted first takes it away.
                gap = max(spans[position + 1][1] - right, _MIN_SEPARATOR)
                blank = pymupdf.Point(right, baseline)
                natural_space = font.text_length(" ", fontsize=size) or gap
                page.insert_text(
                    blank,
                    " ",
                    fontname=TEXT_LAYER_FONT,
                    fontsize=size,
                    render_mode=_INVISIBLE,
                    morph=(blank, pymupdf.Matrix(gap / natural_space, 1.0)),
                    overlay=True,
                )
        except Exception as exc:  # pragma: no cover - pymupdf raises many types
            raise OcrError(f"could not write the OCR text layer: {exc}") from exc
        written += 1
    return 1 if written else 0


def _word_spans(
    line: OcrLine, x0: float, width: float
) -> list[tuple[str, float, float]]:
    """Return each whitespace-separated word with its horizontal extent.

    Falls back to the whole line stretched across its box when the recogniser
    gave no per-character alignment -- the placement is then only as good as
    the writing font's metrics, which is the situation this function exists to
    improve on, but it still covers the line.
    """
    text = line.text
    offsets = line.char_offsets
    stripped = text.strip()
    if len(offsets) != len(text) + 1:
        return [(stripped, x0, x0 + width)] if stripped else []

    spans: list[tuple[str, float, float]] = []
    index = 0
    for word in text.split():
        index = text.index(word, index)
        left = x0 + width * offsets[index]
        right = x0 + width * offsets[index + len(word)]
        bias = _WORD_START_BIAS * (right - left) / max(len(word), 1)
        start = max(x0, left - bias)
        if spans:
            # The bias may not reach back over the word before, because the
            # separator between them lives in that gap. Redaction takes every
            # glyph its rectangle touches, so a word whose box starts inside
            # its predecessor's takes the space with it -- which is how a
            # masked scan read "votre patientCharlotte" where it should read
            # "votre patient Charlotte".
            start = max(start, spans[-1][2])
        spans.append((word, start, right))
        index += len(word)
    return spans


def reader_for(settings: object) -> PageReader | None:
    """Return a reader for *settings*, or None when OCR is switched off.

    The engine is imported here and nowhere else, so a deployment with
    ``PDF_OCR=off`` never loads ONNX Runtime and never touches the artifact.
    """
    if not getattr(settings, "pdf_ocr", False):
        return None
    from ...src.models.ocr.ocr_engine import reader_from_settings

    return reader_from_settings(settings)
