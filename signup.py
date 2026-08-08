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

# Writing user_metadata needs the service role but NOT the invite code, so it is
# available whenever the hosted-app auth env is configured (see set_user_metadata).
ADMIN_ENABLED = bool(_SERVICE_ROLE_KEY and _SUPABASE_URL)


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


def set_user_metadata(user_id: str, patch: dict, existing: dict = None) -> bool:
    """MERGE `patch` into a user's Supabase user_metadata via the Admin API.

    Used for server-derived onboarding facts the browser can't compute (e.g. the
    star-derived genre offsets built at import-commit). Metadata is the right home
    for these — `auth.get_current_user_metadata` already threads it into every
    request, so no books.db schema change is needed for a handful of floats.

    The Admin API's handling of a partial `user_metadata` has varied across
    Supabase versions (merge vs replace), so pass `existing` — the caller's current
    metadata, which every authenticated handler already has — and the merge is done
    HERE. That way a replace-style upstream cannot silently drop `onboarded` or
    `fav_genres`.

    Best-effort by design: returns True on success, False on any failure. Callers
    treat this as an optional enrichment and must never fail a user's request
    because it didn't stick.
    """
    if not ADMIN_ENABLED or not user_id or not patch:
        return False
    merged = dict(existing or {})
    merged.update(patch)
    url = f"{_SUPABASE_URL}/auth/v1/admin/users/{user_id}"
    payload = json.dumps({"user_metadata": merged}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Content-Type", "application/json")
    req.add_header("apikey", _SERVICE_ROLE_KEY)
    req.add_header("Authorization", f"Bearer {_SERVICE_ROLE_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def _friendly(code: int, detail: str) -> str:
    d = detail.lower()
    if code == 422 or any(w in d for w in ("already", "registered", "exists")):
        return "That email is already registered — try signing in instead."
    if "password" in d:
        return "Password is too weak (use at least 6 characters)."
    if "email" in d and "valid" in d:
        return "That doesn't look like a valid email address."
    return "Could not create the account. Please try again."
