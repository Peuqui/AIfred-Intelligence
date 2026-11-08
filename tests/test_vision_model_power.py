"""The image model's power button (lib/vision_prewarm.py): a model with a ``-visiond`` profile runs as llama-swap's
describer, so "loaded" means one of its placements is loaded there and "unload" stops each of them; any other model
is Ollama's. Before, both asked Ollama only: a loaded describer showed as not loaded and stayed on its card.
llama-swap's file, its loaded profiles, the unload call and Ollama are stand-ins."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aifred.lib import service_control, vision_prewarm, vision_routing
from aifred.lib.service_control import ServiceControlRefused

DESCRIBER = "Qwen3VL-4B-Instruct-Q8_0"
PROFILES: dict[str, dict[str, Any]] = {f"{DESCRIBER}-visiond": {}, f"{DESCRIBER}-visiond-gpu2": {}, DESCRIBER: {}, "Big-30B": {}}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"loaded": [], "unloaded": [], "ollama": []}
    monkeypatch.setattr("aifred.lib.calibration.llamaswap_io.parse_llamaswap_config", lambda _path: PROFILES)
    monkeypatch.setattr(vision_routing, "loaded_llamaswap_profiles", lambda: state["loaded"])

    def unload(profile: str) -> None:
        if profile in state.get("refuse", ()):
            raise ServiceControlRefused("service_control_failed", name=profile, error="HTTP 500")
        state["unloaded"].append(profile)

    monkeypatch.setattr(service_control, "unload_llamaswap_model", unload)

    class _Ollama:
        def __init__(self, host: str | None = None) -> None:
            pass

        async def ps(self) -> dict[str, Any]:
            state["ollama"].append("ps")
            return {"models": [{"name": "qwen3-vl:8b"}]}

        async def generate(self, **kwargs: Any) -> None:
            state["ollama"].append(f"generate {kwargs['model']} keep_alive={kwargs['keep_alive']}")

    monkeypatch.setattr("ollama.AsyncClient", _Ollama)
    monkeypatch.setattr("aifred.lib.config.resolve_vlm_host", lambda: "http://ollama")
    return state


def test_a_describer_is_loaded_when_any_of_its_placements_is(world: dict[str, Any]) -> None:
    assert asyncio.run(vision_prewarm.is_vlm_loaded(DESCRIBER)) is False
    world["loaded"] = ["Big-30B", f"{DESCRIBER}-visiond-gpu2"]
    assert asyncio.run(vision_prewarm.is_vlm_loaded(DESCRIBER)) is True
    # The chat model under the same base name is no describer placement.
    world["loaded"] = [DESCRIBER]
    assert asyncio.run(vision_prewarm.is_vlm_loaded(DESCRIBER)) is False
    assert world["ollama"] == []


def test_unloading_a_describer_stops_each_loaded_placement(world: dict[str, Any]) -> None:
    world["loaded"] = [f"{DESCRIBER}-visiond", "Big-30B", f"{DESCRIBER}-visiond-gpu2"]
    assert asyncio.run(vision_prewarm.unload_vlm_model(DESCRIBER)) is True
    assert world["unloaded"] == [f"{DESCRIBER}-visiond", f"{DESCRIBER}-visiond-gpu2"]
    assert world["ollama"] == []


def test_a_refused_unload_says_why(world: dict[str, Any]) -> None:
    world["loaded"] = [f"{DESCRIBER}-visiond"]
    world["refuse"] = {f"{DESCRIBER}-visiond"}
    with pytest.raises(ServiceControlRefused):
        asyncio.run(vision_prewarm.unload_vlm_model(DESCRIBER))


def test_a_model_without_describer_profile_stays_with_ollama(world: dict[str, Any]) -> None:
    assert asyncio.run(vision_prewarm.is_vlm_loaded("qwen3-vl:8b")) is True
    assert asyncio.run(vision_prewarm.unload_vlm_model("qwen3-vl:8b")) is True
    assert world["ollama"] == ["ps", "generate qwen3-vl:8b keep_alive=0"]
    assert world["unloaded"] == []
