"""Unconfirmed W1 handoff and deterministic source comparisons."""
from copy import deepcopy
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CrossCheckResult, CrossCheckStatus, DocumentStatus,
                         FieldValueStatus, TradeLine)
from validation.normalizers import normalize_currency, normalize_decimal, normalize_text


FIELD_LABELS = {
    "order_no": "订单号", "buyer": "买方", "destination_country": "目的国",
    "product_code": "产品编码", "description": "品名", "quantity": "数量",
    "unit_price": "单价", "currency": "币种", "incoterm": "贸易术语",
    "payment_terms": "付款条件",
}
LINE_FIELDS = ("product_code", "description", "quantity", "unit_price")
FIELD_ALIASES = {"buyer": ("buyer", "customer_name"),
                 "description": ("description", "product_name")}


def read_order_draft(metadata):
    draft = metadata.get("w1_order_draft")
    if draft is None:
        return None
    if not isinstance(draft, dict) or not isinstance(draft.get("fields"), dict):
        raise ValidationError("W1 订单草稿格式不正确，请返回 W1 重新创建。")
    draft = deepcopy(draft)
    if not isinstance(draft.get("inquiry_id"), str) or not draft["inquiry_id"].strip():
        raise ValidationError("W1 订单草稿缺少询盘编号。")
    if not isinstance(draft.get("created_by"), str):
        raise ValidationError("W1 订单草稿缺少创建来源。")
    messages = draft.get("messages")
    if not isinstance(messages, list) or any(not isinstance(message, dict)
            or not isinstance(message.get("role"), str)
            or not isinstance(message.get("content"), str) for message in messages):
        raise ValidationError("W1 订单草稿的原始历史格式不正确。")
    fields = {}
    for name in FIELD_LABELS:
        item = draft["fields"].get(name, {})
        if not isinstance(item, dict) or any(item.get(key) is not None
                and not isinstance(item[key], str) for key in ("value", "evidence")):
            raise ValidationError(f"W1 草稿的{FIELD_LABELS[name]}格式不正确。")
        fields[name] = {"value": item.get("value"), "evidence": item.get("evidence"),
                        "confirmed": False}
    draft["fields"] = fields
    return draft


def candidate_trade(trade, draft):
    candidate = deepcopy(trade)
    values = {name: (item["value"] or "").strip() for name, item in draft["fields"].items()}
    changed = False
    if candidate.revision == 1 and not candidate.confirmed_by:
        for name in ("order_no", "buyer", "destination_country", "currency", "incoterm"):
            if not values[name] and name not in candidate.supplied_fields and getattr(candidate, name):
                setattr(candidate, name, "")
                changed = True
    for name, value in values.items():
        if name in LINE_FIELDS or not value or str(getattr(candidate, name) or "").strip():
            continue
        if name == "currency":
            value = normalize_currency(value)
        setattr(candidate, name, value)
        candidate.supplied_fields = list(dict.fromkeys([*candidate.supplied_fields, name]))
        changed = True
    if any(values[name] for name in LINE_FIELDS):
        if len(candidate.line_items) > 1:
            raise ValidationError("现有订单包含多项产品，请在编辑订单资料中人工配对草稿。")
        if not candidate.line_items:
            candidate.line_items.append(TradeLine("", "", "", "", unit="",
                gross_weight_kg="", net_weight_kg=""))
        line = candidate.line_items[0]
        if line.product_code and values["product_code"] and line.product_code != values["product_code"]:
            raise ValidationError("草稿与现有产品编码不同，请人工处理，不能自动配对。")
        for name in LINE_FIELDS:
            if values[name] and not str(getattr(line, name) or "").strip():
                setattr(line, name, values[name])
                line.supplied_fields = list(dict.fromkeys([*line.supplied_fields, name]))
                changed = True
    if not changed:
        raise ValidationError("草稿没有可补入的空字段；现有资料请在编辑订单资料中人工修改。")
    candidate.confirmed_by, candidate.confirmed_at = None, None
    return candidate


def import_order_draft(cases, case_id, actor, metadata, *, expected_revision):
    workspace = cases.get(case_id, actor)
    draft = read_order_draft(metadata)
    if draft is None:
        raise ValidationError("当前 Case 没有 W1 订单草稿。")
    trade = candidate_trade(workspace.trade, draft)
    source = {key: draft[key] for key in ("inquiry_id", "created_by", "fields")}
    return cases.update_trade(case_id, actor, trade,
        "人工导入 W1 未确认候选；来源：" + json.dumps(source, ensure_ascii=False, sort_keys=True),
        expected_revision=expected_revision)


def _normalize(name, value):
    if name in {"quantity", "unit_price"}:
        number = normalize_decimal(value)
        if number < 0 or (name == "quantity" and number == 0):
            raise ValidationError("数量或单价不在有效范围。")
        return number
    return normalize_currency(value) if name == "currency" else normalize_text(value)


def compare_order_draft(workspace, metadata, document_id=None):
    draft = read_order_draft(metadata)
    if draft is None:
        return []
    document = next((item for item in workspace.documents
        if item.document_id == document_id and item.deleted_at is None), None)
    if document_id is not None and document is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "未找到当前 Case 的平台订单原件。")
    version = next((item for item in document.versions
        if item.version_id == document.current_version_id), None) if document else None
    fields = version.fields if version and version.status in {
        DocumentStatus.PARSED, DocumentStatus.MANUALLY_ENTERED} else []
    checks = []
    for name in FIELD_LABELS:
        draft_field = draft["fields"][name]
        matches = [item for item in fields if item.name in FIELD_ALIASES.get(name, (name,))]
        field = matches[0] if len(matches) == 1 else None
        right = None
        if field and field.status not in {FieldValueStatus.MISSING, FieldValueStatus.REJECTED}:
            right = (field.confirmed_value if field.status is FieldValueStatus.CONFIRMED
                and field.confirmed_by and field.confirmed_at else field.value)
        location = " / ".join(part for part in (
            f"page:{field.page_number}" if field and field.page_number is not None else "",
            field.sheet_name if field else "", field.cell_reference if field else "",
            field.name if field else "") if part)
        check = CrossCheckResult(case_id=workspace.case.case_id,
            rule_code="W1_PLATFORM_" + name.upper(), left_field=name,
            right_field=field.name if field else name, left_value=draft_field["value"],
            right_value=right, right_document_id=document_id,
            left_source=f"W1 inquiry:{draft['inquiry_id']} / created_by:{draft['created_by']} / "
                + (draft_field["evidence"] or "无原文引用"),
            right_source=(f"{version.original_filename} / v{version.version_number} / {location}"
                if version else "尚未上传或选择平台订单"),
            right_version_id=version.version_id if version else None,
            scope="w1_platform_order_candidate",
            comparison_policy={"human_review_required": True, "draft_confirmed": False,
                               "text_policy": "trim_only", "numeric_policy": "exact_decimal"})
        try:
            check.normalized_left = _normalize(name, check.left_value)
            check.normalized_right = _normalize(name, right)
        except ValidationError:
            check.message = "缺失、格式无效或来源不唯一，待人工补充复核。"
        else:
            if check.normalized_left != check.normalized_right:
                check.status = CrossCheckStatus.FAIL
                check.message = "草稿与平台订单不一致，请人工核对原件。"
            else:
                check.message = "候选值一致，仍须人工复核；W1 草稿不构成确认或批准。"
        checks.append(check)
    return checks
