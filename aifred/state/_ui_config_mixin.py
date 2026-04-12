"""UI configuration mixin for AIfred state.

Handles temperature, context settings, research mode, web search,
STT configuration, and general UI state.
"""

from __future__ import annotations

import reflex as rx


class UIConfigMixin(rx.State, mixin=True):
    """Mixin for UI configuration, research mode, and STT."""

    # ── Temperature ───────────────────────────────────────────────
    temperature: float = 0.3  # Default: low temperature for factual responses
    temperature_mode: str = "auto"  # "auto" (Intent-Detection) | "manual" (user slider)

    # ── Context Window Control ────────────────────────────────────
    num_ctx: int = 32768

    # Per-Agent manual values (used only when corresponding _enabled flag is True)
    num_ctx_manual_aifred: int = 4096  # Manual value for AIfred - Ollama default
    num_ctx_manual_sokrates: int = 4096  # Manual value for Sokrates
    num_ctx_manual_salomo: int = 4096  # Manual value for Salomo
    # Per-Agent manual toggle (True = use manual value, False = use auto-calibrated)
    num_ctx_manual_aifred_enabled: bool = False
    num_ctx_manual_sokrates_enabled: bool = False
    num_ctx_manual_salomo_enabled: bool = False

    # Vision Context Window Control (PERSISTENT)
    vision_num_ctx_enabled: bool = False  # True = use manual value, False = use calibrated
    vision_num_ctx: int = 32768  # Manual context value (default: 32K)

    # ── Research Settings ─────────────────────────────────────────
    # NOTE: research_mode is now per-session (session_storage.DEFAULT_SESSION_CONFIG).
    # Class default only applies before any session is loaded.
    research_mode: str = "automatik"  # "quick", "deep", "automatik", "none"
    research_mode_display: str = "\u2728 Automatik (KI entscheidet)"  # UI display value

    # ── STT (Whisper) Settings ────────────────────────────────────
    whisper_model_key: str = "small"  # Whisper model key (tiny/base/small/medium/large)
    show_transcription: bool = False  # Show transcribed text for editing before sending

    # ================================================================
    # AUTO-REFRESH TOGGLE
    # ================================================================

    def toggle_auto_refresh(self) -> None:
        """Toggle auto-scroll for all areas (Debug Console, Chat History, AI Response)."""
        self.auto_refresh_enabled = not self.auto_refresh_enabled  # type: ignore[has-type]
        self._save_settings()  # type: ignore[attr-defined]

    # ================================================================
    # TEMPERATURE
    # ================================================================

    def set_temperature(self, temp: list[float]) -> None:
        """Set temperature (from slider which returns list[float])."""
        self.temperature = temp[0] if isinstance(temp, list) else temp
        self._save_settings()  # type: ignore[attr-defined]

    def set_temperature_mode(self, checked: bool) -> None:
        """Set temperature mode from toggle switch.

        Args:
            checked: True = manual mode (user slider), False = auto mode (Intent-Detection)
        """
        self.temperature_mode = "manual" if checked else "auto"
        self._save_settings()  # type: ignore[attr-defined]
        mode_label = "Manual" if checked else "Auto"
        self.add_debug(f"\U0001f321\ufe0f Temperature Mode: {mode_label}")  # type: ignore[attr-defined]

    def set_temperature_mode_radio(self, value: str) -> None:
        """Set temperature mode from radio group (returns string directly).

        Args:
            value: "auto" or "manual"
        """
        self.temperature_mode = value
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"\U0001f321\ufe0f Temperature Mode: {value.title()}")  # type: ignore[attr-defined]

    def set_temperature_mode_from_display(self, display_value: str) -> None:
        """Set temperature mode from radio display value.

        Args:
            display_value: Display string like "Auto (Intent-Detection)" or "Manuell"
        """
        # Extract mode from display value
        if "Auto" in display_value:
            self.temperature_mode = "auto"
        else:
            self.temperature_mode = "manual"
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"\U0001f321\ufe0f Temperature Mode: {self.temperature_mode.title()}")  # type: ignore[attr-defined]

    # ================================================================
    # CONTEXT WINDOW CONTROL
    # ================================================================

    def set_num_ctx_manual_aifred(self, value: str) -> None:
        """Set manual num_ctx value for AIfred (only used when aifred_enabled=True)."""
        from ..lib.config import NUM_CTX_MANUAL_MAX
        from ..lib.formatting import format_number

        try:
            # Handle locale-formatted numbers and spaces (e.g., "1.472", "1,472", "1 472")
            clean_value = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
            if not clean_value:
                return  # Empty input, ignore
            num_value = int(clean_value)
            if num_value < 1:
                num_value = 1
            if num_value > NUM_CTX_MANUAL_MAX:
                num_value = NUM_CTX_MANUAL_MAX
            self.num_ctx_manual_aifred = num_value
            self.add_debug(f"\U0001f527 Manual Context (AIfred): {format_number(num_value)}")  # type: ignore[attr-defined]
            # IMPORTANT: Not saved in settings.json!
        except (ValueError, TypeError):
            self.add_debug(f"\u274c Invalid Context value: {value}")  # type: ignore[attr-defined]

    def set_num_ctx_manual_sokrates(self, value: str) -> None:
        """Set manual num_ctx value for Sokrates (only used when mode=manual)."""
        from ..lib.config import NUM_CTX_MANUAL_MAX
        from ..lib.formatting import format_number

        try:
            clean_value = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
            if not clean_value:
                return
            num_value = int(clean_value)
            if num_value < 1:
                num_value = 1
            if num_value > NUM_CTX_MANUAL_MAX:
                num_value = NUM_CTX_MANUAL_MAX
            self.num_ctx_manual_sokrates = num_value
            self.add_debug(f"\U0001f527 Manual Context (Sokrates): {format_number(num_value)}")  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            self.add_debug(f"\u274c Invalid Context value: {value}")  # type: ignore[attr-defined]

    def set_num_ctx_manual_salomo(self, value: str) -> None:
        """Set manual num_ctx value for Salomo (only used when mode=manual)."""
        from ..lib.config import NUM_CTX_MANUAL_MAX
        from ..lib.formatting import format_number

        try:
            clean_value = str(value).replace(".", "").replace(",", "").replace(" ", "").strip()
            if not clean_value:
                return
            num_value = int(clean_value)
            if num_value < 1:
                num_value = 1
            if num_value > NUM_CTX_MANUAL_MAX:
                num_value = NUM_CTX_MANUAL_MAX
            self.num_ctx_manual_salomo = num_value
            self.add_debug(f"\U0001f527 Manual Context (Salomo): {format_number(num_value)}")  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            self.add_debug(f"\u274c Invalid Context value: {value}")  # type: ignore[attr-defined]

    def toggle_num_ctx_manual_aifred(self, enabled: bool) -> None:
        """Toggle manual context for AIfred."""
        self.num_ctx_manual_aifred_enabled = enabled
        status = "Manual" if enabled else "Auto"
        from ..lib.agent_config import get_agent_label
        self.add_debug(f"{get_agent_label('aifred')} Context: {status}")  # type: ignore[attr-defined]

    def toggle_num_ctx_manual_sokrates(self, enabled: bool) -> None:
        """Toggle manual context for Sokrates."""
        self.num_ctx_manual_sokrates_enabled = enabled
        status = "Manual" if enabled else "Auto"
        self.add_debug(f"\U0001f3db\ufe0f Sokrates Context: {status}")  # type: ignore[attr-defined]

    def toggle_num_ctx_manual_salomo(self, enabled: bool) -> None:
        """Toggle manual context for Salomo."""
        self.num_ctx_manual_salomo_enabled = enabled
        status = "Manual" if enabled else "Auto"
        self.add_debug(f"\U0001f451 Salomo Context: {status}")  # type: ignore[attr-defined]

    def toggle_vision_num_ctx(self, enabled: bool) -> None:
        """Toggle manual context for Vision-LLM (PERSISTENT)."""
        self.vision_num_ctx_enabled = enabled
        status = "Manual" if enabled else "Auto (calibrated)"
        self.add_debug(f"\U0001f441\ufe0f Vision Context: {status}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def set_vision_num_ctx(self, value: str) -> None:
        """Set manual vision context value (PERSISTENT)."""
        from ..lib.config import NUM_CTX_MANUAL_MAX
        from ..lib.formatting import format_number

        try:
            num_value = int(value)
            if num_value < 1024:  # Minimum 1K for vision
                num_value = 1024
            if num_value > NUM_CTX_MANUAL_MAX:
                num_value = NUM_CTX_MANUAL_MAX
            self.vision_num_ctx = num_value
            self.add_debug(f"\U0001f441\ufe0f Manual Context (Vision): {format_number(num_value)}")  # type: ignore[attr-defined]
            self._save_settings()  # type: ignore[attr-defined]
        except (ValueError, TypeError):
            self.add_debug(f"\u274c Invalid Vision Context value: {value}")  # type: ignore[attr-defined]

    def calculate_manual_context(self) -> None:
        """Calculate and display context limits.

        Called when user clicks "Calculate" button.
        Shows all LLM context values (manual or auto-calibrated from persistent cache).
        """
        from ..lib.formatting import format_number
        from ..lib.model_vram_cache import get_ollama_calibration, get_rope_factor_for_model

        # Collect effective limits for compression calculation
        effective_limits: list[int] = []

        def format_model_with_ctx(model_display: str, ctx_value: int, mode: str) -> str:
            """Format model display with context info and mode indicator."""
            if ctx_value > 0:
                ctx_str = format_number(ctx_value)
                mode_str = mode
            else:
                # Not calibrated - show clear indication
                ctx_str = "n/a"
                mode_str = "nicht kalibriert" if mode == "auto" else mode
            if model_display.endswith(")"):
                return model_display[:-1] + f", {ctx_str} ctx, {mode_str})"
            return f"{model_display} ({ctx_str} ctx, {mode_str})"

        self.add_debug("\U0001f4ca Context configuration:")  # type: ignore[attr-defined]

        # AIfred - get auto value from persistent cache if not manual
        if self.num_ctx_manual_aifred_enabled:
            aifred_ctx = self.num_ctx_manual_aifred
            mode = "manual"
        else:
            rope_factor = get_rope_factor_for_model(self.aifred_model_id)  # type: ignore[attr-defined]
            aifred_ctx = get_ollama_calibration(self.aifred_model_id, rope_factor) or 0  # type: ignore[attr-defined]
            mode = "auto"
        from ..lib.agent_config import get_agent_label
        self.add_debug(f"   {get_agent_label('aifred')}: {format_model_with_ctx(self.aifred_model, aifred_ctx, mode)}")  # type: ignore[attr-defined]
        if aifred_ctx > 0:
            effective_limits.append(aifred_ctx)

        # Sokrates - always show (needed for multi-agent context display)
        if self.sokrates_model_id:  # type: ignore[attr-defined]
            if self.num_ctx_manual_sokrates_enabled:
                sokrates_ctx = self.num_ctx_manual_sokrates
                mode = "manual"
            else:
                rope_factor = get_rope_factor_for_model(self.sokrates_model_id)  # type: ignore[attr-defined]
                sokrates_ctx = get_ollama_calibration(self.sokrates_model_id, rope_factor) or 0  # type: ignore[attr-defined]
                mode = "auto"
            self.add_debug(f"   {get_agent_label('sokrates')}: {format_model_with_ctx(self.sokrates_model, sokrates_ctx, mode)}")  # type: ignore[attr-defined]
            if sokrates_ctx > 0:
                effective_limits.append(sokrates_ctx)

        # Salomo - always show (needed for multi-agent context display)
        if self.salomo_model_id:  # type: ignore[attr-defined]
            if self.num_ctx_manual_salomo_enabled:
                salomo_ctx = self.num_ctx_manual_salomo
                mode = "manual"
            else:
                rope_factor = get_rope_factor_for_model(self.salomo_model_id)  # type: ignore[attr-defined]
                salomo_ctx = get_ollama_calibration(self.salomo_model_id, rope_factor) or 0  # type: ignore[attr-defined]
                mode = "auto"
            self.add_debug(f"   {get_agent_label('salomo')}: {format_model_with_ctx(self.salomo_model, salomo_ctx, mode)}")  # type: ignore[attr-defined]
            if salomo_ctx > 0:
                effective_limits.append(salomo_ctx)

        # Vision - always show if vision model is selected
        if self.vision_model_id:  # type: ignore[attr-defined]
            if self.vision_num_ctx_enabled:
                vision_ctx = self.vision_num_ctx
                mode = "manual"
            else:
                rope_factor = get_rope_factor_for_model(self.vision_model_id)  # type: ignore[attr-defined]
                vision_ctx = get_ollama_calibration(self.vision_model_id, rope_factor) or 0  # type: ignore[attr-defined]
                mode = "auto"
            self.add_debug(f"   {get_agent_label('vision')}: {format_model_with_ctx(self.vision_model, vision_ctx, mode)}")  # type: ignore[attr-defined]
            # Vision context is NOT added to effective_limits - separate from chat context

        # Calculate effective limit (minimum of all active limits)
        effective_limit = min(effective_limits) if effective_limits else 0

        # Update cached min context limit
        self._min_agent_context_limit = effective_limit  # type: ignore[has-type]

        # Show history utilization and warn if compression will trigger
        self._log_history_utilization(effective_limit)

    # ================================================================
    # RESEARCH MODE
    # ================================================================

    def set_research_mode(self, mode: str) -> None:
        """Set research mode (from internal value, e.g. pill button click)."""
        from ..lib import TranslationManager

        self.research_mode = mode
        self.research_mode_display = TranslationManager.get_research_mode_display(
            mode, self.ui_language  # type: ignore[attr-defined]
        )
        self.add_debug(f"\U0001f50d Research mode: {mode}")  # type: ignore[attr-defined]
        self._persist_session_config()  # type: ignore[attr-defined]

    def set_research_mode_display(self, display_value: str) -> None:
        """Set research mode from UI display value."""
        from ..lib import TranslationManager

        # Use translation manager to get the internal mode value
        self.research_mode_display = display_value
        self.research_mode = TranslationManager.get_research_mode_value(
            display_value, self.ui_language  # type: ignore[attr-defined]
        )
        self.add_debug(f"\U0001f50d Research mode: {self.research_mode} (from: '{display_value}')")  # type: ignore[attr-defined]
        self._persist_session_config()  # type: ignore[attr-defined]

    # ================================================================
    # STT (WHISPER) SETTINGS
    # ================================================================

    @rx.var(deps=["whisper_model_key", "ui_language"], auto_deps=False)
    def whisper_model_display(self) -> str:
        """Get localized display name for current Whisper model.

        Maps key (tiny/base/small/medium/large) to translated display name.
        """
        from ..lib import TranslationManager

        # Translation map: key -> translation_key
        key_to_translation = {
            "tiny": "stt_model_tiny",
            "base": "stt_model_base",
            "small": "stt_model_small",
            "medium": "stt_model_medium",
            "large-v3": "stt_model_large",
            "large": "stt_model_large",  # Alias
        }
        translation_key = key_to_translation.get(self.whisper_model_key, "stt_model_small")
        return TranslationManager.get_text(translation_key, self.ui_language)  # type: ignore[attr-defined]

    def set_whisper_model(self, model_display_name: str) -> None:
        """Set Whisper model selection.

        The model runs in a Docker container — changing the model here
        saves the preference. The container uses the WHISPER_MODEL env var.
        To apply a model change, the Whisper container must be restarted.
        """
        model_key = model_display_name.split("(")[0].strip() if "(" in model_display_name else model_display_name
        self.whisper_model_key = model_key
        self.add_debug(f"\U0001f3a4 Whisper Model: {model_key} (container restart needed to apply)")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_show_transcription(self) -> None:
        """Toggle show transcription mode."""
        self.show_transcription = not self.show_transcription
        mode = "Edit text" if self.show_transcription else "Send directly"
        self.add_debug(f"\U0001f3a4 Transcription: {mode}")  # type: ignore[attr-defined]
        self._save_settings()  # type: ignore[attr-defined]

    def toggle_audio_recording(self):  # type: ignore[no-untyped-def]
        """Toggle audio recording (calls JavaScript MediaRecorder)."""
        return rx.call_script("toggleRecording()")
