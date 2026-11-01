"""Optional CPU OCR for scanned W3 images and PDF pages.

Uses RapidOCR when installed, or the local Windows OCR engine as a fallback.
No OCR content is logged or persisted by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from threading import RLock

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
MAX_IMAGE_SIDE = 12_000
MAX_PDF_PAGES = 100
MAX_PDF_PIXELS = 80_000_000
MAX_TEXT_CHARS = 20_000
RENDER_DPI = 144


class OCRUnavailableError(RuntimeError):
    """The optional OCR or PDF rendering dependencies are missing."""


class OCRInputError(ValueError):
    """Input could not be safely decoded within supported limits."""


@dataclass(frozen=True, slots=True)
class RecognizedLine:
    text: str
    confidence: float | None
    bbox: tuple[float, float, float, float]
    page_number: int | None = None


@dataclass(frozen=True, slots=True)
class PDFOCRResult:
    lines: tuple[RecognizedLine, ...]
    skipped_text_pages: tuple[int, ...]


class OCRBackend:
    """Lazy local OCR using RapidOCR or the installed Windows language engine."""

    def __init__(self):
        self._engine = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
            return True
        except ImportError:
            from providers.windows_ocr import WindowsOCREngine
            return WindowsOCREngine.available()

    def _get_engine(self):
        with self._lock:
            if self._engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    from providers.windows_ocr import WindowsOCREngine
                    if not WindowsOCREngine.available():
                        raise OCRUnavailableError(
                            "Install RapidOCR or enable a local Windows OCR language."
                        ) from exc
                    self._engine = WindowsOCREngine()
                    return self._engine
                # RapidOCR uses the bundled PP-OCR model files and ONNX CPU runtime.
                self._engine = RapidOCR()
            return self._engine

    @staticmethod
    def _decode_image(content: bytes):
        try:
            from PIL import Image, ImageOps
            with Image.open(BytesIO(content)) as source:
                if source.width > MAX_IMAGE_SIDE or source.height > MAX_IMAGE_SIDE:
                    raise OCRInputError("Image dimensions exceed the OCR limit.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise OCRInputError("Image pixel count exceeds the OCR limit.")
                source.verify()
            with Image.open(BytesIO(content)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise OCRInputError("Image pixel count exceeds the OCR limit.")
            return image
        except OCRInputError:
            raise
        except Exception as exc:
            raise OCRInputError("Image data is invalid or unsupported.") from exc

    @staticmethod
    def _validate_bytes(content: bytes) -> None:
        if not isinstance(content, bytes) or not content:
            raise OCRInputError("OCR input must be non-empty bytes.")
        if len(content) > MAX_INPUT_BYTES:
            raise OCRInputError("OCR input exceeds the 20 MB limit.")

    def recognize_image(self, content: bytes, *, page_number: int | None = None) -> list[RecognizedLine]:
        self._validate_bytes(content)
        image = self._decode_image(content)
        try:
            results, _elapsed = self._get_engine()(image)
        except OCRUnavailableError:
            raise
        except Exception as exc:
            raise OCRInputError("OCR could not process this image.") from exc
        lines, total = [], 0
        for row in results or []:
            try:
                polygon, text, confidence = row
                clean = " ".join(str(text).split())[:1000]
                if not clean:
                    continue
                total += len(clean)
                if total > MAX_TEXT_CHARS:
                    raise OCRInputError("OCR output exceeds the text limit.")
                points = [(float(point[0]), float(point[1])) for point in polygon]
                if len(points) < 4:
                    continue
                xs, ys = zip(*points)
                score = None if confidence is None else max(0.0, min(1.0, float(confidence)))
                lines.append(RecognizedLine(clean, score,
                    (min(xs), min(ys), max(xs), max(ys)), page_number))
            except OCRInputError:
                raise
            except (TypeError, ValueError, IndexError):
                continue
        return lines

    def recognize_pdf(self, content: bytes, *, max_pages: int = MAX_PDF_PAGES) -> PDFOCRResult:
        self._validate_bytes(content)
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PDF_PAGES:
            raise OCRInputError(f"max_pages must be between 1 and {MAX_PDF_PAGES}.")
        try:
            from pypdf import PdfReader
            import pypdfium2 as pdfium
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or not reader.pages:
                raise OCRInputError("Encrypted or empty PDFs are not supported.")
            if len(reader.pages) > max_pages:
                raise OCRInputError("PDF page count exceeds the configured OCR limit.")
            document = pdfium.PdfDocument(content)
        except OCRInputError:
            raise
        except ImportError as exc:
            raise OCRUnavailableError(
                "Install pypdf, pypdfium2 and Pillow, and enable a local OCR backend."
            ) from exc
        except Exception as exc:
            raise OCRInputError("PDF data is invalid or unsupported.") from exc

        lines, skipped, pixel_budget = [], [], 0
        try:
            for index, pdf_page in enumerate(document, start=1):
                try:
                    text = reader.pages[index - 1].extract_text() or ""
                except Exception:
                    text = ""
                # Text extraction stays with the PDF parser. OCR never duplicates or
                # overwrites a page that already has selectable text.
                if text.strip():
                    skipped.append(index)
                    continue
                page = pdf_page
                width, height = page.get_size()
                estimated = int(width * RENDER_DPI / 72) * int(height * RENDER_DPI / 72)
                if estimated <= 0 or estimated > MAX_IMAGE_PIXELS:
                    raise OCRInputError("A PDF page exceeds the OCR pixel limit.")
                pixel_budget += estimated
                if pixel_budget > MAX_PDF_PIXELS:
                    raise OCRInputError("PDF OCR total pixel budget exceeded.")
                bitmap = page.render(scale=RENDER_DPI / 72)
                image = bitmap.to_pil().convert("RGB")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise OCRInputError("Rendered PDF page exceeds the OCR pixel limit.")
                lines.extend(self._recognize_pil(image, index))
                if sum(len(line.text) for line in lines) > MAX_TEXT_CHARS:
                    raise OCRInputError("PDF OCR output exceeds the text limit.")
        except OCRInputError:
            raise
        except Exception as exc:
            raise OCRInputError("PDF page could not be rendered or recognized.") from exc
        finally:
            close = getattr(document, "close", None)
            if close:
                close()
        return PDFOCRResult(tuple(lines), tuple(skipped))

    def _recognize_pil(self, image, page_number: int) -> list[RecognizedLine]:
        try:
            results, _elapsed = self._get_engine()(image)
        except OCRUnavailableError:
            raise
        except Exception as exc:
            raise OCRInputError("OCR could not process a rendered PDF page.") from exc
        lines = []
        for row in results or []:
            try:
                polygon, text, confidence = row
                clean = " ".join(str(text).split())[:1000]
                if not clean:
                    continue
                points = [(float(point[0]), float(point[1])) for point in polygon]
                if len(points) < 4:
                    continue
                xs, ys = zip(*points)
                score = None if confidence is None else max(0.0, min(1.0, float(confidence)))
                lines.append(RecognizedLine(clean, score,
                    (min(xs), min(ys), max(xs), max(ys)), page_number))
            except (TypeError, ValueError, IndexError):
                continue
        return lines
