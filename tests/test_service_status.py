"""service_status: THE /running access, the countdown of a model from llama-swap's events,
STT/TTS state with the time the services report, and when to ask them again.
No service is touched: llama-swap, STT-Suite and TTS-Suite are replaced by fakes."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import aifred.lib.service_status as ss
from aifred.lib.config import (
    SERVICE_STATUS_MIN_DELAY_S,
    SERVICE_STATUS_OVERDUE_POLL_S,
    SERVICE_STATUS_RECHECK_MARGIN_S,
)
from aifred.lib.tts_client import EngineInfo, TTSSuiteUnreachable

MODEL = "Qwen3-27B"
TTL_S = 900


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(ss, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def model_status(*states: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"id": name, "state": state} for name, state in states]


def monitor_with_ttls(monkeypatch: pytest.MonkeyPatch, ttls: dict[str, int]) -> ss.ServiceStatusMonitor:
    monkeypatch.setattr(ss, "running_models", lambda base_url: [ss.LoadedModel(n, "ready", t) for n, t in ttls.items()])
    return ss.ServiceStatusMonitor()


# ---- /running ----------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self.payload


def test_running_models_parses_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"running": [{"model": MODEL, "state": "ready", "ttl": TTL_S, "proxy": "x", "cmd": "y"}]}
    monkeypatch.setattr(ss.requests, "get", lambda url, timeout: FakeResponse(payload))
    assert ss.running_models("http://swap") == [ss.LoadedModel(MODEL, "ready", TTL_S)]


def test_running_models_names_the_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: float) -> None:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(ss.requests, "get", refuse)
    with pytest.raises(ss.LlamaSwapError, match="refused"):
        ss.running_models("http://swap")
    monkeypatch.setattr(ss.requests, "get", lambda url, timeout: FakeResponse({"unexpected": 1}))
    with pytest.raises(ss.LlamaSwapError):
        ss.running_models("http://swap")


# ---- the model's countdown ----------------------------------------------------


def test_a_model_that_becomes_ready_counts_down_from_ready(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    monitor = monitor_with_ttls(monkeypatch, {MODEL: TTL_S})
    asyncio.run(monitor._on_model_status(model_status((MODEL, "starting"))))
    loading = monitor.snapshot().models[0]
    assert (loading.state, loading.remaining_s) == ("starting", None)

    clock[0] += 60
    asyncio.run(monitor._on_model_status(model_status((MODEL, "ready"))))
    clock[0] += 100
    ready = monitor.snapshot().models[0]
    assert (ready.state, ready.remaining_s, ready.exact) == ("ready", TTL_S - 100, True)


def test_a_model_already_ready_when_listening_starts_is_an_upper_bound(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    monitor = monitor_with_ttls(monkeypatch, {MODEL: TTL_S})
    asyncio.run(monitor._on_model_status(model_status((MODEL, "ready"))))
    assert monitor.snapshot().models[0].exact is False


def test_the_clock_stands_still_while_a_request_runs_and_restarts_when_it_ends(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    monitor = monitor_with_ttls(monkeypatch, {MODEL: TTL_S})
    asyncio.run(monitor._on_model_status(model_status((MODEL, "starting"))))
    asyncio.run(monitor._on_model_status(model_status((MODEL, "ready"))))
    clock[0] += 500
    monitor._on_inflight([{"model": MODEL}])
    clock[0] += 300
    busy = monitor.snapshot().models[0]
    assert (busy.busy, busy.remaining_s) == (True, float(TTL_S))

    monitor._on_inflight([])
    clock[0] += 10
    idle = monitor.snapshot().models[0]
    assert (idle.busy, idle.remaining_s, idle.exact) == (False, TTL_S - 10, True)


def test_a_model_with_ttl_zero_never_unloads_and_an_unloaded_one_disappears(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    monitor = monitor_with_ttls(monkeypatch, {"bge-m3": 0})
    asyncio.run(monitor._on_model_status(model_status(("bge-m3", "starting"))))
    asyncio.run(monitor._on_model_status(model_status(("bge-m3", "ready"))))
    assert monitor.snapshot().models[0].remaining_s is None
    asyncio.run(monitor._on_model_status(model_status(("bge-m3", "stopped"))))
    assert monitor.snapshot().models == []


def test_listeners_hear_every_change_and_a_broken_one_does_not_silence_the_rest(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    monitor = monitor_with_ttls(monkeypatch, {MODEL: TTL_S})
    heard: list[ss.ServiceSnapshot] = []

    def broken(snapshot: ss.ServiceSnapshot) -> None:
        raise ValueError("boom")

    monitor.add_listener(broken)
    monitor.add_listener(heard.append)
    asyncio.run(monitor._on_event({"type": "modelStatus", "data": json.dumps(model_status((MODEL, "ready")))}))
    asyncio.run(monitor._on_event({"type": "inflight", "data": json.dumps({"requests": []})}))
    assert len(heard) == 2 and heard[0].models[0].name == MODEL


def test_the_embedding_server_is_not_listed_and_a_describer_is_told_apart(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    ttls = {MODEL: TTL_S, "vl4b-visiond": TTL_S, "vl4b-visiond-gpu1": TTL_S, "bge-m3-567M-Q8_0-embed": 0}
    monitor = monitor_with_ttls(monkeypatch, ttls)
    states = model_status(*((name, "ready") for name in ttls))
    asyncio.run(monitor._on_model_status(states))
    assert {m.name: m.role for m in monitor.snapshot().models} == {
        MODEL: "llm", "vl4b-visiond": "describer", "vl4b-visiond-gpu1": "describer",
    }


# ---- STT and TTS ---------------------------------------------------------------


def stt_status(**changes: Any) -> dict[str, Any]:
    base = {
        "cpu_loaded": True, "cpu_loaded_engine": "parakeet", "cpu_loaded_model": "parakeet-fp32",
        "gpu_loaded": False, "gpu_loaded_engine": None, "gpu_loaded_model": None, "gpu_busy": False,
        "gpu_ttl_minutes": 30,
    }
    return {**base, **changes}


def fake_stt(monkeypatch: pytest.MonkeyPatch, status: dict[str, Any] | Exception) -> None:
    def read() -> dict[str, Any]:
        if isinstance(status, Exception):
            raise status
        return status

    monkeypatch.setattr("aifred.lib.stt_client.get_default_client", lambda: SimpleNamespace(status=read))


def test_stt_cpu_stays_loaded_and_the_gpu_worker_counts_down(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    fake_stt(monkeypatch, stt_status(
        gpu_loaded=True, gpu_loaded_engine="whisper", gpu_loaded_model="large-v3", gpu_ttl_remaining_seconds=1200,
    ))
    monitor = ss.ServiceStatusMonitor()
    asyncio.run(monitor._refresh_stt())
    clock[0] += 20
    cpu, gpu = monitor.snapshot().stt
    assert (cpu.device, cpu.remaining_s) == ("cpu", None)
    assert (gpu.device, gpu.engine, gpu.model, gpu.remaining_s) == ("cuda", "whisper", "large-v3", 1180)


def test_a_busy_gpu_worker_holds_the_full_window(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    fake_stt(monkeypatch, stt_status(
        gpu_loaded=True, gpu_loaded_engine="whisper", gpu_loaded_model="large-v3",
        gpu_busy=True, gpu_ttl_remaining_seconds=5,
    ))
    monitor = ss.ServiceStatusMonitor()
    asyncio.run(monitor._refresh_stt())
    assert monitor.snapshot().stt[1].remaining_s == 1800


def test_stt_service_down_is_shown_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib.stt_client import STTUnreachableError

    fake_stt(monkeypatch, STTUnreachableError("down"))
    monitor = ss.ServiceStatusMonitor()
    asyncio.run(monitor._refresh_stt())
    snapshot = monitor.snapshot()
    assert (snapshot.stt_reachable, snapshot.stt) == (False, [])


def engine_info(engine: str, **changes: Any) -> EngineInfo:
    base: dict[str, Any] = {
        "engine": engine, "host": None, "label": {"de": engine, "en": engine}, "label_short": {"de": engine, "en": engine},
        "status": "running", "unit_default": "sentence", "needs_gpu": True, "cloud": False, "browser": False,
        "installed": True, "start_hint": None, "vram_mib": {}, "idle_remaining_s": 1500.0,
        "default_vram_mib": None, "default_max_parallel": 1, "default_max_queue": 4,
    }
    return EngineInfo(**{**base, **changes})


def test_tts_lists_the_running_gpu_engines_with_their_time_left(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    catalog = (
        engine_info("qwen3local"),
        engine_info("xtts", status="stopped", idle_remaining_s=None),
        engine_info("piper", needs_gpu=False, idle_remaining_s=None),
    )
    monkeypatch.setattr("aifred.lib.tts_service.catalog", lambda fresh=False: catalog)
    monitor = ss.ServiceStatusMonitor()
    asyncio.run(monitor._refresh_tts())
    clock[0] += 100
    (engine,) = monitor.snapshot().tts
    assert (engine.engine, engine.remaining_s) == ("qwen3local", 1400)


def test_tts_service_down_is_shown_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(fresh: bool = False) -> None:
        raise TTSSuiteUnreachable("down")

    monkeypatch.setattr("aifred.lib.tts_service.catalog", refuse)
    monitor = ss.ServiceStatusMonitor()
    asyncio.run(monitor._refresh_tts())
    assert monitor.snapshot().tts_reachable is False


# ---- when to ask again -----------------------------------------------------------


@pytest.mark.parametrize(
    ("remaining_s", "delay_s"),
    [
        (1500, 1500 - SERVICE_STATUS_RECHECK_MARGIN_S),
        (SERVICE_STATUS_RECHECK_MARGIN_S + 1, 1),  # raised to the minimum below
        (50, 50 + SERVICE_STATUS_MIN_DELAY_S),
        (0, SERVICE_STATUS_OVERDUE_POLL_S),
    ],
)
def test_next_look_is_shortly_before_the_expiry_and_polls_slowly_once_overdue(remaining_s: float, delay_s: float) -> None:
    assert ss.next_look_delay_s(remaining_s) == max(delay_s, SERVICE_STATUS_MIN_DELAY_S)


def test_refresh_from_another_thread_does_nothing_before_the_monitor_runs() -> None:
    ss.ServiceStatusMonitor().refresh_threadsafe("tts")


# ---- schedule and bus ---------------------------------------------------------


def test_refresh_reads_the_service_and_schedules_the_next_look_before_the_expiry(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    fake_stt(monkeypatch, stt_status(
        gpu_loaded=True, gpu_loaded_engine="whisper", gpu_loaded_model="large-v3", gpu_ttl_remaining_seconds=1200,
    ))
    monitor = ss.ServiceStatusMonitor()
    heard: list[ss.ServiceSnapshot] = []
    monitor.add_listener(heard.append)

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        monitor._loop = loop
        await monitor.refresh("stt")
        handle = monitor._recheck_handles["stt"]
        delay = handle.when() - loop.time()
        handle.cancel()
        return delay

    delay = asyncio.run(scenario())
    assert len(heard) == 1
    assert delay == pytest.approx(1200 - SERVICE_STATUS_RECHECK_MARGIN_S, abs=1)


def test_the_status_line_keeps_only_the_newest_state_in_the_bus_queue() -> None:
    from aifred.lib.api import browser_bus

    session = "test-session-service-status"
    browser_bus._browser_event_storage.pop(session, None)
    try:
        browser_bus.browser_push(session, "service_status", "{\"n\": 1}")
        browser_bus.browser_push(session, "tts", "http://x/a.wav")
        browser_bus.browser_push(session, "service_status", "{\"n\": 2}")
        queue = browser_bus._browser_event_storage[session]["queue"]
        assert [(item["kind"], item["url"]) for item in queue] == [("tts", "http://x/a.wav"), ("service_status", "{\"n\": 2}")]
    finally:
        browser_bus._browser_event_storage.pop(session, None)
