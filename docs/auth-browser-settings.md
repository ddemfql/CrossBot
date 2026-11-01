# Browser Login and Per-User Provider Settings

The login panel uses the built-in Streamlit components v2 JavaScript bridge.
Streamlit 1.51 or newer is required; the inspected local installation is 1.66.0.
No cookie extension package is required.

## Integration

After `render_auth_panel` returns, and before rendering provider widgets, call:

```python
from providers.settings_ui import load_user_provider_settings
load_user_provider_settings(auth, token, principal)
```

Call this on every Streamlit run. It validates the server session, loads saved
settings once per database/user/session, and clears provider state on a user
change or rejected identity. `render_provider_settings` keeps its existing
signature and handles saving, forgetting, and the optional remember-key choice.
Restoring a key never restores the consent to send business data.

`UserSettingsRepository(auth, cipher=...)` accepts an injectable cipher with
`encrypt(bytes, entropy) -> bytes` and `decrypt(bytes, entropy) -> bytes`.
Production defaults to Windows CurrentUser DPAPI; the injected cipher is for
synthetic tests. Repository operations verify the token and principal in the
same transaction and access only the current application user row.

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

## Encryption and Operational Boundaries

The private repository lazily creates only `user_provider_settings`, without
changing the storage schema version or public tables. The entire settings
payload is encrypted, with the application user ID bound as DPAPI entropy.
The database contains no plaintext key. An unchecked remember option excludes
the API key before encryption. Unchecking remember, clearing a key, changing
the endpoint, or deleting saved settings removes the persisted row.

CurrentUser DPAPI is scoped to the Windows account running the server, not to
a separate Windows identity for each application login. Application-user
isolation comes from authenticated repository access plus user-bound entropy.
The service account must have its Windows user profile loaded. Moving the
database to another Windows account or losing the profile/master keys can make
saved settings unrecoverable. There is no LocalMachine or plaintext fallback.
Encryption failures leave prior saved data intact and report a generic message
plus a Windows error code, never the key.

The CodexSandboxOffline test identity returned WinError 2 for CryptProtectData.
The independent .NET ProtectedData(CurrentUser) API also failed and reported
that the user profile was not loaded. Deployment acceptance therefore requires
an encrypt/decrypt round trip and remember/refresh test under the actual Windows
service account with its profile loaded; injected-cipher tests do not prove this.

Settings and tokens remain outside business export models and audit payloads.
There is no settings audit payload, plaintext export helper, or environment-key
fallback. Tests use synthetic databases and keys and never load .env. The
application must not serialize arbitrary session state into logs or exports.

## Focused Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_user_provider_settings.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_browser_session.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_auth_provider_ui.py -v
```

The DPAPI smoke test explicitly skips when Windows reports that the sandbox
profile is inaccessible. That skip is a deployment validation gap, not success.

Run this non-skipping deployment gate from the repository root under the real
Windows server account before accepting remembered-key persistence:

```powershell
.\.venv\Scripts\python.exe -B tests/check_windows_dpapi.py
```

The isolated Edge browser checks covered cookie set/read on refresh, actual
logout deletion, revoked-cookie replay rejection and tampered-cookie rejection.
The synthetic browser harness also checked settings refresh and reset consent
using an explicitly injected test cipher. This does not validate production
DPAPI encryption under the sandbox identity.
