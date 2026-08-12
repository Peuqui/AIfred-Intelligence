"""Settings mixin for AIfred state.

Handles saving/loading settings.json, user profile, UI language,
and reset-to-defaults.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import reflex as rx
from reflex.event import EventSpec

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

    # OAuth-Verbindung im Credentials-Modal: idle | connecting | connected | error
    oauth_connect_status: str = "idle"
    # Provider-Name des aktuell im Modal bearbeiteten OAuth-Plugins (leer wenn nicht OAuth)
    oauth_connect_provider: str = ""
    # Vorab generierte Auth-URL — wird vom Connect-Link (target="_blank") direkt genutzt,
    # damit der Browser-Pop-up-Blocker den Login-Tab nicht abblockt (echter User-Click).
    oauth_auth_url: str = ""

    # ── Plugin Manager (in the Agent-Editor "plugins" tab) ──────────
    tool_plugin_toggles: dict[str, str] = {}  # {"epim": "1", "calculator": "1", ...}
    channel_allowlists: dict[str, str] = {}  # {"email": "user@mail.de, @family.de", "telegram": "123456"}

    # ── Audit Log Modal ──────────────────────────────────────────
    audit_log_open: bool = False
    audit_log_entries: list[dict[str, str]] = []  # [{timestamp, source, tool_name, ...}]

    # ── Settings File Tracking ────────────────────────────────────
    _last_settings_mtime: float = 0.0  # Last seen settings.json mtime (for multi-browser sync)
    _last_session_mtime: float = 0.0  # Last seen session file mtime (for multi-tab/cross-channel sync)
    _last_pushed_debug_len: int = -1   # Last debug_messages length pushed to browser (for no-op tick suppression)

    # ================================================================
    # SETTINGS PERSISTENCE
    # ================================================================

    def _save_settings(self) -> None:
        """Save current settings to file (per-backend models)."""
        from ..lib.agent_settings import get_agent_setting
        existing = load_settings() or {}
        backend_models = existing.get("backend_models", {})

        # Only update backend models if model IDs are validated against current backend.
        # Prevents saving stale IDs from a different backend during transitions
        # (e.g., backend_id already switched but model_ids not yet validated).
        aifred_model_id: str = get_agent_setting(self, "aifred", "model_id")
        if aifred_model_id and self.backend_id and self.available_models_dict:  # type: ignore[attr-defined, has-type]
            # tuning buckets always contain base IDs (SSOT — speed suffix is
            # computed). Keys are agent ids ("" = inherits AIfred); the
            # Automatik (not an agent) keeps its own entry.
            if aifred_model_id in self.available_models_dict:  # type: ignore[attr-defined, has-type]
                models_out = {
                    agent: tuning.model_id
                    for agent, tuning in self.agent_tuning.items()  # type: ignore[attr-defined]
                }
                models_out["automatik"] = self.automatik_model_id  # type: ignore[attr-defined, has-type]
                backend_models[self.backend_id] = models_out  # type: ignore[attr-defined, has-type]

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
            # Calibration mode (legacy/ai) — MUST be re-emitted here, else any
            # unrelated settings save would drop the key and silently revert
            # the AI calibration toggle to legacy on the next run.
            "calibration_mode": self.calibration_mode or "legacy",  # type: ignore[attr-defined, has-type]
            # NOTE: research_mode, multi_agent_mode are per-session now (session_storage.DEFAULT_SESSION_CONFIG)
            # NOTE: per-agent tuning (temperature, sampling, thinking,
            # personality, speed) is appended below as "agent_tuning"
            "temperature_mode": self.temperature_mode,  # type: ignore[attr-defined, has-type]
            "ui_language": self.ui_language,  # UI language (de/en)
            "user_name": self.user_name,  # User's name for personalized responses
            "user_gender": self.user_gender,  # Gender for salutation (male/female)
            "backend_models": backend_models,  # Merged: preserves all backends
            # Multi-Agent debate params (still global)
            "max_debate_rounds": self.max_debate_rounds,  # type: ignore[attr-defined, has-type]
            "consensus_type": self.consensus_type,  # type: ignore[attr-defined, has-type]
            # NOTE: Modell-Felder (auch sokrates/salomo) leben ausschließlich
            # in backend_models — eine zweite flache Wahrheit gab es bis
            # 2026-07-17 und sie hat den Message-Hub-Pfad verfehlt.
            # vLLM YaRN Settings (only enable/disable, factor is calculated dynamically)
            "enable_yarn": self.enable_yarn,  # type: ignore[attr-defined, has-type]
            # NOTE: yarn_factor is NOT saved - always starts at 1.0, system calibrates maximum
            # NOTE: vllm_max_tokens and vllm_native_context are NEVER saved!
            # They are calculated dynamically on every vLLM startup based on VRAM
            # TTS/STT Settings
            "enable_tts": self.enable_tts,  # type: ignore[attr-defined, has-type]
            "voice": self.tts_voice,  # type: ignore[attr-defined, has-type]
            # Note: tts_speed removed - generation always at 1.0, tempo via tts_playback_rate
            "tts_engine": self.tts_engine,  # type: ignore[attr-defined, has-type]
            "narrator_engine": self.narrator_engine,  # type: ignore[attr-defined, has-type]
            "narrator_fallback_engine": self.narrator_fallback_engine,  # type: ignore[attr-defined, has-type]
            "narrator_voices": self.narrator_voices,  # type: ignore[attr-defined, has-type]
            "xtts_force_cpu": self.xtts_force_cpu,  # type: ignore[attr-defined, has-type]
            # tts_autoplay/tts_streaming_enabled: per-engine only (tts_toggles_per_engine)
            "tts_playback_rate": self.tts_playback_rate,  # type: ignore[attr-defined, has-type]
            "tts_pitch": self.tts_pitch,  # type: ignore[attr-defined, has-type]
            "whisper_model": self.whisper_model_key,  # type: ignore[attr-defined, has-type]
            "show_transcription": self.show_transcription,  # type: ignore[attr-defined, has-type]
            "enter_sends_message": self.enter_sends_message,  # type: ignore[attr-defined, has-type]
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

        # Per-agent tuning — one dict per agent bucket. Field list is SSOT in
        # agent_settings (PERSISTED_TUNING_FIELDS). Asymmetry:
        # num_ctx_manual(+_enabled) persists only for vision (chat agents
        # reset on restart — deliberate, see llm_params UI hint).
        from ..lib.agent_settings import PERSISTED_TUNING_FIELDS
        agent_tuning_out: Dict[str, Dict[str, Any]] = {}
        for agent, tuning in self.agent_tuning.items():  # type: ignore[attr-defined]
            entry = {field: getattr(tuning, field) for field in PERSISTED_TUNING_FIELDS}
            if agent == "vision":
                entry["num_ctx_manual"] = tuning.num_ctx_manual
                entry["num_ctx_manual_enabled"] = tuning.num_ctx_manual_enabled
            agent_tuning_out[agent] = entry
        settings["agent_tuning"] = agent_tuning_out
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
        self.temperature_mode = settings.get("temperature_mode", self.temperature_mode)  # type: ignore[attr-defined, has-type]

        # NOTE: research_mode, multi_agent_mode, active_agent, symposion_agents
        # are now per-session config, NOT global settings. They are loaded from
        # the session file in _restore_session().

        # Multi-Agent debate params (still global)
        self.max_debate_rounds = settings.get("max_debate_rounds", self.max_debate_rounds)  # type: ignore[attr-defined, has-type]
        self.consensus_type = settings.get("consensus_type", self.consensus_type)  # type: ignore[attr-defined, has-type]

        # Calibration mode (legacy / ai-qwen-*) + hybrid permission
        self.calibration_mode = settings.get("calibration_mode", "legacy")  # type: ignore[attr-defined]
        self.calibration_allow_hybrid = settings.get("calibration_allow_hybrid", False)  # type: ignore[attr-defined]

        # NOTE: model ids live exclusively in backend_models (loaded at
        # backend init) — no flat model keys in settings.json anymore.
        from ..lib.agent_settings import get_agent_setting, set_agent_setting

        # RoPE factors (agents: in the tuning dict; automatik: own field)
        self.automatik_rope_factor = settings.get("automatik_rope_factor", self.automatik_rope_factor)  # type: ignore[attr-defined, has-type]

        # Sampling params + personality (per-agent). Deliberately only this
        # subset of PERSISTED_TUNING_FIELDS — reload serves API-driven
        # changes; thinking/reasoning/speed stay untouched here
        # (same behavior as before the loop-ification).
        from ..lib.prompt_loader import set_personality_enabled
        saved_tuning = settings.get("agent_tuning", {})
        for agent, entry in saved_tuning.items():
            if agent not in self.agent_tuning:  # type: ignore[attr-defined]
                continue
            for field in ("temperature", "top_k", "top_p", "min_p", "repeat_penalty", "rope_factor"):
                if field in entry:
                    set_agent_setting(self, agent, field, entry[field])

            # Personality toggles (+ prompt_loader sync)
            if "personality" in entry:
                set_agent_setting(self, agent, "personality", entry["personality"])
            set_personality_enabled(agent, get_agent_setting(self, agent, "personality"))

        # TTS settings
        self.enable_tts = settings.get("enable_tts", self.enable_tts)  # type: ignore[attr-defined, has-type]
        self.tts_voice = settings.get("voice", self.tts_voice)  # type: ignore[attr-defined, has-type]
        self.tts_engine = settings.get("tts_engine", self.tts_engine)  # type: ignore[attr-defined, has-type]
        self.narrator_engine = settings.get("narrator_engine", self.narrator_engine)  # type: ignore[attr-defined, has-type]
        self.narrator_fallback_engine = settings.get("narrator_fallback_engine", self.narrator_fallback_engine)  # type: ignore[attr-defined, has-type]
        self.narrator_voices = settings.get("narrator_voices", self.narrator_voices)  # type: ignore[attr-defined, has-type]
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

                self.temperature_mode = saved_settings["temperature_mode"]  # type: ignore[attr-defined, has-type]
                self.enable_tts = saved_settings["enable_tts"]  # type: ignore[attr-defined, has-type]
                self.enable_yarn = saved_settings["enable_yarn"]  # type: ignore[attr-defined, has-type]
                self.yarn_factor = saved_settings["yarn_factor"]  # type: ignore[attr-defined, has-type]

                # IMPORTANT: Set model names from defaults (prevents fallback to available_models[0])
                # The "model" and "automatik_model" keys come from get_default_settings()
                self.agent_tuning["aifred"].model = saved_settings.get("model", self.agent_tuning["aifred"].model)  # type: ignore[attr-defined, has-type]
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

    def _set_channel_toggle(self, channel: str, key: str, value: bool) -> None:
        """Set a toggle value for a channel and persist."""
        toggles = dict(self.channel_toggles)
        if channel not in toggles:
            toggles[channel] = {}
        ch = dict(toggles[channel])
        ch[key] = value
        toggles[channel] = ch
        self.channel_toggles = toggles

    def toggle_channel_monitor(self, data: list) -> EventSpec | list | None:
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
            # open_channel_credentials returnt rx.redirect — durchreichen
            # damit Reflex die Navigation tatsaechlich ausloest
            return self.open_channel_credentials(channel_name)

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
        return None

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

    # Pfad zu dem nach Schliessen/Save der Credentials-Page zurueck-
    # navigiert wird. Beim Open setzen wir das aus self.router.page.path
    # — der User landet wieder dort wo er das Modal aufgerufen hat
    # (Settings-Accordion auf /, oder Plugin-Tab auf /agent-editor).
    _credentials_return_to: str = "/"

    def open_channel_credentials(self, channel_name: str) -> EventSpec | list | None:
        """Open credentials page, pre-filled from .env (secrets) and settings.json (config)."""
        from ..lib.plugin_base import CredentialField
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        # Try channel first, then tool plugin
        fields: list[CredentialField] = []
        tool = None
        plugin = get_channel(channel_name)
        if plugin:
            fields = plugin.credential_fields
        else:
            tool = get_tool_plugin(channel_name)
            if tool:
                fields = getattr(tool, "credential_fields", [])

        if not fields:
            return None

        # Load plugin settings.json for non-secret fields — channels via
        # load_settings(), tool plugins via the private _load_settings
        # convention (mirrors save_channel_credentials).
        plugin_settings: dict[str, str] = {}
        if plugin:
            plugin_settings = plugin.load_settings()
        elif tool:
            tool_loader = getattr(tool, "_load_settings", None)
            if callable(tool_loader):
                try:
                    plugin_settings = dict(tool_loader())
                except Exception:
                    plugin_settings = {}

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

            # Tooltip (optional, Konvention: <label_key>_tooltip) — MUSS hier
            # serverseitig mit dem ROHEN label_key aufgelöst werden: die UI
            # kennt nur das übersetzte Label und kann weder Plugin-i18n noch
            # den Original-Key rekonstruieren. Kein Treffer → "" = kein Tooltip.
            tooltip_key = f"{field.label_key}_tooltip"
            tooltip = ""
            if plugin:
                tooltip = plugin.translate(tooltip_key, lang=lang)
                if tooltip == tooltip_key:
                    tooltip = ""
            if not tooltip:
                tooltip = _t(tooltip_key, lang=lang)
                if tooltip == tooltip_key:
                    tooltip = ""

            field_descriptors.append({
                "env_key": field.env_key,
                "label_key": label,
                "tooltip": tooltip,
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

        # OAuth-Status für den Connect-Button im Modal initialisieren
        oauth_provider = ""
        if plugin is not None:
            oauth_provider = getattr(plugin, "oauth_provider", None) or ""
        else:
            tool = get_tool_plugin(channel_name)
            if tool is not None:
                oauth_provider = getattr(tool, "oauth_provider", None) or ""
        self.oauth_connect_provider = oauth_provider
        connected = False
        if oauth_provider:
            from ..lib.oauth import oauth_broker
            # Optimistisch aus der Token-Datei — die Echt-Prüfung (Refresh-
            # Roundtrip) läuft als nachgekettetes Event, damit ein Provider-
            # Timeout das Modal-Öffnen nie blockiert.
            connected = oauth_broker.is_connected(oauth_provider)
            self.oauth_connect_status = "connected" if connected else "idle"
            # Auth-URL vorab generieren — der Modal-Connect-Link nutzt sie
            # direkt als <a target="_blank"> (umgeht den Pop-up-Blocker, der
            # window.open() aus async Reflex-Events stillschweigend killt).
            target_plugin = plugin if plugin is not None else get_tool_plugin(channel_name)
            self.oauth_auth_url = (
                "" if connected else self._build_oauth_auth_url(oauth_provider, target_plugin)
            )
        else:
            self.oauth_connect_status = "idle"
            self.oauth_auth_url = ""

        # Remember the page the user came from for the close/save redirect
        # (Settings-Accordion auf /, oder Plugin-Tab auf /agent-editor).
        try:
            current_path = self.router.page.path or "/"
        except Exception:  # noqa: BLE001
            current_path = "/"
        self._credentials_return_to = current_path
        if connected:
            return [rx.redirect("/credentials"), type(self).verify_oauth_connection]
        return rx.redirect("/credentials")

    def close_channel_credentials(self):
        """Close credentials page without saving — back to caller's page."""
        self.channel_credentials_modal_open = False
        # Clear password values from state
        self.channel_credential_values = {}
        self.channel_credentials_editing = ""
        target = self._credentials_return_to or "/"
        return rx.redirect(target)

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

    def _build_oauth_auth_url(self, provider: str, plugin: Any) -> str:
        """Generate the OAuth Auth-URL for the given provider+plugin.

        Returns "" if credentials are missing or URL generation fails.
        Called at modal-open so the Connect-Link can use a real ``<a target="_blank">``
        — that survives the browser pop-up blocker which kills ``window.open()``
        from async event handlers.
        """
        from ..lib.credential_broker import broker
        from ..lib.oauth import oauth_broker

        if not provider or not broker.get(provider, "client_id"):
            return ""

        scopes_method = getattr(plugin, "aggregated_scopes", None)
        scope_list: list[str] = []
        if callable(scopes_method):
            try:
                scope_list = list(scopes_method())
            except Exception:
                scope_list = []

        try:
            page_host = self.router.page.host  # type: ignore[attr-defined]
        except Exception:
            page_host = ""
        if page_host:
            redirect_uri = f"{page_host.rstrip('/')}/api/oauth/{provider}/callback"
        else:
            redirect_uri = f"http://localhost:8002/api/oauth/{provider}/callback"

        try:
            return str(oauth_broker.get_auth_url(provider, scope_list, redirect_uri))
        except Exception:
            return ""

    async def verify_oauth_connection(self) -> None:
        """Echt-Prüfung des angezeigten „Verbunden"-Status (Refresh-Roundtrip).

        Läuft nachgekettet an das Modal-Open: der Status wurde dort
        optimistisch aus der Token-Datei gesetzt. Hier erzwingt der Broker
        einen Token-Refresh — ein beim Provider widerrufener Zugriff
        (invalid_grant) stuft auf ``idle`` zurück und generiert die
        Auth-URL, sodass der User direkt neu verbinden kann. Ist der
        Provider nicht erreichbar, bleibt der Status stehen — „konnte
        nicht prüfen" ist nicht „getrennt".
        """
        import httpx
        from ..lib.oauth import oauth_broker
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        provider = self.oauth_connect_provider
        if not provider or self.oauth_connect_status != "connected":
            return
        try:
            valid = await oauth_broker.verify_connection(provider)
        except httpx.HTTPError as exc:
            self.add_debug(  # type: ignore[attr-defined]
                f"⚠️ OAuth verify skipped for {provider} (provider unreachable): {exc}"
            )
            return
        if valid:
            return
        self.oauth_connect_status = "idle"
        plugin_key = self.channel_credentials_editing
        plugin = get_channel(plugin_key) or get_tool_plugin(plugin_key)
        self.oauth_auth_url = self._build_oauth_auth_url(provider, plugin)
        self.add_debug(  # type: ignore[attr-defined]
            f"⚠️ OAuth grant for {provider} was revoked at the provider — reconnect required"
        )

    async def start_oauth_connection(self):  # type: ignore[no-untyped-def]
        """Set status to ``connecting`` and poll ``is_connected`` for up to 5 min.

        The actual login tab is opened by the Connect-Link in the modal
        (``rx.link target="_blank"``) — that's a direct user click, immune
        to the pop-up blocker. This handler only manages the polling state.
        """
        import asyncio as _aio
        from ..lib.oauth import oauth_broker
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        provider = self.oauth_connect_provider
        if not provider or not self.oauth_auth_url:
            self.oauth_connect_status = "error"
            return

        plugin_name = self.channel_credentials_editing
        plugin = get_channel(plugin_name) or get_tool_plugin(plugin_name)
        display = plugin.display_name if plugin is not None else plugin_name

        self.oauth_connect_status = "connecting"

        # Poll up to 5 min (60 × 5 s) — Google Login + Consent kann dauern.
        for _ in range(60):
            await _aio.sleep(5)
            if oauth_broker.is_connected(provider):
                self.oauth_connect_status = "connected"
                self.add_debug(  # type: ignore[attr-defined]
                    f"✅ {display}: OAuth verbunden — Plugin verfügbar."
                )
                return

        self.oauth_connect_status = "error"
        self.add_debug(  # type: ignore[attr-defined]
            f"⚠️ {display}: OAuth nicht abgeschlossen — bitte erneut versuchen."
        )

    async def disconnect_oauth(self) -> None:  # type: ignore[no-untyped-def]
        """Remove stored OAuth tokens for the current plugin."""
        from ..lib.oauth import oauth_broker
        from ..lib.plugin_registry import get_channel, get_tool_plugin

        provider = self.oauth_connect_provider
        if not provider:
            return
        await oauth_broker.disconnect(provider)
        self.oauth_connect_status = "idle"

        # Auth-URL neu generieren — Credentials sind noch da, also kann der
        # User direkt wieder verbinden ohne Modal zu schließen+wieder zu öffnen.
        plugin_key = self.channel_credentials_editing
        plugin = get_channel(plugin_key) or get_tool_plugin(plugin_key)
        self.oauth_auth_url = self._build_oauth_auth_url(provider, plugin)

        plugin_name = self.channel_credentials_display_name or provider
        self.add_debug(f"🔌 {plugin_name}: OAuth-Verbindung getrennt.")  # type: ignore[attr-defined]

    def save_channel_credentials(self):
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
        elif tool:
            # Tool plugins may have their own settings loader (private convention)
            tool_loader = getattr(tool, "_load_settings", None)
            if callable(tool_loader):
                try:
                    plugin_settings = dict(tool_loader())
                except Exception:
                    plugin_settings = {}

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

        # Write plugin settings.json (non-secrets) — channel and tool variants
        if plugin_settings:
            if channel:
                channel.save_settings(plugin_settings)
            elif tool:
                tool_saver = getattr(tool, "_save_settings", None)
                if callable(tool_saver):
                    try:
                        tool_saver(plugin_settings)
                    except Exception as exc:
                        self.add_debug(  # type: ignore[attr-defined]
                            f"⚠️ {display}: failed to write settings: {exc}"
                        )

        self.add_debug(f"🔧 {display} settings saved")  # type: ignore[attr-defined, has-type]

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

        # Nach erfolgreichem Save: zurueck zur urspruenglichen Page
        target = self._credentials_return_to or "/"
        return rx.redirect(target)

    # ================================================================
    # PLUGIN MANAGER MODAL
    # ================================================================

    async def toggle_tool_plugin(self, plugin_name: str):
        """Toggle a tool plugin AND apply it on the filesystem immediately.

        Enable/disable is a directory move (tools/ ⇄ disabled/) — that's what
        ``discover_tools`` / ``is_plugin_enabled`` read. Doing it here makes
        the toggle actually take effect; the old batch-on-close apply path was
        never wired up. Applies to ALL tool plugins generically; Vision
        additionally disarms its Watcher.

        OAuth plugins that aren't connected yet are not toggleable from here —
        the user opens the credentials modal (gear icon) and uses the explicit
        "Connect" button there. We just leave a hint in the debug log.
        """
        from ..lib.plugin_registry import (
            disable_plugin, enable_plugin, get_tool_plugin, is_plugin_enabled,
        )
        from ..lib.oauth import oauth_broker

        toggles = dict(self.tool_plugin_toggles)
        current = bool(toggles.get(plugin_name, ""))
        plugin = get_tool_plugin(plugin_name)

        # Block toggling ON for OAuth plugins that aren't connected yet.
        if not current and plugin is not None:
            oauth_provider = getattr(plugin, "oauth_provider", None)
            if oauth_provider and not oauth_broker.is_connected(oauth_provider):
                self.add_debug(  # type: ignore[attr-defined]
                    f"🔐 {plugin.display_name}: bitte über das Zahnrad-Icon "
                    f"die Verbindung herstellen — danach wird der Toggle aktiv."
                )
                return

        new_enabled = not current
        if new_enabled:
            if not is_plugin_enabled(plugin_name):
                enable_plugin(plugin_name, "tool")
            # Re-fetch after enabling so the log shows the real display name
            # (e.g. "Vigilantia"), as it appears in the plugin menu.
            plugin = get_tool_plugin(plugin_name)
            display = plugin.display_name if plugin else plugin_name
            self.add_debug(f"🔌 {display} enabled")  # type: ignore[attr-defined]
        else:
            display = plugin.display_name if plugin else plugin_name
            # Vision: disarm the Watcher FIRST — force_disarm writes the
            # vision settings.json, which is gone once disable_plugin moves
            # the package to disabled/ (that caused the FileNotFoundError).
            if plugin_name == "vision":
                await self.force_disarm_vigilantia()  # type: ignore[attr-defined]
            if is_plugin_enabled(plugin_name):
                disable_plugin(plugin_name, "tool")
            self.add_debug(f"🔌 {display} disabled")  # type: ignore[attr-defined]

        toggles[plugin_name] = "1" if new_enabled else ""
        self.tool_plugin_toggles = toggles

    # ================================================================
    # AUDIT LOG MODAL
    # ================================================================

    def open_audit_log(self) -> None:
        """Load recent audit log entries and open modal."""
        from ..lib.security import load_audit_entries

        self.audit_log_entries = load_audit_entries(include_args=True)
        self.audit_log_open = True

    def close_audit_log(self) -> None:
        self.audit_log_open = False

    # ================================================================
    # TRANSLATION HELPER
    # ================================================================

    def get_text(self, key: str) -> str:
        """Get translated text based on current UI language."""
        return TranslationManager.get_text(key, self.ui_language)
