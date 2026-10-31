"""Read-only, versioned assessment of one exact seller export snapshot.

The human flag reviews effective selected/related saved seller form values in
this digest. It never promotes raw source fields, changes a Case, or confirms
unknown provenance. Required-document inventory is advisory for standalone files.
Comparison policy: exact literal text, unique SKU pairing, explicit units,
zero monetary/weight tolerance, no rounding, currency conversion or aliases.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext
from hashlib import sha256
import json
import re

from core.errors import ValidationError
from core.models import (CaseStatus, CrossCheckResult, CrossCheckStatus as S,
    DocumentType as T, ExportReviewReport, FieldValueStatus as F, TradeData, to_dict,
    ReviewEditArea as A, ReviewEditTarget)
from documents.catalog import FORM_FIELDS, ROW_FIELDS, SELLER_TYPES, REQUIRED_FIELDS, REQUIRED_ROW_FIELDS
from documents.forms import effective_rows, effective_values, get_form, missing_form_fields
from documents.provenance import form_value_origins
from .cross_checker import get_current_version
from .normalizers import (convert_weight, normalize_currency, normalize_date,
                          normalize_decimal, normalize_text)
from .rules import compare_currency, compare_money, compare_quantity, compare_text, compare_weight

RULE_VERSION = "w3-export-review-8"
VERSION = RULE_VERSION
POLICY = {"version": RULE_VERSION, "tolerance": "0", "rounding": "none",
          "text": "trim-only-case-sensitive", "pairing": "unique-product_code",
          "human_scope": "exact-effective-selected-and-related-saved-seller-forms",
          "raw_sources": "confirmed-fields-only"}
INVOICES = {T.COMMERCIAL_INVOICE, T.PROFORMA_INVOICE, T.COMBINED_INVOICE_PACKING_LIST}
PRODUCT_KINDS = INVOICES | {T.PACKING_LIST, T.SALES_CONTRACT, T.ORDER_CONFIRMATION, T.QUOTATION}
DATE_ALIASES = {T.COMMERCIAL_INVOICE: "invoice_date", T.PROFORMA_INVOICE: "invoice_date",
    T.COMBINED_INVOICE_PACKING_LIST: "invoice_date", T.PACKING_LIST: "packing_date"}
NUMBER_ALIASES = {kind: "invoice_no" for kind in INVOICES} | {T.PACKING_LIST: "packing_list_no"}
NUMBERS = {"quantity", "unit_price", "amount", "packages", "gross_weight_kg", "net_weight_kg",
           "total_amount", "amount_due", "debit", "credit", "balance", "volume_cbm",
           "opening_balance", "closing_balance"}
COUNT_UNITS = frozenset({"pcs", "piece", "pieces", "box", "boxes", "carton", "cartons", "set", "sets"})
FINAL_PRODUCT_TYPES = frozenset({T.COMMERCIAL_INVOICE, T.COMBINED_INVOICE_PACKING_LIST,
    T.PACKING_LIST, T.SALES_CONTRACT, T.ORDER_CONFIRMATION})


def related_kinds(kind):
    """Same transaction scope only; PI/quotes are not final shipment quantities."""
    if kind in FINAL_PRODUCT_TYPES:
        peers = [item for item in FINAL_PRODUCT_TYPES if item is not kind]
        peers.sort(key=lambda item: item.value)
        if kind is T.PACKING_LIST:
            peers.append(T.BILL_OF_LADING)
        return peers
    return []


def raw_row_candidates(version, document_id):
    from documents.prefill import row_candidates
    return row_candidates(version, document_id, {"product_code", "description", "quantity", "unit",
        "unit_price", "amount", "packages", "gross_weight_kg", "net_weight_kg"}, include_incomplete_identity=True)


def review_digest(workspace, kind):
    """Bind inputs, provenance evidence and current sources; ignore download noise."""
    kind = T(kind)
    documents = []
    for document in workspace.documents:
        current = [to_dict(version) for version in document.versions
                   if version.version_id == document.current_version_id]
        for version in current:
            tables = version.pop("source_tables", [])
            version["source_tables_sha256"] = sha256(json.dumps(tables, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        documents.append({"document_id": document.document_id, "case_id": document.case_id,
            "type": document.document_type.value, "status": document.status.value,
            "current_version_id": document.current_version_id, "current": current,
            "deleted": document.deleted_at is not None})
    evidence = [{"actor": event.actor_id, "action": event.action, "entity": event.entity_id,
                 "before": event.before, "after": event.after}
        for event in workspace.audit if event.action in {
            "case_created", "trade_updated", "document_form_saved", "document_form_confirmed"}]
    payload = {"rule_version": RULE_VERSION, "selected": kind.value,
        "case_id": workspace.case.case_id, "closed": workspace.case.status is CaseStatus.CLOSED,
        "case_identity": {name: getattr(workspace.case, name) for name in (
            "order_no", "customer_name", "destination_country", "currency", "incoterm")},
        "trade": to_dict(workspace.trade), "forms": to_dict(workspace.document_forms),
        "manual_data_records": to_dict(workspace.manual_data_records),
        "reviewed_source_rows": to_dict(workspace.reviewed_source_rows),
        "document_templates": to_dict(workspace.document_templates),
        "selected_template_ids": to_dict(workspace.selected_template_ids),
        "source_requirement_links": to_dict(workspace.source_requirement_links),
        "defaults_user_id": workspace.defaults_user_id,
        "defaults_revision": workspace.defaults_revision,
        "documents": documents, "required": to_dict(workspace.required_document_types),
        "base_required": to_dict(workspace.base_required_document_types),
        "required_sources": to_dict(workspace.required_source_types),
        "required_deliveries": to_dict(workspace.required_delivery_types),
        "policies": to_dict(workspace.requirement_policies), "human_evidence": evidence}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _nonempty(value):
    return value is not None and bool(str(value).strip())


def _multiply(left, right):
    with localcontext() as context:
        context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
        return left * right


def _sum(values):
    if not values:
        return None
    with localcontext() as context:
        context.prec = max(28, max(value.adjusted() for value in values)
            - min(value.as_tuple().exponent for value in values) + len(str(len(values))) + 3)
        return sum(values, Decimal(0))


def _view(workspace, kind):
    from documents.forms import (input_form_fields, input_form_row_fields, input_context_fields,
                                 input_values, input_rows)
    form = get_form(workspace, kind)
    fields, row_fields = input_form_fields(form), input_form_row_fields(form)
    return {"kind": kind, "form": form,
            "values": {name: value for name, value in input_values(workspace.trade, form).items() if name in fields},
            "rows": [{name: value for name, value in row.items() if name in row_fields}
                     for row in input_rows(workspace.trade, form)],
            "fields": fields, "row_fields": row_fields,
            "context_fields": input_context_fields(form, workspace),
            "origins": {(origin.row_number, origin.field_name): origin
                        for origin in form_value_origins(workspace, kind, include_inputs=True)
                        if origin.field_name in (fields if origin.row_number is None else row_fields)}}


def active_source_document_ids(workspace, kind):
    """Ignore links whose only candidates belong to explicitly omitted fields."""
    from documents.forms import input_form_fields, input_form_row_fields
    form = get_form(workspace, kind)
    fields, rows = input_form_fields(form), input_form_row_fields(form)
    known, active = set(), set()
    for name, candidates in form.field_candidates.items():
        for candidate in candidates:
            known.add(candidate.document_id)
            if name in fields:
                active.add(candidate.document_id)
    for candidate in form.row_candidates:
        known.add(candidate.document_id)
        if any(_nonempty(value) for name, value in candidate.values.items() if name in rows):
            active.add(candidate.document_id)
    return [key for key in form.source_document_ids if key not in known or key in active]


def _source(view, field, row=None):
    origin = view["origins"].get((row, field))
    prefix = (f"trade:r{view['trade_revision']}" if "trade_revision" in view
              else f"form:{view['kind'].value}:r{view['form'].revision}")
    if row is not None:
        prefix += f":rows[{row}].{field}"
    else:
        prefix += f":{field}"
    return (prefix + (f";{origin.origin_kind};{origin.filename};{origin.location}" if origin else ""),
            origin.document_id or None if origin else None,
            origin.version_id or None if origin else None)


def review_export(workspace, kind, *, human_confirmed=False):
    kind = T(kind)
    human = human_confirmed is True
    report = ExportReviewReport(workspace.case.case_id, kind, review_digest(workspace, kind),
                                RULE_VERSION, human_confirmed=human)

    def trade_field_name(field):
        if field == "package_details":
            return "packages"
        for group in ("adjustments", "packages", "line_items"):
            if field.startswith(group + "[") and field.endswith("]"):
                return group
        return field

    def edit_target(view, field, row=None):
        """Route by structured input ownership, never by displayed provenance."""
        if view is None or not field or field == "document_form":
            return None
        area = view.get("edit_area", A.FORM)
        if field in {"source_document_ids", "source_form_references"}:
            area = A.SOURCE_LINKS
        if row is None and area in {A.SOURCE, A.MANUAL_SOURCE} and field in view.get("row_fields", {}):
            field = "rows"
        if area is A.TRADE and row is None and (field in view.get("row_fields", {}) or field in {"rows", "total_amount"}):
            field = "line_items"
        if area is A.FORM:
            if field in view.get("context_fields", set()) or any(
                    field == group or field.startswith(group + "[") for group in ("adjustments", "packages", "line_items")):
                area = A.TRADE
            elif row is None and (field in view.get("row_fields", {}) or field == "total_amount"
                    and field not in view.get("fields", {})):
                field = "rows"
        if area is A.TRADE:
            field = trade_field_name(field)
        source_row = view.get("edit_row_numbers", {}).get(row, row)
        table = view.get("edit_table_indices", {}).get(row)
        return ReviewEditTarget(area, document_type=view["kind"], field_name=field,
            row_number=source_row, document_id=view.get("edit_document_id", ""),
            version_id=view.get("edit_version_id", ""), record_id=view.get("edit_record_id", ""),
            table_index=table)

    def requirement_target(item, view):
        if item.row_number is not None and item.field_name not in view["row_fields"]:
            origin_row = item.origin.row_number if item.origin else None
            if origin_row is None and item.row_number <= len(view["rows"]):
                sku = view["rows"][item.row_number - 1].get("product_code")
                matching = [index for index, line in enumerate(workspace.trade.line_items, 1)
                            if sku and line.product_code == sku]
                origin_row = matching[0] if len(matching) == 1 else None
            return ReviewEditTarget(A.TRADE, document_type=view["kind"],
                field_name=item.field_name if origin_row else "line_items", row_number=origin_row)
        if item.field_name in view["fields"] or item.field_name == "rows" or item.row_number is not None:
            return edit_target(view, item.field_name, item.row_number)
        return ReviewEditTarget(A.TRADE, document_type=view["kind"], field_name=trade_field_name(item.field_name))

    def add(code, status, message, *, left=None, right=None, field=None, row=None,
            lv=None, rv=None, lu=None, ru=None, nl=None, nr=None, required=True,
            scope="document", item="", right_field=None, right_row=None, raw=None, policy=None,
            targets=None):
        ls, ld, lver = _source(left, field, row) if left and field else (None, None, None)
        rs, rd, rver = (_source(right, right_field or field, right_row if right_row is not None else row)
                       if right and field else (None, None, None))
        if raw:
            rd, rver, rs = raw
        if targets is None:
            targets = [edit_target(left, field, row),
                edit_target(right, right_field or field, right_row if right_row is not None else row)]
            if raw:
                document = next((doc for doc in workspace.documents if doc.document_id == rd), None)
                raw_view = {"kind": document.document_type if document else kind, "edit_area": A.SOURCE,
                    "edit_document_id": rd or "", "edit_version_id": rver or "",
                    "edit_row_numbers": right.get("edit_row_numbers", {}) if right else {},
                    "edit_table_indices": right.get("edit_table_indices", {}) if right else {}}
                raw_name = "rows" if code in {"RAW_ROW_SOURCE", "RAW_ROW_PAIRING"} else right_field or field or ""
                if code in {"SOURCE_CURRENT", "RELATED_SOURCE_CURRENT"}:
                    raw_view["edit_area"] = A.SOURCE_LINKS
                    raw_name = "source_document_ids"
                raw_row = (right_row if right_row is not None else row) if right else None
                if code.endswith("_SET") and raw_name == "product_code":
                    raw_name, raw_row = "rows", None
                targets.append(edit_target(raw_view, raw_name, raw_row))
        unique_targets = []
        for target in targets:
            if target is not None and target not in unique_targets:
                unique_targets.append(target)
        report.checks.append(CrossCheckResult(case_id=workspace.case.case_id,
            rule_code=code, status=status, message=message, rule_version=RULE_VERSION,
            left_document_id=ld, right_document_id=rd, left_version_id=lver,
            right_version_id=rver, left_field=field, right_field=right_field or field,
            left_value=lv, right_value=rv, left_source=ls, right_source=rs,
            normalized_left=nl, normalized_right=nr, left_unit=lu, right_unit=ru,
            comparison_policy=dict(POLICY, **(policy or {})), scope=scope, item_key=item, required=required,
            edit_targets=unique_targets))

    def state(ok, *, invalid=False):
        return S.FAIL if not ok and invalid else S.PASS if ok else S.PENDING_REVIEW

    def confirmed_status(reviewed):
        return F.CONFIRMED if reviewed else F.CANDIDATE

    def field_required(view, field, *, row=None):
        if view is None:
            return False
        form = view["form"]
        return field in (set(REQUIRED_ROW_FIELDS.get(view["kind"], ())) | set(form.required_input_row_fields)
            if row is not None else set(REQUIRED_FIELDS.get(view["kind"], ())) | set(form.required_input_fields))

    def optional_absent(left, right, field, lv, rv):
        return (not _nonempty(lv) and not _nonempty(rv)
            and not field_required(left, field) and not field_required(right, field))

    def cmp(left, right, field, lv, rv, *, category="text", row=None, right_row=None,
            lu=None, ru=None, reviewed=None, raw=None, code="CROSS_DOCUMENT", raw_field_name=None,
            right_field=None):
        ready = human if reviewed is None else human and reviewed
        args = {"left_status": confirmed_status(ready), "right_status": confirmed_status(ready),
                "context_confirmed": ready}
        nl, nr = None, None
        comparison_policy = {"minimum": "0", "tolerance": "0"}
        try:
            if category == "quantity":
                fractional = field != "packages" and lu not in COUNT_UNITS and ru not in COUNT_UNITS
                comparison_policy.update(allow_fractional=fractional,
                    count_units=sorted(COUNT_UNITS), unit_policy="exact-explicit-no-conversion")
                result = compare_quantity(lv, rv, **args, left_unit=lu, right_unit=ru,
                    minimum=Decimal(0), allow_fractional=fractional)
                nl, nr = str(normalize_decimal(lv)), str(normalize_decimal(rv))
            elif category == "weight":
                result = compare_weight(lv, rv, **args, left_unit=lu, right_unit=ru,
                    minimum=Decimal(0), tolerance=Decimal(0))
                nl, nr = str(convert_weight(lv, lu)), str(convert_weight(rv, ru))
            elif category == "money":
                result = compare_money(lv, rv, **args, left_currency=lu, right_currency=ru,
                    minimum=Decimal(0), tolerance=Decimal(0))
                nl, nr = str(normalize_decimal(lv)), str(normalize_decimal(rv))
            elif category == "currency":
                result = compare_currency(lv, rv, **args)
                nl, nr = normalize_currency(lv), normalize_currency(rv)
            else:
                result = compare_text(lv, rv, **args)
                nl, nr = normalize_text(lv), normalize_text(rv)
        except ValidationError:
            result = S.PENDING_REVIEW
        human_only = bool(result is S.PENDING_REVIEW and not human and reviewed is not False
            and nl is not None and nr is not None and nl == nr
            and (category not in {"quantity", "money"} or lu == ru and lu is not None))
        comparison_policy["human_confirmation_only"] = human_only
        targets = [] if human_only else None
        if not human_only and code in {"PACKAGE_TOTAL", "PACKAGE_COUNT"}:
            targets = [edit_target(left, field, row),
                ReviewEditTarget(A.TRADE, document_type=left["kind"], field_name="packages")]
        if not human_only and code == "LINE_AMOUNT":
            targets = [edit_target(left, name, row) for name in ("amount", "quantity", "unit_price")]
        add(code, result, f"{left['kind'].value} 与 {right['kind'].value if right else '外部原件'}：{field}",
            left=left, right=right, field=field, row=row, right_row=right_row,
            lv=lv, rv=rv, lu=lu, ru=ru, nl=nl, nr=nr, scope="cross_document",
            item=str(left["rows"][row - 1].get("product_code", "")) if row else "", raw=raw,
            right_field=raw_field_name or right_field or field, targets=targets)
        report.checks[-1].comparison_policy.update(comparison_policy)
        if raw_field_name is not None:
            report.checks[-1].right_field = raw_field_name

    selected = _view(workspace, kind) if kind in FORM_FIELDS else None
    add("EXPORT_HUMAN_CONFIRMATION", state(human), "已核对本次精确输入。" if human else "尚未由真人核对本次精确输入。")
    add("EXPORT_SUPPORTED_TYPE", state(kind in SELLER_TYPES, invalid=True),
        "仅生成我方填写文件；外部签发文件须保存真实原件。")
    add("EXPORT_OPEN_CASE", state(workspace.case.status is not CaseStatus.CLOSED, invalid=True),
        "已关闭交易不可新增生成版本。" if workspace.case.status is CaseStatus.CLOSED else "交易允许生成新版本。")
    if selected is None or kind not in SELLER_TYPES:
        return report

    def source_checks(view):
        form = view["form"]
        from documents.manual_inputs import has_manual_data, manual_source
        for ref in form.source_form_references:
            source = manual_source(workspace, ref.document_type, ref.record_id)
            current = (source is not None and (ref.record_id or ref.document_type is not form.document_type)
                and source.revision == ref.revision and has_manual_data(source)
                and not getattr(source, "source_form_references", []))
            add("MANUAL_SOURCE_CURRENT", state(current),
                "人工填写来源修订有效。" if current else "人工填写来源已变更或清空，请重新核对并填入。",
                left=view, field="source_form_references", lv=f"{ref.document_type.value}:r{ref.revision}",
                rv=f"r{source.revision}" if source else "missing", scope="source",
                targets=[ReviewEditTarget(A.SOURCE_LINKS, document_type=ref.document_type,
                    field_name="source_form_references", record_id=ref.record_id)])
            if not current:
                continue
            other = _view(workspace, ref.document_type)
            other.update(values=source.values, rows=source.rows)
            other.update(edit_area=A.MANUAL_SOURCE, edit_record_id=ref.record_id)
            for field in ("order_no", "buyer", "seller", "currency", "incoterm", "payment_terms",
                    "destination_country", "transport_mode", "shipper", "consignee",
                    "port_of_loading", "port_of_discharge"):
                from documents.source_mapping import map_field
                if (map_field(ref.document_type, view["kind"], field) is None
                        or field not in view["fields"] or not _nonempty(source.values.get(field))):
                    continue
                cmp(view, other, field, view["values"].get(field), source.values[field],
                    category="currency" if field == "currency" else "text", code="MANUAL_SOURCE_VALUE")
            if source.rows and "product_code" in view["row_fields"] and "product_code" in ROW_FIELDS[source.document_type]:
                source_codes = [row.get("product_code", "").strip() for row in source.rows]
                target_codes = [row.get("product_code", "").strip() for row in view["rows"]]
                unambiguous = (all(source_codes) and len(set(source_codes)) == len(source_codes)
                    and all(target_codes) and len(set(target_codes)) == len(target_codes))
                add("MANUAL_SOURCE_ROW_SET", state(unambiguous and set(source_codes) == set(target_codes)),
                    "填写来源与目标文件须逐产品对应，不能用总计代替。", left=view, right=other,
                    field="product_code", lv=target_codes, rv=source_codes, scope="source")
                if not unambiguous:
                    continue
                target_index = {code: (i, row) for i, (code, row) in enumerate(zip(target_codes, view["rows"]), 1)}
                for ri, (code, row) in enumerate(zip(source_codes, source.rows), 1):
                    if code not in target_index:
                        continue
                    li, target_row = target_index[code]
                    for field in ("description", "quantity", "unit_price", "unit", "packages", "gross_weight_kg", "net_weight_kg"):
                        if (map_field(ref.document_type, view["kind"], field) is None
                                or field not in view["row_fields"] or not _nonempty(row.get(field))):
                            continue
                        category = ("weight" if field.endswith("_kg") else "money" if field == "unit_price"
                            else "quantity" if field in {"quantity", "packages"} else "text")
                        lu = ("kg" if category == "weight" else "box" if field == "packages"
                            else view["values"].get("currency") if category == "money" else target_row.get("unit"))
                        ru = ("kg" if category == "weight" else "box" if field == "packages"
                            else source.values.get("currency") if category == "money" else row.get("unit"))
                        cmp(view, other, field, target_row.get(field), row[field], category=category,
                            row=li, right_row=ri, lu=lu, ru=ru, code="MANUAL_SOURCE_VALUE")
        for document_id in active_source_document_ids(workspace, view["kind"]):
            document = next((doc for doc in workspace.documents if doc.document_id == document_id
                             and doc.deleted_at is None), None)
            try:
                if document is None:
                    raise ValidationError("来源文件已移除或缺失")
                version = get_current_version(workspace.case, document)
                if not re.fullmatch(r"[0-9a-f]{64}", version.sha256):
                    raise ValidationError("来源哈希无效")
                add("SOURCE_CURRENT", S.PASS, "来源归属、当前解析版本与哈希元数据有效。",
                    left=view, field="source_document_ids", lv=document_id,
                    rv=version.version_id, scope="source",
                    raw=(document_id, version.version_id, version.original_filename))
            except ValidationError as exc:
                add("SOURCE_CURRENT", S.PENDING_REVIEW, str(exc), left=view,
                    field="source_document_ids", lv=document_id, scope="source",
                    raw=(document_id, document.current_version_id if document else None, "来源归属或当前版本待核对"))
        for origin in view["origins"].values():
            if not _nonempty(origin.value):
                continue
            known = (origin.origin_kind in {"file", "default", "derived"}
                     or origin.origin_kind in {"manual", "trade"}
                     and bool(origin.actor_id and origin.occurred_at))
            if origin.origin_kind == "file":
                document = next((doc for doc in workspace.documents
                                 if doc.document_id == origin.document_id and doc.deleted_at is None), None)
                try:
                    version = get_current_version(workspace.case, document) if document else None
                    known = bool(version and version.version_id == origin.version_id
                                 and re.fullmatch(r"[0-9a-f]{64}", version.sha256))
                except ValidationError:
                    known = False
            add("VALUE_PROVENANCE", state(known), origin.note or "字段来源核对。",
                left=view, field=origin.field_name, row=origin.row_number,
                lv=origin.value, scope="source")
        # Persisted machine candidates can predate role-safe mapping. Current
        # file/version provenance alone cannot make a PO date an invoice date.
        from documents.source_mapping import map_field
        for name, automatic in form.auto_values.items():
            if (name not in view["fields"] or not _nonempty(automatic) or name in form.manual_fields
                    or view["values"].get(name) != automatic):
                continue
            matching = [candidate for candidate in form.field_candidates.get(name, [])
                        if candidate.value == automatic]
            mapped = bool(matching)
            for candidate in matching:
                document = next((doc for doc in workspace.documents if doc.document_id == candidate.document_id
                    and doc.deleted_at is None and doc.current_version_id == candidate.version_id), None)
                mapped = mapped and bool(document and map_field(document.document_type, view["kind"],
                    candidate.field_name or name) == name)
            add("AUTO_FIELD_MAPPING", state(mapped),
                "自动填写字段的来源含义一致。" if mapped else
                    "旧版自动填写字段与当前文件含义不一致或来源失效，请重新采集填入；本文件编号、日期须单独核实。",
                left=view, field=name, lv=automatic, scope="source")
        for field, candidates in form.field_candidates.items():
            if field not in view["fields"]:
                continue
            distinct = {candidate.value for candidate in candidates}
            if len(distinct) <= 1:
                continue
            origin = view["origins"].get((None, field))
            resolved = bool(origin and origin.origin_kind == "manual" and origin.actor_id)
            add("SOURCE_FIELD_CONFLICT", state(resolved),
                "多来源字段冲突已由可追溯人工改值解决。" if resolved else "多来源字段存在不同候选，须明确人工修改或选择来源。",
                left=view, field=field, lv=view["values"].get(field), rv=sorted(distinct), scope="source")
        sets = defaultdict(list)
        from documents.prefill import effective_candidate_values
        row_review_cache = {}
        for candidate in form.row_candidates:
            projected = {name: value for name, value in effective_candidate_values(
                workspace, candidate, view["kind"], cache=row_review_cache).items() if name in view["row_fields"]}
            if any(_nonempty(value) for value in projected.values()):
                sets[candidate.document_id].append(projected)
        if len({json.dumps(rows, sort_keys=True, ensure_ascii=False) for rows in sets.values()}) > 1:
            audited = any(event.entity_id == view["kind"].value
                and event.action in {"document_form_saved", "document_form_confirmed"}
                and isinstance(event.after, dict) and event.after.get("rows") == form.rows
                and (event.before or {}).get("rows") != form.rows for event in workspace.audit)
            add("SOURCE_ROW_CONFLICT", state(bool(form.rows_manually_edited and audited)),
                "多来源明细存在不同产品集；须选定来源或明确人工维护明细。", left=view,
                field="rows", lv=form.rows, rv=dict(sets), scope="source")

    source_checks(selected)
    effective_form = deepcopy(selected["form"])
    effective_form.values = {name: "" if value is None else str(value)
                             for name, value in selected["values"].items()}
    effective_form.rows = [{name: "" if value is None else str(value) for name, value in row.items()}
                           for row in selected["rows"]]
    for target, aliases in (("document_no", NUMBER_ALIASES), ("document_date", DATE_ALIASES)):
        alias = aliases.get(kind)
        if alias and _nonempty(effective_form.values.get(alias)):
            if not _nonempty(effective_form.values.get(target)):
                effective_form.values[target] = effective_form.values[alias]
            else:
                cmp(selected, selected, target, effective_form.values[target], effective_form.values[alias],
                    code="DOCUMENT_IDENTITY_ALIAS", right_field=alias)
    from documents.requirements import data_requirements
    requirements = data_requirements(workspace, kind)
    missing = [item.field_name for item in requirements if item.required and item.status == "missing"]
    add("DOCUMENT_COMPLETENESS", state(not missing), "所选文件字段齐备。" if not missing else "待补：" + "、".join(missing),
        left=selected, field="document_form", lv=missing,
        targets=[requirement_target(item, selected) for item in requirements
                 if item.required and item.status == "missing"])
    # Optional controls may permit an empty value. They never authorize using
    # populated customer/internal output whose provenance is still unknown.
    input_missing = [item for item in requirements if
        item.required and item.status != "provided"
        or _nonempty(item.value) and item.status == "review_required"]
    add("INPUT_DATA_REQUIREMENTS", state(not input_missing),
        "所需数据已明确提供。" if not input_missing else "待明确提供：" + "、".join(item.label for item in input_missing),
        left=selected, field="document_form", lv=[to_dict(item) for item in input_missing],
        targets=[requirement_target(item, selected) for item in input_missing])

    def numeric(view, name, value, row=None):
        if not _nonempty(value):
            return None
        try:
            number = normalize_decimal(value)
            valid = number > 0 if name == "quantity" else number >= 0 or name in {"balance", "opening_balance", "closing_balance"}
            if row and kind in {T.PACKING_LIST, T.COMBINED_INVOICE_PACKING_LIST} and name in {"packages", "gross_weight_kg"}:
                valid = number > 0
            if name == "packages":
                valid = valid and number == number.to_integral_value()
            count_unit = (view["rows"][row - 1].get("unit") if row and name == "quantity" else None)
            if count_unit in COUNT_UNITS:
                valid = valid and number == number.to_integral_value()
            add("NUMBER_VALID", state(valid, invalid=True), f"{name} 必须是有效{'正' if name == 'quantity' else '非负'}十进制数" +
                ("且箱数必须为整数。" if name == "packages" else "。"), left=view, field=name,
                row=row, lv=value, nl=str(number), lu=count_unit,
                policy={"minimum": "0", "strictly_positive": name == "quantity",
                    "allow_fractional": name != "packages" and count_unit not in COUNT_UNITS})
            return number if valid else None
        except ValidationError:
            add("NUMBER_VALID", S.FAIL, f"{name} 不接受浮点、指数、非数字或非有限值。", left=view, field=name, row=row, lv=value)
            return None

    for name, value in effective_form.values.items():
        if not _nonempty(value):
            continue
        if name == "currency":
            try:
                normalized = normalize_currency(value)
                add("CURRENCY_VALID", S.PASS, "三位币种代码。", left=selected, field=name, lv=value, nl=normalized)
            except ValidationError:
                add("CURRENCY_VALID", S.FAIL, "币种必须为明确的三位代码。", left=selected, field=name, lv=value)
        elif name in NUMBERS:
            numeric(selected, name, value)
        elif name.endswith(("_date", "_until", "_on")) or name in {"period_start", "period_end"}:
            try:
                normalized = normalize_date(value).isoformat()
                add("DATE_VALID", S.PASS, "明确的 YYYY-MM-DD 日期。", left=selected, field=name, lv=value, nl=normalized)
            except ValidationError:
                add("DATE_VALID", S.FAIL, "日期须为有效 YYYY-MM-DD。", left=selected, field=name, lv=value)

    for index, row in enumerate(selected["rows"], 1):
        numbers = {name: numeric(selected, name, value, index)
                   for name, value in row.items() if name in NUMBERS and _nonempty(value)}
        if {"gross_weight_kg", "net_weight_kg"} <= numbers.keys() and all(
                numbers[name] is not None for name in ("gross_weight_kg", "net_weight_kg")):
            add("GROSS_NOT_BELOW_NET", state(numbers["gross_weight_kg"] >= numbers["net_weight_kg"], invalid=True),
                "同一产品行毛重不得小于净重。", left=selected, field="gross_weight_kg", row=index,
                right=selected, right_field="net_weight_kg", right_row=index,
                lv=row["gross_weight_kg"], rv=row["net_weight_kg"], lu="kg", ru="kg")
        if numbers.get("quantity") is not None and numbers.get("unit_price") is not None:
            expected = _multiply(numbers["quantity"], numbers["unit_price"])
            if _nonempty(row.get("amount")):
                cmp(selected, selected, "amount", row["amount"], str(expected), category="money", row=index,
                    lu=selected["values"].get("currency"), ru=selected["values"].get("currency"), code="LINE_AMOUNT")

    for index, adjustment in enumerate(workspace.trade.adjustments if "adjustments" in selected["context_fields"] else [], 1):
        try:
            amount = normalize_decimal(adjustment.amount)
            currency = normalize_currency(adjustment.currency)
            expected_currency = normalize_currency(selected["values"].get("currency") or workspace.trade.currency)
            valid = (amount >= 0 and currency == expected_currency and bool(adjustment.name.strip())
                     and isinstance(adjustment.included_in_invoice_price, bool))
            add("ADJUSTMENT_VALID", state(valid, invalid=True),
                "费用/折扣须明确项目、非负金额、同一币种及是否含在货价内；折扣由类别表达，未擅自并入应付总额。",
                left=selected, field=f"adjustments[{index}]", lv=to_dict(adjustment),
                policy={"amount_policy": "nonnegative-magnitude-kind-defines-treatment-no-implicit-total"})
        except (ValidationError, AttributeError):
            add("ADJUSTMENT_VALID", S.FAIL, "费用/调整的金额、币种或项目格式无效。",
                left=selected, field=f"adjustments[{index}]", lv=to_dict(adjustment))
    for name in ("transaction_date", "expected_ship_date", "actual_ship_date", "due_date"):
        if name not in selected["context_fields"]:
            continue
        value = getattr(workspace.trade, name)
        if not _nonempty(value):
            continue
        try:
            normalize_date(value)
            valid = True
        except ValidationError:
            valid = False
        add("TRADE_DATE_VALID", state(valid, invalid=True), "交易补充日期须为有效YYYY-MM-DD。",
            left=selected, field=name, lv=value)

    def index_rows(view):
        result = {}
        codes = [row.get("product_code", "") for row in view["rows"]]
        try:
            normalized_codes = [normalize_text(code) for code in codes]
            valid = bool(codes) and len(set(normalized_codes)) == len(codes)
        except ValidationError:
            normalized_codes, valid = [], False
        add("ROW_IDENTITY", state(valid), "产品行必须具有唯一且大小写精确的产品编码。", left=view,
            field="product_code", lv=codes, nl=normalized_codes)
        if valid:
            result = {code: (index, row) for index, (code, row) in enumerate(zip(normalized_codes, view["rows"]), 1)}
        return result

    selected_index = index_rows(selected) if kind in PRODUCT_KINDS else {}
    master = {"kind": kind, "form": selected["form"], "trade_revision": workspace.trade.revision,
              "edit_area": A.TRADE,
              "origins": {}, "rows": [{name: str(getattr(line, name, "") or "")
                  for name in selected["row_fields"]} for line in workspace.trade.line_items],
              "values": {"currency": workspace.trade.currency}, "fields": selected["fields"],
              "row_fields": selected["row_fields"]}

    def compare_rows(left, right, left_index, right_index, *, code, fields, reviewed=None, raw_rows=None):
        add(code + "_SET", state(set(left_index) == set(right_index), invalid=True),
            "相关资料必须逐产品对应，不能用相同合计掩盖不同明细。", left=left, right=right,
            field="product_code", lv=sorted(left_index), rv=sorted(right_index), scope="cross_document",
            raw=next(iter(raw_rows.values())) if raw_rows else None)
        for sku in sorted(set(left_index) & set(right_index)):
            li, lrow = left_index[sku]
            ri, rrow = right_index[sku]
            for field in fields:
                if field not in left["row_fields"]:
                    continue
                if field not in right["row_fields"] and field not in rrow:
                    continue
                if not (_nonempty(lrow.get(field)) or _nonempty(rrow.get(field))):
                    continue
                category = ("weight" if field.endswith("_kg") else "money" if field in {"unit_price", "amount"}
                    else "quantity" if field in {"quantity", "packages"} else "text")
                lu = ("kg" if category == "weight" else "box" if field == "packages" else
                    left["values"].get("currency") if category == "money" else lrow.get("unit"))
                ru = ("kg" if category == "weight" else "box" if field == "packages" else
                    right["values"].get("currency") if category == "money" else rrow.get("unit"))
                raw = raw_rows.get(ri) if raw_rows else None
                field_reviewed = (bool(reviewed and right.get("currency_confirmed"))
                    if raw_rows and category == "money" else reviewed)
                cmp(left, right, field, lrow.get(field), rrow.get(field), category=category,
                    row=li, right_row=ri, lu=lu, ru=ru, code=code, reviewed=field_reviewed, raw=raw)

    if selected_index and master["rows"]:
        master_index = index_rows(master)
        if master_index:
            compare_rows(selected, master, selected_index, master_index, code="ORDER_ROWS",
                fields=("description", "quantity", "unit", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"))
    active_packages = workspace.trade.packages if "packages" in selected["context_fields"] else []
    package_numbers = [package.package_number.strip() for package in active_packages]
    if active_packages:
        add("PACKAGE_IDENTITIES", state(all(package_numbers) and len(set(package_numbers)) == len(package_numbers), invalid=True),
            "逐包记录必须有唯一件号，不能重复或缺失。", left=selected, field="package_number", lv=package_numbers,
            targets=[ReviewEditTarget(A.TRADE, document_type=kind, field_name="packages")])
    package_by_product = defaultdict(list)
    for index, package in enumerate(active_packages, 1):
        try:
            quantity = normalize_decimal(package.quantity)
            gross = normalize_decimal(package.gross_weight_kg) if _nonempty(package.gross_weight_kg) else None
            net = normalize_decimal(package.net_weight_kg) if _nonempty(package.net_weight_kg) else None
            valid = (quantity > 0 and bool(package.unit.strip()) and bool(package.product_code.strip())
                and (package.unit not in COUNT_UNITS or quantity == quantity.to_integral_value())
                and (gross is None or gross > 0) and (net is None or net >= 0)
                and (gross is None or net is None or gross >= net))
            add("PACKAGE_VALID", state(valid, invalid=True), "逐包数量、单位及毛净重须有效，毛重不得小于净重。",
                left=selected, field=f"packages[{index}]", lv=to_dict(package))
            package_by_product[package.product_code.strip()].append(package)
        except (ValidationError, AttributeError):
            add("PACKAGE_VALID", S.FAIL, "逐包数量或重量格式无效。", left=selected, field=f"packages[{index}]", lv=to_dict(package))
    if selected_index and active_packages:
        add("PACKAGE_PRODUCT_SET", state(set(package_by_product) == set(selected_index), invalid=True),
            "逐包记录须完整对应当前文件的产品。", left=selected, field="product_code",
            lv=sorted(package_by_product), rv=sorted(selected_index),
            targets=[edit_target(selected, "rows"), ReviewEditTarget(A.TRADE, document_type=kind, field_name="packages")])
        for sku in set(package_by_product) & set(selected_index):
            index, row = selected_index[sku]
            packages = package_by_product[sku]
            for field in ("quantity", "gross_weight_kg", "net_weight_kg"):
                if not _nonempty(row.get(field)):
                    continue
                units = {package.unit for package in packages}
                try:
                    actual = _sum([normalize_decimal(getattr(package, field)) for package in packages])
                except ValidationError:
                    actual = None
                category = "quantity" if field == "quantity" else "weight"
                unit = row.get("unit") if field == "quantity" else "kg"
                right_unit = next(iter(units)) if field == "quantity" and len(units) == 1 else "kg" if field != "quantity" else None
                cmp(selected, selected, field, row.get(field), str(actual) if actual is not None else None,
                    category=category, row=index, lu=unit, ru=right_unit, code="PACKAGE_TOTAL")
            if _nonempty(row.get("packages")):
                cmp(selected, selected, "packages", row["packages"], str(len(packages)), category="quantity",
                    row=index, lu="box", ru="box", code="PACKAGE_COUNT")
    for field in ("order_no", "seller", "buyer", "destination_country", "currency", "incoterm"):
        expected = getattr(workspace.trade, field)
        if field in selected["fields"] and _nonempty(expected) and _nonempty(selected["values"].get(field)):
            cmp(selected, master, field, selected["values"].get(field), expected,
                category="currency" if field == "currency" else "text", code="ORDER_IDENTITY")
    for field, expected in (("order_no", workspace.case.order_no),
            ("buyer", workspace.case.customer_name), ("destination_country", workspace.case.destination_country),
            ("currency", workspace.case.currency), ("incoterm", workspace.case.incoterm)):
        if (field in selected["fields"] and _nonempty(expected) and _nonempty(selected["values"].get(field))
                and expected != getattr(workspace.trade, field, None)):
            cmp(selected, master, field, selected["values"].get(field), expected,
                category="currency" if field == "currency" else "text", code="CASE_IDENTITY")

    def total(view):
        values = []
        for row in view["rows"]:
            try:
                amount = normalize_decimal(row["amount"]) if _nonempty(row.get("amount")) else _multiply(
                    normalize_decimal(row.get("quantity")), normalize_decimal(row.get("unit_price")))
                values.append(amount)
            except (ValidationError, DecimalException, KeyError):
                return None
        return _sum(values)

    if kind is T.PAYMENT_NOTICE:
        try:
            due = normalize_decimal(selected["values"].get("amount_due"))
            ceiling = _sum([_multiply(normalize_decimal(line.quantity), normalize_decimal(line.unit_price))
                for line in workspace.trade.line_items])
            same_currency = (normalize_currency(selected["values"].get("currency"))
                == normalize_currency(workspace.trade.currency))
            known = ceiling is not None and same_currency and not (
                workspace.trade.adjustments and "adjustments" in selected["context_fields"])
            add("PAYMENT_AMOUNT_LIMIT", state(due <= ceiling, invalid=True) if known else S.PENDING_REVIEW,
                "付款通知不得超过同币种已知订单金额；费用调整或金额依据不明时须补充核对。",
                left=selected, field="amount_due", lv=str(due), rv=str(ceiling) if ceiling is not None else None,
                targets=[edit_target(selected, "amount_due"),
                    ReviewEditTarget(A.TRADE, document_type=kind, field_name="line_items")])
        except ValidationError:
            add("PAYMENT_AMOUNT_LIMIT", S.PENDING_REVIEW, "付款通知金额及同币种订单依据尚不完整。",
                left=selected, field="amount_due", targets=[edit_target(selected, "amount_due"),
                    ReviewEditTarget(A.TRADE, document_type=kind, field_name="line_items")])
    if kind is T.STATEMENT_OF_ACCOUNT:
        values = selected["values"]
        try:
            start, end = normalize_date(values.get("period_start")), normalize_date(values.get("period_end"))
            add("STATEMENT_PERIOD", state(start <= end, invalid=True), "账期结束不得早于开始。",
                left=selected, right=selected, field="period_end", right_field="period_start",
                lv=values.get("period_start"), rv=values.get("period_end"))
            for index, row in enumerate(selected["rows"], 1):
                row_date = normalize_date(row.get("date"))
                add("STATEMENT_ROW_PERIOD", state(start <= row_date <= end, invalid=True), "明细日期必须位于账期内。",
                    left=selected, field="date", row=index, lv=row.get("date"))
        except ValidationError:
            add("STATEMENT_PERIOD", S.PENDING_REVIEW, "账期及明细日期需完整有效。", left=selected, field="period_end")
        try:
            running = normalize_decimal(values.get("opening_balance"))
            for index, row in enumerate(selected["rows"], 1):
                # Missing debit/credit are unknown, not implicitly zero.
                running += normalize_decimal(row.get("debit")) - normalize_decimal(row.get("credit"))
                balance = normalize_decimal(row.get("balance"))
                add("STATEMENT_ROW_BALANCE", state(running == balance, invalid=True),
                    "余额按期初加借方减贷方逐行核对。", left=selected, field="balance", row=index,
                    lv=str(balance), rv=str(running), policy={"balance_policy": "opening+debit-credit"},
                    targets=[edit_target(selected, name, index) for name in ("balance", "debit", "credit")]
                        + [edit_target(selected, "opening_balance")])
            closing = normalize_decimal(values.get("closing_balance"))
            add("STATEMENT_CLOSING_BALANCE", state(running == closing, invalid=True), "期末余额必须与逐行结果相符。",
                left=selected, field="closing_balance", lv=str(closing), rv=str(running),
                targets=[edit_target(selected, name) for name in ("closing_balance", "opening_balance", "rows")])
        except (ValidationError, DecimalException):
            add("STATEMENT_BALANCE", S.PENDING_REVIEW, "期初、借方、贷方、逐行余额及期末余额须明确，零值请填写0。",
                left=selected, field="closing_balance")

    related = related_kinds(kind)
    saved = {}
    for other_kind in related:
        form = next((form for form in workspace.document_forms if form.document_type is other_kind), None)
        # Empty catalog slots and master fallback alone are not another document.
        if form and (any(_nonempty(value) for value in form.values.values()) or bool(form.rows)):
            saved[other_kind] = _view(workspace, other_kind)
    for other_kind, other in saved.items():
        if other_kind not in SELLER_TYPES:
            continue  # External annotations do not substitute for issuer evidence.
        source_checks(other)
        for field in ("order_no", "buyer", "destination_country"):
            if field not in selected["fields"] or field not in other["fields"]:
                continue
            if not optional_absent(selected, other, field, selected["values"].get(field), other["values"].get(field)):
                cmp(selected, other, field, selected["values"].get(field), other["values"].get(field))
        if other_kind in FINAL_PRODUCT_TYPES and kind in FINAL_PRODUCT_TYPES:
            for field in ("currency", "incoterm"):
                if field not in selected["fields"] or field not in other["fields"]:
                    continue
                if optional_absent(selected, other, field, selected["values"].get(field), other["values"].get(field)):
                    continue
                cmp(selected, other, field, selected["values"].get(field), other["values"].get(field),
                    category="currency" if field == "currency" else "text")
            if "unit_price" in selected["row_fields"] and "unit_price" in other["row_fields"]:
                cmp(selected, other, "total_amount", total(selected), total(other), category="money",
                    lu=selected["values"].get("currency"), ru=other["values"].get("currency"))
        other_index = index_rows(other)
        if not selected_index or not other_index:
            continue
        add("CROSS_ROW_SET", state(set(selected_index) == set(other_index), invalid=True),
            "相关文件必须逐产品对应，不能用相同合计掩盖不同明细。", left=selected, right=other,
            field="product_code", lv=sorted(selected_index), rv=sorted(other_index), scope="cross_document")
        for code in sorted(set(selected_index) & set(other_index)):
            li, lrow = selected_index[code]
            ri, rrow = other_index[code]
            for field in ("description", "quantity", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"):
                if field not in selected["row_fields"] or field not in other["row_fields"]:
                    continue
                if field in {"packages", "gross_weight_kg", "net_weight_kg"} and not (
                        _nonempty(lrow.get(field)) or _nonempty(rrow.get(field))):
                    continue
                category = "weight" if field.endswith("_kg") else "money" if field == "unit_price" else "quantity" if field in {"quantity", "packages"} else "text"
                lu = "kg" if category == "weight" else "box" if field == "packages" else selected["values"].get("currency") if category == "money" else lrow.get("unit")
                ru = "kg" if category == "weight" else "box" if field == "packages" else other["values"].get("currency") if category == "money" else rrow.get("unit")
                cmp(selected, other, field, lrow.get(field), rrow.get(field), category=category,
                    row=li, right_row=ri, lu=lu, ru=ru)

    # Compare relevant supplied raw counterparts only where no reviewed seller
    # form represents them. Current source originals of the selected form itself
    # are checked above, not blanket-promoted into a second unreviewed form.
    for document in workspace.documents:
        other_kind = document.document_type
        if document.deleted_at is not None or other_kind not in [*related, kind]:
            continue
        represented = [view for view in [selected, *saved.values()] if view["kind"] is other_kind]
        bound = any(document.document_id in active_source_document_ids(workspace, view["kind"]) and any(
            candidate.document_id == document.document_id and candidate.version_id == document.current_version_id
            for candidate in [*(candidate for name, candidates in view["form"].field_candidates.items()
                                if name in view["fields"] for candidate in candidates),
                              *(candidate for candidate in view["form"].row_candidates if any(
                                  name in view["row_fields"] and _nonempty(value) for name, value in candidate.values.items()))])
            for view in represented)
        if bound:
            continue
        try:
            version = get_current_version(workspace.case, document)
            if not re.fullmatch(r"[0-9a-f]{64}", version.sha256):
                raise ValidationError("相关原件哈希元数据无效。")
        except ValidationError as exc:
            add("RELATED_SOURCE_CURRENT", S.PENDING_REVIEW, str(exc), lv=document.document_id, scope="cross_document",
                raw=(document.document_id, document.current_version_id, document.title))
            continue
        def raw_field(name):
            fields = [field for field in version.fields if field.name == name]
            if len(fields) != 1:
                return None, False, "missing/ambiguous"
            field = fields[0]
            confirmed = bool(field.status is F.CONFIRMED and field.confirmed_by and field.confirmed_at)
            value = field.confirmed_value if confirmed else field.value
            location = f"{version.original_filename} v{version.version_number};page:{field.page_number};sheet:{field.sheet_name};cell:{field.cell_reference}"
            return value, confirmed, location
        source_rows = raw_row_candidates(version, document.document_id)
        row_review_complete = False
        if source_rows:
            originals = {(row.table_index, row.row_number): row for row in source_rows}
            reviewed_rows = [row for row in workspace.reviewed_source_rows
                if row.document_id == document.document_id and row.version_id == version.version_id]
            row_review_complete = (len(reviewed_rows) == len(originals) and
                len({(row.table_index, row.row_number) for row in reviewed_rows}) == len(originals)
                and all((row.table_index, row.row_number) in originals
                    and row.original_values == originals[row.table_index, row.row_number].values
                    and row.confirmed_by and row.confirmed_at for row in reviewed_rows))
            add("RAW_ROW_SOURCE", state(row_review_complete),
                "原件逐行资料已人工核对。" if row_review_complete else "原件逐行资料须完整人工核对。",
                left=selected, field="rows", rv=len(source_rows),
                raw=(document.document_id, version.version_id, version.original_filename), scope="source",
                required=len(source_rows) > 1 or bool(reviewed_rows))
            if row_review_complete:
                reviewed_rows.sort(key=lambda row: (row.table_index, row.row_number))
                raw_values = {field.name: field.confirmed_value if field.status is F.CONFIRMED
                    and field.confirmed_by and field.confirmed_at else field.value for field in version.fields}
                other = _view(workspace, other_kind)
                other.update(values=raw_values, rows=[row.values for row in reviewed_rows])
                other.update(edit_area=A.SOURCE, edit_document_id=document.document_id,
                    edit_version_id=version.version_id,
                    edit_row_numbers={i: row.row_number for i, row in enumerate(reviewed_rows, 1)},
                    edit_table_indices={i: row.table_index for i, row in enumerate(reviewed_rows, 1)})
                currencies = [field for field in version.fields if field.name == "currency"]
                other["currency_confirmed"] = bool(len(currencies) == 1 and currencies[0].status is F.CONFIRMED
                    and currencies[0].confirmed_by and currencies[0].confirmed_at)
                other_index = index_rows(other)
                if selected_index and other_index:
                    compare_rows(selected, other, selected_index, other_index, code="RAW_CROSS_DOCUMENT",
                        fields=("description", "quantity", "unit", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"),
                        reviewed=True, raw_rows={i: (document.document_id, version.version_id, row.location)
                            for i, row in enumerate(reviewed_rows, 1)})
        fields = ("buyer", "currency", "incoterm", "total_amount", "quantity") if other_kind in {
            T.SALES_CONTRACT, T.ORDER_CONFIRMATION, T.COMMERCIAL_INVOICE, T.COMBINED_INVOICE_PACKING_LIST} else (
            "shipper", "consignee", "packages", "gross_weight_kg", "net_weight_kg") if other_kind is T.BILL_OF_LADING else ("quantity", "packages", "gross_weight_kg", "net_weight_kg")
        for field in fields:
            if row_review_complete and field in {"quantity", "packages", "gross_weight_kg", "net_weight_kg"}:
                continue
            # Exact contract mapping; customer_name is an explicit core source
            # synonym for buyer, not a seller/shipper or buyer/consignee alias.
            raw_name = "customer_name" if field == "buyer" and not any(f.name == "buyer" for f in version.fields) else field
            rv, reviewed, location = raw_field(raw_name)
            row = None
            lu = ru = None
            category = "text"
            if field in {"quantity", "packages", "gross_weight_kg", "net_weight_kg"}:
                fixed_single = (version.parser_name or "").startswith("fixed-template")
                explicit_single = (len(source_rows) == 1
                    and source_rows[0].values.get("product_code") == selected["rows"][0].get("product_code")) if len(selected["rows"]) == 1 else False
                if len(selected["rows"]) != 1 or not (explicit_single or fixed_single and not source_rows):
                    add("RAW_ROW_PAIRING", S.PENDING_REVIEW, "原件未提供可唯一对应的逐产品行，不能仅比较合计。",
                        left=selected, field=field, rv=rv, raw=(document.document_id, version.version_id, location), scope="cross_document")
                    continue
                row = 1
                lv = selected["rows"][0].get(field)
                code, code_confirmed, _ = raw_field("product_code")
                reviewed = reviewed and code_confirmed and code == selected["rows"][0].get("product_code")
                category = "weight" if field.endswith("_kg") else "quantity"
                lu = ru = "kg" if category == "weight" else "box" if field == "packages" else None
                if field == "quantity":
                    lu = selected["rows"][0].get("unit")
                    ru, unit_confirmed, _ = raw_field("unit")
                    reviewed = reviewed and unit_confirmed
            else:
                lv = total(selected) if field == "total_amount" else selected["values"].get(field)
                category = "money" if field == "total_amount" else "currency" if field == "currency" else "text"
                if category == "money":
                    lu = selected["values"].get("currency")
                    ru, currency_confirmed, _ = raw_field("currency")
                    reviewed = reviewed and currency_confirmed
            # Only applicable schema fields are compared; optional absent weights
            # on both sides do not become invented mandatory CI fields.
            if field not in selected["fields"] and field not in selected["row_fields"] and field != "total_amount":
                continue
            if not _nonempty(lv) and not _nonempty(rv) and field in {"packages", "gross_weight_kg", "net_weight_kg"}:
                continue
            if row is None and optional_absent(selected,
                    {"kind": other_kind, "form": get_form(workspace, other_kind)}, field, lv, rv):
                continue
            cmp(selected, None, field, lv, rv, category=category, row=row, lu=lu, ru=ru,
                reviewed=reviewed, raw=(document.document_id, version.version_id, location), code="RAW_CROSS_DOCUMENT",
                raw_field_name=raw_name)

    # Whole-case inventory remains visible without blocking a first standalone
    # seller file. The selected completed form counts as available in this view.
    present = {doc.document_type for doc in workspace.documents
               if doc.deleted_at is None and doc.current_version_id}
    from review.policy import current_artifacts
    present.update(artifact.document_type for artifact in current_artifacts(workspace))
    present.add(kind)
    from documents.checklist import requirement_groups
    from documents.manual_inputs import has_manual_data
    sources, deliveries = requirement_groups(workspace)
    for required_kind in sources:
        manual = has_manual_data(get_form(workspace, required_kind))
        # A generated delivery does not prove that its source input was supplied.
        original = any(doc.document_type is required_kind and doc.deleted_at is None
                       and doc.current_version_id for doc in workspace.documents)
        add("CASE_REQUIRED_DOCUMENT", state(original or manual),
            f"需提供资料：{required_kind.value}" + ("（已人工填写，非上传原件）" if manual and not original else ""),
            lv=required_kind.value, required=False, scope="case", item="source")
    for required_kind in deliveries:
        add("CASE_REQUIRED_DOCUMENT", state(required_kind in present),
            f"需制作并交付文件：{required_kind.value}", lv=required_kind.value,
            required=False, scope="case", item="delivery")
    return report
