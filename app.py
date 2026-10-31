"""Case-scoped CrossBot workbench integrating A authorization and B workflows."""
import sqlite3

import streamlit as st

from auth.cases import CaseAccessService
from core.errors import CrossBotError
from core.identity import Permission
from auth.service import ROLE_PERMISSIONS
from storage.bootstrap import open_store
from ui.auth_panel import render_admin_panel, render_auth_panel
from ui.case_panel import render_case_panel, render_case_summary
from workflows.access import GenerationService
from workflows.export_ui import render_downloads
from workflows.listing_ui import render_listing
from workflows.persistence import bind_case_state, persist_case_workflow
from workflows.sales_ui import render_sales
from providers.settings_ui import render_provider_settings

st.set_page_config(page_title="CrossBot", layout="wide")
st.title("CrossBot 外贸业务协作工作台")
st.caption("W1/W2输出是待复核草稿；本地模拟知识不代表官方平台政策或完整合规审查。")

try:
    store = open_store()
    auth, token, principal = render_auth_panel(store)
    access = CaseAccessService(auth)
    render_admin_panel(auth, token, principal)
    case = render_case_panel(store, auth, token, principal)
except (CrossBotError, sqlite3.Error, OSError) as error:
    st.error(f"无法打开工作台：{error}")
    st.stop()

if case is None:
    st.info("请先新建 Case，或导入德国台灯演示案例。")
    st.stop()

render_case_summary(store, case, auth, token)

available = []
if Permission.W1_GENERATE in ROLE_PERMISSIONS[principal.active_role]:
    available.append("W1 询盘极速转化")
if Permission.W2_GENERATE in ROLE_PERMISSIONS[principal.active_role]:
    available.append("W2 多平台Listing生成")
if not available:
    st.info("当前岗位没有已接入的生成工作流；Case 文档和审计记录仍可查看。")
    st.stop()

with st.sidebar:
    st.header("工作流")
    workflow_label = st.radio("工作流", available, label_visibility="collapsed")
    workflow = "sales" if workflow_label.startswith("W1") else "listing"
    try:
        auth.require_permission(token, Permission.W1_GENERATE if workflow == "sales" else Permission.W2_GENERATE, case.case_id)
    except CrossBotError as error:
        st.error(error.message)
        st.stop()
    config, ready = render_provider_settings()

generation_service = GenerationService(auth)
bind_case_state(access, token, st.session_state, case.case_id, workflow)
if workflow == "sales":
    legacy = st.session_state.get("legacy_sales_messages", [])
    if legacy:
        with st.expander("迁移自旧版的 W1 历史"):
            for message in legacy:
                st.markdown(f"**{message.get('role', 'message')}**: {message.get('content', '')}")
    render_sales(config, ready, case_id=case.case_id, generation_service=generation_service, auth_token=token)
else:
    legacy = st.session_state.get("legacy_listing_messages", [])
    if legacy:
        with st.expander("迁移自旧版的 W2 历史"):
            for message in legacy:
                st.markdown(f"**{message.get('role', 'message')}**: {message.get('content', '')}")
    render_listing(config, ready, case_id=case.case_id, generation_service=generation_service, auth_token=token)

try:
    persist_case_workflow(store, access, token, st.session_state, case.case_id, workflow)
except CrossBotError as error:
    st.error(f"工作流状态未能保存：{error.message}")

render_downloads()
