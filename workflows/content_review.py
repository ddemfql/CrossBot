"""Deterministic advisory checks, not certification or semantic fact approval."""
from decimal import Decimal
import re
from core.models import RiskItem, RiskSeverity
from knowledge.wording_rules import load_wording_rules

FACT_PATTERNS = [
    ("price", r"(?:(?:EUR|USD|GBP)\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:EUR|USD|GBP))"),
    ("specification", r"(?<![A-Za-z0-9_.,])\d+(?:\.\d+)?\s*(?:watts?|W|V|mAh|K|cm|mm|ml|kg|pcs|pieces|days?|weeks?|business days|working days|hours?)(?![A-Za-z0-9_])|\d+(?:\.\d+)?\s*(?:瓦|伏|毫安时|厘米|毫米|毫升|公斤|件|工作日|小时|天|周)"),
]


def fact_token(text):
    """Normalize numeral/unit ordering without equating different units/currencies."""
    number = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    unit = re.sub(r"[\d\s.,]", "", text).casefold()
    aliases = {"watts": "w", "watt": "w", "pieces": "pcs", "瓦": "w", "毫米": "mm",
               "厘米": "cm", "毫升": "ml", "公斤": "kg", "件": "pcs", "天": "days", "day": "days", "周": "weeks", "week": "weeks",
               "伏": "v", "毫安时": "mah", "工作日": "businessdays", "workingdays": "businessdays", "小时": "hours", "hour": "hours"}
    return (Decimal(number.group().replace(",", "")), aliases.get(unit, unit))


def review_content(content: str, *, source_text: str = "") -> list[RiskItem]:
    risks = []
    library, _ = load_wording_rules()
    for rule in library["rules"]:
        match = re.search(rule["pattern"], content, re.I)
        if match:
            blocking = rule["severity"] == "blocking"
            risks.append(RiskItem(code=rule["code"],
                message=rule["message"] + "；建议：" + rule["replacement"] + "；命中仅为待审提示，不作合规结论。",
                severity=RiskSeverity(rule["severity"]), blocking=blocking,
                source=f"{rule['id']}@{library['version']}", evidence=match.group()))
    for category, pattern in FACT_PATTERNS:
        known = {fact_token(m.group()) for m in re.finditer(pattern, source_text, re.I)}
        seen = set()
        for match in re.finditer(pattern, content, re.I):
            token = fact_token(match.group())
            if token in known or token in seen:
                continue
            seen.add(token)
            risks.append(RiskItem(code="unsupported_" + category,
                message=f"生成内容含输入未提供的数值“{match.group()}”，须人工核对；未自动放行。",
                severity=RiskSeverity.WARNING, source="project_numeric_evidence_v1", evidence=match.group()))
    return risks
