"""Small, explicit learning rules; no legal review or language-model inference."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentCheck:
    status: str
    message: str


def check_payment_terms(value: str) -> PaymentCheck:
    terms = value.strip()
    if not terms:
        return PaymentCheck("missing", "待补充：付款条件。请记录何时付款、如何分期等信息。")
    if terms.casefold() in {"待确认", "待双方确认", "未确认", "未知", "待定", "tbd", "unknown"}:
        return PaymentCheck("pending", "待确认：付款条件仍未明确，请向买卖双方核实后补充。")
    return PaymentCheck("provided", "已填写付款条件；内容是否合理、是否已获双方确认，仍需人工核对。")
