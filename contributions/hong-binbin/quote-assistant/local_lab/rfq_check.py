"""One explicitly scoped platform-reference check, with conservative materials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata

SCENES = {"general_email": "普通邮件（不套用 RFQ 平台规则）",
          "alibaba_rfq": "阿里国际站 RFQ（模拟场景）"}
ALIASES = {"棉布": "cotton", "纯棉": "cotton", "cotton": "cotton", "cotton fabric": "cotton",
           "涤纶": "polyester", "聚酯纤维": "polyester", "polyester": "polyester", "polyester fabric": "polyester"}


def normalize_material(value):
    # Canvas is a weave, not proof of cotton composition. Blends and alternatives
    # must not match just because one known word occurs inside them.
    cleaned = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return ALIASES.get(cleaned)


def build_rfq_report(scene, requested_material, product):
    rules = json.loads((Path(__file__).with_name("rules") / "rfq-material.json").read_text(encoding="utf-8"))
    offered = product.material if product else ""
    requested = normalize_material(requested_material)
    actual = normalize_material(offered)
    if scene == "general_email":
        status, label = "not_applicable", "当前是普通邮件场景，不套用 RFQ 平台规则"
        suggestion = "如需检查模拟RFQ，请主动选择对应场景，并填写买家要求。"
    elif scene != "alibaba_rfq":
        status, label = "manual", "未知场景，需人工核对适用规则"
        suggestion = "选择明确的业务场景后再检查。"
    elif product is None or not requested_material.strip():
        status, label = "manual", "资料不足，无法比较材质"
        suggestion = "选定商品，并填写买家明确要求的材质；不要猜测。"
    elif not requested or not actual:
        status, label = "manual", "材质描述超出本步支持范围，需人工核对"
        suggestion = "查看原始需求和商品材质证明；帆布、混纺、备选材质等不能自动判定等价。"
    elif requested != actual:
        status, label = "mismatch", "材质不一致：报价商品与买家要求存在差异"
        suggestion = "选择符合要求的商品；如拟提供替代品，先向买家说明差异并确认，不能改写真实需求来消除提醒。"
    else:
        status, label = "material_match", "仅材质字段相符，仍需核对其他规格和当前平台规则"
        suggestion = "继续核对尺寸、数量、认证等要求；此结果不是平台审核通过。"
    return {**rules, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checked_by": "local_material_comparison", "scene": scene,
            "input_requested_material": requested_material,
            "input_product": {"sku": product.sku, "name": product.name_zh, "material": offered} if product else None,
            "normalized_requested": requested, "normalized_offered": actual,
            "status": status, "status_label": label, "suggestion": suggestion}


def report_request(report):
    """Stable business input only: don't invalidate drafts due to check timestamps."""
    if report["scene"] != "alibaba_rfq":
        return None
    return {"scene": report["scene"], "requested_material": report["input_requested_material"],
            "data_kind": "synthetic_user_input"}
