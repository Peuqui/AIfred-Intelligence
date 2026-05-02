"""Agent configuration mixin for AIfred state.

Handles per-agent personality, reasoning, thinking mode,
sampling parameters, speed mode, RoPE factors, multi-agent mode settings,
temperature configuration, and model selection for Sokrates/Salomo.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

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
    sokrates_max_context: int = 0
    sokrates_is_hybrid: bool = False
    sokrates_supports_thinking: bool | None = None
    salomo_max_context: int = 0
    salomo_is_hybrid: bool = False
    salomo_supports_thinking: bool | None = None
    vision_max_context: int = 0
    vision_is_hybrid: bool = False
    vision_supports_thinking: bool | None = None

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

        Works for personality, reasoning, and thinking.
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

    # ── Reasoning Toggles ─────────────────────────────────────────

    def toggle_aifred_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("aifred", "reasoning")

    def toggle_sokrates_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("sokrates", "reasoning")

    def toggle_salomo_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("salomo", "reasoning")

    def toggle_vision_reasoning(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("vision", "reasoning")

    # ── Thinking Toggles ──────────────────────────────────────────

    def toggle_aifred_thinking(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("aifred", "thinking")

    def toggle_sokrates_thinking(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("sokrates", "thinking")

    def toggle_salomo_thinking(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("salomo", "thinking")

    def toggle_vision_thinking(self, _value: bool | None = None) -> None:
        self._toggle_agent_feature("vision", "thinking")

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

        This is the SINGLE SOURCE OF TRUTH for model variant resolution.
        The *_model_id state vars always contain the base ID.
        All code that sends model IDs to the backend must use this method.

        Priority:
        1. Speed mode → base_id-speed
        2. TTS on GPU (llamacpp) → base_id-tts-{engine}
        3. Otherwise → base_id
        """
        base_id: str = getattr(self, f"{agent}_model_id")
        if not base_id:
            return base_id

        # Speed mode takes priority (already has reduced context baked in)
        speed_on: bool = getattr(self, f"{agent}_speed_mode")
        has_speed: bool = getattr(self, f"{agent}_has_speed_variant")
        if speed_on and has_speed:
            return f"{base_id}-speed"

        # TTS on GPU: use TTS-calibrated variant (reduced -c for VRAM sharing)
        # Only when TTS is explicitly enabled in the UI. A leftover running
        # TTS container from a previous session must NOT silently switch the
        # model profile — the user's toggle is authoritative.
        if self.backend_type == "llamacpp" and self.enable_tts:  # type: ignore[attr-defined]
            from ..lib.tts_engine_manager import _detect_running_tts_engine, GPU_ENGINES
            if self.tts_engine in GPU_ENGINES:  # type: ignore[attr-defined]
                running_tts = _detect_running_tts_engine()
                if running_tts:
                    from ..lib.calibration import parse_llamaswap_config
                    from ..lib.config import LLAMASWAP_CONFIG_PATH
                    tts_variant = f"{base_id}-tts-{running_tts}"
                    swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                    if tts_variant in swap_cfg:
                        return tts_variant
                    from ..lib.logging_utils import log_message
                    log_message(f"⚠️ _effective_model_id: TTS variant {tts_variant} NOT in config")

        return base_id

    # ================================================================
    # SPEED MODE TOGGLES (llamacpp only)
    # ================================================================

    def _toggle_speed_mode(self, agent: str) -> None:
        """Toggle speed/context mode for any agent."""
        attr = f"{agent}_speed_mode"
        setattr(self, attr, not getattr(self, attr))
        base_id = getattr(self, f"{agent}_model_id", "") or self.aifred_model_id  # type: ignore[attr-defined]
        max_ctx = getattr(self, f"{agent}_max_context", 0)
        self.add_debug(f"\U0001f500 {agent.capitalize()} mode: {self._speed_mode_debug_str(getattr(self, attr), base_id, max_ctx)}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_aifred_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("aifred")

    def toggle_sokrates_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("sokrates")

    def toggle_salomo_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("salomo")

    def toggle_vision_speed_mode(self, _value: bool | None = None) -> None:
        self._toggle_speed_mode("vision")

    def _speed_mode_debug_str(self, speed_on: bool, base_model_id: str, max_ctx: int) -> str:
        """Build debug string for speed mode toggle showing tensor-split and context."""
        from ..lib.model_vram_cache import get_llamacpp_speed_split
        from ..lib.formatting import format_number
        from ..lib.config import MIN_USEFUL_CONTEXT_TOKENS
        if speed_on:
            cuda0, rest, speed_ctx = get_llamacpp_speed_split(base_model_id)
            split_str = f" ({cuda0}:{rest} tensor-split)" if cuda0 > 0 else ""
            ctx = format_number(speed_ctx if speed_ctx > 0 else MIN_USEFUL_CONTEXT_TOKENS)
            return f"\u26a1 speed \u2014 {ctx} tok{split_str}"
        else:
            ctx = format_number(max_ctx) if max_ctx else "n/a"
            return f"\U0001f4d6 context \u2014 {ctx} tok"

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
            from ..lib.model_vram_cache import set_rope_factor_for_model, get_ollama_calibrated_max_context, get_rope_factor_for_model, get_llamacpp_calibration
            from ..lib.formatting import format_number
            set_rope_factor_for_model(self.aifred_model_id, factor)  # type: ignore[attr-defined]

            # Helper for context limit display (merge GB and ctx into one bracket)
            def format_model_with_ctx(model_display: str, model_id: str) -> str:
                if not model_id:
                    return model_display
                # Backend-aware calibration lookup
                if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                    ctx = get_llamacpp_calibration(model_id)
                else:
                    ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                if ctx:
                    if model_display.endswith(")"):
                        return model_display[:-1] + f", {format_number(ctx)} ctx)"
                    return f"{model_display} ({format_number(ctx)} ctx)"
                else:
                    if model_display.endswith(")"):
                        return model_display[:-1] + ", ctx not calibrated)"
                    return f"{model_display} (ctx not calibrated)"

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

    def set_sokrates_temperature(self, temp: list[float]) -> None:
        """Set Sokrates temperature (from slider which returns list[float])."""
        self.sokrates_temperature = temp[0] if isinstance(temp, list) else temp
        self._save_settings()  # type: ignore[attr-defined]

    def set_sokrates_temperature_offset(self, offset: list[float]) -> None:
        """Set Sokrates temperature offset for Auto mode (from slider which returns list[float])."""
        self.sokrates_temperature_offset = offset[0] if isinstance(offset, list) else offset
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"\U0001f321\ufe0f Sokrates Offset: +{self.sokrates_temperature_offset:.1f}")  # type: ignore[attr-defined]

    def set_salomo_temperature(self, temp: list[float]) -> None:
        """Set Salomo temperature for Manual mode (from slider which returns list[float])."""
        self.salomo_temperature = temp[0] if isinstance(temp, list) else temp
        self._save_settings()  # type: ignore[attr-defined]

    def set_salomo_temperature_offset(self, offset: list[float]) -> None:
        """Set Salomo temperature offset for Auto mode (from slider which returns list[float])."""
        self.salomo_temperature_offset = offset[0] if isinstance(offset, list) else offset
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"\U0001f321\ufe0f Salomo Offset: +{self.salomo_temperature_offset:.1f}")  # type: ignore[attr-defined]

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

    def set_max_debate_rounds(self, value: list[float]) -> None:
        """Set maximum debate rounds (from slider)."""
        self.max_debate_rounds = int(value[0])
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"\U0001f504 Max debate rounds: {self.max_debate_rounds}")  # type: ignore[attr-defined]

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

    def set_consensus_type(self, consensus_type: str | list[str]) -> None:
        """Set consensus type for auto_consensus mode ('majority' or 'unanimous')."""
        # Handle both str and list[str] from segmented_control
        if isinstance(consensus_type, list):
            consensus_type = consensus_type[0] if consensus_type else "majority"
        self.consensus_type = consensus_type
        self._save_settings()  # type: ignore[attr-defined]
        type_label = "2/3 majority" if consensus_type == "majority" else "3/3 unanimous"
        self.add_debug(f"\U0001f5f3\ufe0f Consensus type: {type_label}")  # type: ignore[attr-defined]

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
        """Agent list for the active-agent toggle row (id, display_name, emoji)."""
        from ..lib.agent_config import load_agents_raw
        agents = load_agents_raw()
        result: list[dict[str, str]] = []
        for aid, adata in agents.items():
            if aid == "vision":
                continue  # Vision is not a chat agent
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
                self.add_debug(f"🏛️ Symposion: {label} ist der letzte Agent, kann nicht entfernt werden")  # type: ignore[attr-defined]
                return
            self.symposion_agents = [a for a in self.symposion_agents if a != agent_id]
            self.add_debug(f"🏛️ Symposion: {label} removed")  # type: ignore[attr-defined]
        else:
            self.symposion_agents = self.symposion_agents + [agent_id]
            self.add_debug(f"🏛️ Symposion: {label} added")  # type: ignore[attr-defined]
        self._persist_session_config()  # type: ignore[attr-defined]

    @rx.var(deps=["ui_language", "multi_agent_mode"], auto_deps=False)
    def multi_agent_mode_info(self) -> str:
        """Get localized description for the currently selected multi-agent mode."""
        from ..lib import TranslationManager
        info_key = f"multi_agent_info_{self.multi_agent_mode}"
        return TranslationManager.get_text(info_key, self.ui_language)  # type: ignore[attr-defined]

    # ================================================================
    # MULTI-AGENT RUNTIME STATE MANAGEMENT
    # ================================================================

    def queue_user_interjection(self, text: str) -> None:
        """Queue user input during active debate."""
        if self.debate_in_progress and text.strip():
            self.debate_user_interjection = text.strip()
            self.add_debug(f"\U0001f4ac User interjection queued: {text[:50]}...")  # type: ignore[attr-defined]

    def get_and_clear_user_interjection(self) -> str:
        """Get queued user interjection and clear it (called by orchestrator)."""
        interjection = self.debate_user_interjection
        self.debate_user_interjection = ""
        return interjection

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

    def reset_multi_agent_state(self) -> None:
        """Reset all multi-agent runtime state (Sokrates + Salomo)."""
        self.reset_sokrates_state()
        self.reset_salomo_state()

    # ================================================================
    # SOKRATES / SALOMO MODEL SELECTION
    # ================================================================

    @rx.var
    def sokrates_model_label(self) -> str:
        """Get display label for Sokrates model."""
        if not self.sokrates_model_id:
            return ""  # Empty = use Main-LLM
        return self.available_models_dict.get(self.sokrates_model_id, self.sokrates_model_id)  # type: ignore[attr-defined, no-any-return]

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
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
                get_llamacpp_speed_split,
            )
            self.sokrates_rope_factor = 1.0
            self.sokrates_max_context = get_llamacpp_calibration(self.sokrates_model_id) or 0
            self.sokrates_is_hybrid = False
            self.sokrates_supports_thinking = get_thinking_support_for_model(self.sokrates_model_id)
            self.sokrates_has_speed_variant = get_llamacpp_speed_split(self.sokrates_model_id)[0] > 0
            if not self.sokrates_has_speed_variant:
                self.sokrates_speed_mode = False

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
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
                get_llamacpp_speed_split,
            )
            self.salomo_rope_factor = 1.0
            self.salomo_max_context = get_llamacpp_calibration(self.salomo_model_id) or 0
            self.salomo_is_hybrid = False
            self.salomo_supports_thinking = get_thinking_support_for_model(self.salomo_model_id)
            self.salomo_has_speed_variant = get_llamacpp_speed_split(self.salomo_model_id)[0] > 0
            if not self.salomo_has_speed_variant:
                self.salomo_speed_mode = False

        # Reset sampling params to model defaults
        self._reset_agent_sampling("salomo")

        self._save_settings()  # type: ignore[attr-defined]
        if model:
            self.add_debug(f"\U0001f451 Salomo-LLM: {model}")  # type: ignore[attr-defined]
            self._show_model_calibration_info(self.salomo_model_id)  # type: ignore[attr-defined]
        else:
            self.add_debug("\U0001f451 Salomo-LLM: (same as Main-LLM)")  # type: ignore[attr-defined]

    # ================================================================
    # AGENT EDITOR STATE & HANDLERS
    # ================================================================

    # Editor modal visibility
    agent_editor_open: bool = False

    # Editor view mode: "config", "memory" or "database"
    agent_editor_mode: str = "config"

    # Scheduler state
    scheduler_job_list: List[Dict[str, str]] = []
    scheduler_edit_id: str = ""  # Job being edited ("" = none, "new" = create)
    scheduler_edit_name: str = ""
    scheduler_edit_type: str = "cron"
    scheduler_edit_expr: str = ""
    scheduler_edit_message: str = ""
    scheduler_edit_agent: str = "aifred"
    scheduler_edit_delivery: str = "review"
    scheduler_edit_channel: str = ""
    scheduler_edit_tier: str = "1"
    scheduler_edit_webhook_url: str = ""
    scheduler_edit_recipient: str = ""
    # Structured schedule fields (compose to scheduler_edit_expr on save)
    scheduler_cron_min: str = "0"
    scheduler_cron_hour: str = "8"
    scheduler_cron_dom: str = "*"
    scheduler_cron_month: str = "*"
    scheduler_cron_dow: str = "*"
    scheduler_interval_value: str = "60"
    scheduler_interval_unit: str = "minutes"  # minutes, hours, days
    scheduler_once_date: str = ""
    scheduler_once_time: str = "10:00"

    # Database browser state (system collections: research_cache, aifred_documents)
    db_browser_collection: str = ""  # Selected collection name
    db_browser_entries: List[Dict[str, str]] = []  # Entries for selected collection
    db_clear_confirm: bool = False  # Confirmation state for clear-all

    # Orphan-cleanup state (only meaningful when db_browser_collection == aifred_documents)
    db_orphans: List[Dict[str, Any]] = []      # one entry per orphaned document (not per chunk)
    db_orphans_visible: bool = False           # toggles the orphan section

    # Memory browser state
    memory_browser_agent: str = ""  # Selected agent in memory browser ("" = overview)
    memory_browser_agent_display: str = ""  # Display name of selected agent
    memory_browser_entries: List[Dict[str, str]] = []  # Entries for selected agent
    memory_browser_collections: List[Dict[str, str]] = []  # Collection overview
    memory_browser_filter: str = "all"  # "all", "session", "agent"

    # Agent dropdown options for the editor (["emoji name", ...])
    _agent_dropdown_items: List[str] = []
    # Mapping: display label → agent_id (for dropdown selection)
    _agent_id_by_label: Dict[str, str] = {}

    # Currently editing agent (empty = creating new)
    editor_agent_id: str = ""
    editor_display_name: str = ""
    editor_emoji: str = ""
    _editor_description: str = ""
    editor_role: str = "custom"
    editor_model: str = ""  # Cloud model id, only used for system-role agents
    # Reasoning toggle for system-role agents (e.g. calibration). Mirrors
    # agents.json `toggles.reasoning`. Off by default — system workflows
    # typically don't benefit enough from chain-of-thought to justify the
    # 30-120 s per-turn overhead.
    editor_system_reasoning: bool = False

    # Prompt layer editor state
    editor_prompt_tab: str = "identity"
    _editor_prompt_content: str = ""
    editor_prompt_lang: str = "de"  # Language toggle for prompt editor
    # Available prompt keys for current agent (for tab rendering)
    editor_prompt_keys: List[str] = []

    # New agent creation fields
    _editor_new_agent_id: str = ""

    # Delete confirmation
    editor_delete_confirm: str = ""
    # Memory clear confirmation
    editor_memory_confirm: str = ""

    # ── Agent Bundle Export/Import ──────────────────────────────
    bundle_export_open: bool = False
    bundle_export_selected: List[str] = []
    # Snapshot of all agents for the export modal — refreshed when modal opens
    bundle_all_agents: List[Dict[str, str]] = []

    bundle_import_open: bool = False
    bundle_import_uploaded_b64: str = ""  # base64-encoded ZIP, kept in state until confirm
    bundle_import_agents: List[Dict[str, Any]] = []  # output of peek_bundle
    bundle_import_selected: List[str] = []
    bundle_import_conflict: str = "rename"  # abort | overwrite | rename
    bundle_import_error: str = ""

    # Emoji picker visibility
    editor_emoji_picker_open: bool = False

    # Tool whitelist editor — list of all tool names with enabled/disabled state
    editor_tools: Dict[str, bool] = {}


    @rx.var(deps=["_agent_dropdown_items"], auto_deps=False)
    def agent_dropdown_options(self) -> List[str]:
        """Agent dropdown labels for the editor (e.g. ['🎩 AIfred', '🏛️ Sokrates', ...])."""
        return self._agent_dropdown_items

    @rx.var(deps=["editor_agent_id", "_agent_dropdown_items"], auto_deps=False)
    def editor_agent_dropdown_value(self) -> str:
        """Current dropdown value matching the selected agent."""
        if not self.editor_agent_id:
            return ""
        for item in self._agent_dropdown_items:
            # Format: "emoji Name" — match by checking if it maps to this agent_id
            # We store a parallel mapping, but simpler: just find by id
            pass
        # Reconstruct the label from current editor state
        return f"{self.editor_emoji} {self.editor_display_name}" if self.editor_agent_id else ""

    # Separator + label for Automatik-LLM in agent dropdown
    _AUTOMATIK_SEPARATOR = "─────────────────"
    _AUTOMATIK_LABEL = "⚡ Automatik-LLM"

    @rx.var
    def editor_is_system_agent(self) -> bool:
        """True for system-role agents that use the locked-down editor view
        (only model + prompts editable). Excludes Automatik-LLM, which has
        its own dedicated UI path."""
        return self.editor_role == "system" and self.editor_agent_id != "automatik"

    def set_editor_model(self, value: str) -> None:
        """Set the cloud model for a system-role agent."""
        if value:
            self.editor_model = value
            self.editor_dirty = True  # type: ignore[attr-defined]

    def toggle_editor_system_reasoning(self) -> None:
        """Flip the reasoning toggle for a system-role agent."""
        self.editor_system_reasoning = not self.editor_system_reasoning
        self.editor_dirty = True  # type: ignore[attr-defined]

    def _refresh_agent_dropdown(self) -> None:
        """Refresh the agent dropdown items from config.

        Layout:
          - Regular agents (role != "system")
          - separator
          - Automatik-LLM
          - System agents from agents.json (role == "system"),
            each labelled with its emoji + display_name
        """
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        regular = {aid: d for aid, d in raw.items() if d.get("role") != "system"}
        system = {aid: d for aid, d in raw.items() if d.get("role") == "system"}

        items = [f"{d['emoji']} {d['display_name']}" for d in regular.values()]
        items.append(self._AUTOMATIK_SEPARATOR)
        items.append(self._AUTOMATIK_LABEL)
        for d in system.values():
            items.append(f"{d['emoji']} {d['display_name']}")
        self._agent_dropdown_items = items

        self._agent_id_by_label = {
            f"{d['emoji']} {d['display_name']}": aid for aid, d in raw.items()
        }
        self._agent_id_by_label[self._AUTOMATIK_LABEL] = "automatik"

    def open_agent_editor(self):
        """Open the agent editor modal, select first agent."""
        self._refresh_agent_dropdown()
        self.agent_editor_mode = "config"
        self.agent_editor_open = True
        self.editor_delete_confirm = ""
        self.editor_emoji_picker_open = False
        self.editor_dirty = False
        self.editor_dirty_confirm = False

        # Load first agent's data into state
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        if raw:
            first_id = next(iter(raw))
            self._load_agent_into_state(first_id)

        # Yield to render the modal DOM first
        yield

        # Now populate DOM fields (modal exists now)
        yield self._push_editor_dom()

    # Dirty flag — set on any keystroke in editor fields
    editor_dirty: bool = False
    editor_dirty_confirm: bool = False  # Show unsaved-changes dialog
    _pending_agent_label: str = ""
    _pending_close: bool = False

    def mark_editor_dirty(self) -> None:
        """Mark editor as having unsaved changes (called on any keystroke)."""
        self.editor_dirty = True

    def close_agent_editor(self) -> None:
        """Close the agent editor modal (no dirty check)."""
        self.agent_editor_open = False
        self.editor_agent_id = ""
        self.editor_delete_confirm = ""
        self.editor_dirty_confirm = False
        self.editor_dirty = False

    def close_editor_with_dirty_check(self) -> None:
        """Close editor — warn if unsaved changes."""
        if not self.editor_dirty or self.agent_editor_mode != "config":
            self.close_agent_editor()
            return
        self._pending_close = True
        self._pending_agent_label = ""
        self.editor_dirty_confirm = True

    def select_editor_agent_with_dirty_check(self, label: str):
        """Switch agent — warn if unsaved changes."""
        if not self.editor_dirty:
            return self.select_editor_agent(label)
        self._pending_agent_label = label
        self._pending_close = False
        self.editor_dirty_confirm = True

    def confirm_discard_changes(self):
        """User confirmed discarding unsaved changes — reload current agent, stay open."""
        self.editor_dirty_confirm = False
        self.editor_dirty = False
        if self._pending_close:
            self._pending_close = False
            # Don't close — just reload the current agent to discard changes
            if self.editor_agent_id:
                self._load_agent_into_state(self.editor_agent_id)
                return self._push_editor_dom()
            return
        if self._pending_agent_label:
            label = self._pending_agent_label
            self._pending_agent_label = ""
            self.select_editor_agent(label)
            return self._push_editor_dom()

    def cancel_discard_changes(self) -> None:
        """User cancelled — stay on current agent."""
        self.editor_dirty_confirm = False
        self._pending_agent_label = ""
        self._pending_close = False

    def set_agent_editor_tab(self, tab: str):
        """Switch between config, memory and database tabs."""
        self.agent_editor_mode = tab
        if tab == "config":
            # Re-push DOM fields when switching back to config tab
            yield
            yield self._push_editor_dom()
        elif tab == "memory":
            self.open_memory_browser()
        elif tab == "database":
            self.db_clear_confirm = False
            if self.db_browser_collection:
                self._load_db_entries()
        elif tab == "plugins":
            # Reuse logic from open_plugin_manager (without opening separate modal)
            from ..lib.credential_broker import broker
            from ..lib.plugin_registry import discover_tools, all_channels
            self.tool_plugin_toggles = {
                p.name: ("1" if p.is_available() else "") for p in discover_tools()
            }
            self.channel_allowlists = {
                "email": broker.get("email", "allowed_senders") or "-",
                "telegram": broker.get("telegram", "allowed_users") or "-",
                "discord": broker.get("discord", "channel_ids") or "-",
                "freeecho2": "",
            }
            # Ensure all channels have a security tier entry
            from ..lib.security import DEFAULT_TIER_BY_SOURCE, TIER_COMMUNICATE
            tiers = dict(self.channel_security_tiers)
            for ch_name in all_channels():
                if ch_name not in tiers:
                    tiers[ch_name] = DEFAULT_TIER_BY_SOURCE.get(ch_name, TIER_COMMUNICATE)
            self.channel_security_tiers = tiers
        elif tab == "audit":
            # Reuse logic from open_audit_log (without opening separate modal)
            import sqlite3
            from ..lib.config import SECURITY_AUDIT_DB
            entries: list[dict] = []
            if SECURITY_AUDIT_DB.exists():
                conn = sqlite3.connect(str(SECURITY_AUDIT_DB), timeout=5)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM tool_audit ORDER BY timestamp DESC LIMIT 50"
                ).fetchall()
                conn.close()
                for r in rows:
                    entries.append({
                        "timestamp": r["timestamp"] or "",
                        "source": r["source"] or "",
                        "tool_name": r["tool_name"] or "",
                        "tool_tier": str(r["tool_tier"]),
                        "success": "OK" if r["success"] else "FAIL",
                        "duration": f"{r['duration_ms']:.0f}ms" if r["duration_ms"] else "",
                    })
            self.audit_log_entries = entries
        elif tab == "scheduler":
            self._load_scheduler_jobs()

    @staticmethod
    def _human_cron(expr: str, lang: str) -> str:
        """Convert cron expression to human-readable text."""
        parts = expr.split()
        if len(parts) != 5:
            return expr
        minute, hour, dom, month, dow = parts

        dow_names = {
            "de": {"*": "", "1-5": "Mo–Fr", "6,0": "Wochenende",
                   "0": "So", "1": "Mo", "2": "Di", "3": "Mi",
                   "4": "Do", "5": "Fr", "6": "Sa"},
            "en": {"*": "", "1-5": "Mon–Fri", "6,0": "Weekend",
                   "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
                   "4": "Thu", "5": "Fri", "6": "Sat"},
        }
        month_names = {
            "de": {"*": "", "1": "Jan", "2": "Feb", "3": "Mär", "4": "Apr",
                   "5": "Mai", "6": "Jun", "7": "Jul", "8": "Aug",
                   "9": "Sep", "10": "Okt", "11": "Nov", "12": "Dez"},
            "en": {"*": "", "1": "Jan", "2": "Feb", "3": "Mar", "4": "Apr",
                   "5": "May", "6": "Jun", "7": "Jul", "8": "Aug",
                   "9": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"},
        }
        lc = lang if lang in ("de", "en") else "de"

        # Time part
        if hour == "*" and minute == "0":
            time_str = "Stündlich" if lc == "de" else "Hourly"
        elif hour == "*":
            time_str = f":{minute}"
        else:
            time_str = f"{hour}:{minute.zfill(2)}"

        # Day/week part
        dow_str = dow_names[lc].get(dow, dow)
        month_str = month_names[lc].get(month, month)

        fragments: list[str] = []
        fragments.append(time_str)

        if dow_str:
            fragments.append(dow_str)
        if dom != "*":
            tag = "Tag" if lc == "de" else "day"
            fragments.append(f"{dom}. {tag}" if lc == "de" else f"{tag} {dom}")
        if month_str:
            fragments.append(month_str)

        return " · ".join(fragments) if fragments else expr

    @staticmethod
    def _human_interval(expr: str, lang: str) -> str:
        """Convert interval seconds to human-readable text."""
        try:
            seconds = int(expr)
        except ValueError:
            return expr
        lc = lang if lang in ("de", "en") else "de"
        if seconds >= 86400 and seconds % 86400 == 0:
            n = seconds // 86400
            unit = "Tage" if lc == "de" else "days"
            prefix = "Alle" if lc == "de" else "Every"
            return f"{prefix} {n} {unit}"
        if seconds >= 3600 and seconds % 3600 == 0:
            n = seconds // 3600
            unit = "Stunden" if lc == "de" else "hours"
            if n == 1:
                unit = "Stunde" if lc == "de" else "hour"
            prefix = "Alle" if lc == "de" else "Every"
            return f"{prefix} {n} {unit}"
        n = max(1, seconds // 60)
        unit = "Minuten" if lc == "de" else "minutes"
        if n == 1:
            unit = "Minute" if lc == "de" else "minute"
        prefix = "Alle" if lc == "de" else "Every"
        return f"{prefix} {n} {unit}"

    @staticmethod
    def _human_once(expr: str, _lang: str) -> str:
        """Format ISO datetime for display."""
        if "T" in expr:
            date, time = expr.split("T", 1)
            return f"{date} {time[:5]}"
        return expr

    def _format_schedule_display(self, stype: str, expr: str) -> str:
        """Format schedule expression for human-readable display."""
        lang = self.ui_language if hasattr(self, "ui_language") else "de"
        if stype == "cron":
            return self._human_cron(expr, lang)
        if stype == "interval":
            return self._human_interval(expr, lang)
        if stype == "once":
            return self._human_once(expr, lang)
        return expr

    def _format_agent_display(self, agent_id: str) -> str:
        """Resolve agent ID to emoji + display name."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        if cfg:
            return f"{cfg.emoji} {cfg.display_name}"
        return agent_id

    def _format_delivery_display(self, delivery: str, channel: str) -> str:
        """Format delivery + channel for display."""
        lang = self.ui_language if hasattr(self, "ui_language") else "de"
        label = self._DELIVERY_DISPLAY.get(lang, self._DELIVERY_DISPLAY["de"]).get(
            delivery, delivery
        )
        if channel:
            return f"{label} → {channel}"
        return label

    @staticmethod
    def _format_datetime(iso_str: str) -> str:
        """Format ISO datetime to readable 'DD.MM.YYYY  HH:MM'."""
        if not iso_str or len(iso_str) < 16:
            return iso_str
        # "2026-04-13T10:00:00" → "13.04.2026  10:00"
        date_part = iso_str[:10]  # 2026-04-13
        time_part = iso_str[11:16]  # 10:00
        try:
            y, m, d = date_part.split("-")
            return f"{d}.{m}.{y}  {time_part}"
        except ValueError:
            return iso_str

    def _load_scheduler_jobs(self) -> None:
        """Load all scheduler jobs for display."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        jobs = store.list_all()
        self.scheduler_job_list = [
            {
                "job_id": str(j.job_id),
                "name": j.name,
                "schedule_type": j.schedule_type,
                "schedule_expr": j.schedule_expr,
                "type_display": self._TYPE_DISPLAY.get(
                    self.ui_language if hasattr(self, "ui_language") else "de",
                    self._TYPE_DISPLAY["de"],
                ).get(j.schedule_type, j.schedule_type),
                "schedule_display": self._format_schedule_display(
                    j.schedule_type, j.schedule_expr
                ),
                "agent_display": self._format_agent_display(
                    j.payload.get("agent", "aifred")
                ),
                "delivery_display": self._format_delivery_display(
                    j.payload.get("delivery", ""),
                    j.payload.get("channel", ""),
                ),
                "enabled": "1" if j.enabled else "",
                "next_run": self._format_datetime(j.next_run[:19]) if j.next_run else "",
                "last_run": self._format_datetime(j.last_run[:19]) if j.last_run else "",
                "created_at": self._format_datetime(j.created_at[:19]) if j.created_at else "",
                "message": j.payload.get("message", ""),
                "agent": j.payload.get("agent", "aifred"),
                "delivery": j.payload.get("delivery", ""),
                "channel": j.payload.get("channel", ""),
                "max_tier": str(j.max_tier),
                "retry_count": str(j.retry_count),
                "webhook_url": j.payload.get("webhook_url", ""),
                "recipient": j.payload.get("recipient", ""),
            }
            for j in jobs
        ]

    def toggle_scheduler_job(self, job_id: str) -> None:
        """Toggle a scheduler job enabled/disabled."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        job = store.get(int(job_id))
        if job:
            store.enable(int(job_id), not job.enabled)
        self._load_scheduler_jobs()

    def delete_scheduler_job(self, job_id: str) -> None:
        """Delete a scheduler job."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        store.delete(int(job_id))
        if self.scheduler_edit_id == job_id:
            self.scheduler_edit_id = ""
        self._load_scheduler_jobs()

    def edit_scheduler_job(self, job_id: str) -> None:
        """Load a job into the edit form."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        job = store.get(int(job_id))
        if not job:
            return
        self.scheduler_edit_id = str(job.job_id)
        self.scheduler_edit_name = job.name
        self.scheduler_edit_type = job.schedule_type
        self.scheduler_edit_expr = job.schedule_expr
        self.scheduler_edit_message = job.payload.get("message", "")
        self.scheduler_edit_agent = job.payload.get("agent", "aifred")
        self.scheduler_edit_delivery = job.payload.get("delivery", "review")
        self.scheduler_edit_channel = job.payload.get("channel", "")
        self.scheduler_edit_webhook_url = job.payload.get("webhook_url", "")
        self.scheduler_edit_recipient = job.payload.get("recipient", "")
        self.scheduler_edit_tier = str(job.max_tier)
        self._decompose_schedule_expr()

    def new_scheduler_job(self) -> None:
        """Open empty edit form for a new job."""
        self.scheduler_edit_id = "new"
        self.scheduler_edit_name = ""
        self.scheduler_edit_type = "cron"
        self.scheduler_edit_expr = ""
        self.scheduler_edit_message = ""
        self.scheduler_edit_agent = "aifred"
        self.scheduler_edit_delivery = "review"
        self.scheduler_edit_channel = ""
        self.scheduler_edit_webhook_url = ""
        self.scheduler_edit_recipient = ""
        self.scheduler_edit_tier = "1"
        self.scheduler_cron_min = "0"
        self.scheduler_cron_hour = "8"
        self.scheduler_cron_dom = "*"
        self.scheduler_cron_month = "*"
        self.scheduler_cron_dow = "*"
        self.scheduler_interval_value = "60"
        self.scheduler_interval_unit = "minutes"
        self.scheduler_once_date = ""
        self.scheduler_once_time = "10:00"

    def set_scheduler_edit_channel_safe(self, value: str) -> None:
        """Set channel, converting placeholder to empty string."""
        self.scheduler_edit_channel = "" if value in ("—", "keiner") else value

    # ── Schedule type / delivery i18n mapping ──────────────────

    _TYPE_MAP: dict[str, str] = {
        "Zeitplan": "cron", "Cron": "cron",
        "Intervall": "interval", "Interval": "interval",
        "Einmalig": "once", "Once": "once",
    }
    _TYPE_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"cron": "Zeitplan", "interval": "Intervall", "once": "Einmalig"},
        "en": {"cron": "Cron", "interval": "Interval", "once": "Once"},
    }
    _DELIVERY_MAP: dict[str, str] = {
        "Vorschau": "review", "Review": "review",
        "Senden": "announce", "Send": "announce",
        "Webhook": "webhook",
    }
    _DELIVERY_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"review": "Vorschau", "announce": "Senden", "webhook": "Webhook"},
        "en": {"review": "Review", "announce": "Send", "webhook": "Webhook"},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_type_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._TYPE_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_edit_type", "ui_language"], auto_deps=False)
    def sched_type_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._TYPE_DISPLAY[lang].get(self.scheduler_edit_type, self.scheduler_edit_type)

    def set_scheduler_type_from_label(self, label: str) -> None:
        new_type = self._TYPE_MAP.get(label, "cron")
        self.scheduler_edit_type = new_type

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_delivery_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._DELIVERY_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_edit_delivery", "ui_language"], auto_deps=False)
    def sched_delivery_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._DELIVERY_DISPLAY[lang].get(self.scheduler_edit_delivery, self.scheduler_edit_delivery)

    def set_scheduler_delivery_from_label(self, label: str) -> None:
        self.scheduler_edit_delivery = self._DELIVERY_MAP.get(label, "review")

    # ── Cron presets ───────────────────────────────────────────

    _CRON_PRESETS: list[tuple[str, str, str, str, str, str, str]] = [
        # (label_de, label_en, min, hour, dom, month, dow)
        ("Stündlich", "Hourly", "0", "*", "*", "*", "*"),
        ("Täglich", "Daily", "0", "8", "*", "*", "*"),
        ("Werktags", "Weekdays", "0", "8", "*", "*", "1-5"),
        ("Wöchentlich", "Weekly", "0", "8", "*", "*", "1"),
        ("Monatlich", "Monthly", "0", "8", "1", "*", "*"),
    ]

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_preset_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [p[idx] for p in self._CRON_PRESETS]

    def apply_cron_preset(self, label: str) -> None:
        for p in self._CRON_PRESETS:
            if label in (p[0], p[1]):
                self.scheduler_cron_min = p[2]
                self.scheduler_cron_hour = p[3]
                self.scheduler_cron_dom = p[4]
                self.scheduler_cron_month = p[5]
                self.scheduler_cron_dow = p[6]
                return

    # ── Weekday dropdown ──────────────────────────────────────

    _DOW_OPTIONS: list[tuple[str, str, str]] = [
        # (label_de, label_en, cron_value)
        ("Jeden Tag", "Every day", "*"),
        ("Mo–Fr", "Mon–Fri", "1-5"),
        ("Wochenende", "Weekend", "6,0"),
        ("Montag", "Monday", "1"),
        ("Dienstag", "Tuesday", "2"),
        ("Mittwoch", "Wednesday", "3"),
        ("Donnerstag", "Thursday", "4"),
        ("Freitag", "Friday", "5"),
        ("Samstag", "Saturday", "6"),
        ("Sonntag", "Sunday", "0"),
    ]
    _DOW_LABEL_TO_VAL: dict[str, str] = {
        label: val for de, en, val in _DOW_OPTIONS for label in (de, en)
    }
    _DOW_VAL_TO_LABEL: dict[str, dict[str, str]] = {
        "de": {val: de for de, _en, val in _DOW_OPTIONS},
        "en": {val: en for _de, en, val in _DOW_OPTIONS},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_dow_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [o[idx] for o in self._DOW_OPTIONS]

    @rx.var(deps=["scheduler_cron_dow", "ui_language"], auto_deps=False)
    def sched_dow_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._DOW_VAL_TO_LABEL[lang].get(
            self.scheduler_cron_dow, self.scheduler_cron_dow
        )

    def set_scheduler_dow_from_label(self, label: str) -> None:
        self.scheduler_cron_dow = self._DOW_LABEL_TO_VAL.get(label, "*")

    # ── Month dropdown ────────────────────────────────────────

    _MONTH_OPTIONS: list[tuple[str, str, str]] = [
        ("Jeden", "Every", "*"),
        ("Januar", "January", "1"),
        ("Februar", "February", "2"),
        ("März", "March", "3"),
        ("April", "April", "4"),
        ("Mai", "May", "5"),
        ("Juni", "June", "6"),
        ("Juli", "July", "7"),
        ("August", "August", "8"),
        ("September", "September", "9"),
        ("Oktober", "October", "10"),
        ("November", "November", "11"),
        ("Dezember", "December", "12"),
    ]
    _MONTH_LABEL_TO_VAL: dict[str, str] = {
        label: val for de, en, val in _MONTH_OPTIONS for label in (de, en)
    }
    _MONTH_VAL_TO_LABEL: dict[str, dict[str, str]] = {
        "de": {val: de for de, _en, val in _MONTH_OPTIONS},
        "en": {val: en for _de, en, val in _MONTH_OPTIONS},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_month_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [o[idx] for o in self._MONTH_OPTIONS]

    @rx.var(deps=["scheduler_cron_month", "ui_language"], auto_deps=False)
    def sched_month_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._MONTH_VAL_TO_LABEL[lang].get(
            self.scheduler_cron_month, self.scheduler_cron_month
        )

    def set_scheduler_month_from_label(self, label: str) -> None:
        self.scheduler_cron_month = self._MONTH_LABEL_TO_VAL.get(label, "*")

    # ── Interval unit i18n ─────────────────────────────────────

    _UNIT_MAP: dict[str, str] = {
        "Minuten": "minutes", "Minutes": "minutes",
        "Stunden": "hours", "Hours": "hours",
        "Tage": "days", "Days": "days",
    }
    _UNIT_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"minutes": "Minuten", "hours": "Stunden", "days": "Tage"},
        "en": {"minutes": "Minutes", "hours": "Hours", "days": "Days"},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_interval_unit_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._UNIT_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_interval_unit", "ui_language"], auto_deps=False)
    def sched_interval_unit_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._UNIT_DISPLAY[lang].get(self.scheduler_interval_unit, self.scheduler_interval_unit)

    def set_scheduler_interval_unit_from_label(self, label: str) -> None:
        self.scheduler_interval_unit = self._UNIT_MAP.get(label, "minutes")

    # ── Compose / decompose schedule expression ────────────────

    def _compose_schedule_expr(self) -> str:
        """Build schedule_expr from the structured fields."""
        if self.scheduler_edit_type == "cron":
            return (
                f"{self.scheduler_cron_min} {self.scheduler_cron_hour} "
                f"{self.scheduler_cron_dom} {self.scheduler_cron_month} "
                f"{self.scheduler_cron_dow}"
            )
        if self.scheduler_edit_type == "interval":
            multiplier = {"minutes": 60, "hours": 3600, "days": 86400}
            try:
                val = int(self.scheduler_interval_value)
            except ValueError:
                val = 60
            return str(val * multiplier.get(self.scheduler_interval_unit, 60))
        if self.scheduler_edit_type == "once":
            date = self.scheduler_once_date or "2026-01-01"
            time = self.scheduler_once_time or "00:00"
            return f"{date}T{time}:00"
        return self.scheduler_edit_expr

    def _decompose_schedule_expr(self) -> None:
        """Parse schedule_expr into structured fields."""
        expr = self.scheduler_edit_expr.strip()
        if self.scheduler_edit_type == "cron":
            parts = expr.split()
            if len(parts) >= 5:
                self.scheduler_cron_min = parts[0]
                self.scheduler_cron_hour = parts[1]
                self.scheduler_cron_dom = parts[2]
                self.scheduler_cron_month = parts[3]
                self.scheduler_cron_dow = parts[4]
        elif self.scheduler_edit_type == "interval":
            try:
                seconds = int(expr)
                if seconds >= 86400 and seconds % 86400 == 0:
                    self.scheduler_interval_value = str(seconds // 86400)
                    self.scheduler_interval_unit = "days"
                elif seconds >= 3600 and seconds % 3600 == 0:
                    self.scheduler_interval_value = str(seconds // 3600)
                    self.scheduler_interval_unit = "hours"
                else:
                    self.scheduler_interval_value = str(max(1, seconds // 60))
                    self.scheduler_interval_unit = "minutes"
            except ValueError:
                self.scheduler_interval_value = "60"
                self.scheduler_interval_unit = "minutes"
        elif self.scheduler_edit_type == "once":
            if "T" in expr:
                date_part, time_part = expr.split("T", 1)
                self.scheduler_once_date = date_part
                self.scheduler_once_time = time_part[:5]

    @rx.var(auto_deps=False)
    def scheduler_agent_options(self) -> list[str]:
        """Agent display labels for scheduler dropdown."""
        from ..lib.agent_config import load_agents_raw
        agents = load_agents_raw()
        return [
            f"{data['emoji']} {data['display_name']}"
            for aid, data in agents.items() if aid != "vision"
        ]

    @rx.var(deps=["scheduler_edit_agent"], auto_deps=False)
    def scheduler_edit_agent_display(self) -> str:
        """Display label for currently selected agent in scheduler edit."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(self.scheduler_edit_agent)
        return f"{cfg.emoji} {cfg.display_name}" if cfg else self.scheduler_edit_agent

    def set_scheduler_edit_agent_from_label(self, label: str) -> None:
        """Resolve agent display label back to ID."""
        from ..lib.agent_config import load_agents_raw
        for aid, data in load_agents_raw().items():
            if f"{data['emoji']} {data['display_name']}" == label:
                self.scheduler_edit_agent = aid
                return

    def cancel_scheduler_edit(self) -> None:
        """Close the edit form."""
        self.scheduler_edit_id = ""

    def save_scheduler_job(self) -> None:
        """Save (create or update) a scheduler job."""
        from ..lib.scheduler import get_job_store

        store = get_job_store()
        expr = self._compose_schedule_expr()
        payload = {
            "message": self.scheduler_edit_message,
            "agent": self.scheduler_edit_agent,
            "delivery": self.scheduler_edit_delivery,
        }
        if self.scheduler_edit_delivery == "announce":
            if self.scheduler_edit_channel:
                payload["channel"] = self.scheduler_edit_channel
            if self.scheduler_edit_recipient:
                payload["recipient"] = self.scheduler_edit_recipient
        elif self.scheduler_edit_delivery == "webhook":
            if self.scheduler_edit_webhook_url:
                payload["webhook_url"] = self.scheduler_edit_webhook_url

        if self.scheduler_edit_id == "new":
            store.add(
                name=self.scheduler_edit_name,
                schedule_type=self.scheduler_edit_type,
                schedule_expr=expr,
                payload=payload,
                max_tier=int(self.scheduler_edit_tier),
            )
        else:
            job_id = int(self.scheduler_edit_id)
            old_job = store.get(job_id)
            enabled = old_job.enabled if old_job else True
            store.delete(job_id)
            new_job = store.add(
                name=self.scheduler_edit_name,
                schedule_type=self.scheduler_edit_type,
                schedule_expr=expr,
                payload=payload,
                max_tier=int(self.scheduler_edit_tier),
            )
            if not enabled:
                store.enable(new_job.job_id, False)

        self.scheduler_edit_id = ""
        self._load_scheduler_jobs()

    def select_editor_agent(self, label: str):
        """Select an agent from the dropdown by its display label."""
        if label == self._AUTOMATIK_SEPARATOR:
            return  # Ignore separator click
        agent_id = self._agent_id_by_label.get(label, "")
        if agent_id:
            self._load_agent_into_state(agent_id)
            return self._push_editor_dom()

    def _load_agent_into_state(self, agent_id: str) -> None:
        """Load an agent's config into editor state vars (no DOM touch)."""
        self.editor_delete_confirm = ""
        self.editor_emoji_picker_open = False
        self.editor_dirty = False
        self.editor_reset_confirm = False
        self.editor_prompt_lang = self.ui_language  # type: ignore[attr-defined]

        # Automatik-LLM: no AgentConfig — load prompts directly from directory
        if agent_id == "automatik":
            self._load_automatik_into_state()
            return

        from ..lib.agent_config import get_agent_config
        config = get_agent_config(agent_id)
        if config is None:
            return

        self.editor_agent_id = agent_id
        self.editor_display_name = config.display_name
        self.editor_emoji = config.emoji
        self._editor_description = config.description
        self.editor_role = config.role
        self.editor_model = getattr(config, "model", "") or ""
        self.editor_system_reasoning = bool(config.toggles.get("reasoning", False))
        self.editor_prompt_keys = list(config.prompts.keys())
        # Pick a sensible initial prompt tab — "identity" if available,
        # else the first defined prompt (system-role agents like
        # calibration usually only have "system").
        self.editor_prompt_tab = (
            "identity" if "identity" in config.prompts
            else (self.editor_prompt_keys[0] if self.editor_prompt_keys else "identity")
        )

        # Load tool whitelist — None means all tools allowed
        from ..lib.plugin_registry import discover_tools
        from ..lib.plugin_base import PluginContext
        # Collect all available tool names
        all_tool_names: list[str] = []
        ctx = PluginContext(agent_id=agent_id, lang="de", session_id="", llm_history=[])
        for p in discover_tools():
            if p.is_available():
                for t in p.get_tools(ctx):
                    all_tool_names.append(t.name)
        # Memory tool
        all_tool_names.append("store_memory")
        # Channel tools
        from ..lib.plugin_registry import all_channels
        for ch in all_channels().values():
            if ch.is_configured():
                for t in ch.get_tools(ctx):
                    all_tool_names.append(t.name)

        if config.tools is None:
            # None = all allowed
            self.editor_tools = {name: True for name in all_tool_names}
        else:
            allowed = set(config.tools)
            self.editor_tools = {name: name in allowed for name in all_tool_names}

        # Load TTS settings for this agent — always start with XTTS
        self.editor_tts_engine = "xtts"  # type: ignore[attr-defined]
        self._load_editor_tts_settings()  # type: ignore[attr-defined]

        self._load_editor_prompt(self.editor_prompt_tab)

    def _load_automatik_into_state(self) -> None:
        """Load Automatik-LLM pseudo-agent into editor (prompts only)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        self.editor_agent_id = "automatik"
        self.editor_display_name = "Automatik-LLM"
        self.editor_emoji = "⚡"
        self._editor_description = "Intent Detection, Routing, Research Decisions"
        self.editor_role = "system"
        self.editor_tools = {}

        # Discover prompt files from both language directories
        prompt_keys: list[str] = []
        seen: set[str] = set()
        for lang in ("de", "en"):
            prompt_dir = PROMPTS_DIR / lang / "automatik"
            if prompt_dir.is_dir():
                for f in sorted(prompt_dir.glob("*.txt")):
                    key = f.stem  # e.g. "intent_detection"
                    if key not in seen:
                        prompt_keys.append(key)
                        seen.add(key)

        self.editor_prompt_keys = prompt_keys
        first_key = prompt_keys[0] if prompt_keys else ""
        self.editor_prompt_tab = first_key
        if first_key:
            self._load_editor_prompt(first_key)

    def _push_editor_dom(self):
        """Push current editor state values into DOM fields via JS and store initial state."""
        import json as _json
        name_js = _json.dumps(self.editor_display_name)
        desc_js = _json.dumps(self._editor_description)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            "setTimeout(() => {"
            f" const n = document.getElementById('editor-name'); if (n) n.value = {name_js};"
            f" const d = document.getElementById('editor-description'); if (d) d.value = {desc_js};"
            f" const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js};"
            "}, 50)",
        )

    def _load_editor_prompt(self, prompt_key: str) -> None:
        """Load a prompt file's content into state (for JS population)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        if self.editor_agent_id == "automatik":
            full_path = PROMPTS_DIR / self.editor_prompt_lang / "automatik" / f"{prompt_key}.txt"
        else:
            from ..lib.agent_config import get_agent_config
            config = get_agent_config(self.editor_agent_id)
            if config is None:
                return
            prompt_path = config.prompts.get(prompt_key, "")
            if not prompt_path:
                self._editor_prompt_content = ""
                return
            full_path = PROMPTS_DIR / self.editor_prompt_lang / prompt_path

        if full_path.exists():
            self._editor_prompt_content = full_path.read_text(encoding="utf-8")
        else:
            # Fallback: try the other language (EN-only prompts like intent_detection)
            fallback_lang = "en" if self.editor_prompt_lang == "de" else "de"
            if self.editor_agent_id == "automatik":
                fallback_path = PROMPTS_DIR / fallback_lang / "automatik" / f"{prompt_key}.txt"
            else:
                fallback_path = PROMPTS_DIR / fallback_lang / prompt_path if prompt_path else None  # type: ignore[assignment]
            if fallback_path and fallback_path.exists():
                content = fallback_path.read_text(encoding="utf-8")
                hint = f"[{fallback_lang.upper()} only]\n\n"
                self._editor_prompt_content = hint + content
            else:
                self._editor_prompt_content = ""

    def set_editor_prompt_tab(self, tab: str) -> None:
        """Switch prompt layer tab — load from disk and push to DOM."""
        import json as _json
        self.editor_prompt_tab = tab
        self._load_editor_prompt(tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def set_editor_prompt_lang(self, lang: str) -> None:
        """Switch prompt language — load from disk and push to DOM."""
        import json as _json
        self.editor_prompt_lang = lang
        self._load_editor_prompt(self.editor_prompt_tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def set_editor_emoji(self, value: str) -> None:
        """Update editor emoji field from picker."""
        self.editor_emoji = value
        self.editor_emoji_picker_open = False

    def set_editor_role(self, value: str) -> None:
        """Update editor role field."""
        self.editor_role = value

    def toggle_editor_tool(self, tool_name: str) -> None:
        """Toggle a single tool in the editor whitelist."""
        tools = dict(self.editor_tools)
        tools[tool_name] = not tools.get(tool_name, True)
        self.editor_tools = tools

    def set_all_editor_tools(self, enabled: bool) -> None:
        """Enable or disable all tools at once."""
        self.editor_tools = {name: enabled for name in self.editor_tools}

    def toggle_emoji_picker(self) -> None:
        """Toggle the emoji picker visibility."""
        self.editor_emoji_picker_open = not self.editor_emoji_picker_open

    def _save_editor_prompt_to_disk(self) -> None:
        """Save current prompt content to disk (editor_prompt_lang)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        if not self.editor_agent_id:
            return

        if self.editor_agent_id == "automatik":
            full_path = PROMPTS_DIR / self.editor_prompt_lang / "automatik" / f"{self.editor_prompt_tab}.txt"
        else:
            from ..lib.agent_config import get_agent_config
            config = get_agent_config(self.editor_agent_id)
            if not config:
                return
            prompt_path = config.prompts.get(self.editor_prompt_tab, "")
            if not prompt_path:
                return
            full_path = PROMPTS_DIR / self.editor_prompt_lang / prompt_path

        # Strip fallback language hint if present (e.g. "[EN only]\n\n")
        content = self._editor_prompt_content
        for prefix in ("[EN only]\n\n", "[DE only]\n\n"):
            if content.startswith(prefix):
                content = content[len(prefix):]
                break

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def save_agent_editor(self, dom_values: str = "{}") -> None:
        """Save agent editor — receives DOM values JSON from UI call_script callback."""
        import json
        from ..lib.agent_config import update_agent, create_agent
        from ..lib.prompt_loader import register_agent_toggles

        self.editor_dirty = False

        try:
            vals = json.loads(dom_values)
        except (json.JSONDecodeError, TypeError):
            vals = {}

        # Sync DOM values into state
        if vals.get("name"):
            self.editor_display_name = vals["name"]
        if vals.get("description") is not None:
            self._editor_description = vals["description"]
        # Always sync prompt (even if empty — user may have cleared it)
        self._editor_prompt_content = vals.get("prompt", self._editor_prompt_content)

        # Build tools whitelist from editor state
        # If all tools are enabled → save as None (= all allowed, no whitelist)
        all_enabled = all(self.editor_tools.values())
        tools_value = None if all_enabled else [
            name for name, enabled in self.editor_tools.items() if enabled
        ]

        if self.editor_agent_id == "automatik":
            # Automatik-LLM: only save prompt files, no agents.json entry
            self._save_editor_prompt_to_disk()
            self.editor_dirty_confirm = False
            self.add_debug("\u2705 Automatik-LLM prompt saved")  # type: ignore[attr-defined]
            return rx.toast.success("Automatik-LLM gespeichert", duration=2000)

        if self.editor_agent_id:
            # Update existing agent metadata
            update_payload: dict = {
                "display_name": self.editor_display_name,
                "emoji": self.editor_emoji,
                "description": self._editor_description,
                "role": self.editor_role,
                "tools": tools_value,
            }
            # System-role agents persist a Cloud model id alongside their config
            # plus their own reasoning toggle (off by default — see toggles spec).
            if self.editor_role == "system" and self.editor_agent_id != "automatik":
                update_payload["model"] = self.editor_model
                update_payload["toggles"] = {
                    "personality": False,
                    "reasoning": self.editor_system_reasoning,
                    "thinking": False,
                }
            update_agent(self.editor_agent_id, update_payload)

            # Save current prompt tab content to file
            self._save_editor_prompt_to_disk()

            self.add_debug(  # type: ignore[attr-defined]
                f"\u2705 Agent '{self.editor_display_name}' saved"
            )
        else:
            # Create new agent
            agent_id = vals.get("agent_id", "").strip().lower().replace(" ", "_")
            if not agent_id:
                self.add_debug("\u26a0\ufe0f Agent-ID is required")  # type: ignore[attr-defined]
                return

            new_config = create_agent(
                agent_id=agent_id,
                display_name=self.editor_display_name,
                emoji=self.editor_emoji,
                description=self._editor_description,
                role=self.editor_role,
            )
            register_agent_toggles(agent_id, new_config.toggles)
            self.ensure_all_agents_have_tts()  # type: ignore[attr-defined]
            self.add_debug(  # type: ignore[attr-defined]
                f"\u2705 Agent '{self.editor_display_name}' created"
            )
            # Select the newly created agent in the dropdown
            self._refresh_agent_dropdown()
            self._load_agent_into_state(agent_id)
            return self._push_editor_dom()

        # Existing agent saved — refresh dropdown, show toast, stay open
        self._refresh_agent_dropdown()
        self.editor_dirty_confirm = False
        return rx.toast.success(f"{self.editor_display_name} gespeichert", duration=2000)

    def delete_agent_editor(self, agent_id: str) -> None:
        """Delete an agent (with confirmation)."""
        if self.editor_delete_confirm != agent_id:
            # First click: ask for confirmation
            self.editor_delete_confirm = agent_id
            return

        # Second click: actually delete
        from ..lib.agent_config import delete_agent
        from ..lib.prompt_loader import unregister_agent_toggles

        try:
            delete_agent(agent_id)
            unregister_agent_toggles(agent_id)
            self.ensure_all_agents_have_tts()  # type: ignore[attr-defined]
            self.add_debug(f"\U0001f5d1\ufe0f Agent '{agent_id}' deleted")  # type: ignore[attr-defined]
        except ValueError as e:
            self.add_debug(f"\u26a0\ufe0f {e}")  # type: ignore[attr-defined]

        self.editor_delete_confirm = ""
        self._refresh_agent_dropdown()

        # Select first remaining agent
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        if raw:
            self._select_agent_for_editor(next(iter(raw)))

    def clear_agent_memory(self, agent_id: str) -> None:
        """Clear an agent's long-term memory (confirm on first click, delete on second)."""
        import reflex as rx
        from ..lib.agent_memory import get_agent_memory

        if self.editor_memory_confirm != agent_id:
            self.editor_memory_confirm = agent_id
            return

        self.editor_memory_confirm = ""

        memory = get_agent_memory()
        if not memory:
            return rx.toast.error("AgentMemory unavailable", duration=3000, position="top-center")

        # Get display name for logs/toasts
        from ..lib.multi_agent import get_agent_config
        agent_cfg = get_agent_config(agent_id)
        agent_name = agent_cfg.display_name if agent_cfg else agent_id.capitalize()

        try:
            col = memory._collection(agent_id)
            count = col.count()
            if count == 0:
                return rx.toast.info(f"{agent_name}: memory already empty", duration=3000, position="top-center")
            all_ids = col.get(include=[])["ids"]
            col.delete(ids=all_ids)
            self.add_debug(f"🗑️ {agent_name}: {count} memories cleared")  # type: ignore[attr-defined]
            return rx.toast.success(f"{agent_name}: {count} memories cleared", duration=3000, position="top-center")
        except Exception as e:
            return rx.toast.error(f"Error: {e}", duration=3000, position="top-center")

    # Reset confirm state
    editor_reset_confirm: bool = False

    def request_reset_editor_prompt(self) -> None:
        """First click on reset — show confirmation."""
        self.editor_reset_confirm = True

    def confirm_reset_editor_prompt(self):
        """Second click — actually reset prompt to file on disk."""
        self.editor_reset_confirm = False
        self.editor_dirty = False
        import json as _json
        self._load_editor_prompt(self.editor_prompt_tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def reset_editor_prompt(self) -> None:
        """Legacy — direct reset (kept for compatibility)."""
        self.editor_reset_confirm = False
        return self.confirm_reset_editor_prompt()

    def start_new_agent(self) -> None:
        """Switch editor to 'create new agent' mode (empty form)."""
        self.editor_agent_id = ""
        self.editor_display_name = ""
        self.editor_emoji = "\U0001f916"
        self._editor_description = ""
        self.editor_role = "custom"
        self._editor_new_agent_id = ""
        self.editor_prompt_tab = "identity"
        self._editor_prompt_content = ""
        self.editor_prompt_keys = []
        self.editor_delete_confirm = ""
        # Clear DOM fields
        return rx.call_script(  # type: ignore[return-value]
            "setTimeout(() => {"
            " ['editor-name','editor-description','editor-agent-id','editor-prompt-textarea']"
            "  .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });"
            "}, 50)",
        )

    # ================================================================
    # MEMORY BROWSER
    # ================================================================

    def open_memory_browser(self) -> None:
        """Switch to memory browser view, pre-select AIfred."""
        self.agent_editor_mode = "memory"
        self.memory_browser_filter = "all"
        self._load_memory_collections()

        # Pre-select AIfred's memory (or first available agent)
        agent_collections = self.memory_browser_collections
        if agent_collections:
            # Prefer AIfred
            aifred_col = next((c for c in agent_collections if c["agent_id"] == "aifred"), None)
            pick = aifred_col or agent_collections[0]
            self.browse_memory_agent(pick["agent_id"])

    def select_memory_agent(self, label: str) -> None:
        """Select an agent in the memory browser dropdown."""
        # Find agent_id from display label (label may include count suffix)
        for col in self.memory_browser_collections:
            if col["display_name"] == label or label.startswith(col["display_name"]):
                self.browse_memory_agent(col["agent_id"])
                return

    def set_memory_filter(self, filter_value: str) -> None:
        """Set the memory type filter (all/session/agent)."""
        self.memory_browser_filter = filter_value

    @rx.var(deps=["memory_browser_collections"], auto_deps=False)
    def memory_dropdown_options(self) -> List[str]:
        """All dropdown labels for memory browser (including Research Cache)."""
        return [col["display_name"] for col in self.memory_browser_collections]

    @rx.var(deps=["memory_browser_collections"], auto_deps=False)
    def memory_agent_dropdown_options(self) -> List[str]:
        """Agent dropdown labels with memory count."""
        return [
            f"{col['display_name']} ({col['count']})"
            for col in self.memory_browser_collections
        ]

    @rx.var(deps=["memory_browser_entries", "memory_browser_filter"], auto_deps=False)
    def filtered_memory_entries(self) -> List[Dict[str, str]]:
        """Memory entries filtered by type (all/session/agent)."""
        if self.memory_browser_filter == "all":
            return self.memory_browser_entries
        elif self.memory_browser_filter == "session":
            return [e for e in self.memory_browser_entries if e.get("type") == "session_summary"]
        else:  # "agent" — everything the agent stored itself
            return [e for e in self.memory_browser_entries if e.get("type") != "session_summary"]

    def select_db_collection(self, collection_name: str) -> None:
        """Select a system collection to browse in the database tab."""
        self.db_browser_collection = collection_name
        self.db_clear_confirm = False
        self._load_db_entries()

    def _load_db_entries(self) -> None:
        """Load entries for the selected system collection."""
        if not self.db_browser_collection:
            self.db_browser_entries = []
            return

        try:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.HttpClient(
                host="localhost", port=8000,
                settings=Settings(anonymized_telemetry=False),
            )
            col = client.get_collection(self.db_browser_collection)
            if col.count() == 0:
                self.db_browser_entries = []
                return

            data = col.get(include=["metadatas", "documents"])
        except Exception as e:
            self.add_debug(f"❌ DB browse error: {e}")  # type: ignore[attr-defined]
            self.db_browser_entries = []
            return

        entries: list[dict] = []
        for i, doc_id in enumerate(data["ids"]):
            meta = data["metadatas"][i] if data["metadatas"] else {}  # type: ignore[index]
            doc = data["documents"][i] if data["documents"] else ""  # type: ignore[index]

            if self.db_browser_collection == "research_cache":
                query_text = doc or ""
                answer = meta.get("answer", "") if meta else ""
                volatility = meta.get("volatility", "") if meta else ""
                date = meta.get("timestamp", "")[:19] if meta else ""
                entries.append({
                    "id": doc_id,
                    "date": date,
                    "type": volatility or "cache",
                    "summary": f"Query: {query_text}",
                    "content": answer[:500] if answer else "",
                })
            elif self.db_browser_collection == "aifred_documents":
                filename = meta.get("filename", "") if meta else ""
                chunk_idx = meta.get("chunk_index", 0) if meta else 0
                total = meta.get("total_chunks", 0) if meta else 0
                date = meta.get("upload_date", "")[:19] if meta else ""
                entries.append({
                    "id": doc_id,
                    "date": date,
                    "type": "document",
                    "summary": f"{filename} (chunk {chunk_idx + 1}/{total})",
                    "content": (doc or "")[:300],
                })

        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        self.db_browser_entries = entries

    def delete_db_entry(self, entry_id: str) -> None:
        """Delete a single entry from the current system collection."""
        if not self.db_browser_collection:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.HttpClient(
                host="localhost", port=8000,
                settings=Settings(anonymized_telemetry=False),
            )
            col = client.get_collection(self.db_browser_collection)
            col.delete(ids=[entry_id])
            self.add_debug(f"🗑️ DB entry deleted: {entry_id[:20]}...")  # type: ignore[attr-defined]
        except Exception as e:
            self.add_debug(f"❌ Delete failed: {e}")  # type: ignore[attr-defined]
        self._load_db_entries()

    def confirm_clear_db(self) -> None:
        """Toggle confirmation state for clearing a collection."""
        self.db_clear_confirm = not self.db_clear_confirm

    def clear_db_collection(self) -> None:
        """Clear all entries from the currently selected system collection."""
        self.db_clear_confirm = False
        if not self.db_browser_collection:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.HttpClient(
                host="localhost", port=8000,
                settings=Settings(anonymized_telemetry=False),
            )
            col = client.get_collection(self.db_browser_collection)
            count = col.count()
            if count > 0:
                all_ids = col.get(include=[])["ids"]
                col.delete(ids=all_ids)
            self.add_debug(f"🗑️ Cleared {self.db_browser_collection}: {count} entries")  # type: ignore[attr-defined]
        except Exception as e:
            self.add_debug(f"❌ Clear failed: {e}")  # type: ignore[attr-defined]
        self._load_db_entries()

    def db_toggle_orphans(self) -> None:
        """Toggle the orphan-cleanup section (only meaningful for aifred_documents)."""
        self.db_orphans_visible = not self.db_orphans_visible
        if self.db_orphans_visible:
            self._reload_db_orphans()

    def _reload_db_orphans(self) -> None:
        from ..lib import file_manager as fm
        result = fm.list_orphaned()
        self.db_orphans = result.metadata.get("orphans", []) if result.success else []

    async def db_delete_orphan(self, filename: str) -> None:
        """Delete a single orphaned document from the index only."""
        from ..lib import file_manager as fm
        parts = filename.strip("/").rsplit("/", 1)
        parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
        await fm.delete_file(parent_rel, leaf, from_disk=False, from_index=True)
        self._reload_db_orphans()
        self._load_db_entries()

    async def db_delete_all_orphans(self) -> None:
        """Bulk-delete every orphaned document from the index."""
        from ..lib import file_manager as fm
        for orphan in list(self.db_orphans):
            filename = str(orphan.get("filename", ""))
            if not filename:
                continue
            parts = filename.strip("/").rsplit("/", 1)
            parent_rel, leaf = ("", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            await fm.delete_file(parent_rel, leaf, from_disk=False, from_index=True)
        self._reload_db_orphans()
        self._load_db_entries()

    def _load_memory_collections(self) -> None:
        """Load overview of all ChromaDB agent memory collections."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_collections = []
            return

        from ..lib.agent_config import get_agent_config

        collections = []
        try:
            for col in memory._client.list_collections():
                if col.name.startswith("agent_memory_"):
                    agent_id = col.name.removeprefix("agent_memory_")
                    cfg = get_agent_config(agent_id)
                    display_name = f"{cfg.emoji} {cfg.display_name}" if cfg else agent_id.capitalize()
                    collections.append({
                        "name": col.name,
                        "agent_id": agent_id,
                        "display_name": display_name,
                        "count": str(col.count()),
                    })
        except Exception as e:
            self.add_debug(f"❌ Memory browser error: {e}")  # type: ignore[attr-defined]

        # Agents sorted alphabetically (Research Cache moved to Database tab)
        self.memory_browser_collections = sorted(
            collections,
            key=lambda c: c["agent_id"],
        )

    def browse_memory_agent(self, agent_id: str) -> None:
        """Load all entries for a specific agent's memory collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory:
            self.memory_browser_entries = []
            return

        self.memory_browser_agent = agent_id
        # Resolve display name — must match dropdown format (with count)
        count = "0"
        for col_info in self.memory_browser_collections:
            if col_info["agent_id"] == agent_id:
                count = col_info["count"]
                break
        if agent_id == "research_cache":
            self.memory_browser_agent_display = f"🔍 Research Cache ({count})"
        else:
            from ..lib.agent_config import get_agent_config
            cfg = get_agent_config(agent_id)
            name = f"{cfg.emoji} {cfg.display_name}" if cfg else agent_id.capitalize()
            self.memory_browser_agent_display = f"{name} ({count})"
        entries: list[dict] = []

        try:
            if agent_id == "research_cache":
                col = memory._client.get_collection(
                    name="research_cache",
                    embedding_function=memory._embed_fn,  # type: ignore[arg-type]
                )
            else:
                col = memory._collection(agent_id)

            if col.count() == 0:
                self.memory_browser_entries = []
                return

            data = col.get(include=["metadatas", "documents"])
            for i, doc_id in enumerate(data["ids"]):
                meta = data["metadatas"][i] if data["metadatas"] else {}  # type: ignore[index]
                doc = data["documents"][i] if data["documents"] else ""  # type: ignore[index]

                # Research cache stores query as document, answer in metadata
                if agent_id == "research_cache":
                    query_text = doc or ""
                    answer_text = meta.get("answer", "") if meta else ""
                    sources = meta.get("source_urls", "") if meta else ""
                    volatility = meta.get("volatility", "") if meta else ""
                    expires = meta.get("expires_at", "") if meta else ""
                    date = meta.get("timestamp", "")[:19] if meta else ""
                    summary_text = f"Query: {query_text}"
                    content_parts = []
                    if answer_text:
                        content_parts.append(answer_text)
                    # Sources as newline-separated string for UI rendering
                    if sources:
                        source_list = [s.strip() for s in sources.split(",") if s.strip()]
                        sources_text = "\n".join(source_list)
                    else:
                        sources_text = ""
                    if volatility:
                        content_parts.append(f"\nVolatilität: {volatility}")
                    if expires and expires != "None":
                        content_parts.append(f"\nAblauf: {expires[:19]}")
                    content_text = "".join(content_parts)
                    entries.append({
                        "id": doc_id,
                        "date": date,
                        "type": volatility or "cache",
                        "summary": summary_text,
                        "content": content_text,
                        "sources": sources_text,
                        "session_id": "",
                    })
                else:
                    entries.append({
                        "id": doc_id,
                        "date": meta.get("date", "")[:19] if meta else "",
                        "type": meta.get("type", "unknown"),
                        "summary": meta.get("summary", doc[:120] if doc else ""),
                        "content": meta.get("content", doc or ""),
                        "sources": "",
                        "session_id": meta.get("session_id", ""),
                    })
        except Exception as e:
            self.add_debug(f"❌ Memory browse error: {e}")  # type: ignore[attr-defined]

        entries.sort(key=lambda e: e.get("date", ""), reverse=True)
        self.memory_browser_entries = entries

    def delete_memory_entry(self, entry_id: str) -> None:
        """Delete a single memory entry from the current agent's collection."""
        from ..lib.agent_memory import get_agent_memory
        memory = get_agent_memory()
        if not memory or not self.memory_browser_agent:
            return

        try:
            if self.memory_browser_agent == "research_cache":
                col = memory._client.get_collection(
                    name="research_cache",
                    embedding_function=memory._embed_fn,  # type: ignore[arg-type]
                )
            else:
                col = memory._collection(self.memory_browser_agent)

            col.delete(ids=[entry_id])
            self.add_debug(f"🗑️ Memory entry deleted: {entry_id[:8]}...")  # type: ignore[attr-defined]
        except Exception as e:
            self.add_debug(f"❌ Delete failed: {e}")  # type: ignore[attr-defined]

        # Refresh the view
        self.browse_memory_agent(self.memory_browser_agent)
        self._load_memory_collections()

    # ─────────────────────────────────────────────────────────
    # Agent Bundle Export
    # ─────────────────────────────────────────────────────────

    def open_bundle_export(self) -> None:
        """Open the export modal — preselect the currently edited agent."""
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        self.bundle_all_agents = [
            {
                "agent_id": aid,
                "display_name": data.get("display_name", aid),
                "emoji": data.get("emoji", ""),
            }
            for aid, data in raw.items()
        ]
        preselect = [self.editor_agent_id] if self.editor_agent_id in raw else []
        self.bundle_export_selected = preselect
        self.bundle_export_open = True

    def close_bundle_export(self) -> None:
        self.bundle_export_open = False
        self.bundle_export_selected = []

    def toggle_bundle_export_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_export_selected:
            self.bundle_export_selected = [a for a in self.bundle_export_selected if a != agent_id]
        else:
            self.bundle_export_selected = [*self.bundle_export_selected, agent_id]

    def confirm_bundle_export(self):  # type: ignore[no-untyped-def]
        """Trigger a browser download via the /api/agents/export endpoint."""
        if not self.bundle_export_selected:
            return
        ids_param = ",".join(self.bundle_export_selected)
        url = f"/api/agents/export?ids={ids_param}"
        self.bundle_export_open = False
        self.bundle_export_selected = []
        yield rx.call_script(f"window.location.href = {url!r}")

    # ─────────────────────────────────────────────────────────
    # Agent Bundle Import
    # ─────────────────────────────────────────────────────────

    def open_bundle_import(self) -> None:
        """Open the import modal in its initial empty state."""
        self.bundle_import_open = True
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""

    async def handle_bundle_upload(self, files: list) -> None:  # type: ignore[no-untyped-def]
        """Reflex on_drop callback — read ZIP, peek manifest, open modal."""
        import base64
        from ..lib.agent_bundle import peek_bundle

        if not files:
            return

        try:
            zip_bytes = await files[0].read()
        except Exception as exc:
            self.bundle_import_error = f"Datei konnte nicht gelesen werden: {exc}"
            self.bundle_import_open = True
            return

        try:
            info = peek_bundle(zip_bytes)
        except Exception as exc:
            self.bundle_import_error = f"Kein gültiges Agent-Bundle: {exc}"
            self.bundle_import_open = True
            return

        self.bundle_import_uploaded_b64 = base64.b64encode(zip_bytes).decode("ascii")
        self.bundle_import_agents = info["agents"]
        self.bundle_import_selected = [a["agent_id"] for a in info["agents"]]
        self.bundle_import_conflict = "rename"
        self.bundle_import_error = ""
        self.bundle_import_open = True

    def close_bundle_import(self) -> None:
        self.bundle_import_open = False
        self.bundle_import_uploaded_b64 = ""
        self.bundle_import_agents = []
        self.bundle_import_selected = []
        self.bundle_import_error = ""

    def toggle_bundle_import_agent(self, agent_id: str) -> None:
        if agent_id in self.bundle_import_selected:
            self.bundle_import_selected = [a for a in self.bundle_import_selected if a != agent_id]
        else:
            self.bundle_import_selected = [*self.bundle_import_selected, agent_id]

    def set_bundle_import_conflict(self, value: str) -> None:
        if value in ("abort", "overwrite", "rename"):
            self.bundle_import_conflict = value

    def confirm_bundle_import(self) -> None:
        """Decode the staged bundle and write the selected agents."""
        import base64
        from ..lib.agent_bundle import import_bundle

        if not self.bundle_import_uploaded_b64 or not self.bundle_import_selected:
            return

        try:
            zip_bytes = base64.b64decode(self.bundle_import_uploaded_b64)
            effective_ids, warnings = import_bundle(
                zip_bytes,
                selected_ids=self.bundle_import_selected,
                conflict=self.bundle_import_conflict,  # type: ignore[arg-type]
            )
        except FileExistsError as exc:
            self.bundle_import_error = str(exc)
            return
        except Exception as exc:
            self.bundle_import_error = f"Import fehlgeschlagen: {exc}"
            return

        for w in warnings:
            self.add_debug(f"📦 {w}")  # type: ignore[attr-defined]
        self.add_debug(  # type: ignore[attr-defined]
            f"✅ Agenten importiert: {', '.join(effective_ids)}"
        )

        self.close_bundle_import()
        self._refresh_agent_dropdown()
