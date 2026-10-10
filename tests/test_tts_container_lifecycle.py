"""Container-Lebenszyklus der TTS-Engines: ein gemeinsames Gerüst in ``TTSEngine``."""
from __future__ import annotations

from typing import Any

import pytest

from aifred.lib.tts_engines import TTS_ENGINES
from aifred.lib.tts_engines.fishspeech import FishSpeechEngine
from aifred.lib.tts_engines.moss import MOSSEngine
from aifred.lib.tts_engines.qwen3local import Qwen3LocalEngine
from aifred.lib.tts_engines.xtts import XTTSEngine

CONTAINER_ENGINES = [e for e in TTS_ENGINES.values() if e.runs_in_container]


@pytest.mark.parametrize("engine", CONTAINER_ENGINES, ids=lambda e: e.key)
def test_container_engine_declares_what_the_scaffold_needs(engine: Any) -> None:
    assert engine.default_port
    assert engine.startup_timeout_s > 0
    assert engine.docker_compose_path.exists()
    assert engine.health_path.startswith("/")


def test_model_ready_signatures() -> None:
    assert Qwen3LocalEngine()._model_ready({"model_loaded": True})
    assert not Qwen3LocalEngine()._model_ready({"model_loaded": False})
    assert XTTSEngine()._model_ready({"model_loaded": True, "custom_voices": []})
    assert not XTTSEngine()._model_ready({"model_loaded": True})
    assert MOSSEngine()._model_ready({"model_loaded": True, "voices": [], "sample_rate": 24000})
    assert not MOSSEngine()._model_ready({"model_loaded": True, "voices": []})
    assert FishSpeechEngine()._model_ready({})


def test_ensure_ready_returns_without_starting_when_model_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Qwen3LocalEngine()
    monkeypatch.setattr(engine, "_health", lambda timeout=None: {"model_loaded": True, "device": "cuda:0"})
    monkeypatch.setattr(engine, "_start_local", lambda: pytest.fail("must not start"))
    assert engine.ensure_ready() == (True, "Qwen3-TTS already ready (cuda:0)", "cuda:0")


def test_ensure_ready_starts_and_waits_for_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = XTTSEngine()
    answers = iter([None, None, {"model_loaded": True, "custom_voices": [], "device": "cuda"}])
    started: list[bool] = []

    def start() -> tuple[bool, str]:
        started.append(True)
        return True, "up"

    monkeypatch.setattr(engine, "_health", lambda timeout=None: next(answers))
    monkeypatch.setattr(engine, "_start_local", start)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert engine.ensure_ready() == (True, "XTTS ready (cuda)", "cuda")
    assert started == [True]


def test_ensure_ready_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = XTTSEngine()
    monkeypatch.setattr(engine, "_health", lambda timeout=None: None)
    monkeypatch.setattr(engine, "_start_local", lambda: (True, "up"))
    monkeypatch.setattr("time.sleep", lambda _s: None)
    ok, message, device = engine.ensure_ready(timeout=3)
    assert (ok, device) == (False, "")
    assert "Timeout after 3s" in message


def test_moss_on_cpu_restarts_when_a_gpu_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MOSSEngine()
    on_cpu = {"model_loaded": True, "voices": [], "sample_rate": 24000, "device": "cpu"}
    on_gpu = on_cpu | {"device": "cuda:0"}
    answers = iter([on_cpu, on_gpu])
    calls: list[str] = []

    def stop() -> tuple[bool, str]:
        calls.append("stop")
        return True, "down"

    def start() -> tuple[bool, str]:
        calls.append("start")
        return True, "up"

    monkeypatch.setattr(engine, "_health", lambda timeout=None: next(answers))
    monkeypatch.setattr(engine, "_gpu_available", lambda: True)
    monkeypatch.setattr(engine, "_stop_local", stop)
    monkeypatch.setattr(engine, "_start_local", start)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert engine.ensure_ready()[2] == "cuda:0"
    assert calls == ["stop", "start"]
