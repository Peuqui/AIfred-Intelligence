"""Per-agent tuning-settings access — SSOT for bucket addressing.

Phase 2 of the agent-settings refactor: per-agent tuning lives in
``state.agent_tuning: dict[str, AgentTuning]`` (see ``agent_tuning.py``).
Every code path that reads or writes per-agent tuning goes through these
helpers; they own the remaining asymmetry (AIfred's temperature is the
global ``state.temperature``), so call-sites never special-case agents.
"""

from __future__ import annotations

from typing import Any

from .agent_tuning import CANONICAL_AGENTS, default_tuning

__all__ = [
    "CANONICAL_AGENTS",
    "PERSISTED_TUNING_FIELDS",
    "settings_agent",
    "model_owner",
    "get_agent_setting",
    "set_agent_setting",
    "get_agent_base_model_id",
    "get_persisted_tuning",
]

_MISSING = object()

# Tuning fields persisted per agent under settings.json["agent_tuning"].
# Asymmetries handled in _save_settings/_load: aifred's temperature is the
# global "temperature" key; num_ctx_manual(+_enabled) persists only for
# vision (chat agents reset on restart — deliberate, see llm_params UI hint).
PERSISTED_TUNING_FIELDS = (
    "personality",
    "reasoning",
    "thinking",
    "reasoning_effort",
    "temperature",
    "temperature_offset",
    "top_k",
    "top_p",
    "min_p",
    "repeat_penalty",
    "speed_mode",
)


def settings_agent(agent: str) -> str:
    """Validate an agent id and return the id of its tuning bucket.

    Every registered agent (canonical or custom) owns its bucket — buckets
    for new agents materialize on first write, reads before that serve the
    agent's defaults. Unregistered names raise (typo guard).
    """
    if agent in CANONICAL_AGENTS:
        return agent
    from .agent_config import get_agent_config
    if get_agent_config(agent) is None:
        raise ValueError(
            f"Unknown agent: {agent}. Must be one of {CANONICAL_AGENTS} "
            f"or a registered custom agent."
        )
    return agent


def _tuning_bucket(state: Any, agent: str) -> Any:
    """The agent's AgentTuning bucket, created on demand for new agents."""
    tuning = state.agent_tuning.get(agent)
    if tuning is None:
        tuning = default_tuning(agent)
        state.agent_tuning[agent] = tuning
    return tuning


def get_agent_setting(state: Any, agent: str, field: str, default: Any = _MISSING) -> Any:
    """Read a per-agent tuning value from state.

    Agents without a bucket yet (fresh custom agents) read their
    ``default_tuning`` values. Without ``default`` an unknown FIELD raises
    AttributeError — loud, no silent fallback.
    """
    agent = settings_agent(agent)
    if agent == "aifred" and field == "temperature":
        return getattr(state, "temperature")
    tuning = state.agent_tuning.get(agent)
    if tuning is None:
        tuning = default_tuning(agent)
    if default is _MISSING:
        return getattr(tuning, field)
    return getattr(tuning, field, default)


def set_agent_setting(state: Any, agent: str, field: str, value: Any) -> None:
    """Write a per-agent tuning value to state."""
    agent = settings_agent(agent)
    if agent == "aifred" and field == "temperature":
        setattr(state, "temperature", value)
        return
    setattr(_tuning_bucket(state, agent), field, value)


def model_owner(state: Any, agent: str) -> str:
    """Agent whose model actually loads for this agent.

    An agent without an own model shares AIfred's LLM — and with it
    AIfred's model-bound toggles (speed_mode, has_speed_variant). Resolving
    those from the owner keeps browser, Hub and context lookup on the SAME
    llama-swap variant (a mismatch double-loads base ↔ -speed).
    """
    agent = settings_agent(agent)
    if get_agent_setting(state, agent, "model_id", ""):
        return agent
    return "aifred"


def get_agent_base_model_id(state: Any, agent: str) -> str:
    """Base model ID (no variant suffix) with inheritance.

    Agents without an own model (empty ``model_id``) share AIfred's LLM —
    the single inheritance rule.
    """
    own: str = get_agent_setting(state, agent, "model_id", "")
    if own:
        return own
    inherited: str = get_agent_setting(state, "aifred", "model_id", "")
    return inherited


def get_persisted_tuning(settings: dict, agent: str, field: str, default: Any) -> Any:
    """Read a per-agent tuning value from a loaded settings.json dict.

    For code paths without a State instance (Message Hub workers). Applies
    the same aifred-temperature asymmetry as :func:`get_agent_setting`.
    """
    try:
        agent = settings_agent(agent)
    except ValueError:
        return default
    if agent == "aifred" and field == "temperature":
        return settings.get("temperature", default)
    return settings.get("agent_tuning", {}).get(agent, {}).get(field, default)
