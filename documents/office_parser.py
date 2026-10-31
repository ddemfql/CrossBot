"""Bounded, allowlisted candidate extraction from XLSX and DOCX files."""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import re
from zipfile import BadZipFile, ZipFile

from core.errors import CrossBotError, ErrorCode, ParseError
from core.models import ExtractedField, FieldValueStatus

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000
MAX_SHEETS = 30
MAX_ROWS = 5000
MAX_COLUMNS = 256
MAX_TEXT_CHARS = 200_000


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


_LABELS = {
    "order_no": ("order no", "order number", "po no", "po number", "purchase order no",
                 "purchase order number", "订单号", "采购订单号", "采购单号"),
    "customer_name": ("buyer", "buyer name", "customer", "customer name", "bill to",
                      "买方", "客户", "客户名称"),
    "destination_country": ("destination country", "country of destination", "ship to country",
                             "目的国", "目的地国家"),
    "product_name": ("product", "product name", "item description", "description", "品名",
                     "产品名称", "商品名称"),
    "quantity": ("quantity", "qty", "order quantity", "数量", "订购数量"),
    "unit": ("unit", "uom", "单位"),
    "unit_price": ("unit price", "price per unit", "单价"),
    "currency": ("currency", "币种"),
    "total_amount": ("total amount", "invoice total", "grand total", "总金额", "金额合计", "总价"),
    "incoterm": ("incoterm", "incoterms", "trade terms", "贸易条款"),
    "packages": ("packages", "package count", "cartons", "carton count", "boxes", "箱数", "包装件数"),
    "gross_weight_kg": ("gross weight", "gross weight kg", "毛重", "毛重kg"),
    "net_weight_kg": ("net weight", "net weight kg", "净重", "净重kg"),
    "document_date": ("date", "document date", "order date", "purchase order date", "日期", "订单日期"),
    "shipper": ("shipper", "consignor", "发货人"),
    "consignee": ("consignee", "收货人"),
    "country_of_origin": ("country of origin", "origin country", "原产国"),
    "hs_code": ("hs code", "htsus", "harmonized code", "海关编码"),
    "grade_quality": ("grade", "quality", "grade or quality", "等级", "质量"),
    "marks_and_numbers": ("marks and numbers", "shipping marks", "唛头", "箱号"),
    "package_contents": ("package contents", "contents per package", "包装内容"),
    "shipper": ("seller", "seller name", "supplier", "卖方", "供应商"),
}
_CANONICAL = {_normalize(alias): name for name, aliases in _LABELS.items()
              for alias in (name, *aliases)}


def _label(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if ":" in text or "：" in text or "=" in text:
        text = re.split(r"[:：=]", text, maxsplit=1)[0].strip()
    return _CANONICAL.get(_normalize(text))


def _candidate(name: str, value, *, sheet: str, cell: str) -> ExtractedField:
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    if value is None or (isinstance(value, str) and not value.strip()):
        value, status, confidence = None, FieldValueStatus.MISSING, None
    elif isinstance(value, str):
        value, status, confidence = value.strip(), FieldValueStatus.CANDIDATE, 0.9
    else:
        value, status, confidence = str(value), FieldValueStatus.CANDIDATE, 0.9
    return ExtractedField(name=name, value=value, normalized_value=value,
        status=status, confidence=confidence, sheet_name=sheet, cell_reference=cell,
        note="Office 标签邻接候选，须逐项人工复核。")


def _unique(fields: list[ExtractedField]) -> list[ExtractedField]:
    names = [field.name for field in fields]
    if len(names) != len(set(names)):
        raise ParseError("Office 文档包含重复候选字段，无法确定唯一来源。")
    if not fields:
        raise ParseError("未找到受支持的业务字段标签；原件已保留，可人工复核。")
    return fields


def _check_package(content: bytes, allowed_roots: tuple[str, ...]) -> None:
    if not isinstance(content, bytes) or not content:
        raise ParseError("Office 文档内容为空。")
    if len(content) > MAX_INPUT_BYTES:
        raise CrossBotError(ErrorCode.FILE_TOO_LARGE, "Office 文档超过 20 MB。")
    try:
        with ZipFile(BytesIO(content)) as package:
            infos = package.infolist()
            if (len(infos) > MAX_ARCHIVE_FILES
                    or sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES):
                raise ParseError("Office 文档解压大小或成员数量超过解析上限。")
            if not any(item.filename.startswith(allowed_roots) for item in infos):
                raise ParseError("Office 文档容器结构无效。")
    except ParseError:
        raise
    except (BadZipFile, OSError, ValueError) as exc:
        raise ParseError("Office 文档容器无效或已损坏。") from exc


def parse_xlsx(content: bytes) -> list[ExtractedField]:
    """Read exact allowlisted labels and their adjacent right-hand cell."""
    _check_package(content, ("xl/workbook.xml",))
    try:
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False,
                                 keep_links=False)
    except ImportError as exc:
        raise CrossBotError(ErrorCode.CONFIGURATION, "XLSX 解析依赖 openpyxl 未安装。") from exc
    except Exception as exc:
        raise ParseError("XLSX 工作簿无法读取。") from exc

    fields = []
    total_text = 0
    try:
        if len(workbook.worksheets) > MAX_SHEETS:
            raise ParseError("工作簿超过 30 个工作表的解析上限。")
        for sheet in workbook.worksheets:
            if (sheet.max_row and sheet.max_row > MAX_ROWS) or (sheet.max_column and sheet.max_column > MAX_COLUMNS):
                raise ParseError("工作表超过 5000 行或 256 列的解析上限。")
            for row in sheet.iter_rows(max_row=min(sheet.max_row or MAX_ROWS, MAX_ROWS),
                                       max_col=min(sheet.max_column or MAX_COLUMNS, MAX_COLUMNS)):
                for index, cell in enumerate(row):
                    label = _label(cell.value)
                    if not label:
                        continue
                    value_cell = row[index + 1] if index + 1 < len(row) else None
                    # Column headings with another known label beside them are not
                    # treated as label/value pairs.
                    if value_cell is not None and _label(value_cell.value):
                        value_cell = None
                    value = None if value_cell is None else value_cell.value
                    if value_cell is not None and value_cell.data_type == "f":
                        value = None
                    if value_cell is not None and value_cell.data_type == "e":
                        raise ParseError("候选字段相邻单元格包含错误值，无法安全读取。")
                    total_text += len(str(cell.value or "")) + len(str(value or ""))
                    if total_text > MAX_TEXT_CHARS:
                        raise ParseError("工作簿字段文本超过解析上限。")
                    fields.append(_candidate(label, value, sheet=sheet.title,
                                              cell=value_cell.coordinate if value_cell is not None else cell.coordinate))
    finally:
        workbook.close()
    return _unique(fields)


def _paragraph_field(text: str, *, location: str) -> ExtractedField | None:
    match = re.match(r"^\s*(.{1,80}?)\s*[:：=]\s*(.*?)\s*$", text)
    if not match:
        return None
    name = _label(match.group(1))
    if not name:
        return None
    return _candidate(name, match.group(2) or None, sheet="document", cell=location)


def parse_docx(content: bytes) -> list[ExtractedField]:
    """Read exact allowlisted label/value table cells and labeled paragraphs."""
    _check_package(content, ("word/document.xml",))
    try:
        from docx import Document as WordDocument
        document = WordDocument(BytesIO(content))
    except ImportError as exc:
        raise CrossBotError(ErrorCode.CONFIGURATION, "DOCX 解析依赖 python-docx 未安装。") from exc
    except Exception as exc:
        raise ParseError("DOCX 文档无法读取。") from exc

    fields = []
    total_text = 0
    for index, paragraph in enumerate(document.paragraphs, 1):
        text = paragraph.text
        total_text += len(text)
        if total_text > MAX_TEXT_CHARS:
            raise ParseError("DOCX 文本超过解析上限。")
        candidate = _paragraph_field(text, location=f"paragraph:{index}")
        if candidate:
            fields.append(candidate)
    for table_index, table in enumerate(document.tables, 1):
        for row_index, row in enumerate(table.rows, 1):
            cells = row.cells
            for col_index, cell in enumerate(cells):
                label = _label(cell.text)
                if not label:
                    continue
                value_cell = cells[col_index + 1] if col_index + 1 < len(cells) else None
                if value_cell is not None and _label(value_cell.text):
                    value_cell = None
                value = value_cell.text if value_cell is not None else None
                location = (f"table:{table_index},row:{row_index},col:{col_index + 2}"
                            if value_cell is not None else
                            f"table:{table_index},row:{row_index},col:{col_index + 1}")
                total_text += len(cell.text) + len(value or "")
                if total_text > MAX_TEXT_CHARS:
                    raise ParseError("DOCX 文本超过解析上限。")
                fields.append(_candidate(label, value, sheet="document", cell=location))
    return _unique(fields)

