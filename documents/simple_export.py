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
    sheet = workbook.create_sheet("字段来源")
    _text_row(sheet, ["字段/行", "当前填写值", "填写方式", "候选值", "来源文件",
                      "来源版本", "来源状态", "页码", "工作表", "单元格/位置",
                      "办理人", "操作时间", "来源说明"])
    versions = source_versions(workspace)
    for origin in form_value_origins(workspace, form.document_type):
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
        for candidate in candidates:
            filename, version, state = source(candidate)
            _text_row(sheet, [f"候选字段 {name}", "", "候选（未采用或待核对）", candidate.value,
                filename, version, state, candidate.page_number, candidate.sheet_name,
                candidate.cell_reference, "", "", source_location(candidate)])
    for candidate in form.row_candidates:
        filename, version, state = source(candidate)
        _text_row(sheet, [f"来源明细行 {candidate.row_number}", "", "候选（未采用或待核对）",
            json.dumps(candidate.values, ensure_ascii=False, sort_keys=True), filename, version,
            state, candidate.page_number, candidate.table_name,
            json.dumps(candidate.cell_references, ensure_ascii=False, sort_keys=True), "", "",
            "原表候选明细；逐单元格实际来源见上方，候选不等于人工采用。"])


def _render(workspace, kind, form, actor, checks, *, human_confirmed=True, review=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "文件"
    _text_row(sheet, [DOC_LABELS[kind]])
    _text_row(sheet, ["人工确认", "是" if human_confirmed else "否（待人工确认）",
                      "确认人", actor.user_id if human_confirmed else ""])
    _text_row(sheet, ["文件说明", "卖方填写稿；空白表示资料缺失。人工确认不代表外部签发或规则全部通过。"
        if human_confirmed else "未人工确认预览；不得作为已确认文件使用。空白表示资料缺失，预览不产生正式文件版本或批准。"])
    values = effective_values(workspace.trade, form)
    for name, label in FORM_FIELDS[kind].items():
        _data_row(sheet, ["", name], [label, values.get(name, "")])
    _text_row(sheet, [])
    _text_row(sheet, list(ROW_FIELDS[kind].values()))
    header_row = sheet.max_row
    rows = effective_rows(workspace.trade, form)
    output_rows = []
    for row in rows:
        row = dict(row)
        if "amount" in ROW_FIELDS[kind] and not row.get("amount"):
            quantity, price = number(row.get("quantity")), number(row.get("unit_price"))
            if quantity is not None and price is not None:
                try:
                    row["amount"] = str(quantity * price)
                except DecimalException:
                    row["amount"] = ""
        output_rows.append(row)
        _data_row(sheet, list(ROW_FIELDS[kind]), [row.get(name, "") for name in ROW_FIELDS[kind]])
    _totals(workbook, output_rows, ROW_FIELDS[kind], values)
    _provenance(workbook, workspace, form, values, output_rows)

    if workspace.trade.adjustments:
        adjustments = workbook.create_sheet("费用与调整")
        _text_row(adjustments, ["类别", "说明", "金额", "币种", "包含在发票货价内"])
        for item in workspace.trade.adjustments:
            _data_row(adjustments, ["", "", "amount", "", ""],
                [item.kind.value, item.name, item.amount, item.currency, item.included_in_invoice_price])

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
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(ROW_FIELDS[kind]))}{sheet.max_row}"
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
        source_ids = set(form.source_document_ids)
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
        return report

    def assess(self, case_id, actor, document_type, *, human_confirmed=False):
        """Assess current saved inputs without storing or confirming anything."""
        require(None, actor, "download")
        kind = _seller_kind(document_type)
        return self._review(self.repository.get(case_id), kind,
                            human_confirmed=human_confirmed is True)

    def preview(self, case_id, actor, document_type) -> RenderedOutput:
        """Render an explicitly unconfirmed snapshot without storing or auditing it."""
        require(None, actor, "download")
        kind = _seller_kind(document_type)
        workspace = self.repository.get(case_id)
        form = get_form(workspace, kind)
        report = self._review(workspace, kind, human_confirmed=False)
        content = _render(workspace, kind, form, actor, report.checks,
                          human_confirmed=False, review=report)
        return RenderedOutput(content=content, media_type=MEDIA_TYPE, extension="xlsx",
            template_id="simple-xlsx", template_version=LAYOUT_VERSION,
            template_sha256=sha256(LAYOUT_VERSION.encode()).hexdigest())

    def generate(self, case_id, actor, document_type, *, human_confirmed: bool,
                 expected_review_digest=None):
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
        content = _render(workspace, kind, form, actor, checks, review=report)
        key = self.blobs.put(content)
        versions = [item.version_number for item in workspace.artifacts
                    if item.document_type is kind]
        # Re-exporting CI must not make an unchanged PL disappear from inventory.
        # Preserve current drafts of other kinds while invalidating formal approval.
        other_current = []
        for item in workspace.artifacts:
            if (item.document_type is kind or item.status not in {"draft", "approved"}
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
            template_id="simple-xlsx", template_version=LAYOUT_VERSION,
            template_sha256=sha256(LAYOUT_VERSION.encode()).hexdigest(),
            rule_version=report.rule_version, storage_path=key,
            sha256=sha256(content).hexdigest(), media_type=MEDIA_TYPE,
            filename=f"{kind.value}-r{workspace.trade.revision}-v{max(versions, default=0) + 1}.xlsx",
            created_by=actor.user_id, status="draft", checks=checks)
        workspace.artifacts.append(artifact)
        audit(workspace, actor, "simple_export_confirmed", "artifact", artifact.artifact_id,
              after={"human_confirmed": True, "input_revision": artifact.input_revision,
                     "sha256": artifact.sha256, "source_versions": artifact.source_versions,
                     "form_revision": artifact.form_revision,
                     "strict_review": True, "review_input_digest": report.input_digest,
                     "rule_version": report.rule_version,
                     "warning_count": sum(c.status is not CrossCheckStatus.PASS for c in checks)},
              reason="真人核对本次导出；缺失和规则提示随文件保留。")
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace
