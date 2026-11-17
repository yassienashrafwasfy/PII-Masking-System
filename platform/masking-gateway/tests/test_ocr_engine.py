#!/usr/bin/env python3
"""Pin the pure-numpy half of the OCR engine, and the lazy-import guarantee.

Everything here runs without a model artifact and without ONNX Runtime, which
is the point of keeping preprocessing, detection post-processing and CTC
decoding in modules that import neither. A failure in one of them is a masking
failure -- text that goes unread is PII that goes unmasked -- so they are
pinned against hand-computed arrays rather than against a recorded output.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "platform/masking-gateway/src"
sys.path.insert(0, str(SRC))

from masking_engine.app.core.error_codes import ModelUnreadyError
from masking_engine.src.models.ocr import ctc_decode, db_postprocess, preprocess


def _map(rows: list[str]) -> np.ndarray:
    """Build a probability map from an ASCII picture; '#' is ink."""
    return np.array(
        [[0.9 if char == "#" else 0.0 for char in row] for row in rows], np.float32
    )


# --- preprocess ------------------------------------------------------------


def test_to_gray_accepts_every_shape_pymupdf_can_hand_it() -> None:
    """A pixmap is grayscale, RGB or RGBA depending on the colorspace asked for."""
    plane = np.full((4, 5), 200, np.uint8)
    assert preprocess.to_gray(plane).shape == (4, 5)
    assert preprocess.to_gray(plane[:, :, None]).shape == (4, 5)
    white = np.full((4, 5, 3), 255, np.uint8)
    assert np.allclose(preprocess.to_gray(white), 255.0)
    # The alpha channel is dropped, not composited: compositing would make the
    # result depend on an assumed background colour.
    with_alpha = np.dstack([white, np.zeros((4, 5, 1), np.uint8)])
    assert np.allclose(preprocess.to_gray(with_alpha), 255.0)
    for bad in (np.zeros((2,)), np.zeros((2, 2, 2))):
        try:
            preprocess.to_gray(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"shape {bad.shape} should be rejected")


def test_box_downsample_is_an_exact_mean() -> None:
    """Aliasing here costs whole strokes on 10px type, so it is not bilinear."""
    values = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert preprocess._box_downsample(values, 2).tolist() == [
        [2.5, 4.5],
        [10.5, 12.5],
    ]
    # A factor of one is a no-op rather than a copy through the reshape path.
    assert preprocess._box_downsample(values, 1) is values
    # A size that is not a multiple pads by repetition, so the last block
    # averages real pixels instead of invented black.
    odd = np.full((3, 3), 8.0, np.float32)
    assert np.allclose(preprocess._box_downsample(odd, 2), 8.0)


def test_bilinear_is_half_pixel_centred_and_preserves_corners() -> None:
    """An off-by-half-a-pixel resize is a third of a stroke width at 10px."""
    corners = np.array([[0.0, 255.0], [255.0, 0.0]], np.float32)
    assert np.array_equal(preprocess.resize_bilinear(corners, 2, 2), corners)
    upscaled = preprocess.resize_bilinear(corners, 4, 4)
    assert (upscaled[0, 0], upscaled[0, -1]) == (0.0, 255.0)
    assert (upscaled[-1, 0], upscaled[-1, -1]) == (255.0, 0.0)
    # The assertions above pass either way, because corners and flat fields are
    # both invariant to where in the source pixel you sample. A ramp is not:
    # centred sampling of [0 1 2 3] down to two reads 0.5 and 2.5, and sampling
    # at `i * source / target` reads 0 and 2 -- the whole image shifted half an
    # output pixel left.
    ramp = np.arange(4, dtype=np.float32)[None, :]
    assert preprocess.resize_bilinear(ramp, 1, 2).ravel().tolist() == [0.5, 2.5]

    # A flat field stays flat at every ratio: no ringing, no edge darkening.
    flat = np.full((7, 11), 42.0, np.float32)
    for shape in ((3, 5), (14, 22), (7, 11)):
        assert np.allclose(preprocess.resize_bilinear(flat, *shape), 42.0), shape
    for bad in ((0, 4), (4, -1)):
        try:
            preprocess.resize_bilinear(flat, *bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"target {bad} should be rejected")


def test_detection_input_is_capped_and_padded_to_the_stride() -> None:
    """The real sample's page box lies, so a fixed DPI would allocate 255 MB."""
    page = np.full((2048, 1449), 255, np.float32)
    small, scale = preprocess.resize_for_detection(page, 960)
    assert max(small.shape) == 960, small.shape
    assert abs(scale - 960 / 2048) < 1e-6
    padded = preprocess.pad_to_multiple(small, preprocess.DET_SIZE_DIVISOR)
    assert padded.shape == (960, 704), padded.shape
    assert not padded.shape[0] % 32 and not padded.shape[1] % 32
    # Padding is white, because these pages are dark text on white and black
    # padding would invent an edge for the detector to find.
    assert padded[0, small.shape[1]] == 255.0
    # An already-small page is left alone rather than upsampled.
    tiny = np.full((100, 80), 255, np.float32)
    kept, unit = preprocess.resize_for_detection(tiny, 960)
    assert kept.shape == (100, 80) and unit == 1.0

    tensor = preprocess.normalize_detection(padded)
    assert tensor.shape == (1, 3, 960, 704) and tensor.dtype == np.float32


def test_recognition_crop_is_padded_on_the_right_only() -> None:
    """CTC is positional; the model has only seen left-aligned text."""
    crop = np.full((30, 400), 255, np.float32)
    padded_width = 1024
    tensor = preprocess.normalize_recognition(crop, padded_width)
    assert tensor.shape == (3, preprocess.REC_HEIGHT, padded_width)
    natural = int(np.ceil(preprocess.REC_HEIGHT * 400 / 30))
    assert np.all(tensor[:, :, natural:] == 0.0), "padding must be mid-grey, not ink"
    assert np.any(tensor[:, :, :natural] != 0.0)
    # A crop wider than its rung is clamped rather than overflowing the buffer.
    wide = preprocess.normalize_recognition(np.full((10, 4000), 255, np.float32), 160)
    assert wide.shape == (3, preprocess.REC_HEIGHT, 160)
    try:
        preprocess.normalize_recognition(np.zeros((0, 5), np.float32), 160)
    except ValueError:
        pass
    else:
        raise AssertionError("an empty crop should be rejected")


def test_ink_ratio_separates_a_blank_page_from_a_dead_detector() -> None:
    """Zero lines is ordinary on a blank page and a failure on an inked one."""
    assert preprocess.ink_ratio(np.full((10, 10), 255, np.float32)) == 0.0
    assert preprocess.ink_ratio(np.zeros((10, 10), np.float32)) == 1.0
    assert preprocess.ink_ratio(np.zeros((0, 0), np.float32)) == 0.0
    half = np.full((10, 10), 255, np.float32)
    half[:5] = 0
    assert abs(preprocess.ink_ratio(half) - 0.5) < 1e-9


# --- detection post-processing --------------------------------------------


def test_runs_never_straddle_a_row_boundary() -> None:
    """The sentinel column is what makes that true; without it rows merge."""
    mask = np.array([[1, 1, 0], [1, 1, 1]], bool)
    starts, ends, rows = db_postprocess.extract_runs(mask)
    assert rows.tolist() == [0, 1]
    assert starts.tolist() == [0, 0]
    assert ends.tolist() == [2, 3]
    empty = db_postprocess.extract_runs(np.zeros((0, 0), bool))
    assert all(len(part) == 0 for part in empty)


def test_components_separate_and_join_the_way_eight_connectivity_says() -> None:
    probability = _map(["..####..####..", "..####..####..", ".............."])
    boxes = db_postprocess.components_to_boxes(
        probability,
        db_postprocess.binarize(probability, 0.3),
        box_threshold=0.5,
        min_side=1,
    )
    assert len(boxes) == 2, "a clear gutter must not merge two lines"
    assert [(box.x0, box.x1) for box in boxes] == [(2.0, 6.0), (8.0, 12.0)]
    assert all(abs(box.fill - 1.0) < 1e-9 for box in boxes)

    # An L is one component, and its fill reports the shape honestly.
    corner = _map(["####......", "#.........", "#........."])
    boxes = db_postprocess.components_to_boxes(
        corner, db_postprocess.binarize(corner, 0.3), box_threshold=0.3, min_side=1
    )
    assert len(boxes) == 1 and abs(boxes[0].fill - 0.5) < 1e-9

    # A diagonal chain joins under 8-connectivity, and its low fill is exactly
    # the signal a skewed page produces.
    diagonal = _map(["#.....", ".#....", "..#...", "...#.."])
    boxes = db_postprocess.components_to_boxes(
        diagonal, db_postprocess.binarize(diagonal, 0.3), box_threshold=0.3, min_side=1
    )
    assert len(boxes) == 1 and boxes[0].fill < 0.3
    assert db_postprocess.box_fill_ratio(boxes) < 0.3
    assert db_postprocess.box_fill_ratio([]) == 1.0


def test_erosion_splits_two_lines_whose_kernels_touch() -> None:
    """The one failure mode axis-aligned run labelling has, and its knob."""
    touching = _map(["#####", "#####", "#####", "#####"])
    joined = db_postprocess.components_to_boxes(
        touching, db_postprocess.binarize(touching, 0.3), box_threshold=0.3, min_side=1
    )
    assert len(joined) == 1
    eroded = db_postprocess.binarize(touching, 0.3, erode=True)
    assert eroded.shape == touching.shape
    assert not eroded[-1].any(), "erosion thins from the bottom row"


def test_unclip_matches_the_closed_form_pyclipper_would_produce() -> None:
    """This one line is the whole of the dependency that was not taken."""
    box = db_postprocess.DetectedBox(0.0, 0.0, 400.0, 14.0, 1.0, 1.0)
    grown = db_postprocess.unclip_box(box, 1.5)
    distance = (400.0 * 14.0) * 1.5 / (2.0 * (400.0 + 14.0))
    assert abs(grown.x0 + distance) < 1e-9
    assert abs(grown.x1 - (400.0 + distance)) < 1e-9
    assert abs(grown.y1 - (14.0 + distance)) < 1e-9
    # Score and fill describe the detection, so growing the box leaves them.
    assert (grown.score, grown.fill) == (box.score, box.fill)
    degenerate = db_postprocess.DetectedBox(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert db_postprocess.unclip_box(degenerate, 1.5) == degenerate


def test_box_order_is_total_so_no_tie_falls_back_to_input_order() -> None:
    """Masking has to be byte-reproducible; PaddleOCR's sort is not stable."""
    boxes = [
        db_postprocess.DetectedBox(x, y, x + 10.0, y + 5.0, 0.9, 0.9)
        for y in (30.0, 10.0, 20.0)
        for x in (50.0, 10.0)
    ]
    expected = [(box.y0, box.x0) for box in db_postprocess.order_boxes(boxes)]
    assert expected == [
        (10.0, 10.0),
        (10.0, 50.0),
        (20.0, 10.0),
        (20.0, 50.0),
        (30.0, 10.0),
        (30.0, 50.0),
    ]
    for rotation in range(len(boxes)):
        shuffled = boxes[rotation:] + boxes[:rotation]
        assert [
            (box.y0, box.x0) for box in db_postprocess.order_boxes(shuffled)
        ] == expected, rotation


def test_scale_boxes_clips_to_the_raster_it_maps_into() -> None:
    box = db_postprocess.DetectedBox(-5.0, 10.0, 500.0, 40.0, 0.9, 0.9)
    scaled = db_postprocess.scale_boxes([box], 0.5, width=600, height=800)[0]
    assert (scaled.x0, scaled.y0) == (0.0, 20.0)
    assert scaled.x1 == 600.0, "a box may not extend past the page it is on"
    try:
        db_postprocess.scale_boxes([box], 0.0, width=1, height=1)
    except ValueError:
        pass
    else:
        raise AssertionError("a zero scale should be rejected")


def test_an_empty_and_a_saturated_map_both_behave() -> None:
    assert db_postprocess.detect_boxes(np.zeros((8, 8), np.float32)) == []
    full = db_postprocess.detect_boxes(np.ones((8, 8), np.float32))
    assert len(full) == 1 and abs(full[0].fill - 1.0) < 1e-9


# --- CTC decoding ----------------------------------------------------------

_CHARSET = ("", "a", "b", "c", " ")


def _logits(sequence: list[int]) -> np.ndarray:
    array = np.full((len(sequence), len(_CHARSET)), 0.01, np.float32)
    for step, index in enumerate(sequence):
        array[step, index] = 0.96
    return array / array.sum(axis=-1, keepdims=True)


def test_ctc_collapsing_follows_the_rule_the_model_was_trained_on() -> None:
    cases = {
        "abc": [1, 2, 3],
        "ab": [0, 1, 0, 2, 0],
        "a b": [1, 4, 2],
        # A repeat with no blank between is one character...
        "b": [2, 2, 2],
        "": [0, 0, 0],
    }
    for expected, sequence in cases.items():
        decoded = ctc_decode.greedy_decode(_logits(sequence), _CHARSET)
        assert decoded.text == expected, (sequence, decoded.text)
    # ...and a repeat separated by a blank is two, which is how "aa" spells.
    assert ctc_decode.greedy_decode(_logits([1, 0, 1]), _CHARSET).text == "aa"


def test_confidence_reports_the_weakest_character_as_well_as_the_mean() -> None:
    """One misread accent has a healthy mean; the mean is what hides it."""
    array = _logits([1, 2, 3])
    array[1] = np.float32([0.1, 0.2, 0.45, 0.15, 0.1])
    decoded = ctc_decode.greedy_decode(array, _CHARSET)
    assert decoded.text == "abc"
    assert len(decoded.char_confidences) == 3
    assert abs(decoded.min_char_confidence - 0.45) < 1e-6
    assert decoded.min_char_confidence < decoded.confidence
    blank = ctc_decode.greedy_decode(_logits([0, 0]), _CHARSET)
    assert (blank.text, blank.confidence, blank.min_char_confidence) == ("", 0.0, 0.0)


def test_a_raw_logit_array_is_softmaxed_exactly_once() -> None:
    """Softmaxing twice would drag every confidence toward uniform, silently."""
    probabilities = _logits([1, 2, 3])
    raw = np.log(probabilities + 1e-9) * 3.0
    assert ctc_decode.looks_like_probabilities(probabilities)
    assert not ctc_decode.looks_like_probabilities(raw)
    assert not ctc_decode.looks_like_probabilities(np.zeros((0, 5), np.float32))
    assert ctc_decode.greedy_decode(raw, _CHARSET).text == "abc"
    # Forcing the wrong answer is what the flag is for, and it changes the
    # confidence rather than the text -- which is exactly why it must not be
    # guessed at run time when the manifest already knows.
    forced = ctc_decode.greedy_decode(raw, _CHARSET, already_probabilities=True)
    assert forced.confidence != ctc_decode.greedy_decode(raw, _CHARSET).confidence


def test_char_intervals_are_documented_as_approximate_and_ordered() -> None:
    decoded = ctc_decode.greedy_decode(
        _logits([1, 0, 0, 0, 2]), _CHARSET, padded_width=100.0
    )
    assert decoded.text == "ab"
    intervals = decoded.char_intervals
    assert len(intervals) == 2
    assert intervals[0][0] == 0.0 and intervals[-1][1] == 100.0
    assert intervals[0][1] <= intervals[1][0], "intervals must not overlap"
    assert ctc_decode.char_intervals(np.array([], np.int64), 5, 10.0) == ()
    assert ctc_decode.char_intervals(np.array([0], np.int64), 0, 10.0) == ()


def test_charset_layout_is_derived_from_the_graph_not_configured() -> None:
    """Off by one in an 18,707-entry dictionary reads as plausible text."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "charset.txt"
        path.write_text("a\nb\nc\n", encoding="utf-8")
        assert ctc_decode.load_charset(path, 4) == ("", "a", "b", "c")
        assert ctc_decode.load_charset(path, 5) == ("", "a", "b", "c", " ")
        for impossible in (3, 6, 20000):
            try:
                ctc_decode.load_charset(path, impossible)
            except ModelUnreadyError as exc:
                assert "charset does not match" in str(exc)
            else:
                raise AssertionError(f"{impossible} classes should be rejected")


def test_charset_is_split_on_newlines_only() -> None:
    """`splitlines` breaks on U+2028, which is a legitimate charset entry."""
    body = "a\n \n \nb\n"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "charset.txt"
        path.write_text(body, encoding="utf-8")
        charset = ctc_decode.load_charset(path, 5)
    assert charset == ("", "a", " ", " ", "b")
    assert len(body.splitlines()) == 5, "splitlines would have shifted every index"


def test_a_charset_that_disagrees_with_the_logits_refuses_to_decode() -> None:
    try:
        ctc_decode.greedy_decode(_logits([1, 2]), ("", "a"))
    except ModelUnreadyError:
        pass
    else:
        raise AssertionError("a class-count mismatch must not decode")
    try:
        ctc_decode.greedy_decode(np.zeros((2, 2, 5), np.float32), _CHARSET)
    except ValueError:
        pass
    else:
        raise AssertionError("a batched array must not decode as one line")


def test_batch_decoding_keeps_one_result_per_row() -> None:
    batch = np.stack([_logits([1, 2, 3]), _logits([0, 2, 0])])
    decoded = ctc_decode.greedy_decode_batch(batch, _CHARSET, (100.0, 50.0))
    assert [line.text for line in decoded] == ["abc", "b"]
    try:
        ctc_decode.greedy_decode_batch(batch, _CHARSET, (100.0,))
    except ValueError:
        pass
    else:
        raise AssertionError("a width per row is required")


# --- the lazy-import guarantee --------------------------------------------


def test_importing_the_package_pulls_in_no_runtime() -> None:
    """The property `masking_engine/__init__.py` exists to protect.

    It is documented in prose there and pinned nowhere, which is how it would
    rot: one convenience import of an OCR or model-engine name at the top of
    an eagerly-imported `app.services` module and the gateway process starts
    paying for ONNX Runtime and transformers on every start.
    """
    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(SRC)!r});"
        "import masking_engine;"
        "leaked = sorted(n for n in ('onnxruntime', 'torch', 'transformers')"
        " if n in sys.modules);"
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"eagerly imported: {result.stdout.strip()}"


def test_model_threads_come_from_the_quota_not_the_host() -> None:
    # `os.cpu_count()` reports the machine; a container started with `--cpus`
    # gets a cgroup quota instead of fewer visible cores. Sizing the ONNX
    # thread pool from the host would oversubscribe that quota and spend the
    # difference on context switches.
    from masking_engine.src.models.xlm_r import model_engine

    assert model_engine._available_cores() >= 1
    # A quota of two CPUs must be read as two, whatever the host reports.
    with tempfile.TemporaryDirectory() as directory:
        quota = Path(directory) / "cpu.max"
        quota.write_text("200000 100000\n")
        original = model_engine.Path
        try:
            model_engine.Path = lambda p: quota if str(p).endswith("cpu.max") else original(p)  # type: ignore[assignment]
            assert model_engine._available_cores() == 2
        finally:
            model_engine.Path = original


def test_model_threads_honour_the_configured_override() -> None:
    # The benchmarked half-cores default was measured on one host size, so a
    # deployment on another has to be able to say otherwise.
    from masking_engine.src.models.xlm_r import model_engine

    previous = os.environ.get("MODEL_INTRA_OP_THREADS")
    try:
        os.environ["MODEL_INTRA_OP_THREADS"] = "3"
        assert model_engine._auto_intra_op_threads() == 3
        os.environ["MODEL_INTRA_OP_THREADS"] = "  "
        assert model_engine._auto_intra_op_threads() >= 1  # blank falls back
        os.environ["MODEL_INTRA_OP_THREADS"] = "zero"
        try:
            model_engine._auto_intra_op_threads()
        except ModelUnreadyError:
            pass
        else:
            raise AssertionError("a non-numeric thread count must be refused")
    finally:
        if previous is None:
            os.environ.pop("MODEL_INTRA_OP_THREADS", None)
        else:
            os.environ["MODEL_INTRA_OP_THREADS"] = previous


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
