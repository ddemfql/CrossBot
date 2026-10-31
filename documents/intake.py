"""Bounded source custody and conservative, unconfirmed intake candidates.

Unlike a business parser, intake preserves every supported source table and its
text even when no business labels are understood. It never evaluates formulas.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import PurePosixPath
import re
from zipfile import ZipFile

from core.errors import ParseError
from core.models import DocumentType as Type, ExtractedField, SourceTable
from documents.field_mapping import _LOOKUP, _normalize

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_ROWS = 10000
MAX_COLUMNS = 256
MAX_TABLES = 100
MAX_CELLS = 250000
INTAKE_VERSION = "w3-source-intake-2"

EXTRA_LABELS = {
    # Intake preserves seller and shipping parties as separate facts. The
    # strict parser's legacy seller->shipper alias is not appropriate here.
    "seller": ("seller", "supplier", "seller name", "supplier name", "卖方", "供应商", "卖方名称"),
    "order_no": ("p.o. no", "po #", "order #", "purchase order #"),
    "invoice_no": ("invoice no", "invoice number", "发票号"),
    "document_date": ("date", "document date", "日期"),
    "payment_terms": ("payment terms", "付款条件", "付款方式"),
    "transport_mode": ("transport mode", "运输方式"),
    "product_code": ("product code", "sku", "item no", "产品编码", "货号"),
    "quantity": ("qty pcs", "qty (pcs)", "quantity pcs", "订购数量"),
    "gross_weight_kg": ("gross weight kg", "gross weight kgs", "g.w.", "g.w.(kgs)", "毛重 kg"),
    "net_weight_kg": ("net weight kg", "net weight kgs", "n.w.", "n.w.(kgs)", "净重 kg"),
    "bill_of_lading_no": ("bill of lading no", "b/l no", "bl no", "提单号"),
    "air_waybill_no": ("air waybill no", "awb no", "空运单号"),
    "booking_no": ("booking no", "订舱号"),
    "container_no": ("container no", "集装箱号", "柜号"),
    "seal_no": ("seal no", "封号"),
    "vessel": ("vessel", "船名"), "voyage": ("voyage", "航次"),
    "port_of_loading": ("port of loading", "装货港", "起运港"),
    "port_of_discharge": ("port of discharge", "卸货港"),
    "notify_party": ("notify party", "通知人"),
    "certificate_no": ("certificate no", "certificate number", "证书编号"),
    "report_no": ("report no", "report number", "报告编号"),
    "issuer": ("issued by", "issuing authority", "签发机构", "出具机构"),
    "receipt_no": ("receipt no", "receipt number", "回执编号"),
    "bank_reference": ("bank reference", "transaction reference", "银行流水号"),
    "received_amount": ("received amount", "实收金额"),
    "seller_address": ("seller address", "卖方地址"),
    "buyer_address": ("buyer address", "买方地址"),
}
LABELS = dict(_LOOKUP)
for _name, _aliases in EXTRA_LABELS.items():
    for _alias in (_name, *_aliases):
        LABELS[_normalize(_alias)] = _name


def source_label(value: str) -> str | None:
    return LABELS.get(_normalize(str(value).strip(" .:#：=")))


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _package_check(content: bytes):
    with ZipFile(BytesIO(content)) as package:
        entries = package.infolist()
        if len(entries) > 3000 or sum(item.file_size for item in entries) > 80_000_000:
            raise ParseError("Office 容器超过采集安全上限；原件保留。")


def _candidates(text: str, tables: list[SourceTable]):
    fields, warnings = [], []
    found = {}
    conflicted = set()

    def add(name, value, **location):
        value = str(value).strip()
        if not value or source_label(value) or value.startswith("="):
            return
        if name in conflicted:
            return
        previous = found.get(name)
        if previous:
            if previous.value != value:
                conflicted.add(name)
                fields.remove(previous)
                warnings.append(f"字段 {name} 存在多个不同候选，未自动选择；请对照各表原文。")
            return
        field = ExtractedField(name=name, value=value, normalized_value=value,
            confidence=0.85, note="明确标签匹配候选，未确认；不表示外部文件真实性已核验。", **location)
        found[name] = field
        fields.append(field)

    for table in tables:
        for row_index, row in enumerate(table.rows):
            for index, value in enumerate(row):
                name = source_label(value)
                if name and index + 1 < len(row):
                    add(name, row[index + 1], sheet_name=table.name,
                        cell_reference=f"R{row_index + 1}C{index + 2}", page_number=table.page_number)
                match = re.fullmatch(r"\s*([^:：=]{1,80})\s*[:：=]\s*(.+?)\s*", value)
                if match and source_label(match[1]):
                    add(source_label(match[1]), match[2], sheet_name=table.name,
                        cell_reference=f"R{row_index + 1}C{index + 1}", page_number=table.page_number)
    # Tables already carry provenance; only parse free text for names not found
    # there. PDF page markers preserve locations for extracted/OCR candidates.
    page_number = None
    for line in text.splitlines():
        marker = re.fullmatch(r"\[Page (\d+)\]", line.strip())
        if marker:
            page_number = int(marker[1])
            continue
        match = re.fullmatch(r"\s*([^:：=\t]{1,80})\s*(?:[:：=]|\t)\s*(.+?)\s*", line)
        if match and source_label(match[1]):
            add(source_label(match[1]), match[2], page_number=page_number)
    return fields, warnings


def collect_source(content: bytes, extension: str, *, ocr_images: bool = False) -> tuple[str, list[SourceTable], list[ExtractedField], list[str]]:
    """Collect original text, matrices and unconfirmed explicit-label fields.

    Unsupported and corrupt sources return warnings; the importer still keeps
    their exact bytes. Images are optionally transcribed by OCR; no missing
    business fact is invented. Bulk imports keep photographs preview-only.
    """
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        raise ParseError("源文件超过 20 MB 采集上限。")
    extension = extension.lower()
    if extension and not extension.startswith("."):
        extension = "." + extension
    text, tables, warnings = "", [], []
    image_fields = []
    try:
        if extension == ".xlsx":
            _package_check(content)
            from openpyxl import load_workbook
            book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) > MAX_TABLES:
                    raise ParseError("工作簿工作表数量超过采集上限。")
                cells = 0
                for sheet in book.worksheets:
                    if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                        raise ParseError("工作表行列数量超过采集上限。")
                    rows = []
                    for row in sheet.iter_rows():
                        cells += len(row)
                        if cells > MAX_CELLS:
                            raise ParseError("工作簿单元格数量超过采集上限。")
                        rows.append([_text(cell.value) for cell in row])
                    tables.append(SourceTable(name=sheet.title, rows=rows))
                warnings.append("Excel 公式以原公式保留，不求值；缓存金额不作为确认事实。")
            finally:
                book.close()
        elif extension == ".xls":
            import xlrd
            book = xlrd.open_workbook(file_contents=content, on_demand=True)
            try:
                if book.nsheets > MAX_TABLES:
                    raise ParseError("工作簿工作表数量超过采集上限。")
                cells = 0
                for sheet in book.sheets():
                    cells += sheet.nrows * sheet.ncols
                    if sheet.nrows > MAX_ROWS or sheet.ncols > MAX_COLUMNS or cells > MAX_CELLS:
                        raise ParseError("工作簿单元格数量超过采集上限。")
                    tables.append(SourceTable(name=sheet.name, rows=[
                        [_text(value) for value in sheet.row_values(index)] for index in range(sheet.nrows)]))
                warnings.append("旧 XLS 可能包含公式缓存，所有值均待人工核对。")
            finally:
                book.release_resources()
        elif extension == ".docx":
            _package_check(content)
            from docx import Document as WordDocument
            document = WordDocument(BytesIO(content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
            queue = [(table, f"table:{index}") for index, table in enumerate(document.tables, 1)]
            seen, cells = set(), 0
            while queue:
                table, table_name = queue.pop(0)
                if table._tbl in seen:
                    continue
                seen.add(table._tbl)
                if len(tables) >= MAX_TABLES or len(table.rows) > MAX_ROWS:
                    raise ParseError("Word 表格数量或行数超过采集上限。")
                rows = []
                for row_index, row in enumerate(table.rows, 1):
                    cells += len(row.cells)
                    if cells > MAX_CELLS or len(row.cells) > MAX_COLUMNS:
                        raise ParseError("Word 表格单元格数量超过采集上限。")
                    rows.append([cell.text for cell in row.cells])
                    for col_index, cell in enumerate(row.cells, 1):
                        queue.extend((child, f"{table_name}/R{row_index}C{col_index}/nested:{index}")
                                     for index, child in enumerate(cell.tables, 1))
                tables.append(SourceTable(name=table_name, rows=rows))
        elif extension in {".txt", ".csv"}:
            for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                try:
                    text = content.decode(encoding)
                    break
                except UnicodeError:
                    pass
            else:
                text = content.decode("utf-8", errors="replace")
                warnings.append("文本编码无法确定，替代字符处请查看原件。")
            if extension == ".csv":
                try:
                    dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                rows = list(csv.reader(StringIO(text), dialect=dialect))
                tables.append(SourceTable(name="CSV", rows=rows))
        elif extension == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ParseError("加密或超过 100 页的 PDF 不能自动采集。")
            page_text = {}
            blank = []
            for number, page in enumerate(reader.pages, 1):
                value = page.extract_text() or ""
                page_text[number] = value
                if not value.strip():
                    blank.append(number)
            if blank:
                try:
                    from documents.ocr import OCRBackend
                    recognized = OCRBackend().recognize_pdf(content)
                    for number in blank:
                        page_text[number] = "\n".join(line.text for line in recognized.lines if line.page_number == number)
                    warnings.append("无文本层 PDF 页面已使用 OCR；识别结果须人工复核。")
                except Exception:
                    warnings.append("部分 PDF 页面无文本层且 OCR 未完成；原件保留供预览及人工录入。")
            text = "\n\n".join(f"[Page {number}]\n{value}" for number, value in page_text.items())
        elif extension in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
            if ocr_images:
                try:
                    from documents.ocr import OCRBackend
                    from documents.field_mapping import from_ocr_lines
                    lines = OCRBackend().recognize_image(content, page_number=1)
                    text = "[Page 1]\n" + "\n".join(line.text for line in lines)
                    try:
                        image_fields = from_ocr_lines(lines, lookup=LABELS)
                    except ParseError:
                        warnings.append("OCR 标签出现互相冲突候选；未自动选择，请核对原图。")
                    warnings.append("图中文字为 OCR 转录候选，未虚构补齐未知值；请逐项对照原图确认。")
                except Exception:
                    warnings.append("图片 OCR 未完成，原图完整保留，请人工查看录入。")
            else:
                warnings.append("图片原件已保留供预览；未批量 OCR，避免将出货照片推断为业务数据。")
        else:
            warnings.append("该格式仅保留原件，暂无结构采集器。")
        cell_count = sum(len(row) for table in tables for row in table.rows)
        if cell_count > MAX_CELLS or any(len(table.rows) > MAX_ROWS for table in tables):
            raise ParseError("源表格超出采集上限。")
        fields, field_warnings = _candidates(text, tables)
        for candidate in image_fields:
            previous = next((field for field in fields if field.name == candidate.name), None)
            if previous is None:
                fields.append(candidate)
            elif previous.value == candidate.value:
                fields[fields.index(previous)] = candidate
            else:
                fields.remove(previous)
                field_warnings.append(f"字段 {candidate.name} 的 OCR 行配对与文本候选冲突，未自动选择。")
        table_text = "\n\n".join("[" + table.name + "]\n" + "\n".join("\t".join(row) for row in table.rows) for table in tables)
        if table_text:
            text += ("\n\n" if text else "") + table_text
        if len(text) > MAX_TEXT:
            raise ParseError("源文档文本超过采集上限。")
        warnings.extend(field_warnings)
        if not fields:
            warnings.append("未找到唯一明确业务字段；请查看原文和表格并人工填写。")
        return text, tables, fields, warnings
    except Exception:
        # Arbitrary parser exceptions can carry confidential source contents.
        return "", [], [], ["源文档结构采集未完成或超过安全上限；原件完整保留，需人工查看。"]


def collect_image_source(content: bytes):
    """Opt-in OCR for a user-selected image; returns the collect_source tuple."""
    return collect_source(content, ".png", ocr_images=True)


def classify_source(path: str) -> tuple[Type, str, str]:
    """Filename/path hints only: responsibility and category require review."""
    normalized = path.replace("\\", "/")
    name = PurePosixPath(normalized).name.casefold()
    full = normalized.casefold()
    compact = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", name)
    external_template = any(token in full for token in ("客户提供", "买方提供", "客户模板", "外部模板", "blank template"))
    kind, responsibility = Type.OTHER, "external"
    if any(token in full for token in ("出货照片", "出货相片", "出货图片", "验货照片", "验货相片", "验货图片",
            "装柜照片", "装箱照片", "shipment photo", "loading photo", "inspection photo", "qc photo")):
        kind, responsibility = Type.PHOTO, "seller"
    elif (any(token in full for token in ("包装设计", "包装稿", "包装图", "artwork", "彩盒", "标签设计", "贴纸"))
          or re.search(r"(?:^|[^a-z])aw(?:[^a-z]|$)", name)):
        kind, responsibility = Type.PACKAGING_ARTWORK, "seller"
    elif (any(token in full for token in ("工厂", "生产", "factory", "production"))
          and (re.search(r"(?:^|[^a-z])po(?:[^a-z]|$)", name) or "订单" in name or "order" in name)):
        kind, responsibility = Type.PRODUCTION_ORDER, "seller"
    elif "isf" in name:
        official_receipt = any(token in name for token in ("回执", "receipt", "accepted", "confirmation"))
        kind, responsibility = (Type.ISF_RECEIPT, "external") if official_receipt else (Type.ISF_WORKSHEET, "seller")
    elif ("appendixj" in compact or "appendixn" in compact or "合规声明" in name
          or "declaration" in name):
        kind, responsibility = Type.COMPLIANCE_DRAFT, "seller"
    elif (("invoice" in name or re.search(r"(?:^|[^a-z])ci(?:[^a-z]|$)", name) or "发票" in name)
          and ("packing" in name or re.search(r"(?:^|[^a-z])pl(?:[^a-z]|$)", name) or "装箱" in name)):
        kind, responsibility = Type.COMBINED_INVOICE_PACKING_LIST, "seller"
    else:
        patterns = [
            (Type.BILL_OF_LADING, "external", ("提单", "bill of lading", "b l", "b-l", "bl draft", "telex release")),
            (Type.AIR_WAYBILL, "external", ("air waybill", "airwaybill", "awb", "空运单")),
            (Type.BANK_RECEIPT, "external", ("bank receipt", "银行回单", "银行水单", "收款凭证", "付款水单")),
            (Type.INSURANCE_POLICY, "external", ("insurance policy", "保险单")),
            (Type.CERTIFICATE_OF_ORIGIN, "external", ("certificate of origin", "原产地证")),
            (Type.TEST_REPORT, "external", ("test report", "检测报告", "测试报告")),
            (Type.INSPECTION_REPORT, "external", ("inspection report", "第三方验货报告")),
            (Type.PROFORMA_INVOICE, "seller", ("proforma", "形式发票")),
            (Type.PURCHASE_ORDER, "external", ("purchase order", "采购订单", "客户订单", "买方订单")),
            (Type.QUOTATION, "seller", ("quotation", "报价")),
            (Type.SALES_CONTRACT, "seller", ("contract", "合同", "销售合约")),
            (Type.ORDER_CONFIRMATION, "seller", ("order confirmation", "订单确认")),
            (Type.COMMERCIAL_INVOICE, "seller", ("commercial invoice", "invoice", "商业发票")),
            (Type.PACKING_LIST, "seller", ("packing list", "packinglist", "装箱单", "装箱明细")),
            (Type.SHIPPING_INSTRUCTION, "seller", ("shipping instruction", "补料", "运输指示")),
            (Type.BOOKING_REQUEST, "seller", ("booking request", "订舱", "托书")),
            (Type.CUSTOMS_WORKSHEET, "seller", ("customs", "报关")),
            (Type.PRODUCT_SPEC, "seller", ("specification", "产品规格", "产品参数", "配方")),
            (Type.INSPECTION_RECORD, "seller", ("验货记录", "检验记录", "qc record")),
            (Type.PAYMENT_NOTICE, "seller", ("payment notice", "催款", "付款通知")),
            (Type.STATEMENT_OF_ACCOUNT, "seller", ("statement of account", "对账单")),
            (Type.COMPLIANCE, "external", ("certificate", "certification", "认证", "证书", "合规资料")),
        ]
        for candidate, owner, tokens in patterns:
            if any(token in name for token in tokens):
                kind, responsibility = candidate, owner
                break
        else:
            if re.search(r"(?:^|[^a-z])cft(?:[^a-z]|$)", name):
                kind, responsibility = Type.PRODUCT_SPEC, "seller"
            elif re.search(r"(?:^|[^a-z])pi(?:[^a-z]|$)", name):
                kind, responsibility = Type.PROFORMA_INVOICE, "seller"
            elif re.search(r"(?:^|[^a-z])po(?:[^a-z]|$)", name):
                kind, responsibility = Type.PURCHASE_ORDER, "external"
            elif re.search(r"(?:^|[^a-z])ci(?:[^a-z]|$)", name):
                kind, responsibility = Type.COMMERCIAL_INVOICE, "seller"
            elif re.search(r"(?:^|[^a-z])pl(?:[^a-z]|$)", name):
                kind, responsibility = Type.PACKING_LIST, "seller"
    if external_template:
        responsibility = "external"
    note = "按文件名及路径形成分类候选，来源责任须人工确认；不验证签发或提交事实。"
    if kind is Type.ISF_WORKSHEET:
        note += " ISF 填写表不代表已申报或官方回执。"
    if kind is Type.COMPLIANCE_DRAFT:
        note += " 合规声明/Appendix 表不等同第三方认证证书。"
    if external_template:
        note += " 路径提示外部提供模板，保留为外部参考资料。"
    return kind, responsibility, note
