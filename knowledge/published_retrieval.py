"""Authorized published snapshots and deterministic, advisory term review."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re

from core.errors import ParseError, ValidationError
from core.models import RiskItem, RiskSeverity, to_dict
from core.v3 import ResourceKind
from knowledge.governance import KnowledgeService


POLICY_FILE = Path(__file__).with_name("platform_policies.json")
_COUNTRIES = {"us": "us", "usa": "us", "united states": "us", "united states of america": "us", "美国": "us",
              "de": "de", "germany": "de", "德国": "de", "uk": "gb", "gb": "gb", "united kingdom": "gb", "英国": "gb"}
_PLATFORMS = {"amazon": "amazon", "amazon us": "amazon", "tiktok": "tiktok", "tiktok shop": "tiktok",
              "tiktok shop us": "tiktok", "alibaba": "alibaba", "alibaba.com": "alibaba", "阿里国际站": "alibaba"}
_GLOBAL = {"", "*", "all", "global", "全球", "全部"}
_SKU = re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)"
                  r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*(?![A-Za-z0-9_-])")
_SPECIFICATION = re.compile(
    r"(?:IP[0-9X]{2}K?|\d+(?:mAh|Ah|mA|A|mV|V|mW|kW|W|mm|cm|ml|L|mg|kg|g|kHz|MHz|GHz|Hz|K|h)"
    r"|USB[-_]?(?:[ABC]|[234]))", re.I)
_EXPLICIT_SKU = re.compile(
    r"(?<![A-Za-z0-9_])(?:SKU(?![A-Za-z0-9_])|model(?:\s+(?:number|no\.?))?(?![A-Za-z0-9_])|型号|货号|产品编号)"
    r"\s*(?:[:：=#]\s*)?(?:is\s+|[为是]\s*)?[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)", re.I)
_IDENTITY_FIELDS = ("sku", "product_sku", "model", "model_number")


def _normalize(value, aliases):
    normalized = str(value or "").strip().casefold()
    return aliases.get(normalized, normalized)


def _scope_matches(scope, selected, aliases):
    values = scope if isinstance(scope, (list, tuple)) else re.split(r"[,;，；|]", str(scope or ""))
    return any(_normalize(value, aliases) in _GLOBAL for value in values) or (
        bool(selected) and _normalize(selected, aliases) in {_normalize(value, aliases) for value in values})


def _literal_match(term, text):
    escaped = re.escape(term)
    prefix = r"(?<![A-Za-z0-9_-])" if re.match(r"[A-Za-z0-9]", term) else ""
    suffix = r"(?![A-Za-z0-9_-])" if re.search(r"[A-Za-z0-9]$", term) else ""
    return re.search(prefix + escaped + suffix, text, re.I)


def _applicable(record, platform, country):
    if not _scope_matches(record.get("platform"), platform, _PLATFORMS):
        return False
    if not _scope_matches(record.get("market"), country, _COUNTRIES):
        return False
    content = record.get("content", {})
    start = record.get("effective_date")
    end = content.get("effective_until") or content.get("expires_on")
    try:
        return not (start and date.fromisoformat(start) > date.today() or end and date.fromisoformat(end) < date.today())
    except (ValueError, TypeError):
        return False


def _product_identifiers(record):
    content = record["content"]
    identities = {str(content[key]).strip().casefold() for key in _IDENTITY_FIELDS if content.get(key)}
    name = str(content.get("name") or "").strip()
    name_words = {name.casefold(), *re.findall(r"[A-Za-z]{3,}|[\u3400-\u9fff]{2,}", name.casefold())}
    resource_id = record["resource_id"].casefold()
    if resource_id not in name_words:
        identities.add(resource_id)
    return identities


def _identity_references(text, known_identifiers):
    references = {identity for identity in known_identifiers if _literal_match(identity, text)}
    references.update(match.group().casefold() for match in _SKU.finditer(text)
                      if not _SPECIFICATION.fullmatch(match.group()))
    references.update(match.group(1).casefold() for match in _EXPLICIT_SKU.finditer(text))
    return references


def _product_matches(record, query, query_identifiers):
    if any(_literal_match(identity, query) for identity in _product_identifiers(record)):
        return True
    if query_identifiers:
        return False
    name = str(record["content"].get("name") or "").strip()
    keywords = [name] + re.findall(r"[A-Za-z]{3,}|[\u3400-\u9fff]{2,}", name)
    return any(word and _literal_match(word, query) for word in keywords)


def _product_scope_matches(scope, product):
    if _scope_matches(scope, None, {}):
        return True
    if product is None:
        return False
    identities = [product["resource_id"], product["content"].get("sku"), product["content"].get("name")]
    return any(identity and _scope_matches(scope, identity, {}) for identity in identities)


def _keywords_match(record, query):
    content = record["content"]
    searchable = " ".join([record["resource_id"], str(content.get("text", "")), str(content.get("title", "")),
                           str(content.get("category", "")), str(content.get("keywords", ""))]).casefold()
    words = re.findall(r"[A-Za-z0-9_-]{2,}|[\u3400-\u9fff]{2,}", query.casefold())
    return any(word in searchable for word in words)


def _knowledge_product_matches(record, product, known_identifiers):
    content = record["content"]
    text = " ".join(str(content.get(key) or "") for key in ("text", "title"))
    references = _identity_references(text, known_identifiers)
    references.update(str(content[key]).strip().casefold() for key in _IDENTITY_FIELDS if content.get(key))
    allowed = _product_identifiers(product) if product else set()
    return not references - allowed


def retrieve_published(auth, token, query, *, kind=None, platform=None, country=None, limit=8) -> list[dict]:
    """Read only through KnowledgeService; None limit scans every applicable term.

    Products require a unique exact SKU or unique product-name keyword match.
    Scoped knowledge/terms require that identity; global terms need no query hit.
    """
    if not isinstance(query, str):
        raise ValidationError("Published retrieval query must be text")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValidationError("Published retrieval limit must be a nonnegative integer or None")
    try:
        selected_kind = ResourceKind(kind).value if kind is not None else None
    except (ValueError, TypeError):
        raise ValidationError("Unsupported published resource kind") from None
    records = [to_dict(version) for version in KnowledgeService(auth).list_published(token)]
    known_identifiers = set().union(*(_product_identifiers(record) for record in records if record["kind"] == "product"))
    query_identifiers = _identity_references(query, known_identifiers)
    records = [record for record in records if _applicable(record, platform, country)]
    products = [record for record in records if record["kind"] == "product"
                and _product_matches(record, query, query_identifiers)]
    product = products[0] if len(products) == 1 else None
    if product and query_identifiers - _product_identifiers(product):
        product = None
    matches = []
    for record in records:
        if selected_kind is not None and record["kind"] != selected_kind:
            continue
        if not _product_scope_matches(record.get("product_scope"), product):
            continue
        if record["kind"] == "product":
            if record is not product:
                continue
        elif record["kind"] == "knowledge":
            if not _knowledge_product_matches(record, product, known_identifiers):
                continue
            if not _keywords_match(record, query) and not (product and record.get("product_scope")):
                continue
        matches.append(record)
    matches.sort(key=lambda record: (record["kind"] != "product", record["resource_id"]))
    return deepcopy(matches if limit is None else matches[:limit])


def review_published_terms(text, resources, platform=None) -> list[RiskItem]:
    """Resources must already be filtered for market, date and product scope."""
    risks = []
    for resource in resources:
        if resource.get("kind") != "prohibited_term" or not _scope_matches(resource.get("platform"), platform, _PLATFORMS):
            continue
        content = resource["content"]
        source = f"{resource['resource_id']}@{resource['version_id']} | {resource.get('source', '')}"
        if resource.get("source_url"):
            source += " | " + resource["source_url"]
        try:
            term = content["term"]
            if not isinstance(term, str) or not term.strip():
                raise ValueError
            pattern_type = content.get("pattern_type", "exact")
            if pattern_type == "regex":
                match = re.search(term, text, re.I)
            elif pattern_type in {"exact", "synonym"}:
                terms = [term] + (content.get("synonyms", []) if pattern_type == "synonym" else [])
                match = next((hit for item in terms if (hit := _literal_match(item, text))), None)
            else:
                raise ValueError
            severity = RiskSeverity(content.get("severity", "warning"))
        except (KeyError, ValueError, TypeError, re.error):
            risks.append(RiskItem(code="published_term_invalid", message="风险词条无效，须修复并人工复核。",
                                  source=source, blocking=True, severity=RiskSeverity.BLOCKING))
            continue
        if match:
            risks.append(RiskItem(code=content.get("code") or "published_prohibited_term",
                                  message=content.get("message", "命中已发布风险词，须人工复核。")
                                  + " 建议：" + (content.get("replacement_suggestion") or "核实依据并修改表述")
                                  + "；仅为本地词项提示，不代表平台完整词表或合规结论。",
                                  source=source, evidence=match.group(), severity=severity,
                                  blocking=severity == RiskSeverity.BLOCKING))
    return risks


def platform_policy_resources(platform, country=None) -> list[dict]:
    """Official summaries and explicitly local derived rules, never product facts."""
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        if data["schema_version"] != 1 or not isinstance(data["policies"], list):
            raise ValueError
        result = []
        for policy in data["policies"]:
            base = {key: deepcopy(policy[key]) for key in (
                "resource_id", "version_id", "source", "source_url", "platform", "market", "collected_on")}
            base["effective_date"] = policy.get("effective_date")
            base["verification_status"] = policy["verification_status"]
            base["reported_effective_date_unverified"] = policy.get("reported_effective_date_unverified")
            if not _applicable(base, platform, country):
                continue
            base.update(source_kind="official_summary_local_derivation", is_product_fact=False,
                        official_word_list=False, product_scope=None)
            result.append(dict(base, kind="knowledge", content={"text": policy["summary"]}))
            for rule in policy["derived_rules"]:
                re.compile(rule["term"], re.I)
                result.append(dict(base, resource_id=base["resource_id"] + ":" + rule["code"],
                                   kind="prohibited_term", content=dict(rule, pattern_type="regex", severity="warning")))
        return result
    except (OSError, KeyError, TypeError, ValueError, re.error):
        raise ParseError("Official policy summaries/local derived rules are missing or invalid") from None
