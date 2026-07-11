"""
signup.py — invite-code-gated account creation for the hosted multi-user app.
=============================================================================
The shared invite code and the Supabase SERVICE-ROLE key live ONLY here (server
env), never in the browser bundle — so the gate cannot be bypassed by calling
Supabase's public sign-up directly. Accounts are minted through the Supabase
Admin API with the email PRE-CONFIRMED (email confirmation is intentionally off
for the trusted-invite flow), so a new user can sign up → sign in → add books
immediately.

Deployment requirements (all server-side env on the backend):
  SIGNUP_INVITE_CODE          the one shared code you hand out.
  SUPABASE_SERVICE_ROLE_KEY   Supabase → Settings → API → service_role (secret!).
  SUPABASE_URL                the project URL (already set for auth.py).
And in the Supabase dashboard: DISABLE public sign-ups (Authentication → Sign In
/ Providers → Email → "Allow new users to sign up" OFF), so this backend path is
the only way to create an account.

Stdlib-only (urllib) so it never depends on httpx/requests being importable.
"""
import hmac
import json
import os
import urllib.error
import urllib.request

_INVITE_CODE = os.environ.get("SIGNUP_INVITE_CODE", "").strip()
_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")

# Sign-up is only available when all three are configured (i.e. the hosted app).
# Local dev / the static build leave these unset → the endpoint 404s.
SIGNUP_ENABLED = bool(_INVITE_CODE and _SERVICE_ROLE_KEY and _SUPABASE_URL)


class SignupError(Exception):
    """A user-facing sign-up failure (bad email/password, duplicate, upstream)."""


def check_invite_code(code: str) -> bool:
    """Constant-time compare against the server's shared invite code."""
    if not _INVITE_CODE:
        return False
    return hmac.compare_digest((code or "").strip(), _INVITE_CODE)


def create_user(email: str, password: str) -> dict:
    """Create a PRE-CONFIRMED Supabase user via the Admin API. Returns the created
    user object; raises SignupError with a friendly message on any failure."""
    url = f"{_SUPABASE_URL}/auth/v1/admin/users"
    payload = json.dumps({
        "email": email,
        "password": password,
        "email_confirm": True,  # mark confirmed → no email step, usable at once
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("apikey", _SERVICE_ROLE_KEY)
    req.add_header("Authorization", f"Bearer {_SERVICE_ROLE_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise SignupError(_friendly(exc.code, exc.read().decode(errors="replace")))
    except urllib.error.URLError as exc:
        raise SignupError(f"Could not reach the auth server: {exc.reason}")


def _friendly(code: int, detail: str) -> str:
    d = detail.lower()
    if code == 422 or any(w in d for w in ("already", "registered", "exists")):
        return "That email is already registered — try signing in instead."
    if "password" in d:
        return "Password is too weak (use at least 6 characters)."
    if "email" in d and "valid" in d:
        return "That doesn't look like a valid email address."
    return "Could not create the account. Please try again."
