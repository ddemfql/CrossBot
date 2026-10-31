"""Deterministic advisory checks, not certification or semantic fact approval."""
from decimal import Decimal
import re
from core.models import RiskItem, RiskSeverity

WORD_RULES = [
    ("certification_claim", r"\b(?:CE[ -]certified|FDA[ -]approved|RoHS[ -]compliant|certified)\b|通过.{0,8}认证|已获.{0,8}认证", "认证声明须核验证书及适用范围"),
    ("absolute_claim", r"100\s*%\s*(?:safe|guaranteed|effective)|\b(?:best in the world|zero risk|risk[ -]free|guaranteed safe)\b|绝对安全|零风险|无风险|百分之百有效", "绝对化表述需人工处理"),
    ("medical_claim", r"\b(?:cures?|treats?|medical[ -]grade|medical benefit|heals?)\b|治疗|治愈|医疗级", "医疗或功效表达需证据及人工复核"),
    ("evidence_claim", r"\beye protection\b|护眼", "护眼等声明需证据及人工复核"),
    ("design_inference", r"\b(?:compact design|lightweight|waterproof)\b|小巧设计|轻量化|防水", "设计或性能描述需原始参数依据"),
    ("delivery_commitment", r"\b(?:ships? tomorrow|immediate delivery|guaranteed delivery|ready for dispatch)\b|明天发货|立即发货|保证交期", "交付承诺须核实库存、运输及日期依据"),
]
FACT_PATTERNS = [
    ("price", r"(?:(?:EUR|USD|GBP)\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:EUR|USD|GBP))"),
    ("specification", r"(?<![A-Za-z0-9_.,])\d+(?:\.\d+)?\s*(?:watts?|W|cm|mm|ml|kg|pcs|pieces|days?|weeks?|business days)(?![A-Za-z0-9_])|\d+(?:\.\d+)?\s*(?:瓦|厘米|毫米|毫升|公斤|件|天|周)"),
]


def fact_token(text):
    """Normalize numeral/unit ordering without equating different units/currencies."""
    number = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    unit = re.sub(r"[\d\s.,]", "", text).casefold()
    aliases = {"watts": "w", "watt": "w", "pieces": "pcs", "瓦": "w", "毫米": "mm",
               "厘米": "cm", "毫升": "ml", "公斤": "kg", "件": "pcs", "天": "days", "day": "days", "周": "weeks", "week": "weeks"}
    return (Decimal(number.group().replace(",", "")), aliases.get(unit, unit))


def review_content(content: str, *, source_text: str = "") -> list[RiskItem]:
    risks = []
    for code, pattern, message in WORD_RULES:
        match = re.search(pattern, content, re.I)
        if match:
            risks.append(RiskItem(code=code, message=message + "；命中仅为待审提示，不作合规结论。",
                source="project_content_review_v2", evidence=match.group()))
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
