# Browser Login and Session-Only Provider Settings

The login panel uses the built-in Streamlit components v2 JavaScript bridge.
Streamlit 1.51 or newer is required; the inspected local installation is 1.66.0.
No cookie extension package is required.

## Integration

After `render_auth_panel` returns, and before rendering provider widgets, call:

```python
from providers.settings_ui import initialize_provider_session
initialize_provider_session(auth, token, principal)
```

Call this on every Streamlit run. It validates the server session and initializes
in-memory provider values once per database/user/session. A rejected identity or
user change clears the provider values. It never reads configuration from disk
or from the database. `render_provider_settings` has no save or remember action.

The main app calls `ui.workbench.retain_widget_state()` before routing, so URL,
model, protocol, key and confirmation survive navigation between work pages.
A full browser refresh creates new Streamlit state and resets these values.
Restoring the login cookie does not restore the key or the send-data consent.

## Session Boundaries

The cookie carries the existing opaque AuthService session token. Every
restoration and subsequent authenticated render validates the server-side
session, account status, and role authorization. The cookie never establishes
identity on its own. Expired, unknown, revoked, disabled-account and revoked-role
sessions are rejected. Restoration never extends the original eight-hour expiry.
No token is added to URL query parameters or browser local/session storage.

Cookie names include a hash of the absolute database path, browser origin
(including port), and configured application base path. Different test databases
and development ports therefore do not overwrite each other cookies. Cookies
still belong to their host, not to a port; namespacing avoids collisions but is
not a security boundary against another malicious application on the same host.

Cookies use SameSite=Strict and Path=/, and Secure on HTTPS. Non-HTTPS remote
origins are refused by the bridge; HTTP localhost, 127.0.0.1 and IPv6 loopback are
allowed for local development. The JavaScript bridge checks set/clear results.

**HttpOnly is false.** A JavaScript component cannot issue an HttpOnly cookie.
The token is readable by same-origin JavaScript, so XSS and untrusted components
are material risks. An HttpOnly deployment needs an actual server/auth proxy
capable of issuing Set-Cookie; the Streamlit bridge does not claim that support.
The client bridge cannot set a Secure cookie on plain HTTP.

Logout first revokes the server token, clears session state, then queues cookie
deletion. Even if JavaScript is blocked or the tab closes before deletion, the
remaining cookie cannot authenticate because its server session is revoked.
The initial websocket cookie header is consulted once; a stale header cannot
undo a completed logout.

## Configuration Boundaries

Provider persistence and its Windows DPAPI implementation have been removed.
The app neither creates nor reads, decrypts, updates or deletes the historical
`user_provider_settings` table. Existing encrypted rows are left untouched.
No database migration, account change or customer-data cleanup is needed.

The model configuration lives only in the current Streamlit server session.
Clearing the key or changing the endpoint clears the key and send-data consent.
Changing model or protocol resets consent. Logout and account changes clear all
provider values. A browser refresh or a new tab needs a new key entry, even when
the valid login cookie restores the user's identity.

Settings and tokens remain outside business export models and audit payloads.
There is no settings audit payload, plaintext export helper, or environment-key
fallback. Tests use synthetic databases and keys and never load .env. The
application must not serialize arbitrary session state into logs or exports.

## Focused Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_browser_session.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_auth_provider_ui.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_provider_ui.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_workbench_ui.py -v
```

The UI regression checks cover missing save/remember controls, fresh-session
configuration reset, account isolation, clearing the key, revoked login, ignored
legacy ciphertext, no new settings table, and secret-free business exports.
Workbench tests cover configuration retention across page navigation. All model
calls in automated checks use synthetic responses; they do not consume real API
quota. The browser harness requires an explicitly supplied synthetic database.
