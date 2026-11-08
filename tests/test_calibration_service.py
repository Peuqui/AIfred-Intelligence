"""The calibration run outside the browser (lib/calibration/service.py, api/backend.py): what a request must have,
the server's run (one per process, the gate free again afterwards, a cancel or a failure said in its lines, its
console readable from a line on), the speed of an agent whose model lost its -speed profile, the options. The run's
steps are a stand-in (a real one stops llama-swap and takes every card); settings.json, the caches and the
llama-swap file are files of their own."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import calibration_gate, model_vram_cache, settings
from aifred.lib.calibration import service
from aifred.lib.calibration.service import CalibrationRequest, CalibrationRun, check_request
from aifred.lib.user_error import Conflict, Invalid, Missing

MODEL = "Big-30B"


@pytest.fixture(autouse=True)
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """settings.json, the cache and the llama-swap file of their own; the gate closed, no earlier server run."""
    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"backend_type": "llamacpp", "backend_models": {"llamacpp": {"aifred": MODEL}},
                                "agent_tuning": {"aifred": {"speed_mode": True}}}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    monkeypatch.setattr(model_vram_cache, "CACHE_FILE", tmp_path / "model_vram_cache.json")
    monkeypatch.setattr(model_vram_cache, "_cache", None)
    for module in ("config", "operating_points", "runtime_settings"):
        monkeypatch.setattr(f"aifred.lib.{module}.LLAMASWAP_CONFIG_PATH", tmp_path / "llama-swap.yaml")
    (tmp_path / "llama-swap.yaml").write_text(f"models:\n  {MODEL}:\n    cmd: llama-server -m big.gguf -c 32768\n")
    monkeypatch.setattr(calibration_gate, "_active", False)
    monkeypatch.setattr(calibration_gate, "_cancel_requested", False)
    monkeypatch.setattr(service, "_server_run", None)
    return file


def _request(**changes: Any) -> CalibrationRequest:
    values: dict[str, Any] = {"backend_type": "llamacpp", "backend_url": "http://llama-swap", "model_id": MODEL, "cells": {"|": True}}
    return CalibrationRequest(**{**values, **changes})


# ── What a request must have ─────────────────────────────────────────────


def test_a_request_needs_a_calibrating_backend_a_model_and_a_cell() -> None:
    assert check_request(_request()) is None
    assert check_request(_request(backend_type="ollama", cells={})) is None  # Ollama has no matrix
    with pytest.raises(Invalid):
        check_request(_request(backend_type="cloud_api"))
    with pytest.raises(Invalid):
        check_request(_request(model_id=""))
    with pytest.raises(Invalid):
        check_request(_request(cells={"|": False}))  # without a cell the config's values would pass as calibrated


def test_vllm_needs_its_checkpoint(tmp_path: Path) -> None:
    yaml = tmp_path / "llama-swap.yaml"
    yaml.write_text(f"models:\n  {MODEL}-vllm:\n    cmd: vllm serve --model {tmp_path / 'ckpt'} --max-model-len 4096\n")
    with pytest.raises(Missing):
        check_request(_request(backend_type="vllm", model_id=f"{MODEL}-vllm"))
    (tmp_path / "ckpt").mkdir()
    assert check_request(_request(backend_type="vllm", model_id=f"{MODEL}-vllm")) == (tmp_path / "ckpt", f"{MODEL}-vllm")


# ── The server's run ─────────────────────────────────────────────────────


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The run's steps as a stand-in: three lines, optionally a failure; its finally (llama-swap) is recorded."""
    world: dict[str, Any] = {"cleaned": False, "fail": False, "gate_during": None}

    async def fake(self: CalibrationRun) -> AsyncGenerator[None, None]:
        try:
            for step in range(3):
                world["gate_during"] = calibration_gate.is_calibration_active()
                self._report(f"step {step}")
                if world["fail"] and step == 1:
                    raise RuntimeError("boom")
                yield
        finally:
            world["cleaned"] = True

    monkeypatch.setattr(CalibrationRun, "_calibrate_context_impl", fake)
    return world


def _run_to_end(request: CalibrationRequest, before: Any = None) -> service.ServerRun:
    async def go() -> service.ServerRun:
        state = service.start_server_run(request)
        if before:
            before()
        assert service._server_task is not None
        await service._server_task
        return state

    return asyncio.run(go())


def test_a_run_reports_its_lines_and_frees_the_gate(steps: dict[str, Any]) -> None:
    state = _run_to_end(_request())
    assert (state.running, state.outcome) == (False, "done")
    assert [line.split(" | ", 1)[1] for line in state.lines] == [f"🔧 Starting calibration for {MODEL}...", "step 0", "step 1", "step 2"]
    assert steps["gate_during"] is True and calibration_gate.is_calibration_active() is False
    assert steps["cleaned"]


def test_a_cancel_stops_at_the_next_step_and_says_what_is_left(steps: dict[str, Any]) -> None:
    state = _run_to_end(_request(), before=lambda: calibration_gate.request_cancel())
    assert state.outcome == "cancelled" and steps["cleaned"]
    assert any("cancelled by user" in line for line in state.lines)
    assert "Calibration aborted" in state.lines[-1]
    assert calibration_gate.is_calibration_active() is False


def test_a_failing_step_ends_the_run_with_its_reason(steps: dict[str, Any]) -> None:
    steps["fail"] = True
    state = _run_to_end(_request())
    assert state.outcome == "failed" and "boom" in state.lines[-1]
    assert calibration_gate.is_calibration_active() is False


def test_a_second_start_is_refused_while_one_runs(steps: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibration_gate, "_active", True)  # e.g. started in the Reflex UI
    with pytest.raises(Conflict):
        service.start_server_run(_request())
    assert service.latest_server_run() is None


def test_after_a_run_an_agent_without_speed_profile_runs_without_speed(files: Path) -> None:
    service.settle_speed_after_run()
    assert json.loads(files.read_text())["agent_tuning"]["aifred"]["speed_mode"] is False


# ── Options ──────────────────────────────────────────────────────────────


def test_the_options_are_checked_and_kept_in_settings(files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aifred.lib.agent_config.load_agents_raw", lambda: {"calibration": {"cloud_provider": "qwen"}})
    options = service.set_calibration_options(mode="ai", allow_hybrid=True)
    assert (options.mode, options.allow_hybrid, options.ai_model, options.ai_available) == ("ai", True, "", False)
    saved = json.loads(files.read_text())
    assert (saved["calibration_mode"], saved["calibration_allow_hybrid"], saved["backend_type"]) == ("ai", True, "llamacpp")
    with pytest.raises(Invalid):
        service.set_calibration_options(mode="magic")


# ── The endpoints ────────────────────────────────────────────────────────


@pytest.fixture
def client(steps: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    monkeypatch.setattr("aifred.lib.calibration.matrix.all_cell_keys", lambda: ["|", "|xtts"])
    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_api_starts_follows_and_refuses(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post("/calibration/run", json={"cells": ["eyes|"]}).status_code == 422  # no such cell
    assert client.post("/calibration/run", json={"cells": []}).status_code == 422  # nothing ticked
    started = client.post("/calibration/run", json={"cells": ["|"]})
    assert started.status_code == 202 and started.json()["model_id"] == MODEL
    seen = client.get("/calibration/run").json()
    for _ in range(50):  # the run is a task of the app's loop; it ends after its three steps
        if not seen["running"]:
            break
        seen = client.get("/calibration/run").json()
    assert seen["outcome"] == "done" and seen["active"] is False
    later = client.get(f"/calibration/run?after={seen['next'] - 1}").json()
    assert [line.split(" | ", 1)[1] for line in later["lines"]] == ["step 2"]
    monkeypatch.setattr(calibration_gate, "_active", True)
    assert client.post("/calibration/run", json={"cells": ["|"]}).status_code == 409


# ── The pairs of the vLLM matrix ─────────────────────────────────────────

SIDE, OTHER = "GPU-side", "GPU-other"


@pytest.fixture
def pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A describer with a 7.000 MiB reserve, two engines (3.000 and 30.000 MiB), one without a budget; the side card
    has 32.768 MiB. The pair cache is a file of its own."""
    from aifred.lib import sidechannel_vram_cache
    from aifred.lib.vram_peak_cache import JsonPeakCache

    world: dict[str, Any] = {"vlm_uuid": SIDE, "reserve": 7000, "measured": []}
    monkeypatch.setattr(sidechannel_vram_cache, "_store", JsonPeakCache(tmp_path / "pairs.json", "pairs"))
    monkeypatch.setattr("aifred.lib.vision_routing.vlm_calibration_choices",
                        lambda: [{"key": "eyes", "model_id": "Eyes-4B", "label": "Eyes-4B"}])
    monkeypatch.setattr("aifred.lib.tts_service.gpu_engine_needs", lambda: {"small": 3000, "huge": 30000})
    monkeypatch.setattr("aifred.lib.process_utils.get_tts_gpu_uuid", lambda: SIDE)
    monkeypatch.setattr("aifred.lib.nvidia_smi.query", lambda fields: [
        {"index": "4", "uuid": SIDE, "memory.total": "32768"}, {"index": "0", "uuid": OTHER, "memory.total": "49152"}])

    async def reserve(num_ctx: int, model_id_override: str | None = None) -> tuple[str | None, int]:
        world["measured"].append(model_id_override)
        return world["vlm_uuid"], world["reserve"]

    monkeypatch.setattr("aifred.lib.vlm_stress_prewarm.resolve_vlm_reserve", reserve)
    return world


def _judge(cells: list[str]) -> list[str]:
    lines: list[str] = []
    run = CalibrationRun(_request(cells=dict.fromkeys(cells, True)), report=lines.append)

    async def go() -> None:
        async for _ in run._judge_side_channel_pairs():
            pass

    asyncio.run(go())
    return lines


def test_a_pair_fits_when_both_reserves_fit_the_side_card(pairs: dict[str, Any]) -> None:
    from aifred.lib import sidechannel_vram_cache

    lines = _judge(["|", "eyes|small", "eyes|huge", "eyes|", "|huge"])
    assert sidechannel_vram_cache.fits("eyes", "small") is True  # 7.000 + 3.000 of 32.768
    assert sidechannel_vram_cache.fits("eyes", "huge") is False  # 7.000 + 30.000
    assert sidechannel_vram_cache.fits("eyes", "") is True
    assert sidechannel_vram_cache.fits("", "huge") is True  # the engine alone fits
    assert sidechannel_vram_cache.fits("", "") is None  # the base cell is the operating point, no pair
    assert any("does NOT fit: VLM 7000 + TTS 30000 = 37000 of 32768 MiB on GPU4" in line for line in lines)
    assert pairs["measured"] == ["Eyes-4B", "Eyes-4B", "Eyes-4B"]


def test_on_different_cards_each_reserve_needs_only_its_own(pairs: dict[str, Any]) -> None:
    from aifred.lib import sidechannel_vram_cache

    pairs["vlm_uuid"] = OTHER
    _judge(["eyes|huge"])
    assert sidechannel_vram_cache.fits("eyes", "huge") is True  # 30.000 on the side card, 7.000 on the other


def test_a_pair_that_cannot_be_judged_stays_open_and_says_why(pairs: dict[str, Any]) -> None:
    from aifred.lib import sidechannel_vram_cache

    pairs["reserve"] = 0
    lines = _judge(["eyes|small", "|nobudget", "gone|small"])
    assert sidechannel_vram_cache.fits("eyes", "small") is None
    assert sidechannel_vram_cache.fits("", "nobudget") is None
    assert sidechannel_vram_cache.fits("gone", "small") is None
    assert sum("left unjudged" in line for line in lines) == 3


def test_a_vllm_run_with_pairs_only_starts_llama_swap_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the base unticked the run returns after the pairs; it left llama-swap stopped before."""
    (tmp_path / "ckpt").mkdir()
    (tmp_path / "llama-swap.yaml").write_text(
        f"models:\n  {MODEL}-vllm:\n    cmd: vllm serve --model {tmp_path / 'ckpt'} --max-model-len 4096\n")
    calls: list[str] = []
    def systemctl(action: str) -> Any:
        def run() -> bool:
            calls.append(action)
            return True
        return run

    monkeypatch.setattr("aifred.lib.process_utils.stop_llama_swap", systemctl("stop"))
    monkeypatch.setattr("aifred.lib.process_utils.start_llama_swap", systemctl("start"))
    monkeypatch.setattr(service, "_free_cards_for_calibration", lambda report: None)

    async def no_pairs(self: CalibrationRun) -> AsyncGenerator[None, None]:
        calls.append("pairs")
        yield

    monkeypatch.setattr(CalibrationRun, "_judge_side_channel_pairs", no_pairs)
    lines: list[str] = []
    run = CalibrationRun(_request(backend_type="vllm", model_id=f"{MODEL}-vllm", cells={"eyes|xtts": True}), report=lines.append)

    async def go() -> None:
        async for _ in run.steps():
            pass

    asyncio.run(go())
    assert calls == ["stop", "pairs", "start"]
    assert "🔄 llama-swap restarted (operating point unchanged)" in lines
