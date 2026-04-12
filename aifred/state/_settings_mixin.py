"""Settings mixin for AIfred state.

Handles saving/loading settings.json, user profile, UI language,
and reset-to-defaults.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import reflex as rx

from ..lib import TranslationManager, set_language
from ..lib.settings import SETTINGS_FILE, load_settings, save_settings


class SettingsMixin(rx.State, mixin=True):
    """Mixin for settings persistence and UI configuration."""

    # ── User Settings ─────────────────────────────────────────────
    ui_language: str = "de"  # "de" or "en" - for UI language
    user_name: str = ""  # User's name for personalized responses (optional)
    user_gender: str = "male"  # "male" or "female" - for proper salutation (Herr/Frau)

    # ── Message Hub Settings (generic, per-channel) ────────────
    # Toggles per channel: {"email": {"monitor": True, "auto_reply": False}, ...}
    channel_toggles: dict[str, dict[str, bool]] = {}
    # Security tier per channel: {"freeecho2": 4, "email": 1, ...}
    channel_security_tiers: dict[str, int] = {}

    # Generic Credentials Modal (one modal, dynamic fields per channel)
    channel_credentials_modal_open: bool = False
    channel_credentials_editing: str = ""  # Which channel we're editing (internal name)
    channel_credentials_display_name: str = ""  # Display name for modal title
    channel_credential_values: dict[str, str] = {}  # env_key → value
    channel_credential_fields: list[dict[str, str]] = []  # Rendered field descriptors
    channel_cred_show_password: bool = False  # Eye toggle

    # ── Plugin Manager Modal ─────────────────────────────────────
    plugin_manager_open: bool = False
    tool_plugin_toggles: dict[str, str] = {}  # {"epim": "1", "calculator": "1", ...}
    channel_allowlists: dict[str, str] = {}  # {"email": "user@mail.de, @family.de", "telegram": "123456"}

    # ── Audit Log Modal ──────────────────────────────────────────
    audit_log_open: bool = False
    audit_log_entries: list[dict[str, str]] = []  # [{timestamp, source, tool_name, ...}]

    # ── Settings File Tracking ────────────────────────────────────
    _last_settings_mtime: float = 0.0  # Last seen settings.json mtime (for multi-browser sync)
    _last_session_mtime: float = 0.0  # Last seen session file mtime (for multi-tab/cross-channel sync)

    # ================================================================
    # SETTINGS PERSISTENCE
    # ================================================================

    def _save_settings(self) -> None:
        """Save current settings to file (per-backend models)."""
        existing = load_settings() or {}
        backend_models = existing.get("backend_models", {})

        # Only update backend models if model IDs are validated against current backend.
        # Prevents saving stale IDs from a different backend during transitions
        # (e.g., backend_id already switched but model_ids not yet validated).
        if self.aifred_model_id and self.backend_id and self.available_models_dict:  # type: ignore[attr-defined, has-type]
            # model_id vars always contain base IDs (SSOT — speed suffix is computed)
            if self.aifred_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                backend_models[self.backend_id] = {  # type: ignore[attr-defined, has-type]
                    "aifred_model": self.aifred_model_id,  # type: ignore[attr-defined, has-type]
                    "automatik_model": self.automatik_model_id,  # type: ignore[attr-defined, has-type]
                    "vision_model": self.vision_model_id,  # type: ignore[attr-defined, has-type]
                    "sokrates_model": self.sokrates_model_id,  # type: ignore[attr-defined, has-type]
                    "salomo_model": self.salomo_model_id,  # type: ignore[attr-defined, has-type]
                }

        # Only save self.backend_type if backend is fully initialized.
        # Prevents the class default "ollama" from overwriting the persisted
        # backend_type when _save_settings() fires during startup
        # (e.g., vision model auto-select, capabilities check).
        if self._backend_initialized:  # type: ignore[attr-defined, has-type]
            saved_backend_type = self.backend_type  # type: ignore[attr-defined, has-type]
        else:
            saved_backend_type = existing.get("backend_type", self.backend_type)  # type: ignore[attr-defined, has-type]

        settings: Dict[str, Any] = {
            "backend_type": saved_backend_type,
            "cloud_api_provider": self.cloud_api_provider,  # type: ignore[attr-defined, has-type]
            # NOTE: research_mode, multi_agent_mode are per-session now (session_storage.DEFAULT_SESSION_CONFIG)
            "temperature": self.temperature,  # type: ignore[attr-defined, has-type]
            "temperature_mode": self.temperature_mode,  # type: ignore[attr-defined, has-type]
            "sokrates_temperature": self.sokrates_temperature,  # type: ignore[attr-defined, has-type]
            "sokrates_temperature_offset": self.sokrates_temperature_offset,  # type: ignore[attr-defined, has-type]
            "ui_language": self.ui_language,  # UI language (de/en)
            "user_name": self.user_name,  # User's name for personalized responses
            "user_gender": self.user_gender,  # Gender for salutation (male/female)
            "backend_models": backend_models,  # Merged: preserves all backends
            # Multi-Agent debate params (still global)
            "max_debate_rounds": self.max_debate_rounds,  # type: ignore[attr-defined, has-type]
            "consensus_type": self.consensus_type,  # type: ignore[attr-defined, has-type]
            "sokrates_model": self.sokrates_model_id,  # type: ignore[attr-defined, has-type]
            "salomo_model": self.salomo_model_id,  # type: ignore[attr-defined, has-type]
            "salomo_temperature": self.salomo_temperature,  # type: ignore[attr-defined, has-type]
            "salomo_temperature_offset": self.salomo_temperature_offset,  # type: ignore[attr-defined, has-type]
            # vLLM YaRN Settings (only enable/disable, factor is calculated dynamically)
            "enable_yarn": self.enable_yarn,  # type: ignore[attr-defined, has-type]
            # NOTE: yarn_factor is NOT saved - always starts at 1.0, system calibrates maximum
            # NOTE: vllm_max_tokens and vllm_native_context are NEVER saved!
            # They are calculated dynamically on every vLLM startup based on VRAM
            # Vision LLM Context Settings (PERSISTENT)
            "vision_num_ctx_enabled": self.vision_num_ctx_enabled,  # type: ignore[attr-defined, has-type]
            "vision_num_ctx": self.vision_num_ctx,  # type: ignore[attr-defined, has-type]
            # Agent Personality & Reasoning Settings
            "aifred_personality": self.aifred_personality,  # type: ignore[attr-defined, has-type]
            "sokrates_personality": self.sokrates_personality,  # type: ignore[attr-defined, has-type]
            "salomo_personality": self.salomo_personality,  # type: ignore[attr-defined, has-type]
            "vision_personality": self.vision_personality,  # type: ignore[attr-defined, has-type]
            "aifred_reasoning": self.aifred_reasoning,  # type: ignore[attr-defined, has-type]
            "sokrates_reasoning": self.sokrates_reasoning,  # type: ignore[attr-defined, has-type]
            "salomo_reasoning": self.salomo_reasoning,  # type: ignore[attr-defined, has-type]
            "vision_reasoning": self.vision_reasoning,  # type: ignore[attr-defined, has-type]
            "aifred_thinking": self.aifred_thinking,  # type: ignore[attr-defined, has-type]
            "sokrates_thinking": self.sokrates_thinking,  # type: ignore[attr-defined, has-type]
            "salomo_thinking": self.salomo_thinking,  # type: ignore[attr-defined, has-type]
            "vision_thinking": self.vision_thinking,  # type: ignore[attr-defined, has-type]
            # Sampling params (per-agent)
            "aifred_top_k": self.aifred_top_k,  # type: ignore[attr-defined, has-type]
            "aifred_top_p": self.aifred_top_p,  # type: ignore[attr-defined, has-type]
            "aifred_min_p": self.aifred_min_p,  # type: ignore[attr-defined, has-type]
            "aifred_repeat_penalty": self.aifred_repeat_penalty,  # type: ignore[attr-defined, has-type]
            "sokrates_top_k": self.sokrates_top_k,  # type: ignore[attr-defined, has-type]
            "sokrates_top_p": self.sokrates_top_p,  # type: ignore[attr-defined, has-type]
            "sokrates_min_p": self.sokrates_min_p,  # type: ignore[attr-defined, has-type]
            "sokrates_repeat_penalty": self.sokrates_repeat_penalty,  # type: ignore[attr-defined, has-type]
            "salomo_top_k": self.salomo_top_k,  # type: ignore[attr-defined, has-type]
            "salomo_top_p": self.salomo_top_p,  # type: ignore[attr-defined, has-type]
            "salomo_min_p": self.salomo_min_p,  # type: ignore[attr-defined, has-type]
            "salomo_repeat_penalty": self.salomo_repeat_penalty,  # type: ignore[attr-defined, has-type]
            "vision_top_k": self.vision_top_k,  # type: ignore[attr-defined, has-type]
            "vision_top_p": self.vision_top_p,  # type: ignore[attr-defined, has-type]
            "vision_min_p": self.vision_min_p,  # type: ignore[attr-defined, has-type]
            "vision_repeat_penalty": self.vision_repeat_penalty,  # type: ignore[attr-defined, has-type]
            "vision_temperature": self.vision_temperature,  # type: ignore[attr-defined, has-type]
            "aifred_speed_mode": self.aifred_speed_mode,  # type: ignore[attr-defined, has-type]
            "sokrates_speed_mode": self.sokrates_speed_mode,  # type: ignore[attr-defined, has-type]
            "salomo_speed_mode": self.salomo_speed_mode,  # type: ignore[attr-defined, has-type]
            "vision_speed_mode": self.vision_speed_mode,  # type: ignore[attr-defined, has-type]
            # TTS/STT Settings
            "enable_tts": self.enable_tts,  # type: ignore[attr-defined, has-type]
            "voice": self.tts_voice,  # type: ignore[attr-defined, has-type]
            # Note: tts_speed removed - generation always at 1.0, tempo via tts_playback_rate
            "tts_engine": self.tts_engine,  # type: ignore[attr-defined, has-type]
            "xtts_force_cpu": self.xtts_force_cpu,  # type: ignore[attr-defined, has-type]
            # tts_autoplay/tts_streaming_enabled: per-engine only (tts_toggles_per_engine)
            "tts_playback_rate": self.tts_playback_rate,  # type: ignore[attr-defined, has-type]
            "tts_pitch": self.tts_pitch,  # type: ignore[attr-defined, has-type]
            "whisper_model": self.whisper_model_key,  # type: ignore[attr-defined, has-type]
            # whisper_device removed - now in config.py
            "show_transcription": self.show_transcription,  # type: ignore[attr-defined, has-type]
            # Language-specific TTS voices (user preferences per engine/language)
            "tts_voices_per_language": existing.get("tts_voices_per_language", {}),
            # Per-engine agent voice settings
            "tts_agent_voices_per_engine": existing.get("tts_agent_voices_per_engine", {}),
            # Per-engine TTS toggles (autoplay, streaming)
            "tts_toggles_per_engine": existing.get("tts_toggles_per_engine", {}),
            # UI Settings
            "auto_scroll": self.auto_refresh_enabled,  # type: ignore[attr-defined, has-type]
            # Message Hub Settings (per-channel toggles + security tiers)
            "channel_toggles": self.channel_toggles,
            "channel_security_tiers": self.channel_security_tiers,
        }
        # Update tts_voices_per_language with current voice selection
        engine_key = self._get_engine_key()  # type: ignore[attr-defined, has-type]
        lang = self.ui_language
        if "tts_voices_per_language" not in settings:
            settings["tts_voices_per_language"] = {}
        if engine_key not in settings["tts_voices_per_language"]:
            settings["tts_voices_per_language"][engine_key] = {}
        settings["tts_voices_per_language"][engine_key][lang] = self.tts_voice  # type: ignore[attr-defined, has-type]

        # Per-engine data (tts_agent_voices_per_engine, tts_toggles_per_engine)
        # is NOT written here — it's managed by dedicated save functions
        # (_save_agent_voices_for_engine, _save_tts_toggles_for_engine)
        # that are called when the user actually changes those settings.
        save_settings(settings)

        # Update mtime tracker to prevent immediate reload by check_for_updates()
        try:
            self._last_settings_mtime = os.path.getmtime(SETTINGS_FILE)
        except OSError:
            pass

    def _reload_settings_from_file(self) -> None:
        """Reload settings from settings.json file.

        Called when API update flag is detected. Updates all UI-visible settings
        to reflect changes made via REST API.
        """
        settings = load_settings()
        if not settings:
            return

        # Core settings
        self.temperature = settings.get("temperature", self.temperature)  # type: ignore[attr-defined, has-type]
        self.temperature_mode = settings.get("temperature_mode", self.temperature_mode)  # type: ignore[attr-defined, has-type]

        # NOTE: research_mode, multi_agent_mode, active_agent, symposion_agents
        # are now per-session config, NOT global settings. They are loaded from
        # the session file in _restore_session().

        # Multi-Agent debate params (still global)
        self.max_debate_rounds = settings.get("max_debate_rounds", self.max_debate_rounds)  # type: ignore[attr-defined, has-type]
        self.consensus_type = settings.get("consensus_type", self.consensus_type)  # type: ignore[attr-defined, has-type]

        # Model IDs - update both ID and display variables
        # AIfred model (top-level "model" field in settings.json)
        if "model" in settings:
            model_id = settings["model"]
            self.aifred_model_id = model_id  # type: ignore[attr-defined, has-type]
            # Update display name if we have the models dict
            if model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.aifred_model = self.available_models_dict[model_id]  # type: ignore[attr-defined, has-type]
            else:
                self.aifred_model = model_id  # type: ignore[attr-defined, has-type]

        # Sokrates model
        if "sokrates_model" in settings:
            model_id = settings["sokrates_model"]
            self.sokrates_model_id = model_id  # type: ignore[attr-defined, has-type]
            if model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.sokrates_model = self.available_models_dict[model_id]  # type: ignore[attr-defined, has-type]
            else:
                self.sokrates_model = model_id  # type: ignore[attr-defined, has-type]

        # Salomo model
        if "salomo_model" in settings:
            model_id = settings["salomo_model"]
            self.salomo_model_id = model_id  # type: ignore[attr-defined, has-type]
            if model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.salomo_model = self.available_models_dict[model_id]  # type: ignore[attr-defined, has-type]
            else:
                self.salomo_model = model_id  # type: ignore[attr-defined, has-type]

        # Automatik model (can be empty = same as AIfred)
        if "automatik_model" in settings:
            model_id = settings["automatik_model"]
            self.automatik_model_id = model_id  # type: ignore[attr-defined, has-type]
            if not model_id:
                self.automatik_model = ""  # type: ignore[attr-defined, has-type]
            elif model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.automatik_model = self.available_models_dict[model_id]  # type: ignore[attr-defined, has-type]
            else:
                self.automatik_model = model_id  # type: ignore[attr-defined, has-type]

        # Vision model
        if "vision_model" in settings:
            model_id = settings["vision_model"]
            self.vision_model_id = model_id  # type: ignore[attr-defined, has-type]
            if model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                self.vision_model = self.available_models_dict[model_id]  # type: ignore[attr-defined, has-type]
            else:
                self.vision_model = model_id  # type: ignore[attr-defined, has-type]

        # RoPE factors
        self.aifred_rope_factor = settings.get("aifred_rope_factor", self.aifred_rope_factor)  # type: ignore[attr-defined, has-type]
        self.sokrates_rope_factor = settings.get("sokrates_rope_factor", self.sokrates_rope_factor)  # type: ignore[attr-defined, has-type]
        self.salomo_rope_factor = settings.get("salomo_rope_factor", self.salomo_rope_factor)  # type: ignore[attr-defined, has-type]
        self.automatik_rope_factor = settings.get("automatik_rope_factor", self.automatik_rope_factor)  # type: ignore[attr-defined, has-type]
        self.vision_rope_factor = settings.get("vision_rope_factor", self.vision_rope_factor)  # type: ignore[attr-defined, has-type]

        # Sampling params (per-agent)
        self.aifred_top_k = settings.get("aifred_top_k", self.aifred_top_k)  # type: ignore[attr-defined, has-type]
        self.aifred_top_p = settings.get("aifred_top_p", self.aifred_top_p)  # type: ignore[attr-defined, has-type]
        self.aifred_min_p = settings.get("aifred_min_p", self.aifred_min_p)  # type: ignore[attr-defined, has-type]
        self.aifred_repeat_penalty = settings.get("aifred_repeat_penalty", self.aifred_repeat_penalty)  # type: ignore[attr-defined, has-type]
        self.sokrates_top_k = settings.get("sokrates_top_k", self.sokrates_top_k)  # type: ignore[attr-defined, has-type]
        self.sokrates_top_p = settings.get("sokrates_top_p", self.sokrates_top_p)  # type: ignore[attr-defined, has-type]
        self.sokrates_min_p = settings.get("sokrates_min_p", self.sokrates_min_p)  # type: ignore[attr-defined, has-type]
        self.sokrates_repeat_penalty = settings.get("sokrates_repeat_penalty", self.sokrates_repeat_penalty)  # type: ignore[attr-defined, has-type]
        self.salomo_top_k = settings.get("salomo_top_k", self.salomo_top_k)  # type: ignore[attr-defined, has-type]
        self.salomo_top_p = settings.get("salomo_top_p", self.salomo_top_p)  # type: ignore[attr-defined, has-type]
        self.salomo_min_p = settings.get("salomo_min_p", self.salomo_min_p)  # type: ignore[attr-defined, has-type]
        self.salomo_repeat_penalty = settings.get("salomo_repeat_penalty", self.salomo_repeat_penalty)  # type: ignore[attr-defined, has-type]
        self.vision_top_k = settings.get("vision_top_k", self.vision_top_k)  # type: ignore[attr-defined, has-type]
        self.vision_top_p = settings.get("vision_top_p", self.vision_top_p)  # type: ignore[attr-defined, has-type]
        self.vision_min_p = settings.get("vision_min_p", self.vision_min_p)  # type: ignore[attr-defined, has-type]
        self.vision_repeat_penalty = settings.get("vision_repeat_penalty", self.vision_repeat_penalty)  # type: ignore[attr-defined, has-type]
        self.vision_temperature = settings.get("vision_temperature", self.vision_temperature)  # type: ignore[attr-defined, has-type]

        # Personality toggles
        self.aifred_personality = settings.get("aifred_personality", self.aifred_personality)  # type: ignore[attr-defined, has-type]
        self.sokrates_personality = settings.get("sokrates_personality", self.sokrates_personality)  # type: ignore[attr-defined, has-type]
        self.salomo_personality = settings.get("salomo_personality", self.salomo_personality)  # type: ignore[attr-defined, has-type]
        self.vision_personality = settings.get("vision_personality", self.vision_personality)  # type: ignore[attr-defined, has-type]
        # Sync to prompt_loader
        from ..lib.prompt_loader import set_personality_enabled
        set_personality_enabled("aifred", self.aifred_personality)  # type: ignore[attr-defined, has-type, arg-type]
        set_personality_enabled("sokrates", self.sokrates_personality)  # type: ignore[attr-defined, has-type, arg-type]
        set_personality_enabled("salomo", self.salomo_personality)  # type: ignore[attr-defined, has-type, arg-type]
        set_personality_enabled("vision", self.vision_personality)  # type: ignore[attr-defined, has-type, arg-type]

        # TTS settings
        self.enable_tts = settings.get("enable_tts", self.enable_tts)  # type: ignore[attr-defined, has-type]
        self.tts_voice = settings.get("voice", self.tts_voice)  # type: ignore[attr-defined, has-type]
        saved_engine = settings.get("tts_engine", self.tts_engine)  # type: ignore[attr-defined, has-type]
        # Migrate old display-string format to key format
        if saved_engine and len(saved_engine) > 10:
            engine_map = {"XTTS": "xtts", "MOSS": "moss", "DashScope": "dashscope",
                          "Piper": "piper", "eSpeak": "espeak", "Edge": "edge"}
            for name, key in engine_map.items():
                if name in saved_engine:
                    saved_engine = key
                    break
        self.tts_engine = saved_engine  # type: ignore[attr-defined, has-type]
        self.xtts_force_cpu = settings.get("xtts_force_cpu", self.xtts_force_cpu)  # type: ignore[attr-defined, has-type]

        # Ensure all registered agents have TTS voice entries
        self.ensure_all_agents_have_tts()  # type: ignore[attr-defined]
        # Restore per-engine agent voices + toggles (single source of truth)
        self._restore_agent_voices_for_engine(self.tts_engine)  # type: ignore[attr-defined, has-type]
        self._restore_tts_toggles_for_engine(self.tts_engine)  # type: ignore[attr-defined, has-type]

        # UI language
        new_ui_lang = settings.get("ui_language", self.ui_language)
        if new_ui_lang != self.ui_language and new_ui_lang in ["de", "en"]:
            self.ui_language = new_ui_lang
            from ..lib.formatting import set_ui_locale
            set_ui_locale(new_ui_lang)
            set_language(new_ui_lang)  # Sync prompt language

        # User name
        self.user_name = settings.get("user_name", self.user_name)
        from ..lib.prompt_loader import set_user_name
        set_user_name(self.user_name)

        # Message Hub settings (per-channel toggles + security tiers)
        self.channel_toggles = settings.get("channel_toggles", {})
        self.channel_security_tiers = settings.get("channel_security_tiers", {})

    # ================================================================
    # UI LANGUAGE
    # ================================================================

    def set_ui_language(self, lang: str) -> None:
        """Set UI language and switch TTS voice to matching language."""
        if lang in ["de", "en"]:
            self.ui_language = lang
            # Update global locale for number formatting
            from ..lib.formatting import set_ui_locale
            set_ui_locale(lang)
            # Update prompt language for LLM responses
            set_language(lang)
            # Update research_mode_display to match new language
            self.research_mode_display = TranslationManager.get_research_mode_display(  # type: ignore[attr-defined, has-type]
                self.research_mode, lang  # type: ignore[attr-defined, has-type, arg-type]
            )
            self.add_debug(f"\U0001f310 UI Language changed to: {lang}")  # type: ignore[attr-defined, has-type]

            # Auto-switch TTS voice to matching language
            self._switch_tts_voice_for_language(lang)  # type: ignore[attr-defined, has-type]

            # Save to settings
            self._save_settings()
        else:
            self.add_debug(f"\u274c Invalid language: {lang}. Use 'de' or 'en'")  # type: ignore[attr-defined, has-type]

    # ================================================================
    # USER PROFILE
    # ================================================================

    def set_user_name(self, name: str) -> None:
        """Set user name (called on every keystroke)."""
        self.user_name = name

    def save_user_name(self, name: str) -> None:
        """Save user name when input loses focus."""
        self.user_name = name.strip()
        # Sync to prompt_loader for automatic injection into system prompts
        from ..lib.prompt_loader import set_user_name
        set_user_name(self.user_name)
        if self.user_name:
            self.add_debug(f"\U0001f464 User name: {self.user_name}")  # type: ignore[attr-defined, has-type]
        self._save_settings()

    def set_user_gender(self, gender: str | list[str]) -> None:
        """Set user gender for salutation (male/female)."""
        # Reflex segmented_control can return str or list[str]
        if isinstance(gender, list):
            gender = gender[0] if gender else "male"
        self.user_gender = gender
        from ..lib.prompt_loader import set_user_gender
        set_user_gender(gender)
        self.add_debug(f"\U0001f464 Gender: {'\u2642 male' if gender == 'male' else '\u2640 female'}")  # type: ignore[attr-defined, has-type]
        self._save_settings()

    # ================================================================
    # RESET TO DEFAULTS
    # ================================================================

    async def load_default_settings(self):
        """Load default settings from config.py and apply them to state."""
        from ..lib.settings import reset_to_defaults

        self.add_debug("\U0001f4be Loading default settings from config.py...")  # type: ignore[attr-defined, has-type]
        yield  # Update UI immediately

        if reset_to_defaults():
            self.add_debug("\u2705 Default settings saved to file")  # type: ignore[attr-defined, has-type]
            yield

            # Reload settings from file (all values MUST be present after reset_to_defaults())
            saved_settings = load_settings()
            if saved_settings:
                # Update state with loaded settings (only attributes that exist in state)
                # No fallbacks needed - reset_to_defaults() ensures all values are present
                self.backend_type = saved_settings["backend_type"]  # type: ignore[attr-defined, has-type]
                self.backend_id = self.backend_type  # type: ignore[attr-defined, has-type]
                self.current_backend_label = self.available_backends_dict.get(  # type: ignore[attr-defined, has-type]
                    self.backend_id, self.backend_id  # type: ignore[attr-defined, has-type]
                )

                # NOTE: research_mode, multi_agent_mode are now per-session.
                # Reset to clean defaults (matches DEFAULT_SESSION_CONFIG).
                from ..lib.session_storage import DEFAULT_SESSION_CONFIG
                self.research_mode = DEFAULT_SESSION_CONFIG["research_mode"]  # type: ignore[attr-defined, has-type]
                self.multi_agent_mode = DEFAULT_SESSION_CONFIG["multi_agent_mode"]  # type: ignore[attr-defined, has-type]
                self.active_agent = DEFAULT_SESSION_CONFIG["active_agent"]  # type: ignore[attr-defined, has-type]
                self.symposion_agents = list(DEFAULT_SESSION_CONFIG["symposion_agents"])  # type: ignore[attr-defined, has-type]

                # Update research_mode_display to match reset research_mode
                self.research_mode_display = TranslationManager.get_research_mode_display(  # type: ignore[attr-defined, has-type]
                    self.research_mode, self.ui_language  # type: ignore[attr-defined, has-type]
                )

                self.temperature = saved_settings["temperature"]  # type: ignore[attr-defined, has-type]
                self.temperature_mode = saved_settings["temperature_mode"]  # type: ignore[attr-defined, has-type]
                self.enable_tts = saved_settings["enable_tts"]  # type: ignore[attr-defined, has-type]
                self.enable_yarn = saved_settings["enable_yarn"]  # type: ignore[attr-defined, has-type]
                self.yarn_factor = saved_settings["yarn_factor"]  # type: ignore[attr-defined, has-type]

                # IMPORTANT: Set model names from defaults (prevents fallback to available_models[0])
                # The "model" and "automatik_model" keys come from get_default_settings()
                self.aifred_model = saved_settings.get("model", self.aifred_model)  # type: ignore[attr-defined, has-type]
                self.automatik_model = saved_settings.get("automatik_model", self.automatik_model)  # type: ignore[attr-defined, has-type]

                self.add_debug("\U0001f504 Settings reloaded from file")  # type: ignore[attr-defined, has-type]
                yield

                # Reinitialize backend with new settings
                await self.initialize_backend()  # type: ignore[attr-defined, has-type]
                self.add_debug("\u2705 All settings applied successfully")  # type: ignore[attr-defined, has-type]
                yield
            else:
                self.add_debug("\u26a0\ufe0f Failed to reload settings from file")  # type: ignore[attr-defined, has-type]
                yield
        else:
            self.add_debug("\u274c Failed to load default settings")  # type: ignore[attr-defined, has-type]
            yield  # Update UI even on error

    # ================================================================
    # MESSAGE HUB — GENERIC CHANNEL TOGGLES
    # ================================================================

    def set_channel_security_tier(self, data: list) -> None:
        """Set security tier for a channel. Called from UI with [channel_name, tier_label].

        tier_label is either "1" or "1 — Communicate" format.
        """
        channel, tier_label = data[0], data[1]
        # Extract integer from "T1 — Communicate", "1 — Communicate", or plain "1"
        prefix = tier_label.split(" ")[0]  # "T1" or "1"
        tier_value = int(prefix.lstrip("T"))
        tiers = dict(self.channel_security_tiers)
        tiers[channel] = tier_value
        self.channel_security_tiers = tiers
        self._save_settings()

    def _get_channel_toggle(self, channel: str, key: str) -> bool:
        """Read a toggle value for a channel."""
        return self.channel_toggles.get(channel, {}).get(key, False)

    def _set_channel_toggle(self, channel: str, key: str, value: bool) -> None:
        """Set a toggle value for a channel and persist."""
        toggles = dict(self.channel_toggles)
        if channel not in toggles:
            toggles[channel] = {}
        ch = dict(toggles[channel])
        ch[key] = value
        toggles[channel] = ch
        self.channel_toggles = toggles

    def toggle_channel_monitor(self, data: list) -> None:
        """Toggle channel plugin on/off. Called from UI with [channel_name, value].

        For always_reply channels (Discord): also starts/stops the listener.
        For other channels (Email): only enables/disables the plugin.
        The listener is controlled separately via toggle_channel_listener.
        """
        channel_name: str = data[0]
        value: bool = data[1]

        from ..lib.plugin_registry import get_channel
        plugin = get_channel(channel_name)

        if value and plugin and not plugin.is_configured():
            self.open_channel_credentials(channel_name)
            return

        self._set_channel_toggle(channel_name, "monitor", value)
        display = plugin.display_name if plugin else channel_name
        status = "enabled" if value else "disabled"
        self.add_debug(f"📨 {display} {status}")  # type: ignore[attr-defined, has-type]
        self._save_settings()

        # For always_reply channels: toggle also controls the listener
        if plugin and plugin.always_reply:
            from ..lib.message_hub import message_hub
            if value:
                if not message_hub.is_running(channel_name):
                    message_hub.register(channel_name, plugin.listener_loop)
                    import asyncio
                    asyncio.create_task(message_hub.start_all())
            else:
                message_hub.unregister(channel_name)

    def toggle_channel_listener(self, data: list) -> None:
        """Toggle background listener for a channel. Called from UI with [channel_name, value]."""
        channel_name: str = data[0]
        value: bool = data[1]

        from ..lib.plugin_registry import get_channel
        plugin = get_channel(channel_name)

        self._set_channel_toggle(channel_name, "listener", value)
        display = plugin.display_name if plugin else channel_name
        status = "enabled" if value else "disabled"
        self.add_debug(f"📨 {display} Monitor {status}")  # type: ignore[attr-defined, has-type]
        self._save_settings()

        from ..lib.message_hub import message_hub
        if value and plugin:
            if not message_hub.is_running(channel_name):
                message_hub.register(channel_name, plugin.listener_loop)
                import asyncio
                asyncio.create_task(message_hub.start_all())
        else:
            message_hub.unregister(channel_name)

    def toggle_channel_auto_reply(self, data: list) -> None:
        """Toggle auto-reply for a channel. Called from UI with [channel_name, value]."""
        channel_name: str = data[0]
        value: bool = data[1]

        self._set_channel_toggle(channel_name, "auto_reply", value)
        display = channel_name.capitalize()
        status = "enabled" if value else "disabled"
        self.add_debug(f"📨 {display} Auto-Reply {status}")  # type: ignore[attr-defined, has-type]
        self._save_settings()

    # ================================================================
    # MESSAGE HUB — GENERIC CREDENTIALS MODAL
    # ================================================================

    def open_channel_credentials(self, channel_name: str) -> None:
        """Open credentials modal, pre-filled from .env (secrets) and settings.json (config)."""
        from ..lib.plugin_base import CredentialField
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        # Try channel first, then tool plugin
        fields: list[CredentialField] = []
        plugin = get_channel(channel_name)
        if plugin:
            fields = plugin.credential_fields
        else:
            tool = get_tool_plugin(channel_name)
            if tool:
                fields = getattr(tool, "credential_fields", [])

        if not fields:
            return

        # Load plugin settings.json for non-secret fields
        plugin_settings: dict[str, str] = {}
        if plugin:
            plugin_settings = plugin.load_settings()

        # Pre-fill values: secrets from os.environ, config from settings.json
        lang = self.ui_language  # type: ignore[attr-defined]
        values: dict[str, str] = {}
        field_descriptors: list[dict[str, str]] = []

        # Translate labels: try plugin i18n first, then central i18n
        from ..lib.i18n import t as _t

        for field in fields:
            if field.is_secret:
                raw_value = os.environ.get(field.env_key, field.default)
            else:
                raw_value = plugin_settings.get(field.env_key, os.environ.get(field.env_key, field.default))

            # Map stored value to display label for dropdown fields
            if field.options:
                value_to_label = {val: lbl for val, lbl in field.options}
                values[field.env_key] = value_to_label.get(raw_value, raw_value)
            else:
                values[field.env_key] = raw_value

            # Label translation: plugin i18n → central i18n
            label = ""
            if plugin:
                label = plugin.translate(field.label_key, lang=lang)
            if not label or label == field.label_key:
                label = _t(field.label_key, lang=lang)

            field_descriptors.append({
                "env_key": field.env_key,
                "label_key": label,
                "placeholder": field.placeholder,
                "is_password": "1" if field.is_password else "",
                "group": field.group,
                "width_ratio": str(field.width_ratio),
                "options": ",".join(val for val, _ in field.options) if field.options else "",
                "option_labels": ",".join(lbl for _, lbl in field.options) if field.options else "",
            })

        display = plugin.display_name if plugin else channel_name.capitalize()
        suffix = _t("cred_title_suffix", lang=lang)
        self.channel_credentials_editing = channel_name
        self.channel_credentials_display_name = f"{display} — {suffix}"
        self.channel_credential_values = values
        self.channel_credential_fields = field_descriptors
        self.channel_cred_show_password = False
        self.channel_credentials_modal_open = True

    def close_channel_credentials(self) -> None:
        """Close credentials modal without saving."""
        self.channel_credentials_modal_open = False
        # Clear password values from state
        self.channel_credential_values = {}
        self.channel_credentials_editing = ""

    def update_channel_credential(self, data: list) -> None:
        """Update a single credential field. Called with [env_key, value]."""
        env_key: str = data[0]
        value: str = data[1]
        values = dict(self.channel_credential_values)
        values[env_key] = value
        self.channel_credential_values = values

    def toggle_channel_cred_show_password(self) -> None:
        """Toggle password visibility in credentials modal."""
        self.channel_cred_show_password = not self.channel_cred_show_password

    def save_channel_credentials(self) -> None:
        """Write credentials to .env (secrets) and plugin settings.json (config).

        Works for both channel plugins and tool plugins.
        Secrets (is_secret=True) → .env + os.environ
        Config  (is_secret=False) → plugin's settings.json
        """
        from dotenv import set_key
        from ..lib.config import PROJECT_ROOT
        from ..lib.plugin_base import CredentialField
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        plugin_name = self.channel_credentials_editing
        env_path = str(PROJECT_ROOT / ".env")

        # Determine if this is a channel or tool plugin
        channel = get_channel(plugin_name)
        tool = get_tool_plugin(plugin_name) if not channel else None

        fields: list[CredentialField] = []
        display = plugin_name
        if channel:
            fields = channel.credential_fields
            display = channel.display_name
        elif tool:
            fields = getattr(tool, "credential_fields", [])
            display = tool.display_name

        if not fields:
            return

        # Separate secrets from config settings
        plugin_settings: dict[str, str] = {}
        if channel:
            plugin_settings = channel.load_settings()

        for field in fields:
            val = self.channel_credential_values.get(field.env_key, "")
            # Map display label back to stored value for dropdown fields
            if field.options:
                label_to_value = {lbl: v for v, lbl in field.options}
                val = label_to_value.get(val, val)

            if field.is_secret:
                # Secrets → .env + os.environ
                if val or not field.is_password:  # Don't overwrite password with empty
                    set_key(env_path, field.env_key, val)
                    os.environ[field.env_key] = val
            else:
                # Config → plugin's settings.json
                plugin_settings[field.env_key] = val
                # Also set in os.environ for runtime access
                os.environ[field.env_key] = val

        # Write plugin settings.json (non-secrets)
        if channel and plugin_settings:
            channel.save_settings(plugin_settings)

        self.add_debug(f"🔧 {display} Einstellungen gespeichert")  # type: ignore[attr-defined, has-type]

        # Prepare values for apply_credentials (all fields, regardless of storage)
        saved_values = {}
        for field in fields:
            val = self.channel_credential_values.get(field.env_key, "")
            if field.options:
                label_to_value = {lbl: v for v, lbl in field.options}
                val = label_to_value.get(val, val)
            saved_values[field.env_key] = val

        # Close modal
        self.channel_credentials_modal_open = False
        self.channel_credential_values = {}
        self.channel_credentials_editing = ""

        if tool:
            # Tool plugin: update toggle to reflect new availability
            toggles = dict(self.tool_plugin_toggles)
            toggles[plugin_name] = "1" if tool.is_available() else ""
            self.tool_plugin_toggles = toggles

        if channel:
            # Channel-specific: apply credentials, enable monitor, start worker
            channel.apply_credentials(saved_values)

            enabled_key = f"{plugin_name.upper()}_ENABLED"
            set_key(env_path, enabled_key, "true")
            os.environ[enabled_key] = "true"

            self._set_channel_toggle(plugin_name, "monitor", True)
            self._save_settings()

            from ..lib.message_hub import message_hub
            if not message_hub.is_running(plugin_name):
                message_hub.register(plugin_name, channel.listener_loop)
                import asyncio
                asyncio.create_task(message_hub.start_all())

    # ================================================================
    # PLUGIN MANAGER MODAL
    # ================================================================

    def open_plugin_manager(self) -> None:
        """Open plugin manager modal and refresh plugin lists + allowlists."""
        from ..lib.plugin_registry import discover_tools
        from ..lib.credential_broker import broker
        # Use plugin.name (not file stem) — must match the static UI rows
        # Show as ON only if the plugin is actually available (credentials etc.)
        self.tool_plugin_toggles = {
            p.name: ("1" if p.is_available() else "") for p in discover_tools()
        }
        # Load current allowlists for display
        self.channel_allowlists = {
            "email": broker.get("email", "allowed_senders") or "-",
            "telegram": broker.get("telegram", "allowed_users") or "-",
            "discord": broker.get("discord", "channel_ids") or "-",
            "freeecho2": "",
        }
        # Ensure all channels have a tier entry (fill defaults for missing)
        from ..lib.security import DEFAULT_TIER_BY_SOURCE, TIER_COMMUNICATE
        from ..lib.plugin_registry import all_channels
        tiers = dict(self.channel_security_tiers)
        for ch_name in all_channels():
            if ch_name not in tiers:
                tiers[ch_name] = DEFAULT_TIER_BY_SOURCE.get(ch_name, TIER_COMMUNICATE)
        self.channel_security_tiers = tiers
        self.plugin_manager_open = True

    def close_plugin_manager(self) -> None:
        """Apply pending tool plugin changes and close modal.

        Channel toggles apply immediately (running workers).
        Tool toggles apply on OK (file movement, batch).
        Currently only handles disabling (enabled plugins are discovered at build-time).
        """
        from ..lib.plugin_registry import discover_tools, disable_plugin

        # All discovered plugins are currently enabled on the filesystem
        discovered = {p.name for p in discover_tools()}

        for name, ui_enabled_str in self.tool_plugin_toggles.items():
            ui_enabled = bool(ui_enabled_str)
            fs_enabled = name in discovered

            if not ui_enabled and fs_enabled:
                disable_plugin(name, "tool")
                self.add_debug(f"🔌 {name} deaktiviert")  # type: ignore[attr-defined, has-type]

        self.plugin_manager_open = False

    def toggle_tool_plugin(self, plugin_name: str) -> None:
        """Toggle a tool plugin in UI state (applied on OK)."""
        toggles = dict(self.tool_plugin_toggles)
        current = toggles.get(plugin_name, "1")
        toggles[plugin_name] = "" if current else "1"
        self.tool_plugin_toggles = toggles

    # ================================================================
    # AUDIT LOG MODAL
    # ================================================================

    def open_audit_log(self) -> None:
        """Load recent audit log entries and open modal."""
        import sqlite3
        from ..lib.config import SECURITY_AUDIT_DB

        entries: list[dict[str, str]] = []
        db_path = SECURITY_AUDIT_DB
        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=5)
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
                    "args": (r["tool_args_preview"] or "")[:100],
                })

        self.audit_log_entries = entries
        self.audit_log_open = True

    def close_audit_log(self) -> None:
        self.audit_log_open = False

    # ================================================================
    # TRANSLATION HELPER
    # ================================================================

    def get_text(self, key: str) -> str:
        """Get translated text based on current UI language."""
        return TranslationManager.get_text(key, self.ui_language)
