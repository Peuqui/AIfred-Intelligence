"""A refresh token the provider rejects is reported as an error that must not
be retried; nothing is disconnected or switched off."""

import asyncio
import time

import httpx
import pytest

from aifred.lib.oauth import broker as broker_module
from aifred.lib.oauth.broker import OAuthBroker, OAuthGrantRevoked, OAuthProvider, TokenSet


class _FakeProvider(OAuthProvider):
    def __init__(self, status: int | None) -> None:
        self.status = status  # None = refresh succeeds

    @property
    def name(self) -> str:
        return "fake"

    def get_auth_url(self, scopes: list[str], redirect_uri: str, state: str) -> str:
        return ""

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        raise NotImplementedError

    async def refresh(self, token_set: TokenSet) -> TokenSet:
        if self.status is not None:
            request = httpx.Request("POST", "https://oauth.example/token")
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("refresh failed", request=request, response=response)
        return TokenSet("fresh", token_set.refresh_token, time.time() + 3600, token_set.scopes)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_module, "_KEY_FILE", tmp_path / "key.bin")
    monkeypatch.setattr(broker_module, "_TOKENS_FILE", tmp_path / "tokens.json")


def _broker_with_expired_token(status: int | None) -> OAuthBroker:
    broker = OAuthBroker()
    broker.register(_FakeProvider(status))
    broker_module._save_token("fake", TokenSet("old", "refresh", time.time() - 10, []))
    return broker


@pytest.mark.parametrize("status", [400, 401])
def test_rejected_refresh_is_reported_but_keeps_the_plugin(storage, status):
    broker = _broker_with_expired_token(status)
    with pytest.raises(OAuthGrantRevoked, match="Do not retry"):
        asyncio.run(broker.get_token("fake"))
    assert broker.is_connected("fake") is True


@pytest.mark.parametrize("status", [429, 503])
def test_server_failure_keeps_connection(storage, status):
    broker = _broker_with_expired_token(status)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(broker.get_token("fake"))
    assert broker.is_connected("fake") is True


def test_successful_refresh_stores_token(storage):
    broker = _broker_with_expired_token(None)
    assert asyncio.run(broker.get_token("fake")) == "fresh"
    stored = broker_module._load_token("fake")
    assert stored is not None and stored.access_token == "fresh"


def test_verify_connection_rejected_reports_invalid(storage):
    broker = _broker_with_expired_token(400)
    assert asyncio.run(broker.verify_connection("fake")) is False
    assert broker.is_connected("fake") is True


def test_verify_connection_server_failure_propagates(storage):
    broker = _broker_with_expired_token(503)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(broker.verify_connection("fake"))
    assert broker.is_connected("fake") is True
