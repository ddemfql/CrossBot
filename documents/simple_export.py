"""Plain seller XLSX previews and versioned exports with current checks and human confirmation."""
from __future__ import annotations

from hashlib import sha256
from decimal import Decimal, DecimalException
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CrossCheckResult, CrossCheckStatus, DocumentType,
                         GeneratedArtifact, to_dict)
from core.ports import RenderedOutput
from review.policy import audit, invalidate, require, source_versions
from validation.rules import number
from .catalog import DOC_LABELS, FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from .forms import effective_rows, effective_values, get_form
from .workbook_renderer import _stable_xlsx

LAYOUT_VERSION = "simple-xlsx-4"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NUMERIC_FIELDS = frozenset({"quantity", "unit_price", "amount", "packages",
    "gross_weight_kg", "net_weight_kg", "amount_due", "opening_balance",
    "closing_balance", "total_amount", "volume_cbm", "debit", "credit", "balance"})
TRADE_EXTRA_FIELDS = {"transaction_date": "交易日期", "transaction_place": "交易地点",
    "port_of_entry": "进口口岸", "seller_address": "卖方地址", "buyer_address": "买方地址",
    "responsible_exporter_person": "出口方负责人", "incoterm_version": "贸易术语版本",
    "product_category": "产品类别", "expected_ship_date": "预计出运日期",
    "actual_ship_date": "实际出运日期（人工记录）", "due_date": "约定付款日期",
    "invoice_adjustments_confirmed": "费用与调整已人工核对"}
PACKAGE_FIELDS = {"package_number": "包件编号", "marks_and_numbers": "唛头/件号",
    "product_code": "产品编码", "contents_description": "包件内容", "quantity": "数量",
    "unit": "单位", "gross_weight_kg": "毛重 kg", "net_weight_kg": "净重 kg"}
LINE_EXTRA_FIELDS = {"product_code": "产品编码", "hs_code": "HS/HTS 编码",
    "country_of_origin": "原产国", "grade_quality": "品级/质量", "marks_and_numbers": "唛头/件号",
    "package_contents": "包装内容", "packages": "箱数", "gross_weight_kg": "毛重 kg",
    "net_weight_kg": "净重 kg"}
ADJUSTMENT_LABELS = {"freight": "运费", "insurance": "保险", "commission": "佣金",
    "packing": "包装费", "discount": "折扣", "rebate": "返利", "drawback": "退税",
    "bounty": "补贴", "assist": "生产协助", "other": "其他"}


def layout_hash(kind, output_format="xlsx", form=None, workspace=None, audience="internal"):
    from .forms import form_fields, form_row_fields, form_context_fields
    fields = FORM_FIELDS[kind] if form is None else form_fields(form)
    row_fields = ROW_FIELDS[kind] if form is None else form_row_fields(form)
    return sha256(json.dumps({"version": LAYOUT_VERSION, "format": output_format, "audience": audience,
        "fields": fields, "rows": row_fields,
        "context_fields": None if form is None else sorted(form_context_fields(form, workspace)),
        "trade_extra_fields": TRADE_EXTRA_FIELDS, "package_fields": PACKAGE_FIELDS,
        "line_extra_fields": LINE_EXTRA_FIELDS, "adjustment_labels": ADJUSTMENT_LABELS,
        "sheets": ["文件", "明细合计", "字段来源", "交易补充资料", "逐包内容", "订单补充明细", "费用与调整", "缺失与提示", "生成记录"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _text_row(sheet, values):
    """All supplied text stays text, including values beginning with '='."""
    sheet.append(["" if value is None else str(value) for value in values])
    for cell in sheet[sheet.max_row]:
        cell.data_type = "s"


def _data_row(sheet, names, values):
    """Use Excel numbers only when their 15 significant digit limit is safe."""
    _text_row(sheet, values)
    for cell, name, value in zip(sheet[sheet.max_row], names, values):
        parsed = number(value) if name in NUMERIC_FIELDS else None
        try:
            if (parsed is not None and (parsed == 0 or -307 <= parsed.adjusted() <= 307)
                    and len(parsed.normalize().as_tuple().digits) <= 15):
                cell.value = parsed
                cell.number_format = "0.###############"
        except DecimalException:
            # Unrepresentable user input stays visible text, never an export crash.
            continue


def _sum_complete(rows, name):
    parsed = [number(row.get(name)) for row in rows]
    if not parsed or any(value is None for value in parsed):
        return None
    try:
        return sum(parsed, Decimal(0))
    except DecimalException:
        return None


def _totals(workbook, rows, columns, values):
    sheet = workbook.create_sheet("明细合计")
    _text_row(sheet, ["项目", "合计", "单位/币种", "说明"])
    if "quantity" in columns:
        units = {str(row.get("unit", "")).strip() for row in rows}
        same_unit = len(units) == 1 and "" not in units
        total = _sum_complete(rows, "quantity") if same_unit else None
        _data_row(sheet, ["", "quantity", "", ""], ["产品行数量之和", total,
            next(iter(units)) if same_unit else "", "" if total is not None else "数量缺失或单位不一致，未合计"])
    if "amount" in columns:
        currencies = {str(row.get("currency") or values.get("currency", "")).strip() for row in rows}
        same_currency = len(currencies) == 1 and "" not in currencies
        total = _sum_complete(rows, "amount") if same_currency else None
        _data_row(sheet, ["", "amount", "", ""], ["产品行金额小计", total,
            next(iter(currencies)) if same_currency else "",
            "不含另列费用或调整，不代表最终应付总额" if total is not None else "金额缺失或币种不一致，未合计"])
    for name, label, unit in (("packages", "明细行箱数之和（未去重）", "箱"),
                              ("gross_weight_kg", "明细行毛重之和（未去重）", "kg"),
                              ("net_weight_kg", "明细行净重之和（未去重）", "kg")):
        if name in columns:
            total = _sum_complete(rows, name)
            _data_row(sheet, ["", name, "", ""], [label, total, unit,
                "仅加总明细行，不推定独立包件或整票货物总数" if total is not None else "存在缺失值，未合计"])
    if sheet.max_row == 1:
        _text_row(sheet, ["此文件没有可合计的明细列", "", "", ""])


def _provenance(workbook, workspace, form, values, rows):
    from .provenance import ORIGIN_LABELS, form_value_origins, source_location
    from .forms import form_fields, form_row_fields
    fields, row_fields = form_fields(form), form_row_fields(form)
    sheet = workbook.create_sheet("字段来源")
    _text_row(sheet, ["字段/行", "当前填写值", "填写方式", "候选值", "来源文件",
                      "来源版本", "来源状态", "页码", "工作表", "单元格/位置",
                      "办理人", "操作时间", "来源说明"])
    versions = source_versions(workspace)
    for origin in form_value_origins(workspace, form.document_type):
        if origin.field_name not in (fields if origin.row_number is None else row_fields):
            continue
        label = (f"明细行 {origin.row_number}.{origin.field_name}" if origin.row_number is not None
                 else origin.field_name)
        _text_row(sheet, [label, origin.value, ORIGIN_LABELS[origin.origin_kind], "",
            origin.filename, f"v{origin.version_number}" if origin.version_number is not None else "",
            "当前" if origin.origin_kind == "file" else "", "", "", origin.location,
            origin.actor_id, origin.occurred_at.isoformat() if origin.occurred_at else "", origin.note])
    def source(candidate):
        document = next((doc for doc in workspace.documents if doc.document_id == candidate.document_id), None)
        version = next((item for item in document.versions if item.version_id == candidate.version_id), None) if document else None
        return (version.original_filename if version else "来源文件未找到",
                f"v{version.version_number}" if version else candidate.version_id,
                "当前候选（非当前值来源承诺）" if versions.get(candidate.document_id) == candidate.version_id else "已失效候选")
    # Alternative source readings are kept separate from actual value origins.
    for name, candidates in form.field_candidates.items():
        if name not in fields:
            continue
        for candidate in candidates:
            filename, version, state = source(candidate)
            _text_row(sheet, [f"候选字段 {name}", "", "候选（未采用或待核对）", candidate.value,
                filename, version, state, candidate.page_number, candidate.sheet_name,
                candidate.cell_reference, "", "", source_location(candidate)])
    for candidate in form.row_candidates:
        filename, version, state = source(candidate)
        _text_row(sheet, [f"来源明细行 {candidate.row_number}", "", "候选（未采用或待核对）",
            json.dumps({k: v for k, v in candidate.values.items() if k in row_fields}, ensure_ascii=False, sort_keys=True), filename, version,
            state, candidate.page_number, candidate.table_name,
            json.dumps({k: v for k, v in candidate.cell_references.items() if k in row_fields}, ensure_ascii=False, sort_keys=True), "", "",
            "原表候选明细；逐单元格实际来源见上方，候选不等于人工采用。"])


def _render(workspace, kind, form, actor, checks, *, human_confirmed=True, review=None, audience="internal"):
    from .forms import form_fields, form_row_fields, form_context_fields
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    if audience not in {"internal", "customer"}:
        raise ValidationError("请选择客户文件或内部核对资料包。")
    fields, row_fields = form_fields(form), form_row_fields(form)
    context_fields = form_context_fields(form, workspace)
    sheet = workbook.active
    sheet.title = "文件"
    _text_row(sheet, [DOC_LABELS[kind]])
    if audience == "internal":
        _text_row(sheet, ["人工确认", "是" if human_confirmed else "否（待人工确认）",
                          "确认人", actor.user_id if human_confirmed else ""])
    else:
        _text_row(sheet, ["文件状态", "人工确认填写稿" if human_confirmed else "未人工确认预览"])
    if audience == "customer":
        _text_row(sheet, ["文件说明", "卖方填写稿；空白内容待补充。" if human_confirmed
                         else "未人工确认预览，请核对后再交付。空白内容待补充。"])
    else:
        _text_row(sheet, ["文件说明", "卖方填写稿；空白表示资料缺失。人工确认不代表外部签发或规则全部通过。"
            if human_confirmed else "未人工确认预览；不得作为已确认文件使用。空白表示资料缺失，预览不产生正式文件版本或批准。"])
    values = effective_values(workspace.trade, form)
    if audience == "customer":
        from .requirements import NUMBER_ALIASES, DATE_ALIASES
        for generic, alias in (("document_no", NUMBER_ALIASES.get(kind)), ("document_date", DATE_ALIASES.get(kind))):
            if alias and alias in fields:
                values[alias] = values.get(alias) or values.get(generic, "")
                fields.pop(generic, None)
    for name, label in fields.items():
        _data_row(sheet, ["", name], [label, values.get(name, "")])
    _text_row(sheet, [])
    _text_row(sheet, list(row_fields.values()))
    header_row = sheet.max_row
    rows = effective_rows(workspace.trade, form)
    output_rows = []
    for row in rows:
        row = dict(row)
        if "amount" in row_fields and not row.get("amount"):
            quantity, price = number(row.get("quantity")), number(row.get("unit_price"))
            if quantity is not None and price is not None:
                try:
                    row["amount"] = str(quantity * price)
                except DecimalException:
                    row["amount"] = ""
        output_rows.append(row)
        _data_row(sheet, list(row_fields), [row.get(name, "") for name in row_fields])
    if audience == "internal" or output_rows:
        _totals(workbook, output_rows, row_fields, values)
    if audience == "internal":
        _provenance(workbook, workspace, form, values, output_rows)

    if any(name in context_fields for name in TRADE_EXTRA_FIELDS):
        extras = workbook.create_sheet("交易补充资料")
        _text_row(extras, ["订单补充项", "订单记录值", "说明"])
        _text_row(extras, ["资料口径", "订单中的业务记录", "与当前文件字段分别列示；不据此推断发运、付款或外部签发事实。"])
        for name, label in TRADE_EXTRA_FIELDS.items():
            if name not in context_fields or (name in FORM_FIELDS[kind] and name not in fields):
                continue
            value = getattr(workspace.trade, name)
            if isinstance(value, bool):
                value = "是" if value else "否（待核对）"
            _text_row(extras, [label, value, ""])
    if workspace.trade.packages and "packages" in context_fields:
        packages = workbook.create_sheet("逐包内容")
        _text_row(packages, list(PACKAGE_FIELDS.values()))
        for package in workspace.trade.packages:
            _data_row(packages, list(PACKAGE_FIELDS), [getattr(package, name) for name in PACKAGE_FIELDS])
    if workspace.trade.line_items and "line_items" in context_fields:
        line_extras = workbook.create_sheet("订单补充明细")
        extra_fields = {k: v for k, v in LINE_EXTRA_FIELDS.items()
                        if k not in ROW_FIELDS[kind] or k in row_fields}
        _text_row(line_extras, list(extra_fields.values()))
        for line in workspace.trade.line_items:
            _data_row(line_extras, list(extra_fields), [getattr(line, name) for name in extra_fields])

    if workspace.trade.adjustments and "adjustments" in context_fields:
        adjustments = workbook.create_sheet("费用与调整")
        _text_row(adjustments, ["类别原值", "说明", "金额原值", "币种", "包含在发票货价内", "类别说明"])
        for item in workspace.trade.adjustments:
            _data_row(adjustments, ["", "", "amount", "", "", ""],
                [item.kind.value, item.name, item.amount, item.currency, item.included_in_invoice_price,
                 ADJUSTMENT_LABELS.get(item.kind.value, item.kind.value)])
        _text_row(adjustments, ["金额口径", "按输入原值列示；不推断折扣正负、不自动折算币种或计算最终应付款。"])

    if audience == "internal":
        warnings = workbook.create_sheet("缺失与提示")
        _text_row(warnings, ["规则", "结果", "提示"])
        for check in checks:
            if check.status is not CrossCheckStatus.PASS:
                _text_row(warnings, [check.rule_code, check.status.value, check.message])
        if warnings.max_row == 1:
            _text_row(warnings, ["", "", "当前检查未发现缺失或冲突。"])

        trace = workbook.create_sheet("生成记录")
        for label, value in (("生成方式", LAYOUT_VERSION),
                         ("导出状态", "人工确认生成" if human_confirmed else "未人工确认预览"),
                         ("人工确认人", actor.user_id if human_confirmed else ""),
                         ("预览申请账号", "" if human_confirmed else actor.user_id),
                         ("核验规则版本", review.rule_version if review else ""),
                         ("核验输入摘要", review.input_digest if review else ""),
                         ("交易修订", workspace.trade.revision),
                         ("来源版本", json.dumps(source_versions(workspace), ensure_ascii=False,
                                                sort_keys=True)),
                         ("表单修订", form.revision)):
            _text_row(trace, [label, value])
    for target in workbook:
        target.freeze_panes = "A2"
        target.sheet_view.showGridLines = False
        target.sheet_properties.pageSetUpPr.fitToPage = True
        target.page_setup.orientation = "landscape" if target.max_column > 5 else "portrait"
        target.page_setup.paperSize = target.PAPERSIZE_A4
        target.page_setup.fitToWidth, target.page_setup.fitToHeight = 1, 0
        target.print_options.horizontalCentered = True
        target.print_area = target.dimensions
        target.print_title_rows = "1:1"
        for column in range(1, target.max_column + 1):
            target.column_dimensions[get_column_letter(column)].width = 22
        target.column_dimensions["A"].width = 32
        target.column_dimensions["B"].width = 42
        for cell in target[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E4EDE9")
        for row in target:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.print_title_rows = f"{header_row}:{header_row}"
    if row_fields:
        sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(row_fields))}{sheet.max_row}"
    for cell in sheet[header_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E4EDE9")
    try:
        return _stable_xlsx(workbook)
    finally:
        workbook.close()


def _seller_kind(document_type):
    try:
        kind = DocumentType(document_type)
    except (ValueError, TypeError) as exc:
        raise ValidationError("请选择有效的文件类型。") from exc
    if kind not in SELLER_TYPES:
        raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                            "外部签发文件请上传真实原件，不能生成。")
    return kind


class SimpleExportService:
    def __init__(self, repository, blobs):
        self.repository, self.blobs = repository, blobs

    def _review(self, workspace, kind, *, human_confirmed):
        from validation.export_review import review_export
        report = review_export(workspace, kind, human_confirmed=human_confirmed)
        form = get_form(workspace, kind)
        from validation.export_review import active_source_document_ids
        source_ids = set(active_source_document_ids(workspace, kind))
        for check in report.checks:
            if check.required:
                source_ids.update(value for value in (check.left_document_id, check.right_document_id) if value)
        documents = {doc.document_id: doc for doc in workspace.documents}
        for document_id in sorted(source_ids & documents.keys()):
            document = documents[document_id]
            version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
            valid = False
            if version is not None and document.deleted_at is None:
                try:
                    valid = sha256(self.blobs.get(version.storage_path)).hexdigest() == version.sha256
                except Exception:
                    # No raw source content, storage paths, or exception text escapes.
                    pass
            report.checks.append(CrossCheckResult(case_id=workspace.case.case_id,
                rule_code="SOURCE_INTEGRITY", status=CrossCheckStatus.PASS if valid else CrossCheckStatus.FAIL,
                message="当前来源原件完整性校验通过。" if valid else "当前来源原件缺失、已移除或完整性校验失败。",
                required=True, left_document_id=document_id,
                left_version_id=version.version_id if version else document.current_version_id,
                left_field="sha256", left_value=version.sha256 if version else None,
                left_source=f"document:{document_id}:version:{document.current_version_id}",
                rule_version=report.rule_version))
        from .templates import selected_template, _load
        try:
            template = selected_template(workspace, kind)
            if template:
                content = self.blobs.get(template.storage_path)
                if sha256(content).hexdigest() != template.sha256:
                    raise ValueError("hash")
                from .forms import form_fields, form_row_fields
                wb, repeat = _load(content, kind, form_fields(form), form_row_fields(form))
                wb.close()
                if effective_rows(workspace.trade, form) and not repeat:
                    raise ValueError("missing rows")
        except Exception:
            report.checks.append(CrossCheckResult(case_id=workspace.case.case_id,
                rule_code="TEMPLATE_INTEGRITY", status=CrossCheckStatus.FAIL,
                message="所选模板缺失、损坏或不支持当前明细，请重新上传或使用默认排版。",
                required=True, rule_version=report.rule_version))
        return report

    def _output(self, workspace, kind, form, actor, report, human_confirmed, output_format, audience="internal"):
        from .templates import selected_template, render_template
        from .forms import form_fields, form_row_fields
        if output_format not in {"xlsx", "pdf"}:
            raise ValidationError("仅支持 Excel 或 PDF 导出。")
        if audience not in {"internal", "customer"}:
            raise ValidationError("请选择客户文件或内部核对资料包。")
        template = selected_template(workspace, kind)
        try:
            content = _render(workspace, kind, form, actor, report.checks,
                human_confirmed=human_confirmed, review=report, audience=audience)
            if output_format == "pdf":
                from .pdf_export import render_pdf
                return (render_pdf(content, DOC_LABELS[kind], audience=audience), "application/pdf",
                        "simple-pdf", LAYOUT_VERSION, layout_hash(kind, "pdf", form, workspace, audience))
            if template:
                data = self.blobs.get(template.storage_path)
                if sha256(data).hexdigest() != template.sha256:
                    raise ValidationError("所选模板完整性校验失败。")
                # Retain all standard audit/check/source sheets in the custom
                # workbook; its user layout is only the customer-facing layer.
                from openpyxl import load_workbook
                from io import BytesIO
                from copy import copy
                rows = effective_rows(workspace.trade, form)
                for row in rows:
                    if "amount" in ROW_FIELDS[kind] and not row.get("amount"):
                        quantity, price = number(row.get("quantity")), number(row.get("unit_price"))
                        if quantity is not None and price is not None:
                            row["amount"] = str(quantity * price)
                custom = load_workbook(BytesIO(render_template(data, kind,
                    effective_values(workspace.trade, form), rows,
                    human_confirmed=human_confirmed, actor_id=actor.user_id,
                    fields=form_fields(form), row_fields=form_row_fields(form), audience=audience)))
                if audience == "customer":
                    for name in list(custom.sheetnames):
                        if name in {"字段来源", "缺失与提示", "生成记录", "CrossBot核对记录"}:
                            del custom[name]
                    if not custom.worksheets:
                        raise ValidationError("模板没有可交付的业务工作表。")
                standard = load_workbook(BytesIO(content))
                try:
                    for source in standard:
                        if source.title == "文件":
                            continue
                        name = source.title if source.title not in custom.sheetnames else "核对-" + source.title
                        target = custom.create_sheet(name)
                        for row in source:
                            for cell in row:
                                new = target.cell(cell.row, cell.column, cell.value)
                                new.font, new.fill = copy(cell.font), copy(cell.fill)
                                new.border, new.alignment = copy(cell.border), copy(cell.alignment)
                                new.number_format = cell.number_format
                                if cell.data_type == "s":
                                    new.data_type = "s"
                    content = _stable_xlsx(custom)
                finally:
                    custom.close()
                    standard.close()
                return content, MEDIA_TYPE, template.template_id, str(template.version_number), template.sha256
            return content, MEDIA_TYPE, "simple-xlsx", LAYOUT_VERSION, layout_hash(kind, form=form, workspace=workspace, audience=audience)
        except CrossBotError:
            raise
        except Exception as exc:
            raise CrossBotError(ErrorCode.CONFIGURATION,
                "文件排版或保存失败，请检查内容中的特殊字符和模板后重试。") from exc

    def assess(self, case_id, actor, document_type, *, human_confirmed=False):
        """Assess current saved inputs without storing or confirming anything."""
        require(None, actor, "download")
        kind = _seller_kind(document_type)
        return self._review(self.repository.get(case_id), kind,
                            human_confirmed=human_confirmed is True)

    def preview(self, case_id, actor, document_type, *, output_format="xlsx", audience="internal") -> RenderedOutput:
        """Render an explicitly unconfirmed snapshot without storing or auditing it."""
        require(None, actor, "download")
        kind = _seller_kind(document_type)
        workspace = self.repository.get(case_id)
        form = get_form(workspace, kind)
        report = self._review(workspace, kind, human_confirmed=False)
        content, media, template_id, version, template_hash = self._output(
            workspace, kind, form, actor, report, False, output_format, audience)
        return RenderedOutput(content=content, media_type=media, extension=output_format,
            template_id=template_id, template_version=version, template_sha256=template_hash)

    def generate(self, case_id, actor, document_type, *, human_confirmed: bool,
                 expected_review_digest=None, output_format="xlsx", audience="internal"):
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, "generate")
        except CrossBotError:
            self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        if human_confirmed is not True:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先勾选已由真人核对本次导出内容。")
        kind = _seller_kind(document_type)
        form = get_form(workspace, kind)
        report = self._review(workspace, kind, human_confirmed=True)
        details = {"checks": [to_dict(check) for check in report.checks],
                   "input_digest": report.input_digest, "rule_version": report.rule_version}
        if expected_review_digest is not None and expected_review_digest != report.input_digest:
            raise CrossBotError(ErrorCode.CONFLICT, "资料或来源版本已变化，请重新核对当前核验结果。",
                                details=details)
        if not report.ready:
            failed = any(check.required and check.status is CrossCheckStatus.FAIL
                         for check in report.checks)
            raise CrossBotError(ErrorCode.CROSS_CHECK_FAILED if failed else ErrorCode.REVIEW_REQUIRED,
                "存在核验冲突，暂不能生成正式文件。" if failed else "资料或来源尚未完整核验，请补充后再生成正式文件。",
                details=details)
        checks = report.checks
        try:
            content, media, template_id, version, template_hash = self._output(
                workspace, kind, form, actor, report, True, output_format, audience)
            key = self.blobs.put(content)
        except Exception as exc:
            audit(workspace, actor, "generation_failed", "artifact", reason="文件排版或保存失败，未保存正式文件。")
            self.repository.save(workspace, expected_revision=workspace.revision)
            if isinstance(exc, CrossBotError):
                raise
            raise CrossBotError(ErrorCode.CONFIGURATION, "文件保存失败，请重试。") from exc
        versions = [item.version_number for item in workspace.artifacts
                    if item.document_type is kind]
        # Re-exporting CI must not make an unchanged PL disappear from inventory.
        # Preserve current drafts of other kinds while invalidating formal approval.
        other_current = []
        for item in workspace.artifacts:
            receipts = [event.after for event in workspace.audit if event.action == "simple_export_confirmed"
                        and event.entity_id == item.artifact_id and isinstance(event.after, dict)]
            item_audience = receipts[-1].get("audience", "internal") if receipts else "internal"
            if ((item.document_type is kind and item.filename.lower().endswith("." + output_format)
                    and item_audience == audience)
                    or item.status not in {"draft", "approved"}
                    or item.input_revision != workspace.trade.revision
                    or item.input_snapshot != to_dict(workspace.trade)
                    or item.source_versions != source_versions(workspace)):
                continue
            current_form = get_form(workspace, item.document_type)
            if (item.form_revision == current_form.revision
                    and item.form_snapshot == (to_dict(current_form) if current_form.revision else {})):
                other_current.append(item)
        invalidate(workspace, actor, "人工确认并导出新文件版本。")
        for item in other_current:
            item.status = "draft"
        artifact = GeneratedArtifact(document_type=kind,
            version_number=max(versions, default=0) + 1,
            input_revision=workspace.trade.revision, input_snapshot=to_dict(workspace.trade),
            source_versions=source_versions(workspace),
            form_snapshot=to_dict(form) if form.revision else {},
            form_revision=form.revision,
            template_id=template_id, template_version=version,
            template_sha256=template_hash,
            rule_version=report.rule_version, storage_path=key,
            sha256=sha256(content).hexdigest(), media_type=media,
            filename=f"{kind.value}-r{workspace.trade.revision}-v{max(versions, default=0) + 1}"
                     + ("-customer" if audience == "customer" else "") + f".{output_format}",
            created_by=actor.user_id, status="draft", checks=checks)
        workspace.artifacts.append(artifact)
        audit(workspace, actor, "simple_export_confirmed", "artifact", artifact.artifact_id,
              after={"human_confirmed": True, "input_revision": artifact.input_revision,
                     "sha256": artifact.sha256, "source_versions": artifact.source_versions,
                     "form_revision": artifact.form_revision,
                     "strict_review": True, "review_input_digest": report.input_digest,
                     "rule_version": report.rule_version,
                     "output_format": output_format, "template_id": template_id,
                     "audience": audience,
                     "template_version": version, "template_sha256": template_hash,
                     "layout_schema_sha256": layout_hash(kind, output_format, form, workspace, audience),
                     "warning_count": sum(c.status is not CrossCheckStatus.PASS for c in checks)},
              reason="真人核对本次导出；缺失和规则提示随文件保留。")
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

