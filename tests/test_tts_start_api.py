"""TTS API: token guard, the list in AIfred's order, start/stop per entry —
locally on AIfred's card, on a host over SSH, refused without a fitting card."""

import pytest
from starlette.testclient import TestClient

import aifred.lib.settings as settings_module
from aifred.lib import tts_escalation
from aifred.lib.api.app import api_app
from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_escalation import GPUPlacement, PlacementRefused

TOKEN = "test-tts-control-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOSTS = [{"name": "Box", "address": "box.lan", "enabled": True, "ssh": "mp@box.lan:22"}]
LIST = [
    {"engine": "qwen3local", "host": None, "enabled": True},
    {"engine": "qwen3local", "host": "Box", "enabled": True},
    {"engine": "edge", "host": None, "enabled": True},
]


@pytest.fixture
def aifred(monkeypatch: pytest.MonkeyPatch):
    """AIfred with a three-entry list; records card starts, SSH commands and stops.
    A started engine serves from then on (per address)."""
    monkeypatch.setenv("TTS_CONTROL_API_TOKEN", TOKEN)
    monkeypatch.setattr(settings_module, "persisted_settings", lambda: {"tts_hosts": HOSTS, "tts_escalation": LIST})
    qwen = TTS_ENGINES["qwen3local"]
    calls: list[tuple] = []
    serving: set[str] = set()
    monkeypatch.setattr(qwen, "is_installed", lambda: True)
    monkeypatch.setattr(type(qwen), "is_running", lambda self: self.address in serving)
    monkeypatch.setattr(qwen, "stop", lambda: (calls.append(("stop", "local")), (True, "stopped"))[1])

    def ready(placed):
        calls.append(("start", placed.gpu_uuid))
        serving.add(placed.address)
        return True, "ready", "cuda:0"

    async def place(engine):
        placed = engine.on_gpu("GPU-card-0")
        monkeypatch.setattr(placed, "ensure_ready", lambda: ready(placed))
        return GPUPlacement(placed, 0, 6536, 9231)

    def ssh(host, *command):
        calls.append((host.name, *command))
        if command[0] == "start":
            serving.add(host.address)
        return ""

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", place)
    monkeypatch.setattr(tts_escalation, "host_control", ssh)
    return calls


def _post(path: str, engine: str = "qwen3local", host: str | None = None, headers=AUTH):
    return TestClient(api_app).post(path, headers=headers, json={"engine": engine, "host": host, "caller": "Steuerseite"})


def test_the_service_dir_is_the_container_name() -> None:
    """The API names local containers by service_dir — every compose file must agree."""
    import re

    for engine in TTS_ENGINES.values():
        if engine.runs_in_container:
            compose = engine.docker_compose_path
            assert compose is not None, engine.key
            assert re.search(rf"container_name:\s*{re.escape(engine.service_dir)}\s*$", compose.read_text(), re.M), engine.key


def test_every_endpoint_needs_the_token(aifred) -> None:
    client = TestClient(api_app)
    assert client.get("/tts/entries", params={"lang": "de"}).status_code == 403
    assert _post("/tts/start", headers={}).status_code == 403
    assert _post("/tts/stop", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert aifred == []


def test_missing_server_token_is_503(aifred, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_CONTROL_API_TOKEN")
    assert _post("/tts/start").status_code == 503


def test_the_list_comes_in_aifreds_order_with_status(aifred) -> None:
    rows = TestClient(api_app).get("/tts/entries", params={"lang": "de"}, headers=AUTH).json()
    assert [(row["engine"], row["host"], row["controllable"]) for row in rows] == [
        ("qwen3local", None, True), ("qwen3local", "Box", True), ("edge", None, False),
    ]
    assert [row["container"] for row in rows] == ["qwen3-tts", None, None]
    assert rows[0]["label"] == "Qwen3-TTS · lokal"
    assert rows[0]["status"] == "sleeping" and rows[0]["status_text"] == "schläft — startet bei Bedarf"


def _started(client: TestClient, host: str | None = None) -> dict:
    """Ask for a start and follow /tts/entries until it has settled."""
    import time

    response = client.post("/tts/start", headers=AUTH, json={"engine": "qwen3local", "host": host, "caller": "Steuerseite"})
    assert response.json() == {"success": True, "message": "Qwen3-TTS: start requested"}
    for _ in range(100):
        rows = client.get("/tts/entries", params={"lang": "de"}, headers=AUTH).json()
        row = next(r for r in rows if r["engine"] == "qwen3local" and r["host"] == host)
        if row["status"] != "starting":
            return row
        time.sleep(0.02)
    raise AssertionError("start did not settle")


def test_a_local_entry_starts_in_the_background_on_the_card_aifred_picks(aifred) -> None:
    with TestClient(api_app) as client:
        assert _started(client)["status"] == "running"
    assert aifred == [("start", "GPU-card-0")]


def test_a_host_entry_starts_and_stops_over_ssh(aifred) -> None:
    with TestClient(api_app) as client:
        assert _started(client, host="Box")["status"] == "running"
    assert _post("/tts/stop", host="Box").status_code == 200
    assert aifred == [("Box", "start", "qwen3-tts"), ("Box", "stop", "qwen3-tts")]


def test_no_fitting_card_shows_as_failed_start_with_the_reason(aifred, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(engine):
        raise PlacementRefused("needs 6.536 MiB, most free 461 MiB on GPU 3")

    monkeypatch.setattr(tts_escalation, "place_local_gpu_engine", refuse)
    with TestClient(api_app) as client:
        row = _started(client)
    assert row["status"] == "start_failed" and row["status_text"] == "Start fehlgeschlagen"
    assert "most free 461 MiB" in row["detail"]
    assert aifred == []
    tts_escalation._START_FAILURES.clear()


def test_a_local_entry_stops_through_compose(aifred) -> None:
    assert _post("/tts/stop").status_code == 200
    assert aifred == [("stop", "local")]


@pytest.mark.parametrize(("engine", "host", "status"), [
    ("xtts", None, 404),          # not in the list
    ("qwen3local", "Nope", 404),  # unknown host
    ("edge", None, 409),          # nothing AIfred could start
])
def test_only_controllable_list_entries(aifred, engine: str, host: str | None, status: int) -> None:
    assert _post("/tts/start", engine=engine, host=host).status_code == status
    assert aifred == []


def test_local_starts_run_one_at_a_time(aifred, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each local start sees the memory the one before took: no overlap between
    choosing a card and the model being loaded; a second start of the same
    engine finds it running and starts nothing."""
    import asyncio
    import threading
    import time

    from aifred.lib.tts_escalation import find_entry, launch_entry

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
