"""Isolated component harness; full app auth/Case integration is tested separately."""
import streamlit as st

from providers.settings_ui import render_provider_settings
from workflows.export_ui import render_downloads
from workflows.listing_ui import render_listing
from workflows.sales_ui import render_sales

st.set_page_config(page_title="CrossBot workflow component test", layout="wide")
with st.sidebar:
    workflow = st.radio("工作流", ["W1 询盘极速转化", "W2 多平台Listing生成"], key="test_workflow")
    config, ready = render_provider_settings()
if workflow.startswith("W1"):
    render_sales(config, ready)
else:
    render_listing(config, ready)
render_downloads()
