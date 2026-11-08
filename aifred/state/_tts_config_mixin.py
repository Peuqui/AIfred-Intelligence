"""TTS configuration mixin for AIfred state.

Handles the spoken output on/off switch, the list editor, autoplay,
per-agent mute/language, the agent editor's voice settings per engine and the narrator
voices. The list (order, on/off, host per entry) and the unit per engine
are AIfred's own and live in settings.json; the catalog (labels, status, which engines and
hosts exist) comes from the TTS service (``lib.tts_service``). Nothing is added or removed:
the editor shows everything the service offers and only switches and orders it.

Does NOT contain TTS streaming/generation logic (see _tts_streaming_mixin.py).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import reflex as rx

from ..lib.config import TTS_AUTOPLAY_DEFAULT, TTS_DEFAULT_ENGINE

if TYPE_CHECKING:
    from ..lib.agent_tuning import AgentTuning


def _lang(state: Any) -> str:
    return "de" if state.ui_language == "auto" else str(state.ui_language)


def _error_text(error: Exception, lang: str) -> str:
    """What a failed call shows the user: the unreachable message, or the reason in the UI language."""
    from ..lib.tts_client import TTSSuiteUnreachable
    from ..lib.tts_service import unreachable_text
    from ..lib.user_error import said
    return unreachable_text(error, lang) if isinstance(error, TTSSuiteUnreachable) else said(error, lang)


class TTSConfigMixin(rx.State, mixin=True):
    """Mixin for TTS configuration, the list editor and voice settings."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        agent_tuning: rx.Field[dict[str, AgentTuning]]
        editor_agent_id: rx.Field[str]

        def _save_settings(self) -> None: ...
        @rx.event
        def add_debug(self, message: str) -> None: ...

    # ── TTS Settings ──────────────────────────────────────────────
    enable_tts: rx.Field[bool] = rx.field(False)
    tts_autoplay: rx.Field[bool] = rx.field(TTS_AUTOPLAY_DEFAULT)  # Global, persisted ("tts_autoplay")
    tts_playback_rate: rx.Field[str] = rx.field("1.0x")  # Browser playback rate (1.0 = neutral, speed via Agent Settings)
    tts_pitch: rx.Field[str] = rx.field("1.0")  # Pitch adjustment (0.8 = lower, 1.0 = normal, 1.2 = higher)
    # Per agent, for every engine: {"enabled": bool, "language": "auto"|ISO code}
    tts_agents: rx.Field[dict[str, dict[str, Any]]] = rx.field(default_factory=dict)

    # The list and the unit per engine live in settings.json; bumping this revision recomputes every var derived from them.
    tts_list_revision: rx.Field[int] = rx.field(0)
    tts_status_checking: rx.Field[bool] = rx.field(False)

    # Narrator (narrate_file) voice PER ENGINE — voices are engine-bound.
    narrator_voices: rx.Field[dict[str, str]] = rx.field(default_factory=dict)
    narrator_voice_engine: rx.Field[str] = rx.field(TTS_DEFAULT_ENGINE)  # Engine whose narrator voice the modal edits
    narrator_settings_open: rx.Field[bool] = rx.field(False)

    # ── List (computed) ───────────────────────────────────────────

    @rx.var(deps=["tts_list_revision", "ui_language", "agent_tuning"], auto_deps=False)
    def tts_service_error(self) -> str:
        """Why the service's data (status, labels, choices) is not shown (empty = it answers)."""
        from ..lib.tts_settings import list_view
        return list_view(_lang(self), self.agent_tuning["aifred"].model_id).service_error

    @rx.var(
        deps=["tts_list_revision", "ui_language", "agent_tuning", "backend_type", "llamaswap_revision"],
        auto_deps=False,
    )
    def tts_escalation_rows(self) -> list[dict[str, Any]]:
        """One row per list entry, in order — what the menu shows (``tts_settings.list_view``)."""
        from dataclasses import asdict

        from ..lib.tts_settings import list_view
        return [asdict(row) for row in list_view(_lang(self), self.agent_tuning["aifred"].model_id).rows]

    @rx.var(deps=["enable_tts"], auto_deps=False)
    def tts_player_visible(self) -> bool:
        """The audio player shows while the spoken output is on."""
        return self.enable_tts

    # ── Spoken output on/off, autoplay ────────────────────────────

    @rx.event
    def set_enable_tts(self, enabled: bool):
        self.enable_tts = enabled
        self._save_settings()
        self.add_debug(f"🔊 TTS: {'enabled' if enabled else 'disabled'}")

    @rx.event
    def toggle_tts_autoplay(self):
        self.tts_autoplay = not self.tts_autoplay
        self._save_settings()
        self.add_debug(f"🔊 TTS Auto-Play: {'enabled' if self.tts_autoplay else 'disabled'}")

    # ── List editing (settings.json) ──────────────────────────────

    async def _edit_tts_lists(self, change: Callable[[], bool]) -> list[Any]:
        """Run a change of the list (``tts_settings``); the events the handler yields: an error toast
        when nothing was saved (also while the service does not answer: the change needs its catalog)."""
        from ..lib.toast import toast
        from ..lib.tts_client import TTSSuiteError

        try:
            saved = await asyncio.to_thread(change)
        except (TTSSuiteError, ValueError) as error:
            return [toast("error", f"TTS: {_error_text(error, _lang(self))}")]
        if not saved:
            from ..lib.i18n import t
            return [toast("error", f"TTS: {t('error_settings_not_saved', lang=_lang(self))}")]
        self.tts_list_revision += 1
        return []

    @rx.event
    async def move_tts_entry(self, index: int, delta: int):
        from ..lib.tts_settings import move_entry
        for event in await self._edit_tts_lists(lambda: move_entry(index, delta)):
            yield event

    @rx.event
    async def set_tts_entry_enabled(self, index: int, enabled: bool):
        from ..lib.tts_settings import enable_entry
        for event in await self._edit_tts_lists(lambda: enable_entry(index, enabled)):
            yield event

    @rx.event
    async def set_tts_entry_unit(self, engine_key: str, unit: str):
        """Speech unit of an engine (system-wide, every agent and channel)."""
        from ..lib.tts_settings import set_engine_unit
        for event in await self._edit_tts_lists(lambda: set_engine_unit(engine_key, unit)):
            yield event

    @rx.event(background=True)
    async def check_tts_status(self):
        """Show the list as the catalog reports it now (read only)."""
        from ..lib.toast import toast
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import catalog

        async with self:
            self.tts_status_checking = True
        try:
            offered = await asyncio.to_thread(catalog, fresh=True)
        except TTSSuiteError as error:
            async with self:
                lang = _lang(self)
                self.tts_status_checking = False
                self.tts_list_revision += 1
            yield toast("error", _error_text(error, lang))
            return
        async with self:
            self.tts_status_checking = False
            self.tts_list_revision += 1
            self.add_debug(f"🔊 TTS status read ({len(offered)} catalog rows)")

    # ── Per-agent mute and language ───────────────────────────────

    def _tts_agent_enabled(self, agent: str) -> bool:
        from ..lib.reply_speech import agent_voice_enabled
        return agent_voice_enabled(self.tts_agents, agent)

    def _set_tts_agent(self, agent: str, key: str, value: Any) -> None:
        self.tts_agents = {**self.tts_agents, agent: {**self.tts_agents.get(agent, {}), key: value}}
        self._save_settings()

    # ── Agent Editor TTS State ───────────────────────────────────
    # The editor configures each agent's voice per engine; whether that
    # engine speaks is the escalation list's decision.
    editor_tts_engine: rx.Field[str] = rx.field(TTS_DEFAULT_ENGINE)
    _editor_tts_settings: dict[str, Any] = {}  # {"voice": ..., "speed": ..., "pitch": ...}

    @rx.var(deps=["ui_language", "tts_list_revision"], auto_deps=False)
    def tts_editor_engine_options(self) -> list[str]:
        """'Off' (agent muted) plus every usable engine."""
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_client import TTSSuiteUnreachable
        from ..lib.tts_service import unreachable_text, usable_engine_keys
        try:
            return [tts_key_to_label(key, lang=_lang(self)) for key in ["off", *usable_engine_keys()]]
        except TTSSuiteUnreachable as unreachable:
            return [unreachable_text(unreachable, _lang(self))]

    @rx.var(deps=["ui_language", "editor_tts_engine", "tts_agents", "editor_agent_id"], auto_deps=False)
    def editor_tts_engine_label(self) -> str:
        """'Off' when the agent is muted, the edited engine otherwise (the failure while the service
        does not answer: the app compiles this var at start, AIfred must start without the service)."""
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_client import TTSSuiteUnreachable
        from ..lib.tts_service import unreachable_text
        if not self._tts_agent_enabled(self.editor_agent_id):
            return tts_key_to_label("off", lang=_lang(self))
        try:
            return tts_key_to_label(self.editor_tts_engine, lang=_lang(self))
        except TTSSuiteUnreachable as unreachable:
            return unreachable_text(unreachable, _lang(self))

    @rx.var(deps=["editor_tts_engine", "_editor_tts_settings"], auto_deps=False)
    def editor_tts_available_voices(self) -> list[str]:
        """Voices of the edited engine (``tts_settings.voice_choices``)."""
        from ..lib.tts_settings import voice_choices
        return voice_choices(self.editor_tts_engine, str(self._editor_tts_settings.get("voice", "")))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_voice(self) -> str:
        return str(self._editor_tts_settings.get("voice", ""))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_speed(self) -> str:
        return str(self._editor_tts_settings.get("speed", "1.0x"))

    @rx.var(deps=["_editor_tts_settings"], auto_deps=False)
    def editor_agent_tts_pitch(self) -> str:
        return str(self._editor_tts_settings.get("pitch", "1.0"))

    @rx.var(deps=["tts_agents", "editor_agent_id"], auto_deps=False)
    def editor_agent_tts_language(self) -> str:
        """Dropdown value: label of the agent's language override ("Auto" =
        follow the detected/UI language)."""
        from ..lib.config import TTS_LANGUAGE_CODE_TO_LABEL
        agent = self.editor_agent_id
        code = str(self.tts_agents.get(agent, {}).get("language", "") or "auto")
        return TTS_LANGUAGE_CODE_TO_LABEL.get(code, "Auto")

    @rx.var(auto_deps=False)
    def tts_language_labels(self) -> list[str]:
        """Static list of labels for the agent-editor language dropdown."""
        from ..lib.config import TTS_LANGUAGE_LABELS
        return TTS_LANGUAGE_LABELS

    @rx.event
    def set_editor_agent_tts_language(self, label: str) -> None:
        from ..lib.config import TTS_LANGUAGE_LABEL_TO_CODE
        self._set_tts_agent(self.editor_agent_id, "language", TTS_LANGUAGE_LABEL_TO_CODE.get(label, "auto"))

    def _load_editor_tts_settings(self) -> None:
        """Load the edited agent's voice settings for the edited engine (``tts_settings.agent_voice``)."""
        from ..lib.tts_settings import agent_voice

        agent_id = self.editor_agent_id
        self._editor_tts_settings = agent_voice(agent_id, self.editor_tts_engine) if agent_id else {}

    def _save_editor_tts_settings(self) -> None:
        """Persist the edited agent's voice settings for the edited engine."""
        from ..lib.tts_settings import save_agent_voice

        if self.editor_agent_id:
            save_agent_voice(self.editor_agent_id, self.editor_tts_engine, self._editor_tts_settings)

    @rx.event
    def set_editor_tts_engine(self, label: str):
        """'Off' mutes the agent (every engine); an engine unmutes it and
        loads its voice settings for that engine."""
        from ..lib.i18n import tts_label_to_key
        from ..lib.toast import toast
        agent_id = self.editor_agent_id
        try:
            key = tts_label_to_key(label)
        except ValueError as error:   # the dropdown's one line while the service does not answer
            return toast("error", str(error))
        if key == "off":
            self._set_tts_agent(agent_id, "enabled", False)
            return
        self._set_tts_agent(agent_id, "enabled", True)
        self.editor_tts_engine = key
        self._load_editor_tts_settings()

    @rx.event
    def set_editor_agent_tts_voice(self, voice: str):
        self._editor_tts_settings["voice"] = voice
        self._save_editor_tts_settings()

    @rx.event
    def set_editor_agent_tts_speed(self, speed: str):
        self._editor_tts_settings["speed"] = speed
        self._save_editor_tts_settings()

    @rx.event
    def set_editor_agent_tts_pitch(self, pitch: str):
        self._editor_tts_settings["pitch"] = pitch
        self._save_editor_tts_settings()

    # ── Narrator plugin settings (voice per engine) ──────────────

    @rx.var(deps=[], auto_deps=False)
    def narrator_plugin_enabled(self) -> bool:
        # Plugin enable/disable requires a restart anyway — static per process.
        from ..lib.plugin_registry import is_plugin_enabled
        return is_plugin_enabled("narrator")

    @rx.var(deps=["ui_language", "tts_list_revision"], auto_deps=False)
    def narrator_engine_options(self) -> list[str]:
        """Engines whose narrator voice can be set (usable here or on a list host)."""
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_client import TTSSuiteUnreachable
        from ..lib.tts_service import unreachable_text, usable_engine_keys
        try:
            return [tts_key_to_label(key, lang=_lang(self)) for key in usable_engine_keys()]
        except TTSSuiteUnreachable as unreachable:
            return [unreachable_text(unreachable, _lang(self))]

    @rx.var(deps=["ui_language", "narrator_voice_engine"], auto_deps=False)
    def narrator_engine_display(self) -> str:
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_client import TTSSuiteUnreachable
        from ..lib.tts_service import unreachable_text
        try:
            return tts_key_to_label(self.narrator_voice_engine, lang=_lang(self))
        except TTSSuiteUnreachable as unreachable:
            return unreachable_text(unreachable, _lang(self))

    @rx.var(deps=["narrator_voice_engine"], auto_deps=False)
    def narrator_voice_options(self) -> list[str]:
        """The edited engine's voices from the TTS service (none while it does not answer)."""
        from ..lib.tts_client import TTSSuiteUnreachable
        from ..lib.tts_service import voice_names
        try:
            return voice_names(self.narrator_voice_engine)
        except TTSSuiteUnreachable:
            return []

    @rx.var(deps=["narrator_voices", "narrator_voice_engine"], auto_deps=False)
    def narrator_voice_display(self) -> str:
        """Saved voice for the edited engine, else its first own voice (``tts_settings.narrator_voice``)."""
        from ..lib.tts_settings import narrator_voice
        return narrator_voice(self.narrator_voice_engine, self.narrator_voice_options)

    @rx.event
    def set_narrator_voice_engine(self, label: str):
        from ..lib.i18n import tts_label_to_key
        from ..lib.toast import toast
        try:
            self.narrator_voice_engine = tts_label_to_key(label)
        except ValueError as error:   # the dropdown's one line while the service does not answer
            return toast("error", str(error))

    @rx.event
    def set_narrator_voice(self, voice: str) -> None:
        engine = self.narrator_voice_engine
        # Re-assign the dict so Reflex flags it dirty.
        self.narrator_voices = {**self.narrator_voices, engine: voice}
        self._save_settings()
        self.add_debug(f"🔊 Narrator voice ({engine}): {voice}")

    @rx.event
    def open_narrator_settings(self) -> None:
        self.narrator_settings_open = True

    @rx.event
    def close_narrator_settings(self) -> None:
        self.narrator_settings_open = False
