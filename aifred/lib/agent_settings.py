"""Per-agent tuning-settings access — SSOT for bucket addressing.

Phase 2 of the agent-settings refactor: per-agent tuning lives in
``state.agent_tuning: dict[str, AgentTuning]`` (see ``agent_tuning.py``).
Every code path that reads or writes per-agent tuning goes through these
helpers, so call-sites never special-case agents.
"""

from __future__ import annotations

from typing import Any

from .agent_tuning import CANONICAL_AGENTS, default_tuning

__all__ = [
    "CANONICAL_AGENTS",
    "PERSISTED_TUNING_FIELDS",
    "THINKING_OFF",
    "THINKING_ON",
    "effective_thinking_mode",
    "get_agent_base_model_id",
    "get_agent_setting",
    "get_persisted_tuning",
    "model_owner",
    "persisted_thinking_mode",
    "resolve_agent_temperature",
    "set_agent_setting",
    "set_thinking_mode",
    "settings_agent",
    "thinking_fields",
    "thinking_mode",
    "thinking_model_key",
]

_MISSING = object()

# Tuning fields persisted per agent under settings.json["agent_tuning"].
# Asymmetry handled in _save_settings/_load: num_ctx_manual(+_enabled)
# persists only for vision (chat agents reset on restart — deliberate,
# see llm_params UI hint).
PERSISTED_TUNING_FIELDS = (
    "personality",
    "reasoning",
    "thinking_by_model",
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
    tuning = state.agent_tuning.get(agent)
    if tuning is None:
        tuning = default_tuning(agent)
    if default is _MISSING:
        return getattr(tuning, field)
    return getattr(tuning, field, default)


def set_agent_setting(state: Any, agent: str, field: str, value: Any) -> None:
    """Write a per-agent tuning value to state."""
    agent = settings_agent(agent)
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


def resolve_agent_temperature(state: Any, agent: str) -> float:
    """SSOT for the effective temperature of an agent inference.

    Manual mode: every agent uses its own configured temperature from its
    ``agent_tuning`` bucket. Auto mode: the temperature of the agent with the
    role main as base, the agents with the roles critic and judge add their
    offset on top (agents.json decides the roles, not the names).
    """
    if state.temperature_mode == "manual":
        return float(get_agent_setting(state, agent, "temperature"))
    from .agent_config import (
        ROLE_CRITIC,
        ROLE_JUDGE,
        ROLE_MAIN,
        agent_for_role,
        get_agent_config,
    )
    config = get_agent_config(agent)
    if config is None:
        raise ValueError(f"Unknown agent: {agent}")
    base = float(get_agent_setting(state, agent_for_role(ROLE_MAIN), "temperature"))
    if config.role in (ROLE_CRITIC, ROLE_JUDGE):
        return min(1.0, base + float(get_agent_setting(state, agent, "temperature_offset")))
    return base


def get_persisted_tuning(settings: dict, agent: str, field: str, default: Any) -> Any:
    """Read a per-agent tuning value from a loaded settings.json dict.

    For code paths without a State instance (Message Hub workers).
    """
    try:
        agent = settings_agent(agent)
    except ValueError:
        return default
    return settings.get("agent_tuning", {}).get(agent, {}).get(field, default)


# ── Thinking choice per agent and model (SSOT) ───────────────────────────
# One truth: ``AgentTuning.thinking_by_model`` maps a model to the agent's
# choice on it. Every request (chat, Symposion, hub, sub-agents, image
# descriptions) reads it through thinking_mode / persisted_thinking_mode with
# the model the request goes to, the dropdown writes it through
# set_thinking_mode. Switching an agent's model needs no copying: the other
# model simply has its own entry.

THINKING_OFF = "off"
THINKING_ON = "on"  # thinking with the level the chat template picks itself


def thinking_model_key(model_id: str) -> str:
    """The model a request goes to, as the thinking choice knows it: its base
    model. A describer of another machine (``<machine>/<model>``) and every
    llama-swap variant (``-visiond``, ``-tts-…``, ``-vlm-…``, ``-speed``) is
    the same model — the choice belongs to the model, not to where or how it
    is loaded."""
    from .remote_describers import split
    from .vision_utils import strip_variant_suffixes
    remote = split(model_id)
    return strip_variant_suffixes(remote[1] if remote else model_id)


def _default_thinking_mode(agent: str) -> str:
    """The agent's thinking default from agents.json (``toggles.thinking``),
    for a model it never had a choice on."""
    from .agent_config import get_agent_config
    config = get_agent_config(agent)
    if config is None:
        raise ValueError(f"Unknown agent: {agent}")
    return THINKING_ON if config.toggles.get("thinking", True) else THINKING_OFF


def effective_thinking_mode(mode: str, model_id: str) -> str:
    """The choice ``mode`` as the model can carry it out, judged by its chat
    template (gguf_utils.resolve_reasoning_info): a model that cannot think
    stays off, "off" on a model that always thinks is its template default,
    a level its template does not know (any more) is the template default.
    The stored choice itself stays untouched. An unknown model (no llama-swap
    entry: Ollama, cloud, another machine) runs as chosen."""
    from .gguf_utils import resolve_reasoning_info
    info = resolve_reasoning_info(model_id)
    if info is None:
        return mode
    if not info.thinks:
        return THINKING_OFF
    if mode == THINKING_OFF:
        return THINKING_OFF if info.switchable else THINKING_ON
    if mode == THINKING_ON or mode in info.levels:
        return mode
    return THINKING_ON


def _mode_in(choices: dict[str, str], agent: str, model_id: str) -> str:
    mode = choices.get(thinking_model_key(model_id))
    return effective_thinking_mode(_default_thinking_mode(agent) if mode is None else mode, model_id)


def thinking_mode(state: Any, agent: str, model_id: str) -> str:
    """The agent's thinking choice on ``model_id`` as the model can carry it
    out (:func:`effective_thinking_mode`): THINKING_OFF, THINKING_ON or an
    effort level."""
    choices: dict[str, str] = get_agent_setting(state, agent, "thinking_by_model")
    return _mode_in(choices, agent, model_id)


def persisted_thinking_mode(settings: dict, agent: str, model_id: str) -> str:
    """:func:`thinking_mode` from a loaded settings.json dict (paths without a
    State: Message Hub, scheduler, image describers)."""
    choices: dict[str, str] = get_persisted_tuning(settings, agent, "thinking_by_model", {})
    return _mode_in(choices, agent, model_id)


def set_thinking_mode(state: Any, agent: str, model_id: str, mode: str) -> None:
    """Store the agent's thinking choice on ``model_id``."""
    choices = dict(get_agent_setting(state, agent, "thinking_by_model"))
    choices[thinking_model_key(model_id)] = mode
    set_agent_setting(state, agent, "thinking_by_model", choices)


def thinking_fields(mode: str) -> tuple[bool, str | None]:
    """``(enable_thinking, reasoning_effort)`` of a choice for LLMOptions;
    ``None`` lets the chat template pick its default level."""
    if mode == THINKING_OFF:
        return False, None
    if mode == THINKING_ON:
        return True, None
    return True, mode
