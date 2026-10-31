"""Focused human correction of a current deterministic review finding."""
from copy import deepcopy
from dataclasses import fields
import re

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (ReviewEditArea as A, ReviewEditTarget, CrossCheckStatus,
    DocumentType, TradeLine, TradeAdjustment, TradeAdjustmentKind, TradePackage, from_dict, to_dict)
from documents.catalog import FORM_FIELDS, ROW_FIELDS
from documents.forms import FormService, get_form, input_rows
from documents.manual_inputs import ManualInputService, manual_source
from documents.input_validation import validate_input_text
from review.policy import require, audit
from validation.export_review import review_export


def _text(value):
    if not isinstance(value, str) or len(value) > 4000:
        raise ValidationError("修改值须为不超过 4000 字的文本。")
    validate_input_text(value)
    return value.strip()


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or re.fullmatch(r"[0-9]+", str(value)) is None:
        raise ValidationError("箱数须明确填写非负整数，不可留空。")
    return int(value)


def _collection(name, rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValidationError("明细须为列表且最多 1000 行。")
    model = {"line_items": TradeLine, "adjustments": TradeAdjustment, "packages": TradePackage}[name]
    allowed = {field.name for field in fields(model)}
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValidationError("明细包含不支持的字段。")
        values = {}
        for key, value in row.items():
            if key == "supplied_fields":
                continue
            if key == "included_in_invoice_price":
                if not isinstance(value, bool):
                    raise ValidationError("费用包含标记须为布尔值。")
                values[key] = value
            elif key == "packages":
                if name == "line_items" and (value is None or isinstance(value, str) and not value.strip()):
                    # The shared line model stores zero for legacy compatibility;
                    # absence is retained by omitting its explicit-supply marker.
                    continue
                values[key] = _integer(value)
            elif key == "kind":
                try:
                    values[key] = TradeAdjustmentKind(value)
                except (ValueError, TypeError) as exc:
                    raise ValidationError("费用类别无效。") from exc
            else:
                values[key] = _text(value)
        if name == "line_items":
            for key in ("product_code", "description", "quantity", "unit_price", "unit", "gross_weight_kg", "net_weight_kg"):
                values.setdefault(key, "")
            values["supplied_fields"] = [key for key in values if key != "supplied_fields"]
        elif name == "adjustments":
            values.setdefault("amount", "")
            values.setdefault("currency", "")
        result.append(model(**values))
    return result


class _CorrectionRepository:
    """Append exact human verification in the same CAS save as the correction."""
    def __init__(self, repository, actor, target, reason, preserved_row_origins=None):
        self.repository, self.actor, self.target, self.reason = repository, actor, target, reason
        self.preserved_row_origins = preserved_row_origins

    def get(self, case_id):
        return self.repository.get(case_id)

    def save(self, workspace, expected_revision):
        target = self.target
        if target.area is A.TRADE:
            event = next((event for event in reversed(workspace.audit) if event.action == "trade_updated"), None)
            if event is not None:
                event.after["review_edit_target"] = to_dict(target)
        elif target.area in {A.FORM, A.MANUAL_SOURCE} and not target.record_id:
            form = get_form(workspace, target.document_type)
            event = next((event for event in reversed(workspace.audit) if event.action == "document_form_saved"
                and event.entity_id == target.document_type.value), None)
            if event is not None:
                event.after["review_edit_target"] = to_dict(target)
                if self.preserved_row_origins is not None:
                    event.after["preserved_row_origins"] = to_dict(self.preserved_row_origins)
            audit(workspace, self.actor, "review_value_verified", "document_form", target.document_type.value,
                after={"field_name": target.field_name, "row_number": target.row_number,
                    "values": deepcopy(form.values), "rows": deepcopy(form.rows)}, reason=self.reason)
        self.repository.save(workspace, expected_revision=expected_revision)


class ReviewEditingService:
    def __init__(self, repository, cases):
        self.repository, self.cases = repository, cases

    def save(self, case_id, actor, selected_kind, target, *, value=None, rows=None,
             reason, expected_revision, expected_review_digest):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已更新，请重新查看审核结果后修改。")
        if not isinstance(target, ReviewEditTarget):
            raise ValidationError("请选择审核结果提供的修改位置。")
        try:
            target = from_dict(ReviewEditTarget, to_dict(target))
            selected_kind = DocumentType(selected_kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("审核修改位置无效。") from exc
        report = review_export(workspace, selected_kind, human_confirmed=True)
        if not expected_review_digest or report.input_digest != expected_review_digest:
            raise CrossBotError(ErrorCode.CONFLICT, "审核输入已变化，请重新核对后修改。")
        targets = [item for check in report.checks if check.status is not CrossCheckStatus.PASS for item in check.edit_targets]
        if target not in targets:
            raise ValidationError("此修改位置已不属于当前待处理问题，请重新选择。")
        return self._apply(workspace, actor, target, value=value, rows=rows,
            reason=reason, expected_revision=expected_revision)

    def _apply(self, workspace, actor, target, *, value=None, rows=None, reason, expected_revision):
        """Shared mutation engine; callers must establish their own target allowlist."""
        case_id = workspace.case.case_id
        require(workspace, actor, "manual_field")
        if workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已更新，请重新核对填写位置。")
        if target.area in {A.SOURCE, A.SOURCE_LINKS}:
            raise ValidationError("原件核对或来源关联须使用专门的原件确认及重新关联入口。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("请填写不超过 2000 字的修改依据。")
        validate_input_text(reason)
        preserved = None
        if target.area is A.FORM and target.row_number is not None:
            from documents.provenance import form_value_origins
            preserved = [origin for origin in form_value_origins(workspace, target.document_type, include_inputs=True)
                if origin.row_number is not None and not (
                    origin.row_number == target.row_number and origin.field_name == target.field_name)]
        repository = _CorrectionRepository(self.repository, actor, target, reason.strip(), preserved)
        if target.area is A.TRADE:
            trade = deepcopy(workspace.trade)
            name, number = target.field_name, target.row_number
            path = re.fullmatch(r"(line_items|adjustments|packages)\[([1-9][0-9]*)\]\.([a-z_]+)", name)
            if path:
                group, raw_number, name = path.groups()
                number = int(raw_number)
                collection = getattr(trade, group)
                if number > len(collection) or name not in {field.name for field in fields(collection[number - 1])} or name == "supplied_fields":
                    raise ValidationError("对应业务明细行或字段已不存在。")
                item = collection[number - 1]
                if name == "included_in_invoice_price":
                    if not isinstance(value, bool): raise ValidationError("费用包含标记须为布尔值。")
                    corrected = value
                elif name == "kind": corrected = TradeAdjustmentKind(value)
                elif name == "packages": corrected = _integer(value)
                else: corrected = _text(value)
                setattr(item, name, corrected)
                if group == "line_items": item.supplied_fields = sorted(set(item.supplied_fields) | {name})
            elif name in {"rows", "line_items", "adjustments", "packages", "package_details"} and number is None:
                group = "line_items" if name == "rows" else "packages" if name == "package_details" else name
                setattr(trade, group, _collection(group, rows))
            elif number is not None:
                if number < 1 or number > len(trade.line_items) or name not in {field.name for field in fields(TradeLine)} or name == "supplied_fields":
                    raise ValidationError("对应订单产品行或字段已不存在。")
                item = trade.line_items[number - 1]
                setattr(item, name, _integer(value) if name == "packages" else _text(value))
                item.supplied_fields = sorted(set(item.supplied_fields) | {name})
            else:
                allowed = {field.name for field in fields(type(trade))} - {
                    "line_items", "packages", "adjustments", "revision", "confirmed_by", "confirmed_at", "supplied_fields"}
                if name not in allowed: raise ValidationError("对应订单字段不支持直接修改。")
                if name == "invoice_adjustments_confirmed":
                    if not isinstance(value, bool): raise ValidationError("确认标记须为布尔值。")
                    corrected = value
                else: corrected = _text(value)
                setattr(trade, name, corrected)
                trade.supplied_fields = sorted(set(trade.supplied_fields) | {name})
            from case_workflow.service import CaseService
            cases = CaseService(repository, self.cases.rules, identity=self.cases.identity,
                                defaults_store=self.cases.defaults_store)
            return cases.update_trade(case_id, actor, trade, reason, expected_revision=expected_revision)
        if target.area not in {A.FORM, A.MANUAL_SOURCE} or target.document_type not in FORM_FIELDS:
            raise ValidationError("此修改区域暂不支持直接保存。")
        source = (manual_source(workspace, target.document_type, target.record_id)
                  if target.area is A.MANUAL_SOURCE else get_form(workspace, target.document_type))
        if source is None: raise ValidationError("这份人工资料已不存在。")
        values = deepcopy(source.values)
        current_rows = deepcopy(source.rows)
        if target.area is A.FORM and not current_rows:
            current_rows = input_rows(workspace.trade, source)
        name, number = target.field_name, target.row_number
        if name == "rows" and number is None:
            current_rows = rows
        elif number is not None:
            if number < 1 or number > len(current_rows) or name not in ROW_FIELDS[target.document_type]:
                raise ValidationError("对应表单明细行或字段已不存在。")
            current_rows[number - 1][name] = _text(value)
        else:
            if name not in FORM_FIELDS[target.document_type]: raise ValidationError("对应表单字段不支持直接修改。")
            values[name] = _text(value)
            # Header-only edits do not materialize inherited product rows.
            current_rows = deepcopy(source.rows)
        if target.area is A.MANUAL_SOURCE:
            inputs = ManualInputService(repository)
            if target.record_id:
                return inputs.save_record(case_id, actor, target.document_type, values, current_rows,
                    reason, record_id=target.record_id, title=source.title, expected_revision=expected_revision)
            return inputs.save(case_id, actor, target.document_type, values, current_rows, reason, expected_revision=expected_revision)
        return FormService(repository).save(case_id, actor, target.document_type, values, current_rows, reason,
            manual_fields=[name] if number is None and name != "rows" else None,
            rows_manually_edited=True if number is not None or name == "rows" else None,
            expected_workspace_revision=expected_revision, preserve_inherited_rows=number is None and name != "rows")
