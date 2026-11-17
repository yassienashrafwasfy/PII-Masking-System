"""Shared extraction and redaction pipeline for private model services."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.error_codes import InferenceFailureError
from ..core.locale_detection import LocaleVerdict, confidence_report, resolve_locale
from ..core.schemas import LocaleBadge, MaskSpan
from ..engines.masking_router import MaskingRouter
from .json_masking_service import mask_json
from .pdf_extraction import PdfGlyph, extract_pdf_pages
from .pdf_masking_service import mask_pdf
from .pdf_ocr import DEFAULT_BOUNDS, OcrBounds, OcrError, PageReader, inject_text_layer
from .text_masking_service import decode_utf8, mask_txt


@dataclass(frozen=True)
class DocumentText:
    """One detector input and the page whose offsets it represents."""

    text: str
    page: int | None
    glyphs: tuple[PdfGlyph | None, ...] = ()


@dataclass(frozen=True)
class PreparedDocument:
    """Original bytes plus the shared reading-order text supplied to a model."""

    content: bytes
    content_type: str
    texts: tuple[DocumentText, ...]


def prepare_document(
    content: bytes,
    content_type: str,
    *,
    ocr: PageReader | None = None,
    ocr_bounds: OcrBounds = DEFAULT_BOUNDS,
) -> PreparedDocument:
    """Validate and extract the model inputs for one supported document.

    *ocr_bounds* carries the per-page OCR limits an operator configured.
    Leaving it out keeps every documented default, which is what this call
    silently did for all of them before the argument existed.

    With *ocr* given, a PDF page that yields no characters of its own is read
    by OCR instead of refused, so a scan can be masked. It is a fallback per
    page: a page with a text layer is read from that layer alone, and an image
    on such a page still refuses the document. Passing `None` -- the default,
    and what `PDF_OCR=off` resolves to -- leaves behaviour exactly as it was,
    image rejection included.
    """
    if content_type == "application/pdf":
        ocr_regions: tuple = ()
        try:
            if ocr is not None:
                # Give the scan a real text layer first, then treat it as an
                # ordinary PDF. Everything downstream -- extraction, reading
                # order, run grouping, font resolution, chip sizing, the
                # redaction renderer -- then works unchanged, and the masked
                # output is searchable rather than a picture of one.
                #
                # `content` becomes the augmented bytes on purpose: `mask_pdf`
                # re-opens them, and the surrogate seed is their digest. Both
                # stay deterministic because the injection is.
                content, ocr_regions = inject_text_layer(
                    content, ocr, bounds=ocr_bounds
                )
            pages = extract_pdf_pages(content, ocr_regions=ocr_regions)
        except OcrError as exc:
            # A fault on this side, not a document the caller got wrong. 500
            # rather than 422 keeps the distinction visible to an operator and
            # keeps the request retryable.
            raise InferenceFailureError(
                f"OCR could not read the document: {exc}"
            ) from exc
        if not pages or not any(page.text.strip() for page in pages):
            # Unchanged message, and still the right answer: a document nobody
            # could read might carry PII, and returning it unmasked is the one
            # outcome that must not happen.
            raise ValueError("PDF has no extractable text")
        return PreparedDocument(
            content,
            content_type,
            tuple(DocumentText(page.text, page.index, page.glyphs) for page in pages),
        )

    kind = {
        "text/plain": "TXT",
        "text/csv": "CSV",
        "application/json": "JSON",
    }.get(content_type)
    if kind is None:
        raise ValueError("unsupported content type")
    return PreparedDocument(
        content, content_type, (DocumentText(decode_utf8(content, kind), None),)
    )


def _language_sample(document: PreparedDocument) -> str:
    """Return the document text detection reads, in page order.

    Joined in the order extraction produced, so the sample is a fixed
    function of the document and two runs read the same characters.
    """
    return "\n".join(text.text for text in document.texts)


def document_verdict(
    document: PreparedDocument, locale: str | None = None
) -> LocaleVerdict:
    """Resolve, once, which language *document* is masked in.

    A detector that picks its checkpoint by language needs the verdict
    *before* detection, and the surrogates need it after. Both take it from
    here and pass the result on: detection runs once per document, and the
    two halves cannot answer differently.

    Resolving it per document rather than per extracted text is the point. A
    single page of a letter carries too few letters to detect, and a document
    that changed detector halfway through would mask its second half by
    different rules than its first.
    """
    return resolve_locale(_language_sample(document), pinned=locale)


def redact_document(
    document: PreparedDocument,
    spans: list[MaskSpan],
    audit: list[dict[str, object]] | None = None,
    *,
    organization_pool: str = "medical",
    redaction_style: str = "invisible",
    redaction_mode: str = "pseudonymize",
    chip_reflow: bool = True,
    locale: str | LocaleVerdict | None = None,
    locale_badge: bool = False,
) -> bytes:
    """Apply the shared deterministic replacement policy to original bytes.

    *locale* decides which language every surrogate reads as. Leave it None,
    which is the normal case, and it is detected from the document's own
    extracted text: a pseudonym has to read like the document it replaced a
    value in, and that is a property of the document rather than of the
    deployment. Pass a locale string to override detection, which is what the
    tests that pin one locale's output do. Pass a `LocaleVerdict` -- what
    `document_verdict` returns -- when the caller has already resolved it,
    and no second detection runs.

    *chip_reflow* lets a replacement that wants more width than the value it
    covers take it from the rest of its line. It reaches only the PDF path,
    and every redaction style there; see `pdf_line_reflow`.

    *locale_badge* stamps the per-language confidence scores on the first
    page of a PDF. It is a diagnostic overlay for reviewing why a document
    was masked in the language it was, and it changes no replacement.
    """
    verdict = (
        locale
        if isinstance(locale, LocaleVerdict)
        else document_verdict(document, locale)
    )
    badge = None
    if locale_badge:
        # "Detected" means the detector chose this locale, as opposed to the
        # fallback taking it or the caller pinning it; the overlay draws the
        # three differently. The verdict already carries that fact, so the
        # badge reports it rather than inferring it from the scores.
        badge = LocaleBadge(
            entries=confidence_report(_language_sample(document)),
            resolved=verdict.locale,
            detected=verdict.detected,
        )
    router = MaskingRouter(
        document.content,
        audit,
        organization_pool=organization_pool,
        locale=verdict.locale,
    )
    if document.content_type == "application/pdf":
        return mask_pdf(
            document.content,
            spans,
            router,
            [text.glyphs for text in document.texts],
            redaction_style=redaction_style,
            redaction_mode=redaction_mode,
            chip_reflow=chip_reflow,
            locale_badge=badge,
        )
    if document.content_type == "application/json":
        return mask_json(document.content, spans, router)
    return mask_txt(document.content, spans, router)
