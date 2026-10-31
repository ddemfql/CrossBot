"""Case selection and creation; persistence stays in the storage layer."""
from decimal import Decimal, InvalidOperation

import streamlit as st

from core.errors import CrossBotError
from core.models import Case
from core.identity import Permission, Role
from auth.cases import CaseAccessService
from storage.bootstrap import DEMO_ID, import_animal_lamp_case, import_demo_case
from storage.sqlite_store import SQLiteStore

STATUS_LABELS = {
    "draft": "草稿", "missing_documents": "资料待补", "parsing": "解析中",
    "pending_review": "待复核", "review_returned": "复核退回",
    "cross_check_failed": "比对失败", "passed": "通过", "closed": "关闭",
}


def render_case_panel(store: SQLiteStore, auth, token, principal) -> Case | None:
    access = CaseAccessService(auth)
    with st.sidebar:
        st.header("我的 Case")
        if st.button("导入台灯演示案例", key="import_demo", disabled=principal.active_role != Role.ADMIN):
            auth.require_permission(token, Permission.CASE_ASSIGN)
            import_demo_case(store)
            st.session_state["selected_case_id"] = DEMO_ID
            st.rerun()
        if st.button("导入动物台灯固定演示案例", key="import_animal_lamp_demo", disabled=principal.active_role != Role.ADMIN):
            auth.require_permission(token, Permission.CASE_ASSIGN)
            case = import_animal_lamp_case(store)
            st.session_state["selected_case_id"] = case.case_id
            st.rerun()
        with st.expander("新建 Case"):
            with st.form("create_case"):
                number = st.text_input("Case 编号")
                customer = st.text_input("客户名称")
                country = st.text_input("目的国")
                order_no = st.text_input("订单号（选填）")
                currency = st.text_input("币种（选填）")
                total = st.text_input("总金额（选填）")
                submitted = st.form_submit_button("保存 Case", disabled=principal.active_role not in {Role.ADMIN, Role.CUSTOMER_SERVICE, Role.DOCUMENT})
            if submitted:
                try:
                    amount = Decimal(total.strip()) if total.strip() else None
                    if amount is not None and (not amount.is_finite() or amount < 0):
                        raise ValueError("总金额应为非负数字")
                    case = Case(
                        case_number=number.strip(), customer_name=customer.strip(),
                        destination_country=country.strip(), order_no=order_no.strip() or None,
                        currency=currency.strip().upper() or None, invoice_total=amount,
                    )
                    access.create_case(token, case)
                    st.session_state["selected_case_id"] = case.case_id
                    st.rerun()
                except (CrossBotError, InvalidOperation, ValueError) as error:
                    st.error(str(error))
        cases = access.list_cases(token)
        if not cases:
            st.info("暂无 Case")
            return None
        labels = {
            case.case_id: f"{case.case_number} · {case.customer_name}"
            + (" · 演示" if case.metadata.get("is_demo") else "") for case in cases
        }
        options = list(labels)
        if st.session_state.get("selected_case_id") not in options:
            st.session_state["selected_case_id"] = options[0]
        selected = st.selectbox("当前 Case", options, format_func=labels.get, key="selected_case_id")
    return access.get_case(token, selected)


def render_case_summary(store: SQLiteStore, case: Case, auth, token) -> None:
    access = CaseAccessService(auth)
    st.subheader(case.case_number)
    st.caption(
        f"{case.customer_name} · {case.destination_country} · "
        f"{STATUS_LABELS.get(case.status.value, case.status.value)}"
    )
    if case.metadata.get("is_demo"):
        st.info("演示数据：客户和订单为虚构样例，认证与交期仍待确认。")
    with st.expander("Case 资料与记录"):
        st.write({
            "订单号": case.order_no or "未填写", "币种": case.currency or "未填写",
            "总金额": str(case.invoice_total) if case.invoice_total is not None else "未填写",
            "贸易术语": case.incoterm or "未填写",
        })
        fixture = case.metadata.get("fixture")
        if fixture:
            st.json(fixture)
        st.write(f"关联文档：{len(access.list_documents(token, case.case_id))}")
        st.write("审计记录")
        for audit in access.list_audit_events(token, case.case_id):
            st.write(f"{audit.occurred_at.isoformat()} · {audit.action} · {audit.actor_id}")
