"""Decode PP-OCR recognition logits into text, with confidences.

Greedy CTC: take the most likely class at each timestep, drop blanks, and
collapse a symbol that repeats without a blank between. That is what the model
was trained against, and beam search buys nothing on a CTC head with this
alphabet.

The dangerous part of this file is not the decode, it is the charset. The
recognition head emits one class per dictionary entry plus a blank, and
PaddleOCR builds that list as ``["blank"] + &lt;file lines&gt;`` with an optional
appended space. If the file and the graph disagree by a single entry, every
character after the disagreement decodes one index off -- and one index off in
a 18,707-entry multilingual dictionary does not produce an error or even
obvious nonsense, it produces plausible-looking text. For a masking service
that is the worst possible failure: names that read as names, detected as
names, and masked to the wrong thing, with nothing anywhere to notice.

So the charset is not trusted. ``load_charset`` derives the layout from the
graph's own output width and refuses to run when the two cannot be reconciled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ....app.core.error_codes import ModelUnreadyError

# PaddleOCR reserves index 0 for the CTC blank in every recognition config.
BLANK_INDEX = 0


@dataclass(frozen=True)
class DecodedLine:
    """One recognised line: its text and everything known about how sure it is."""

    text: str
    # Arithmetic mean of the winning class probability at each emitted step.
    # This is PaddleOCR's own definition, kept so published accuracy numbers
    # stay comparable to what this pipeline reports.
    confidence: float
    # The weakest single character. A name misread on one accent has a healthy
    # mean and a poor minimum, and the mean is what hides it.
    min_char_confidence: float
    char_confidences: tuple[float, ...]
    # Half-open [left, right) timestep-derived intervals, one per emitted
    # character, in units of the padded crop width. Approximate -- see
    # `char_intervals`.
    char_intervals: tuple[tuple[float, float], ...]


def load_charset(path: Path | str, output_classes: int) -> tuple[str, ...]:
    """Return the class table for a head emitting *output_classes* classes.

    Index 0 is the blank; index ``i`` is dictionary line ``i - 1``. Whether a
    space is appended is *derived* from the graph rather than configured,
    because the two possible layouts differ by one index and the wrong guess
    is silent.

    The file is split on ``"\\n"`` rather than with ``str.splitlines()`` on
    purpose: ``splitlines`` also breaks on U+2028, U+2029 and U+0085, several
    of which can legitimately be dictionary entries in a multilingual charset.
    Splitting on those would insert phantom classes and shift every entry
    after them.
    """
    raw = Path(path).read_text(encoding="utf-8")
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    if output_classes == len(lines) + 1:
        return ("",) + tuple(lines)
    if output_classes == len(lines) + 2:
        # PaddleOCR's `use_space_char`, which appends the space after the
        # file's own entries rather than inside them.
        return ("",) + tuple(lines) + (" ",)
    raise ModelUnreadyError(
        "The OCR recognition charset does not match the model: "
        f"{len(lines)} dictionary entries cannot produce {output_classes} classes."
    )


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Return row-wise softmax, shifted by the row max for stability."""
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def looks_like_probabilities(logits: np.ndarray) -> bool:
    """Say whether *logits* already carries a softmax over its last axis.

    PaddleOCR's exported inference graphs usually end in softmax, but that is
    a property of how the artifact was exported, not of the architecture.
    Applying softmax twice is not harmless: it flattens the distribution and
    drags every confidence toward the uniform value, which would quietly
    destroy the meaning of the thresholds built on top of it.
    """
    if logits.size == 0:
        return False
    if logits.min() < 0.0:
        return False
    sums = logits.reshape(-1, logits.shape[-1]).sum(axis=-1)
    return bool(np.allclose(sums, 1.0, atol=1e-3))


def char_intervals(
    steps: np.ndarray, timesteps: int, padded_width: float
) -> tuple[tuple[float, float], ...]:
    """Return an approximate horizontal extent per emitted character.

    A CTC head has no notion of character boundaries; it fires a class at the
    timestep where it becomes confident, which is typically at or just after
    the end of the glyph. So these intervals run slightly right of the ink
    they describe, and a character emitted after a long blank run gets an
    interval far wider than itself.

    They are good enough to place a box over a word and **not** good enough to
    place one over a single character. A caller that needs per-character
    geometry should apportion the line box by font metrics instead.
    """
    if timesteps <= 0 or len(steps) == 0:
        return ()
    span = padded_width / timesteps
    return tuple((float(step * span), float((step + 1) * span)) for step in steps)


def greedy_decode(
    logits: np.ndarray,
    charset: tuple[str, ...],
    *,
    padded_width: float = 1.0,
    already_probabilities: bool | None = None,
) -> DecodedLine:
    """Decode one ``(timesteps, classes)`` array into a line.

    ``already_probabilities`` skips the detection in `looks_like_probabilities`
    when the caller knows the answer from the artifact manifest, which is the
    normal path; passing ``None`` makes this usable on its own in a test.
    """
    if logits.ndim != 2:
        raise ValueError(f"expected (timesteps, classes), got shape {logits.shape}")
    if logits.shape[-1] != len(charset):
        raise ModelUnreadyError(
            "The OCR recognition charset does not match the model: "
            f"{len(charset)} classes expected, {logits.shape[-1]} produced."
        )

    if already_probabilities is None:
        already_probabilities = looks_like_probabilities(logits)
    probabilities = logits if already_probabilities else _softmax(logits)

    # `argmax` resolves a tie to the lowest index, which is deterministic and
    # is the only tie-break behaviour this file relies on.
    best = probabilities.argmax(axis=-1)
    scores = probabilities.max(axis=-1)

    # CTC collapsing: drop blanks, and drop a symbol identical to the one
    # immediately before it. A repeated letter survives only when a blank
    # separated the two, which is exactly how the model was trained to spell
    # "aa".
    changed = np.ones(len(best), dtype=bool)
    changed[1:] = best[1:] != best[:-1]
    keep = (best != BLANK_INDEX) & changed

    steps = np.flatnonzero(keep)
    if len(steps) == 0:
        return DecodedLine("", 0.0, 0.0, (), ())

    characters = [charset[index] for index in best[steps]]
    confidences = tuple(float(value) for value in scores[steps])
    return DecodedLine(
        "".join(characters),
        float(np.mean(scores[steps])),
        float(np.min(scores[steps])),
        confidences,
        char_intervals(steps, logits.shape[0], padded_width),
    )


def greedy_decode_batch(
    logits: np.ndarray,
    charset: tuple[str, ...],
    padded_widths: tuple[float, ...],
    *,
    already_probabilities: bool | None = None,
) -> list[DecodedLine]:
    """Decode a ``(batch, timesteps, classes)`` array, one entry per row."""
    if logits.ndim != 3:
        raise ValueError(
            f"expected (batch, timesteps, classes), got shape {logits.shape}"
        )
    if len(padded_widths) != logits.shape[0]:
        raise ValueError(f"got {len(padded_widths)} widths for {logits.shape[0]} rows")
    return [
        greedy_decode(
            logits[index],
            charset,
            padded_width=padded_widths[index],
            already_probabilities=already_probabilities,
        )
        for index in range(logits.shape[0])
    ]
