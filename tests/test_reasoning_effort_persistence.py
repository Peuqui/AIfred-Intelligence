"""Regression: ein Backend-Wechsel darf die gewaehlte Reasoning-Tiefe nicht loeschen.

Bug (2026-09-07, Peuqui): ``_load_agent_reasoning_levels`` las eine leere
Stufenliste als "das Modell kann keine einzige Stufe" und verwarf
``reasoning_effort``. Leer ist die Liste aber auch, wenn die Stufen schlicht
unbekannt sind — Ollama- und Cloud-Modelle haben keinen llama-swap-Eintrag.
Da ``_save_settings`` sofort schreibt, war die Wahl endgueltig weg.

Getroffen hat es nur AIfred: Agenten auf "(wie AIfred-LLM)" haben keine eigene
``model_id``, und ``_load_agent_model_params`` steigt davor schon aus.
"""

from unittest.mock import patch

from aifred.lib.agent_tuning import AgentTuning
from aifred.state._agent_config_mixin import AgentConfigMixin

LEVELS = ["low", "medium", "xhigh"]


class _FakeState:
    """Minimalzustand mit den beiden echten Methoden unter Test."""

    _load_agent_reasoning_levels = AgentConfigMixin._load_agent_reasoning_levels
    _effective_reasoning_levels = AgentConfigMixin._effective_reasoning_levels

    def __init__(self, backend_type: str, model_id: str, effort: str):
        self.backend_type = backend_type
        self.agent_tuning = {
            "aifred": AgentTuning(model_id=model_id, reasoning_effort=effort),
        }
        self.saved = False
        self.debug_lines: list[str] = []

    def _save_settings(self) -> None:
        self.saved = True

    def add_debug(self, message: str) -> None:
        self.debug_lines.append(message)


def _run(state: _FakeState, *, cached_levels, resolved=None):
    """Ruft die Validierung mit gemocktem Template-/Cache-Zugriff.

    ``cached_levels`` ist die Cache-Antwort: ``None`` = nie analysiert,
    ``[]`` = analysiert und ohne steuerbare Stufen.
    """
    resolved = LEVELS if resolved is None else resolved
    with patch(
        "aifred.lib.gguf_utils.resolve_reasoning_levels", return_value=resolved,
    ), patch(
        "aifred.lib.model_vram_cache.get_reasoning_default_for_model",
        return_value="xhigh",
    ), patch(
        "aifred.lib.model_vram_cache.get_reasoning_levels_for_model",
        return_value=cached_levels,
    ):
        state._load_agent_reasoning_levels("aifred", state.agent_tuning["aifred"].model_id)
    return state.agent_tuning["aifred"].reasoning_effort


class TestEffortSurvivesUnknownLevels:
    def test_ollama_backend_keeps_effort(self):
        # DER Bugfall: Ollama hat keinen llama-swap-Eintrag, also keine
        # Stufeninformation — "medium" muss den Wechsel ueberleben.
        state = _FakeState("ollama", "irgendein-ollama-modell", "medium")
        assert _run(state, cached_levels=None, resolved=[]) == "medium"
        assert state.saved is False

    def test_unanalyzed_llamaswap_model_keeps_effort(self):
        # Noch nie analysiert (Cache liefert None) → keine Information,
        # also nicht anfassen. resolve_reasoning_levels persistiert in dem
        # Fall bewusst nichts und laesst einen spaeteren Versuch zu.
        state = _FakeState("vllm", "Frisch-Eingetragen-vllm", "medium")
        assert _run(state, cached_levels=None, resolved=[]) == "medium"
        assert state.saved is False


class TestEffortValidatedWhenLevelsKnown:
    def test_supported_effort_survives(self):
        state = _FakeState("vllm", "Qwen3.8-Flash-Next-vllm", "medium")
        assert _run(state, cached_levels=LEVELS) == "medium"
        assert state.saved is False

    def test_unsupported_effort_is_cleared(self):
        # Modell ohne steuerbare Stufen (analysiert, Ergebnis leer) —
        # hier ist das Verwerfen richtig, und es wird protokolliert.
        state = _FakeState("vllm", "DeepSeek-V4-Flash-vllm", "medium")
        assert _run(state, cached_levels=[], resolved=[]) == ""
        assert state.saved is True
        assert any("medium" in line for line in state.debug_lines)

    def test_effort_outside_known_levels_is_cleared(self):
        state = _FakeState("vllm", "Qwen3.8-Flash-Next-vllm", "max")
        assert _run(state, cached_levels=LEVELS) == ""
        assert state.saved is True
