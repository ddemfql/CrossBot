"""V3 authenticated Case workbench; no legacy main imports or unauthenticated bypass."""
import streamlit as st
from core.config import AppConfig
from core.errors import CrossBotError
from core.identity import Permission
from storage.bootstrap import open_store
from ui.auth_panel import render_auth_panel, render_admin_panel
from ui.case_panel import render_case_panel, render_case_summary
from knowledge.manage_ui import render_knowledge_management, can
from providers.settings_ui import render_provider_settings
from workflows.v3_ui import (reset_scope, render_workflow, render_attachments,
                             render_search, render_history)

st.set_page_config(page_title="CrossBot · V3", layout="wide")
st.title("CrossBot · Case 外贸工作台")
store = open_store()
auth, token, principal = render_auth_panel(store)
render_admin_panel(auth, token, principal)
case = render_case_panel(store, auth, token, principal)
reset_scope(principal, case.case_id if case else None)
options = []
if can(principal, Permission.W1_GENERATE):
    options.append("W1 询盘极速转化")
if can(principal, Permission.W2_GENERATE):
    options.append("W2 多平台Listing生成")
with st.sidebar:
    workflow = st.radio("工作流", options, key="v3_workflow") if options else None
    config, ready = render_provider_settings() if options else (AppConfig(mock_mode=True), False)
if case:
    render_case_summary(store, case, auth, token)
    pages = st.tabs(["当前工作流", "附件与来源", "客户背调", "产品与知识", "历史与复核"])
    for page, operation in zip(pages, [
        lambda: render_workflow(auth, token, principal, case, config, ready, "sales" if workflow and workflow.startswith("W1") else "listing") if workflow else st.info("当前岗位可使用其他授权页面。"),
        lambda: render_attachments(auth, token, case),
        lambda: render_search(auth, token, principal, case),
        lambda: render_knowledge_management(auth, token, principal),
        lambda: render_history(auth, token, principal, case),
    ]):
        with page:
            try:
                operation()
            except CrossBotError as error:
                st.error(error.message)
else:
    st.info("请先由管理员导入／创建Case并分配。可先维护有权限的产品与知识草稿。")
    render_knowledge_management(auth, token, principal)
