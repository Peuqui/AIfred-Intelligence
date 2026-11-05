"""Thinking choice per agent and model (agent_settings SSOT, Peuqui 05.11.2026).

Every row (AIfred, the agents, Vision) keeps one choice per model; switching a
row's model reads that model's own entry, a model without one thinks as the
agent's agents.json default says. Image descriptions are the vision agent's
task: they think as the vision row says for the model that describes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aifred.lib.agent_config import AgentConfig
from aifred.lib.agent_settings import (
    THINKING_OFF,
    THINKING_ON,
    persisted_thinking_mode,
    set_thinking_mode,
    thinking_fields,
    thinking_mode,
    thinking_model_key,
)
from aifred.lib.agent_tuning import AgentTuning, default_tuning
from aifred.lib.llm_client import build_llm_options

GLM = "GLM-5.3-Flash-NVFP4-DFlash2-vllm"
SMALL_VL = "Qwen3VL-4B-Instruct-Q8_0"


class _State:
    backend_type = "vllm"

    def __init__(self) -> None:
        self.agent_tuning = {agent: default_tuning(agent) for agent in ("aifred", "sokrates", "vision")}


def _config(thinking: bool) -> AgentConfig:
    return AgentConfig(display_name="X", emoji="", description="", role="main",
                       toggles={"personality": True, "reasoning": False, "thinking": thinking})


@pytest.fixture
def agents_default_on_except_vision():
    """agents.json defaults: every agent thinks, the vision agent does not."""
    with patch("aifred.lib.agent_config.get_agent_config",
               side_effect=lambda agent: _config(agent != "vision")):
        yield


class TestModelKey:
    @pytest.mark.parametrize("profile", [
        SMALL_VL,
        f"{SMALL_VL}-visiond",
        f"{SMALL_VL}-visiond-gpu3",
        f"{SMALL_VL}-tts-qwen3local",
        f"{SMALL_VL}-tts-qwen3local-vlm-qwen3vl4b",
        f"{SMALL_VL}-speed",
    ])
    def test_every_variant_is_its_base_model(self, profile):
        assert thinking_model_key(profile) == thinking_model_key(SMALL_VL)

    def test_a_describer_of_another_machine_is_the_same_model(self):
        from aifred.lib.remote_describers import RemoteHost
        aragon = RemoteHost("Aragon", "http://aragon:11435", "http://aragon:5070", "token")
        with patch("aifred.lib.remote_describers.remote_hosts", return_value=[aragon]):
            assert thinking_model_key(f"Aragon/{SMALL_VL}-visiond") == thinking_model_key(SMALL_VL)

    def test_an_unknown_machine_prefix_stays_part_of_the_model(self):
        with patch("aifred.lib.remote_describers.remote_hosts", return_value=[]):
            assert thinking_model_key("vendor/some-model") == "vendor/some-model"


class TestChoicePerModel:
    def test_a_model_without_a_choice_follows_the_agents_json_default(self, agents_default_on_except_vision):
        state = _State()
        assert thinking_mode(state, "aifred", GLM) == THINKING_ON
        assert thinking_mode(state, "vision", SMALL_VL) == THINKING_OFF

    def test_each_model_keeps_its_own_choice(self, agents_default_on_except_vision):
        state = _State()
        set_thinking_mode(state, "vision", GLM, "high")
        set_thinking_mode(state, "vision", SMALL_VL, THINKING_OFF)
        # Switching the row's model back and forth reads each model's own entry.
        assert thinking_mode(state, "vision", GLM) == "high"
        assert thinking_mode(state, "vision", SMALL_VL) == THINKING_OFF
        assert thinking_mode(state, "vision", GLM) == "high"

    def test_rows_are_independent_on_the_same_model(self, agents_default_on_except_vision):
        # GLM as main model with "max" for chat, GLM in the vision row with "high" for images.
        state = _State()
        set_thinking_mode(state, "aifred", GLM, "max")
        set_thinking_mode(state, "vision", GLM, "high")
        assert thinking_mode(state, "aifred", GLM) == "max"
        assert thinking_mode(state, "vision", GLM) == "high"
        assert thinking_mode(state, "sokrates", GLM) == THINKING_ON

    def test_a_variant_reads_the_choice_of_its_base_model(self, agents_default_on_except_vision):
        state = _State()
        set_thinking_mode(state, "vision", SMALL_VL, THINKING_ON)
        assert thinking_mode(state, "vision", f"{SMALL_VL}-visiond-gpu1") == THINKING_ON

    def test_the_persisted_path_reads_the_same_choice(self, agents_default_on_except_vision):
        state = _State()
        set_thinking_mode(state, "vision", GLM, "low")
        settings = {"agent_tuning": {"vision": {"thinking_by_model": state.agent_tuning["vision"].thinking_by_model}}}
        assert persisted_thinking_mode(settings, "vision", GLM) == "low"
        assert persisted_thinking_mode(settings, "vision", SMALL_VL) == THINKING_OFF
        assert persisted_thinking_mode({}, "aifred", GLM) == THINKING_ON


class TestFields:
    def test_modes_map_to_llm_options_fields(self):
        assert thinking_fields(THINKING_OFF) == (False, None)
        assert thinking_fields(THINKING_ON) == (True, None)
        assert thinking_fields("high") == (True, "high")


class TestBuildLlmOptions:
    def test_options_carry_the_choice_on_the_requested_model(self, agents_default_on_except_vision):
        state = _State()
        set_thinking_mode(state, "aifred", GLM, "high")
        options = build_llm_options(state, "aifred", GLM, 0.7, 8192)  # type: ignore[arg-type]
        assert (options.enable_thinking, options.reasoning_effort) == (True, "high")
        other = build_llm_options(state, "aifred", SMALL_VL, 0.7, 8192)  # type: ignore[arg-type]
        assert (other.enable_thinking, other.reasoning_effort) == (True, None)

    def test_hub_path_reads_settings_json(self, agents_default_on_except_vision):
        settings = {"backend_type": "vllm",
                    "agent_tuning": {"aifred": {"thinking_by_model": {thinking_model_key(GLM): THINKING_OFF}}}}
        with patch("aifred.lib.settings.load_settings", return_value=settings):
            options = build_llm_options(None, "aifred", GLM, 0.7, 8192)
        assert (options.enable_thinking, options.reasoning_effort) == (False, None)


def test_tuning_holds_no_second_thinking_field():
    # One truth: the per-model map is the only thinking state of a row.
    fields = AgentTuning.__dataclass_fields__
    assert "thinking_by_model" in fields
    assert "thinking" not in fields and "reasoning_effort" not in fields
