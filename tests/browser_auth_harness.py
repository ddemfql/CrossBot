"""Manual browser QA harness; requires an explicitly supplied synthetic DB."""
import sys

import streamlit as st

from providers.settings_ui import initialize_provider_session, render_provider_settings
from storage.sqlite_store import SQLiteStore
from ui.auth_panel import render_auth_panel

if len(sys.argv) < 2:
    raise RuntimeError("Pass an isolated synthetic database path; no production fallback")

st.set_page_config(page_title="Synthetic auth QA")
auth, token, principal = render_auth_panel(SQLiteStore(sys.argv[1]))

initialize_provider_session(auth, token, principal)
render_provider_settings(mode="真实", show_test=False)
