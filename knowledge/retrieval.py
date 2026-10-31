"""Exact demo SKU lookup and explicit scope filters; never a similarity-based fact claim."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from core.errors import CrossBotError, ErrorCode
from knowledge.wording_rules import load_wording_rules

ROOT = Path(__file__).parent
FILES = ("products.json", "platform_rules.json", "compliance_rules.json", "demo_case_products.json",
         "animal_lamp_knowledge.json")


def load_entries():
    records, hashes, ids = [], {}, set()
    try:
        for name in FILES:
            raw = (ROOT / name).read_bytes()
            hashes[name] = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw)
            if data["schema_version"] != 1 or not isinstance(data["entries"], list):
                raise ValueError
            for record in data["entries"]:
                for key in ("id", "type", "content", "version"):
                    if not isinstance(record[key], str) or not record[key].strip():
                        raise ValueError
                if record["id"] in ids or record["type"] not in {"product", "platform", "country", "process"}:
                    raise ValueError
                if record["data_kind"] != "synthetic" or record["source"]["kind"] != "synthetic" or record["source"]["official"] is not False:
                    raise ValueError
                for key in ("title", "path"):
                    if not isinstance(record["source"][key], str) or not record["source"][key].strip():
                        raise ValueError
                for key in ("applicable_product_skus", "applicable_platforms", "applicable_countries"):
                    if not isinstance(record[key], list) or any(not isinstance(x, str) or not x for x in record[key]):
                        raise ValueError
                if not isinstance(record["facts"], dict):
                    raise ValueError
                dates = [date.fromisoformat(record[x]) if record[x] is not None else None for x in ("effective_from", "effective_until")]
                if dates[0] and dates[1] and dates[0] > dates[1]:
                    raise ValueError
                ids.add(record["id"])
                records.append(record)
    except (OSError, ValueError, KeyError, TypeError):
        raise CrossBotError(ErrorCode.PARSE_FAILED, "知识资料缺失或格式错误，请检查本地资料；未将失败当作无命中。") from None
    return records, hashes


def retrieve_context(product_text: str, *, platforms=(), country=None, as_of=None):
    records, hashes = load_entries()
    rules, rule_hash = load_wording_rules()
    hashes["prohibited_terms.json"] = rule_hash
    today = as_of or date.today()
    # Only complete, explicit demo identifiers from the product field are eligible.
    # A generic phrase such as 'USB lamp' never establishes product identity.
    tokens = set(re.findall(r"(?<![A-Za-z0-9_-])DEMO-[A-Za-z0-9-]+(?![A-Za-z0-9_-])", product_text.upper()))
    known_skus = {r["id"] for r in records if r["type"] == "product"}
    sku = next(iter(tokens)) if len(tokens) == 1 and tokens <= known_skus else None
    normalized_country = {"de": "DE", "germany": "DE", "德国": "DE", "us": "US", "美国": "US"}.get((country or "").strip().casefold(), country)
    matches = []
    for record in records:
        start, end = record["effective_from"], record["effective_until"]
        if start and date.fromisoformat(start) > today or end and date.fromisoformat(end) < today:
            continue
        skus, ps, cs = (record[x] for x in ("applicable_product_skus", "applicable_platforms", "applicable_countries"))
        if skus and sku not in skus or ps and not set(ps).intersection(platforms) or cs and normalized_country not in cs:
            continue
        if record["type"] == "product" and record["id"] != sku:
            continue
        matches.append(deepcopy(record))
    product_hit = any(r["type"] == "product" for r in matches)
    notes = ["全部命中仅为项目自建模拟参考，不证明真实产品相同、认证通过或官方规则有效。",
             "知识库未提供认证、价格、交期依据；不得补写未知值，用户输入仍需核实。"]
    if not product_hit:
        notes.append("产品资料未知、待确认：未找到唯一且适用的完整演示SKU。")
    if not any(r["type"] == "country" for r in matches):
        notes.append("目标国资料未知、待确认：未找到适用条目。")
    for platform in platforms:
        if not any(r["type"] == "platform" and platform in r["applicable_platforms"] for r in matches):
            notes.append(f"{platform}平台资料未知、待确认。")
    return {"product_status": "reference_only" if product_hit else "unknown", "entries": matches,
            "notes": notes, "source_hashes": hashes, "as_of": today.isoformat(),
            "wording_rules": rules}


def citation_notes(context):
    result = list(context["notes"])
    for record in context["entries"]:
        scope = {k: record[k] for k in ("applicable_product_skus", "applicable_platforms", "applicable_countries")}
        result.append(f"模拟引用 {record['id']} | {record['source']['title']} | {record['source']['path']} | "
                      f"版本{record['version']} | 生效{record['effective_from'] or '未知'} | "
                      f"截止{record['effective_until'] or '未知'} | 适用{json.dumps(scope, ensure_ascii=False)} | {record['content']}")
    rules = context.get("wording_rules")
    if rules:
        result.append(f"内部演示风险词库 | {rules['source']['path']} | 版本{rules['version']} | "
                      f"{len(rules['rules'])}条规则；非官方违禁词全集；命中需人工处理。")
    result.append("知识文件SHA256：" + json.dumps(context["source_hashes"], ensure_ascii=False))
    return result
