"""What a turn reads from the settings, and the rules that resolve it.

``RuntimeSettings`` is the view of settings.json for every path without a Reflex state (the turn
service, channels, scheduler). Per-session choices (active agent, multi-agent mode, incognito)
are not settings; they come from the session's config. The functions below hold the rules once, for that view and for the
Reflex state alike: both carry ``backend_type``, ``enable_tts``, ``automatik_model_id`` and
``agent_tuning`` (one ``AgentTuning`` per agent), and the helpers in ``agent_settings`` read the
per-agent values from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .agent_settings import (
    PERSISTED_TUNING_FIELDS,
    get_agent_setting,
    set_agent_setting,
)
from .agent_tuning import AgentTuning, default_agent_tuning, default_tuning
from .config import (
    BACKEND_DEFAULT_MODELS,
    BACKEND_URLS,
    LLAMASWAP_BACKENDS,
    LLAMASWAP_CONFIG_PATH,
)


class TuningTarget(Protocol):
    """What the resolution rules read and write: the Reflex state and RuntimeSettings."""

    backend_type: str
    enable_tts: bool
    automatik_model_id: str
    agent_tuning: dict[str, AgentTuning]


@dataclass
class RuntimeSettings:
    backend_type: str
    backend_url: str
    ui_language: str
    enable_tts: bool
    automatik_model_id: str
    agent_tuning: dict[str, AgentTuning]
    # The spoken reply (lib/reply_speech.py): read while it is written, and per agent its voice
    # on/off and language.
    tts_autoplay: bool
    tts_agents: dict[str, dict[str, Any]]
    # Multi-agent modes (lib/turn_debate.py): rounds of a debate, symposion and tribunal, and how
    # many votes end an auto-consensus ("majority" or "unanimous").
    max_debate_rounds: int
    consensus_type: str


def load_runtime_settings() -> RuntimeSettings:
    """settings.json as a turn reads it; a key the file lacks has its value from get_default_settings."""
    from .settings import get_default_settings, load_settings

    saved = {**get_default_settings(), **(load_settings() or {})}
    backend_type = saved["backend_type"]
    view = RuntimeSettings(
        backend_type=backend_type,
        backend_url=BACKEND_URLS[backend_type],
        ui_language=saved["ui_language"],
        enable_tts=bool(saved["enable_tts"]),
        automatik_model_id="",
        agent_tuning=default_agent_tuning(),
        tts_autoplay=bool(saved["tts_autoplay"]),
        tts_agents=saved["tts_agents"],
        max_debate_rounds=saved["max_debate_rounds"],
        consensus_type=saved["consensus_type"],
    )
    apply_saved_tuning(view, saved)
    apply_held_manual_context(view)
    had_backend_settings = apply_saved_models(view, saved)
    for agent, tuning in view.agent_tuning.items():
        load_agent_model_params(view, agent, tuning.model_id)
    apply_default_models(view, had_backend_settings)
    return view


# ── A context window set by hand: the chat agents' until the server restarts ──
#
# Peuqui 08.11.2026 (as in Reflex): a manual context is for trying something out, normally the calibration decides;
# one forgotten small value that stayed would shrink every answer unnoticed. So the chat agents' values live in the
# server's memory only; Vision's is saved in settings.json (save_choices).
_held_manual_context: dict[str, tuple[bool, int]] = {}


def hold_manual_context(agent: str, enabled: bool, value: int) -> None:
    _held_manual_context[agent] = (enabled, value)


def apply_held_manual_context(target: TuningTarget) -> None:
    for agent, (enabled, value) in _held_manual_context.items():
        bucket = target.agent_tuning.get(agent)
        if bucket is not None:
            bucket.num_ctx_manual_enabled, bucket.num_ctx_manual = enabled, value


# ── From settings.json into the tuning buckets ──────────────────────────


def apply_saved_tuning(target: TuningTarget, saved: dict[str, Any]) -> None:
    """Per-agent tuning values of settings.json; agents without a bucket get one on demand.

    Keys missing in the file keep the bucket's defaults.
    """
    for agent, entry in saved.get("agent_tuning", {}).items():
        bucket = target.agent_tuning.get(agent)
        if bucket is None:
            bucket = default_tuning(agent)
            target.agent_tuning[agent] = bucket
        for field in (*PERSISTED_TUNING_FIELDS, "num_ctx_manual", "num_ctx_manual_enabled"):
            if field in entry:
                setattr(bucket, field, entry[field])


def apply_saved_models(target: TuningTarget, saved: dict[str, Any]) -> bool:
    """The models of settings.json's ``backend_models`` for the target's backend.

    Agents no longer registered are skipped. Returns whether settings.json has models for this
    backend (``apply_default_models`` needs it).
    """
    from .agent_config import get_agent_config

    backend_data = saved.get("backend_models", {}).get(target.backend_type)
    if backend_data is not None:
        for agent, model_id in backend_data.items():
            if agent == "automatik":
                target.automatik_model_id = model_id
                continue
            if agent not in target.agent_tuning:
                if get_agent_config(agent) is None:
                    continue
                target.agent_tuning[agent] = default_tuning(agent)
            target.agent_tuning[agent].model_id = model_id
            target.agent_tuning[agent].model = model_id
    return backend_data is not None


# Agents whose default model applies whenever they have none. Every other agent of
# BACKEND_DEFAULT_MODELS (and the Automatik) gets its default only when settings.json holds no
# models for the backend: there an empty value means "AIfred's model".
ALWAYS_DEFAULTED = ("aifred", "vision")


def apply_default_models(target: TuningTarget, had_backend_settings: bool) -> list[tuple[str, str]]:
    """The backend's default models of config.py where the target has none.

    Returns ``(agent, model)`` for every default that applied, the Automatik as ``"automatik"``;
    an empty model means config.py has none either.
    """
    defaults = BACKEND_DEFAULT_MODELS.get(target.backend_type, {})
    applied: list[tuple[str, str]] = []
    for agent, model in defaults.items():
        if agent == "automatik":
            if not target.automatik_model_id and not had_backend_settings:
                target.automatik_model_id = model
                applied.append((agent, model))
            continue
        bucket = target.agent_tuning.get(agent)
        if bucket is None:
            continue
        if agent in ALWAYS_DEFAULTED:
            if bucket.model:
                continue
        elif had_backend_settings or bucket.model_id:
            continue
        bucket.model = model
        bucket.model_id = model
        applied.append((agent, model))
    return applied


# ── Which variant of a model loads ───────────────────────────────────────


def effective_suffix(target: TuningTarget, base_id: str, owner: str, *, has_speed_variant: bool) -> str:
    """Variant suffix (``-speed``, ``-tts-<engine>``, …) llama-swap loads for ``base_id``.

    ``owner`` is the agent whose speed toggle applies. The TTS part follows the toggle
    ``enable_tts`` plus the planned engine of the escalation list, not a live container probe
    (that would leak transient states into the model choice).
    """
    from .calibration import resolve_effective_suffix
    from .tts_llm_coordination import planned_tts_engine

    return resolve_effective_suffix(
        LLAMASWAP_CONFIG_PATH,
        base_id,
        target.backend_type,
        speed_on=get_agent_setting(target, owner, "speed_mode"),
        has_speed_variant=has_speed_variant,
        tts_active=bool(target.enable_tts),
        tts_engine=planned_tts_engine(base_id) if target.enable_tts else "",
    )


def effective_model_id(target: TuningTarget, agent: str) -> str:
    """Model id with variant suffix that the agent's requests go to; empty when the agent has no
    own model (its requests then go to AIfred's, ``effective_model_id(target, "aifred")``)."""
    base_id: str = get_agent_setting(target, agent, "model_id")
    if not base_id:
        return base_id
    return base_id + effective_suffix(
        target, base_id, agent, has_speed_variant=get_agent_setting(target, agent, "has_speed_variant"),
    )


def effective_automatik_id(target: TuningTarget) -> str:
    """Model id of the Automatik (intent detection): AIfred's unless an own model is set.

    The Automatik has no speed toggle of its own and follows AIfred's; whether its model has a
    ``-speed`` profile the resolver checks itself.
    """
    own = target.automatik_model_id
    if not own or own == target.agent_tuning["aifred"].model_id:
        return effective_model_id(target, "aifred")
    return own + effective_suffix(target, own, "aifred", has_speed_variant=True)


# ── Model properties from the calibration caches ────────────────────────


def load_agent_model_params(target: TuningTarget, agent: str, model_id: str) -> None:
    """Model properties of the agent's model (context, thinking support, speed profile) from the caches."""
    if not model_id or target.backend_type not in ("ollama", *LLAMASWAP_BACKENDS):
        return
    if agent == "vision" and target.backend_type not in LLAMASWAP_BACKENDS:
        return
    if target.backend_type == "ollama":
        from .model_vram_cache import get_model_parameters

        params = get_model_parameters(model_id)
        for field in ("rope_factor", "max_context", "is_hybrid", "supports_thinking"):
            set_agent_setting(target, agent, field, params[field])
        return

    from .calibration import model_has_speed_variant
    from .model_discovery import is_vllm_entry
    from .model_vram_cache import (
        get_llamacpp_calibration,
        get_thinking_support_for_model,
    )

    set_agent_setting(target, agent, "rope_factor", 1.0)
    set_agent_setting(target, agent, "is_hybrid", False)
    set_agent_setting(target, agent, "supports_thinking", get_thinking_support_for_model(model_id))
    # Before the context lookup: the variant resolver reads it. The truth is the llama-swap config
    # (is there a -speed entry), not the cache's speed_split field: a fresh multi-GPU calibration
    # leaves that one empty.
    set_agent_setting(target, agent, "has_speed_variant", model_has_speed_variant(model_id))
    if is_vllm_entry(model_id):
        # vLLM entry: context from the llama-swap entry, no speed variants, no RoPE.
        from .operating_points import get_vllm_entry_context

        set_agent_setting(target, agent, "max_context", get_vllm_entry_context(model_id))
        return
    # The context belongs to the variant: the active -tts-<engine>-vlm-<vlm> profile has less than
    # the base (TTS takes VRAM). The whole matrix is calibrated; the base answers when the variant
    # is not.
    resolved = effective_model_id(target, agent) or model_id
    context = get_llamacpp_calibration(resolved)
    if context is None and resolved != model_id:
        context = get_llamacpp_calibration(model_id)
    set_agent_setting(target, agent, "max_context", context or 0)
