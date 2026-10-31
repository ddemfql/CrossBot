"""Restore the user's verified local RFQ archive, never credentials or new drafts."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from local_lab.catalog import load_catalog

BACKUP_PATH = Path(__file__).parents[1] / "docs/verification/rfq-live-history.json"
BACKUP_SHA256 = "3a9de5b06d488a83a8490b7d45eeba89f66bd77772864a1bac5e6eeb3ef40828"
ROUND1_PATH = BACKUP_PATH.with_name("demo-round1-history.json")
ROUND1_SHA256 = "a953b37494d1ddbeeda5f9d6aeba39b4b19502efc1e96a2ee17dc94761f0a65e"
SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()


def restore_archived_rfq(state):
    return _restore_archive(state, BACKUP_PATH, BACKUP_SHA256)


def restore_demo_round1(state):
    return _restore_archive(state, ROUND1_PATH, ROUND1_SHA256, replace_existing=True)


def _restore_archive(state, path, expected_sha256, replace_existing=False):
    """Button callback runs before widgets; prepare everything before changing state."""
    if not replace_existing and (state.get("quote_history") or state.get("ai_draft")):
        raise ValueError("当前会话已有草稿或历史，未覆盖。请先下载当前记录。")
    try:
        raw = path.read_bytes()
    except OSError:
        raise ValueError("本地归档暂时无法读取，尚未恢复。请保留已下载的 JSON 文件。") from None
    # This is a fixed, previously reviewed archive, not a general JSON importer.
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("备份内容与已核实版本不一致，尚未恢复；请检查备份文件。")
    history = json.loads(raw.decode("utf-8-sig"))["versions"]
    latest = history[-1]
    context = latest["context"]
    try:
        products = load_catalog().products
    except (OSError, ValueError):
        raise ValueError("当前商品目录无法读取，请修复目录后再恢复。备份未改变。") from None
    if context["product"]["sku"] not in {p.sku for p in products}:
        raise ValueError("当前目录找不到备份中的商品编号，尚未恢复；请先恢复该商品资料。")
    request = context.get("platform_request", {})
    restored = {
        "quote_history": deepcopy(history),
        "revision_view": latest["version"],
        "quote_sku": context["product"]["sku"],
        "quote_quantity": context["quantity"],
        "payment_terms": context["payment_terms"],
        "rfq_scene": request.get("scene", "general_email"),
        "rfq_required_material": request.get("requested_material", ""),
        "ai_draft": {"context": deepcopy(context), "draft": deepcopy(latest["draft"])},
    }
    if replace_existing and state.get("quote_history") != history and state.get("quote_history"):
        state["history_before_recovery"] = deepcopy(state["quote_history"])
    for key in ("pending_change", "applied_dialogue", "change_message"):
        state.pop(key, None)
    state.update(restored)

