"""Public API of the shared platform masking engine.

Everything a model service, a team runtime, or a test needs is re-exported
here, so consumers import one name instead of walking the internal layout::

    from masking_engine import MaskSpan, MaskingRouter, prepare_document

The module paths under ``masking_engine.app`` and ``masking_engine.src`` are
implementation detail: they may be reorganised without a contract change.
Import them directly only for names this module does not export -- private
helpers a test pins on purpose.

Model-engine names (``OnnxTokenClassifierEngine`` and friends) resolve lazily,
so importing this package never pulls ONNX Runtime into a process that only
runs the document pipeline -- the gateway image, for one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .app.core import label_registry, locales
from .app.core.config import Settings, load_settings, unknown_release_fields
from .app.core.logging_config import configure_logging
from .app.core.error_codes import (
    ERROR_CODES,
    RETRYABLE_STATUS,
    CorruptInputError,
    InferenceFailureError,
    MaskingError,
    ModelUnreadyError,
    UnsupportedMediaTypeError,
    error_response,
)
from .app.core.schemas import MaskSource, MaskSpan
from .app.engines.label_converter import LabelConverter, UnknownLabelError
from .app.engines.masking_router import MaskingRouter, Replacement
from .app.engines.regex_arbitration import coalesce_spans
from .app.engines.orchestrator import Orchestrator
from .app.engines.regex_engine import find_regex_spans, find_regex_spans_pages
from .app.engines import span_shape
from .app.services import pdf_redaction_styles
from .app.services.document_pipeline import (
    DocumentText,
    PreparedDocument,
    document_verdict,
    prepare_document,
    redact_document,
)
from .app.services.json_masking_service import mask_json
from .app.services.pdf_extraction import ExtractedPdfPage, PdfGlyph, extract_pdf_pages
from .app.services.pdf_masking_service import mask_pdf
from .app.services.pdf_ocr import (
    OcrBounds,
    OcrError,
    OcrLine,
    PageReader,
    reader_for,
)
from .app.services.text_masking_service import apply_replacements, decode_utf8, mask_txt
from .runtime import detect, mask, ready, release, supported_model_formats, unload

if TYPE_CHECKING:  # pragma: no cover - import-time cost is the point
    from .src.models.xlm_r.model_engine import (
        OnnxTokenClassificationPipeline,
        OnnxTokenClassifierEngine,
        clip_spans_to_fields,
        onnx_model_checksum,
        structured_fields,
    )

_MODEL_ENGINE_EXPORTS = frozenset(
    {
        "OnnxTokenClassificationPipeline",
        "OnnxTokenClassifierEngine",
        "clip_spans_to_fields",
        "onnx_model_checksum",
        "structured_fields",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve the model-engine exports on first use."""
    if name in _MODEL_ENGINE_EXPORTS:
        from .src.models.xlm_r import model_engine

        return getattr(model_engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    # Types
    "MaskSpan",
    "MaskSource",
    "DocumentText",
    "PreparedDocument",
    "ExtractedPdfPage",
    "PdfGlyph",
    "OcrBounds",
    "OcrLine",
    "PageReader",
    "Replacement",
    "Settings",
    # Errors
    "MaskingError",
    "CorruptInputError",
    "OcrError",
    "InferenceFailureError",
    "ModelUnreadyError",
    "UnsupportedMediaTypeError",
    "UnknownLabelError",
    "ERROR_CODES",
    "RETRYABLE_STATUS",
    "error_response",
    # Configuration and taxonomy
    "configure_logging",
    "load_settings",
    "unknown_release_fields",
    "label_registry",
    "locales",
    # Detection and arbitration
    "LabelConverter",
    "MaskingRouter",
    "Orchestrator",
    "coalesce_spans",
    "find_regex_spans",
    "find_regex_spans_pages",
    "span_shape",
    # Document pipeline
    "document_verdict",
    "prepare_document",
    "reader_for",
    "redact_document",
    "extract_pdf_pages",
    "mask_json",
    "mask_pdf",
    "mask_txt",
    "apply_replacements",
    "decode_utf8",
    "pdf_redaction_styles",
    # Octo runtime (the adapter API)
    "detect",
    "mask",
    "ready",
    "release",
    "supported_model_formats",
    "unload",
    # Model engines (lazily resolved)
    "OnnxTokenClassificationPipeline",
    "OnnxTokenClassifierEngine",
    "clip_spans_to_fields",
    "onnx_model_checksum",
    "structured_fields",
]
