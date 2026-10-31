"""Plain, versioned seller XLSX exports after one explicit human confirmation."""
from __future__ import annotations

from hashlib import sha256
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CrossCheckResult, CrossCheckStatus, DocumentType,
                         GeneratedArtifact, to_dict)
from review.policy import audit, invalidate, require, source_versions
from validation.rules import number, validate_trade
from .catalog import DOC_LABELS, FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from .forms import (effective_values, form_conflicts, get_form,
                    missing_form_fields)
from .workbook_renderer import _stable_xlsx

LAYOUT_VERSION = "simple-xlsx-1"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _text_row(sheet, values):
    """All supplied text stays text, including values beginning with '='."""
    sheet.append(["" if value is None else str(value) for value in values])
    for cell in sheet[sheet.max_row]:
        cell.data_type = "s"


def _render(workspace, kind, form, actor, checks):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "文件"
    _text_row(sheet, [DOC_LABELS[kind]])
    _text_row(sheet, ["人工确认", "是", "确认人", actor.user_id])
    _text_row(sheet, ["文件说明", "卖方填写稿；空白表示资料缺失。人工确认不代表外部签发或规则全部通过。"])
    values = effective_values(workspace.trade, form)
    for name, label in FORM_FIELDS[kind].items():
        _text_row(sheet, [label, values.get(name, "")])
    _text_row(sheet, [])
    _text_row(sheet, list(ROW_FIELDS[kind].values()))
    rows = form.rows
    if not rows and {"description", "quantity"} <= ROW_FIELDS[kind].keys():
        rows = [to_dict(line) for line in workspace.trade.line_items]
    for row in rows:
        row = dict(row)
        if "amount" in ROW_FIELDS[kind] and not row.get("amount"):
            quantity, price = number(row.get("quantity")), number(row.get("unit_price"))
            if quantity is not None and price is not None:
                row["amount"] = str(quantity * price)
        _text_row(sheet, [row.get(name, "") for name in ROW_FIELDS[kind]])

    if workspace.trade.adjustments:
        adjustments = workbook.create_sheet("费用与调整")
        _text_row(adjustments, ["类别", "说明", "金额", "币种", "包含在发票货价内"])
        for item in workspace.trade.adjustments:
            _text_row(adjustments, [item.kind.value, item.name, item.amount,
                                   item.currency, item.included_in_invoice_price])

    warnings = workbook.create_sheet("缺失与提示")
    _text_row(warnings, ["规则", "结果", "提示"])
    for check in checks:
        if check.status is not CrossCheckStatus.PASS:
            _text_row(warnings, [check.rule_code, check.status.value, check.message])
    if warnings.max_row == 1:
        _text_row(warnings, ["", "", "当前检查未发现缺失或冲突。"])

    trace = workbook.create_sheet("生成记录")
    for label, value in (("生成方式", LAYOUT_VERSION), ("人工确认人", actor.user_id),
                         ("交易修订", workspace.trade.revision),
                         ("来源版本", json.dumps(source_versions(workspace), ensure_ascii=False,
                                                sort_keys=True)),
                         ("表单修订", form.revision)):
        _text_row(trace, [label, value])
    for target in workbook:
        target.freeze_panes = "A2"
        target.column_dimensions["A"].width = 30
        target.column_dimensions["B"].width = 65
        for cell in target[1]:
            cell.font = Font(bold=True)
        for row in target:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    try:
        return _stable_xlsx(workbook)
    finally:
        workbook.close()


class SimpleExportService:
    def __init__(self, repository, blobs):
        self.repository, self.blobs = repository, blobs

    def generate(self, case_id, actor, document_type, *, human_confirmed: bool):
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, "generate")
        except CrossBotError:
            self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        if human_confirmed is not True:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先勾选已由真人核对本次导出内容。")
        try:
            kind = DocumentType(document_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的文件类型。") from exc
        if kind not in SELLER_TYPES:
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                                "外部签发文件请上传真实原件，不能生成。")
        form = get_form(workspace, kind)
        # Rules are evidence and warnings, never an additional approval workflow.
        checks = [check for check in validate_trade(workspace)
                  if check.rule_code != "TRADE_HUMAN_CONFIRMED"]
        for code, details, status in (
            ("FORM_COMPLETE", missing_form_fields(workspace.trade, form), CrossCheckStatus.PENDING_REVIEW),
            ("FORM_CONSISTENCY", form_conflicts(workspace.trade, form), CrossCheckStatus.FAIL),
        ):
            checks.append(CrossCheckResult(case_id=case_id, rule_code=code,
                status=status if details else CrossCheckStatus.PASS,
                message="；".join(details) if details else "未发现缺失或冲突",
                left_value=details, left_source=f"form:{kind.value}:r{form.revision}",
                rule_version=LAYOUT_VERSION))
        content = _render(workspace, kind, form, actor, checks)
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
            rule_version=LAYOUT_VERSION, storage_path=key,
            sha256=sha256(content).hexdigest(), media_type=MEDIA_TYPE,
            filename=f"{kind.value}-r{workspace.trade.revision}-v{max(versions, default=0) + 1}.xlsx",
            created_by=actor.user_id, status="draft", checks=checks)
        workspace.artifacts.append(artifact)
        audit(workspace, actor, "simple_export_confirmed", "artifact", artifact.artifact_id,
              after={"human_confirmed": True, "input_revision": artifact.input_revision,
                     "sha256": artifact.sha256, "source_versions": artifact.source_versions,
                     "form_revision": artifact.form_revision,
                     "warning_count": sum(c.status is not CrossCheckStatus.PASS for c in checks)},
              reason="真人核对本次导出；缺失和规则提示随文件保留。")
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace
