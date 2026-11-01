"""Launch the existing standalone ZIP workbench for the tester guide."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from w3_app import render

st.set_page_config(page_title="CrossBot · ZIP 单证测试", layout="wide")
render()