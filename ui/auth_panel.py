"""Login and server-validated role controls for the local Streamlit app."""
import streamlit as st

from auth import AuthService
from auth.browser_session import (
    app_cookie_name, browser_cookie, queue_cookie, queue_cookie_clear, session_expiry, validated_cookie,
)
from core.errors import CrossBotError
from core.identity import Role

ROLE_LABELS = {
    Role.ADMIN: "管理员", Role.CUSTOMER_SERVICE: "客服", Role.PRODUCT: "产品",
    Role.OPERATIONS: "运营", Role.DOCUMENT: "单证", Role.REVIEWER: "复核",
}


def render_auth_panel(store, *, show_controls=True):
    auth = AuthService(store)
    name = app_cookie_name(auth)
    candidate = browser_cookie(name=name)
    token = st.session_state.get("session_token")
    if not token and candidate:
        try:
            validated_cookie(auth, candidate)
            st.session_state.clear()
            st.session_state["session_token"] = token = candidate
            st.rerun()
        except CrossBotError:
            st.session_state.clear()
            queue_cookie_clear(name=name)
            st.session_state["_auth_notice"] = "登录已失效，请重新登录"
            st.rerun()
    if not token:
        if notice := st.session_state.pop("_auth_notice", None):
            st.warning(notice)
        first = auth.needs_bootstrap()
        st.subheader("创建初始管理员" if first else "登录")
        with st.form("login"):
            username = st.text_input("账号")
            password = st.text_input("密码（至少 12 位）" if first else "密码", type="password")
            submitted = st.form_submit_button("创建管理员并登录" if first else "登录")
        if submitted:
            try:
                if first:
                    auth.bootstrap_admin(username, password)
                token = auth.login(username, password)
                expires_at = session_expiry(auth, token)
                st.session_state.clear()
                st.session_state["session_token"] = token
                queue_cookie(token, expires_at, name=name)
                st.rerun()
            except CrossBotError as error:
                st.error(str(error))
        st.stop()
    try:
        principal = auth.require_session(token)
    except CrossBotError as error:
        st.session_state.clear()
        queue_cookie_clear(name=name)
        st.session_state["_auth_notice"] = "登录已失效，请重新登录"
        st.rerun()
    if show_controls:
        render_user_controls(auth, token, principal)
    return auth, token, principal


def render_user_controls(auth, token, principal):
    with st.sidebar.expander("当前账号与岗位", expanded=False):
        st.write(principal.display_name)
        st.caption(f"当前岗位：{ROLE_LABELS[principal.active_role]}")
        for role, label in ROLE_LABELS.items():
            if st.button(label, key=f"role_{role.value}", disabled=role not in principal.allowed_roles or role == principal.active_role):
                auth.switch_role(token, role)
                st.session_state.pop("selected_case_id", None)
                st.rerun()
        if st.button("退出登录"):
            try:
                auth.logout(token)
            except CrossBotError:
                pass
            finally:
                st.session_state.clear()
                queue_cookie_clear(name=app_cookie_name(auth))
            st.rerun()


def render_admin_panel(auth, token, principal, *, sidebar=True):
    if principal.active_role != Role.ADMIN:
        return
    with (st.sidebar if sidebar else st).expander("账号与分配", expanded=not sidebar):
        with st.form("new_user"):
            username = st.text_input("新账号")
            name = st.text_input("显示名称")
            password = st.text_input("初始密码", type="password")
            roles = st.multiselect("授权角色", list(Role), format_func=ROLE_LABELS.get)
            submitted = st.form_submit_button("创建账号")
        if submitted:
            try:
                auth.create_user(token, username, password, name, roles)
                st.success("账号已创建")
            except CrossBotError as error:
                st.error(str(error))
        users = auth.list_users(token)
        cases = auth.store.list_cases()
        if cases and users:
            with st.form("assign_case"):
                case_id = st.selectbox("分配 Case", [c.case_id for c in cases])
                user_id = st.selectbox("分配给", [u.user_id for u in users], format_func={u.user_id: u.username for u in users}.get)
                assigned = st.form_submit_button("授权 Case")
                revoked = st.form_submit_button("撤销 Case")
            if assigned or revoked:
                try:
                    if assigned:
                        auth.assign_case(token, case_id, user_id)
                    else:
                        auth.revoke_case(token, case_id, user_id)
                    st.success("Case 分配已更新")
                except CrossBotError as error:
                    st.error(str(error))
