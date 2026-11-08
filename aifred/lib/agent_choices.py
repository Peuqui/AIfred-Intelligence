"""The agents' quick choices: per agent its model, 🎩 persona, 💭 reasoning, 🧠 thinking and ⚡ speed,
the Automatik's model and the Vision agent's (the modal of the Vue app, the agent rows of the Reflex
settings).

The rules hold for both: which thinking options a model offers (values with their labels), which
sampling a model brings along, what a model change does (model properties, speed, sampling of the
agent and of every agent that runs on AIfred's model), and which vision models fit beside the chat
model. The Reflex state applies them to its own fields; the server reads settings.json as
``RuntimeSettings`` (``quick_choices``) and writes only what a choice changed (``save_choices``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent_settings import (
    PERSISTED_TUNING_FIELDS,
    THINKING_OFF,
    THINKING_ON,
    get_agent_base_model_id,
    get_agent_setting,
    set_agent_setting,
    set_thinking_mode,
    thinking_mode,
)
from .config import LLAMASWAP_BACKENDS, SAMPLING_RANGES
from .runtime_settings import RuntimeSettings, TuningTarget, load_agent_model_params


@dataclass(frozen=True)
class Option:
    """A choice as it is stored (``value``) and as the interface shows it (``label``)."""

    value: str
    label: str


@dataclass(frozen=True)
class ModelOption:
    """A model of a list: its id, its label (size, context), for vision models whether it fits beside
    the chat model (``badge`` as the Reflex UI shows it, ``fits``: no swap needed)."""

    id: str
    label: str
    badge: str = ""
    fits: bool = True


@dataclass(frozen=True)
class AgentChoices:
    id: str
    display_name: str
    emoji: str
    model_id: str            # "" = the agent runs on AIfred's model (never for AIfred)
    personality: bool
    reasoning: bool
    thinking: str            # the value of one of ``thinking_options``
    thinking_options: tuple[Option, ...]
    speed_available: bool    # the model has a -speed variant (llama.cpp)
    speed: bool
    sampling: dict[str, float]   # temperature, top_k, top_p, min_p, repeat_penalty (config.SAMPLING_RANGES)
    context: int             # the context window the agent's calls get (research.context_utils.get_agent_num_ctx)
    context_source: str      # where it comes from: manual, the llama-swap YAML, the calibration …
    manual_context_enabled: bool   # a value set by hand applies (Ollama only; a chat agent's until the restart)
    manual_context: int


@dataclass(frozen=True)
class SamplingRange:
    min: float
    max: float
    step: float


@dataclass(frozen=True)
class QuickChoices:
    agents: tuple[AgentChoices, ...]     # AIfred first, the Vision agent last
    automatik_model_id: str              # "" = AIfred's model
    models: tuple[ModelOption, ...]      # for every agent but Vision, and for the Automatik
    vision_models: tuple[ModelOption, ...]
    sampling_ranges: dict[str, SamplingRange]
    manual_context_offered: bool         # the backend takes a context set by hand (Ollama)


# ── Thinking: what the model's template lets the agent choose ────────────


def thinking_choices(target: TuningTarget, agent: str, lang: str) -> tuple[str, list[Option]]:
    """The agent's thinking choice on the model it runs on and the options that model offers:
    "off" where thinking can be turned off, "on" (the template's default level) and the levels where
    it thinks; a model without a thinking phase offers only "off", an unknown model (Ollama, cloud)
    "off" and "on".

    "on" carries the template's default in its label ("An (xhigh)"); that level is no option of its
    own. A model that always thinks names the entry after its level ("max (Standard)") at that
    level's place. A stored level equal to the default reads as "on".
    """
    from .gguf_utils import resolve_reasoning_info
    from .i18n import t

    model_id = get_agent_base_model_id(target, agent)
    info = resolve_reasoning_info(model_id) if model_id else None
    off = Option(THINKING_OFF, t("thinking_mode_off", lang=lang))
    on_label = t("thinking_mode_on", lang=lang)
    if info is not None and info.default:
        on_label = (
            f"{info.default} ({t('thinking_mode_default', lang=lang)})" if not info.switchable
            else f"{on_label} ({info.default})"
        )
    on = Option(THINKING_ON, on_label)

    if info is None:
        options = [off, on]
    elif not info.thinks:
        options = [off]
    elif not info.switchable:
        levels = [on if level == info.default else Option(level, level) for level in info.levels]
        options = levels if info.default in info.levels else [on, *levels]
    else:
        options = [off, on, *(Option(level, level) for level in info.levels if level != info.default)]

    mode = thinking_mode(target, agent, model_id)
    if mode != THINKING_OFF and (mode == THINKING_ON or (info is not None and mode == info.default)):
        mode = THINKING_ON
    return mode, options


def choose_thinking(target: TuningTarget, agent: str, mode: str) -> None:
    """The agent's thinking choice on the model it runs on (a value of ``thinking_choices``)."""
    set_thinking_mode(target, agent, get_agent_base_model_id(target, agent), mode)


# ── Sampling a model brings along ────────────────────────────────────────


def sampling_defaults(target: TuningTarget, agent: str) -> dict[str, float]:
    """The sampling of the model the agent runs on: llama.cpp from the llama-swap entry's flags, vLLM
    from the checkpoint's generation_config.json (the values the calibration measures with), else
    the backend's defaults; the Vision agent has its own. An unreadable generation_config.json is
    reported in the debug console and gives the backend's defaults.
    """
    from .config import (
        DEFAULT_MIN_P,
        DEFAULT_REPEAT_PENALTY,
        DEFAULT_TOP_K,
        DEFAULT_TOP_P,
        LLAMASERVER_DEFAULT_MIN_P,
        LLAMASERVER_DEFAULT_REPEAT_PENALTY,
        LLAMASERVER_DEFAULT_TEMPERATURE,
        LLAMASERVER_DEFAULT_TOP_K,
        LLAMASERVER_DEFAULT_TOP_P,
        LLAMASWAP_CONFIG_PATH,
        VISION_DEFAULT_MIN_P,
        VISION_DEFAULT_REPEAT_PENALTY,
        VISION_DEFAULT_TEMPERATURE,
        VISION_DEFAULT_TOP_K,
        VISION_DEFAULT_TOP_P,
    )

    if agent == "vision":
        defaults: dict[str, float] = {
            "temperature": VISION_DEFAULT_TEMPERATURE, "top_k": VISION_DEFAULT_TOP_K, "top_p": VISION_DEFAULT_TOP_P,
            "min_p": VISION_DEFAULT_MIN_P, "repeat_penalty": VISION_DEFAULT_REPEAT_PENALTY,
        }
    else:
        defaults = {
            "temperature": LLAMASERVER_DEFAULT_TEMPERATURE, "top_k": DEFAULT_TOP_K, "top_p": DEFAULT_TOP_P,
            "min_p": DEFAULT_MIN_P, "repeat_penalty": DEFAULT_REPEAT_PENALTY,
        }
    model_id = get_agent_base_model_id(target, agent)
    if not model_id or target.backend_type not in LLAMASWAP_BACKENDS:
        return defaults

    from .calibration import parse_llamaswap_config

    entry = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(model_id)
    if entry is None:
        return defaults
    if target.backend_type == "llamacpp":
        from .calibration import parse_sampling_from_cmd

        flags = parse_sampling_from_cmd(entry["full_cmd"])
        return {
            "temperature": flags.get("temperature", LLAMASERVER_DEFAULT_TEMPERATURE),
            "top_k": flags.get("top_k", LLAMASERVER_DEFAULT_TOP_K),
            "top_p": flags.get("top_p", LLAMASERVER_DEFAULT_TOP_P),
            "min_p": flags.get("min_p", LLAMASERVER_DEFAULT_MIN_P),
            "repeat_penalty": flags.get("repeat_penalty", LLAMASERVER_DEFAULT_REPEAT_PENALTY),
        }
    from pathlib import Path

    from .calibration.vllm_probe import generation_defaults
    from .debug_bus import debug

    try:
        vllm_defaults: dict[str, float] = generation_defaults(Path(entry["gguf_path"]))
    except (OSError, ValueError, KeyError) as error:
        debug(f"⚠️ generation_config.json unreadable for {model_id}: {error}")
        return defaults
    return vllm_defaults


def set_sampling(target: TuningTarget, agent: str, values: dict[str, float]) -> None:
    """The agent's sampling values; one outside ``config.SAMPLING_RANGES`` is refused (422), none is changed then."""
    from .user_error import Invalid

    for field, value in values.items():
        low, high, _step = SAMPLING_RANGES[field]
        if not low <= value <= high:
            raise Invalid("sampling_out_of_range", field=field, value=value, low=low, high=high)
    for field, value in values.items():
        set_agent_setting(target, agent, field, int(value) if field == "top_k" else float(value))


def reset_sampling(target: TuningTarget, agent: str, defaults: dict[str, float], *, include_temperature: bool) -> None:
    """``defaults`` (``sampling_defaults``) into the agent's sampling; the temperature only with
    ``include_temperature`` (a model change; at a restart it stays as saved)."""
    if include_temperature:
        set_agent_setting(target, agent, "temperature", defaults["temperature"])
    set_agent_setting(target, agent, "top_k", int(defaults["top_k"]))
    for field in ("top_p", "min_p", "repeat_penalty"):
        set_agent_setting(target, agent, field, defaults[field])


# ── A model change ───────────────────────────────────────────────────────


def chat_agents() -> list[tuple[str, str, str]]:
    """``(id, display name, emoji)`` of every agent a chat can address (agent_config.selectable_agents),
    in the order of agents.json."""
    from .agent_config import selectable_agents

    return [(agent, config.display_name, config.emoji) for agent, config in selectable_agents().items()]


def choose_model(target: TuningTarget, agent: str, model_id: str, label: str) -> list[str]:
    """The agent's model (``""``: AIfred's; AIfred and the Vision agent always have one of their own):
    its properties from the caches, speed off where the model has no -speed variant, and the
    model's sampling for the agent; a change of AIfred's model also for every agent that runs on
    it (not for the Vision agent, which keeps its sampling). Returns the agents whose sampling was
    reset.
    """
    set_agent_setting(target, agent, "model_id", model_id)
    set_agent_setting(target, agent, "model", label)
    if model_id:
        load_agent_model_params(target, agent, model_id)
    else:
        set_agent_setting(target, agent, "has_speed_variant", False)
    if not get_agent_setting(target, agent, "has_speed_variant"):
        set_agent_setting(target, agent, "speed_mode", False)
    if agent == "vision":
        return []
    affected = [agent]
    if agent == "aifred":
        affected += [other for other, _, _ in chat_agents() if other != "aifred" and not get_agent_setting(target, other, "model_id")]
    for other in affected:
        reset_sampling(target, other, sampling_defaults(target, other), include_temperature=True)
    return affected


# ── The vision models ────────────────────────────────────────────────────


def vision_catalog(target: TuningTarget, catalog: dict[str, str], backend_url: str) -> dict[str, str]:
    """The models (id → label) a Vision model is chosen from. The describer is a side channel
    (llama.cpp ``-visiond``, the Ollama daemon), not a citizen of the main backend: under vLLM the
    GGUF view of the llama-swap catalog joins the backend's models, else there would be none."""
    if target.backend_type != "vllm":
        return catalog
    from .model_discovery import discover_llamaswap_models

    return {**discover_llamaswap_models(backend_url, vllm_entries=False), **catalog}


async def vision_candidates(target: TuningTarget, catalog: dict[str, str], backend_url: str) -> dict[str, str]:
    """The models of ``vision_catalog`` that can describe images: by the backend's metadata (Ollama),
    by what the llama-swap entry loads (llama.cpp), by name (vLLM's GGUF view, cloud APIs)."""
    from .vision_utils import is_vision_model, is_vision_model_sync

    candidates = vision_catalog(target, catalog, backend_url)
    if target.backend_type in ("vllm", "cloud_api"):
        return {model: label for model, label in candidates.items() if is_vision_model_sync(model)}
    return {model: label for model, label in candidates.items() if await is_vision_model(target, model)}


def vision_options(target: TuningTarget, candidates: dict[str, str]) -> list[ModelOption]:
    """The vision models with whether they fit beside the chat model (the rule of the turn,
    ``vision_routing``): "🧠 Chat-LLM" — the model is AIfred's and sees itself; "⚡ No Swap" — one of
    its ``-visiond`` placements fits beside what is loaded, or it runs over the Ollama side channel;
    "🔄 Swap" — it displaces the chat model. The Ollama VLM list is fetched once."""
    from .ollama_models import list_ollama_vlm_models
    from .vision_routing import fitting_visiond, same_model, vision_swap_status
    from .vision_utils import has_native_vision

    ollama_names = [model.name for model in list_ollama_vlm_models()]  # empty when Ollama is down
    aifred_model = get_agent_setting(target, "aifred", "model_id") or ""
    options = []
    for model, label in candidates.items():
        visiond, fit = fitting_visiond(model)
        if same_model(model, aifred_model) and has_native_vision(aifred_model):
            options.append(ModelOption(model, label, "🧠 Chat-LLM", True))
        elif visiond is not None or (fit is None and vision_swap_status(model, target.backend_type, ollama_names=ollama_names)):
            options.append(ModelOption(model, label, "⚡ No Swap", True))
        else:
            options.append(ModelOption(model, label, "🔄 Swap", False))
    return options


# ── The server's view and what it writes ─────────────────────────────────


def models_in_use(view: TuningTarget, lang: str) -> list[tuple[str, list[str]]]:
    """Every model the agents run on (base ids, AIfred's first) with the names of who runs on it: the
    chat agents (one without a model of its own runs on AIfred's), the Automatik and the Vision agent."""
    from .agent_config import get_agent_config
    from .i18n import t

    vision = get_agent_config("vision")
    users = [(get_agent_base_model_id(view, agent), f"{emoji} {name}") for agent, name, emoji in chat_agents()]
    users.append((view.automatik_model_id or get_agent_base_model_id(view, "aifred"), t("automatik_agent_name", lang=lang)))
    if vision:
        users.append((get_agent_setting(view, "vision", "model_id"), f"{vision.emoji or '📷'} {vision.display_name}"))
    models: dict[str, list[str]] = {get_agent_base_model_id(view, "aifred"): []}
    for model_id, name in users:
        if model_id:
            models.setdefault(model_id, []).append(name)
    return [(model_id, names) for model_id, names in models.items() if model_id and names]


def agent_row(view: TuningTarget, agent: str, display_name: str, emoji: str, lang: str) -> AgentChoices:
    from .agent_settings import get_agent_base_model_id
    from .research.context_utils import get_agent_num_ctx

    thinking, options = thinking_choices(view, agent, lang)
    context, context_source = get_agent_num_ctx(agent, view, get_agent_base_model_id(view, agent))
    return AgentChoices(
        id=agent,
        display_name=display_name,
        emoji=emoji,
        model_id=get_agent_setting(view, agent, "model_id"),
        personality=get_agent_setting(view, agent, "personality"),
        reasoning=get_agent_setting(view, agent, "reasoning"),
        thinking=thinking,
        thinking_options=tuple(options),
        speed_available=get_agent_setting(view, agent, "has_speed_variant"),
        speed=get_agent_setting(view, agent, "speed_mode"),
        sampling={field: get_agent_setting(view, agent, field) for field in SAMPLING_RANGES},
        context=context,
        context_source=context_source,
        manual_context_enabled=get_agent_setting(view, agent, "num_ctx_manual_enabled"),
        manual_context=get_agent_setting(view, agent, "num_ctx_manual"),
    )


async def quick_choices(view: RuntimeSettings, catalog: dict[str, str], lang: str) -> QuickChoices:
    """Every row of the quick choices from the settings (``view``) and the backend's models."""
    from .agent_config import get_agent_config

    vision = get_agent_config("vision")
    rows = [agent_row(view, agent, name, emoji, lang) for agent, name, emoji in chat_agents()]
    rows.append(agent_row(view, "vision", vision.display_name if vision else "Vision", (vision.emoji if vision else "") or "📷", lang))
    candidates = await vision_candidates(view, catalog, view.backend_url)
    return QuickChoices(
        agents=tuple(rows),
        automatik_model_id=view.automatik_model_id,
        models=tuple(ModelOption(model, label) for model, label in catalog.items()),
        vision_models=tuple(vision_options(view, candidates)),
        sampling_ranges={field: SamplingRange(*limits) for field, limits in SAMPLING_RANGES.items()},
        manual_context_offered=view.backend_type == "ollama",
    )


def save_choices(view: RuntimeSettings, agents: list[str]) -> bool:
    """What a choice changed into settings.json: the persisted tuning of ``agents`` (``agent_tuning``)
    and the models of the view's backend (``backend_models``, as the Reflex state writes them: every
    agent's model and the Automatik's). Every other key stays as the file has it. False when the
    file cannot be read or written (``settings.update_settings``)."""
    from .settings import load_settings, update_settings

    saved = load_settings() or {}
    tuning: dict[str, dict[str, Any]] = dict(saved.get("agent_tuning", {}))
    for agent in agents:
        bucket = view.agent_tuning[agent]
        entry = {field: getattr(bucket, field) for field in PERSISTED_TUNING_FIELDS}
        if agent == "vision":
            entry["num_ctx_manual"] = bucket.num_ctx_manual
            entry["num_ctx_manual_enabled"] = bucket.num_ctx_manual_enabled
        tuning[agent] = entry
    models = {agent: bucket.model_id for agent, bucket in view.agent_tuning.items()}
    models["automatik"] = view.automatik_model_id
    backend_models = {**saved.get("backend_models", {}), view.backend_type: models}
    return update_settings({"agent_tuning": tuning, "backend_models": backend_models})
