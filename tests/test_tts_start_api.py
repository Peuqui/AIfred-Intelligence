"""TTS API: token guard, AIfred's list with the status from the service's catalog (read only, through a
fake service behind httpx.MockTransport)."""

import httpx
import pytest
from starlette.testclient import TestClient

from aifred.lib import tts_service
from aifred.lib.api.app import api_app
from aifred.lib.tts_client import TTSClient

TOKEN = "test-tts-control-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _entry(engine: str, host: str | None, **fields) -> dict:
    base = {
        "engine": engine, "host": host, "label": {"de": engine, "en": engine},
        "label_short": {"de": "Qwen3-TTS", "en": "Qwen3-TTS"},
        "status": "stopped", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {}, "idle_remaining_s": None,
        "default_vram_mib": None, "default_max_parallel": 1, "default_max_queue": 4,
    }
    return {**base, **fields}


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch):
    """A fake TTS service with three catalog rows and AIfred's list naming them; records every request
    (only reads are expected)."""
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    listed = [
        _entry("qwen3local", None),
        _entry("qwen3local", "Box"),
        _entry("edge", None, needs_gpu=False, label_short={"de": "Edge", "en": "Edge"}, cloud=True),
    ]
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert (request.method, request.url.path) == ("GET", "/engines"), "AIfred must only read the catalog"
        return httpx.Response(200, json=listed)

    client = TTSClient("http://tts.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
    monkeypatch.setattr(tts_service, "persisted_settings", lambda: {
        "tts_escalation": [
            {"engine": "qwen3local", "host": None, "enabled": True},
            {"engine": "qwen3local", "host": "Box", "enabled": True},
            {"engine": "edge", "host": None, "enabled": True},
        ],
        "tts_toggles_per_engine": {},
    })
    tts_service.reset_cache()
    return calls


def test_the_endpoint_needs_the_token(service) -> None:
    client = TestClient(api_app)
    assert client.get("/tts/entries", params={"lang": "de"}).status_code == 403
    assert client.get("/tts/entries", params={"lang": "de"}, headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert service == []


def test_missing_server_token_is_503(service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_CONTROL_API_TOKEN")
    assert TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH).status_code == 503


def test_the_list_comes_in_aifreds_order_with_status_and_only_reads(service) -> None:
    rows = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH).json()
    assert [(row["engine"], row["host"]) for row in rows] == [
        ("qwen3local", None), ("qwen3local", "Box"), ("edge", None),
    ]
    assert rows[0]["label"] == "Qwen3-TTS · lokal"
    assert rows[1]["label"] == "Qwen3-TTS · Box"
    assert rows[0]["status"] == "sleeping" and rows[0]["status_text"] == "schläft — startet bei Bedarf"
    assert rows[2]["status"] == "no_access"
    assert set(service) == {("GET", "/engines")}


def test_there_is_no_start_or_stop_endpoint() -> None:
    paths = {getattr(route, "path", "") for route in api_app.routes}
    assert "/tts/entries" in paths
    assert not paths & {"/tts/start", "/tts/stop"}


def test_an_unreachable_service_is_503_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    response = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH)
    assert response.status_code == 503 and "not reachable" in response.json()["detail"]
