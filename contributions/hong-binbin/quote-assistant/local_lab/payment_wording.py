"""Bounded phrase screening, not semantic approval or a legal determination."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re


RULE_FILE = Path(__file__).with_name("rules") / "payment-wording.json"
RISK_EXAMPLE = "The payment terms have been agreed by both parties."
PENDING_EXAMPLE = "The proposed payment terms remain subject to confirmation by both parties."
PAYMENT = re.compile(r"\b(?:payment|deposit|prepayment|advance)\b", re.I)
CLAIM = re.compile(r"\b(?:agreed|accepted|confirmed|approved|agreement)\b", re.I)
# These qualifiers do not prove safety: route to manual review, never green approval.
QUALIFIER = re.compile(
    r"\b(?:not|never|no|if|unless|whether|please|pending|proposed|proposal|"
    r"subject to|would|could|should|may|might|once|until|after|before|"
    r"hasn't|haven't|isn't|aren't|don't|doesn't|didn't)\b|\?", re.I)


def check_payment_wording(english_reply, payment_terms):
    rules = json.loads(RULE_FILE.read_text(encoding="utf-8"))
    findings = []
    # Separate sentences/clauses; don't let a qualifier elsewhere hide a clear claim.
    for match in re.finditer(r"[^.!?;\n]+[.!?;]?", english_reply):
        sentence = match.group().strip()
        normalized = sentence.replace("’", "'")
        if PAYMENT.search(normalized) and CLAIM.search(normalized):
            qualified = bool(QUALIFIER.search(normalized))
            findings.append({
                "severity": "manual" if qualified else "risk",
                "original": sentence,
                "reason": ("含确认词及否定/条件/疑问等表达，需人工判断语义。" if qualified else
                           "出现付款条件已确认/接受/同意的措辞，但本应用未记录双方接受的证据。"),
                "suggestion": rules["suggestion"],
                "suggestion_zh": rules["suggestion_zh"],
            })
    risk = any(f["severity"] == "risk" for f in findings)
    return {
        **rules,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checked_by": "local_english_phrase_rules",
        "input_english_reply": english_reply,
        "input_payment_terms": payment_terms,
        "agreement_evidence": "not_recorded_by_application",
        "status": "risk" if risk else "manual",
        "status_label": "发现可能误写，需人工复核" if risk else "未发现明确肯定表述，仍需人工审核",
        "findings": findings,
    }
