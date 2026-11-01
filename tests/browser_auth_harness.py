"""Manual browser QA harness; requires an explicitly supplied synthetic DB."""
import sys

import streamlit as st

from providers.settings_ui import load_user_provider_settings, render_provider_settings
from storage.sqlite_store import SQLiteStore
from ui.auth_panel import render_auth_panel

if len(sys.argv) < 2:
    raise RuntimeError("Pass an isolated synthetic database path; no production fallback")

st.set_page_config(page_title="Synthetic auth QA")
auth, token, principal = render_auth_panel(SQLiteStore(sys.argv[1]))

if "--injected-cipher" in sys.argv:
    from test_user_provider_settings import MemoryCipher

    @st.cache_resource
    def synthetic_cipher():
        return MemoryCipher()

    load_user_provider_settings(auth, token, principal, cipher=synthetic_cipher())
else:
    load_user_provider_settings(auth, token, principal)

render_provider_settings(mode="真实", show_test=False)
