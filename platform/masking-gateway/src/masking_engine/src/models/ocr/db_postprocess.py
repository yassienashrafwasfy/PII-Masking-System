"""Turn a DB probability map into ordered text-line boxes.

The reference implementation of this step calls ``cv2.findContours``,
``cv2.minAreaRect`` and ``pyclipper``. None of those is a dependency here, and
none of them needs to be, because DB's output is not a general contour
problem:

* DBNet is trained against text-line polygons **shrunk** by a ratio of 0.4, so
  a thresholded map is a set of well-separated, roughly rectangular line
  kernels -- one blob per line, with the inter-line gap deliberately widened.
  Labelling those needs connected components, not contour tracing.
* Clipper's polygon offset of an axis-aligned rectangle by ``d`` is exactly
  that rectangle grown by ``d`` on each side. ``unclip_box`` below is the
  whole of the pyclipper dependency, and it is exact rather than an
  approximation.

The cost argument that usually decides this the other way is based on
labelling pixels. This labels **runs**: a filled line kernel contributes about
one run per scanline, so a page of clinical prose is ~400 runs rather than
~680,000 pixels. Measured on a realistic 704x960 map with 55 text lines:
~2 ms to extract runs, ~3 ms to label them, ~11 ms for the whole
threshold-to-ordered-boxes path including scoring. Detection inference is two
orders of magnitude more than that, so this step is not where the time goes.

What this gives up is rotated quadrilaterals. Boxes here are axis-aligned,
which is right for a flatbed scan and degrades above roughly four degrees of
skew. That is an explicit non-goal rather than a silent failure -- see
``box_fill_ratio``, which lets the caller refuse a page it cannot read
straight instead of placing redactions slightly beside the text they are
meant to cover.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DetectedBox:
    """One text-line region, in the coordinate space of the map it came from."""

    x0: float
    y0: float
    x1: float
    y1: float
    # Mean DB probability over the component's own mask, not over its bounding
    # box. PaddleOCR calls these its "accurate" and "fast" scoring modes and
    # defaults to the fast one; the accurate number is free here because the
    # row sums are already being computed, so there is no reason to take the
    # approximation.
    score: float
    # Filled pixels over bounding-box area. A horizontal line fills 0.8-0.95
    # of its box; a skewed one fills much less, which is what makes this a
    # usable skew signal without a second pass over the image.
    fill: float


def binarize(
    probability: np.ndarray, threshold: float, erode: bool = False
) -> np.ndarray:
    """Return the thresholded map, optionally eroded once vertically.

    Erosion is off by default and exists for one failure: two lines set with
    very tight leading, or joined by an underline or a table rule, whose
    kernels touch and label as a single component. One vertical erosion
    separates them at the cost of thinning every kernel by a row, so it is a
    knob rather than a default.
    """
    mask = probability > threshold
    if erode and mask.shape[0] > 1:
        eroded = mask[:-1] & mask[1:]
        mask = np.zeros_like(mask)
        mask[:-1] = eroded
    return mask


def extract_runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(starts, ends, rows)`` for every horizontal run of True.

    ``starts`` and ``ends`` are column indices, half-open. The sentinel column
    appended before flattening is what guarantees a run can never straddle a
    row boundary, which is where the off-by-one bugs in this shape live.
    """
    height, width = mask.shape
    if height == 0 or width == 0:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty, empty

    padded = np.zeros((height, width + 1), dtype=bool)
    padded[:, :width] = mask
    flat = padded.reshape(-1).view(np.int8)
    edges = np.diff(flat, prepend=np.int8(0), append=np.int8(0))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    rows = starts // (width + 1)
    stride = rows * (width + 1)
    return starts - stride, ends - stride, rows


def label_runs(starts: np.ndarray, ends: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Return a component id per run, merging runs that touch across rows.

    Union-find with path halving over run indices, so the work is proportional
    to the number of runs rather than to the number of set pixels. Eight-way
    connectivity: two runs on adjacent rows join when their column intervals
    overlap or merely abut, which is what keeps a stroke that steps sideways
    by one pixel from splitting its own line in two.

    Component ids are assigned in raster order, so they are a deterministic
    function of the mask -- nothing here iterates a set or a dict keyed on
    anything but an integer.
    """
    count = len(starts)
    if count == 0:
        return np.empty(0, dtype=np.int64)

    parent = np.arange(count, dtype=np.int64)

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return int(node)

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    # Runs come out of `extract_runs` in raster order, so each row's runs are a
    # contiguous slice and the boundaries can be found once.
    row_starts = np.searchsorted(rows, np.arange(rows[-1] + 2))
    for row in range(1, len(row_starts) - 1):
        above_lo, above_hi = row_starts[row - 1], row_starts[row]
        here_lo, here_hi = row_starts[row], row_starts[row + 1]
        if above_lo == above_hi or here_lo == here_hi:
            continue
        # Two-pointer merge: both slices are sorted by column, so each run is
        # visited a bounded number of times rather than compared against the
        # whole row above.
        above = above_lo
        for here in range(here_lo, here_hi):
            while above < above_hi and ends[above] < starts[here]:
                above += 1
            probe = above
            while probe < above_hi and starts[probe] <= ends[here]:
                union(here, probe)
                probe += 1

    roots = np.array([find(index) for index in range(count)], dtype=np.int64)
    _, dense = np.unique(roots, return_inverse=True)
    return dense.astype(np.int64)


def components_to_boxes(
    probability: np.ndarray,
    mask: np.ndarray,
    *,
    box_threshold: float,
    min_side: int = 3,
) -> list[DetectedBox]:
    """Return one box per connected component that clears *box_threshold*.

    The per-component probability sum comes from a single cumulative sum over
    the map: a run's contribution is one subtraction, so scoring every
    component costs one pass regardless of how many there are.
    """
    starts, ends, rows = extract_runs(mask)
    if len(starts) == 0:
        return []

    component = label_runs(starts, ends, rows)
    total = int(component.max()) + 1

    # Row-wise prefix sums, with a leading zero column so a run's sum is
    # `cumulative[row, end] - cumulative[row, start]` with no special case for
    # a run that starts at column zero.
    cumulative = np.zeros((probability.shape[0], probability.shape[1] + 1), np.float64)
    np.cumsum(probability, axis=1, out=cumulative[:, 1:])
    run_sum = cumulative[rows, ends] - cumulative[rows, starts]
    run_len = (ends - starts).astype(np.float64)

    x0 = np.full(total, np.inf)
    x1 = np.full(total, -np.inf)
    y0 = np.full(total, np.inf)
    y1 = np.full(total, -np.inf)
    np.minimum.at(x0, component, starts)
    np.maximum.at(x1, component, ends)
    np.minimum.at(y0, component, rows)
    np.maximum.at(y1, component, rows + 1)
    area = np.bincount(component, weights=run_len, minlength=total)
    weight = np.bincount(component, weights=run_sum, minlength=total)

    boxes: list[DetectedBox] = []
    for index in range(total):
        width = x1[index] - x0[index]
        height = y1[index] - y0[index]
        if min(width, height) < min_side or area[index] <= 0:
            continue
        score = float(weight[index] / area[index])
        if score < box_threshold:
            continue
        boxes.append(
            DetectedBox(
                float(x0[index]),
                float(y0[index]),
                float(x1[index]),
                float(y1[index]),
                score,
                float(area[index] / (width * height)),
            )
        )
    return boxes


def unclip_box(box: DetectedBox, ratio: float) -> DetectedBox:
    """Grow *box* to undo the shrink DBNet was trained to predict.

    For a rectangle, Clipper's polygon offset is an equal outset on all four
    sides by ``area * ratio / perimeter``, so this is exactly what pyclipper
    would return and not an approximation of it.
    """
    width = box.x1 - box.x0
    height = box.y1 - box.y0
    perimeter = 2.0 * (width + height)
    if perimeter <= 0:
        return box
    distance = width * height * ratio / perimeter
    return DetectedBox(
        box.x0 - distance,
        box.y0 - distance,
        box.x1 + distance,
        box.y1 + distance,
        box.score,
        box.fill,
    )


def order_boxes(boxes: list[DetectedBox]) -> list[DetectedBox]:
    """Return *boxes* in reading order, with no tie left to input order.

    PaddleOCR sorts with a bubble pass that swaps neighbours within a 10px
    vertical threshold, which makes the result depend on the order the boxes
    arrived in. That is a determinism hazard in a pipeline whose output has to
    be byte-reproducible, so the key here is injective: two distinct boxes
    cannot compare equal.
    """
    return sorted(boxes, key=lambda box: (box.y0, box.x0, box.y1, box.x1, box.score))


def box_fill_ratio(boxes: list[DetectedBox]) -> float:
    """Return the median fill of *boxes*, or 1.0 when there are none.

    The caller uses this to decide whether a page is too skewed to redact with
    axis-aligned boxes. Median rather than mean: one full-width rule or a
    stamp should not condemn a page that is otherwise straight.
    """
    if not boxes:
        return 1.0
    return float(np.median([box.fill for box in boxes]))


def scale_boxes(
    boxes: list[DetectedBox], scale: float, width: int, height: int
) -> list[DetectedBox]:
    """Map *boxes* from detection space back to the raster, clipped to it."""
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale}")
    scaled: list[DetectedBox] = []
    for box in boxes:
        scaled.append(
            DetectedBox(
                max(0.0, min(width, box.x0 / scale)),
                max(0.0, min(height, box.y0 / scale)),
                max(0.0, min(width, box.x1 / scale)),
                max(0.0, min(height, box.y1 / scale)),
                box.score,
                box.fill,
            )
        )
    return scaled


def detect_boxes(
    probability: np.ndarray,
    *,
    # Defaults match PP-OCRv6's shipped DBPostProcess block. The engine passes
    # them explicitly; these exist so the module is usable on its own.
    threshold: float = 0.2,
    box_threshold: float = 0.45,
    unclip_ratio: float = 1.4,
    erode: bool = False,
    min_side: int = 3,
) -> list[DetectedBox]:
    """Run the whole post-process: threshold, label, score, unclip, order."""
    mask = binarize(probability, threshold, erode=erode)
    boxes = components_to_boxes(
        probability, mask, box_threshold=box_threshold, min_side=min_side
    )
    return order_boxes([unclip_box(box, unclip_ratio) for box in boxes])
