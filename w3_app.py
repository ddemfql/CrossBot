"""Simple transaction workspace with two account roles."""
from pathlib import Path
import os

import streamlit as st

from core.errors import CrossBotError
from core.models import Actor, TradeData
from case_workflow.service import CaseService
from documents.service import DocumentService
from documents.simple_workbench import render_workspace
from storage.blobs import LocalBlobStore
from storage.identity import SQLiteIdentityStore
from storage.sqlite import SQLiteWorkspaceRepository
from storage.preferences import SQLiteUserDefaultsStore
from case_workflow.preferences import UserDefaultsService
from validation.rules import DeterministicRules

DATA_DIR = Path(os.environ.get("CROSSBOT_W3_DATA", Path.home() / ".crossbot" / "w3"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
repository = SQLiteWorkspaceRepository(DATA_DIR / "workspace.sqlite3")
identity = SQLiteIdentityStore(DATA_DIR / "workspace.sqlite3")
blobs = LocalBlobStore(DATA_DIR / "files")
defaults_store = SQLiteUserDefaultsStore(DATA_DIR / "workspace.sqlite3")
user_defaults = UserDefaultsService(repository, defaults_store)
cases = CaseService(repository, DeterministicRules(), identity=identity, defaults_store=defaults_store)
documents = DocumentService(repository, blobs)
ROLE_LABELS = {"sales": "业务员", "admin": "管理员"}


def _run(action, success):
    try:
        result = action()
        if result is not None and hasattr(result, "case"):
            st.session_state.w3_pending_case_id = result.case.case_id
        st.session_state.w3_notice = success
        st.rerun()
    except CrossBotError as exc:
        st.error(exc.message)
    except (ValueError, TypeError) as exc:
        st.error(str(exc))


def _login():
    st.title("交易单证工作台")
    first = not identity.count_users()
    if first:
        st.info("创建首个管理员账号；管理员可以开通业务员账号。")
    with st.form("w3_login"):
        user = st.text_input("账号")
        password = st.text_input("密码", type="password", help="至少 10 个字符")
        submitted = st.form_submit_button("创建管理员" if first else "登录")
    if submitted:
        try:
            if first:
                identity.add_user(user, password, ["admin"])
            actor = identity.authenticate(user, password)
            if actor:
                st.session_state.w3_actor = actor
                st.rerun()
            st.error("账号或密码不正确。")
        except CrossBotError as exc:
            st.error(exc.message)


def _accounts(actor):
    if "admin" not in actor.roles:
        return
    with st.expander("账号管理"):
        st.dataframe([{"账号": u["user_id"], "角色": " / ".join(
            ROLE_LABELS[r] for r in u["roles"])} for u in identity.list_users()], hide_index=True)
        with st.form("add_user"):
            name = st.text_input("新账号")
            password = st.text_input("初始密码", type="password", help="至少 10 个字符")
            role = st.selectbox("角色", ["sales", "admin"], format_func=ROLE_LABELS.get)
            submit = st.form_submit_button("开通账号")
        if submit:
            _run(lambda: identity.add_user(name, password, [role]), "账号已开通。")


def _session_actor():
    """Keep an authenticated session across Streamlit's module hot reload."""
    actor = st.session_state.get("w3_actor")
    if (actor is not None and not isinstance(actor, Actor)
            and type(actor).__module__ == "core.models" and type(actor).__name__ == "Actor"
            and getattr(actor, "authenticated", False) is True
            and getattr(actor, "kind", None) == "human"):
        # The object is held in trusted server session state from an earlier
        # module instance. Rebind to the current class, using provisioned roles.
        user = next((user for user in identity.list_users()
                     if user["user_id"] == getattr(actor, "user_id", None)), None)
        if user is not None:
            actor = Actor(user["user_id"], user["roles"], authenticated=True, kind="human")
            st.session_state.w3_actor = actor
    return actor


def _creation_tools(actor):
    with st.expander("新建交易 / 导入资料"):
        with st.form("new_trade"):
            order = st.text_input("订单号（可稍后补充）")
            buyer = st.text_input("客户（可稍后补充）")
            submitted = st.form_submit_button("新建交易")
        if submitted:
            _run(lambda: cases.create(actor, TradeData(order_no=order, buyer=buyer, currency="")), "交易已创建。")
        with st.form("import_trade_archive"):
            archive = st.file_uploader("导入已有资料 ZIP", type=["zip"])
            imported = st.form_submit_button("导入资料")
        if imported and archive:
            from case_workflow.importer import ArchiveImporter
            _run(lambda: ArchiveImporter(repository, blobs).import_zip(
                archive.getvalue(), archive.name, actor), "资料已导入，请核对分类。")


def render():
    actor = _session_actor()
    if not isinstance(actor, Actor) or not actor.authenticated:
        _login()
        return
    st.title("交易单证工作台")
    st.caption("整理文件分类，查看缺失资料，规则核验与人工确认后导出。")
    notice = st.session_state.pop("w3_notice", None)
    if notice:
        st.success(notice)
    with st.sidebar:
        st.write(f"{actor.user_id} · {' / '.join(ROLE_LABELS.get(r, '业务员') for r in actor.roles)}")
        if st.button("退出登录", key="w3_logout"):
            st.session_state.pop("w3_actor", None)
            from documents.drafts import NAMESPACE, EPOCHS
            for key in list(st.session_state):
                if key in {NAMESPACE, EPOCHS} or str(key).startswith("w3-draft-widget-"):
                    st.session_state.pop(key, None)
            st.rerun()
    workspaces = cases.list(actor)
    if not workspaces:
        st.info("新建一笔交易或导入 ZIP 开始。")
        _creation_tools(actor)
        _accounts(actor)
        return
    pending_case = st.session_state.pop("w3_pending_case_id", None)
    if pending_case in {w.case.case_id for w in workspaces}:
        st.session_state.w3_case_id = pending_case
    requested_case = st.query_params.get("case")
    if (requested_case and requested_case != st.session_state.get("w3_requested_case")
            and requested_case in {w.case.case_id for w in workspaces}):
        st.session_state.w3_case_id = requested_case
        st.session_state.w3_requested_case = requested_case
    if st.session_state.get("w3_case_id") not in {w.case.case_id for w in workspaces}:
        st.session_state.pop("w3_case_id", None)
    selected = st.selectbox("选择交易", [w.case.case_id for w in workspaces],
        format_func=lambda key: next(f"{w.trade.order_no or w.case.case_number} · {w.trade.buyer or '客户待补'}"
            for w in workspaces if w.case.case_id == key), key="w3_case_id")
    render_workspace(cases.get(selected, actor), actor, cases, documents, repository, blobs,
                     defaults_service=user_defaults)
    with st.container(key="w3-secondary-account"):
        _creation_tools(actor)
        _accounts(actor)

