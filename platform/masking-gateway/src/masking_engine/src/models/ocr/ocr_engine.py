"""Run PP-OCRv6 detection and recognition under ONNX Runtime.

The claim this module makes, precisely: **bit-identical output for the same
input bytes, on the same image, on the same host CPU.** Anything weaker breaks
NiFi's ability to replay an idempotency key, because the surrogate seed is a
digest of the document and a single different character changes the masked
bytes. Anything stronger would be false -- ONNX Runtime dispatches different
kernels for AVX2 and AVX-512, and their results differ in the last bits.

Two design points exist purely to hold that line.

**Threads are pinned, not derived.** `model_engine.py` sets
``intra_op_num_threads`` from ``os.cpu_count() // 2``, which is right for it
and wrong here: the count varies with the host and with cgroup limits, and
ONNX Runtime splits some reductions across threads, so it changes float
accumulation order and can flip a near-tie argmax. One flipped character is a
different masked document.

**Recognition pads to a fixed width ladder.** PaddleOCR pads each batch to the
widest crop in it, so the same line decodes differently depending on which
other lines happened to share its batch -- the model attends across the padded
columns. Here the padded width is a function of the crop alone, so batch
composition cannot change any result.

Every failure is loud. A page this engine reads badly is a page whose PII goes
unmasked, and there is nothing downstream that can notice, so there is no path
here that returns a partial result looking like a complete one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from ....app.core.error_codes import InferenceFailureError, ModelUnreadyError
from . import ctc_decode, db_postprocess, preprocess

log = logging.getLogger(__name__)

# Recognition crops are padded to one of these widths. Nine rungs rather than a
# width per line keeps ONNX Runtime to a handful of input shapes -- with the
# memory arena disabled, every new shape forces a fresh allocation and replan.
REC_WIDTH_LADDER = (160, 256, 384, 512, 768, 1024, 1280, 1600, 1920)

# A crop wider than the last rung runs alone at its own width, rounded up to a
# multiple of 32. Squeezing a full-width line horizontally destroys the
# separation between a letter and its accent, which is the one thing that must
# not degrade on French and Dutch clinical text.
REC_WIDTH_STEP = 32


@dataclass(frozen=True)
class OcrLine:
    """One recognised line, in the pixel space of the image it was read from."""

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float
    min_char_confidence: float
    # Per-character boundaries across the line, as fractions of its width.
    # The text layer uses them to place each word over its own ink; laying a
    # whole line out with one font's metrics drifts from whatever the page was
    # actually set in.
    char_offsets: tuple[float, ...] = ()


def _file_digest(path: Path) -> str:
    """Return the SHA-256 of *path*, read in chunks."""
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, expected: str, label: str) -> None:
    """Refuse to load *path* unless it matches *expected*.

    Checked before any session is built, so a Git LFS pointer -- 130 bytes of
    text where a model should be -- fails as a digest mismatch naming the file
    rather than as an opaque protobuf parse error. That distinction is worth
    the read: the pointer case is the likely one in a repository whose CI runs
    with ``GIT_LFS_SKIP_SMUDGE=1``.
    """
    if not path.is_file():
        raise ModelUnreadyError(f"The OCR {label} artifact is missing: {path}")
    if not expected:
        return
    actual = _file_digest(path)
    if actual != expected:
        raise ModelUnreadyError(
            f"The OCR {label} artifact does not match its expected digest."
        )


class OcrEngine:
    """Detection and recognition over a rendered page region."""

    def __init__(
        self,
        model_dir: Path | str,
        *,
        intra_op_threads: int = 4,
        det_limit_side_len: int = 960,
        # PP-OCRv6's own DBPostProcess values, read from the `inference.yml`
        # shipped in its tarball -- NOT the PP-OCRv4/v5 numbers (0.3 / 0.6 /
        # 1.5) that most reimplementations carry forward. v6 detects at a
        # lower threshold and unclips less, so the v5 constants would have
        # missed faint lines and drawn every box slightly small. The export
        # script asserts these against the tarball so a future version cannot
        # change them silently.
        det_threshold: float = 0.2,
        det_box_threshold: float = 0.45,
        det_unclip_ratio: float = 1.4,
        det_erosion: bool = False,
        rec_batch_size: int = 8,
        max_lines: int = 400,
        min_box_fill: float = 0.55,
        blank_ink_ratio: float = 0.002,
        det_sha256: str = "",
        rec_sha256: str = "",
        charset_sha256: str = "",
        accelerator: str = "cpu",
    ) -> None:
        if accelerator == "cuda":
            # Copied from the XLM-R engine deliberately: this artifact is a
            # CPU float32 export and there is no GPU path to fall back to.
            raise ModelUnreadyError("The OCR model is configured for CPU only.")

        self._root = Path(model_dir)
        self._det_path = self._root / "det" / "model.onnx"
        self._rec_path = self._root / "rec" / "model.onnx"
        self._charset_path = self._root / "rec" / "charset.txt"
        _verify(self._det_path, det_sha256, "detection")
        _verify(self._rec_path, rec_sha256, "recognition")
        _verify(self._charset_path, charset_sha256, "charset")

        self._det_limit_side_len = det_limit_side_len
        self._det_threshold = det_threshold
        self._det_box_threshold = det_box_threshold
        self._det_unclip_ratio = det_unclip_ratio
        self._det_erosion = det_erosion
        self._rec_batch_size = max(1, rec_batch_size)
        self._max_lines = max_lines
        self._min_box_fill = min_box_fill
        self._blank_ink_ratio = blank_ink_ratio

        # Imported here rather than at module scope so that importing this
        # package -- which `masking_engine.__getattr__` may do for a type
        # annotation -- does not pull the runtime in.
        import onnxruntime as ort

        options = ort.SessionOptions()
        # Keeps peak RSS near the graph's working set, the same reason the
        # XLM-R engine disables it.
        options.enable_cpu_mem_arena = False
        options.intra_op_num_threads = max(1, intra_op_threads)
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        # Set explicitly rather than left to the default: fusions change
        # accumulation order, and an ONNX Runtime upgrade that shifted the
        # default would change masked output with nothing to show for it.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        try:
            self._det = ort.InferenceSession(
                str(self._det_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._rec = ort.InferenceSession(
                str(self._rec_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise ModelUnreadyError(
                f"The OCR model could not be loaded: {exc}"
            ) from exc

        self._det_input = self._det.get_inputs()[0].name
        self._rec_input = self._rec.get_inputs()[0].name
        self._manifest = self._load_manifest()
        self._charset = ctc_decode.load_charset(
            self._charset_path, self._recognition_classes()
        )
        self._rec_is_softmax = bool(self._manifest.get("rec_output_is_softmax", True))

    # -- artifact metadata --------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        path = self._root / "rec" / "manifest.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ModelUnreadyError(f"The OCR manifest is not readable: {exc}") from exc

    def _recognition_classes(self) -> int:
        """Return the head's class count, from the graph rather than the file.

        A dictionary that disagrees with the graph by one entry shifts every
        decoded character by one index, which produces plausible text rather
        than an error. Reading the count from the graph is what turns that into
        a startup failure.
        """
        declared = self._rec.get_outputs()[0].shape[-1]
        if isinstance(declared, int) and declared > 1:
            return declared
        # A dynamic axis: ask the graph directly with one tiny input.
        probe = np.zeros((1, 3, preprocess.REC_HEIGHT, REC_WIDTH_LADDER[0]), np.float32)
        return int(self._rec.run(None, {self._rec_input: probe})[0].shape[-1])

    # -- inference ----------------------------------------------------------

    def read(self, page: np.ndarray) -> tuple[OcrLine, ...]:
        """Return every line read from *page*, a 2-D grayscale array."""
        gray = preprocess.to_gray(page)
        if gray.size == 0:
            return ()

        boxes = self._detect(gray)
        if not boxes:
            ink = preprocess.ink_ratio(gray)
            if ink < self._blank_ink_ratio:
                # Genuinely blank. Ordinary, and not the same thing as a
                # detector that produced nothing on a page full of text.
                return ()
            raise InferenceFailureError(
                "OCR detected no text on a page that contains ink."
            )

        if len(boxes) > self._max_lines:
            raise InferenceFailureError(
                f"OCR detected {len(boxes)} lines on one page, above the "
                f"{self._max_lines} limit."
            )
        fill = db_postprocess.box_fill_ratio(boxes)
        if fill < self._min_box_fill:
            # Axis-aligned boxes cannot follow slanted text, and a box beside
            # the words it should cover is a redaction that misses.
            raise InferenceFailureError(
                "The page appears skewed beyond the OCR engine's tolerance "
                f"(median box fill {fill:.2f})."
            )
        return self._recognise(gray, boxes)

    def _detect(self, gray: np.ndarray) -> list[db_postprocess.DetectedBox]:
        resized, scale = preprocess.resize_for_detection(gray, self._det_limit_side_len)
        padded = preprocess.pad_to_multiple(resized, preprocess.DET_SIZE_DIVISOR)
        tensor = preprocess.normalize_detection(padded)
        try:
            output = self._det.run(None, {self._det_input: tensor})[0]
        except Exception as exc:
            raise InferenceFailureError(f"OCR detection failed: {exc}") from exc

        probability = np.asarray(output)[0, 0]
        # The padding added above is blank paper to the detector, so its
        # predictions there are discarded rather than trusted.
        probability = probability[: resized.shape[0], : resized.shape[1]]
        boxes = db_postprocess.detect_boxes(
            probability,
            threshold=self._det_threshold,
            box_threshold=self._det_box_threshold,
            unclip_ratio=self._det_unclip_ratio,
            erode=self._det_erosion,
        )
        return db_postprocess.scale_boxes(
            boxes, scale, width=gray.shape[1], height=gray.shape[0]
        )

    def _recognise(
        self, gray: np.ndarray, boxes: list[db_postprocess.DetectedBox]
    ) -> tuple[OcrLine, ...]:
        """Crop, batch by width rung, decode, and restore the reading order."""
        crops: list[tuple[int, np.ndarray, int]] = []
        for index, box in enumerate(boxes):
            x0 = max(int(box.x0), 0)
            y0 = max(int(box.y0), 0)
            x1 = min(int(np.ceil(box.x1)), gray.shape[1])
            y1 = min(int(np.ceil(box.y1)), gray.shape[0])
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            crop = gray[y0:y1, x0:x1]
            crops.append((index, crop, _rung_for(crop)))

        decoded: dict[int, ctc_decode.DecodedLine] = {}
        # Stable ordering by (rung, source index) so batch membership is a
        # function of the page, never of dict or set iteration order.
        for rung in sorted({rung for _, _, rung in crops}):
            members = [(i, crop) for i, crop, r in crops if r == rung]
            for start in range(0, len(members), self._rec_batch_size):
                chunk = members[start : start + self._rec_batch_size]
                tensor = np.stack(
                    [preprocess.normalize_recognition(crop, rung) for _, crop in chunk]
                )
                try:
                    logits = self._rec.run(None, {self._rec_input: tensor})[0]
                except Exception as exc:
                    raise InferenceFailureError(
                        f"OCR recognition failed: {exc}"
                    ) from exc
                lines = ctc_decode.greedy_decode_batch(
                    np.asarray(logits),
                    self._charset,
                    tuple(float(rung) for _ in chunk),
                    already_probabilities=self._rec_is_softmax,
                )
                for (index, _), line in zip(chunk, lines):
                    decoded[index] = line

        return tuple(
            OcrLine(
                decoded[index].text,
                (box.x0, box.y0, box.x1, box.y1),
                decoded[index].confidence,
                decoded[index].min_char_confidence,
                _char_offsets(decoded[index]),
            )
            for index, box in enumerate(boxes)
            if index in decoded and decoded[index].text
        )


def _char_offsets(line: ctc_decode.DecodedLine) -> tuple[float, ...]:
    """Return per-character boundaries across the line, as fractions of it.

    Normalised by where the last character ended rather than by the padded
    crop width, because the padding carries no ink. Measured against the
    actual ink on a scan these land within a couple of pixels; a half-step
    "CTC fires late" correction was tried and made them worse.

    Forced non-decreasing so a boundary can never run backwards.
    """
    intervals = line.char_intervals
    if not intervals:
        return ()
    extent = max(right for _, right in intervals)
    if extent <= 0:
        return ()
    edges = [0.0]
    for _, right in intervals:
        edges.append(min(max(right / extent, edges[-1]), 1.0))
    edges[-1] = 1.0
    return tuple(edges)


def _rung_for(crop: np.ndarray) -> int:
    """Return the padded width for *crop*, from its own shape alone."""
    height, width = crop.shape
    natural = max(1, int(np.ceil(preprocess.REC_HEIGHT * width / max(height, 1))))
    for rung in REC_WIDTH_LADDER:
        if natural <= rung:
            return rung
    return int(np.ceil(natural / REC_WIDTH_STEP) * REC_WIDTH_STEP)


@cache
def _engine(
    model_dir: str,
    intra_op_threads: int,
    det_sha256: str,
    rec_sha256: str,
    charset_sha256: str,
    min_box_fill: float,
) -> OcrEngine:
    """Build the engine once per process, keyed on everything that defines it.

    `min_box_fill` is part of that key: it decides which detected boxes are
    kept, so two values are two different engines. It was a constructor
    default nothing ever passed, which left PDF_OCR_MIN_BOX_FILL validated and
    inert.
    """
    return OcrEngine(
        model_dir,
        intra_op_threads=intra_op_threads,
        det_sha256=det_sha256,
        rec_sha256=rec_sha256,
        charset_sha256=charset_sha256,
        min_box_fill=min_box_fill,
        accelerator=os.getenv("ACCELERATOR", "cpu"),
    )


def unload() -> None:
    """Release the sessions, for the service's idle-unload timer."""
    _engine.cache_clear()


def reader_from_settings(settings: Any):
    """Return a `PageReader` closure over the configured engine.

    The engine is built on the first page that needs it, not at import and not
    at service start: most deployments never OCR anything, and the artifact may
    not even be mounted.
    """

    def reader(image: Any, *, width: int, height: int):
        engine = _engine(
            str(settings.ocr_model_path),
            int(settings.ocr_intra_op_threads),
            str(settings.ocr_det_sha256),
            str(settings.ocr_rec_sha256),
            str(settings.ocr_charset_sha256),
            float(getattr(settings, "pdf_ocr_min_box_fill", 0.55)),
        )
        page = np.frombuffer(image, dtype=np.uint8).reshape(height, width)
        lines = engine.read(page)
        # Translated to the bridge's own line type, so `pdf_ocr` never imports
        # this module's names and the seam stays one-directional.
        from ....app.services.pdf_ocr import OcrLine as BridgeLine

        return tuple(
            BridgeLine(line.text, line.bbox, line.confidence, line.char_offsets)
            for line in lines
        )

    return reader
