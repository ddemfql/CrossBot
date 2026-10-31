"""Streamlit entry point with Case-scoped, persistent W1/W2 conversations."""
import sqlite3

import streamlit as st

from core.errors import CrossBotError
from core.models import to_dict
from core.schemas import ListingRequest, SalesRequest
from core.identity import Permission
from core.v3 import RequestContext
from auth.cases import CaseAccessService
from auth.service import ROLE_PERMISSIONS
from main import iterate_workflow_stream, run_workflow_stream
from storage.bootstrap import open_store
from ui.case_panel import render_case_panel, render_case_summary
from ui.auth_panel import render_auth_panel, render_admin_panel
from workflows.access import GenerationService

st.set_page_config(page_title="CrossBot", layout="wide")
st.title("CrossBot 外贸智能业务员与合规助手")


def save_or_stop(access, token, conversation):
    try:
        access.save_conversation(token, conversation)
    except (CrossBotError, sqlite3.Error) as error:
        st.error(f"保存失败：{error}")
        st.stop()


def render_risks(risks):
    if risks:
        with st.expander("合规校验反馈", expanded=True):
            for risk in risks:
                st.warning(risk)
    else:
        st.caption("当前关键词检查未发现命中项，仍需人工复核。")


try:
    store = open_store()
    auth, token, principal = render_auth_panel(store)
    access = CaseAccessService(auth)
    render_admin_panel(auth, token, principal)
    case = render_case_panel(store, auth, token, principal)
except (CrossBotError, sqlite3.Error, OSError) as error:
    st.error(f"无法打开本地数据：{error}")
    st.stop()

if case is None:
    st.info("请先新建 Case，或导入台灯演示案例。")
    st.stop()

render_case_summary(store, case, auth, token)
options = []
if Permission.W1_GENERATE in ROLE_PERMISSIONS[principal.active_role]:
    options.append("W1 询盘极速转化")
if Permission.W2_GENERATE in ROLE_PERMISSIONS[principal.active_role]:
    options.append("W2 多平台Listing生成")
if not options:
    st.info("当前岗位暂无已接入的生成任务。")
    st.stop()
with st.sidebar:
    st.header("工作流选择")
    wf = st.radio("工作流", options)

workflow = "sales" if wf.startswith("W1") else "listing"
conversation = access.get_conversation(token, case.case_id, workflow)
request = None
locked = case.status.value in {"passed", "closed"}
prefix = f"{case.case_id}_{workflow}"
product = case.metadata.get("fixture", {}).get("product", {})
product_default = (
    f"{product.get('name', '')}，{product.get('material', '')}，USB供电，三档色温。"
    if product else ""
)

with st.form(f"request_{prefix}"):
    col1, col2 = st.columns(2)
    if workflow == "sales":
        with col1:
            inquiry = st.text_area("客户询盘", key=f"inquiry_{prefix}")
            lang = st.selectbox("目标语言", ["英文", "德文"], key=f"lang_{prefix}")
        with col2:
            product_info = st.text_area("中文产品信息", value=product_default, key=f"product_{prefix}")
        if st.form_submit_button("生成商务回复", disabled=locked):
            if not inquiry.strip() or not product_info.strip():
                st.error("请填写客户询盘和产品信息。")
            else:
                request = SalesRequest(inquiry, product_info, lang, customer_country=case.destination_country, context=RequestContext(case.case_id))
    else:
        with col1:
            description = st.text_area("中文产品描述", value=product_default, key=f"description_{prefix}")
        with col2:
            platforms = st.multiselect("目标平台", ["Amazon", "TikTok", "阿里国际站"], default=["Amazon", "TikTok"], key=f"platforms_{prefix}")
        if st.form_submit_button("生成 Listing", disabled=locked):
            if not description.strip() or not platforms:
                st.error("请填写产品描述并选择平台。")
            else:
                request = ListingRequest(description, platforms, target_country=case.destination_country, context=RequestContext(case.case_id))

for message in conversation.messages:
    with st.chat_message(message["role"]):
        if message.get("error"):
            st.error(message["content"])
        else:
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_risks(message.get("risks", []))

feedback = st.chat_input("输入修改意见", disabled=locked or not any(
    message["role"] == "assistant" and not message.get("error") for message in conversation.messages
), key=f"feedback_{prefix}")

if request is not None or feedback:
    if request is not None:
        try:
            request = GenerationService(auth).authorize_request(token, request)
        except CrossBotError as error:
            st.error(str(error))
            st.stop()
        data = to_dict(request)
        content = request.inquiry if isinstance(request, SalesRequest) else request.product_description
        conversation.messages.append({"role": "user", "content": content, "request": data})
        if isinstance(request, SalesRequest):
            generator = run_workflow_stream("sales", request.inquiry, f"{request.target_language}, {request.product_info}")
        else:
            generator = run_workflow_stream("listing", request.product_description, ",".join(request.platforms))
    else:
        auth.require_permission(token, Permission.W1_GENERATE if workflow == "sales" else Permission.W2_GENERATE, case.case_id)
        previous = next(message["content"] for message in reversed(conversation.messages)
                        if message["role"] == "assistant" and not message.get("error"))
        content = feedback
        conversation.messages.append({"role": "user", "content": feedback, "kind": "feedback"})
        generator = iterate_workflow_stream(previous, feedback)
    # Save before calling the provider. Reruns never replay requests automatically.
    save_or_stop(access, token, conversation)
    with st.chat_message("user"):
        st.markdown(content)
    full_text, risks = "", []
    with st.chat_message("assistant"):
        placeholder = st.empty()
        try:
            for chunk in generator:
                if isinstance(chunk, tuple) and chunk[0] == "###RISKS###":
                    risks = chunk[1]
                else:
                    full_text += chunk
                    placeholder.markdown(full_text + "▌")
            conversation.messages.append({"role": "assistant", "content": full_text, "risks": risks})
        except Exception:
            conversation.messages.append({"role": "assistant", "content": "生成失败，请检查模型服务后重新提交。", "error": True})
    save_or_stop(access, token, conversation)
    st.rerun()
