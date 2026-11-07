"""lib/runtime_settings.py: one set of rules for model choice, for the Reflex state and for the paths
without it (turn service, channels, scheduler).

Before, the rules stood three times: _effective_model_id in the state, get_effective_model_from_settings
in config.py, and the variant lookup in get_agent_num_ctx.
"""

from typing import Any

import pytest

from aifred.lib import runtime_settings as rs
from aifred.lib.agent_tuning import default_agent_tuning


def _view(backend: str = "llamacpp", **tuning: dict[str, Any]) -> rs.RuntimeSettings:
    view = rs.RuntimeSettings(
        backend_type=backend, backend_url="", temperature_mode="auto", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agent_tuning(),
    )
    for agent, fields in tuning.items():
        for name, value in fields.items():
            setattr(view.agent_tuning[agent], name, value)
    return view


@pytest.fixture(autouse=True)
def _no_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    # The variant resolver reads the llama-swap YAML; here a model has a "-speed" variant exactly
    # when the speed toggle is on and the model has one.
    from aifred.lib import calibration

    def suffix(_path: Any, _base: str, _backend: str, *, speed_on: bool, has_speed_variant: bool,
               tts_active: bool, tts_engine: str) -> str:
        return "-speed" if speed_on and has_speed_variant else ""

    monkeypatch.setattr(calibration, "resolve_effective_suffix", suffix)


def test_an_agent_without_an_own_model_resolves_to_nothing() -> None:
    view = _view(aifred={"model_id": "big"})
    assert rs.effective_model_id(view, "sokrates") == ""
    assert rs.effective_model_id(view, "aifred") == "big"


def test_the_speed_toggle_of_the_agent_selects_its_variant() -> None:
    view = _view(aifred={"model_id": "big", "speed_mode": True, "has_speed_variant": True})
    assert rs.effective_model_id(view, "aifred") == "big-speed"


def test_the_automatik_follows_aifred_without_an_own_model() -> None:
    view = _view(aifred={"model_id": "big", "speed_mode": True, "has_speed_variant": True})
    assert rs.effective_automatik_id(view) == "big-speed"
    view.automatik_model_id = "small"
    assert rs.effective_automatik_id(view) == "small-speed"   # AIfred's toggle, the resolver checks the profile


def test_always_defaulted_agents_get_a_default_even_with_saved_models() -> None:
    view = _view("ollama")
    applied = rs.apply_default_models(view, had_backend_settings=True)
    assert {agent for agent, _ in applied} == {"aifred", "vision"}
    assert view.agent_tuning["sokrates"].model_id == ""   # saved empty = "AIfred's model"


def test_without_saved_models_every_agent_and_the_automatik_get_their_default() -> None:
    from aifred.lib.config import BACKEND_DEFAULT_MODELS

    view = _view("ollama")
    rs.apply_default_models(view, had_backend_settings=False)
    defaults = BACKEND_DEFAULT_MODELS["ollama"]
    assert view.automatik_model_id == defaults["automatik"]
    assert view.agent_tuning["sokrates"].model_id == defaults["sokrates"]


def test_saved_models_skip_unregistered_agents_and_set_the_automatik() -> None:
    view = _view("llamacpp")
    saved = {"backend_models": {"llamacpp": {"aifred": "big", "automatik": "small", "gibt-es-nicht": "x"}}}
    assert rs.apply_saved_models(view, saved) is True
    assert (view.agent_tuning["aifred"].model_id, view.automatik_model_id) == ("big", "small")
    assert "gibt-es-nicht" not in view.agent_tuning


def test_load_takes_missing_keys_from_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import settings

    monkeypatch.setattr(settings, "load_settings", lambda: {"backend_type": "ollama", "backend_models": {}})
    monkeypatch.setattr(rs, "load_agent_model_params", lambda *_args: None)
    view = rs.load_runtime_settings()
    defaults = settings.get_default_settings()
    assert (view.temperature_mode, view.ui_language) == (defaults["temperature_mode"], defaults["ui_language"])


def test_the_channel_resolver_inherits_aifreds_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import config

    monkeypatch.setattr(rs, "load_runtime_settings", lambda: _view(aifred={"model_id": "big"}))
    assert config.get_effective_model_from_settings("sokrates") == "big"
    assert config.get_effective_model_from_settings("automatik") == "big"
