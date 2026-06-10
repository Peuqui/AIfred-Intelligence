"""Central authentication primitives — single source of truth.

Two concerns live here so they cannot drift apart across call sites:

1. Service-token checks for the HTTP control endpoints (inject, webhook).
   Always constant-time, always fail-closed.
2. Integrity-protected username cookies for the web auto-login. The cookie
   is HMAC-signed with a server-side secret so a client cannot forge it to
   impersonate another account (the cookie sits behind the reverse proxy's
   basic-auth, but the signature is the per-account boundary underneath it).

The server secret is read from AIFRED_SESSION_SECRET; if unset, a random
secret is generated once and persisted to ``data/.session_secret`` (0600).
Regenerating the secret invalidates existing cookies — users simply log in
again once; nothing breaks.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from hashlib import sha256

from .config import DATA_DIR
from .credential_broker import broker

logger = logging.getLogger(__name__)

_SECRET_FILE = DATA_DIR / ".session_secret"
_secret_cache: bytes | None = None


# ============================================================
# SERVER SECRET
# ============================================================


def _session_secret() -> bytes:
    """Return the stable server secret used to sign cookies.

    Order: AIFRED_SESSION_SECRET env → persisted file → freshly generated +
    persisted. Cached after first read.
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache

    env_value = broker.get("auth", "session_secret")
    if env_value:
        _secret_cache = env_value.encode()
        return _secret_cache

    if _SECRET_FILE.exists():
        _secret_cache = _SECRET_FILE.read_bytes().strip()
        if _secret_cache:
            return _secret_cache

    # Generate + persist a new secret (0600). Existing cookies become invalid.
    new_secret = secrets.token_hex(32).encode()
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SECRET_FILE.write_bytes(new_secret)
    os.chmod(_SECRET_FILE, 0o600)
    logger.warning(
        "Generated new session secret at %s — existing login cookies are now "
        "invalid, users will be asked to log in once.", _SECRET_FILE
    )
    _secret_cache = new_secret
    return _secret_cache


# ============================================================
# SERVICE TOKEN (inject / webhook control endpoints)
# ============================================================


def require_service_token(service: str, provided: str | None) -> None:
    """Validate an API token for a control endpoint, constant-time, fail-closed.

    Raises an HTTPException (503 if the token is not configured, 403 if it is
    missing or wrong). ``service`` is the credential-broker service name
    (e.g. "inject", "webhook").
    """
    from fastapi import HTTPException

    expected = broker.get(service, "api_token")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{service} API not configured ({service.upper()}_API_TOKEN not set)",
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


# ============================================================
# SIGNED USERNAME COOKIE (web auto-login)
# ============================================================


def sign_username(username: str) -> str:
    """Return ``username.<hmac>`` — an integrity-protected cookie value."""
    mac = hmac.new(_session_secret(), username.encode(), sha256).hexdigest()
    return f"{username}.{mac}"


def verify_signed_username(value: str) -> str | None:
    """Verify a signed username cookie. Return the username, or None if invalid.

    Returns None (caller falls back to the login dialog) on any mismatch — no
    exception, because a malformed/forged cookie is an expected, non-fatal case.
    """
    if not value or "." not in value:
        return None
    username, _, mac = value.rpartition(".")
    if not username or not mac:
        return None
    expected = hmac.new(_session_secret(), username.encode(), sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    return username
