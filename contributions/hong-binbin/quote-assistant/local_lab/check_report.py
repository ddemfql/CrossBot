"""Freeze a code-produced C02 result independently from model-generated prose."""
from datetime import datetime, timezone

from local_lab.payment_ratio import check_payment_ratio


def build_check_report(payment_terms):
    check = check_payment_ratio(payment_terms)
    def number(value):
        return None if value is None else format(value, "f")
    labels = {"consistent": "算术一致，仍需双方确认", "invalid": "比例错误",
              "manual": "需人工核对，未判通过", "missing": "缺少比例，未判通过"}
    return {
        "rule_id": "C02", "rule_version": "1",
        "rule_name": "两笔付款比例算术一致性",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checked_by": "local_python_decimal",
        "input_payment_terms": payment_terms,
        "status": check.status, "status_label": labels[check.status],
        "message": check.message,
        "advance_percent": number(check.advance),
        "before_shipment_percent": number(check.before_shipment),
        "total_percent": number(check.total), "expected_total_percent": "100",
        "source": "docs/payment-ratio-practice.md",
        "basis": "项目模拟练习设定：两笔付款覆盖全部款项；每项大于0且不超过100，合计100%。",
        "limitation": "自定义算术检查，不是平台规定或法律审查，不证明双方已同意。未支持的句式须人工核对。",
    }
