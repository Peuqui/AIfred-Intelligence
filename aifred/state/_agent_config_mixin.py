"""Agent configuration mixin for AIfred state.

Handles per-agent personality, reasoning, thinking mode,
sampling parameters, speed mode, RoPE factors, multi-agent mode settings,
temperature configuration, and model selection for Sokrates/Salomo.

Per-agent tuning lives in the ``agent_tuning`` dict (one AgentTuning bucket
per agent id) — access via the ``agent_settings`` helpers.
"""

from __future__ import annotations

from typing import ClassVar, List

import reflex as rx

from ..lib.agent_tuning import (
    AgentModelRow,
    AgentTuning,
    CtxRow,
    SamplingRow,
    default_agent_tuning,
)
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
    VISION_DEFAULT_TEMPERATURE,
    VISION_DEFAULT_TOP_K,
    VISION_DEFAULT_TOP_P,
    VISION_DEFAULT_MIN_P,
    VISION_DEFAULT_REPEAT_PENALTY,
)

# NOTE (mypy SCC): agent_tuning.py is dependency-free and safe to import at
# module level. aifred.lib.agent_settings must still be imported lazily in
# methods — a module-level import changes mypy's SCC analysis order and
# breaks type inference of the temperature state var.

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

    # ── Per-Agent Tuning (SSOT: one bucket per agent id) ──────────
    agent_tuning: dict[str, AgentTuning] = default_agent_tuning()

    sampling_reset_key: int = 0  # UI key counter to force re-mount on reset

    # ── Automatik (intent LLM — not an agent, own fields) ─────────
    automatik_rope_factor: float = 1.0

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
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        new_val = not get_agent_setting(self, agent, feature)
        set_agent_setting(self, agent, feature, new_val)

        # Emoji for debug message
        if feature == "personality":
            emoji = _PERSONALITY_EMOJI.get(agent, "\U0001f916")
        else:
            emoji = _FEATURE_EMOJI[feature]

        status = "ON" if new_val else "OFF"
        self.add_debug(f"{emoji} {agent.capitalize()} {feature}: {status}")  # type: ignore[attr-defined]

        self._save_settings()  # type: ignore[attr-defined]

        # Sync to prompt_loader (if setter exists — thinking has none)
        setter_name = _FEATURE_META[feature][1]
        if setter_name:
            from ..lib import prompt_loader
            getattr(prompt_loader, setter_name)(agent, new_val)

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
        from ..lib.agent_settings import set_agent_setting
        off_label, on_label = self._thinking_mode_labels()
        if mode == off_label:
            mode = "off"
        elif mode in (on_label, self._thinking_on_option(agent)):
            # Plain label or default-annotated variant ("An (xhigh)") —
            # both mean "on with template default", no stored effort.
            mode = "on"
        set_agent_setting(self, agent, "thinking", mode != "off")
        set_agent_setting(
            self, agent, "reasoning_effort",
            "" if mode in ("off", "on") else mode,
        )
        emoji = _FEATURE_EMOJI["thinking"]
        self.add_debug(f"{emoji} {agent.capitalize()} thinking: {mode}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def _agent_thinking_mode(self, agent: str) -> str:
        """Current dropdown label derived from (thinking, effort)."""
        from ..lib.agent_settings import get_agent_setting, model_owner
        off_label, _ = self._thinking_mode_labels()
        if not get_agent_setting(self, agent, "thinking"):
            return off_label
        effort: str = get_agent_setting(self, agent, "reasoning_effort")
        # Ein explizit gespeichertes Level, das zufällig dem Template-Default
        # entspricht (Qwen3.8: "xhigh"), zeigt "An (xhigh)" statt "xhigh" —
        # als eigener Options-Eintrag existiert "xhigh" nicht mehr (siehe
        # _agent_thinking_options), sonst hätte das Dropdown einen Wert
        # gewählt, der in seiner eigenen Liste fehlt.
        default: str = get_agent_setting(
            self, model_owner(self, agent), "reasoning_default",
        )
        if effort and default and effort == default:
            return self._thinking_on_option(agent)
        return effort or self._thinking_on_option(agent)

    def _load_agent_reasoning_levels(self, agent: str, model_id: str) -> None:
        """Refresh the agent's ``reasoning_levels`` for a newly selected model
        and clear a selected effort level the new model doesn't support.
        Levels exist only for llama.cpp models (embedded chat template)."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        levels: list[str] = []
        default = ""
        if model_id and self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            from ..lib.gguf_utils import resolve_reasoning_levels
            from ..lib.model_vram_cache import get_reasoning_default_for_model
            levels = resolve_reasoning_levels(model_id)
            # resolve above just (re)wrote the cache entry incl. default
            default = get_reasoning_default_for_model(model_id) or ""
        set_agent_setting(self, agent, "reasoning_levels", levels)
        set_agent_setting(self, agent, "reasoning_default", default)
        # Effort gegen die EFFEKTIVEN Stufen validieren (Owner-aware):
        # Beim Umstellen auf "(wie AIfred-LLM)" ist die eigene Liste leer,
        # aber ein gesetztes "high" bleibt gültig, solange der Owner es kann.
        effective = self._effective_reasoning_levels(agent)
        if get_agent_setting(self, agent, "reasoning_effort") not in ("", *effective):
            set_agent_setting(self, agent, "reasoning_effort", "")
            self._save_settings()  # type: ignore[attr-defined]

    # AIfred/Vision keep dedicated vars — their rows are special-cased in
    # the UI (main-model select, vision badges), not part of the foreach.
    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def aifred_thinking_mode(self) -> str:
        return self._agent_thinking_mode("aifred")

    @rx.var(deps=["agent_tuning", "ui_language"], auto_deps=False)
    def vision_thinking_mode(self) -> str:
        return self._agent_thinking_mode("vision")

    def _effective_reasoning_levels(self, agent: str) -> list[str]:
        """Reasoning-Levels des Modells, das für diesen Agenten real lädt.

        Agenten ohne eigenes Modell ("(wie AIfred-LLM)") erben AIfreds LLM —
        und damit dessen steuerbare Effort-Stufen (z.B. DeepSeek-V4
        high/max). Vorher zeigte ihr Dropdown nur Aus/An, weil die eigene
        ``reasoning_levels``-Liste leer blieb. Owner-Lookup über die
        ``model_owner``-SSOT, damit ein AIfred-Modellwechsel die Stufen
        aller Erben automatisch mitzieht."""
        from ..lib.agent_settings import get_agent_setting, model_owner
        levels: list[str] = get_agent_setting(
            self, model_owner(self, agent), "reasoning_levels",
        )
        return levels

    def _thinking_on_option(self, agent: str) -> str:
        """Dropdown-Label für "Thinking an ohne Stufen-Vorgabe".

        Trägt das Template seinen eigenen Default (Qwen3.8:
        ``default('xhigh')``), zeigt das Label ihn an — "An (xhigh)" —
        weil ein nacktes "An" sonst kontraintuitiv NEBEN den expliziten
        Stufen steht, obwohl es einer davon entspricht. Ohne
        Template-Default (DeepSeek) bleibt es schlicht "An"."""
        from ..lib.agent_settings import get_agent_setting, model_owner
        _, on_label = self._thinking_mode_labels()
        default: str = get_agent_setting(
            self, model_owner(self, agent), "reasoning_default",
        )
        return f"{on_label} ({default})" if default else on_label

    def _agent_thinking_options(self, agent: str) -> list[str]:
        from ..lib.agent_settings import get_agent_setting, model_owner
        off_label, _ = self._thinking_mode_labels()
        levels = self._effective_reasoning_levels(agent)
        # "An (xhigh)" deckt bereits die Stufe ab, auf die das Template ohne
        # explizite Vorgabe von selbst auflöst — sie zusätzlich als eigenen
        # "xhigh"-Eintrag zu zeigen wäre dieselbe Redundanz wie "high" vs.
        # "xhigh" (Template-Alias), nur zwischen implizitem und explizitem
        # Wert statt zwischen zwei Strings. reasoning_levels selbst bleibt
        # unangetastet (Validierung in _load_agent_reasoning_levels braucht
        # den vollen Ground-Truth-Satz) — gefiltert wird nur die Anzeige.
        default: str = get_agent_setting(
            self, model_owner(self, agent), "reasoning_default",
        )
        if default:
            levels = [lv for lv in levels if lv != default]
        return [off_label, self._thinking_on_option(agent)] + levels

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
            self.add_debug(f"\U0001f3b2 {agent.capitalize()} {param}={final_val}")  # type: ignore[attr-defined]
            self._save_settings()  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

    def _reset_agent_sampling(self, agent: str, include_temperature: bool = True) -> None:
        """Reset sampling parameters for an agent to model/backend defaults.

        Args:
            agent: any registered agent id
            include_temperature: If True, reset temperature too (model change / reset button).
                If False, keep current temperature (app restart -- temperature is persisted).
        """
        from ..lib.agent_settings import set_agent_setting
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
            # Agents with empty model_id inherit from AIfred
            from ..lib.agent_settings import get_agent_base_model_id
            model_id = get_agent_base_model_id(self, agent)
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
            set_agent_setting(self, agent, "temperature", defaults["temperature"])
        set_agent_setting(self, agent, "top_k", int(defaults["top_k"]))
        set_agent_setting(self, agent, "top_p", defaults["top_p"])
        set_agent_setting(self, agent, "min_p", defaults["min_p"])
        set_agent_setting(self, agent, "repeat_penalty", defaults["repeat_penalty"])

        # Debug log — use get_agent_label for emoji + display_name from config
        from ..lib.agent_config import get_agent_label
        from ..lib.agent_settings import get_agent_base_model_id
        reset_model = get_agent_base_model_id(self, agent)
        model_info = f" → {reset_model} defaults" if reset_model else ""
        temp_info = f"temp={defaults['temperature']}, " if include_temperature else ""
        self.add_debug(  # type: ignore[attr-defined]
            f"{get_agent_label(agent)} sampling reset{model_info}: "
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

        Accepts any registered agent — custom agents resolve to AIfred's
        model/speed bucket via the agent_settings SSOT.

        Delegates the suffix resolution to the SSOT helper
        :func:`aifred.lib.calibration.resolve_variant_suffix`, so the
        agents, the Automatik path, the compression-ctx lookup and the
        chat gate all use the same fallback rules. The tuning buckets
        always contain the base ID; this method is what every code path
        sends to the backend.

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
        from ..lib.agent_settings import get_agent_setting
        base_id: str = get_agent_setting(self, agent, "model_id")
        if not base_id or self.backend_type != "llamacpp":  # type: ignore[attr-defined]
            return base_id

        from ..lib.calibration import resolve_effective_suffix
        from ..lib.config import LLAMASWAP_CONFIG_PATH

        suffix = resolve_effective_suffix(
            LLAMASWAP_CONFIG_PATH,
            base_id,
            speed_on=get_agent_setting(self, agent, "speed_mode"),
            has_speed_variant=get_agent_setting(self, agent, "has_speed_variant"),
            tts_active=bool(self.enable_tts),  # type: ignore[attr-defined]
            tts_engine=self.tts_engine,  # type: ignore[attr-defined]
        )
        return base_id + suffix

    # ================================================================
    # SPEED MODE TOGGLES (llamacpp only)
    # ================================================================

    def _toggle_speed_mode(self, agent: str) -> None:
        """Toggle speed/context mode for any agent."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        new_val = not get_agent_setting(self, agent, "speed_mode")
        set_agent_setting(self, agent, "speed_mode", new_val)
        self.add_debug(f"\U0001f500 {agent.capitalize()} mode: {self._speed_mode_debug_str(agent, new_val)}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

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
        ctx = get_model_native_context(effective, self.backend_type)  # type: ignore[attr-defined]
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

    def set_aifred_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for AIfred-LLM."""
        from ..lib.agent_settings import get_agent_setting, set_agent_setting
        # Convert UI string to float
        factor = float(value.replace("x", ""))
        set_agent_setting(self, "aifred", "rope_factor", factor)
        self.add_debug(f"\U0001f39a️ AIfred RoPE Factor: {value}")  # type: ignore[attr-defined]

        # Save to VRAM cache (per-model setting)
        aifred_model_id: str = get_agent_setting(self, "aifred", "model_id")
        if aifred_model_id:
            from ..lib.model_vram_cache import (
                set_rope_factor_for_model,
                get_ollama_calibrated_max_context,
                get_rope_factor_for_model,
                get_llamacpp_calibration,
                format_model_with_ctx as _format_model_with_ctx,
            )
            set_rope_factor_for_model(aifred_model_id, factor)

            # SSOT in lib/model_vram_cache — thin wrapper binds backend_type
            def format_model_with_ctx(model_display: str, model_id: str) -> str:
                return _format_model_with_ctx(model_display, model_id, self.backend_type)  # type: ignore[attr-defined]

            # Re-display all agent models with updated context limits
            from ..lib.agent_config import get_agent_label
            self.add_debug(f"   {get_agent_label('aifred')}: {format_model_with_ctx(get_agent_setting(self, 'aifred', 'model'), aifred_model_id)}")  # type: ignore[attr-defined]
            if self.multi_agent_mode != "standard":
                for secondary in ("sokrates", "salomo"):
                    sec_id: str = get_agent_setting(self, secondary, "model_id")
                    if sec_id:
                        self.add_debug(f"   {get_agent_label(secondary)}: {format_model_with_ctx(get_agent_setting(self, secondary, 'model'), sec_id)}")  # type: ignore[attr-defined]

            # Update cached min context limit
            context_limits: list[int] = []
            for chat_agent in ("aifred", "sokrates", "salomo"):
                model_id = get_agent_setting(self, chat_agent, "model_id")
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
                extended_ctx = get_ollama_calibrated_max_context(aifred_model_id, rope_factor=2.0)
                if extended_ctx is None:
                    self.add_debug("⚠️ No RoPE 2x calibration found - please calibrate first!")  # type: ignore[attr-defined]
            else:
                native_ctx = get_ollama_calibrated_max_context(aifred_model_id, rope_factor=1.0)
                if native_ctx is None:
                    self.add_debug("⚠️ No native calibration found - please calibrate first!")  # type: ignore[attr-defined]

    def set_automatik_rope_factor(self, value: str) -> None:
        """Set RoPE scaling factor for Automatik-LLM."""
        factor = float(value.replace("x", ""))
        self.automatik_rope_factor = factor
        effective_auto = self._effective_automatik_id  # type: ignore[attr-defined]
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
            self.add_debug(f"\U0001f321️ {agent.capitalize()} temperature={get_agent_setting(self, agent, 'temperature')}")  # type: ignore[attr-defined]
            self._save_settings()  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            pass

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
        self.add_debug(f"\U0001f5f3️ Consensus type: {type_label}")  # type: ignore[attr-defined]

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
    def secondary_available_models(self) -> list[str]:
        """Model list with localized '(wie AIfred-LLM)' as first selectable option."""
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        return [t("sokrates_llm_same", lang=lang)] + list(self.available_models)  # type: ignore[attr-defined]

    def _model_select_value(self, agent: str) -> str:
        """Maps empty model (inherit) to the localized sentinel label."""
        from ..lib.agent_settings import get_agent_setting
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        model: str = get_agent_setting(self, agent, "model")
        return t("sokrates_llm_same", lang=lang) if model == "" else model

    # Debug-emoji for the secondary-model selects (historical, differs from
    # the agents.json emoji — 🧠 for Sokrates, 👑 for Salomo)
    _SECONDARY_MODEL_EMOJI: ClassVar[dict[str, str]] = {
        "sokrates": "\U0001f9e0",
        "salomo": "\U0001f451",
    }

    def _set_secondary_agent_model(self, agent: str, model: str) -> None:
        """Set Sokrates/Salomo LLM model for multi-agent debate (shared logic)."""
        from ..lib.agent_settings import set_agent_setting
        from ..lib.i18n import t
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
        if model == t("sokrates_llm_same", lang=lang):
            model = ""
        set_agent_setting(self, agent, "model", model)
        model_id: str = self._resolve_model_id(model)  # type: ignore[attr-defined]
        set_agent_setting(self, agent, "model_id", model_id)

        if not model_id:
            # "(wie AIfred-LLM)" selected -- clear speed variant
            set_agent_setting(self, agent, "has_speed_variant", False)
            set_agent_setting(self, agent, "speed_mode", False)

        # Load all model parameters from cache
        if self.backend_id == "ollama" and model_id:  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import get_model_parameters
            params = get_model_parameters(model_id)
            set_agent_setting(self, agent, "rope_factor", params["rope_factor"])
            set_agent_setting(self, agent, "max_context", params["max_context"])
            set_agent_setting(self, agent, "is_hybrid", params["is_hybrid"])
            set_agent_setting(self, agent, "supports_thinking", params["supports_thinking"])
        elif self.backend_type == "llamacpp" and model_id:  # type: ignore[attr-defined]
            from ..lib.calibration import model_has_speed_variant
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_thinking_support_for_model,
            )
            set_agent_setting(self, agent, "rope_factor", 1.0)
            set_agent_setting(self, agent, "max_context", get_llamacpp_calibration(model_id) or 0)
            set_agent_setting(self, agent, "is_hybrid", False)
            set_agent_setting(self, agent, "supports_thinking", get_thinking_support_for_model(model_id))
            has_speed = model_has_speed_variant(model_id)
            set_agent_setting(self, agent, "has_speed_variant", has_speed)
            if not has_speed:
                set_agent_setting(self, agent, "speed_mode", False)
        self._load_agent_reasoning_levels(agent, model_id)

        # Reset sampling params to model defaults
        self._reset_agent_sampling(agent)

        self._save_settings()  # type: ignore[attr-defined]
        emoji = self._SECONDARY_MODEL_EMOJI.get(agent, "\U0001f916")
        if model:
            self.add_debug(f"{emoji} {agent.capitalize()}-LLM: {model}")  # type: ignore[attr-defined]
            self._show_model_calibration_info(model_id)  # type: ignore[attr-defined]
        else:
            self.add_debug(f"{emoji} {agent.capitalize()}-LLM: (same as Main-LLM)")  # type: ignore[attr-defined]

    # ================================================================
    # GENERIC EVENT HANDLERS (rx.foreach rows pass the agent id)
    # ================================================================

    def set_agent_model_for(self, agent: str, model: str) -> None:
        """Model select handler for any secondary agent (incl. custom)."""
        self._set_secondary_agent_model(agent, model)

    def set_agent_sampling_for(self, agent: str, param: str, value: str) -> None:
        self._set_agent_sampling(agent, param, value)

    def set_agent_temperature_for(self, agent: str, value: str) -> None:
        self._set_temperature_input(agent, value)

    def reset_agent_sampling_for(self, agent: str) -> None:
        self._reset_agent_sampling(agent)
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_agent_personality_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_agent_feature(agent, "personality")

    def toggle_agent_reasoning_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_agent_feature(agent, "reasoning")

    def set_agent_thinking_mode_for(self, agent: str, mode: str) -> None:
        self._set_agent_thinking_mode(agent, mode)

    def toggle_agent_speed_mode_for(self, agent: str, _value: bool | None = None) -> None:
        self._toggle_speed_mode(agent)

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
        deps=["agent_tuning", "temperature_mode", "_agent_dropdown_items"],
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
                # Vision always uses manual temperature (no Auto mode)
                temp_disabled=(agent != "vision" and self.temperature_mode == "auto"),
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
        lang = self.ui_language if self.ui_language != "auto" else "de"  # type: ignore[attr-defined]
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
                select_value=self._model_select_value(agent),
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
