"""Per-agent runtime tuning — the dict-keyed storage model (Phase 2).

One ``AgentTuning`` instance per agent id lives in
``RuntimeSettings.agent_tuning: dict[str, AgentTuning]``. The permanent agents get
their buckets from :func:`default_agent_tuning`; every other agent gets one from
``runtime_settings.registered_agent_tuning`` (see ``agent_settings.settings_agent``).

This module stays dependency-free within aifred (only ``config``), so it can be
imported at module level everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    DEFAULT_MIN_P,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    VISION_DEFAULT_MIN_P,
    VISION_DEFAULT_REPEAT_PENALTY,
    VISION_DEFAULT_TEMPERATURE,
    VISION_DEFAULT_TOP_K,
    VISION_DEFAULT_TOP_P,
)

# The agents that are always there: AIfred (the base agent) and Vision (a system agent). Every other agent of
# agents.json gets an equal bucket at runtime (runtime_settings.load_runtime_settings); until 08.11.2026 Sokrates and
# Salomo were fixed here, because the Reflex state reached into their buckets by name.
PERMANENT_AGENTS = ("aifred", "vision")


@dataclass
class AgentTuning:
    """Runtime tuning for one agent (model, sampling, thinking, speed, ctx).

    ``model_id`` is always the BASE id (no variant suffix); empty means
    "inherit AIfred's model". Every agent — including aifred — owns its
    ``temperature`` here (SSOT, no global state var).
    """

    # Model selection (display name + base id)
    model: str = ""
    model_id: str = ""

    # Speed variant (llamacpp only)
    speed_mode: bool = False
    has_speed_variant: bool = False

    # Prompt toggles
    personality: bool = True
    reasoning: bool = True
    # Thinking choice per model this agent runs on: model key → "off", "on"
    # (template default level) or an effort level. Read and written only
    # through agent_settings (thinking_mode / set_thinking_mode); a model
    # without an entry thinks as the agent's agents.json default says.
    thinking_by_model: dict[str, str] = field(default_factory=dict)

    # Temperature: every agent answers with its own
    temperature: float = DEFAULT_TEMPERATURE

    # Sampling
    top_k: int = DEFAULT_TOP_K
    top_p: float = DEFAULT_TOP_P
    min_p: float = DEFAULT_MIN_P
    repeat_penalty: float = DEFAULT_REPEAT_PENALTY

    # Model metadata (runtime, not persisted)
    rope_factor: float = 1.0
    max_context: int = 0
    is_hybrid: bool = False
    supports_thinking: bool | None = None

    # Manual context override (persisted for vision only)
    num_ctx_manual: int = 4096
    num_ctx_manual_enabled: bool = False


def default_tuning(agent: str) -> AgentTuning:
    """Fresh tuning bucket with the agent-specific defaults."""
    if agent == "vision":
        return AgentTuning(
            reasoning=False,
            temperature=VISION_DEFAULT_TEMPERATURE,
            top_k=VISION_DEFAULT_TOP_K,
            top_p=VISION_DEFAULT_TOP_P,
            min_p=VISION_DEFAULT_MIN_P,
            repeat_penalty=VISION_DEFAULT_REPEAT_PENALTY,
            num_ctx_manual=32768,
        )
    return AgentTuning()


def default_agent_tuning() -> dict[str, AgentTuning]:
    """The buckets of the agents that are always there; the others come from agents.json."""
    return {agent: default_tuning(agent) for agent in PERMANENT_AGENTS}

