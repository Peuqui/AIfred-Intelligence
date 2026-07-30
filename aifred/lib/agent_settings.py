"""Per-agent tuning-settings access — SSOT for attribute addressing.

Phase 1 of the agent-settings refactor: every code path that addresses
per-agent tuning fields (model, sampling, thinking, speed, manual context)
goes through these helpers. The helpers own the naming scheme INCLUDING its
historical asymmetries, so Phase 2 (dict storage) only has to change this
module — not the call-sites.

The returned names are valid both as Reflex-state attributes AND as flat
``settings.json`` keys (the persistence layer uses the same names).
"""

from __future__ import annotations

from typing import Any

# The four agents with own state vars. Everything else (custom agents from
# agents.json) shares AIfred's tuning bucket — they run on AIfred's model.
CANONICAL_AGENTS = ("aifred", "sokrates", "salomo", "vision")

_MISSING = object()

# Per-agent tuning fields persisted flat in settings.json — the keys equal
# the state-attr names (see agent_attr, incl. the aifred-temperature
# asymmetry: agent_attr("aifred", "temperature") == "temperature").
# Reload note: _reload_settings_from_file deliberately reloads only a
# subset of these (sampling + personality) — see the comments there.
PER_AGENT_PERSISTED_FIELDS = (
    "personality",
    "reasoning",
    "thinking",
    "reasoning_effort",
    "temperature",
    "top_k",
    "top_p",
    "min_p",
    "repeat_penalty",
    "speed_mode",
)


def settings_agent(agent: str) -> str:
    """Map an agent id to the agent whose tuning bucket applies.

    Canonical agents own their bucket; registered custom agents share
    AIfred's. Unregistered names raise (typo guard).
    """
    if agent in CANONICAL_AGENTS:
        return agent
    from .agent_config import get_agent_config
    if get_agent_config(agent) is None:
        raise ValueError(
            f"Unknown agent: {agent}. Must be one of {CANONICAL_AGENTS} "
            f"or a registered custom agent."
        )
    return "aifred"


def agent_attr(agent: str, field: str) -> str:
    """SSOT: (agent, field) → state-attribute / settings.json key.

    Encapsulates the naming asymmetries:
    - AIfred's temperature is the global ``temperature`` (no ``aifred_`` prefix)
    - Vision's manual context is ``vision_num_ctx(_enabled)`` instead of
      ``num_ctx_manual_vision(_enabled)``
    """
    agent = settings_agent(agent)
    if field == "temperature" and agent == "aifred":
        return "temperature"
    if field == "num_ctx_manual":
        return "vision_num_ctx" if agent == "vision" else f"num_ctx_manual_{agent}"
    if field == "num_ctx_manual_enabled":
        return (
            "vision_num_ctx_enabled" if agent == "vision"
            else f"num_ctx_manual_{agent}_enabled"
        )
    return f"{agent}_{field}"


def get_agent_setting(state: Any, agent: str, field: str, default: Any = _MISSING) -> Any:
    """Read a per-agent tuning value from state (or any attr container).

    Without ``default`` a missing attribute raises AttributeError — loud,
    no silent fallback.
    """
    attr = agent_attr(agent, field)
    if default is _MISSING:
        return getattr(state, attr)
    return getattr(state, attr, default)


def set_agent_setting(state: Any, agent: str, field: str, value: Any) -> None:
    """Write a per-agent tuning value to state."""
    setattr(state, agent_attr(agent, field), value)


def get_agent_base_model_id(state: Any, agent: str) -> str:
    """Base model ID (no variant suffix) with inheritance.

    Agents without an own model (empty ``*_model_id``, and all custom
    agents) share AIfred's LLM — the single inheritance rule that was
    previously duplicated as ``... or state.aifred_model_id`` at several
    call-sites.
    """
    own: str = get_agent_setting(state, agent, "model_id", "")
    return own or getattr(state, "aifred_model_id", "")
