"""The agents' quick choices (lib/agent_choices.py, api/agent_choices.py): a model change brings the
model's sampling to the agent and to every agent on AIfred's model, speed goes off without a -speed
variant, the Vision agent keeps its sampling, settings.json gets only what changed, the API offers
only what the row can do. The model caches, the llama-swap file and agents.json of this machine stay
out: stand-ins for them."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_choices, config, settings
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.gguf_utils import ReasoningInfo
from aifred.lib.runtime_settings import RuntimeSettings

BIG, SMALL, EYES = "Big-30B", "Small-4B", "Eyes-4B-VL"
# Big brings its own sampling and a -speed variant; Small neither.
LLAMASWAP = {BIG: {"full_cmd": "llama-server -m big.gguf --temp 0.6 --top-k 20 --top-p 0.95 --min-p 0.01 --repeat-penalty 1.05"}}
SPEEDY = {BIG}
CATALOG = {BIG: "Big (30B)", SMALL: "Small (4B)", EYES: "Eyes (4B)"}


def _view() -> RuntimeSettings:
    tuning = default_agent_tuning()
    tuning["aifred"].model_id = SMALL
    tuning["vision"].model_id = EYES
    return RuntimeSettings(
        backend_type="llamacpp", backend_url="http://llama-swap", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=tuning, tts_autoplay=False, tts_agents={},
        max_debate_rounds=3, consensus_type="majority",
    )


@pytest.fixture(autouse=True)
def stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canonical agents only, the model caches and llama-swap's file as stand-ins."""
    from aifred.lib import calibration

    monkeypatch.setattr(agent_choices, "chat_agents", lambda: [("aifred", "AIfred", "🎩"), ("sokrates", "Sokrates", "🏛️"), ("salomo", "Salomo", "👑")])

    def model_params(target: RuntimeSettings, agent: str, model_id: str) -> None:
        target.agent_tuning[agent].has_speed_variant = model_id in SPEEDY

    monkeypatch.setattr(agent_choices, "load_agent_model_params", model_params)
    monkeypatch.setattr(calibration, "parse_llamaswap_config", lambda _path: LLAMASWAP)


def test_aifreds_model_brings_its_sampling_to_him_and_every_agent_on_his_model() -> None:
    view = _view()
    view.agent_tuning["salomo"].model_id = SMALL                 # Salomo has a model of his own
    view.agent_tuning["salomo"].temperature = 0.3
    vision_before = dataclasses.replace(view.agent_tuning["vision"])
    reset = agent_choices.choose_model(view, "aifred", BIG, CATALOG[BIG])
    assert reset == ["aifred", "sokrates"]
    for agent in ("aifred", "sokrates"):
        tuning = view.agent_tuning[agent]
        assert (tuning.temperature, tuning.top_k, tuning.top_p, tuning.min_p, tuning.repeat_penalty) == (0.6, 20, 0.95, 0.01, 1.05)
    assert view.agent_tuning["salomo"].temperature == 0.3 and view.agent_tuning["vision"] == vision_before
    assert view.agent_tuning["aifred"].model == "Big (30B)" and view.agent_tuning["aifred"].has_speed_variant


def test_speed_goes_off_with_a_model_without_speed_variant_and_on_aifreds_model() -> None:
    view = _view()
    agent_choices.choose_model(view, "sokrates", BIG, CATALOG[BIG])
    view.agent_tuning["sokrates"].speed_mode = True
    agent_choices.choose_model(view, "sokrates", SMALL, CATALOG[SMALL])
    assert not view.agent_tuning["sokrates"].speed_mode
    agent_choices.choose_model(view, "sokrates", BIG, CATALOG[BIG])
    view.agent_tuning["sokrates"].speed_mode = True
    assert agent_choices.choose_model(view, "sokrates", "", "") == ["sokrates"]      # back on AIfred's model
    tuning = view.agent_tuning["sokrates"]
    assert (tuning.model_id, tuning.has_speed_variant, tuning.speed_mode) == ("", False, False)


def test_the_vision_agent_keeps_its_sampling() -> None:
    view = _view()
    top_k = view.agent_tuning["vision"].top_k
    assert agent_choices.choose_model(view, "vision", BIG, CATALOG[BIG]) == []
    assert view.agent_tuning["vision"].top_k == top_k


def test_the_thinking_options_are_values_with_labels() -> None:
    view = _view()
    always = ReasoningInfo(["low", "high", "max"], "max", thinks=True, switchable=False)
    with patch("aifred.lib.agent_config.get_agent_config", return_value=None), \
            patch.object(agent_choices, "thinking_mode", return_value="max"), \
            patch("aifred.lib.gguf_utils.resolve_reasoning_info", return_value=always):
        current, options = agent_choices.thinking_choices(view, "aifred", "de")
    assert [(option.value, option.label) for option in options] == [("low", "low"), ("high", "high"), ("on", "max (Standard)")]
    assert current == "on"                                                   # the template default reads as "on"


def test_saving_writes_the_changed_tuning_and_the_backends_models_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = tmp_path / "settings.json"
    file.write_text(json.dumps({
        "ui_language": "de",
        "agent_tuning": {"salomo": {"temperature": 0.3}, "sokrates": {"temperature": 0.9}},
        "backend_models": {"ollama": {"aifred": "qwen3:8b"}},
    }))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    view = _view()
    view.automatik_model_id = SMALL
    agent_choices.choose_model(view, "aifred", BIG, CATALOG[BIG])
    assert agent_choices.save_choices(view, ["aifred", "sokrates"])
    saved = json.loads(file.read_text())
    assert saved["ui_language"] == "de" and saved["agent_tuning"]["salomo"] == {"temperature": 0.3}
    assert saved["agent_tuning"]["sokrates"]["temperature"] == 0.6 and saved["agent_tuning"]["aifred"]["top_k"] == 20
    assert saved["backend_models"]["ollama"] == {"aifred": "qwen3:8b"}
    assert saved["backend_models"]["llamacpp"]["aifred"] == BIG and saved["backend_models"]["llamacpp"]["automatik"] == SMALL


# ── The endpoints ────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API with a settings.json of its own, the backend's catalog and the vision models as stand-ins."""
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"backend_type": "llamacpp", "backend_models": {"llamacpp": {"aifred": SMALL, "vision": EYES}}}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", file)
    async def catalog(_backend: str, _provider: str) -> tuple[dict[str, str], list[str]]:
        return CATALOG, []

    monkeypatch.setattr("aifred.lib.backend_switch.model_catalog", catalog)

    async def vision_candidates(_target: object, catalog: dict[str, str], _url: str) -> dict[str, str]:
        return {EYES: catalog[EYES]}

    monkeypatch.setattr(agent_choices, "vision_candidates", vision_candidates)
    monkeypatch.setattr(agent_choices, "vision_options", lambda _target, candidates: [agent_choices.ModelOption(m, label) for m, label in candidates.items()])
    with patch("aifred.lib.gguf_utils.resolve_reasoning_info", return_value=None):
        test_client = TestClient(api_app)
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _row(choices: dict[str, Any], agent: str) -> dict[str, Any]:
    """The row of ``agent`` in the JSON of the quick choices."""
    row: dict[str, Any] = next(row for row in choices["agents"] if row["id"] == agent)
    return row


def test_the_quick_choices_list_every_agent_with_its_models(client: TestClient) -> None:
    choices = client.get("/agents/choices").json()
    assert [row["id"] for row in choices["agents"]] == ["aifred", "sokrates", "salomo", "vision"]
    assert _row(choices, "aifred")["model_id"] == SMALL and _row(choices, "sokrates")["model_id"] == ""
    assert [model["id"] for model in choices["models"]] == [BIG, SMALL, EYES]
    assert [model["id"] for model in choices["vision_models"]] == [EYES]
    assert [option["value"] for option in _row(choices, "aifred")["thinking_options"]] == ["off", "on"]


def test_a_change_is_saved_and_answered_with_every_row(client: TestClient) -> None:
    choices = client.patch("/agents/aifred/choices", json={"model_id": BIG, "speed": True, "personality": False}).json()
    aifred = _row(choices, "aifred")
    assert (aifred["model_id"], aifred["speed_available"], aifred["speed"], aifred["personality"]) == (BIG, True, True, False)
    saved = json.loads(settings.SETTINGS_FILE.read_text())
    assert saved["backend_models"]["llamacpp"]["aifred"] == BIG
    assert saved["agent_tuning"]["aifred"]["speed_mode"] is True and saved["agent_tuning"]["sokrates"]["top_k"] == 20
    assert client.patch("/agents/automatik/choices", json={"model_id": SMALL}).status_code == 200
    assert json.loads(settings.SETTINGS_FILE.read_text())["backend_models"]["llamacpp"]["automatik"] == SMALL


def test_the_sampling_changes_and_goes_back_to_the_models_values(client: TestClient) -> None:
    # Until 08.11.2026 only the Reflex settings could set an agent's sampling; Vue had none of it.
    changed = client.patch("/agents/sokrates/choices", json={"sampling": {"temperature": 1.2, "top_k": 100}}).json()
    assert _row(changed, "sokrates")["sampling"]["temperature"] == 1.2 and _row(changed, "sokrates")["sampling"]["top_k"] == 100
    saved = json.loads(settings.SETTINGS_FILE.read_text())["agent_tuning"]["sokrates"]
    assert (saved["temperature"], saved["top_k"]) == (1.2, 100)
    assert changed["sampling_ranges"]["top_k"] == {"min": 0, "max": 200, "step": 1}
    back = _row(client.patch("/agents/sokrates/choices", json={"reset_sampling": True}).json(), "sokrates")["sampling"]
    # Sokrates runs on AIfred's Small, whose entry sets no sampling: llama-server's own values.
    assert (back["top_k"], back["temperature"]) == (config.LLAMASERVER_DEFAULT_TOP_K, config.LLAMASERVER_DEFAULT_TEMPERATURE)


@pytest.mark.parametrize(("agent", "change", "status"), [
    ("nobody", {"personality": True}, 404),
    ("aifred", {"model_id": "gibt-es-nicht"}, 422),
    ("aifred", {"model_id": ""}, 422),                     # AIfred always has a model of his own
    ("vision", {"model_id": BIG}, 422),                     # not a vision model
    ("sokrates", {"speed": True}, 422),                     # on AIfred's Small: no speed variant
    ("sokrates", {"thinking": "max"}, 422),                 # Small offers off and on only
    ("automatik", {"personality": True}, 422),
    ("aifred", {"temperature": 0.5}, 422),                  # no field of the quick choices
    ("aifred", {"sampling": {"top_p": 1.5}}, 422),           # outside its range: refused, not clamped
    ("aifred", {"sampling": {"top_p": 0.5, "top_k": -1}}, 422),  # nothing of a refused change is kept
    ("aifred", {"sampling": {"seed": 1}}, 422),
    ("automatik", {"sampling": {"top_k": 10}}, 422),         # the Automatik's row has a model only
])
def test_a_row_takes_only_what_it_offers(client: TestClient, agent: str, change: dict[str, object], status: int) -> None:
    before = settings.SETTINGS_FILE.read_text()
    assert client.patch(f"/agents/{agent}/choices", json=change).status_code == status
    assert settings.SETTINGS_FILE.read_text() == before
