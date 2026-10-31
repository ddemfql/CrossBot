import streamlit as st

from workflows.sales_ui import render_sales
from workflows.listing_ui import render_listing
from providers.settings_ui import render_provider_settings
from workflows.export_ui import render_downloads

st.set_page_config(page_title="CrossBot", layout="wide")
st.title("CrossBot 外贸智能业务员与合规助手")
st.caption("W1/W2支持明确模拟或真实模式；知识资料仍为自建模拟参考，不是完整合规审核。")

with st.sidebar:
    st.header("工作流选择")
    workflow = st.radio("工作流", ["W1 询盘极速转化", "W2 多平台Listing生成"], label_visibility="collapsed")
    config, ready = render_provider_settings()

if workflow.startswith("W1"):
    render_sales(config, ready)
else:
    render_listing(config, ready)

render_downloads()
