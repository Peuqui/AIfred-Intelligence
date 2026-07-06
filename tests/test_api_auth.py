"""App-level API auth: expiring signed cookie + /api middleware gate.

The login cookie carries its expiry INSIDE the HMAC payload
(``username.<expires>.<mac>``), and every /api route except the
token-guarded control endpoints (inject/webhook) and the OAuth callback
requires a valid cookie — defense in depth below nginx basic-auth.
"""

import hmac
import time
from hashlib import sha256

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from aifred.lib import auth
from aifred.lib.auth import sign_username, verify_signed_username
from aifred.lib.browser_storage import USERNAME_COOKIE_NAME


def _forge(payload: str) -> str:
    """Correctly signed cookie for an arbitrary payload (white-box helper)."""
    mac = hmac.new(auth._session_secret(), payload.encode(), sha256).hexdigest()
    return f"{payload}.{mac}"


# ── Cookie signing / verification ─────────────────────────────

class TestExpiringSignedCookie:
    def test_roundtrip_valid(self):
        assert verify_signed_username(sign_username("ki")) == "ki"

    def test_username_with_dots_survives(self):
        # rpartition-based parsing: dots in the name must not break it
        value = _forge(f"lord.helmchen.{int(time.time()) + 3600}")
        assert verify_signed_username(value) == "lord.helmchen"

    def test_expired_cookie_rejected(self):
        value = _forge(f"ki.{int(time.time()) - 1}")
        assert verify_signed_username(value) is None

    def test_tampered_expiry_rejected(self):
        # Extending the lifetime without re-signing must fail
        username, expires, mac = sign_username("ki").split(".")
        assert verify_signed_username(f"{username}.{int(expires) + 999999}.{mac}") is None

    def test_forged_mac_rejected(self):
        assert verify_signed_username(f"ki.{int(time.time()) + 3600}.deadbeef") is None

    def test_legacy_format_without_expiry_rejected(self):
        # Pre-expiry cookies (username.<mac>) are invalid — user logs in once
        legacy_mac = hmac.new(auth._session_secret(), b"ki", sha256).hexdigest()
        assert verify_signed_username(f"ki.{legacy_mac}") is None

    def test_garbage_rejected(self):
        for value in ("", "ki", "ki.", ".", "a.b.c", f"ki.notanumber.{'0' * 64}"):
            assert verify_signed_username(value) is None


# ── /api middleware gate ──────────────────────────────────────

@pytest.fixture(scope="module")
def client() -> TestClient:
    from aifred.lib.api import api_app

    app = Starlette()
    app.mount("/api", api_app)
    return TestClient(app)


class TestApiCookieGate:
    def test_rejects_without_cookie(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Login cookie required"

    def test_rejects_expired_cookie(self, client):
        client.cookies.set(USERNAME_COOKIE_NAME, _forge(f"ki.{int(time.time()) - 1}"))
        resp = client.get("/api/health")
        assert resp.status_code == 403
        client.cookies.clear()

    def test_allows_with_valid_cookie(self, client):
        client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        client.cookies.clear()

    def test_inject_exempt_enforces_own_token(self, client, monkeypatch):
        # No cookie: the request must PASS the middleware and be answered by
        # require_service_token (503 = token not configured) — not by the
        # cookie gate (403 "Login cookie required").
        monkeypatch.setenv("INJECT_API_TOKEN", "")
        resp = client.post(
            "/api/chat/inject",
            json={"session_id": "x", "message": "y", "token": "z"},
        )
        assert resp.status_code == 503

    def test_oauth_callback_exempt_from_cookie(self, client):
        # No cookie: must reach the handler (422 = missing query params),
        # not the cookie gate.
        resp = client.get("/api/oauth/google/callback")
        assert resp.status_code != 403
