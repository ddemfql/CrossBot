"""B's scoped projection of A's published versions; never scans draft tables or demo JSON."""
from datetime import date
import hashlib
import json

from core.models import RiskItem, RiskSeverity, to_dict
from core.schemas import ListingRequest
from core.v3 import ResourceKind
from knowledge.governance import KnowledgeService


def country_key(value):
    value = (value or "").strip().casefold()
    return {"germany": "de", "德国": "de", "deutschland": "de"}.get(value, value)


def published_context(auth, token, request):
    service = KnowledgeService(auth)
    versions = service.list_published(token)
    selected = request.context.product_version_id
    product = service.get_version(token, selected) if selected else None
    # The generation gate already checks this is a published/archived PRODUCT version.
    ids = {x for x in [getattr(product, "resource_id", None), selected,
                       product.content.get("sku") if product else None] if x}
    country = request.target_country if isinstance(request, ListingRequest) else request.customer_country
    platforms = request.platforms if isinstance(request, ListingRequest) else []
    if product:
        versions = [v for v in versions if v.kind != ResourceKind.PRODUCT] + [product]
    else:
        versions = [v for v in versions if v.kind != ResourceKind.PRODUCT]
    entries, notes = [], []
    for version in versions:
        if version.market and country_key(version.market) != country_key(country):
            continue
        if version.platform and version.platform not in platforms:
            continue
        if version.product_scope and version.product_scope not in ids:
            continue
        if version.effective_date:
            try:
                if date.fromisoformat(version.effective_date) > date.today():
                    continue
            except ValueError:
                notes.append(f"版本{version.version_id}生效日期无效，未用于本次提示词。")
                continue
        entries.append(to_dict(version))
    if not entries:
        notes.append("没有命中适用的已发布知识；手工补充和演示Case不能当作已审核主数据。")
    notes.append("已发布只表示独立审核/发布流程完成，不自动证明认证、价格、交期或最新法规事实；冲突需人工复核。")
    immutable = ("version_id", "resource_id", "version_number", "kind", "content", "source", "source_url",
                 "source_kind", "effective_date", "market", "platform", "product_scope", "language",
                 "parent_version_id", "created_by", "created_at")
    hashes = {v["version_id"]: hashlib.sha256(json.dumps({k: v[k] for k in immutable}, ensure_ascii=False,
                                                         sort_keys=True).encode()).hexdigest() for v in entries}
    return {"kind": "published", "entries": entries, "notes": notes, "source_hashes": hashes,
            "product_status": "published_reference" if any(v["kind"] == "product" for v in entries) else "manual_unverified",
            "as_of": date.today().isoformat()}


def published_notes(context):
    notes = list(context["notes"])
    for v in context["entries"]:
        notes.append(f"已发布引用 {v['resource_id']} | 版本{v['version_number']} / {v['version_id']} | "
                     f"来源{v['source']} {v.get('source_url') or ''} | 市场{v.get('market')} | 平台{v.get('platform')} | "
                     f"产品范围{v.get('product_scope')} | 生效{v.get('effective_date') or '未提供'}")
    notes.append("发布版本快照SHA256：" + json.dumps(context["source_hashes"], ensure_ascii=False))
    return notes


def review_published_terms(content, context):
    risks = []
    for version in context["entries"]:
        if version["kind"] != "prohibited_term":
            continue
        term = version["content"]
        if term.get("pattern_type", "exact") == "regex":
            # Arbitrary published regex is not executed on the UI/model thread.
            risks.append(RiskItem("published_regex_manual_review", "已发布正则词规则尚需D的有界执行器复核；本轮未执行该规则。",
                                  source=version["version_id"], evidence=term.get("term")))
            continue
        words = [term.get("term", "")] + (term.get("synonyms", []) if term.get("pattern_type") == "synonym" else [])
        for word in words:
            if isinstance(word, str) and word and word.casefold() in content.casefold():
                blocking = term.get("severity") == "blocking"
                risks.append(RiskItem("published_term_match", "命中已发布词项，需人工处理：" + word,
                    severity=RiskSeverity.BLOCKING if blocking else RiskSeverity.WARNING,
                    source=version["version_id"], evidence=word, blocking=blocking))
                break
    return risks
