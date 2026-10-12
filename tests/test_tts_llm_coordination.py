"""Abstimmung LLM und TTS: nur das LLM-Profil wird gewählt (llama-swap), kein TTS-Container wird
gestartet, gestoppt oder geladen. Der Dienst ist ein httpx.MockTransport, der nur GET /engines beantwortet."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aifred.lib import process_utils, tts_llm_coordination, tts_service
from aifred.lib.tts_client import TTSClient


def row(engine: str, **fields: Any) -> dict[str, Any]:
    base = {
        "engine": engine, "host": None, "label": {"de": engine, "en": engine}, "label_short": {"de": engine, "en": engine},
        "status": "running", "unit_default": "sentence", "needs_gpu": True, "cloud": False, "browser": False,
        "installed": True, "start_hint": None, "vram_mib": {},
        "default_vram_mib": None, "default_max_parallel": 1, "default_max_queue": 4,
    }
    return {**base, **fields}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Catalog: xtts (GPU) and piper (CPU); records the LLM-side actions and every request to the service."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/engines"), "no container may be touched"
        return httpx.Response(200, json=[row("xtts"), row("piper", needs_gpu=False)])

    monkeypatch.setattr(tts_service, "get_tts_client", lambda: TTSClient("http://tts.test", transport=httpx.MockTransport(handler)))
    tts_service.reset_cache()
    monkeypatch.setattr(tts_llm_coordination, "_is_llm_loaded", lambda backend: True)
    monkeypatch.setattr(tts_llm_coordination, "_llm_already_on_tts_variant", lambda engine, backend: False)
    monkeypatch.setattr(tts_llm_coordination, "restart_llm_backend", lambda backend="llamacpp": seen.append("restart") or True)
    monkeypatch.setattr(tts_llm_coordination, "get_effective_model_info", lambda backend="llamacpp": "model (ctx: 1)")
    monkeypatch.setattr(process_utils, "unload_llm_models", lambda backend="llamacpp": seen.append("unload") or ["llama-swap stopped"])
    return seen


def run(wanted: str) -> tuple[list[str], tts_llm_coordination.TTSState]:
    generator = tts_llm_coordination.ensure_tts_state(wanted_tts=wanted, backend_type="llamacpp")
    messages: list[str] = []
    while True:
        try:
            messages.append(next(generator))
        except StopIteration as done:
            return messages, done.value


def test_a_gpu_engine_switches_the_llm_profile_only(calls) -> None:
    messages, state = run("xtts")
    assert calls == ["unload", "restart"]
    assert state.success and state.changed
    assert messages[-1] == "LLM profile ready: model (ctx: 1)"


def test_a_llm_already_on_the_matching_profile_is_left_alone(calls, monkeypatch) -> None:
    monkeypatch.setattr(tts_llm_coordination, "_llm_already_on_tts_variant", lambda engine, backend: True)
    messages, state = run("xtts")
    assert calls == [] and state.success and not state.changed
    assert messages == ["LLM already on the xtts profile"]


@pytest.mark.parametrize("wanted", ["", "piper"])
def test_no_gpu_engine_planned_changes_nothing(calls, wanted: str) -> None:
    messages, state = run(wanted)
    assert calls == [] and messages == [] and state.success and not state.changed


def test_the_module_has_no_container_calls() -> None:
    for name in ("start_engine", "stop_engine", "running_gpu_engine", "is_tts_in_use"):
        assert not hasattr(tts_llm_coordination, name), name
    for name in ("stop_all_installed_tts", "unload_all_gpu_models"):
        assert not hasattr(process_utils, name), name
