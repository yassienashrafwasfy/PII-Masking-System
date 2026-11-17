"""Run XLM-R token-classification inference.

The ONNX Runtime pipeline returns character offsets that map directly to
``MaskSpan``. This is the only serving path: the repository ships ONNX graphs,
not torch checkpoints. Do not log input text or span contents.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from ....app.core.error_codes import InferenceFailureError, ModelUnreadyError
from ....app.core.schemas import MaskSpan

log = logging.getLogger(__name__)

# CSV cells and JSON values both sit between these characters.
_FIELD_SEPARATOR_RE = re.compile(r"[,;\t\r\n:{}\[\]\"]+")

# A field longer than this is left to the whole-document pass, which windows.
_MAX_FIELD_CHARS = 2000

# Tokenizing a whole document at once is what breaks on large input: 10 MiB
# produced 3.5 million offsets and exhausted memory before any inference ran.
# Inference tokenizes one slice at a time instead, so peak memory follows the
# slice, not the request. Slices overlap so that an entity on a boundary still
# sits well inside the neighbouring slice.
_SLICE_CHARS = 256 * 1024
_SLICE_OVERLAP_CHARS = 4096


def _document_slices(text: str) -> list[tuple[int, str]]:
    """Return overlapping (offset, chunk) pairs covering *text*."""
    if len(text) <= _SLICE_CHARS:
        return [(0, text)]

    slices: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + _SLICE_CHARS, len(text))
        if end < len(text):
            # Prefer a whitespace boundary so tokens match the whole document.
            boundary = text.rfind(" ", start + _SLICE_CHARS - 1024, end)
            if boundary > start:
                end = boundary
        slices.append((start, text[start:end]))
        if end >= len(text):
            break
        start = end - _SLICE_OVERLAP_CHARS
    return slices


def clip_spans_to_fields(text: str, spans: list[MaskSpan]) -> list[MaskSpan]:
    """Keep each span inside one field of *text*.

    The model sometimes merges a column header and the value beneath it into
    one entity, for example ``city\\r\\nEls Wouters``. Masking that span as it
    stands would overwrite the header and the line break, turning two CSV rows
    into one. Each span is clipped to the field it covers most, which keeps the
    value masked and the structure intact.
    """
    fields = structured_fields(text)
    if not fields:
        return spans

    clipped: list[MaskSpan] = []
    for span in spans:
        overlaps = [
            (min(span.end, end) - max(span.start, start), start, end)
            for start, end in fields
            if min(span.end, end) > max(span.start, start)
        ]
        if not overlaps:
            continue
        _, start, end = max(overlaps)
        clipped.append(
            replace(span, start=max(span.start, start), end=min(span.end, end))
        )
    return clipped


def structured_fields(text: str) -> list[tuple[int, int]]:
    """Return the offsets of each delimiter-separated field in *text*.

    The model was trained on prose, so a value surrounded by commas scores far
    lower than the same value on its own. Detection runs over each field as
    well as the whole document, and the caller keeps both sets of spans.
    """
    fields: list[tuple[int, int]] = []
    position = 0
    for match in _FIELD_SEPARATOR_RE.finditer(text):
        if match.start() > position:
            fields.append((position, match.start()))
        position = match.end()
    if position < len(text):
        fields.append((position, len(text)))
    return [
        (start, end)
        for start, end in fields
        if text[start:end].strip() and end - start <= _MAX_FIELD_CHARS
    ]


def _file_checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def onnx_model_checksum(model_path: str | Path) -> str:
    """Return the SHA-256 fingerprint of an exported ONNX graph."""
    path = Path(model_path) / "model.onnx"
    if not path.is_file():
        raise ValueError("The ONNX model directory contains no model.onnx file.")
    return _file_checksum(path)


def _available_cores() -> int:
    """Cores this process may actually use, not the cores the host has.

    `os.cpu_count()` reports the machine. A container started with `--cpus`
    gets a cgroup CPU *quota* rather than fewer visible cores, and neither
    `cpu_count()` nor the affinity mask reflects it -- so sizing a thread pool
    from either oversubscribes the quota and spends the difference on context
    switches. deploy/compose.yml sets no CPU limit today, which is why this
    has not bitten; it would the moment one is added.
    """
    for quota_file, period_file in (
        ("/sys/fs/cgroup/cpu.max", None),  # cgroup v2: "<quota> <period>"
        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
    ):
        try:
            raw = Path(quota_file).read_text().split()
            quota = raw[0]
            if quota in ("max", "-1"):
                continue
            period = (
                int(raw[1])
                if period_file is None
                else int(Path(period_file).read_text())
            )
            allowed = int(quota) // period
            if allowed >= 1:
                return allowed
        except (OSError, ValueError, IndexError, ZeroDivisionError):
            continue
    return os.cpu_count() or 4


def _auto_intra_op_threads() -> int:
    """Threads for one inference when the deployment names no preference.

    Half the available cores, for the reason benchmarked below. An operator
    overrides it with `MODEL_INTRA_OP_THREADS`, which matters because that
    benchmark was run on one host size and the right ratio is a property of
    the machine, not of the graph.
    """
    configured = os.getenv("MODEL_INTRA_OP_THREADS", "").strip()
    if configured:
        try:
            if int(configured) >= 1:
                return int(configured)
        except ValueError:
            pass
        raise ModelUnreadyError("MODEL_INTRA_OP_THREADS must be a positive integer")
    return max(1, _available_cores() // 2)


class OnnxTokenClassificationPipeline:
    """CPU ONNX Runtime equivalent of Transformers' simple token classifier."""

    def __init__(
        self, model_path: str, max_model_tokens: int, intra_op_threads: int = 0
    ) -> None:
        import onnxruntime as ort
        from transformers import AutoConfig, AutoTokenizer

        path = Path(model_path)
        self.tokenizer = AutoTokenizer.from_pretrained(path, use_fast=True)
        config = AutoConfig.from_pretrained(path)
        self._labels = {int(key): str(value) for key, value in config.id2label.items()}
        self._max_length = max_model_tokens + 2
        # The default CPU arena over-allocates and retains a growing memory
        # pool for reuse across runs. Disabling it costs some speed but keeps
        # peak RSS close to the graph's actual working set.
        session_options = ort.SessionOptions()
        session_options.enable_cpu_mem_arena = False
        # ONNX Runtime otherwise defaults intra-op threads to every visible
        # core. Each call scores one short document at a time (batch size 1),
        # so the full core count adds thread-pool synchronization overhead
        # without more matmul work to spread across it. A controlled
        # benchmark on a 16-core host measured the unset default (16 threads)
        # at 90ms/call for Octo's XLM-R graph and 15.5ms/call for Berts'
        # MedRoBERTa graph, against 78ms and 12ms at half the core count.
        #
        # That ratio is a property of the host, not of the graph, so it is now
        # overridable -- and the core count it halves comes from the cgroup
        # quota rather than from the host, which are the same number only
        # until someone sets a CPU limit.
        session_options.intra_op_num_threads = (
            intra_op_threads if intra_op_threads >= 1 else _auto_intra_op_threads()
        )
        session_options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(path / "model.onnx"),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {item.name for item in self._session.get_inputs()}

    def __call__(self, texts: list[str] | str) -> list[list[dict[str, object]]]:
        values = [texts] if isinstance(texts, str) else texts
        if not values:
            return []
        encoded = self.tokenizer(
            values,
            padding=True,
            truncation=True,
            max_length=self._max_length,
            return_offsets_mapping=True,
            return_special_tokens_mask=True,
            return_tensors="np",
        )
        # The tokenizer's numpy output dtype is int32 or int64 depending on the
        # installed transformers version; the graph itself is exported with
        # int64 inputs, so a version drift here must not turn into a load-time
        # crash.
        feeds = {
            name: encoded[name].astype("int64", copy=False)
            for name in self._input_names
            if name in encoded
        }
        if feeds.keys() != self._input_names:
            raise ValueError("The ONNX model has unsupported required inputs.")
        logits = self._session.run(None, feeds)[0]
        return [
            self._entities(
                text,
                encoded["input_ids"][row],
                encoded["offset_mapping"][row],
                encoded["special_tokens_mask"][row],
                logits[row],
            )
            for row, text in enumerate(values)
        ]

    def _entities(
        self,
        text: str,
        # Indexed below, so `object` was untrue: these arrive as numpy arrays.
        # `Any` rather than an ndarray annotation because numpy is imported
        # inside the function -- the gateway image runs the document pipeline
        # without it, and a module-level import would pull it in everywhere.
        input_ids: Any,
        offsets: Any,
        special_tokens: Any,
        logits: Any,
    ) -> list[dict[str, object]]:
        import numpy as np

        score_rows = np.asarray(logits, dtype=np.float32)
        score_rows -= score_rows.max(axis=-1, keepdims=True)
        score_rows = np.exp(score_rows)
        score_rows /= score_rows.sum(axis=-1, keepdims=True)
        entities: list[dict[str, object]] = []
        for index, scores in enumerate(score_rows):
            if bool(special_tokens[index]):
                continue
            start, end = (int(value) for value in offsets[index])
            # A SentencePiece tokenizer marks a word start with U+2581 and
            # reports the offset of the space it replaced, so every token
            # after the first arrives one character long at the front.
            # CamemBERT does this and RoBERTa does not, which would leave one
            # model's spans opening on the space before the value and the
            # other's not -- a difference in masked bytes that has nothing to
            # do with what either model detected. No entity begins or ends on
            # whitespace in any tokenizer, so trimming here is a no-op for the
            # ones that already report it that way.
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if end <= start:
                continue
            label_index = int(scores.argmax())
            entities.append(
                {
                    "entity": self._labels[label_index],
                    "score": float(scores[label_index]),
                    "index": index,
                    "word": self.tokenizer.convert_ids_to_tokens(int(input_ids[index])),
                    "start": start,
                    "end": end,
                }
            )
        grouped = []
        for entity in self._group_entities(entities):
            if entity["entity_group"] == "O":
                continue
            start, end = self._whole_words(
                text, int(entity["start"]), int(entity["end"])
            )
            entity["start"], entity["end"] = start, end
            grouped.append(entity)
        return grouped

    def _group_entities(
        self, entities: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        groups: list[list[dict[str, object]]] = []
        for entity in entities:
            if not groups:
                groups.append([entity])
                continue
            prefix, label = self._tag(str(entity["entity"]))
            _, previous_label = self._tag(str(groups[-1][-1]["entity"]))
            if label == previous_label and prefix != "B":
                groups[-1].append(entity)
            else:
                groups.append([entity])
        return [self._group(group) for group in groups]

    def _group(self, entities: list[dict[str, object]]) -> dict[str, object]:
        return {
            "entity_group": str(entities[0]["entity"]).split("-", 1)[-1],
            "score": sum(float(entity["score"]) for entity in entities) / len(entities),
            "word": self.tokenizer.convert_tokens_to_string(
                [str(entity["word"]) for entity in entities]
            ),
            "start": entities[0]["start"],
            "end": entities[-1]["end"],
        }

    @staticmethod
    def _whole_words(text: str, start: int, end: int) -> tuple[int, int]:
        """Widen a span to the whole token it lands in the middle of.

        A token classifier predicts per subword token, and a subword is not a
        word: MedRoBERTa tags the `IN` of `INSZ` as the start of an address, and
        the `rijks` and `ister` of `rijksregisternummer`. Left alone, the router
        replaces exactly those characters, so a full street address is spliced
        into the middle of a word and the rest of it -- `SZ` -- is left standing
        next to it. The reader cannot tell that apart from an address that was
        really there, and the field label the cue word carried is destroyed.

        Widening is the safe direction. A model that misreads a cue word still
        over-masks it, which is visible in the output and can be measured; the
        alternative is a document that is quietly corrupt.

        **Digits count as inside a word.** This used to follow letters only,
        on the reasoning that a digit beside a name is its own value with its
        own detector. That reasoning does not survive contact with a
        mixed-alphanumeric token: a live run left the `1` of a house number
        `12` and the `A` of `A12` standing beside a replaced remainder, which
        is the same quietly-corrupt output the letters case exists to prevent.
        The stop condition is unchanged -- whitespace and punctuation still end
        a word -- so this only decides which characters are *inside* one.
        Both directions follow the same rule: a trailing unit number or
        postcode fragment is pulled in exactly as a leading one is.
        """
        while start > 0 and text[start].isalnum() and text[start - 1].isalnum():
            start -= 1
        while end < len(text) and text[end - 1].isalnum() and text[end].isalnum():
            end += 1
        return start, end

    @staticmethod
    def _tag(label: str) -> tuple[str, str]:
        if label.startswith("B-"):
            return "B", label[2:]
        if label.startswith("I-"):
            return "I", label[2:]
        return "I", label


class OnnxTokenClassifierEngine:
    """Token classifier backed by a float32 CPU ONNX Runtime session."""

    def __init__(
        self,
        model_path: str,
        model_version: str,
        confidence_threshold: float,
        entity_confidence_thresholds: dict[str, float] | None = None,
        accelerator: str = "cpu",
        model_sha256: str = "",
        max_model_tokens: int = 510,
        model_stride_tokens: int = 192,
    ) -> None:
        if accelerator == "cuda":
            raise ModelUnreadyError("The ONNX model is configured for CPU only.")
        self.model_path = model_path
        self.model_version = model_version
        self.confidence_threshold = confidence_threshold
        self.entity_confidence_thresholds = entity_confidence_thresholds or {}
        self.accelerator = "cpu"
        self.model_sha256 = ""
        self.max_model_tokens = max_model_tokens
        self.model_stride_tokens = model_stride_tokens
        self._pipe = None
        try:
            self.model_sha256 = onnx_model_checksum(model_path)
            if model_sha256 and self.model_sha256 != model_sha256.lower():
                raise ValueError(
                    "ONNX model checksum does not match ONNX_MODEL_SHA256."
                )
            self._load()
            self._warmup()
        except ModelUnreadyError:
            raise
        except Exception as exc:  # pragma: no cover - convert startup failures
            log.error("onnx_model_load_failed model_version=%s", self.model_version)
            raise ModelUnreadyError("ONNX model failed to load.") from exc
        log.info(
            "onnx_model_loaded model_version=%s device=cpu threshold=%.3f",
            self.model_version,
            self.confidence_threshold,
        )

    @property
    def ready(self) -> bool:
        return self._pipe is not None

    def _load(self) -> None:
        self._pipe = OnnxTokenClassificationPipeline(
            self.model_path, self.max_model_tokens
        )

    def _warmup(self) -> None:
        assert self._pipe is not None  # noqa: S101  # Startup loads the pipeline.
        self._pipe("warmup")

    def infer(self, text: str) -> list[MaskSpan]:
        """Return entity spans (character offsets) at or above threshold."""
        if not self.ready:
            raise ModelUnreadyError("Model is not ready.")
        if not text.strip():
            return []
        assert self._pipe is not None  # noqa: S101  # Startup loads the pipeline.
        spans: list[MaskSpan] = []
        try:
            for slice_start, chunk in _document_slices(text):
                windows = self._text_windows(chunk)
                for (window_start, _), entities in zip(
                    windows, self._pipe([w for _, w in windows]), strict=True
                ):
                    offset = slice_start + window_start
                    for ent in entities or []:
                        score = float(ent.get("score", 0.0))
                        label = str(ent.get("entity_group", ent.get("entity", "")))
                        threshold = self.entity_confidence_thresholds.get(
                            label.upper(), self.confidence_threshold
                        )
                        if score < threshold:
                            continue
                        start = offset + int(ent["start"])
                        end = offset + int(ent["end"])
                        if end > start:
                            spans.append(
                                MaskSpan(
                                    start,
                                    end,
                                    label,
                                    "model",
                                    confidence=score,
                                    source_layer=self.model_path,
                                )
                            )
        except Exception as exc:
            log.error("inference_failed")
            raise InferenceFailureError("Inference failed.") from exc

        unique = {(span.start, span.end, span.label): span for span in spans}
        result = sorted(
            unique.values(), key=lambda span: (span.start, span.end, span.label)
        )
        log.info("inference_ok spans=%d", len(result))
        return result

    def infer_fields(self, text: str, threshold: float) -> list[MaskSpan]:
        """Return spans found by scoring each structured field on its own.

        Structured input hides entities from the whole-document pass: the same
        name scores 0.90 inside a CSV row and 0.9997 as a bare field. Fields
        carry a separate, lower threshold because a value standing alone in a
        record is far more likely to be an identifier than a word in prose.
        """
        if not self.ready:
            raise ModelUnreadyError("Model is not ready.")
        fields = structured_fields(text)
        if not fields:
            return []
        assert self._pipe is not None  # noqa: S101  # Startup loads the pipeline.

        spans: list[MaskSpan] = []
        try:
            for (start, end), entities in zip(
                fields, self._pipe([text[s:e] for s, e in fields]), strict=True
            ):
                for ent in entities or []:
                    score = float(ent.get("score", 0.0))
                    if score < threshold:
                        continue
                    label = str(ent.get("entity_group", ent.get("entity", "")))
                    span_start = start + int(ent["start"])
                    span_end = min(start + int(ent["end"]), end)
                    if span_end > span_start:
                        spans.append(
                            MaskSpan(
                                span_start,
                                span_end,
                                label,
                                "model",
                                confidence=score,
                                source_layer=self.model_path,
                            )
                        )
        except Exception as exc:
            log.error("field_inference_failed")
            raise InferenceFailureError("Inference failed.") from exc

        log.info("field_inference_ok fields=%d spans=%d", len(fields), len(spans))
        return spans

    def _text_windows(self, text: str) -> list[tuple[int, str]]:
        assert self._pipe is not None  # noqa: S101  # Startup loads the pipeline.
        encoded = self._pipe.tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        offsets = encoded.get("offset_mapping") or []
        if len(offsets) <= self.max_model_tokens:
            return [(0, text)]

        step = self.max_model_tokens - self.model_stride_tokens
        windows = []
        for token_start in range(0, len(offsets), step):
            token_end = min(token_start + self.max_model_tokens, len(offsets))
            char_start = int(offsets[token_start][0])
            char_end = int(offsets[token_end - 1][1])
            if char_end > char_start:
                windows.append((char_start, text[char_start:char_end]))
            if token_end == len(offsets):
                break
        return windows
