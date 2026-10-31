"""C02: arithmetic only, for the explicitly supported two-instalment exercise."""
from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata


@dataclass(frozen=True)
class RatioCheck:
    status: str
    message: str
    advance: Decimal | None = None
    before_shipment: Decimal | None = None
    total: Decimal | None = None


# Full-match intentionally avoids treating discounts, material percentages,
# alternatives or negated terms as an agreed payment schedule.
_NUMBER = r"([+-]?\d{1,3}(?:\.\d{1,2})?)"
_TERMS = re.compile(
    r"\s*(?:(?:模拟拟议条款|模拟约定|拟议条款|付款条件)\s*[:,]\s*)?"
    + _NUMBER + r"\s*%\s*预付款\s*[,;]\s*"
    + _NUMBER + r"\s*%\s*发货前支付"
    + r"\s*(?:[,;]\s*待双方确认)?\s*[。.]?\s*"
)


def check_payment_ratio(value: str) -> RatioCheck:
    terms = unicodedata.normalize("NFKC", value)
    if not terms.strip():
        return RatioCheck("missing", "尚未填写两笔付款比例，暂不能检查合计。")
    match = _TERMS.fullmatch(terms)
    if match is None:
        return RatioCheck("manual", "暂不能自动判断此写法，请人工核对；不等于校验通过。支持示例：50% 预付款，50% 发货前支付，待双方确认。")
    advance, before_shipment = (Decimal(number) for number in match.groups())
    total = advance + before_shipment
    if not (0 < advance <= 100 and 0 < before_shipment <= 100):
        return RatioCheck("invalid", "两笔付款的每项比例都应大于 0%、不超过 100%；请修改后再生成草稿。",
                          advance, before_shipment, total)
    if total != 100:
        return RatioCheck("invalid", f"付款比例合计为 {total:g}%，应为 100%；请修改后再生成草稿。",
                          advance, before_shipment, total)
    return RatioCheck("consistent", "两笔付款比例合计为 100%，算术一致；条款仍需双方确认。",
                      advance, before_shipment, total)
