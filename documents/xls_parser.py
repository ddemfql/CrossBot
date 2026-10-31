"""Narrow key/value parser for legacy BIFF .xls files."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

from core.errors import CrossBotError, ErrorCode, ParseError
from core.models import ExtractedField, FieldValueStatus
from documents.parsers import ALIASES, FIELD_NAMES

MAX_XLS_BYTES = 20 * 1024 * 1024
MAX_SHEETS = 30
MAX_ROWS = 5000
MAX_COLUMNS = 256
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


def _text_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        try:
            number = Decimal(str(value))
            if number.is_finite():
                rendered = format(number, "f")
                return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
        except InvalidOperation:
            return None
    text = str(value).strip()
    return text or None


def parse_xls(content: bytes) -> list[ExtractedField]:
    """Extract only allowlisted A/B key-value rows; all values remain candidates.

    xlrd exposes BIFF cached cell results rather than reliable formula provenance,
    so values are explicitly marked for human verification.
    """
    if not isinstance(content, bytes) or not content:
        raise ParseError("旧版工作簿内容为空。")
    if len(content) > MAX_XLS_BYTES:
        raise CrossBotError(ErrorCode.FILE_TOO_LARGE, "旧版工作簿超过 20 MB。")
    if not content.startswith(OLE_SIGNATURE):
        raise ParseError("文件不是受支持的 BIFF .xls 工作簿。")
    try:
        import xlrd
    except ImportError as exc:
        raise CrossBotError(ErrorCode.CONFIGURATION, "旧版 Excel 解析依赖 xlrd 未安装。") from exc

    workbook = None
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True,
                                      ragged_rows=False)
        if workbook.nsheets > MAX_SHEETS:
            raise ParseError("工作簿超过 30 个工作表的解析上限。")
        fields: list[ExtractedField] = []
        for sheet in workbook.sheets():
            if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS:
                raise ParseError("工作表超过固定模板解析大小上限。")
            for rowx in range(sheet.nrows):
                key = sheet.cell_value(rowx, 0) if sheet.ncols else ""
                if not isinstance(key, str):
                    continue
                name = key.strip().casefold()
                if name not in FIELD_NAMES:
                    continue
                value_cell = sheet.cell(rowx, 1) if sheet.ncols > 1 else None
                if value_cell is not None and value_cell.ctype == xlrd.XL_CELL_ERROR:
                    raise ParseError("候选字段包含 Excel 错误值，无法安全读取。")
                value = _text_value(value_cell.value if value_cell is not None else None)
                status = FieldValueStatus.CANDIDATE if value is not None else FieldValueStatus.MISSING
                fields.append(ExtractedField(
                    name=ALIASES.get(name, name),
                    value=value,
                    normalized_value=value,
                    status=status,
                    confidence=0.95 if value is not None else None,
                    sheet_name=sheet.name,
                    cell_reference=f"B{rowx + 1}",
                    note="BIFF 固定键值行候选；旧版公式缓存可能过期，须人工复核。"))
        if not fields:
            raise ParseError("未找到支持的固定模板字段，请保留原件并人工补充资料。")
        names = [field.name for field in fields]
        if len(names) != len(set(names)):
            raise ParseError("模板包含重复字段，无法确定唯一候选值。")
        return fields
    except CrossBotError:
        raise
    except Exception as exc:
        raise ParseError("旧版工作簿无法读取；仅支持 .xls 固定键值模板。") from exc
    finally:
        if workbook is not None:
            workbook.release_resources()

