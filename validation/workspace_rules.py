"""Deterministic W3 rules. Unknown or unreviewed evidence never means agreement."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import product
from core.models import (CrossCheckResult, CrossCheckStatus as Status, DocumentStatus,
    DocumentType as Type, FieldValueStatus, TradeData, Workspace, to_dict)
from review.policy import artifact_form_is_current

VERSION = "w3-1"
NUMERIC = {"quantity", "packages", "gross_weight_kg", "net_weight_kg", "total_amount"}
ALIASES = {
    "product_name": ("description", "product_description"),
    "packages": ("cartons", "package_count", "pieces"),
    "total_amount": ("invoice_total", "amount"),
    "customer_name": ("buyer", "customer"),
    "destination_country": ("country",),
    "shipper": ("seller",), "consignee": ("buyer", "customer_name"),
    "country_of_origin": ("origin_country",), "hs_code": ("htsus",),
    "marks_and_numbers": ("shipping_marks",),
}
PARSED_TYPES = {Type.COMMERCIAL_INVOICE, Type.PACKING_LIST, Type.PROFORMA_INVOICE,
                Type.PURCHASE_ORDER, Type.SALES_CONTRACT, Type.BILL_OF_LADING}


def number(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def trade_values(trade: TradeData) -> dict:
    """Stable semantic field names shared by templates and document comparisons."""
    result = {"order_no": trade.order_no, "customer_name": trade.buyer,
              "destination_country": trade.destination_country, "currency": trade.currency,
              "incoterm": trade.incoterm, "shipper": trade.seller, "consignee": trade.buyer,
              "product_name": "; ".join(line.description for line in trade.line_items),
              "country_of_origin": "; ".join(line.country_of_origin for line in trade.line_items),
              "hs_code": "; ".join(line.hs_code for line in trade.line_items),
              "grade_quality": "; ".join(line.grade_quality for line in trade.line_items),
              "marks_and_numbers": "; ".join(line.marks_and_numbers for line in trade.line_items),
              "package_contents": "; ".join(line.package_contents for line in trade.line_items)}
    for name in ("quantity", "packages", "gross_weight_kg", "net_weight_kg"):
        values = [number(getattr(line, name)) for line in trade.line_items]
        result[name] = str(sum(values, Decimal(0))) if values and all(v is not None for v in values) else None
    totals = [(number(line.quantity), number(line.unit_price)) for line in trade.line_items]
    result["total_amount"] = (str(sum((q * p for q, p in totals), Decimal(0)))
        if totals and all(q is not None and p is not None for q, p in totals) else None)
    return result


@dataclass
class _Evidence:
    document_id: str | None
    version_id: str | None
    values: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    def value(self, name):
        return self.values.get(name)
    def source(self, name):
        return self.sources.get(name, "missing/unreviewed")


class DeterministicRules:
    def evaluate(self, workspace: Workspace, *, trade_only: bool = False) -> list[CrossCheckResult]:
        results = []
        trade = workspace.trade
        master = _Evidence(None, str(trade.revision), trade_values(trade),
                           {name: f"trade revision {trade.revision}" for name in trade_values(trade)})

        def add(code, status, message, left=None, right=None, name=None, left_value=None, right_value=None):
            results.append(CrossCheckResult(case_id=workspace.case.case_id, rule_code=code,
                status=status, message=message, rule_version=VERSION,
                left_document_id=left.document_id if left else None,
                right_document_id=right.document_id if right else None,
                left_version_id=left.version_id if left else None,
                right_version_id=right.version_id if right else None,
                left_field=name, right_field=name,
                left_value=left.value(name) if left and name else left_value,
                right_value=right.value(name) if right and name else right_value,
                left_source=left.source(name) if left else "trade data",
                right_source=right.source(name) if right else None))

        def compare(code, left, right, name):
            a, b = left.value(name), right.value(name)
            if name in NUMERIC:
                a, b = number(a), number(b)
            elif a is not None and b is not None:
                a, b = str(a).strip().casefold(), str(b).strip().casefold()
            if a is None or b is None or a == "" or b == "":
                status = Status.PENDING_REVIEW
            else:
                status = Status.PASS if a == b else Status.FAIL
            add(code, status, f"{name}: " + {Status.PASS: "一致", Status.FAIL: "不一致",
                Status.PENDING_REVIEW: "缺失、格式异常或尚未人工确认"}[status], left, right, name)

        required_fields = ("order_no", "seller", "buyer", "destination_country", "currency",
                           "incoterm", "incoterm_version", "payment_terms", "transport_mode")
        missing = [name for name in required_fields if not str(getattr(trade, name) or "").strip()]
        if len(trade.currency) != 3 or not trade.currency.isalpha():
            missing.append("currency_format")
        add("TRADE_REQUIRED", Status.PENDING_REVIEW if missing else Status.PASS,
            "待补充: " + ", ".join(missing) if missing else "交易必要信息齐全", left_value=missing)
        add("TRADE_HUMAN_CONFIRMED", Status.PASS if trade.confirmed_by and trade.confirmed_at else Status.PENDING_REVIEW,
            "交易资料需要具名人工确认", left_value=trade.confirmed_by)
        add("TRADE_LINES", Status.PASS if trade.line_items else Status.PENDING_REVIEW,
            "需要至少一项产品", left_value=len(trade.line_items))
        for index, line in enumerate(trade.line_items, 1):
            for name in ("product_code", "description", "unit"):
                add(f"LINE_{index}_{name.upper()}", Status.PASS if str(getattr(line, name) or "").strip() else Status.PENDING_REVIEW,
                    f"第{index}行 {name} 必填", left_value=getattr(line, name))
            for name in ("quantity", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"):
                value = number(getattr(line, name))
                status = (Status.PENDING_REVIEW if value is None else
                    Status.PASS if value > 0 and (name != "packages" or value == value.to_integral_value()) else Status.FAIL)
                add(f"LINE_{index}_{name.upper()}", status, f"第{index}行 {name} 需为有效正数（箱数为整数）",
                    left_value=getattr(line, name))
            gross, net = number(line.gross_weight_kg), number(line.net_weight_kg)
            add(f"LINE_{index}_WEIGHT_ORDER", Status.PENDING_REVIEW if gross is None or net is None else
                Status.PASS if gross >= net else Status.FAIL, "毛重不得小于净重",
                left_value=line.gross_weight_kg, right_value=line.net_weight_kg)
        product_codes = [line.product_code.strip() for line in trade.line_items if line.product_code.strip()]
        duplicate_codes = sorted({code for code in product_codes if product_codes.count(code) > 1})
        add("TRADE_PRODUCT_CODES_UNIQUE", Status.FAIL if duplicate_codes else Status.PASS,
            "产品编码必须唯一" if duplicate_codes else "产品编码唯一", left_value=duplicate_codes)
        invalid_adjustments = [index for index, item in enumerate(trade.adjustments, 1)
            if not item.name.strip() or number(item.amount) is None
            or len(item.currency) != 3 or not item.currency.isalpha()]
        add("TRADE_ADJUSTMENTS", Status.PENDING_REVIEW if invalid_adjustments else Status.PASS,
            "费用或调整项目需填写说明、有限金额和币种" if invalid_adjustments else "费用或调整项目格式有效",
            left_value=invalid_adjustments)
        for name in ("expected_ship_date", "actual_ship_date", "due_date", "transaction_date"):
            value = getattr(trade, name)
            if value:
                try:
                    date.fromisoformat(value)
                    valid = True
                except (ValueError, TypeError):
                    valid = False
                add("TRADE_DATE_" + name.upper(), Status.PASS if valid else Status.PENDING_REVIEW,
                    "日期必须为有效的 YYYY-MM-DD", left_value=value)

        destination = trade.destination_country.strip().casefold()
        if destination in {"us", "usa", "united states", "united states of america"}:
            us_missing = [name for name in ("port_of_entry", "transaction_date", "transaction_place",
                "seller_address", "buyer_address", "responsible_exporter_person")
                if not str(getattr(trade, name) or "").strip()]
            for index, line in enumerate(trade.line_items, 1):
                for name in ("country_of_origin", "grade_quality", "marks_and_numbers", "package_contents"):
                    if not str(getattr(line, name) or "").strip():
                        us_missing.append(f"line_{index}_{name}")
            if not trade.invoice_adjustments_confirmed:
                us_missing.append("invoice_adjustments_review")
            expected_packages = sum(line.packages for line in trade.line_items
                                    if isinstance(line.packages, int) and not isinstance(line.packages, bool))
            package_failures = []
            package_missing = []
            if not trade.packages:
                package_missing.append("package_details")
            elif len(trade.packages) != expected_packages:
                package_failures.append("package_count")
            line_by_code = {line.product_code: line for line in trade.line_items}
            package_quantity: dict[str, Decimal] = {}
            package_gross: dict[str, Decimal] = {}
            package_net: dict[str, Decimal] = {}
            package_numbers = []
            line_codes = {line.product_code for line in trade.line_items}
            for index, package in enumerate(trade.packages, 1):
                package_numbers.append(package.package_number.strip())
                if package.product_code not in line_codes:
                    package_failures.append(f"package_{index}_unknown_product")
                for name in ("package_number", "marks_and_numbers", "product_code", "contents_description", "unit"):
                    if not str(getattr(package, name) or "").strip():
                        package_missing.append(f"package_{index}_{name}")
                quantity = number(package.quantity)
                gross = number(package.gross_weight_kg)
                net = number(package.net_weight_kg)
                if quantity is None or quantity <= 0 or gross is None or gross <= 0 or net is None or net <= 0:
                    package_missing.append(f"package_{index}_quantity_or_weight")
                if gross is not None and net is not None and gross < net:
                    package_failures.append(f"package_{index}_gross_below_net")
                if package.product_code and quantity is not None:
                    package_quantity[package.product_code] = package_quantity.get(package.product_code, Decimal(0)) + quantity
                if package.product_code and gross is not None:
                    package_gross[package.product_code] = package_gross.get(package.product_code, Decimal(0)) + gross
                if package.product_code and net is not None:
                    package_net[package.product_code] = package_net.get(package.product_code, Decimal(0)) + net
            duplicate_package_numbers = sorted({number for number in package_numbers
                if number and package_numbers.count(number) > 1})
            if duplicate_package_numbers:
                package_failures.append("duplicate_package_number")
            if trade.packages and not package_missing:
                for line in trade.line_items:
                    if line.product_code not in package_quantity:
                        package_failures.append("package_product_mapping")
                        continue
                    if number(line.quantity) != package_quantity[line.product_code]:
                        package_failures.append(f"{line.product_code}_quantity")
                    if number(line.gross_weight_kg) != package_gross.get(line.product_code):
                        package_failures.append(f"{line.product_code}_gross_weight")
                    if number(line.net_weight_kg) != package_net.get(line.product_code):
                        package_failures.append(f"{line.product_code}_net_weight")
            package_status = (Status.FAIL if package_failures else
                              Status.PENDING_REVIEW if package_missing else Status.PASS)
            add("US_IMPORT_PACKAGE_CONTENTS", package_status,
                "逐包内容、数量、唛头和重量需补齐或复核", left_value=package_failures or package_missing)
            if package_status is Status.PENDING_REVIEW:
                us_missing.append("package_details")
            elif package_status is Status.FAIL:
                us_missing.append("package_detail_conflict")
            for index, charge in enumerate(trade.adjustments, 1):
                if not charge.name.strip() or number(charge.amount) is None:
                    us_missing.append(f"adjustment_{index}")
            add("US_IMPORT_INVOICE_CONTENT", Status.PENDING_REVIEW if us_missing else Status.PASS,
                "美国进口发票资料待补齐或待确认" if us_missing else "美国进口发票基础信息已逐项补齐",
                left_value=us_missing)
            # 19 CFR Part 149 ISF applies to vessel cargo. The service only
            # records an external receipt; it never asserts that CrossBot filed ISF.
            is_vessel = trade.transport_mode.strip().casefold() in {"sea", "ocean", "vessel", "maritime"}
            receipt = next((document for document in workspace.documents
                            if document.deleted_at is None
                            and document.document_type is Type.ISF_RECEIPT
                            and document.current_version_id), None)
            isf_status = (Status.PASS if not is_vessel else
                          Status.PASS if receipt is not None else Status.PENDING_REVIEW)
            add("US_ISF_EXTERNAL_RECEIPT", isf_status,
                "海运美国交易需上传进口方或授权代理提供的 ISF 外部回执；系统不代报"
                if is_vessel and receipt is None else
                "非海运运输不适用本条 ISF 检查" if not is_vessel else
                "已托管外部 ISF 回执，仍不代表系统完成申报",
                left_value=receipt.document_id if receipt else None)

        # Case and trade are independently persisted shared contracts; avoid silent divergence.
        for case_name, trade_name in (("order_no", "order_no"), ("customer_name", "customer_name"),
                                       ("destination_country", "destination_country"), ("currency", "currency"),
                                       ("incoterm", "incoterm")):
            if getattr(workspace.case, case_name):
                case_side = _Evidence(None, str(workspace.trade.revision), {trade_name: getattr(workspace.case, case_name)},
                                      {trade_name: "case master"})
                compare("CASE_TRADE_" + case_name.upper(), case_side, master, trade_name)

        if trade_only:
            return results

        evidence_by_type = {}
        current_versions = {doc.document_id: doc.current_version_id for doc in workspace.documents
                            if doc.current_version_id and doc.deleted_at is None}
        for document in workspace.documents:
            if document.deleted_at is not None:
                continue
            current = next((v for v in document.versions if v.version_id == document.current_version_id), None)
            if current is None:
                if document.document_type in workspace.required_document_types:
                    add("DOCUMENT_CURRENT_VERSION", Status.PENDING_REVIEW, "文档缺少当前版本", left_value=document.document_id)
                continue
            if current.expires_on:
                try:
                    status = Status.FAIL if date.fromisoformat(current.expires_on) < date.today() else Status.PASS
                except (ValueError, TypeError):
                    status = Status.PENDING_REVIEW
                add("DOCUMENT_EXPIRY", status, "检查文件有效期", left_value=current.expires_on)
            if document.document_type not in PARSED_TYPES:
                continue  # Generic custody never asserts that attachment contents were checked.
            if current.status not in {DocumentStatus.PARSED, DocumentStatus.MANUALLY_ENTERED}:
                continue  # An unrequired external issuer file can be held without parsing it.
            side = _Evidence(document.document_id, current.version_id)
            for name in set(ALIASES) | {"order_no", "currency", "incoterm", "quantity", "gross_weight_kg", "net_weight_kg"}:
                candidates = [f for candidate in (name,) + ALIASES.get(name, ())
                              for f in current.fields if f.name == candidate]
                if candidates:
                    value = candidates[0]
                    location = (f"page {value.page_number}" if value.page_number else
                                f"{value.sheet_name or ''}!{value.cell_reference or ''}")
                    side.sources[name] = f"{current.version_id}: {location}; reviewer={value.confirmed_by or 'pending'}"
                    if value.status == FieldValueStatus.CONFIRMED and value.confirmed_by:
                        side.values[name] = value.confirmed_value
            evidence_by_type.setdefault(document.document_type, []).append(side)
            for name in ("order_no", "customer_name", "destination_country"):
                compare("DOCUMENT_TRADE_" + name.upper(), side, master, name)
            if document.document_type in {Type.COMMERCIAL_INVOICE, Type.PACKING_LIST, Type.PROFORMA_INVOICE, Type.PURCHASE_ORDER}:
                names = ["product_name", "quantity"]
                if document.document_type in {Type.PACKING_LIST, Type.COMMERCIAL_INVOICE}:
                    names += ["packages", "gross_weight_kg", "net_weight_kg"]
                if document.document_type in {Type.COMMERCIAL_INVOICE, Type.PROFORMA_INVOICE, Type.PURCHASE_ORDER}:
                    names += ["currency", "total_amount", "incoterm"]
                for name in names:
                    compare("DOCUMENT_TRADE_" + name.upper(), side, master, name)

        # Only latest output of each type may satisfy a requirement; revisions remain in history.
        latest_artifacts = {}
        for artifact in workspace.artifacts:
            previous = latest_artifacts.get(artifact.document_type)
            if previous is None or artifact.version_number >= previous.version_number:
                latest_artifacts[artifact.document_type] = artifact
        for kind, artifact in latest_artifacts.items():
            # All supported seller templates carry their deterministic checks;
            # external-issued records can never enter this generation path.
            if kind in evidence_by_type:
                continue
            valid = (artifact.status in {"draft", "approved"} and artifact.input_revision == trade.revision
                and artifact.input_snapshot == to_dict(trade) and artifact.source_versions == current_versions
                and artifact_form_is_current(workspace, artifact)
                and bool(artifact.sha256) and bool(artifact.storage_path) and bool(artifact.template_sha256)
                and bool(artifact.checks) and all(check.status == Status.PASS for check in artifact.checks))
            add("GENERATED_ARTIFACT_CURRENT", Status.PASS if valid else Status.PENDING_REVIEW,
                "生成稿需绑定当前交易、来源版本、模板及通过的校验", left_value=artifact.artifact_id,
                right_value=artifact.input_revision)
            if valid:
                values = trade_values(trade)
                evidence_by_type[kind] = [_Evidence(artifact.artifact_id, str(artifact.version_number), values,
                    {name: f"generated {artifact.sha256}; trade revision {trade.revision}" for name in values})]

        for kind in dict.fromkeys(workspace.required_document_types):
            present = bool(evidence_by_type.get(kind)) or (kind not in PARSED_TYPES | {Type.SHIPPING_INSTRUCTION} and any(
                d.document_type == kind and d.deleted_at is None and d.current_version_id
                and any(v.version_id == d.current_version_id for v in d.versions)
                for d in workspace.documents))
            add("REQUIRED_DOCUMENT_" + kind.value.upper(), Status.PASS if present else Status.PENDING_REVIEW,
                f"必需资料: {kind.value}", left_value=kind.value, right_value=present)

        pair_rules = [
            (Type.COMMERCIAL_INVOICE, Type.PACKING_LIST,
             ("product_name", "quantity", "packages", "gross_weight_kg", "net_weight_kg"), "CI_PL"),
            (Type.COMMERCIAL_INVOICE, Type.SALES_CONTRACT,
             ("customer_name", "currency", "total_amount", "incoterm"), "CI_CONTRACT"),
            (Type.PACKING_LIST, Type.BILL_OF_LADING,
             ("packages", "gross_weight_kg", "net_weight_kg", "shipper", "consignee"), "PL_BL"),
        ]
        for left_type, right_type, names, prefix in pair_rules:
            for left, right in product(evidence_by_type.get(left_type, []), evidence_by_type.get(right_type, [])):
                for name in names:
                    compare(prefix + "_" + name.upper(), left, right, name)
        return results


def validate_trade(workspace: Workspace) -> list[CrossCheckResult]:
    """Validate source trade data without creating circular document requirements."""
    return DeterministicRules().evaluate(workspace, trade_only=True)
