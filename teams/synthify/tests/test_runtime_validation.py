"""Validate Synthify's post-processing of fragmented model entities."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "platform/masking-gateway/src"))

from teams.synthify import runtime  # noqa: E402


class FragmentingModel:
    def predict_entities(self, text: str, labels: list[str], threshold: float):
        url_start = text.index("https")
        name_start = text.index("Jan Van den Berg")
        phone_start = text.index("T 07/204.21.58")
        return [
            {"start": url_start, "end": url_start + 5, "label": "URL"},
            {
                "start": name_start,
                "end": name_start + len("Jan Van den Berg"),
                "label": "Name",
            },
            {
                "start": phone_start,
                "end": phone_start + len("T 07/204.21.58"),
                "label": "TELEFOON",
            },
        ]


def main() -> None:
    original_model = runtime._model
    setattr(runtime, "_model", lambda model_format: FragmentingModel())
    try:
        url = "https://www.gezondheidenwetenschap.be/nl/contact"
        text = f"Dr Jan Van den Berg: T 07/204.21.58. Visit {url}."
        spans = runtime.detect(
            text, {"content_type": "text/plain", "model_format": "onnx"}
        )
    finally:
        setattr(runtime, "_model", original_model)
    assert any(
        span.label == "URL" and text[span.start : span.end] == url for span in spans
    )
    assert any(
        span.label == "NAME" and text[span.start : span.end] == "Jan Van den Berg"
        for span in spans
    )
    assert any(
        span.label == "PHONE_NUMBER" and text[span.start : span.end] == "07/204.21.58"
        for span in spans
    )
    print("Validated Synthify handling of fragmented, prefixed, and particle names.")


if __name__ == "__main__":
    main()
