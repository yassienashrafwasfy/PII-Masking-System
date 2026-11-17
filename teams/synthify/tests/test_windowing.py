"""Tests for the sliding-window inference path.

The committed validation split maxes out at 273 GLiNER tokens, so no benchmark
record exercises this code. These tests stand in for that missing coverage --
without the window, a long document's tail is silently never inspected.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windowing import (  # noqa: E402
    MAX_TOKENS,
    _chunk_bounds,
    count_tokens,
    predict_sliding_window,
)


class RecordingModel:
    """Stub GLiNER that flags a marker word wherever it appears in a chunk."""

    def __init__(self, marker: str = "SECRET") -> None:
        self.marker = marker
        self.calls: list[str] = []

    def predict_entities(self, text, labels, threshold=0.5):
        self.calls.append(text)
        return [
            {
                "start": index,
                "end": index + len(self.marker),
                "label": "Name",
                "score": 0.9,
            }
            for index in _find_all(text, self.marker)
        ]


def _find_all(text: str, needle: str) -> list[int]:
    found, index = [], text.find(needle)
    while index != -1:
        found.append(index)
        index = text.find(needle, index + 1)
    return found


def _long_text(marker_at_end: bool = True) -> str:
    words = ["woord"] * 2000
    if marker_at_end:
        words[-1] = "SECRET"
    return " ".join(words)


def test_short_text_bypasses_windowing() -> None:
    model = RecordingModel()
    text = "Patient SECRET was seen today."
    assert predict_sliding_window(model, text, ["Name"]) == [
        {"start": 8, "end": 14, "label": "Name", "score": 0.9}
    ]
    assert model.calls == [text]


def test_entity_past_the_token_limit_is_still_found() -> None:
    """This is the regression: a flat predict_entities call misses this span."""
    model = RecordingModel()
    text = _long_text()
    assert count_tokens(text) > MAX_TOKENS
    spans = predict_sliding_window(model, text, ["Name"])
    assert len(model.calls) > 1
    found = [(span["start"], span["end"]) for span in spans]
    assert found == [(text.index("SECRET"), len(text))]


def test_offsets_map_back_to_the_original_text() -> None:
    model = RecordingModel()
    words = ["woord"] * 2000
    for position in (0, 700, 1999):
        words[position] = "SECRET"
    text = " ".join(words)
    spans = predict_sliding_window(model, text, ["Name"])
    starts = sorted(span["start"] for span in spans)
    assert starts == _find_all(text, "SECRET")
    for span in spans:
        assert text[span["start"] : span["end"]] == "SECRET"


def test_windows_overlap_and_cover_the_whole_text() -> None:
    text = _long_text()
    chunks = _chunk_bounds(text, MAX_TOKENS, 50)
    assert chunks[0][0] == 0
    assert chunks[-1][1] == len(text)
    for previous, current in zip(chunks, chunks[1:]):
        assert current[0] < previous[1], "windows must overlap, not just abut"
        assert count_tokens(text[current[0] : current[1]]) <= MAX_TOKENS


def test_chunking_terminates_on_a_single_oversized_word() -> None:
    text = "x" * 5000 + " tail"
    chunks = _chunk_bounds(text, MAX_TOKENS, 50)
    assert chunks[-1][1] == len(text)


def test_duplicate_spans_from_overlapping_windows_are_merged() -> None:
    model = RecordingModel()
    # A marker inside the overlap region is seen by two consecutive windows.
    words = ["woord"] * 2000
    words[360] = "SECRET"
    text = " ".join(words)
    spans = predict_sliding_window(model, text, ["Name"])
    assert len(spans) == 1


def main() -> None:
    cases = [
        case for name, case in sorted(globals().items()) if name.startswith("test_")
    ]
    for case in cases:
        case()
    print(f"Validated Synthify sliding-window inference ({len(cases)} cases).")


if __name__ == "__main__":
    main()
