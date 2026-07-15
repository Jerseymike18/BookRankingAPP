"""
auth.py — Supabase JWT verification for the FastAPI backend (Phase 3).
======================================================================
One FastAPI dependency, get_current_user_id(), that derives the tenant key from
the *verified* Supabase access token — NEVER from the request body or a query
param (that would let any caller impersonate any tenant).

Modes (env-driven and auditable, like ALLOWED_ORIGIN / BIND_HOST):

  AUTH_ENABLED unset / "0"  -> local single-user dev. No token required; returns
                               db_backend.DEFAULT_USER_ID (Michael). The local
                               SQLite app keeps working exactly as before.
  AUTH_ENABLED = "1"        -> hosted multi-user. Every request MUST carry a
                               valid Supabase Bearer token; the tenant key is its
                               `sub` claim. Missing / invalid / expired -> 401.

Signature verification (whichever the project uses):
  * SUPABASE_JWT_SECRET set -> HS256 (Supabase legacy shared-secret JWT).
  * else SUPABASE_URL set    -> asymmetric (RS256/ES256) via the project JWKS
                                endpoint /auth/v1/.well-known/jwks.json.
Supabase access tokens carry aud="authenticated"; we require it.

This module is imported by backend/main.py; it never touches predict_engine or
db_write's tenancy logic — it only produces the user_id those layers already take.
"""
import os

from fastapi import Header, HTTPException
# `jwt` (PyJWT) + cryptography are imported lazily inside _verify() so that merely
# importing this module (and thus backend/main.py) never requires them — the
# snapshot export runs on a Python that only has the engine deps, not the auth deps.

import db_backend

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "0").strip() == "1"
_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_AUDIENCE = "authenticated"

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def _verify(token):
    """Decode+verify a Supabase access token; return its claims or raise."""
    import jwt
    if _JWT_SECRET:
        return jwt.decode(token, _JWT_SECRET, algorithms=["HS256"],
                          audience=_AUDIENCE)
    if _SUPABASE_URL:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(token, signing_key, algorithms=["RS256", "ES256"],
                          audience=_AUDIENCE)
    raise RuntimeError(
        "AUTH_ENABLED=1 but neither SUPABASE_JWT_SECRET nor SUPABASE_URL is set.")


def get_current_user_id(authorization: str = Header(default=None)) -> str:
    """FastAPI dependency → the tenant key.

    Local (AUTH_ENABLED off): the single-user default. Hosted: the verified
    token's subject. Derived ONLY from the Authorization header, never the body.
    """
    if not AUTH_ENABLED:
        return db_backend.DEFAULT_USER_ID
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _verify(token)
    except RuntimeError as exc:              # server misconfiguration
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception:                        # bad signature / expired / malformed
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token has no subject (sub).")
    return sub


def get_current_user_metadata(authorization: str = Header(default=None)) -> dict:
    """The verified token's `user_metadata` (non-sensitive per-user preferences such
    as the onboarding word-count preference), or {} locally / on any error. The tenant
    key still comes ONLY from get_current_user_id — this never carries identity, and a
    bad/absent token simply yields {} (no preference) rather than raising."""
    if not AUTH_ENABLED:
        return {}
    if not authorization or not authorization.lower().startswith("bearer "):
        return {}
    try:
        claims = _verify(authorization.split(" ", 1)[1].strip())
    except Exception:
        return {}
    md = claims.get("user_metadata")
    return md if isinstance(md, dict) else {}
