"""A Reflex tab takes over what another writer (the Vue app's quick choices) left in settings.json: the
whole persisted tuning and the backend's models, read by the same rules as the server
(runtime_settings). 07.11.2026: the reload took only sampling and persona; reasoning, thinking,
speed and the models stayed as the tab had them, and its next save wrote them back over the change."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from aifred.lib import runtime_settings
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.state._settings_mixin import SettingsMixin

if TYPE_CHECKING:
    # For mypy the stand-in is the mixin, so its fields and borrowed methods are checked against the
    # real ones. At runtime it stays a plain class: a Reflex state refuses undeclared attributes.
    _StateBase = SettingsMixin
else:
    _StateBase = object

BIG, SMALL = "Big-30B", "Small-4B"


class _Tab(_StateBase):
    _reload_agent_choices = SettingsMixin._reload_agent_choices

    def __init__(self) -> None:
        self.backend_type = "llamacpp"
        self.agent_tuning = default_agent_tuning()
        self.agent_tuning["aifred"].model_id = SMALL
        self.automatik_model_id = ""
        self.automatik_model = ""
        self.vision_models_cache = ["Eyes"]
        self.available_vision_models_rich = []
        self.badges_built = 0

    def _label_for(self, agent: str, model_id: str) -> str:
        return f"<{model_id}>" if model_id else ""

    def _build_vision_rich(self, vision_model_ids: list[str]) -> list[dict[str, str]]:
        self.badges_built += 1
        return []


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """The models whose properties were loaded from the caches (a stand-in for the caches)."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(runtime_settings, "load_agent_model_params", lambda _target, agent, model: calls.append((agent, model)))
    return calls


def _settings() -> dict[str, Any]:
    return {
        "agent_tuning": {"sokrates": {"reasoning": False, "speed_mode": True, "thinking_by_model": {BIG: "off"}, "top_k": 7}},
        "backend_models": {"llamacpp": {"aifred": BIG, "sokrates": "", "salomo": SMALL, "automatik": SMALL}},
    }


def test_the_tab_takes_every_persisted_field_and_the_models(loaded: list[tuple[str, str]]) -> None:
    tab = _Tab()
    tab._reload_agent_choices(_settings())
    sokrates = tab.agent_tuning["sokrates"]
    assert (sokrates.reasoning, sokrates.speed_mode, sokrates.thinking_by_model, sokrates.top_k) == (False, True, {BIG: "off"}, 7)
    assert tab.agent_tuning["aifred"].model_id == BIG and tab.agent_tuning["aifred"].model == f"<{BIG}>"
    assert tab.agent_tuning["salomo"].model_id == SMALL and tab.automatik_model == f"<{SMALL}>"
    assert sorted(loaded) == [("aifred", BIG), ("salomo", SMALL)]            # only the changed models
    assert tab.badges_built == 1                                                # AIfred's model changed


def test_an_unchanged_file_loads_no_model_again(loaded: list[tuple[str, str]]) -> None:
    tab = _Tab()
    tab._reload_agent_choices(_settings())
    loaded.clear()
    tab._reload_agent_choices(_settings())
    assert loaded == [] and tab.badges_built == 1


def test_without_models_for_the_backend_the_models_stay(loaded: list[tuple[str, str]]) -> None:
    tab = _Tab()
    tab._reload_agent_choices({"agent_tuning": {"aifred": {"personality": False}}})
    assert tab.agent_tuning["aifred"].model_id == SMALL and not tab.agent_tuning["aifred"].personality
    assert loaded == []
