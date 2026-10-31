"""Deliberately narrow key/value parsers; storing a file does not imply parsing it."""
from __future__ import annotations

from io import BytesIO
import re
from zipfile import ZipFile

from core.errors import CrossBotError, ErrorCode, ParseError
from core.models import ExtractedField, FieldValueStatus
from .field_mapping import from_ocr_lines, from_text_lines
from .ocr import OCRBackend, OCRInputError, OCRUnavailableError

FIELD_NAMES = frozenset({
    "order_no", "customer_name", "destination_country", "product_name", "description",
    "quantity", "packages", "gross_weight_kg", "net_weight_kg", "currency",
    "total_amount", "invoice_total", "incoterm", "shipper", "consignee",
    "country_of_origin", "hs_code", "grade_quality", "marks_and_numbers", "package_contents",
})
ALIASES = {"description": "product_name", "invoice_total": "total_amount"}
PARSER_VERSION = "w3-fixed-ocr-2"


class FixedTemplateParser:
    """PDF: `field_name: value` on a line. XLSX: keys in A, values in B."""

    def parse(self, content: bytes, extension: str) -> list[ExtractedField]:
        if extension == ".xls":
            try:
                from .xls_parser import parse_xls
                return parse_xls(content)
            except ImportError as exc:
                raise CrossBotError(ErrorCode.CONFIGURATION, "旧版 Excel 解析依赖未安装。") from exc
        if extension == ".pdf":
            return self._pdf(content)
        if extension == ".xlsx":
            from .office_parser import parse_xlsx
            try:
                return parse_xlsx(content)
            except ParseError as office_error:
                try:
                    return self._xlsx(content)
                except ParseError:
                    raise office_error
        if extension == ".docx":
            from .office_parser import parse_docx
            return parse_docx(content)
        if extension in {".jpg", ".jpeg", ".png", ".webp"}:
            return self._image(content)
        raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                            "该格式已保存原件，暂不支持字段解析。")

    @staticmethod
    def _candidate(name: str, value, **location) -> ExtractedField:
        if value is None or (isinstance(value, str) and not value.strip()):
            value, status = None, FieldValueStatus.MISSING
        else:
            value = str(value).strip()
            status = FieldValueStatus.CANDIDATE
        return ExtractedField(name=ALIASES.get(name, name), value=value,
                              normalized_value=value, status=status,
                              confidence=1.0 if value is not None else None,
                              note="固定模板字段匹配；数值仍须人工复核。", **location)

    @staticmethod
    def _finish(fields: list[ExtractedField]) -> list[ExtractedField]:
        if not fields:
            raise ParseError("未找到支持的固定模板字段，请保留原件并人工补充资料。")
        names = [item.name for item in fields]
        if len(names) != len(set(names)):
            raise ParseError("模板包含重复字段，无法确定唯一候选值。")
        return fields

    def _pdf(self, content: bytes) -> list[ExtractedField]:
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ParseError("不支持加密或超过 100 页的 PDF。")
            fields, lines, total_chars = [], [], 0
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total_chars += len(text)
                if total_chars > 1_000_000:
                    raise ParseError("PDF 文本超过固定模板解析上限。")
                for line in text.splitlines():
                    lines.append((line, number))
                    match = re.fullmatch(r"\s*([a-z_]+)\s*[:：=]\s*(.*?)\s*", line)
                    if match and match[1] in FIELD_NAMES:
                        fields.append(self._candidate(match[1], match[2], page_number=number))
            if not fields:
                fields = from_text_lines(lines)
            if not fields:
                ocr = OCRBackend().recognize_pdf(content, max_pages=100)
                fields = from_ocr_lines(ocr.lines)
            return self._finish(fields)
        except ImportError as exc:
            raise CrossBotError(ErrorCode.CONFIGURATION, "PDF 解析依赖未安装。") from exc
        except OCRUnavailableError as exc:
            raise CrossBotError(ErrorCode.CONFIGURATION,
                                "扫描件解析需要安装 RapidOCR 和 PDFium 依赖。") from exc
        except OCRInputError as exc:
            raise ParseError(str(exc)) from exc
        except CrossBotError:
            raise
        except Exception as exc:
            raise ParseError("PDF 无法读取；扫描件 OCR 和任意版式暂不支持。") from exc

    def _xlsx(self, content: bytes) -> list[ExtractedField]:
        try:
            with ZipFile(BytesIO(content)) as package:
                entries = package.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 50_000_000:
                    raise ParseError("工作簿解压大小或文件数量超过解析上限。")
            from openpyxl import load_workbook
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=False,
                                     keep_links=False)
            try:
                fields = []
                if len(workbook.worksheets) > 30:
                    raise ParseError("工作簿超过 30 个工作表。")
                for sheet in workbook.worksheets:
                    if sheet.max_row and sheet.max_row > 5000:
                        raise ParseError("工作表超过 5000 行固定模板解析上限。")
                    for row in sheet.iter_rows(min_col=1, max_col=2,
                                               max_row=min(sheet.max_row or 5000, 5000)):
                        key, value = row
                        name = str(key.value or "").strip()
                        if name not in FIELD_NAMES:
                            continue
                        if value.data_type in {"f", "e"}:
                            raise ParseError("候选字段包含公式或单元格错误，请提供确定值。")
                        fields.append(self._candidate(name, value.value,
                                                      sheet_name=sheet.title,
                                                      cell_reference=value.coordinate))
                return self._finish(fields)
            finally:
                workbook.close()
        except ImportError as exc:
            raise CrossBotError(ErrorCode.CONFIGURATION, "Excel 解析依赖未安装。") from exc
        except CrossBotError:
            raise
        except Exception as exc:
            raise ParseError("工作簿无法读取；仅支持 .xlsx 固定模板，旧 .xls 仅托管原件。") from exc

    def _image(self, content: bytes) -> list[ExtractedField]:
        try:
            fields = from_ocr_lines(OCRBackend().recognize_image(content))
            return self._finish(fields)
        except OCRUnavailableError as exc:
            raise CrossBotError(ErrorCode.CONFIGURATION,
                                "图片字段解析需要安装 RapidOCR 依赖。") from exc
        except OCRInputError as exc:
            raise ParseError(str(exc)) from exc

