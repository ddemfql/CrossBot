"""Session snapshots and explicit exports; never accept credentials or session state."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from hashlib import sha256
from pathlib import Path

SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()


def append_version(history, context, draft, check_report=None, wording_report=None, platform_report=None):
    record = {"version": len(history) + 1,
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "context": deepcopy(context), "draft": deepcopy(draft)}
    if check_report is not None:
        record["check_report"] = deepcopy(check_report)
    if wording_report is not None:
        record["wording_report"] = deepcopy(wording_report)
    if platform_report is not None:
        record["platform_report"] = deepcopy(platform_report)
    return [*history, record]


def compare_contexts(before, after):
    fields = [("product", "商品资料"), ("quantity", "采购数量"),
              ("unit_price", "货品单价"), ("goods_total", "货品金额"),
              ("currency", "币种"), ("payment_terms", "付款条件"),
              ("payment_status", "付款填写状态"), ("price_scope", "价格范围"),
              ("unconfirmed", "待确认事项"), ("source", "资料来源"),
              ("platform_request", "平台场景与买家要求")]
    def display(value):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "未填写")
    return [{"字段": label, "上一版": display(before.get(key)), "新版": display(after.get(key))}
            for key, label in fields if before.get(key) != after.get(key)]


def request_context(context, previous=None, check_report=None, platform_report=None):
    data = deepcopy(context)
    data["workflow"] = "首次询盘回复" if previous is None else "邮件报价修订"
    if check_report is not None:
        data["check_report"] = deepcopy(check_report)
    if platform_report is not None:
        data["platform_report"] = deepcopy(platform_report)
        if platform_report["scene"] == "alibaba_rfq":
            data["workflow"] = "RFQ 首次回复" if previous is None else "RFQ 报价修订"
    if previous is not None:
        data["revision"] = {"previous_version": previous["version"],
                            "previous_facts": deepcopy(previous["context"]),
                            "changes": compare_contexts(previous["context"], context)}
    return data


def export_history(history):
    return json.dumps({"notice": "模拟业务资料与 AI 草稿；未经人工业务审核，未发送给客户。",
                       "versions": history}, ensure_ascii=False, indent=2)


def business_context(context):
    """Dialogue metadata does not change quotation facts."""
    return {key: value for key, value in context.items() if key != "dialogue"}


def attach_current_dialogue(history, active):
    """Explicit post-generation attachment; never rewrite an old model input/draft."""
    if len(history) < 2 or not active:
        return history
    latest = history[-1]
    if latest["context"].get("dialogue") or latest.get("dialogue_attachment"):
        return history
    if active.get("context") != latest["context"] or not active.get("user_message"):
        return history
    changes = compare_contexts(history[-2]["context"], latest["context"])
    if not changes or active.get("feedback") != changes:
        return history
    result = deepcopy(history)
    result[-1]["dialogue_attachment"] = {
        "source": "current_session_post_generation",
        "notice": "从当前会话事后补充关联；原生成输入和草稿未改，不代表生成时已包含该要求。",
        "attached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_message": active["user_message"],
        "feedback": deepcopy(changes),
    }
    return result


def export_text(record):
    context = record["context"]
    dialogue = context.get("dialogue") or record.get("dialogue_attachment")
    dialogue_text = ""
    report = record.get("check_report")
    report_text = "\n本版未保存生成时的校验报告。\n"
    if report:
        report_text = (f"\n本版校验报告：{report['rule_id']} v{report['rule_version']} · {report['rule_name']}\n"
                       f"检查时间：{report['checked_at']}\n检查者：本地程序（Decimal），非模型判定\n"
                       f"检查输入：{report['input_payment_terms']}\n"
                       f"结果：{report['status_label']}\n说明：{report['message']}\n"
                       f"依据：{report['basis']}\n来源：{report['source']}\n"
                       f"适用边界：{report['limitation']}\n")
    if dialogue:
        dialogue_text = ("\n本轮用户修改要求：" + dialogue["user_message"] + "\n"
                         + dialogue.get("notice", "生成时已记录本轮修改要求。") + "\n")
    wording = record.get("wording_report")
    platform = record.get("platform_report")
    platform_text = "\n此版本未保存 RFQ 平台预检报告。\n"
    if platform:
        offered = platform["input_product"] or {}
        platform_text = (f"\n平台规则参考预检：{platform['rule_id']} v{platform['rule_version']} · {platform['status_label']}\n"
                         f"场景：{platform['scene']}\n检查时间：{platform['checked_at']}\n"
                         f"买家材质要求：{platform['input_requested_material']}\n商品材质：{offered.get('material', '')}\n"
                         f"建议：{platform['suggestion']}\n依据：{platform['basis']}\n"
                         f"规则来源：{platform['reference_url']}\n版本：{platform['reference_version']}\n"
                         f"有效性边界：{platform['freshness']}\n实现范围：{platform['implementation']}\n"
                         f"适用边界：{platform['limitation']}\n")
    wording_text = "\n此版本未保存英文检查报告。\n"
    if wording:
        wording_text = (f"\n英文表达检查：{wording['rule_id']} v{wording['rule_version']} · {wording['status_label']}\n"
                        f"检查时间：{wording['checked_at']}\n依据：{wording['basis']}\n"
                        f"来源：{wording['source']}\n背景参考：{wording['reference_url']}\n"
                        f"参考范围：{wording['reference_note']}\n边界：{wording['limitation']}\n")
        for finding in wording["findings"]:
            wording_text += (f"原句：{finding['original']}\n批注：{finding['reason']}\n"
                             f"建议：{finding['suggestion']}\n{finding['suggestion_zh']}\n")
    return (f"模拟报价草稿 · V{record['version']}\n生成时间（UTC）：{record['created_at']}\n"
            "未经人工业务审核，未发送给客户。\n\n"
            f"商品：{context['product']['sku']} · {context['product']['name']}\n"
            f"代码计算：{context['quantity']} × {context['unit_price']} {context['currency']}"
            f" = {context['goods_total']} {context['currency']}\n"
            f"范围：{context['price_scope']}\n{dialogue_text}\n英文客户回复\n{record['draft']['english_reply']}"
            f"\n\n中文内部核对清单\n{record['draft']['chinese_checklist']}\n{report_text}{wording_text}{platform_text}")
