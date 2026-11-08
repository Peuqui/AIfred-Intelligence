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
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

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
