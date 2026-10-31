"""Authorized page composition for the CrossBot workbench."""
import sqlite3

import streamlit as st

from auth.cases import CaseAccessService
from core.errors import CrossBotError
from core.identity import Permission, Role
from providers.settings_ui import render_provider_settings, session_provider_config
from storage.bootstrap import open_store
from ui.auth_panel import render_admin_panel, render_auth_panel, render_user_controls
from ui.case_panel import render_case_panel, render_case_summary
from ui.workbench import PAGE_LABELS, RESOURCE_PAGES, apply_theme, navigate, render_navigation, render_steps, retain_widget_state
from workflows.access import GenerationService
from workflows.export_ui import render_downloads
from workflows.listing_ui import render_listing
from workflows.persistence import bind_case_state, persist_case_workflow
from workflows.sales_ui import render_sales

st.set_page_config(page_title="CrossBot · 工作台", layout="wide")
apply_theme()
retain_widget_state()

try:
    store = open_store()
    auth, token, principal = render_auth_panel(store, show_controls=False)
    access = CaseAccessService(auth)
    page, demo = render_navigation(principal)
    render_user_controls(auth, token, principal)
    environment = "模拟演示区" if demo else "真实工作区"
    st.caption(f"{environment}  /  {PAGE_LABELS[page]}")

    if page in RESOURCE_PAGES:
        from ui.resources_panel import render_resources
        render_resources(auth, token, principal, page, demo=demo)
        st.stop()
    if page == "model":
        st.title("模型服务")
        if demo:
            st.info("模拟演示区使用本地模拟模型。请切换真实工作区配置 API 服务。")
        else:
            render_provider_settings(mode="真实", show_test=False)
        st.stop()
    if page == "accounts":
        st.title("账号与岗位")
        if principal.active_role == Role.ADMIN:
            render_admin_panel(auth, token, principal, sidebar=False)
        else:
            st.info("可在侧栏查看已授权岗位；账号创建与 Case 分配由管理员操作。")
        st.stop()
    if page == "offline":
        from ui.offline_panel import render_offline
        render_offline()
        st.stop()

    case = render_case_panel(store, auth, token, principal, sidebar=False,
                             show_actions=page == "cases", demo_mode=demo, show_header=page == "cases")
    if page == "cases":
        st.title("Case 协作")
        st.caption("在上方新建或选择 Case，再进入对应工作模块。" + ("演示 Case 与真实 Case 分开显示。" if demo else "此处仅显示真实 Case。"))
        if case:
            render_case_summary(store, case, auth, token)
        st.stop()
    if case is None:
        st.title(PAGE_LABELS[page])
        st.info("当前区域暂无可用 Case，请先前往「Case 协作」新建" + ("或导入演示案例。" if demo else "真实 Case。"))
        st.button("前往 Case 协作", on_click=navigate, args=("cases",), type="primary")
        st.stop()

    workflow = "sales" if page.startswith("sales") else "listing" if page.startswith("listing") else "documents"
    action = Permission.W1_GENERATE if workflow == "sales" else Permission.W2_GENERATE if workflow == "listing" else Permission.CASE_READ
    auth.require_permission(token, action, case.case_id)
    st.caption(f"当前 Case：{case.case_number} · {case.customer_name} · {case.destination_country}")
    if workflow == "documents":
        from ui.w3_panel import render_w3
        render_w3(auth, token, case)
        st.stop()

    # Bind both snapshots before showing downloads so another Case cannot leak into an export.
    for bound in ("sales", "listing"):
        bind_case_state(access, token, st.session_state, case.case_id, bound)
    history = page.endswith("_history")
    step = 3 if history else render_steps(workflow, case.case_id)
    config, ready = session_provider_config(mock=demo)
    if not demo and not ready:
        st.info("请在「设置与权限 → 模型服务」填写 API 设置并确认后生成。断网时可进入独立保底测试。")
    service = GenerationService(auth)
    renderer = render_sales if workflow == "sales" else render_listing
    renderer(config, ready, case_id=case.case_id, generation_service=service, auth_token=token, step=step)
    legacy = st.session_state.get(f"legacy_{workflow}_messages", [])
    if legacy:
        with st.expander("迁移自旧版的历史"):
            for message in legacy:
                st.markdown(f"**{message.get('role', 'message')}**: {message.get('content', '')}")
    persist_case_workflow(store, access, token, st.session_state, case.case_id, workflow)
    if step == 3:
        render_downloads()
except (CrossBotError, sqlite3.Error, OSError) as error:
    st.error(f"无法完成当前操作：{error}")
