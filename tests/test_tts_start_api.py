"""TTS API: token guard, AIfred's list with the status from the service's catalog (read only, through a
fake service behind httpx.MockTransport), plus the old placement rules that the speech paths still use."""

import httpx
import pytest
from starlette.testclient import TestClient

import aifred.lib.tts_service as tts_service
from aifred.lib import tts_escalation
from aifred.lib.api.app import api_app
from aifred.lib.tts_client import TTSClient
from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_escalation import GPUPlacement

TOKEN = "test-tts-control-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _entry(engine: str, host: str | None, **fields) -> dict:
    base = {
        "engine": engine, "host": host, "label": {"de": engine, "en": engine},
        "label_short": {"de": "Qwen3-TTS", "en": "Qwen3-TTS"},
        "status": "stopped", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {},
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
        "tts_hosts": [{"name": "Box", "enabled": True}],
        "tts_toggles_per_engine": {},
    })
    tts_service.reset_cache()
    return calls


def test_the_service_dir_is_the_container_name() -> None:
    """The API names local containers by service_dir — every compose file must agree."""
    import re

    for engine in TTS_ENGINES.values():
        if engine.runs_in_container:
            compose = engine.docker_compose_path
            assert compose is not None, engine.key
            assert re.search(rf"container_name:\s*{re.escape(engine.service_dir)}\s*$", compose.read_text(), re.M), engine.key


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


def test_a_switched_off_host_shows_as_host_off(service, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tts_service.persisted_settings()
    settings["tts_hosts"] = [{"name": "Box", "enabled": False}]
    monkeypatch.setattr(tts_service, "persisted_settings", lambda: settings)
    rows = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH).json()
    assert rows[1]["status"] == "host_off" and rows[1]["status_text"] == "Rechner aus"


def test_there_is_no_start_or_stop_endpoint() -> None:
    paths = {getattr(route, "path", "") for route in api_app.routes}
    assert "/tts/entries" in paths
    assert not paths & {"/tts/start", "/tts/stop"}


def test_an_unreachable_service_is_503_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    response = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH)
    assert response.status_code == 503 and "not reachable" in response.json()["detail"]


def test_local_starts_run_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each local start sees the memory the one before took: no overlap between
    choosing a card and the model being loaded; a second start of the same
    engine finds it running and starts nothing."""
    import asyncio
    import threading
    import time

    from aifred.lib.tts_escalation import find_entry, launch_entry

    aifred: list[tuple] = []
    monkeypatch.setattr(TTS_ENGINES["qwen3local"], "is_installed", lambda: True)
    inside = threading.Semaphore(1)
    overlaps: list[bool] = []

    async def place(engine):
        placed = engine.on_gpu(f"GPU-for-{engine.key}")

        def ready():
            overlaps.append(not inside.acquire(blocking=False))
            time.sleep(0.05)
            inside.release()
            aifred.append(("start", placed.gpu_uuid))
            return True, "ready", "cuda:0"

        monkeypatch.setattr(placed, "ensure_ready", ready)
        return GPUPlacement(placed, 0, 1, 1)

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", place)
    monkeypatch.setattr(
        tts_escalation, "escalation_entries",
        lambda settings=None: [tts_escalation.EscalationEntry(TTS_ENGINES["qwen3local"], None, True)],
    )
    entry = find_entry("qwen3local", None)

    async def both():
        await asyncio.gather(launch_entry(entry, lambda line: None), launch_entry(entry, lambda line: None))

    # Engines that never report "running": both really load — one after the other.
    monkeypatch.setattr(type(TTS_ENGINES["qwen3local"]), "is_running", lambda self: False)
    asyncio.run(both())
    assert overlaps == [False, False]
    assert len(aifred) == 2

    # The usual case: the first start's engine serves, the second finds it running.
    aifred.clear()
    overlaps.clear()
    monkeypatch.setattr(type(TTS_ENGINES["qwen3local"]), "is_running", lambda self: ("start", f"GPU-for-{self.key}") in aifred)
    asyncio.run(both())
    assert overlaps == [False]
    assert aifred == [("start", "GPU-for-qwen3local")]


def test_a_cancelled_waiter_never_keeps_the_start_lock() -> None:
    """An Echo reply interrupted while waiting for another start must not
    leave the lock held — later starts would hang forever."""
    import asyncio

    async def scenario() -> bool:
        async with tts_escalation._one_local_gpu_start():
            waiter = asyncio.create_task(tts_escalation._one_local_gpu_start().__aenter__())
            await asyncio.sleep(0.3)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        return tts_escalation._LOCAL_GPU_START.acquire(blocking=False)

    assert asyncio.run(scenario())
    tts_escalation._LOCAL_GPU_START.release()
