"""Turn a rendered page into the tensors PP-OCR's two graphs expect.

Pure numpy on purpose. The alternative was OpenCV, whose entire contribution
here is a resize and a contour walk; see ``db_postprocess`` for the second
half of that argument. Nothing in this module imports a model or a runtime,
so all of it is testable against hand-written arrays.

Two decisions carry most of the accuracy in this file.

**Grayscale, then replicate to three channels.** PaddleOCR's training configs
say ``img_mode: BGR`` while its normalisation constants are the RGB-ordered
ImageNet ones, because it feeds ``cv2.imread`` output straight through. Which
order a reimplementation should use is a question with a quiet, wrong answer
and no error message. Converting to luma first makes the question disappear:
with ``R == G == B`` the channel order cannot matter. These are ~4%-ink
black-on-white clinical scans, so colour carries no glyph information, and
rendering grayscale costs 3.0 MB per page instead of 8.9 MB.

**Detect small, crop large.** Detection runs on a page scaled down to a 960px
long edge; the crops fed to recognition are cut from the *full-resolution*
raster. At 2048px a 9pt line is ~30px tall and upsamples to the required 48
cleanly; at 960px it is ~14px and upsampling 3.4x smears the accents that
French and Dutch names depend on. PaddleOCR does the same thing, and it is
easy to lose when reimplementing because both paths look like "resize".
"""

from __future__ import annotations

import numpy as np

# ImageNet statistics, in RGB order. Applied to a grayscale plane replicated
# across three channels, so the per-channel differences below act as a fixed
# affine transform rather than as a colour operation.
DET_MEAN = np.float32([0.485, 0.456, 0.406]).reshape(3, 1, 1)
DET_STD = np.float32([0.229, 0.224, 0.225]).reshape(3, 1, 1)

# Recognition normalises to [-1, 1] and is channel-uniform, so it is provably
# unaffected by the grayscale decision above.
REC_MEAN = np.float32(0.5)
REC_STD = np.float32(0.5)

# The detection graph is fully convolutional and strides by 32, so its input
# has to be a multiple of that in both axes.
DET_SIZE_DIVISOR = 32

# Recognition was trained at this height and the exported graph fixes it.
REC_HEIGHT = 48

# BT.601 luma. Fixed float32 constants rather than a library call, so the
# conversion is bit-identical run to run.
_LUMA = np.float32([0.299, 0.587, 0.114])


def to_gray(image: np.ndarray) -> np.ndarray:
    """Return *image* as a single-channel float32 plane in 0..255.

    Accepts ``(H, W)``, ``(H, W, 1)``, ``(H, W, 3)`` or ``(H, W, 4)``; an
    alpha channel is dropped rather than composited, because a scanned page
    that carries one is opaque everywhere that matters and compositing would
    make the result depend on an assumed background colour.
    """
    array = np.asarray(image)
    if array.ndim == 2:
        return array.astype(np.float32)
    if array.ndim != 3:
        raise ValueError(f"expected a 2-D or 3-D image, got shape {array.shape}")
    if array.shape[2] == 1:
        return array[:, :, 0].astype(np.float32)
    if array.shape[2] in (3, 4):
        return array[:, :, :3].astype(np.float32) @ _LUMA
    raise ValueError(f"unsupported channel count: {array.shape[2]}")


def ink_ratio(gray: np.ndarray, threshold: float = 128.0) -> float:
    """Return the fraction of *gray* darker than *threshold*.

    Used to tell a blank page from a detector that produced nothing. Those
    two look identical in the output and must not be treated alike: one is
    ordinary, the other means text went unread, which for a masking service
    means PII went unmasked.
    """
    if gray.size == 0:
        return 0.0
    return float(np.count_nonzero(gray < threshold) / gray.size)


def _box_downsample(gray: np.ndarray, factor: int) -> np.ndarray:
    """Average *gray* over ``factor``x``factor`` blocks.

    Bilinear sampling at a ratio above 2x skips source rows entirely, and on
    10px-tall type the rows it skips are the ones carrying the stroke. This
    is the same reason OpenCV ships ``INTER_AREA`` as a separate mode from
    ``INTER_LINEAR``. Edges are padded by repetition so the last partial
    block is averaged over real pixels rather than over invented black.
    """
    if factor <= 1:
        return gray
    height, width = gray.shape
    pad_h = (-height) % factor
    pad_w = (-width) % factor
    if pad_h or pad_w:
        gray = np.pad(gray, ((0, pad_h), (0, pad_w)), mode="edge")
    blocks = gray.reshape(
        gray.shape[0] // factor, factor, gray.shape[1] // factor, factor
    )
    return blocks.mean(axis=(1, 3), dtype=np.float32)


def _axis_weights(
    source: int, target: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(low, high, weight)`` index/blend arrays for one axis.

    Half-pixel centred, matching the convention OpenCV and PIL both use:
    sample ``i`` of the output corresponds to source coordinate
    ``(i + 0.5) * source / target - 0.5``. Sampling at ``i * source / target``
    instead shifts the whole image by half an output pixel, which is
    invisible on a photograph and is a third of a stroke width on 10px type.
    """
    position = (np.arange(target, dtype=np.float32) + 0.5) * (source / target) - 0.5
    position = np.clip(position, 0.0, source - 1.0)
    low = np.floor(position).astype(np.int64)
    high = np.minimum(low + 1, source - 1)
    return low, high, (position - low).astype(np.float32)


def resize_bilinear(gray: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """Return *gray* resampled to ``target_h`` x ``target_w``.

    Separable and vectorised. Not bit-identical to OpenCV, which computes
    uint8 bilinear in 5-bit fixed point -- a side-by-side against a reference
    PaddleOCR run will differ in the last bit. That is acceptable: what this
    pipeline needs is to agree with *itself* every time, which it does,
    because nothing here depends on thread count or input order.
    """
    if target_h <= 0 or target_w <= 0:
        raise ValueError(f"target size must be positive, got {target_h}x{target_w}")
    source_h, source_w = gray.shape
    if (source_h, source_w) == (target_h, target_w):
        return gray.astype(np.float32, copy=False)

    row_low, row_high, row_weight = _axis_weights(source_h, target_h)
    rows = (
        gray[row_low] * (1.0 - row_weight)[:, None]
        + gray[row_high] * row_weight[:, None]
    )
    col_low, col_high, col_weight = _axis_weights(source_w, target_w)
    return (
        rows[:, col_low] * (1.0 - col_weight)[None, :]
        + rows[:, col_high] * col_weight[None, :]
    ).astype(np.float32)


def resize_for_detection(
    gray: np.ndarray, limit_side_len: int
) -> tuple[np.ndarray, float]:
    """Return *gray* scaled so its long edge is at most *limit_side_len*.

    Returns the scaled plane and the scale factor, which is all the caller
    needs to map a detected box back to the raster it came from. The box
    downsample runs first and takes the integer part of the ratio, so the
    bilinear pass that follows never has to cross a factor of two.
    """
    height, width = gray.shape
    longest = max(height, width)
    if longest <= limit_side_len or longest == 0:
        return gray.astype(np.float32, copy=False), 1.0

    scale = limit_side_len / longest
    factor = int(1.0 / scale)
    if factor > 1:
        gray = _box_downsample(gray, factor)
    target_h = max(1, int(round(height * scale)))
    target_w = max(1, int(round(width * scale)))
    return resize_bilinear(gray, target_h, target_w), scale


def pad_to_multiple(gray: np.ndarray, divisor: int, fill: float = 255.0) -> np.ndarray:
    """Pad right and bottom with *fill* to a multiple of *divisor*.

    Padding rather than resizing to the multiple keeps the detection scale a
    single clean factor instead of a scale plus a rounding residual, which is
    one fewer place for an off-by-one to hide in the coordinate mapping. The
    detector is fully convolutional, so white padding reads to it as more
    blank paper. ``fill`` is 255 for the same reason: these pages are dark
    text on white, and padding with black would invent an edge.
    """
    height, width = gray.shape
    pad_h = (-height) % divisor
    pad_w = (-width) % divisor
    if not pad_h and not pad_w:
        return gray
    return np.pad(gray, ((0, pad_h), (0, pad_w)), mode="constant", constant_values=fill)


def _to_three_channels(gray: np.ndarray) -> np.ndarray:
    """Return *gray* as a contiguous ``(3, H, W)`` array.

    ``broadcast_to`` is a view, so the triplication is only paid once, at the
    copy that makes the buffer contiguous for the runtime.
    """
    return np.ascontiguousarray(np.broadcast_to(gray, (3,) + gray.shape))


def normalize_detection(gray: np.ndarray) -> np.ndarray:
    """Return the ``(1, 3, H, W)`` float32 tensor the detection graph takes."""
    scaled = _to_three_channels(gray.astype(np.float32, copy=False) / np.float32(255.0))
    return ((scaled - DET_MEAN) / DET_STD)[None, ...]


def normalize_recognition(gray: np.ndarray, padded_width: int) -> np.ndarray:
    """Return one ``(3, 48, padded_width)`` recognition tensor for a crop.

    The crop is resized to height 48, keeping aspect, and padded on the right
    only. Never on the left and never centred: CTC is positional and the
    model has only ever seen left-aligned text. The pad value is zero *after*
    normalisation, which is mid-grey rather than black -- PaddleOCR's
    convention, and it keeps the padding from reading as a stroke.
    """
    height, width = gray.shape
    if height <= 0 or width <= 0:
        raise ValueError(f"cannot recognise an empty crop: {height}x{width}")
    natural = max(1, min(padded_width, int(np.ceil(REC_HEIGHT * width / height))))
    resized = resize_bilinear(gray, REC_HEIGHT, natural) / np.float32(255.0)
    normalised = (resized - REC_MEAN) / REC_STD

    buffer = np.zeros((3, REC_HEIGHT, padded_width), dtype=np.float32)
    buffer[:, :, :natural] = normalised
    return buffer
