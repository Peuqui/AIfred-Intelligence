"""Agent configuration mixin for AIfred state.

Handles per-agent personality, reasoning, thinking mode,
sampling parameters, speed mode, RoPE factors, multi-agent mode settings,
temperature configuration, and model selection for Sokrates/Salomo.

Per-agent tuning lives in the ``agent_tuning`` dict (one AgentTuning bucket
per agent id) — access via the ``agent_settings`` helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import reflex as rx

from ..lib.agent_tuning import (
    AgentModelRow,
    AgentTuning,
    CtxRow,
    SamplingRow,
    default_agent_tuning,
)

if TYPE_CHECKING:
    from ..lib.agent_choices import Option
    from ..lib.vision_routing import Describer

# NOTE (mypy SCC): agent_tuning.py is dependency-free and safe to import at
# module level. aifred.lib.agent_settings must still be imported lazily in
# methods — a module-level import changes mypy's SCC analysis order and
# breaks type inference of the temperature state var.

# Per-agent emoji for personality toggles
_PERSONALITY_EMOJI: dict[str, str] = {
    "aifred": "\U0001f3a9",      # top hat
    "sokrates": "\U0001f3db️",  # classical building
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

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        _min_agent_context_limit: int
        automatik_model_id: rx.Field[str]
        available_models_dict: rx.Field[dict[str, str]]
        backend_id: rx.Field[str]
        backend_type: rx.Field[str]
        enable_tts: rx.Field[bool]
        ui_language: rx.Field[str]

        @property
        def _effective_automatik_id(self) -> str: ...
        def _label_for(self, agent: str, model_id: str) -> str: ...
        def _log_history_utilization(self, effective_limit: int) -> None: ...
        def _persist_session_config(self) -> None: ...
        def _save_settings(self) -> None: ...
        def _show_model_calibration_info(self, model_id: str): ...
        @rx.event
        def add_debug(self, message: str) -> None: ...

    # ── Per-Agent Tuning (SSOT: one bucket per agent id) ──────────
    agent_tuning: rx.Field[dict[str, AgentTuning]] = rx.field(default_factory=default_agent_tuning)

    sampling_reset_key: rx.Field[int] = rx.field(0)  # UI key counter to force re-mount on reset

    # ── Automatik (intent LLM — not an agent, own fields) ─────────
    automatik_rope_factor: rx.Field[float] = rx.field(1.0)

    # ── Active Agent (direct chat) ─────────────────────────────────
    # NOTE: active_agent, multi_agent_mode, symposion_agents are now
    # per-session (session_storage.DEFAULT_SESSION_CONFIG). Class defaults
    # only apply before any session is loaded.
    active_agent: rx.Field[str] = rx.field("aifred")  # Which agent responds (default: aifred)
    agent_memory_enabled: rx.Field[bool] = rx.field(True)  # Global toggle: agents use long-term memory

    # ── Multi-Agent Settings (per-session) ────────────────────────
    multi_agent_mode: rx.Field[str] = rx.field("standard")
    max_debate_rounds: rx.Field[int] = rx.field(3)  # still global (debate param)
    symposion_agents: rx.Field[list[str]] = rx.field(default_factory=list)  # Selected agents for Symposion mode
    consensus_type: rx.Field[str] = rx.field("majority")

    # ── Multi-Agent Runtime State ─────────────────────────────────
    sokrates_critique: rx.Field[str] = rx.field("")
    show_sokrates_panel: rx.Field[bool] = rx.field(False)
    salomo_synthesis: rx.Field[str] = rx.field("")
    show_salomo_panel: rx.Field[bool] = rx.field(False)
    debate_round: rx.Field[int] = rx.field(0)
    debate_user_interjection: rx.Field[str] = rx.field("")
    debate_in_progress: rx.Field[bool] = rx.field(False)

    # ================================================================
    # GENERIC HELPERS (deduplicated triple-agent pattern)
    # ================================================================

    def _toggle_agent_feature(self, agent: str, feature: str) -> None:
        """Toggle a boolean per-agent feature and persist it (prompt_loader reads settings.json).

        Works for personality and reasoning (thinking moved to the
        thinking-mode dropdown, see _set_agent_thinking_mode).
        """
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        new_val = not get_agent_setting(self, agent, feature)
        set_agent_setting(self, agent, feature, new_val)

        # Emoji for debug message
        if feature == "personality":
            emoji = _PERSONALITY_EMOJI.get(agent, "\U0001f916")
        else:
            emoji = _FEATURE_EMOJI[feature]

        status = "ON" if new_val else "OFF"
        self.add_debug(f"{emoji} {agent.capitalize()} {feature}: {status}")

        self._save_settings()

    # ── Thinking Mode (dropdown: off / on / effort level) ─────────

    def _thinking_choices(self, agent: str) -> tuple[str, list[Option]]:
        """The agent's thinking choice and the options of its model (rule: ``agent_choices.thinking_choices``)."""
        from ..lib.agent_choices import thinking_choices
        return thinking_choices(self, agent, self.ui_language if self.ui_language != "auto" else "de")

    def _set_agent_thinking_mode(self, agent: str, label: str) -> None:
        """Dropdown label → the agent's thinking choice on the model it runs on. A label the menu
        no longer offers (the model changed meanwhile) is stored as the level it names."""
        from ..lib.agent_choices import choose_thinking
        from ..lib.agent_settings import get_agent_base_model_id
        _, options = self._thinking_choices(agent)
        mode = next((option.value for option in options if option.label == label), label)
        choose_thinking(self, agent, mode)
        emoji = _FEATURE_EMOJI["thinking"]
        self.add_debug(f"{emoji} {agent.capitalize()} thinking on {get_agent_base_model_id(self, agent)}: {mode}")
        self._save_settings()

    def _agent_thinking_mode(self, agent: str) -> str:
        """Dropdown label of the agent's thinking choice on the model it runs on."""
        mode, options = self._thinking_choices(agent)
        return next((option.label for option in options if option.value == mode), mode)

    def _agent_thinking_options(self, agent: str) -> list[str]:
        """The labels of the thinking options of the agent's model."""
        return [option.label for option in self._thinking_choices(agent)[1]]

    # AIfred/Vision keep dedicated vars — their rows are special-cased in
    # the UI (main-model select, vision badges), not part of the foreach.
    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def aifred_thinking_mode(self) -> str:
        return self._agent_thinking_mode("aifred")

    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def vision_thinking_mode(self) -> str:
        return self._agent_thinking_mode("vision")

    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def aifred_thinking_options(self) -> list[str]:
        return self._agent_thinking_options("aifred")

    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def vision_thinking_options(self) -> list[str]:
        return self._agent_thinking_options("vision")

    # ================================================================
    # SAMPLING PARAMETERS
    # ================================================================

    def _set_agent_sampling(self, agent: str, param: str, value: str) -> None:
        """Set a sampling parameter for an agent and save to settings."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        try:
            if param == "top_k":
                int_val = int(float(value))
                set_agent_setting(self, agent, "top_k", max(0, min(200, int_val)))
            elif param == "top_p":
                float_val = float(value)
                set_agent_setting(self, agent, "top_p", max(0.0, min(1.0, float_val)))
            elif param == "min_p":
                float_val = float(value)
                set_agent_setting(self, agent, "min_p", max(0.0, min(1.0, float_val)))
            elif param == "repeat_penalty":
                float_val = float(value)
                set_agent_setting(self, agent, "repeat_penalty", max(1.0, min(2.0, float_val)))
            final_val = get_agent_setting(self, agent, param)
            self.add_debug(f"\U0001f3b2 {agent.capitalize()} {param}={final_val}")
            self._save_settings()
        except (ValueError, TypeError):
            pass

    def _reset_agent_sampling(self, agent: str, include_temperature: bool = True) -> None:
        """Reset sampling parameters for an agent to its model's defaults (rule:
        ``agent_choices.sampling_defaults``).

        Args:
            agent: any registered agent id
            include_temperature: If True, reset temperature too (model change / reset button).
                If False, keep current temperature (app restart -- temperature is persisted).
        """
        from ..lib.agent_choices import reset_sampling, sampling_defaults
        reset_sampling(self, agent, sampling_defaults(self, agent), include_temperature=include_temperature)
        self._note_sampling_reset(agent, include_temperature)

    def _note_sampling_reset(self, agent: str, include_temperature: bool) -> None:
        """The reset in the debug console, and a new key: the sampling inputs mount again with the new values."""
        from ..lib.agent_config import get_agent_label
        from ..lib.agent_settings import get_agent_base_model_id, get_agent_setting
        model = get_agent_base_model_id(self, agent)
        model_info = f" → {model} defaults" if model else ""
        temp_info = f"temp={get_agent_setting(self, agent, 'temperature')}, " if include_temperature else ""
        self.add_debug(
            f"{get_agent_label(agent)} sampling reset{model_info}: "
            f"{temp_info}top_k={get_agent_setting(self, agent, 'top_k')}, "
            f"top_p={get_agent_setting(self, agent, 'top_p')}, min_p={get_agent_setting(self, agent, 'min_p')}, "
            f"rep={get_agent_setting(self, agent, 'repeat_penalty')}"
        )
        self.sampling_reset_key += 1

    # ================================================================
    # SPEED MODE — SINGLE SOURCE OF TRUTH
    # ================================================================

    def _effective_model_id(self, agent: str) -> str:
        """Model ID with variant suffix for the current configuration (rules: lib/runtime_settings)."""
        from ..lib.runtime_settings import effective_model_id
        return effective_model_id(self, agent)

    def _image_describer(self) -> Describer:
        """Who looks at uploaded images (rule A, ``vision_routing.chat_describer``): the main
        (AIfred) model when it can see, else a describer. Raises ``NoVisionModelError`` when
        nothing can see."""
        from ..lib.vision_routing import chat_describer
        return chat_describer(self._effective_model_id("aifred"))

    # ================================================================
    # SPEED MODE TOGGLES (llamacpp only)
    # ================================================================

    def _toggle_speed_mode(self, agent: str) -> None:
        """Toggle speed/context mode for any agent."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        new_val = not get_agent_setting(self, agent, "speed_mode")
        set_agent_setting(self, agent, "speed_mode", new_val)
        self.add_debug(f"\U0001f500 {agent.capitalize()} mode: {self._speed_mode_debug_str(agent, new_val)}")
        self._save_settings()

    def _speed_mode_debug_str(self, agent: str, speed_on: bool) -> str:
        """Build the speed-toggle debug string from the profile that ACTUALLY
        resolves — not the speed split in isolation.

        When a higher-precedence variant (VLM / TTS) overrides Speed, the
        message says so and reports the real loaded context, instead of
        promising the speed context the user won't actually get. Mirrors the
        runtime resolver (``_effective_model_id``)."""
        from ..lib.agent_settings import get_agent_base_model_id
        from ..lib.formatting import format_number
        from ..lib.research.context_utils import get_model_native_context
        base_id = get_agent_base_model_id(self, agent)
        effective = self._effective_model_id(agent)
        ctx = get_model_native_context(effective, self.backend_type)
        ctx_str = format_number(ctx) if ctx > 0 else "n/a"
        suffix = (
            effective[len(base_id):].lstrip("-")
            if base_id and effective.startswith(base_id) and effective != base_id
            else ""
        )
        if not speed_on:
            return f"\U0001f4d6 context — {ctx_str} tok"
        if suffix.endswith("speed"):
            return f"⚡ speed — {ctx_str} tok"
        # Speed requested but a higher-precedence variant won resolution.
        return f"⚡ speed → overridden by {suffix or 'base'} — {ctx_str} tok"

    # ================================================================
    # ROPE FACTOR SETTERS
    # ================================================================

    @rx.event
    def set_aifred_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for AIfred-LLM."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        # Convert UI string to float
        factor = float(value.replace("x", ""))
        set_agent_setting(self, "aifred", "rope_factor", factor)
        self.add_debug(f"\U0001f39a️ AIfred RoPE Factor: {value}")

        # Save to VRAM cache (per-model setting)
        aifred_model_id: str = get_agent_setting(self, "aifred", "model_id")
        if aifred_model_id:
            from ..lib.model_vram_cache import (
                format_model_with_ctx as _format_model_with_ctx,
            )
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_ollama_calibrated_max_context,
                get_rope_factor_for_model,
                set_rope_factor_for_model,
            )
            set_rope_factor_for_model(aifred_model_id, factor)

            # SSOT in lib/model_vram_cache — thin wrapper binds backend_type
            def format_model_with_ctx(model_display: str, model_id: str) -> str:
                return _format_model_with_ctx(model_display, model_id, self.backend_type)

            # Re-display all agent models with updated context limits
            from ..lib.agent_config import get_agent_label
            self.add_debug(f"   {get_agent_label('aifred')}: {format_model_with_ctx(get_agent_setting(self, 'aifred', 'model'), aifred_model_id)}")
            if self.multi_agent_mode != "standard":
                for secondary in ("sokrates", "salomo"):
                    sec_id: str = get_agent_setting(self, secondary, "model_id")
                    if sec_id:
                        self.add_debug(f"   {get_agent_label(secondary)}: {format_model_with_ctx(get_agent_setting(self, secondary, 'model'), sec_id)}")

            # Update cached min context limit
            context_limits: list[int] = []
            for chat_agent in ("aifred", "sokrates", "salomo"):
                model_id = get_agent_setting(self, chat_agent, "model_id")
                if model_id:
                    if self.backend_type == "llamacpp":
                        ctx = get_llamacpp_calibration(model_id)
                    elif self.backend_type == "vllm":
                        from ..lib.operating_points import get_vllm_entry_context
                        ctx = get_vllm_entry_context(model_id)
                    else:
                        ctx = get_ollama_calibrated_max_context(model_id, get_rope_factor_for_model(model_id))
                    if ctx:
                        context_limits.append(ctx)
            self._min_agent_context_limit = min(context_limits) if context_limits else 0

            # Show history utilization and warn if compression will trigger
            self._log_history_utilization(self._min_agent_context_limit)

            # Warn if no calibration exists for this mode
            if factor >= 2.0:
                extended_ctx = get_ollama_calibrated_max_context(aifred_model_id, rope_factor=2.0)
                if extended_ctx is None:
                    self.add_debug("⚠️ No RoPE 2x calibration found - please calibrate first!")
            else:
                native_ctx = get_ollama_calibrated_max_context(aifred_model_id, rope_factor=1.0)
                if native_ctx is None:
                    self.add_debug("⚠️ No native calibration found - please calibrate first!")

    @rx.event
    def set_automatik_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Automatik-LLM."""
        factor = float(value.replace("x", ""))
        self.automatik_rope_factor = factor
        effective_auto = self._effective_automatik_id
        if effective_auto:
            from ..lib.model_vram_cache import set_rope_factor_for_model
            set_rope_factor_for_model(effective_auto, factor)

    def _set_agent_rope_factor(self, agent: str, value: str) -> None:
        """Set RoPE factor for a non-AIfred agent (own model only)."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        factor = float(value.replace("x", ""))
        set_agent_setting(self, agent, "rope_factor", factor)
        model_id: str = get_agent_setting(self, agent, "model_id")
        if model_id:
            from ..lib.model_vram_cache import set_rope_factor_for_model
            set_rope_factor_for_model(model_id, factor)

    # ================================================================
    # ROPE FACTOR DISPLAY (computed vars)
    # ================================================================

    def _rope_display(self, agent: str) -> str:
        from ..lib.agent_settings import get_agent_setting
        return f"{get_agent_setting(self, agent, 'rope_factor')}x"

    @rx.var(deps=["agent_tuning"], auto_deps=False)
    def rope_factor_display(self) -> str:
        """Display value for AIfred RoPE factor select (e.g., '1.0x', '2.0x')."""
        return self._rope_display("aifred")

    @rx.var
    def automatik_rope_display(self) -> str:
        """Display value for Automatik RoPE factor select."""
        return f"{self.automatik_rope_factor}x"

    @rx.var(deps=["agent_tuning"], auto_deps=False)
    def vision_rope_display(self) -> str:
        """Display value for Vision RoPE factor select."""
        return self._rope_display("vision")

    # ================================================================
    # TEMPERATURE SETTINGS
    # ================================================================

    def _set_temperature_input(self, agent: str, value: str) -> None:
        """Set temperature for any agent from text input field."""
        try:
            from ..lib.agent_settings import get_agent_setting, set_agent_setting
            set_agent_setting(self, agent, "temperature", max(0.0, min(2.0, float(value))))
            self.add_debug(f"\U0001f321️ {agent.capitalize()} temperature={get_agent_setting(self, agent, 'temperature')}")
            self._save_settings()
        except (ValueError, TypeError):
            pass

    # ================================================================
    # MULTI-AGENT MODE SETTINGS
    # ================================================================

    @rx.event
    def set_multi_agent_mode(self, mode: str) -> None:
        """Set multi-agent discussion mode."""
        self.multi_agent_mode = mode
        # Reset Sokrates panel when switching modes
        self.show_sokrates_panel = False
        self.sokrates_critique = ""
        self.debate_round = 0

        # Enforce agent selection rules per mode
        if mode == "symposion":
            # Symposion: ensure at least one agent is selected
            if not self.symposion_agents:
                self.symposion_agents = ["aifred"]
        elif mode in ("critical_review", "auto_consensus", "tribunal"):
            # These modes always use AIfred + Sokrates + Salomo
            self.active_agent = "aifred"

        self._persist_session_config()

        mode_labels = {
            "standard": "Standard",
            "critical_review": "Critical Review",
            "auto_consensus": "Auto-Consensus",
            "tribunal": "Tribunal",
            "symposion": "Symposion",
        }
        self.add_debug(f"\U0001f916 Discussion mode: {mode_labels.get(mode, mode)}")

    @rx.event
    def increase_debate_rounds(self) -> None:
        """Increase max debate rounds by 1 (up to DEBATE_ROUNDS_MAX)."""
        from ..lib.config import DEBATE_ROUNDS_MAX
        if self.max_debate_rounds < DEBATE_ROUNDS_MAX:
            self.max_debate_rounds += 1
            self._save_settings()
            self.add_debug(f"\U0001f504 Max debate rounds: {self.max_debate_rounds}")

    @rx.event
    def decrease_debate_rounds(self) -> None:
        """Decrease max debate rounds by 1 (down to DEBATE_ROUNDS_MIN)."""
        from ..lib.config import DEBATE_ROUNDS_MIN
        if self.max_debate_rounds > DEBATE_ROUNDS_MIN:
            self.max_debate_rounds -= 1
            self._save_settings()
            self.add_debug(f"\U0001f504 Max debate rounds: {self.max_debate_rounds}")

    @rx.event
    def toggle_consensus_type(self, checked: bool) -> None:
        """Toggle consensus type between majority (off) and unanimous (on)."""
        self.consensus_type = "unanimous" if checked else "majority"
        self._save_settings()
        type_label = "3/3 unanimous" if checked else "2/3 majority"
        self.add_debug(f"\U0001f5f3️ Consensus type: {type_label}")

    @rx.var
    def is_unanimous_consensus(self) -> bool:
        """Check if consensus type is unanimous (for toggle state)."""
        return self.consensus_type == "unanimous"

    @rx.var(deps=["consensus_type", "ui_language"], auto_deps=False)
    def consensus_toggle_tooltip(self) -> str:
        """Get tooltip text for consensus toggle based on current state and language."""
        from ..lib.i18n import t
        if self.consensus_type == "unanimous":
            return t("consensus_toggle_tooltip_on", lang=self.ui_language)
        return t("consensus_toggle_tooltip_off", lang=self.ui_language)

    @rx.var(deps=["ui_language"], auto_deps=False)
    def speed_switch_tooltip(self) -> str:
        """Localized tooltip for the Ctx/Speed switch."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"
        return t("speed_switch_tooltip", lang=lang)

    @rx.var(deps=["ui_language"], auto_deps=False)
    def multi_agent_mode_options(self) -> list[list[str]]:
        """Get localized multi-agent mode options as [key, label] pairs for dropdown."""
        from ..lib.i18n import TranslationManager
        return [
            ["standard", TranslationManager.get_text("multi_agent_standard", self.ui_language)],
            ["critical_review", TranslationManager.get_text("multi_agent_critical_review", self.ui_language)],
            ["auto_consensus", TranslationManager.get_text("multi_agent_auto_consensus", self.ui_language)],
            ["tribunal", TranslationManager.get_text("multi_agent_tribunal", self.ui_language)],
            ["symposion", TranslationManager.get_text("multi_agent_symposion", self.ui_language)],
        ]

    # Core agents used in fixed multi-agent modes
    CORE_AGENTS: ClassVar[set[str]] = {"aifred", "sokrates", "salomo"}
    # Modes where only core agents participate and selection is locked
    FIXED_MODES: ClassVar[set[str]] = {"critical_review", "auto_consensus", "tribunal"}

    @rx.var(deps=["_agent_dropdown_items"], auto_deps=False)
    def selectable_agents(self) -> list[dict[str, str]]:
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

    @rx.event
    def toggle_agent_memory(self) -> None:
        """Toggle agent memory on/off (incognito mode)."""
        self.agent_memory_enabled = not self.agent_memory_enabled
        if self.agent_memory_enabled:
            self.add_debug("🔓 Agent memory enabled")
        else:
            self.add_debug("🔒 Incognito mode (no memory)")

    @rx.event
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
        self.add_debug(f"🎯 Active agent: {label}")
        self._persist_session_config()

    @rx.event
    def toggle_symposion_agent(self, agent_id: str) -> None:
        """Toggle an agent's participation in Symposion mode."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        label = cfg.display_name if cfg else agent_id.capitalize()
        if agent_id in self.symposion_agents:
            # Don't allow deselecting the last agent
            if len(self.symposion_agents) <= 1:
                self.add_debug(f"🏛️ Symposion: {label} is the last agent, cannot be removed")
                return
            self.symposion_agents = [a for a in self.symposion_agents if a != agent_id]
            self.add_debug(f"🏛️ Symposion: {label} removed")
        else:
            self.symposion_agents = self.symposion_agents + [agent_id]
            self.add_debug(f"🏛️ Symposion: {label} added")
        self._persist_session_config()

    @rx.var(deps=["symposion_agents"], auto_deps=False)
    def symposion_agent_positions(self) -> dict[str, int]:
        """1-based speaking-order position per selected Symposion agent.

        The order is toggle history (append-on-enable, see
        toggle_symposion_agent), invisible otherwise — this exposes it so
        the UI can show a small "who speaks first" badge instead of the
        user only finding out from the debug console mid-run.
        """
        return {aid: i + 1 for i, aid in enumerate(self.symposion_agents)}

    # ================================================================
    # MULTI-AGENT RUNTIME STATE MANAGEMENT
    # ================================================================

    @rx.event
    def reset_sokrates_state(self) -> None:
        """Reset all Sokrates-related runtime state."""
        self.sokrates_critique = ""
        self.show_sokrates_panel = False
        self.debate_round = 0
        self.debate_user_interjection = ""
        self.debate_in_progress = False

    @rx.event
    def reset_salomo_state(self) -> None:
        """Reset all Salomo-related runtime state."""
        self.salomo_synthesis = ""
        self.show_salomo_panel = False

    # ================================================================
    # SOKRATES / SALOMO MODEL SELECTION
    # ================================================================

    @rx.var(deps=["available_models_dict", "ui_language"], auto_deps=False)
    def secondary_available_models_rich(self) -> list[dict[str, str]]:
        """Optionen der Sekundaer-Agenten: Sentinel zuerst, dann alle Modelle."""
        from ..lib.i18n import t
        from ..state._backend_mixin import SAME_AS_AIFRED
        lang = self.ui_language if self.ui_language != "auto" else "de"
        return [{"id": SAME_AS_AIFRED, "label": t("sokrates_llm_same", lang=lang),
                 "badge": "", "color": ""}] + [
            {"id": mid, "label": label, "badge": "", "color": ""}
            for mid, label in self.available_models_dict.items()
        ]

    def _model_select_id(self, agent: str) -> str:
        """Select-Wert: die Modell-ID, leer wird zum Sentinel.

        Die ID ist die einzige Wahrheit — Labels sind Ansicht und taugen
        nicht als Auswahlwert (siehe ``_catalog_for``/``_label_for``).
        """
        from ..lib.agent_settings import get_agent_setting
        from ..state._backend_mixin import SAME_AS_AIFRED
        model_id: str = get_agent_setting(self, agent, "model_id")
        return SAME_AS_AIFRED if not model_id else model_id

    # Debug-emoji for the secondary-model selects (historical, differs from
    # the agents.json emoji — 🧠 for Sokrates, 👑 for Salomo)
    _SECONDARY_MODEL_EMOJI: ClassVar[dict[str, str]] = {
        "sokrates": "\U0001f9e0",
        "salomo": "\U0001f451",
    }

    def _set_secondary_agent_model(self, agent: str, model_id: str) -> None:
        """Set the model of an agent other than AIfred (rule: ``agent_choices.choose_model``).

        Bekommt die MODELL-ID aus dem Select; das Label wird daraus abgeleitet.
        """
        from ..lib.agent_choices import choose_model
        from ..lib.agent_settings import get_agent_setting
        from ..state._backend_mixin import SAME_AS_AIFRED
        if model_id == SAME_AS_AIFRED:
            model_id = ""
        for reset in choose_model(self, agent, model_id, self._label_for(agent, model_id)):
            self._note_sampling_reset(reset, include_temperature=True)
        self._save_settings()
        emoji = self._SECONDARY_MODEL_EMOJI.get(agent, "\U0001f916")
        if model_id:
            self.add_debug(f"{emoji} {agent.capitalize()}-LLM: {get_agent_setting(self, agent, 'model')}")
            self._show_model_calibration_info(model_id)
        else:
            self.add_debug(f"{emoji} {agent.capitalize()}-LLM: (same as Main-LLM)")

    # ================================================================
    # GENERIC EVENT HANDLERS (rx.foreach rows pass the agent id)
    # ================================================================

    @rx.event
    def set_agent_model_for(self, agent: str, model: str) -> None:
        """Model select handler for any secondary agent (incl. custom)."""
        self._set_secondary_agent_model(agent, model)

    @rx.event
    def set_agent_sampling_for(self, agent: str, param: str, value: str) -> None:
        self._set_agent_sampling(agent, param, value)

    @rx.event
    def set_agent_temperature_for(self, agent: str, value: str) -> None:
        self._set_temperature_input(agent, value)

    def _reset_sampling_for_all_agents(self, *, inheriting_only: bool, include_temperature: bool) -> None:
        """Reset sampling of every agent affected by a backend or base-model switch.

        Custom agents (Codine, Pater, ...) included — they run on the same
        models and kept stale values otherwise (1.1 repeat penalty from the
        pre-model days, 2026-09-06). Vision keeps its own handling.

        Args:
            inheriting_only: only agents without an own model_id (base-model
                switch: agents with their own model are unaffected)
            include_temperature: see _reset_agent_sampling
        """
        from ..lib.agent_settings import get_agent_setting
        for entry in self._ui_agent_list(include_vision=False):
            agent = entry["id"]
            if inheriting_only and agent != "aifred" and get_agent_setting(self, agent, "model_id"):
                continue
            self._reset_agent_sampling(agent, include_temperature=include_temperature)

    @rx.event
    def reset_agent_sampling_for(self, agent: str) -> None:
        self._reset_agent_sampling(agent)
        self._save_settings()

    @rx.event
    def toggle_agent_personality_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_agent_feature(agent, "personality")

    @rx.event
    def toggle_agent_reasoning_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_agent_feature(agent, "reasoning")

    @rx.event
    def set_agent_thinking_mode_for(self, agent: str, mode: str) -> None:
        self._set_agent_thinking_mode(agent, mode)

    @rx.event
    def toggle_agent_speed_mode_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_speed_mode(agent)

    @rx.event
    def set_agent_rope_factor_for(self, agent: str, value: str) -> None:
        self._set_agent_rope_factor(agent, value)

    # ================================================================
    # UI ROW MODELS (rendered via rx.foreach)
    # ================================================================

    def _ui_agent_list(self, include_vision: bool = True) -> list[dict[str, str]]:
        """(id, display_name, emoji) of all non-system agents, vision last."""
        rows = [dict(entry) for entry in self.selectable_agents]
        if include_vision:
            from ..lib.agent_config import get_agent_config
            cfg = get_agent_config("vision")
            rows.append({
                "id": "vision",
                "display_name": cfg.display_name if cfg else "Vision",
                "emoji": cfg.emoji if (cfg and cfg.emoji) else "\U0001f4f7",
            })
        return rows

    @rx.var(
        deps=["agent_tuning", "_agent_dropdown_items"],
        auto_deps=False,
    )
    def sampling_rows(self) -> list[SamplingRow]:
        """Per-agent sampling table rows — one for every registered agent."""
        from ..lib.agent_settings import get_agent_setting
        rows: list[SamplingRow] = []
        for entry in self._ui_agent_list():
            agent = entry["id"]
            rows.append(SamplingRow(
                id=agent,
                emoji=entry["emoji"],
                label=entry["display_name"],
                temp=str(get_agent_setting(self, agent, "temperature")),
                top_k=str(get_agent_setting(self, agent, "top_k")),
                top_p=str(get_agent_setting(self, agent, "top_p")),
                min_p=str(get_agent_setting(self, agent, "min_p")),
                repeat_penalty=str(get_agent_setting(self, agent, "repeat_penalty")),
            ))
        return rows

    @rx.var(deps=["agent_tuning", "_agent_dropdown_items"], auto_deps=False)
    def ctx_rows(self) -> list[CtxRow]:
        """Per-agent manual-context columns — one for every registered agent."""
        from ..lib.agent_settings import get_agent_setting
        rows: list[CtxRow] = []
        for entry in self._ui_agent_list():
            agent = entry["id"]
            rows.append(CtxRow(
                id=agent,
                emoji=entry["emoji"],
                label=entry["display_name"],
                enabled=get_agent_setting(self, agent, "num_ctx_manual_enabled"),
                value=get_agent_setting(self, agent, "num_ctx_manual"),
            ))
        return rows

    @rx.var(
        deps=["agent_tuning", "multi_agent_mode", "ui_language", "_agent_dropdown_items"],
        auto_deps=False,
    )
    def agent_model_rows(self) -> list[AgentModelRow]:
        """Secondary-agent model rows (Sokrates/Salomo/custom agents).

        Visibility rules live here (server-side): Sokrates only outside
        Standard mode, Salomo only in consensus/tribunal, custom agents
        always. AIfred/Automatik/Vision keep their special rows.
        """
        from ..lib.agent_settings import get_agent_setting
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"
        rows: list[AgentModelRow] = []
        for entry in self._ui_agent_list(include_vision=False):
            agent = entry["id"]
            if agent == "aifred":
                continue
            if agent == "sokrates" and self.multi_agent_mode == "standard":
                continue
            # Salomo participates in consensus/tribunal (judge role) and can
            # be picked as a Symposion participant — show the select there too.
            if agent == "salomo" and self.multi_agent_mode not in ("auto_consensus", "tribunal", "symposion"):
                continue
            personality_key = (
                f"personality_{agent}_tooltip"
                if agent in ("sokrates", "salomo")
                else "personality_generic_tooltip"
            )
            model: str = get_agent_setting(self, agent, "model")
            rows.append(AgentModelRow(
                id=agent,
                emoji=entry["emoji"],
                label=f"{entry['display_name']}-LLM:",
                select_id=self._model_select_id(agent),
                model_empty=(model == ""),
                personality=get_agent_setting(self, agent, "personality"),
                personality_tooltip=t(personality_key, lang=lang),
                reasoning=get_agent_setting(self, agent, "reasoning"),
                thinking_mode=self._agent_thinking_mode(agent),
                thinking_options=self._agent_thinking_options(agent),
                has_speed_variant=get_agent_setting(self, agent, "has_speed_variant"),
                speed_mode=get_agent_setting(self, agent, "speed_mode"),
                rope_display=self._rope_display(agent),
            ))
        return rows
