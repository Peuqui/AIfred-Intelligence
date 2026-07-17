"""Agent configuration mixin for AIfred state.

Handles per-agent personality, reasoning, thinking mode,
sampling parameters, speed mode, RoPE factors, multi-agent mode settings,
temperature configuration, and model selection for Sokrates/Salomo.
"""

from __future__ import annotations

from typing import ClassVar, List

import reflex as rx

from ..lib.config import (
    DEFAULT_MIN_P,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    LLAMASERVER_DEFAULT_MIN_P,
    LLAMASERVER_DEFAULT_REPEAT_PENALTY,
    LLAMASERVER_DEFAULT_TEMPERATURE,
    LLAMASERVER_DEFAULT_TOP_K,
    LLAMASERVER_DEFAULT_TOP_P,
    SOKRATES_TEMPERATURE_OFFSET,
    SALOMO_TEMPERATURE_OFFSET,
    VISION_DEFAULT_TEMPERATURE,
    VISION_DEFAULT_TOP_K,
    VISION_DEFAULT_TOP_P,
    VISION_DEFAULT_MIN_P,
    VISION_DEFAULT_REPEAT_PENALTY,
)

# Agent names used throughout this mixin
_AGENTS = ("aifred", "sokrates", "salomo", "vision")

# Feature -> (emoji, prompt_loader setter name)
# Note: thinking has no prompt_loader sync — read directly from State at runtime.
_FEATURE_META: dict[str, tuple[str, str]] = {
    "personality": ("", "set_personality_enabled"),
    "reasoning": ("", "set_reasoning_enabled"),
    "thinking": ("", ""),
}

# Per-agent emoji for personality toggles
_PERSONALITY_EMOJI: dict[str, str] = {
    "aifred": "\U0001f3a9",      # top hat
    "sokrates": "\U0001f3db\ufe0f",  # classical building
    "salomo": "\U0001f451",      # crown
    "vision": "\U0001f4f7",      # camera
}

# Per-feature emoji (same for all agents)
_FEATURE_EMOJI: dict[str, str] = {
    "personality": "",   # filled per-agent from _PERSONALITY_EMOJI
    "reasoning": "\U0001f4ad",   # thought balloon
    "thinking": "\U0001f9e0",    # brain
}


class AgentConfigMixin(rx.State, mixin=True):
    """Mixin for per-agent configuration and sampling parameters."""

    # ── Per-Agent Personality Toggles ─────────────────────────────
    aifred_personality: bool = True
    sokrates_personality: bool = True
    salomo_personality: bool = True
    vision_personality: bool = True

    # ── Per-Agent Reasoning Toggles ───────────────────────────────
    aifred_reasoning: bool = True
    sokrates_reasoning: bool = True
    salomo_reasoning: bool = True
    vision_reasoning: bool = False

    # ── Per-Agent Thinking Toggles (enable_thinking to backend) ───
    aifred_thinking: bool = True
    sokrates_thinking: bool = True
    salomo_thinking: bool = True
    vision_thinking: bool = True

    # ── Per-Agent Reasoning-Effort Level (chat_template_kwargs) ───
    # "" = template default (e.g. DeepSeek-V4 "Think High"); otherwise
    # a level from {agent}_reasoning_levels (e.g. "max"). Only sent
    # when thinking is on.
    aifred_reasoning_effort: str = ""
    sokrates_reasoning_effort: str = ""
    salomo_reasoning_effort: str = ""
    vision_reasoning_effort: str = ""

    # ── Per-Agent Sampling Parameters ─────────────────────────────
    aifred_top_k: int = DEFAULT_TOP_K
    aifred_top_p: float = DEFAULT_TOP_P
    aifred_min_p: float = DEFAULT_MIN_P
    aifred_repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    sokrates_top_k: int = DEFAULT_TOP_K
    sokrates_top_p: float = DEFAULT_TOP_P
    sokrates_min_p: float = DEFAULT_MIN_P
    sokrates_repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    salomo_top_k: int = DEFAULT_TOP_K
    salomo_top_p: float = DEFAULT_TOP_P
    salomo_min_p: float = DEFAULT_MIN_P
    salomo_repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    vision_top_k: int = VISION_DEFAULT_TOP_K
    vision_top_p: float = VISION_DEFAULT_TOP_P
    vision_min_p: float = VISION_DEFAULT_MIN_P
    vision_repeat_penalty: float = VISION_DEFAULT_REPEAT_PENALTY
    sampling_reset_key: int = 0  # UI key counter to force re-mount on reset

    # ── Per-Agent Speed Mode (llamacpp only) ──────────────────────
    aifred_speed_mode: bool = False
    sokrates_speed_mode: bool = False
    salomo_speed_mode: bool = False
    vision_speed_mode: bool = False
    aifred_has_speed_variant: bool = False
    sokrates_has_speed_variant: bool = False
    salomo_has_speed_variant: bool = False
    vision_has_speed_variant: bool = False

    # ── Per-Agent RoPE Scaling Factors ────────────────────────────
    aifred_rope_factor: float = 1.0
    automatik_rope_factor: float = 1.0
    sokrates_rope_factor: float = 1.0
    salomo_rope_factor: float = 1.0
    vision_rope_factor: float = 1.0

    # ── Per-Agent Model Metadata ──────────────────────────────────
    aifred_max_context: int = 0
    aifred_is_hybrid: bool = False
    aifred_supports_thinking: bool | None = None
    aifred_reasoning_levels: list[str] = []
    sokrates_max_context: int = 0
    sokrates_is_hybrid: bool = False
    sokrates_supports_thinking: bool | None = None
    sokrates_reasoning_levels: list[str] = []
    salomo_max_context: int = 0
    salomo_is_hybrid: bool = False
    salomo_supports_thinking: bool | None = None
    salomo_reasoning_levels: list[str] = []
    vision_max_context: int = 0
    vision_is_hybrid: bool = False
    vision_supports_thinking: bool | None = None
    vision_reasoning_levels: list[str] = []

    # ── Temperature Settings ──────────────────────────────────────
    sokrates_temperature: float = 0.5
    sokrates_temperature_offset: float = SOKRATES_TEMPERATURE_OFFSET
    salomo_temperature: float = 0.5
    salomo_temperature_offset: float = SALOMO_TEMPERATURE_OFFSET
    vision_temperature: float = VISION_DEFAULT_TEMPERATURE

    # ── Active Agent (direct chat) ─────────────────────────────────
    # NOTE: active_agent, multi_agent_mode, symposion_agents are now
    # per-session (session_storage.DEFAULT_SESSION_CONFIG). Class defaults
    # only apply before any session is loaded.
    active_agent: str = "aifred"  # Which agent responds (default: aifred)
    agent_memory_enabled: bool = True  # Global toggle: agents use long-term memory

    # ── Multi-Agent Settings (per-session) ────────────────────────
    multi_agent_mode: str = "standard"
    max_debate_rounds: int = 3  # still global (debate param)
    symposion_agents: list[str] = []  # Selected agents for Symposion mode
    consensus_type: str = "majority"
    sokrates_model: str = ""
    sokrates_model_id: str = ""
    salomo_model: str = ""
    salomo_model_id: str = ""

    # ── Multi-Agent Runtime State ─────────────────────────────────
    sokrates_critique: str = ""
    sokrates_pro_args: str = ""
    sokrates_contra_args: str = ""
    show_sokrates_panel: bool = False
    salomo_synthesis: str = ""
    show_salomo_panel: bool = False
    debate_round: int = 0
    debate_user_interjection: str = ""
    debate_in_progress: bool = False

    # ================================================================
    # GENERIC HELPERS (deduplicated triple-agent pattern)
    # ================================================================

    def _toggle_agent_feature(self, agent: str, feature: str) -> None:
        """Toggle a boolean per-agent feature and persist + sync to prompt_loader.

        Works for personality and reasoning (thinking moved to the
        thinking-mode dropdown, see _set_agent_thinking_mode).
        """
        attr = f"{agent}_{feature}"
        new_val = not getattr(self, attr)
        setattr(self, attr, new_val)

        # Emoji for debug message
        if feature == "personality":
            emoji = _PERSONALITY_EMOJI[agent]
        else:
            emoji = _FEATURE_EMOJI[feature]

        status = "ON" if new_val else "OFF"
        self.add_debug(f"{emoji} {agent.capitalize()} {feature}: {status}")  # type: ignore[attr-defined]

        # Save all three values for this feature at once
        save_method = f"_save_{feature}_settings"
        getattr(self, save_method)()

        # Sync to prompt_loader (if setter exists — thinking has none)
        setter_name = _FEATURE_META[feature][1]
        if setter_name:
            from ..lib import prompt_loader
            getattr(prompt_loader, setter_name)(agent, new_val)

    # ── Personality Toggles ───────────────────────────────────────

    def toggle_aifred_personality(self, _value: bool | None = None) -> None:
        """Toggle AIfred Butler personality style on/off."""
        self._toggle_agent_feature("aifred", "personality")

    def toggle_sokrates_personality(self, _value: bool | None = None) -> None:
        """Toggle Sokrates philosophical personality style on/off."""
        self._toggle_agent_feature("sokrates", "personality")

    def toggle_salomo_personality(self, _value: bool | None = None) -> None:
        """Toggle Salomo judge personality style on/off."""
        self._toggle_agent_feature("salomo", "personality")

    def toggle_vision_personality(self, _value: bool | None = None) -> None:
        """Toggle Vision agent personality style on/off."""
        self._toggle_agent_feature("vision", "personality")

    def _save_feature_settings(self, feature: str) -> None:
        """Save toggle states for a feature (personality/reasoning/thinking) to settings."""
        from ..lib.settings import load_settings, save_settings
        settings = load_settings() or {}
        for agent in ("aifred", "sokrates", "salomo", "vision"):
            settings[f"{agent}_{feature}"] = getattr(self, f"{agent}_{feature}")
        save_settings(settings)

    _save_personality_settings = lambda self: self._save_feature_settings("personality")  # noqa: E731
    _save_reasoning_settings = lambda self: self._save_feature_settings("reasoning")  # noqa: E731
    _save_thinking_settings = lambda self: self._save_feature_settings("thinking")  # noqa: E731
    _save_reasoning_effort_settings = lambda self: self._save_feature_settings("reasoning_effort")  # noqa: E731

    # ── Reasoning Toggles ─────────────────────────────────────────

    def toggle_aifred_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("aifred", "reasoning")

    def toggle_sokrates_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("sokrates", "reasoning")

    def toggle_salomo_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("salomo", "reasoning")

    def toggle_vision_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("vision", "reasoning")

    # ── Thinking Mode (dropdown: off / on / effort level) ─────────

    def _thinking_mode_labels(self) -> tuple[str, str]:
        """(off_label, on_label) in the current UI language. Effort levels
        stay raw — they are the template's proper names (e.g. "max")."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return t("thinking_mode_off", lang=lang), t("thinking_mode_on", lang=lang)

    def _set_agent_thinking_mode(self, agent: str, mode: str) -> None:
        """Dropdown label → (thinking bool, reasoning_effort str).

        off → thinking off; on → thinking on with template default;
        any other value → thinking on + that effort level.
        """
        off_label, on_label = self._thinking_mode_labels()
        if mode == off_label:
            mode = "off"
        elif mode == on_label:
            mode = "on"
        setattr(self, f"{agent}_thinking", mode != "off")
        setattr(
            self, f"{agent}_reasoning_effort",
            "" if mode in ("off", "on") else mode,
        )
        emoji = _FEATURE_EMOJI["thinking"]
        self.add_debug(f"{emoji} {agent.capitalize()} thinking: {mode}")  # type: ignore[attr-defined]
        self._save_thinking_settings()
        self._save_reasoning_effort_settings()

    def set_aifred_thinking_mode(self, mode: str) -> None:
        self._set_agent_thinking_mode("aifred", mode)

    def set_sokrates_thinking_mode(self, mode: str) -> None:
        self._set_agent_thinking_mode("sokrates", mode)

    def set_salomo_thinking_mode(self, mode: str) -> None:
        self._set_agent_thinking_mode("salomo", mode)

    def set_vision_thinking_mode(self, mode: str) -> None:
        self._set_agent_thinking_mode("vision", mode)

    def _agent_thinking_mode(self, agent: str) -> str:
        """Current dropdown label derived from (thinking, effort)."""
        off_label, on_label = self._thinking_mode_labels()
        if not getattr(self, f"{agent}_thinking"):
            return off_label
        return getattr(self, f"{agent}_reasoning_effort") or on_label

    def _load_agent_reasoning_levels(self, agent: str, model_id: str) -> None:
        """Refresh ``{agent}_reasoning_levels`` for a newly selected model
        and clear a selected effort level the new model doesn't support.
        Levels exist only for llama.cpp models (embedded chat template)."""
        levels: list[str] = []
        if model_id and self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            from ..lib.gguf_utils import resolve_reasoning_levels
            levels = resolve_reasoning_levels(model_id)
        setattr(self, f"{agent}_reasoning_levels", levels)
        if getattr(self, f"{agent}_reasoning_effort") not in ("", *levels):
            setattr(self, f"{agent}_reasoning_effort", "")
            self._save_reasoning_effort_settings()

    @rx.var(deps=["aifred_thinking", "aifred_reasoning_effort", "ui_language"], auto_deps=False)
    def aifred_thinking_mode(self) -> str:
        return self._agent_thinking_mode("aifred")

    @rx.var(deps=["sokrates_thinking", "sokrates_reasoning_effort", "ui_language"], auto_deps=False)
    def sokrates_thinking_mode(self) -> str:
        return self._agent_thinking_mode("sokrates")

    @rx.var(deps=["salomo_thinking", "salomo_reasoning_effort", "ui_language"], auto_deps=False)
    def salomo_thinking_mode(self) -> str:
        return self._agent_thinking_mode("salomo")

    @rx.var(deps=["vision_thinking", "vision_reasoning_effort", "ui_language"], auto_deps=False)
    def vision_thinking_mode(self) -> str:
        return self._agent_thinking_mode("vision")

    @rx.var(deps=["aifred_reasoning_levels", "ui_language"], auto_deps=False)
    def aifred_thinking_options(self) -> list[str]:
        return [*self._thinking_mode_labels()] + self.aifred_reasoning_levels

    @rx.var(deps=["sokrates_reasoning_levels", "ui_language"], auto_deps=False)
    def sokrates_thinking_options(self) -> list[str]:
        return [*self._thinking_mode_labels()] + self.sokrates_reasoning_levels

    @rx.var(deps=["salomo_reasoning_levels", "ui_language"], auto_deps=False)
    def salomo_thinking_options(self) -> list[str]:
        return [*self._thinking_mode_labels()] + self.salomo_reasoning_levels

    @rx.var(deps=["vision_reasoning_levels", "ui_language"], auto_deps=False)
    def vision_thinking_options(self) -> list[str]:
        return [*self._thinking_mode_labels()] + self.vision_reasoning_levels

    # ================================================================
    # SAMPLING PARAMETERS
    # ================================================================

    def set_aifred_sampling(self, param: str, value: str) -> None:
        """Set AIfred sampling parameter from UI input."""
        self._set_agent_sampling("aifred", param, value)

    def set_sokrates_sampling(self, param: str, value: str) -> None:
        """Set Sokrates sampling parameter from UI input."""
        self._set_agent_sampling("sokrates", param, value)

    def set_salomo_sampling(self, param: str, value: str) -> None:
        """Set Salomo sampling parameter from UI input."""
        self._set_agent_sampling("salomo", param, value)

    def set_vision_sampling(self, param: str, value: str) -> None:
        """Set Vision sampling parameter from UI input."""
        self._set_agent_sampling("vision", param, value)

    def _set_agent_sampling(self, agent: str, param: str, value: str) -> None:
        """Set a sampling parameter for an agent and save to settings."""
        try:
            if param == "top_k":
                int_val = int(float(value))
                setattr(self, f"{agent}_top_k", max(0, min(200, int_val)))
            elif param == "top_p":
                float_val = float(value)
                setattr(self, f"{agent}_top_p", max(0.0, min(1.0, float_val)))
            elif param == "min_p":
                float_val = float(value)
                setattr(self, f"{agent}_min_p", max(0.0, min(1.0, float_val)))
            elif param == "repeat_penalty":
                float_val = float(value)
                setattr(self, f"{agent}_repeat_penalty", max(1.0, min(2.0, float_val)))
            final_val = getattr(self, f"{agent}_{param}")
            self.add_debug(f"\U0001f3b2 {agent.capitalize()} {param}={final_val}")  # type: ignore[attr-defined]
            self._save_settings()  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

    def reset_aifred_sampling(self) -> None:
        """Reset AIfred sampling to model defaults."""
        self._reset_agent_sampling("aifred")

    def reset_sokrates_sampling(self) -> None:
        """Reset Sokrates sampling to model defaults."""
        self._reset_agent_sampling("sokrates")

    def reset_salomo_sampling(self) -> None:
        """Reset Salomo sampling to model defaults."""
        self._reset_agent_sampling("salomo")

    def reset_vision_sampling(self) -> None:
        """Reset Vision sampling to vision-specific defaults."""
        self._reset_agent_sampling("vision")

    def _reset_agent_sampling(self, agent: str, include_temperature: bool = True) -> None:
        """Reset sampling parameters for an agent to model/backend defaults.

        Args:
            agent: "aifred", "sokrates", "salomo", or "vision"
            include_temperature: If True, reset temperature too (model change / reset button).
                If False, keep current temperature (app restart -- temperature is persisted).
        """
        if agent == "vision":
            defaults: dict[str, float] = {
                "temperature": VISION_DEFAULT_TEMPERATURE,
                "top_k": VISION_DEFAULT_TOP_K,
                "top_p": VISION_DEFAULT_TOP_P,
                "min_p": VISION_DEFAULT_MIN_P,
                "repeat_penalty": VISION_DEFAULT_REPEAT_PENALTY,
            }
        else:
            defaults = {
                "temperature": LLAMASERVER_DEFAULT_TEMPERATURE,
                "top_k": DEFAULT_TOP_K,
                "top_p": DEFAULT_TOP_P,
                "min_p": DEFAULT_MIN_P,
                "repeat_penalty": DEFAULT_REPEAT_PENALTY,
            }

        if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            # Try to get model-specific values from llama-swap YAML
            # Sokrates/Salomo with empty model_id inherit from AIfred
            model_id = getattr(self, f"{agent}_model_id", "") or self.aifred_model_id  # type: ignore[attr-defined]
            if model_id:
                from ..lib.calibration import parse_llamaswap_config, parse_sampling_from_cmd
                from ..lib.config import LLAMASWAP_CONFIG_PATH
                config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                if model_id in config:
                    yaml_sampling = parse_sampling_from_cmd(config[model_id]["full_cmd"])
                    defaults = {
                        "temperature": yaml_sampling.get("temperature", LLAMASERVER_DEFAULT_TEMPERATURE),
                        "top_k": yaml_sampling.get("top_k", LLAMASERVER_DEFAULT_TOP_K),
                        "top_p": yaml_sampling.get("top_p", LLAMASERVER_DEFAULT_TOP_P),
                        "min_p": yaml_sampling.get("min_p", LLAMASERVER_DEFAULT_MIN_P),
                        "repeat_penalty": yaml_sampling.get("repeat_penalty", LLAMASERVER_DEFAULT_REPEAT_PENALTY),
                    }

        if include_temperature:
            if agent == "aifred":
                self.temperature = defaults["temperature"]  # type: ignore[attr-defined]
            else:
                setattr(self, f"{agent}_temperature", defaults["temperature"])
        setattr(self, f"{agent}_top_k", int(defaults["top_k"]))
        setattr(self, f"{agent}_top_p", defaults["top_p"])
        setattr(self, f"{agent}_min_p", defaults["min_p"])
        setattr(self, f"{agent}_repeat_penalty", defaults["repeat_penalty"])

        # Debug log — use get_agent_label for emoji + display_name from config
        from ..lib.agent_config import get_agent_label
        temp_info = f"temp={defaults['temperature']}, " if include_temperature else ""
        self.add_debug(  # type: ignore[attr-defined]
            f"{get_agent_label(agent)} sampling reset: "
            f"{temp_info}top_k={int(defaults['top_k'])}, "
            f"top_p={defaults['top_p']}, min_p={defaults['min_p']}, "
            f"rep={defaults['repeat_penalty']}"
        )

        # Increment key to force UI re-mount of input fields
        self.sampling_reset_key += 1

    # ================================================================
    # SPEED MODE — SINGLE SOURCE OF TRUTH
    # ================================================================

    def _effective_model_id(self, agent: str) -> str:
        """Return model ID with variant suffix for the current configuration.

        Delegates the suffix resolution to the SSOT helper
        :func:`aifred.lib.calibration.resolve_variant_suffix`, so the
        agents, the Automatik path, the compression-ctx lookup and the
        chat gate all use the same fallback rules. The ``*_model_id``
        state vars always contain the base ID; this method is what
        every code path sends to the backend.

        See ``resolve_variant_suffix`` for the precedence rules
        (Speed+TTS > TTS only > Speed only > base, with graceful
        fallback when a variant isn't actually present in the YAML).

        SSOT for the active profile is the user's UI toggle
        (``enable_tts`` + ``tts_engine``), NOT the live container state.
        Probing the container via HTTP every call would leak transient
        states (idle KEEP_ALIVE, busy with a batch of sentences,
        restart in progress) into model resolution.
        ``ensure_tts_state()`` at pipeline start guarantees the
        container is up before inference; from there the toggle stays
        authoritative for the rest of the request.
        """
        base_id: str = getattr(self, f"{agent}_model_id")
        if not base_id or self.backend_type != "llamacpp":  # type: ignore[attr-defined]
            return base_id

        from ..lib.calibration import resolve_effective_suffix
        from ..lib.config import LLAMASWAP_CONFIG_PATH

        suffix = resolve_effective_suffix(
            LLAMASWAP_CONFIG_PATH,
            base_id,
            speed_on=getattr(self, f"{agent}_speed_mode"),
            has_speed_variant=getattr(self, f"{agent}_has_speed_variant"),
            tts_active=bool(self.enable_tts),  # type: ignore[attr-defined]
            tts_engine=self.tts_engine,  # type: ignore[attr-defined]
        )
        return base_id + suffix

    # ================================================================
    # SPEED MODE TOGGLES (llamacpp only)
    # ================================================================

    def _toggle_speed_mode(self, agent: str) -> None:
        """Toggle speed/context mode for any agent."""
        attr = f"{agent}_speed_mode"
        setattr(self, attr, not getattr(self, attr))
        self.add_debug(f"\U0001f500 {agent.capitalize()} mode: {self._speed_mode_debug_str(agent, getattr(self, attr))}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_aifred_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("aifred")

    def toggle_sokrates_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("sokrates")

    def toggle_salomo_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("salomo")

    def toggle_vision_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("vision")

    def _speed_mode_debug_str(self, agent: str, speed_on: bool) -> str:
        """Build the speed-toggle debug string from the profile that ACTUALLY
        resolves \u2014 not the speed split in isolation.

        When a higher-precedence variant (VLM / TTS) overrides Speed, the
        message says so and reports the real loaded context, instead of
        promising the speed context the user won't actually get. Mirrors the
        runtime resolver (``_effective_model_id``)."""
        from ..lib.formatting import format_number
        from ..lib.research.context_utils import get_model_native_context
        base_id = getattr(self, f"{agent}_model_id", "") or self.aifred_model_id  # type: ignore[attr-defined]
        effective = self._effective_model_id(agent)
        ctx = get_model_native_context(effective, self.backend_type)  # type: ignore[attr-defined]
        ctx_str = format_number(ctx) if ctx > 0 else "n/a"
        suffix = (
            effective[len(base_id):].lstrip("-")
            if base_id and effective.startswith(base_id) and effective != base_id
            else ""
        )
        if not speed_on:
            return f"\U0001f4d6 context \u2014 {ctx_str} tok"
        if suffix.endswith("speed"):
            return f"\u26a1 speed \u2014 {ctx_str} tok"
        # Speed requested but a higher-precedence variant won resolution.
        return f"\u26a1 speed \u2192 overridden by {suffix or 'base'} \u2014 {ctx_str} tok"

    # ================================================================
    # ROPE FACTOR SETTERS
    # ================================================================

    def set_aifred_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for AIfred-LLM."""
        # Convert UI string to float
        factor = float(value.replace("x", ""))
        self.aifred_rope_factor = factor
        self.add_debug(f"\U0001f39a\ufe0f AIfred RoPE Factor: {value}")  # type: ignore[attr-defined]

        # Save to VRAM cache (per-model setting)
        if self.aifred_model_id:  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import (
                set_rope_factor_for_model,
                get_ollama_calibrated_max_context,
                get_rope_factor_for_model,
                get_llamacpp_calibration,
                format_model_with_ctx as _format_model_with_ctx,
            )
            set_rope_factor_for_model(self.aifred_model_id, factor)  # type: ignore[attr-defined]

            # SSOT in lib/model_vram_cache — thin wrapper binds backend_type
            def format_model_with_ctx(model_display: str, model_id: str) -> str:
                return _format_model_with_ctx(model_display, model_id, self.backend_type)  # type: ignore[attr-defined]

            # Re-display all agent models with updated context limits
            from ..lib.agent_config import get_agent_label
            self.add_debug(f"   {get_agent_label('aifred')}: {format_model_with_ctx(self.aifred_model, self.aifred_model_id)}")  # type: ignore[attr-defined]
            if self.multi_agent_mode != "standard":
                if self.sokrates_model_id:
                    self.add_debug(f"   {get_agent_label('sokrates')}: {format_model_with_ctx(self.sokrates_model, self.sokrates_model_id)}")  # type: ignore[attr-defined]
                if self.salomo_model_id:
                    self.add_debug(f"   {get_agent_label('salomo')}: {format_model_with_ctx(self.salomo_model, self.salomo_model_id)}")  # type: ignore[attr-defined]

            # Update cached min context limit
            context_limits: list[int] = []
            for model_id in [self.aifred_model_id, self.sokrates_model_id, self.salomo_model_id]:  # type: ignore[attr-defined]
                if model_id:
                    if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                        ctx = get_llamacpp_calibration(model_id)
                    else:
                        ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                    if ctx:
                        context_limits.append(ctx)
            self._min_agent_context_limit = min(context_limits) if context_limits else 0  # type: ignore[attr-defined]

            # Show history utilization and warn if compression will trigger
            self._log_history_utilization(self._min_agent_context_limit)  # type: ignore[attr-defined]

            # Warn if no calibration exists for this mode
            if factor >= 2.0:
                extended_ctx = get_ollama_calibrated_max_context(self.aifred_model_id, rope_factor=2.0)  # type: ignore[attr-defined]
                if extended_ctx is None:
                    self.add_debug("\u26a0\ufe0f No RoPE 2x calibration found - please calibrate first!")  # type: ignore[attr-defined]
            else:
                native_ctx = get_ollama_calibrated_max_context(self.aifred_model_id, rope_factor=1.0)  # type: ignore[attr-defined]
                if native_ctx is None:
                    self.add_debug("\u26a0\ufe0f No native calibration found - please calibrate first!")  # type: ignore[attr-defined]

    def set_automatik_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Automatik-LLM."""
        factor = float(value.replace("x", ""))
        self.automatik_rope_factor = factor
        effective_auto = self._effective_automatik_id  # type: ignore[attr-defined]
        if effective_auto:
            from ..lib.model_vram_cache import set_rope_factor_for_model
            set_rope_factor_for_model(effective_auto, factor)

    def _set_secondary_agent_rope_factor(self, agent: str, value: str) -> None:
        """Set RoPE factor for Sokrates or Salomo."""
        factor = float(value.replace("x", ""))
        setattr(self, f"{agent}_rope_factor", factor)
        model_id = getattr(self, f"{agent}_model_id")
        if model_id:
            from ..lib.model_vram_cache import set_rope_factor_for_model
            set_rope_factor_for_model(model_id, factor)

    def set_sokrates_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Sokrates-LLM."""
        self._set_secondary_agent_rope_factor("sokrates", value)

    def set_salomo_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Salomo-LLM."""
        self._set_secondary_agent_rope_factor("salomo", value)

    def set_vision_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Vision-LLM."""
        factor = float(value.replace("x", ""))
        self.vision_rope_factor = factor
        if self.vision_model_id:  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import set_rope_factor_for_model
            set_rope_factor_for_model(self.vision_model_id, factor)  # type: ignore[attr-defined]

    # ================================================================
    # ROPE FACTOR DISPLAY (computed vars)
    # ================================================================

    @rx.var
    def rope_factor_display(self) -> str:
        """Display value for AIfred RoPE factor select (e.g., '1.0x', '2.0x')."""
        return f"{self.aifred_rope_factor}x"

    @rx.var
    def automatik_rope_display(self) -> str:
        """Display value for Automatik RoPE factor select."""
        return f"{self.automatik_rope_factor}x"

    @rx.var
    def sokrates_rope_display(self) -> str:
        """Display value for Sokrates RoPE factor select."""
        return f"{self.sokrates_rope_factor}x"

    @rx.var
    def salomo_rope_display(self) -> str:
        """Display value for Salomo RoPE factor select."""
        return f"{self.salomo_rope_factor}x"

    @rx.var
    def vision_rope_display(self) -> str:
        """Display value for Vision RoPE factor select."""
        return f"{self.vision_rope_factor}x"

    # ================================================================
    # TEMPERATURE SETTINGS
    # ================================================================





    def _set_temperature_input(self, agent: str, value: str) -> None:
        """Set temperature for any agent from text input field."""
        try:
            attr = "temperature" if agent == "aifred" else f"{agent}_temperature"
            setattr(self, attr, max(0.0, min(2.0, float(value))))
            self.add_debug(f"\U0001f321\ufe0f {agent.capitalize()} temperature={getattr(self, attr)}")  # type: ignore[attr-defined]
            self._save_settings()  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

    def set_aifred_temperature_input(self, value: str) -> None:
        self._set_temperature_input("aifred", value)

    def set_sokrates_temperature_input(self, value: str) -> None:
        self._set_temperature_input("sokrates", value)

    def set_salomo_temperature_input(self, value: str) -> None:
        self._set_temperature_input("salomo", value)

    def set_vision_temperature_input(self, value: str) -> None:
        self._set_temperature_input("vision", value)

    # ================================================================
    # MULTI-AGENT MODE SETTINGS
    # ================================================================

    def set_multi_agent_mode(self, mode: str) -> None:
        """Set multi-agent discussion mode."""
        self.multi_agent_mode = mode
        # Reset Sokrates panel when switching modes
        self.show_sokrates_panel = False
        self.sokrates_critique = ""
        self.sokrates_pro_args = ""
        self.sokrates_contra_args = ""
        self.debate_round = 0

        # Enforce agent selection rules per mode
        if mode == "symposion":
            # Symposion: ensure at least one agent is selected
            if not self.symposion_agents:
                self.symposion_agents = ["aifred"]
        elif mode in ("critical_review", "auto_consensus", "tribunal"):
            # These modes always use AIfred + Sokrates + Salomo
            self.active_agent = "aifred"

        self._persist_session_config()  # type: ignore[attr-defined]

        mode_labels = {
            "standard": "Standard",
            "critical_review": "Critical Review",
            "auto_consensus": "Auto-Consensus",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
        }
        self.add_debug(f"\U0001f916 Discussion mode: {mode_labels.get(mode, mode)}")  # type: ignore[attr-defined]


    def increase_debate_rounds(self) -> None:
        """Increase max debate rounds by 1 (max 10)."""
        if self.max_debate_rounds < 10:
            self.max_debate_rounds += 1
            self._save_settings()  # type: ignore[attr-defined]
            self.add_debug(f"\U0001f504 Max debate rounds: {self.max_debate_rounds}")  # type: ignore[attr-defined]

    def decrease_debate_rounds(self) -> None:
        """Decrease max debate rounds by 1 (min 1)."""
        if self.max_debate_rounds > 1:
            self.max_debate_rounds -= 1
            self._save_settings()  # type: ignore[attr-defined]
            self.add_debug(f"\U0001f504 Max debate rounds: {self.max_debate_rounds}")  # type: ignore[attr-defined]


    def toggle_consensus_type(self, checked: bool) -> None:
        """Toggle consensus type between majority (off) and unanimous (on)."""
        self.consensus_type = "unanimous" if checked else "majority"
        self._save_settings()  # type: ignore[attr-defined]
        type_label = "3/3 unanimous" if checked else "2/3 majority"
        self.add_debug(f"\U0001f5f3\ufe0f Consensus type: {type_label}")  # type: ignore[attr-defined]

    @rx.var
    def is_unanimous_consensus(self) -> bool:
        """Check if consensus type is unanimous (for toggle state)."""
        return self.consensus_type == "unanimous"

    @rx.var(deps=["consensus_type", "ui_language"], auto_deps=False)
    def consensus_toggle_tooltip(self) -> str:
        """Get tooltip text for consensus toggle based on current state and language."""
        from ..lib.i18n import t
        if self.consensus_type == "unanimous":
            return t("consensus_toggle_tooltip_on", lang=self.ui_language)  # type: ignore[attr-defined]
        return t("consensus_toggle_tooltip_off", lang=self.ui_language)  # type: ignore[attr-defined]

    @rx.var(deps=["ui_language"], auto_deps=False)
    def speed_switch_tooltip(self) -> str:
        """Localized tooltip for the Ctx/Speed switch."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return t("speed_switch_tooltip", lang=lang)

    @rx.var(deps=["ui_language"], auto_deps=False)
    def multi_agent_mode_options(self) -> List[List[str]]:
        """Get localized multi-agent mode options as [key, label] pairs for dropdown."""
        from ..lib import TranslationManager
        return [
            ["standard", TranslationManager.get_text("multi_agent_standard", self.ui_language)],  # type: ignore[attr-defined]
            ["critical_review", TranslationManager.get_text("multi_agent_critical_review", self.ui_language)],  # type: ignore[attr-defined]
            ["auto_consensus", TranslationManager.get_text("multi_agent_auto_consensus", self.ui_language)],  # type: ignore[attr-defined]
            ["tribunal", TranslationManager.get_text("multi_agent_tribunal", self.ui_language)],  # type: ignore[attr-defined]
            ["symposion", TranslationManager.get_text("multi_agent_symposion", self.ui_language)],  # type: ignore[attr-defined]
        ]

    # Core agents used in fixed multi-agent modes
    CORE_AGENTS: ClassVar[set[str]] = {"aifred", "sokrates", "salomo"}
    # Modes where only core agents participate and selection is locked
    FIXED_MODES: ClassVar[set[str]] = {"critical_review", "auto_consensus", "tribunal"}

    @rx.var(deps=["_agent_dropdown_items"], auto_deps=False)
    def selectable_agents(self) -> List[dict[str, str]]:
        """Agent list for the active-agent toggle row (id, display_name, emoji).

        Excludes:
        - any agent with role=system (calibration, vision, etc. — internal
          workflows that never appear as a user-selectable chat agent)
        """
        from ..lib.agent_config import load_agents_raw
        agents = load_agents_raw()
        result: list[dict[str, str]] = []
        for aid, adata in agents.items():
            if adata.get("role") == "system":
                continue
            result.append({
                "id": aid,
                "display_name": adata.get("display_name", aid.capitalize()),
                "emoji": adata.get("emoji", "\U0001f916"),
            })
        return result

    @rx.var(deps=["multi_agent_mode"], auto_deps=False)
    def is_fixed_agent_mode(self) -> bool:
        """True when the current mode locks agents to AIfred+Sokrates+Salomo."""
        return self.multi_agent_mode in self.FIXED_MODES

    def toggle_agent_memory(self) -> None:
        """Toggle agent memory on/off (incognito mode)."""
        self.agent_memory_enabled = not self.agent_memory_enabled
        if self.agent_memory_enabled:
            self.add_debug("🔓 Agent memory enabled")  # type: ignore[attr-defined]
        else:
            self.add_debug("🔒 Incognito mode (no memory)")  # type: ignore[attr-defined]

    def set_active_agent(self, agent_id: str) -> None:
        """Set which agent responds to messages. In Symposion mode, toggles multi-select."""
        # Fixed modes: agents are locked, ignore clicks
        if self.multi_agent_mode in self.FIXED_MODES:
            return
        if self.multi_agent_mode == "symposion":
            self.toggle_symposion_agent(agent_id)
            return
        self.active_agent = agent_id
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        label = cfg.display_name if cfg else agent_id.capitalize()
        self.add_debug(f"🎯 Active agent: {label}")  # type: ignore[attr-defined]
        self._persist_session_config()  # type: ignore[attr-defined]

    def toggle_symposion_agent(self, agent_id: str) -> None:
        """Toggle an agent's participation in Symposion mode."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        label = cfg.display_name if cfg else agent_id.capitalize()
        if agent_id in self.symposion_agents:
            # Don't allow deselecting the last agent
            if len(self.symposion_agents) <= 1:
                self.add_debug(f"🏛️ Symposion: {label} is the last agent, cannot be removed")  # type: ignore[attr-defined]
                return
            self.symposion_agents = [a for a in self.symposion_agents if a != agent_id]
            self.add_debug(f"🏛️ Symposion: {label} removed")  # type: ignore[attr-defined]
        else:
            self.symposion_agents = self.symposion_agents + [agent_id]
            self.add_debug(f"🏛️ Symposion: {label} added")  # type: ignore[attr-defined]
        self._persist_session_config()  # type: ignore[attr-defined]


    # ================================================================
    # MULTI-AGENT RUNTIME STATE MANAGEMENT
    # ================================================================



    def reset_sokrates_state(self) -> None:
        """Reset all Sokrates-related runtime state."""
        self.sokrates_critique = ""
        self.sokrates_pro_args = ""
        self.sokrates_contra_args = ""
        self.show_sokrates_panel = False
        self.debate_round = 0
        self.debate_user_interjection = ""
        self.debate_in_progress = False

    def reset_salomo_state(self) -> None:
        """Reset all Salomo-related runtime state."""
        self.salomo_synthesis = ""
        self.show_salomo_panel = False


    # ================================================================
    # SOKRATES / SALOMO MODEL SELECTION
    # ================================================================


    @rx.var(deps=["available_models", "ui_language"], auto_deps=False)
    def sokrates_available_models(self) -> list[str]:
        """Model list with localized '(wie AIfred-LLM)' as first selectable option."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return [t("sokrates_llm_same", lang=lang)] + list(self.available_models)  # type: ignore[attr-defined]

    @rx.var(deps=["available_models", "ui_language"], auto_deps=False)
    def salomo_available_models(self) -> list[str]:
        """Model list with localized '(wie AIfred-LLM)' as first selectable option."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return [t("sokrates_llm_same", lang=lang)] + list(self.available_models)  # type: ignore[attr-defined]

    @rx.var(deps=["sokrates_model", "ui_language"], auto_deps=False)
    def sokrates_model_select_value(self) -> str:
        """Maps empty string (auto) to the localized sentinel label for the select."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return t("sokrates_llm_same", lang=lang) if self.sokrates_model == "" else self.sokrates_model

    @rx.var(deps=["salomo_model", "ui_language"], auto_deps=False)
    def salomo_model_select_value(self) -> str:
        """Maps empty string (auto) to the localized sentinel label for the select."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return t("sokrates_llm_same", lang=lang) if self.salomo_model == "" else self.salomo_model

    def set_sokrates_model(self, model: str) -> None:
        """Set Sokrates LLM model for multi-agent debate."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        if model == t("sokrates_llm_same", lang=lang):
            model = ""
        self.sokrates_model = model
        self.sokrates_model_id = self._resolve_model_id(model)  # type: ignore[attr-defined]

        if not self.sokrates_model_id:
            # "(wie AIfred-LLM)" selected -- clear speed variant
            self.sokrates_has_speed_variant = False
            self.sokrates_speed_mode = False

        # Load all model parameters from cache
        if self.backend_id == "ollama" and self.sokrates_model_id:  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import get_model_parameters
            params = get_model_parameters(self.sokrates_model_id)
            self.sokrates_rope_factor = params["rope_factor"]
            self.sokrates_max_context = params["max_context"]
            self.sokrates_is_hybrid = params["is_hybrid"]
            self.sokrates_supports_thinking = params["supports_thinking"]
        elif self.backend_type == "llamacpp" and self.sokrates_model_id:  # type: ignore[attr-defined]
            from ..lib.calibration import model_has_speed_variant
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
            )
            self.sokrates_rope_factor = 1.0
            self.sokrates_max_context = get_llamacpp_calibration(self.sokrates_model_id) or 0
            self.sokrates_is_hybrid = False
            self.sokrates_supports_thinking = get_thinking_support_for_model(self.sokrates_model_id)
            self.sokrates_has_speed_variant = model_has_speed_variant(self.sokrates_model_id)
            if not self.sokrates_has_speed_variant:
                self.sokrates_speed_mode = False
        self._load_agent_reasoning_levels("sokrates", self.sokrates_model_id)

        # Reset sampling params to model defaults
        self._reset_agent_sampling("sokrates")

        self._save_settings()  # type: ignore[attr-defined]
        if model:
            self.add_debug(f"\U0001f9e0 Sokrates-LLM: {model}")  # type: ignore[attr-defined]
            self._show_model_calibration_info(self.sokrates_model_id)  # type: ignore[attr-defined]
        else:
            self.add_debug("\U0001f9e0 Sokrates-LLM: (same as Main-LLM)")  # type: ignore[attr-defined]

    def set_salomo_model(self, model: str) -> None:
        """Set Salomo LLM model for multi-agent debate."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        if model == t("sokrates_llm_same", lang=lang):
            model = ""
        self.salomo_model = model
        self.salomo_model_id = self._resolve_model_id(model)  # type: ignore[attr-defined]

        if not self.salomo_model_id:
            # "(wie AIfred-LLM)" selected -- clear speed variant
            self.salomo_has_speed_variant = False
            self.salomo_speed_mode = False

        # Load all model parameters from cache
        if self.backend_id == "ollama" and self.salomo_model_id:  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import get_model_parameters
            params = get_model_parameters(self.salomo_model_id)
            self.salomo_rope_factor = params["rope_factor"]
            self.salomo_max_context = params["max_context"]
            self.salomo_is_hybrid = params["is_hybrid"]
            self.salomo_supports_thinking = params["supports_thinking"]
        elif self.backend_type == "llamacpp" and self.salomo_model_id:  # type: ignore[attr-defined]
            from ..lib.calibration import model_has_speed_variant
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
            )
            self.salomo_rope_factor = 1.0
            self.salomo_max_context = get_llamacpp_calibration(self.salomo_model_id) or 0
            self.salomo_is_hybrid = False
            self.salomo_supports_thinking = get_thinking_support_for_model(self.salomo_model_id)
            self.salomo_has_speed_variant = model_has_speed_variant(self.salomo_model_id)
            if not self.salomo_has_speed_variant:
                self.salomo_speed_mode = False
        self._load_agent_reasoning_levels("salomo", self.salomo_model_id)

        # Reset sampling params to model defaults
        self._reset_agent_sampling("salomo")

        self._save_settings()  # type: ignore[attr-defined]
        if model:
            self.add_debug(f"\U0001f451 Salomo-LLM: {model}")  # type: ignore[attr-defined]
            self._show_model_calibration_info(self.salomo_model_id)  # type: ignore[attr-defined]
        else:
            self.add_debug("\U0001f451 Salomo-LLM: (same as Main-LLM)")  # type: ignore[attr-defined]
