"""Cookie transport only; AuthService remains the authority for every session."""
from __future__ import annotations

import secrets
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from core.errors import CrossBotError, ErrorCode

COOKIE_NAME = "crossbot_session_v1"
SESSION_SECONDS = 8 * 3600


def cookie_name(auth, app_url, base_path=""):
    location = urlsplit(app_url or "http://localhost")
    namespace = "|".join((str(Path(auth.store.path).resolve()).casefold(),
                          location.scheme.lower(), location.netloc.lower(), base_path))
    return COOKIE_NAME + "_" + hashlib.sha256(namespace.encode()).hexdigest()[:20]


def app_cookie_name(auth):
    import streamlit as st
    url = getattr(st.context, "url", None) or f"http://localhost:{st.get_option('server.port')}"
    return cookie_name(auth, url, st.get_option("server.baseUrlPath"))

COOKIE_JS = r"""
export default function(component) {
    const {data, setStateValue} = component;
    const read = () => {
        const prefix = data.name + '=';
        const item = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(prefix));
        return item ? decodeURIComponent(item.slice(prefix.length)) : '';
    };
    let ok = true;
    let cookie = '';
    try {
        const secure = location.protocol === 'https:' ? '; Secure' : '';
        if (location.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) {
            throw new Error('HTTPS required');
        }
        const attributes = '; Path=/; SameSite=Strict' + secure;
        if (data.action === 'set') {
            const remaining = Math.max(0, Math.floor(data.expires_at - Date.now() / 1000));
            document.cookie = data.name + '=' + encodeURIComponent(data.token) + '; Max-Age=' + remaining + attributes;
        } else if (data.action === 'clear') {
            document.cookie = data.name + '=; Max-Age=0' + attributes;
        }
        cookie = read();
        ok = data.action === 'set' ? cookie === data.token : data.action !== 'clear' || cookie === '';
    } catch (_) {
        ok = false;
    }
    setStateValue('result', {request_id: data.request_id, cookie, ok});
}
"""


def queue_cookie(token, expires_at, *, name=COOKIE_NAME):
    import streamlit as st
    st.session_state["_browser_cookie_command"] = {
        "action": "set", "token": token, "expires_at": expires_at,
        "request_id": secrets.token_hex(16), "name": name,
    }


def queue_cookie_clear(*, name=COOKIE_NAME):
    import streamlit as st
    st.session_state["_browser_cookie_command"] = {
        "action": "clear", "request_id": secrets.token_hex(16), "name": name,
    }


def session_expiry(auth, token):
    with auth.repository.transaction() as connection:
        auth._principal(connection, token)
        return connection.execute("SELECT expires_at FROM auth_sessions WHERE token_hash=?", (auth._token_hash(token),)).fetchone()[0]


def validated_cookie(auth, cookie):
    if not isinstance(cookie, str) or not 20 <= len(cookie) <= 256:
        raise CrossBotError(ErrorCode.UNAUTHENTICATED, "登录已失效，请重新登录")
    return auth.require_session(cookie)


def browser_cookie(*, name=COOKIE_NAME):
    """Return only a transport candidate, never an authenticated identity."""
    import streamlit as st
    try:
        from streamlit.components.v2 import component
    except ImportError:
        st.warning("刷新恢复需要 Streamlit 1.51 或更新版本")
        return None
    command = st.session_state.setdefault("_browser_cookie_command", {
        "action": "read", "request_id": secrets.token_hex(16), "name": name,
    })
    first_read = not st.session_state.get("_browser_header_checked", False)
    st.session_state["_browser_header_checked"] = True
    bridge = component("crossbot_browser_session", js=COOKIE_JS)
    result = bridge(data=command, key="_browser_cookie_bridge", default={"result": None},
                    on_result_change=lambda: None, height=0).result
    if result and result.get("request_id") == command["request_id"]:
        if not result.get("ok"):
            st.warning("浏览器未允许安全登录 cookie；请使用 HTTPS 或 localhost，并允许 cookie。")
        if command["action"] != "read":
            st.session_state["_browser_cookie_command"] = {
                "action": "read", "request_id": secrets.token_hex(16), "name": name,
            }
            return None
        return result.get("cookie") if result.get("ok") else None
    if command["action"] == "read" and first_read:
        return st.context.cookies.get(name)
    return None
