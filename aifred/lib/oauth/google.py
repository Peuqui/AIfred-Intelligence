"""Google OAuth 2.0 provider.

Scopes are passed by callers (e.g. Calendar plugin passes calendar scope,
Contacts plugin passes contacts scope).  The broker aggregates nothing —
the caller is responsible for requesting all needed scopes in one go so
Google issues a single refresh token that covers everything.

Recommended combined scope list for all Google plugins::

    GoogleProvider.SCOPES_CALENDAR
    + GoogleProvider.SCOPES_CONTACTS
    + GoogleProvider.SCOPES_DRIVE       (optional)
    + GoogleProvider.SCOPES_TASKS       (optional)

Client credentials must be set in .env::

    GOOGLE_CLIENT_ID=<your client id>
    GOOGLE_CLIENT_SECRET=<your client secret>

Obtain them from Google Cloud Console → APIs & Services → Credentials
→ Create OAuth 2.0 Client ID (type: Web application).
Register all redirect URIs you will use (e.g. narnia URL + local URL).
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

import httpx

from ..credential_broker import broker
from .broker import OAuthProvider, TokenSet

# Scope constants for convenient use by plugin callers
SCOPES_CALENDAR = [
    "https://www.googleapis.com/auth/calendar",
]
SCOPES_CONTACTS = [
    "https://www.googleapis.com/auth/contacts",
]
SCOPES_DRIVE_READONLY = [
    "https://www.googleapis.com/auth/drive.readonly",
]
SCOPES_TASKS = [
    "https://www.googleapis.com/auth/tasks",
]
SCOPES_PROFILE = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


class GoogleProvider(OAuthProvider):
    name = "google"

    _AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"

    @property
    def _client_id(self) -> str:
        return broker.get("google", "client_id")

    @property
    def _client_secret(self) -> str:
        return broker.get("google", "client_secret")

    def get_auth_url(self, scopes: list[str], redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",  # request refresh_token
            "prompt": "consent",       # force refresh_token even if previously granted
        }
        return f"{self._AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
        return _parse_token_response(resp.json())

    async def refresh(self, token_set: TokenSet) -> TokenSet:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._TOKEN_URL,
                data={
                    "refresh_token": token_set.refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
        refreshed = _parse_token_response(resp.json())
        # Google only returns a new refresh_token when rotating tokens is enabled.
        # Keep the original one when not present in the response.
        if not refreshed.refresh_token:
            refreshed = TokenSet(
                access_token=refreshed.access_token,
                refresh_token=token_set.refresh_token,
                expiry=refreshed.expiry,
                scopes=refreshed.scopes or token_set.scopes,
                token_type=refreshed.token_type,
            )
        return refreshed


def _parse_token_response(data: dict[str, Any]) -> TokenSet:
    expires_in = int(data.get("expires_in", 3600))
    scope_str = data.get("scope", "")
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expiry=time.time() + expires_in,
        scopes=scope_str.split() if scope_str else [],
        token_type=data.get("token_type", "Bearer"),
    )
