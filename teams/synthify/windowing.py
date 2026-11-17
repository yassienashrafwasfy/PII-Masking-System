"""Token-aware sliding-window inference for GLiNER.

GLiNER silently truncates anything past `max_len` (384 for this checkpoint,
see model/gliner_config.json) -- no error, no warning, the tail of the document
is simply never looked at. For a masking service that means unredacted PII, so
long inputs are chunked here and the per-chunk spans are shifted back into the
original text's coordinates.

This is a direct port of `predict_sliding_window` from the source notebook
(Milestone_1/train_colab.ipynb, cell 14), which is the configuration the
reported validation scores were produced under.

Note the notebook's validation split maxes out at 273 tokens, so no record
there ever triggered the window. It is production PDFs, not the benchmark,
that need this path.
"""

from __future__ import annotations

import re

# GLiNER's own tokenizer regex: each punctuation mark is a separate token, so
# 384 GLiNER tokens is fewer than 384 whitespace words. Counting with the
# stricter regex keeps the estimate conservative even though the checkpoint is
# configured with `words_splitter_type: whitespace`.
GLINER_TOKEN_RE = re.compile(r"\w+(?:[-_]\w+)*|\S")

MAX_TOKENS = 380  # 4-token safety margin below the model's hard limit of 384
OVERLAP = 285  # token overlap between consecutive windows


def count_tokens(text: str) -> int:
    return len(GLINER_TOKEN_RE.findall(text))


def split_words_with_offsets(text: str) -> list[tuple[int, int, str]]:
    """Split on whitespace, keeping each word's character offsets."""
    words: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        words.append((start, index, text[start:index]))
    return words


def _chunk_bounds(text: str, max_tokens: int, overlap: int) -> list[tuple[int, int]]:
    """Return character spans covering `text` in overlapping token windows."""
    words = split_words_with_offsets(text)
    stride = max(max_tokens - overlap, 1)
    chunks: list[tuple[int, int]] = []
    word_index = 0
    while word_index < len(words):
        token_count = 0
        end_index = word_index
        while end_index < len(words):
            width = count_tokens(words[end_index][2])
            if token_count + width > max_tokens and end_index > word_index:
                break
            token_count += width
            end_index += 1
        chunks.append((words[word_index][0], words[end_index - 1][1]))
        if end_index >= len(words):
            break
        skipped = 0
        next_index = word_index
        while next_index < end_index and skipped < stride:
            skipped += count_tokens(words[next_index][2])
            next_index += 1
        word_index = next_index
    return chunks


def _deduplicate(predictions: list[dict]) -> list[dict]:
    """Keep the highest-scoring span of each overlapping same-label group."""
    predictions.sort(key=lambda entity: -entity.get("score", 0))
    kept: list[dict] = []
    for entity in predictions:
        if not any(
            entity["label"] == other["label"]
            and entity["start"] < other["end"]
            and other["start"] < entity["end"]
            for other in kept
        ):
            kept.append(entity)
    return kept


def predict_sliding_window(
    model,
    text: str,
    labels: list[str],
    threshold: float = 0.5,
    max_tokens: int = MAX_TOKENS,
    overlap: int = OVERLAP,
) -> list[dict]:
    """Run `model.predict_entities` over `text`, windowing it if it is too long."""
    if count_tokens(text) <= max_tokens:
        return model.predict_entities(text, labels, threshold=threshold)

    predictions: list[dict] = []
    for start, end in _chunk_bounds(text, max_tokens, overlap):
        for entity in model.predict_entities(
            text[start:end], labels, threshold=threshold
        ):
            entity["start"] += start
            entity["end"] += start
            predictions.append(entity)
    return _deduplicate(predictions)
