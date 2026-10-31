"""Allowlisted business snapshots only: never serialize settings or an entire session."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from core.errors import CrossBotError, ErrorCode
from core.models import to_dict

SALES_FIELDS = ("inquiry", "product_info", "target_language", "customer_country", "requested_quantity", "correlation_id")
LISTING_FIELDS = ("product_description", "platforms", "target_country", "target_language", "correlation_id")
RESPONSE_FIELDS = ("content", "risks", "pending_confirmations", "correlation_id", "provider", "model")


def fields(value, names):
    return {name: deepcopy(to_dict(getattr(value, name))) for name in names}


def business_snapshot(state):
    """Read exactly three successful-result keys, ignoring current form/configuration values."""
    conversation = state.get("sales_conversation")
    listing_request = state.get("listing_request")
    listing_results = state.get("listing_results")
    w1 = w2 = None
    try:
        if conversation is not None and conversation.turns:
            w1 = {"request": fields(conversation.request, SALES_FIELDS), "versions": [
                {"version": number, "kind": turn.kind, "feedback": turn.feedback,
                 "response": fields(turn.response, RESPONSE_FIELDS)}
                for number, turn in enumerate(conversation.turns, 1)
            ]}
        if listing_request is not None or listing_results is not None:
            if not listing_results or listing_request is None or set(listing_results) != set(listing_request.platforms):
                raise ValueError
            w2 = {"request": fields(listing_request, LISTING_FIELDS), "results": {
                platform: fields(listing_results[platform], RESPONSE_FIELDS) for platform in listing_request.platforms
            }}
    except (AttributeError, TypeError, ValueError):
        raise CrossBotError(ErrorCode.INVALID_INPUT, "保存的业务记录结构不完整，暂无法导出；请保留当前页面。") from None
    return {"w1": w1, "w2": w2}


def export_bundle(snapshot):
    if snapshot["w1"] is None and snapshot["w2"] is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "当前没有可下载的成功业务记录。")
    business = deepcopy(snapshot)
    canonical = json.dumps(business, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "format": "crossbot-business-export", "schema_version": 1,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "notice": "仅保存已有业务输入、草稿和引用，不是原始HTTP日志/请求ID/用量；没有记录各版生成时间。知识资料仍为模拟，内容须人工核对。",
        "business_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **business,
    }


def json_bytes(bundle):
    return json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")


def text_bytes(bundle, workflow):
    lines = ["CrossBot 业务备份", "导出时间（非生成时间）：" + bundle["exported_at_utc"], bundle["notice"], ""]
    data = bundle[workflow]
    if data is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "该工作流没有可下载记录。")
    lines += ["保存的业务输入：", json.dumps(data["request"], ensure_ascii=False, indent=2), ""]
    if workflow == "w1":
        records = [(f"W1 V{item['version']} · {item['kind']}", item["feedback"], item["response"]) for item in data["versions"]]
    else:
        records = [("W2 " + platform, None, response) for platform, response in data["results"].items()]
    for title, feedback, response in records:
        lines += [title, f"来源：{response['provider'] or '未标注'}；模型：{response['model'] or '未标注'}"]
        if feedback is not None:
            lines += ["本轮修改要求：", feedback]
        lines += ["草稿正文：", response["content"], "风险及待确认项：",
                  json.dumps(response["risks"], ensure_ascii=False)]
        lines += response["pending_confirmations"] + ["", "=" * 40, ""]
    # BOM makes Chinese readable in common Windows text editors.
    return "\n".join(lines).encode("utf-8-sig")
