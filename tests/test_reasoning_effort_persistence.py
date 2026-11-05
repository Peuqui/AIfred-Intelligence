"""Regression: ein Backend- oder Modellwechsel darf die gewählte Denktiefe nicht löschen.

Bug (2026-09-07, Peuqui): Eine leere Stufenliste wurde als "das Modell kann
keine Stufe" gelesen und ``reasoning_effort`` verworfen. Leer ist die Liste aber
auch, wenn die Stufen schlicht unbekannt sind — Ollama- und Cloud-Modelle haben
keinen llama-swap-Eintrag.

Seit 05.11.2026 wird die Wahl je Modell gespeichert und nie umgeschrieben: Was
das Modell davon ausführen kann, entscheidet beim Lesen
``agent_settings.effective_thinking_mode`` anhand seiner Vorlage.
"""

from unittest.mock import patch

import pytest

from aifred.lib.agent_settings import (
    THINKING_OFF,
    THINKING_ON,
    effective_thinking_mode,
    set_thinking_mode,
    thinking_mode,
    thinking_model_key,
)
from aifred.lib.agent_tuning import AgentTuning
from aifred.lib.gguf_utils import ReasoningInfo

QWEN38 = ReasoningInfo(["low", "medium", "xhigh"], "xhigh", thinks=True, switchable=True)
GLM = ReasoningInfo(["low", "high", "max"], "max", thinks=True, switchable=False)
INSTRUCT_VL = ReasoningInfo([], None, thinks=False, switchable=False)


def _with_info(info):
    return patch("aifred.lib.gguf_utils.resolve_reasoning_info", return_value=info)


class TestUnknownModelRunsAsChosen:
    @pytest.mark.parametrize("mode", [THINKING_OFF, THINKING_ON, "medium"])
    def test_no_template_information_keeps_the_choice(self, mode):
        # DER Bugfall: Ollama/Cloud haben keine Stufeninformation.
        with _with_info(None):
            assert effective_thinking_mode(mode, "irgendein-ollama-modell") == mode


class TestTemplateDecides:
    def test_supported_level_survives(self):
        with _with_info(QWEN38):
            assert effective_thinking_mode("medium", "Qwen3.8-vllm") == "medium"

    def test_unknown_level_is_the_template_default(self):
        with _with_info(QWEN38):
            assert effective_thinking_mode("max", "Qwen3.8-vllm") == THINKING_ON

    def test_off_on_a_model_that_always_thinks_is_the_template_default(self):
        with _with_info(GLM):
            assert effective_thinking_mode(THINKING_OFF, "GLM-vllm") == THINKING_ON
            assert effective_thinking_mode("high", "GLM-vllm") == "high"

    @pytest.mark.parametrize("mode", [THINKING_ON, "high"])
    def test_a_model_without_thinking_phase_stays_off(self, mode):
        with _with_info(INSTRUCT_VL):
            assert effective_thinking_mode(mode, "Qwen3VL-4B") == THINKING_OFF


def test_the_stored_choice_is_never_rewritten():
    class _State:
        agent_tuning = {"aifred": AgentTuning(model_id="GLM-vllm")}

    state = _State()
    set_thinking_mode(state, "aifred", "GLM-vllm", THINKING_OFF)
    with _with_info(GLM):
        assert thinking_mode(state, "aifred", "GLM-vllm") == THINKING_ON
    assert state.agent_tuning["aifred"].thinking_by_model == {thinking_model_key("GLM-vllm"): THINKING_OFF}
    with _with_info(None):
        assert thinking_mode(state, "aifred", "GLM-vllm") == THINKING_OFF
