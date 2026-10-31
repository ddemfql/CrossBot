"""Versioned XLSX seller-document templates with transaction data only."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from xml.dom import minidom
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from core.errors import CrossBotError, ErrorCode
from core.models import DocumentForm, DocumentType, TradeData
from core.ports import RenderedOutput

TITLES = {
    DocumentType.PROFORMA_INVOICE: "PROFORMA INVOICE / 形式发票",
    DocumentType.COMMERCIAL_INVOICE: "COMMERCIAL INVOICE / 商业发票",
    DocumentType.PACKING_LIST: "PACKING LIST / 装箱单",
}
TEMPLATE_VERSION = "w3-xlsx-1"
TEMPLATE_SPEC = {
    "version": TEMPLATE_VERSION,
    "common": ["order_no", "seller", "buyer", "destination_country", "currency",
                "incoterm", "payment_terms", "transport_mode", "port_of_entry",
                "transaction_date", "transaction_place", "seller_address", "buyer_address",
                "responsible_exporter_person"],
    "invoice_columns": ["product_code", "description", "hs_code", "country_of_origin",
                        "grade_quality", "marks_and_numbers", "package_contents", "quantity",
                        "unit", "unit_price", "amount", "currency"],
    "packing_columns": ["product_code", "description", "country_of_origin", "marks_and_numbers",
                        "package_contents", "quantity", "unit", "packages", "gross_weight_kg",
                        "net_weight_kg"],
    "adjustments": ["kind", "name", "amount", "currency", "included_in_invoice_price"],
}
def template_sha256(document_type: DocumentType) -> str:
    spec = dict(TEMPLATE_SPEC, document_type=document_type.value)
    return sha256(json.dumps(spec, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

NAVY = "294B45"
GREEN = "DDEBE5"
BLUE = "E6EFF2"
WHITE = "FFFFFF"
INK = "203330"
BORDER = "B7C7C1"


def _decimal(value) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise CrossBotError(ErrorCode.INVALID_INPUT, "订单数字字段必须是有限十进制数。") from None


def _stable_xlsx(workbook) -> bytes:
    workbook.properties.creator = "CrossBot"
    workbook.properties.lastModifiedBy = "CrossBot"
    fixed_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    workbook.properties.created = fixed_time
    workbook.properties.modified = fixed_time
    original = BytesIO()
    workbook.save(original)
    stable = BytesIO()
    with ZipFile(BytesIO(original.getvalue()), "r") as source, \
            ZipFile(stable, "w", compression=ZIP_DEFLATED, compresslevel=9) as output:
        for item in source.infolist():
            entry = ZipInfo(item.filename, date_time=(2000, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = item.external_attr
            entry.create_system = item.create_system
            content = source.read(item.filename)
            if item.filename == "docProps/core.xml":
                # openpyxl overwrites modified during save_workbook(), even when
                # workbook properties were fixed before saving. Normalize the
                # serialized XML as well as the ZIP timestamps.
                properties = minidom.parseString(content)
                for name in ("created", "modified"):
                    for element in properties.getElementsByTagNameNS("http://purl.org/dc/terms/", name):
                        for child in list(element.childNodes):
                            element.removeChild(child)
                        element.appendChild(properties.createTextNode("2000-01-01T00:00:00Z"))
                # Preserve namespace prefixes used inside xsi:type values too.
                content = properties.toxml(encoding="utf-8")
                properties.unlink()
            output.writestr(entry, content, compress_type=ZIP_DEFLATED, compresslevel=9)
    return stable.getvalue()


class XLSXRenderer:
    """Seller-controlled drafts using blank templates based on the provided forms."""

    def render(self, document_type: DocumentType, trade: TradeData,
               form: DocumentForm | None = None) -> RenderedOutput:
        if document_type not in TITLES:
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                                "仅支持生成 PI、商业发票和装箱单卖方草稿。")
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        packing = document_type is DocumentType.PACKING_LIST
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = {DocumentType.PROFORMA_INVOICE: "Proforma Invoice",
                       DocumentType.COMMERCIAL_INVOICE: "Commercial Invoice",
                       DocumentType.PACKING_LIST: "Packing List"}[document_type]
        sheet.sheet_view.showGridLines = False
        thin = Side(style="thin", color=BORDER)
        box = Border(left=thin, right=thin, top=thin, bottom=thin)

        sheet.merge_cells("A1:L1")
        sheet["A1"] = TITLES[document_type]
        sheet["A1"].font = Font(name="Arial", size=16, bold=True, color=WHITE)
        sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
        sheet["A1"].alignment = Alignment(vertical="center")
        sheet.row_dimensions[1].height = 30
        sheet.merge_cells("A2:L2")
        sheet["A2"] = "DRAFT / 草稿 · 人工复核后方可使用"
        sheet["A2"].font = Font(name="Arial", bold=True, color="9A5100")

        info = [
            ("订单号", trade.order_no, "卖方", trade.seller),
            ("卖方地址", trade.seller_address, "买方地址", trade.buyer_address),
            ("买方", trade.buyer, "目的地", trade.destination_country),
            ("币种", trade.currency, "贸易术语", f"{trade.incoterm} (Incoterms {trade.incoterm_version})"),
            ("付款条件", trade.payment_terms, "运输方式", trade.transport_mode),
            ("交易日期/地点", f"{trade.transaction_date or '待补充'} · {trade.transaction_place or '待补充'}",
             "进口口岸", trade.port_of_entry or "待补充"),
            ("出口方负责人", trade.responsible_exporter_person or "待补充",
             "预计出运/数据修订", f"{trade.expected_ship_date or '待补充'} · r{trade.revision}"),
        ]
        for row, values in enumerate(info, 4):
            for column, value in ((1, values[0]), (2, values[1]), (5, values[2]), (6, values[3])):
                cell = sheet.cell(row, column, value)
                cell.border = box
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if column in (1, 5):
                    cell.font = Font(bold=True, color=INK)
                    cell.fill = PatternFill("solid", fgColor=GREEN)
                else:
                    cell.font = Font(color=INK)
        sheet.merge_cells("A11:L11")
        sheet["A11"] = ("费用、折扣、返利及生产协助：已核对" if trade.invoice_adjustments_confirmed
                        else "费用、折扣、返利及生产协助：待人工核对")
        sheet["A11"].font = Font(bold=True, color=INK if trade.invoice_adjustments_confirmed else "9A5100")
        sheet.merge_cells("A12:L12")
        sheet["A12"] = "产品明细 / Product details"
        sheet["A12"].font = Font(bold=True, color=WHITE)
        sheet["A12"].fill = PatternFill("solid", fgColor=NAVY)

        headers = (["编码", "品名", "原产国", "唛头/件号", "包装内容", "数量", "单位", "箱数", "毛重 kg", "净重 kg"] if packing
                   else ["编码", "品名", "HS/HTS", "原产国", "品级/质量", "唛头/件号", "数量", "单位", "单价", "金额", "币种"])
        for column, header in enumerate(headers, 1):
            cell = sheet.cell(13, column, header)
            cell.font = Font(bold=True, color=INK)
            cell.fill = PatternFill("solid", fgColor=BLUE)
            cell.border = box
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        amounts = []
        packages_total = 0
        gross_total = Decimal(0)
        net_total = Decimal(0)
        for row_number, line in enumerate(trade.line_items, 14):
            quantity = _decimal(line.quantity)
            price = _decimal(line.unit_price)
            amount = quantity * price
            amounts.append(amount)
            gross = _decimal(line.gross_weight_kg)
            net = _decimal(line.net_weight_kg)
            packages_total += line.packages
            gross_total += gross
            net_total += net
            values = ([line.product_code, line.description, line.country_of_origin,
                       line.marks_and_numbers, line.package_contents, line.quantity, line.unit,
                       line.packages, line.gross_weight_kg, line.net_weight_kg] if packing else
                      [line.product_code, line.description, line.hs_code, line.country_of_origin,
                       line.grade_quality, line.marks_and_numbers, line.quantity, line.unit,
                       line.unit_price, format(amount, "f"), trade.currency])
            for column, value in enumerate(values, 1):
                cell = sheet.cell(row_number, column, value)
                cell.border = box
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        total_row = 14 + len(trade.line_items)
        sheet.cell(total_row, 1, "合计 / Total").font = Font(bold=True)
        if packing:
            sheet.cell(total_row, 8, packages_total)
            sheet.cell(total_row, 9, format(gross_total, "f"))
            sheet.cell(total_row, 10, format(net_total, "f"))
        else:
            sheet.cell(total_row, 10, format(sum(amounts, Decimal(0)), "f"))
            sheet.cell(total_row, 11, trade.currency)
        for cell in sheet[total_row]:
            if cell.value is not None:
                cell.border = box
                cell.fill = PatternFill("solid", fgColor=GREEN)
                cell.font = Font(bold=True, color=INK)

        adjustment_row = total_row + 2
        sheet.merge_cells(start_row=adjustment_row, start_column=1, end_row=adjustment_row, end_column=5)
        section = sheet.cell(adjustment_row, 1, "费用与其他调整 / Charges and adjustments")
        section.font = Font(bold=True, color=WHITE)
        section.fill = PatternFill("solid", fgColor=NAVY)
        for column, header in enumerate(["类别", "说明", "金额", "币种", "已含于货价"], 1):
            cell = sheet.cell(adjustment_row + 1, column, header)
            cell.font = Font(bold=True, color=INK)
            cell.fill = PatternFill("solid", fgColor=BLUE)
            cell.border = box
        adjustments = trade.adjustments or ([None] if trade.invoice_adjustments_confirmed else [])
        if adjustments:
            for index, item in enumerate(adjustments, adjustment_row + 2):
                values = (["无额外调整", "人工确认无费用、折扣或协助", "0", trade.currency, "是"]
                          if item is None else [item.kind.value, item.name, item.amount,
                                                item.currency, "是" if item.included_in_invoice_price else "否"])
                for column, value in enumerate(values, 1):
                    cell = sheet.cell(index, column, value)
                    cell.border = box

        widths = {"A": 18, "B": 34, "C": 16, "D": 16, "E": 18, "F": 30,
                  "G": 16, "H": 14, "I": 16, "J": 18, "K": 14, "L": 12}
        for column, width in widths.items():
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = "A14"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.page_margins.left = 0.3
        sheet.page_margins.right = 0.3
        sheet.page_margins.top = 0.5
        sheet.page_margins.bottom = 0.5
        sheet.print_title_rows = "1:13"
        sheet.sheet_properties.pageSetUpPr.autoPageBreaks = False
        sheet.oddFooter.center.text = "Seller-controlled draft · Human review required"

        if form is not None:
            from .catalog import FORM_FIELDS, ROW_FIELDS
            from .forms import effective_values
            details = workbook.create_sheet("Form Details")
            details.append(["DRAFT / 草稿", "人工填写补充资料"])
            details.append(["表单修订", str(form.revision)])
            values = effective_values(trade, form)
            for name, label in FORM_FIELDS[document_type].items():
                details.append([label, values.get(name, "")])
            if form.rows:
                details.append([])
                details.append(list(ROW_FIELDS[document_type].values()))
                for row in form.rows:
                    details.append([row.get(name, "") for name in ROW_FIELDS[document_type]])
            details.column_dimensions["A"].width = 28
            details.column_dimensions["B"].width = 60
        # Treat every user-supplied text as text, including Excel formula prefixes.
        for page in workbook.worksheets:
            for row in page.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.lstrip().startswith(("=", "+", "-", "@")):
                        cell.data_type = "s"
        content = _stable_xlsx(workbook)
        version = TEMPLATE_VERSION
        digest = template_sha256(document_type)
        if form is not None:
            version = TEMPLATE_VERSION + "-forms-1"
            digest = sha256(json.dumps({"base": digest, "version": version,
                "fields": FORM_FIELDS[document_type], "rows": ROW_FIELDS[document_type]},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return RenderedOutput(content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            extension="xlsx", template_id="w3-" + document_type.value,
            template_version=version, template_sha256=digest)

