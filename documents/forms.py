"""Human-maintained seller forms and deterministic HTML worksheets."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, DecimalException, InvalidOperation
from hashlib import sha256
from html import escape
import json
import re

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import CaseStatus, DocumentForm, DocumentType, TradeData, to_dict, utc_now
from core.ports import RenderedOutput
from review.policy import audit, invalidate, require
from .catalog import (DOC_LABELS, FORM_FIELDS, REQUIRED_FIELDS, REQUIRED_ROW_FIELDS,
                      ROW_FIELDS, SELLER_TYPES)

FORM_TEMPLATE_VERSION = "w3-seller-forms-1"
TRADE_ROW_TYPES = frozenset({DocumentType.PROFORMA_INVOICE,
    DocumentType.COMMERCIAL_INVOICE, DocumentType.PACKING_LIST})


def get_form(workspace, kind) -> DocumentForm:
    kind = DocumentType(kind)
    existing = next((form for form in workspace.document_forms if form.document_type is kind), None)
    return deepcopy(existing) if existing else DocumentForm(document_type=kind, revision=0)


def effective_values(trade, form):
    values = {name: str(getattr(trade, name, "") or "") for name in FORM_FIELDS[form.document_type]}
    values.update(form.values)
    return values


def missing_form_fields(trade, form):
    values = effective_values(trade, form)
    missing = [name for name in REQUIRED_FIELDS[form.document_type] if not values.get(name, "").strip()]
    has_trade_rows = form.document_type in TRADE_ROW_TYPES and bool(trade.line_items)
    if not has_trade_rows and (not form.rows or not any(any(value.strip() for value in row.values()) for row in form.rows)):
        missing.append("rows")
    for number, row in enumerate(form.rows, 1):
        missing.extend(f"rows[{number}].{name}" for name in REQUIRED_ROW_FIELDS[form.document_type]
                       if not row.get(name, "").strip())
        if form.document_type in TRADE_ROW_TYPES and not row.get("product_code", "").strip():
            missing.append(f"rows[{number}].product_code")
    return missing


def form_conflicts(trade, form):
    """Deterministic consistency checks; no inference about compliance or signatures."""
    errors = []
    for name in ("order_no", "seller", "buyer", "currency", "destination_country", "incoterm"):
        value = form.values.get(name, "").strip()
        expected = str(getattr(trade, name, "") or "").strip()
        if value and expected and value.casefold() != expected.casefold():
            errors.append(name + ": 与已确认交易不一致")
    header_numbers = {}
    for name in ("amount_due", "opening_balance", "closing_balance", "total_amount", "volume_cbm"):
        value = form.values.get(name, "").strip()
        if not value:
            continue
        try:
            number = Decimal(value)
            if not number.is_finite() or (name not in {"opening_balance", "closing_balance"} and number < 0):
                raise InvalidOperation
            header_numbers[name] = number
        except DecimalException:
            errors.append(f"{name}: 不是有效十进制数字")
    if "amount_due" in header_numbers:
        # A payment request may cover a deposit or instalment, rather than the whole order.
        try:
            total = sum((Decimal(line.quantity) * Decimal(line.unit_price)
                         for line in trade.line_items), Decimal(0))
            if not total.is_finite() or header_numbers["amount_due"] > total:
                errors.append("amount_due: 不得超过已确认订单总额")
        except DecimalException:
            errors.append("amount_due: 已确认订单总额无法核验")
    dates = {}
    for name, value in form.values.items():
        if not value.strip() or not (name.endswith(("_date", "_until", "_on"))
                                    or name in {"period_start", "period_end"}):
            continue
        try:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                raise ValueError
            dates[name] = date.fromisoformat(value)
        except (ValueError, TypeError):
            errors.append(f"{name}: 日期须为有效的 YYYY-MM-DD")
    if "period_start" in dates and "period_end" in dates and dates["period_start"] > dates["period_end"]:
        errors.append("period_end: 账期结束不得早于开始")
    numeric = {"quantity", "unit_price", "amount", "packages", "gross_weight_kg",
        "net_weight_kg", "debit", "credit", "balance"}
    for index, row in enumerate(form.rows, 1):
        numbers = {}
        for name, value in row.items():
            if name not in numeric or not value.strip():
                continue
            try:
                number = Decimal(value)
                if not number.is_finite() or (name != "balance" and number < 0):
                    raise InvalidOperation
                numbers[name] = number
            except DecimalException:
                errors.append(f"rows[{index}].{name}: 不是有效十进制数字")
        if {"quantity", "unit_price", "amount"} <= numbers.keys():
            try:
                if numbers["quantity"] * numbers["unit_price"] != numbers["amount"]:
                    errors.append(f"rows[{index}].amount: 与数量乘单价不一致")
            except DecimalException:
                errors.append(f"rows[{index}].amount: 数字超出可计算范围")
        if {"gross_weight_kg", "net_weight_kg"} <= numbers.keys():
            if numbers["gross_weight_kg"] < numbers["net_weight_kg"]:
                errors.append(f"rows[{index}].gross_weight_kg: 毛重小于净重")
    if form.document_type in TRADE_ROW_TYPES and form.rows:
        _compare_trade_rows(trade, form, errors)
    return errors


def _compare_trade_rows(trade, form, errors):
    """The legacy XLSX main sheet uses trade rows; supplementary rows must agree."""
    line_by_code = {line.product_code.strip(): line for line in trade.line_items}
    if len(line_by_code) != len(trade.line_items):
        errors.append("rows: 订单产品编码不唯一，无法核对明细")
        return
    seen = set()
    numeric_fields = {"quantity", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"}
    text_fields = {"description", "unit", "country_of_origin", "marks_and_numbers", "package_contents"}
    for index, row in enumerate(form.rows, 1):
        code = row.get("product_code", "").strip()
        if not code:
            errors.append(f"rows[{index}].product_code: 需要产品编码关联订单明细")
            continue
        if code in seen:
            errors.append(f"rows[{index}].product_code: 产品编码重复")
        seen.add(code)
        line = line_by_code.get(code)
        if line is None:
            errors.append(f"rows[{index}].product_code: 不在已确认订单中")
            continue
        for name in (numeric_fields | text_fields) & ROW_FIELDS[form.document_type].keys():
            value = row.get(name, "").strip()
            # Blank supplementary cells stay blank in output; required blanks are pending.
            if not value:
                continue
            expected = str(getattr(line, name, "") or "").strip()
            if name in numeric_fields:
                try:
                    actual_number, expected_number = Decimal(value), Decimal(expected)
                    matches = actual_number.is_finite() and expected_number.is_finite() and actual_number == expected_number
                except DecimalException:
                    matches = False
            else:
                matches = value.casefold() == expected.casefold()
            if not matches:
                errors.append(f"rows[{index}].{name}: 与已确认订单产品 {code} 不一致")
    if seen != set(line_by_code):
        errors.append("rows: 表单产品编码必须完整对应已确认订单")


class FormService:
    def __init__(self, repository):
        self.repository = repository

    def save(self, case_id, actor, kind, values, rows, reason, confirm=False):
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, "manual_field")
        except CrossBotError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的文件类型。") from exc
        if kind not in FORM_FIELDS:
            raise ValidationError("该类型暂无结构化记录字段。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("保存表单必须填写不超过 2000 字的依据。")
        if not isinstance(confirm, bool):
            raise ValidationError("确认标记须为布尔值。")
        if not isinstance(values, dict) or set(values) - set(FORM_FIELDS[kind]):
            raise ValidationError("表单包含不支持的字段。")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValidationError("明细须为列表且最多 1000 行。")

        def clean(mapping, allowed):
            if not isinstance(mapping, dict) or set(mapping) - set(allowed):
                raise ValidationError("明细包含不支持的字段。")
            if any(not isinstance(value, str) or len(value) > 4000 for value in mapping.values()):
                raise ValidationError("表单值须为不超过 4000 字的文本。")
            return {name: value.strip() for name, value in mapping.items()}

        cleaned = clean(values, FORM_FIELDS[kind])
        cleaned_rows = [clean(row, ROW_FIELDS[kind]) for row in rows]
        previous = get_form(workspace, kind)
        form = DocumentForm(document_type=kind, values=cleaned, rows=cleaned_rows,
            source_document_ids=previous.source_document_ids, revision=previous.revision + 1,
            confirmed_by=actor.user_id if confirm else None,
            confirmed_at=utc_now() if confirm else None)
        invalidate(workspace, actor, reason.strip())
        workspace.document_forms = [item for item in workspace.document_forms if item.document_type is not kind]
        workspace.document_forms.append(form)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "document_form_confirmed" if confirm else "document_form_saved",
            "document_form", kind.value, before=to_dict(previous) if previous.revision else None,
            after=to_dict(form), reason=reason.strip())
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace


def render_blank(kind) -> RenderedOutput:
    """Return an empty seller template without saving or confirming any data."""
    kind = DocumentType(kind)
    return render_form(kind, TradeData(currency="", revision=0), DocumentForm(document_type=kind, revision=0))


def render_form(kind, trade=None, form=None) -> RenderedOutput:
    # Also accept render_form(kind, form) for UI previews of blank worksheets.
    if isinstance(trade, DocumentForm) and form is None:
        form, trade = trade, TradeData(currency="", revision=0)
    trade = trade or TradeData(currency="", revision=0)
    form = form or DocumentForm(document_type=kind, revision=0)
    if kind not in SELLER_TYPES:
        raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED, "该类型由外部机构或买方提供，仅可托管原件。")
    spec = {"type": kind.value, "version": FORM_TEMPLATE_VERSION,
        "fields": FORM_FIELDS[kind], "rows": ROW_FIELDS[kind], "required": REQUIRED_FIELDS[kind]}
    template_hash = sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    values = effective_values(trade, form)
    def e(value):
        return escape(str(value or ""), quote=True)
    fields = "".join(f"<tr><th>{e(label)}</th><td>{e(values.get(name, ''))}</td></tr>"
        for name, label in FORM_FIELDS[kind].items())
    headers = "".join(f"<th>{e(label)}</th>" for label in ROW_FIELDS[kind].values())
    rows = "".join("<tr>" + "".join(f"<td>{e(row.get(name, ''))}</td>"
        for name in ROW_FIELDS[kind]) + "</tr>" for row in form.rows)
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{e(DOC_LABELS[kind])}</title>
<style>body{{font-family:Arial,sans-serif;margin:30px;color:#203330}}table{{border-collapse:collapse;width:100%;margin:20px 0}}th,td{{border:1px solid #bbc7ce;padding:8px;text-align:left;white-space:pre-wrap}}th{{background:#eef3f6}}.draft{{padding:12px;border:1px solid #9b4a00;color:#9b4a00}}@media print{{body{{margin:15mm}}}}</style></head>
<body><h1>{e(DOC_LABELS[kind])}</h1><p class="draft">DRAFT / 卖方填写稿 · 使用前须人工复核；空白内容待填写。</p>
<table>{fields}</table><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>
<footer>模板 {e(FORM_TEMPLATE_VERSION)} · 表单修订 {form.revision} · 订单修订 {trade.revision}。
仅整理用户确认的资料；不代表外部签发、实际验货、认证、申报成功或任何人的签名。</footer></body></html>'''
    return RenderedOutput(content=html.encode("utf-8"), media_type="text/html", extension="html",
        template_id="w3-form-" + kind.value, template_version=FORM_TEMPLATE_VERSION,
        template_sha256=template_hash)
