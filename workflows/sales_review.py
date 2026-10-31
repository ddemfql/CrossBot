"""Limited local wording checks; unhit text still requires manual review."""
from core.models import RiskItem


def review_sales_text(content: str) -> list[RiskItem]:
    rules = [("ce certified", "CE认证声明"), ("fda approved", "FDA批准声明"), ("100% safe", "绝对安全声明")]
    return [RiskItem(code="unverified_claim", message=f"{label}需人工核实依据，不代表已认证或违规。",
                     source="local_sales_wording_v1", evidence=phrase)
            for phrase, label in rules if phrase in content.casefold()]
