"""Presentation shell and navigation for the Streamlit workbench."""
import streamlit as st

from auth.service import ROLE_PERMISSIONS
from core.identity import Permission

RESOURCE_PAGES = {"products": "产品资料", "knowledge": "知识库", "risk_terms": "风险词库"}
PAGE_LABELS = {"cases": "我的 Case", "sales": "起草客户回复", "sales_history": "回复与修订历史",
               "listing": "多平台文案", "listing_history": "文案结果记录", "documents": "交易单证工作台",
               **RESOURCE_PAGES, "model": "模型服务", "accounts": "账号与岗位", "offline": "断网保底测试"}


def apply_theme():
    st.markdown("""<style>
    .stApp {background:#f6f7f4;color:#23352f;}
    [data-testid="stSidebar"] {background:#fff;border-right:1px solid #e1e7e2;}
    .block-container {max-width:1280px;padding-top:4.5rem;}
    h1 {font-size:1.9rem!important;letter-spacing:-.03em;}
    h2,h3 {letter-spacing:-.02em;}
    [data-testid="stExpander"] {background:#fff;border-radius:12px;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
    button[kind="primary"] {background:#176e5d!important;border-color:#176e5d!important;}
    button[data-variant="segmented_control"][aria-checked="true"] {background:#edf5ef!important;color:#176e5d!important;border-color:#176e5d!important;}
    [data-testid="stSidebar"] button {text-align:left;}
    .cb-brand {font-size:25px;font-weight:750;color:#176e5d;margin:0 0 3px;}
    .cb-kicker {font-size:12px;letter-spacing:.1em;color:#7d8982;margin-bottom:20px;}
    </style>""", unsafe_allow_html=True)


def retain_widget_state():
    """Detach values from absent widgets when navigating between steps/pages."""
    from workflows.persistence import SALES_KEYS, LISTING_KEYS
    keys = (*SALES_KEYS, *LISTING_KEYS, "selected_case_id", "provider_url", "provider_model",
            "provider_protocol", "provider_key", "provider_confirm")
    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def navigate(page):
    st.session_state["workbench_page"] = page


def render_navigation(principal):
    permissions = ROLE_PERMISSIONS[principal.active_role]
    permitted = {"cases", "documents", *RESOURCE_PAGES, "model", "accounts", "offline"}
    if Permission.W1_GENERATE in permissions:
        permitted.update(("sales", "sales_history"))
    if Permission.W2_GENERATE in permissions:
        permitted.update(("listing", "listing_history"))
    default = "sales" if "sales" in permitted else "listing" if "listing" in permitted else "cases"
    page = st.session_state.get("workbench_page", default)
    if page not in permitted:
        page = default
        st.session_state["workbench_page"] = page
    with st.sidebar:
        st.markdown('<div class="cb-brand">CrossBot<span style="color:#9fb2a7"> /</span></div>'
                    '<div class="cb-kicker">外贸业务协作工作台</div>', unsafe_allow_html=True)
        environment = st.segmented_control("工作区域", ["真实工作区", "模拟演示区"],
                                           default="模拟演示区", key="workbench_environment")
        if environment is None:
            environment = "模拟演示区"
        groups = [("Case 协作", ["cases"]), ("W1 · 询盘回复", ["sales", "sales_history"]),
                  ("W2 · 商品文案", ["listing", "listing_history"]),
                  ("W3 · 交易单证", ["documents"]),
                  ("资料与知识", list(RESOURCE_PAGES)), ("设置与权限", ["model", "accounts"])]
        for title, pages in groups:
            with st.expander(title, expanded=page in pages):
                for target in pages:
                    st.button(PAGE_LABELS[target], key=f"nav_{target}", width="stretch",
                              disabled=target not in permitted,
                              type="primary" if page == target else "secondary",
                              on_click=navigate, args=(target,))
        st.divider()
        st.button("断网保底测试", key="nav_offline", width="stretch",
                  on_click=navigate, args=("offline",))
        st.caption("独立本地测试 · 不会替代真实业务结果")
    return page, environment == "模拟演示区"


def render_steps(workflow, case_id):
    marker = f"_step_case_{workflow}"
    if st.session_state.get(marker) != case_id:
        st.session_state[marker] = case_id
        st.session_state[f"{workflow}_step"] = 1
    value = st.session_state.get(f"{workflow}_step", 1)
    columns = st.columns(3)
    for index, label in enumerate(("填写资料", "生成与修订", "复核与导出"), 1):
        columns[index - 1].button(f"{index:02d}  {label}", key=f"{workflow}_step_{index}",
                                 width="stretch", type="primary" if value == index else "secondary",
                                 on_click=_set_step, args=(workflow, index))
    st.divider()
    return value


def _set_step(workflow, value):
    st.session_state[f"{workflow}_step"] = value
