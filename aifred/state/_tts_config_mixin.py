"""TTS configuration mixin for AIfred state.

Handles the spoken output on/off switch, the list editor (entries and hosts), autoplay,
per-agent mute/language, the agent editor's voice settings per engine and the narrator
voices. The list (order, on/off, host per entry) and the unit per engine are AIfred's own
and live in settings.json; the catalog (labels, status, which engines exist) and the hosts
come from the TTS service (``lib.tts_service``), which also stores the hosts.

Does NOT contain TTS streaming/generation logic (see _tts_streaming_mixin.py).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Tuple

import reflex as rx

from ..lib.config import TTS_AUTOPLAY_DEFAULT, TTS_DEFAULT_ENGINE


def _lang(state: Any) -> str:
    return "de" if state.ui_language == "auto" else str(state.ui_language)


def _error_text(error: Exception, lang: str) -> str:
    """What a failed call shows the user: the unreachable message, or the reason as it is."""
    from ..lib.tts_client import TTSSuiteUnreachable
    from ..lib.tts_service import unreachable_text
    return unreachable_text(error, lang) if isinstance(error, TTSSuiteUnreachable) else str(error)


def _planned_engine(model_id: str) -> str | None:
    """The engine the LLM profile reserves VRAM for; ``None`` while the service does not answer
    (blocking: call in a thread)."""
    from ..lib.tts_client import TTSSuiteError
    from ..lib.tts_llm_coordination import planned_tts_engine
    try:
        return planned_tts_engine(model_id)
    except TTSSuiteError:
        return None


def _entry_at(entries: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    if not 0 <= index < len(entries):
        raise ValueError(f"the list has no entry at position {index} (it changed, reload the page)")
    return entries[index]


class TTSConfigMixin(rx.State, mixin=True):
    """Mixin for TTS configuration, the list editor and voice settings."""

    # ── TTS Settings ──────────────────────────────────────────────
    enable_tts: bool = False
    tts_autoplay: bool = TTS_AUTOPLAY_DEFAULT  # Global, persisted ("tts_autoplay")
    tts_playback_rate: str = "1.0x"  # Browser playback rate (1.0 = neutral, speed via Agent Settings)
    tts_pitch: str = "1.0"  # Pitch adjustment (0.8 = lower, 1.0 = normal, 1.2 = higher)
    # Per agent, for every engine: {"enabled": bool, "language": "auto"|ISO code}
    tts_agents: Dict[str, Dict[str, Any]] = {}

    # The list and the unit per engine live in settings.json, the hosts in the TTS service;
    # bumping this revision recomputes every var derived from them.
    tts_list_revision: int = 0
    tts_status_checking: bool = False
    tts_host_form_open: bool = False
    tts_new_host_name: str = ""
    tts_new_host_address: str = ""
    tts_new_host_ssh_user: str = ""
    tts_new_host_ssh_port: str = ""  # empty = the service's default

    # Narrator (narrate_file) voice PER ENGINE — voices are engine-bound.
    narrator_voices: Dict[str, str] = {}
    narrator_voice_engine: str = TTS_DEFAULT_ENGINE  # Engine whose narrator voice the modal edits
    narrator_settings_open: bool = False

    # ── List (computed) ───────────────────────────────────────────

    @rx.var(deps=["tts_list_revision", "ui_language"], auto_deps=False)
    def tts_service_error(self) -> str:
        """Why the service's data (status, labels, choices) is not shown (empty = it answers)."""
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import catalog, unreachable_text
        try:
            catalog()
        except TTSSuiteError as error:
            return unreachable_text(error, _lang(self))
        return ""

    @rx.var(
        deps=["tts_list_revision", "ui_language", "agent_tuning", "backend_type", "llamaswap_revision"],
        auto_deps=False,
    )
    def tts_escalation_rows(self) -> List[Dict[str, Any]]:
        """One row per list entry, in order — what the menu shows. The list comes from the
        settings and is shown even while the service does not answer; then label, status
        and note are bare (``tts_service_error`` says why)."""
        from ..lib.i18n import t
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_llm_coordination import planned_tts_engine
        from ..lib.tts_service import entries, entry_label, status_key, stored_entries

        lang = _lang(self)
        service_down = False
        reserved = ""
        try:
            listed = entries()
            reserved = planned_tts_engine(self.agent_tuning["aifred"].model_id)  # type: ignore[attr-defined]
        except TTSSuiteError:
            service_down = True
            listed = tuple(stored_entries())
        rows: List[Dict[str, Any]] = []
        for index, entry in enumerate(listed):
            unavailable = entry.info is None and not service_down
            if unavailable:
                note = t("tts_entry_not_offered", lang=lang)
            elif entry.info is not None and not entry.info.installed:
                note = t("tts_entry_not_installed", lang=lang)
            else:
                note = (entry.info.start_hint or "") if entry.info is not None else ""
            rows.append({
                "index": index,
                "engine": entry.engine,
                "label": entry_label(entry, lang),
                "enabled": entry.enabled,
                "unit": entry.unit,
                "unavailable": unavailable,
                "reserved": entry.host is None and entry.engine == reserved,
                "note": note,
                "status": t("tts_status_unreachable", lang=lang) if service_down else t(f"tts_status_{status_key(entry)}", lang=lang),
            })
        return rows

    @rx.var(deps=["tts_list_revision", "ui_language"], auto_deps=False)
    def tts_add_options(self) -> List[Dict[str, str]]:
        """Every catalog row not yet in the list, as one choice "engine · place" (see
        ``tts_service.add_choices``). Empty while the service does not answer."""
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import add_choices
        try:
            return add_choices(_lang(self))
        except TTSSuiteError:
            return []

    @rx.var(deps=["tts_list_revision"], auto_deps=False)
    def tts_host_rows(self) -> List[Dict[str, str]]:
        """The service's hosts. Empty while the service does not answer."""
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import host_rows
        try:
            return host_rows()
        except TTSSuiteError:
            return []

    @rx.var(deps=["tts_list_revision", "ui_language"], auto_deps=False)
    def tts_speech_unit(self) -> str:
        """Speech unit of the topmost active entry — decides whether the browser streams
        sentence by sentence (the entry that really speaks is only known at the first
        sentence). While the service does not answer, the failure message instead of a unit."""
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import first_enabled_entry, unreachable_text
        try:
            entry = first_enabled_entry()
        except TTSSuiteError as error:
            return unreachable_text(error, _lang(self))
        return entry.unit if entry else "whole"

    @rx.var(deps=["tts_list_revision"], auto_deps=False)
    def tts_streaming_enabled(self) -> bool:
        """Streaming = the spoken output starts before the response is complete
        (every unit except "whole")."""
        return self.tts_speech_unit != "whole"

    @rx.var(deps=["enable_tts"], auto_deps=False)
    def tts_player_visible(self) -> bool:
        """The audio player shows while the spoken output is on."""
        return self.enable_tts

    # ── Spoken output on/off, autoplay ────────────────────────────

    def set_enable_tts(self, enabled: bool):
        self.enable_tts = enabled
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"🔊 TTS: {'enabled' if enabled else 'disabled'}")  # type: ignore[attr-defined]
        yield
        yield from self._apply_planned_tts()

    def toggle_tts_autoplay(self):
        self.tts_autoplay = not self.tts_autoplay
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"🔊 TTS Auto-Play: {'enabled' if self.tts_autoplay else 'disabled'}")  # type: ignore[attr-defined]

    def _apply_planned_tts(self):
        """Bring the VRAM state in line with the list: the LLM profile reserves
        room for the planned engine (topmost enabled local GPU entry with a
        calibrated profile), or for none when the spoken output is off.
        Generator — each yield is one finished blocking step."""
        from ..lib.tts_llm_coordination import ensure_tts_state, planned_tts_engine
        from ..lib.tts_service import entries, sync_voices

        wanted = (
            planned_tts_engine(self.agent_tuning["aifred"].model_id)  # type: ignore[attr-defined]
            if self.enable_tts else ""
        )
        generator = ensure_tts_state(wanted_tts=wanted, backend_type=self.backend_type)  # type: ignore[attr-defined]
        try:
            while True:
                self.add_debug(f"🔊 {next(generator)}")  # type: ignore[attr-defined]
                yield
        except StopIteration:
            pass

        # Engines that need their voices prepared (cloud enrollment) do it for the
        # enabled entries once per key.
        if self.enable_tts:
            prepared: set[str] = set()
            for entry in entries():
                if entry.active and entry.info is not None and not entry.info.browser and entry.engine not in prepared:
                    prepared.add(entry.engine)
                    voices = sync_voices(entry.engine)
                    self.add_debug(f"🔊 {entry.engine}: {len(voices)} voices ready")  # type: ignore[attr-defined]
                    yield

    # ── List editing (settings.json) ──────────────────────────────

    def replan_tts(self):
        """Bring the VRAM state in line with the list (event chain after an edit that moved
        the planned engine; sync generator: each step blocks)."""
        yield from self._apply_planned_tts()

    async def _edit_tts_lists(self, change: Callable[[Dict[str, Any]], None]) -> List[Any]:
        """Apply ``change`` to a copy of the persisted settings, check the list, save. Returns
        the events the handler yields: an error toast when nothing was saved, a warning when the
        service could not be asked for the VRAM plan, the re-plan when the planned engine moved."""
        import copy

        from ..lib.settings import persisted_settings
        from ..lib.i18n import t
        from ..lib.toast import toast
        from ..lib.tts_service import check_stored_list

        model_id = self.agent_tuning["aifred"].model_id  # type: ignore[attr-defined]
        before = await asyncio.to_thread(_planned_engine, model_id)
        settings = copy.deepcopy(persisted_settings())
        try:
            change(settings)
            check_stored_list(settings["tts_escalation"])
        except (ValueError, KeyError) as error:
            return [toast("error", f"TTS: {error}")]
        self._write_settings_file(settings)  # type: ignore[attr-defined]
        self.tts_list_revision += 1
        after = await asyncio.to_thread(_planned_engine, model_id)
        if before is None or after is None:
            return [toast("warning", t("tts_plan_unchecked", lang=_lang(self)))]
        return [type(self).replan_tts] if after != before else []

    async def move_tts_entry(self, index: int, delta: int):
        def change(settings: Dict[str, Any]) -> None:
            entries = settings["tts_escalation"]
            _entry_at(entries, index)
            target = index + delta
            if 0 <= target < len(entries):
                entries[index], entries[target] = entries[target], entries[index]
        for event in await self._edit_tts_lists(change):
            yield event

    async def set_tts_entry_enabled(self, index: int, enabled: bool):
        def change(settings: Dict[str, Any]) -> None:
            _entry_at(settings["tts_escalation"], index)["enabled"] = enabled
        for event in await self._edit_tts_lists(change):
            yield event

    async def remove_tts_entry(self, index: int):
        def change(settings: Dict[str, Any]) -> None:
            _entry_at(settings["tts_escalation"], index)
            del settings["tts_escalation"][index]
        for event in await self._edit_tts_lists(change):
            yield event

    async def add_tts_entry(self, choice: str):
        """Append the chosen "engine|host" (host empty = this machine/cloud)."""
        engine_key, _, host_name = choice.partition("|")

        def change(settings: Dict[str, Any]) -> None:
            settings["tts_escalation"].append({"engine": engine_key, "host": host_name or None, "enabled": True})
        for event in await self._edit_tts_lists(change):
            yield event

    async def set_tts_entry_unit(self, engine_key: str, unit: str):
        """Speech unit of an engine (system-wide, every agent and channel)."""
        from ..lib.tts_client import SPEECH_UNITS

        def change(settings: Dict[str, Any]) -> None:
            if unit not in SPEECH_UNITS:
                raise ValueError(f"speech unit must be one of {SPEECH_UNITS}, got {unit!r}")
            settings.setdefault("tts_toggles_per_engine", {}).setdefault(engine_key, {})["unit"] = unit
        for event in await self._edit_tts_lists(change):
            yield event

    # ── Hosts (stored by the TTS service) ─────────────────────────

    def set_tts_new_host_name(self, value: str) -> None:
        self.tts_new_host_name = value.strip()

    def set_tts_new_host_address(self, value: str) -> None:
        self.tts_new_host_address = value.strip()

    def set_tts_new_host_ssh_user(self, value: str) -> None:
        self.tts_new_host_ssh_user = value.strip()

    def set_tts_new_host_ssh_port(self, value: str) -> None:
        self.tts_new_host_ssh_port = value.strip()

    async def _edit_tts_hosts(self, change: Callable[[Dict[str, Any]], None]) -> Tuple[List[Any], bool]:
        """Apply ``change`` to the service's configuration (read, change, store; the service
        validates). Returns ``(events, stored)``: an error toast when nothing was stored."""
        from ..lib.toast import toast
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import edit_config

        try:
            await asyncio.to_thread(edit_config, change)
        except (TTSSuiteError, ValueError) as error:
            return [toast("error", _error_text(error, _lang(self)))], False
        self.tts_list_revision += 1
        return [], True

    async def save_tts_host(self):
        """Create the host from the form, or change it when a host of that name exists."""
        from ..lib.i18n import t
        from ..lib.toast import toast
        lang = _lang(self)
        name, address = self.tts_new_host_name, self.tts_new_host_address
        if not name or not address:
            yield toast("error", t("tts_host_fields_required", lang=lang))
            return
        host: Dict[str, Any] = {"name": name, "address": address}
        if self.tts_new_host_ssh_user:
            host["ssh_user"] = self.tts_new_host_ssh_user
        if self.tts_new_host_ssh_port:
            if not self.tts_new_host_ssh_port.isdigit():
                yield toast("error", t("tts_host_port_invalid", lang=lang, port=self.tts_new_host_ssh_port))
                return
            host["ssh_port"] = int(self.tts_new_host_ssh_port)

        def change(config: Dict[str, Any]) -> None:
            config["hosts"] = [h for h in config["hosts"] if h["name"] != name] + [host]
        events, stored = await self._edit_tts_hosts(change)
        for event in events:
            yield event
        if stored:
            self.tts_new_host_name = ""
            self.tts_new_host_address = ""
            self.tts_new_host_ssh_user = ""
            self.tts_new_host_ssh_port = ""
            self.tts_host_form_open = False

    def edit_tts_host(self, name: str) -> None:
        """Fill the form with the host; saving it changes that host."""
        from ..lib.tts_service import host_rows
        host = next(row for row in host_rows() if row["name"] == name)
        self.tts_new_host_name = host["name"]
        self.tts_new_host_address = host["address"]
        self.tts_new_host_ssh_user = host["ssh_user"]
        self.tts_new_host_ssh_port = host["ssh_port"]
        self.tts_host_form_open = True

    def toggle_tts_host_form(self) -> None:
        self.tts_host_form_open = not self.tts_host_form_open

    async def remove_tts_host(self, name: str):
        """Remove a host from the service; refused while an entry of the list still names it."""
        from ..lib.tts_service import stored_list

        def change(config: Dict[str, Any]) -> None:
            if any(raw["host"] == name for raw in stored_list()):
                raise ValueError(f"host {name!r} still has entries in the list — remove them first")
            config["hosts"] = [host for host in config["hosts"] if host["name"] != name]
        events, _ = await self._edit_tts_hosts(change)
        for event in events:
            yield event

    @rx.event(background=True)  # type: ignore[operator]
    async def check_tts_status(self):
        """Make the service read its engine folders again (an engine moved into or out of
        ``disabled/`` takes effect, remembered start failures are forgotten) and show the
        list as the catalog reports it now. Never starts an engine."""
        from ..lib.toast import toast
        from ..lib.tts_client import TTSSuiteError
        from ..lib.tts_service import catalog, reload_engines

        async with self:
            self.tts_status_checking = True
        try:
            changed = await asyncio.to_thread(reload_engines)
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
            removed, added = changed["removed"], changed["added"]
            if removed or added:
                self.add_debug(f"🔊 TTS engines reloaded: removed {removed or '-'}, added {added or '-'}")  # type: ignore[attr-defined]
            self.add_debug(f"🔊 TTS status read ({len(offered)} catalog rows)")  # type: ignore[attr-defined]

    # ── Per-agent mute and language ───────────────────────────────

    def tts_agent_enabled(self, agent: str) -> bool:
        return bool(self.tts_agents.get(agent, {}).get("enabled", True))

    def _set_tts_agent(self, agent: str, key: str, value: Any) -> None:
        self.tts_agents = {**self.tts_agents, agent: {**self.tts_agents.get(agent, {}), key: value}}
        self._save_settings()  # type: ignore[attr-defined]

    # ── Agent Editor TTS State ───────────────────────────────────
    # The editor configures each agent's voice per engine; whether that
    # engine speaks is the escalation list's decision.
    editor_tts_engine: str = TTS_DEFAULT_ENGINE
    _editor_tts_settings: Dict[str, Any] = {}  # {"voice": ..., "speed": ..., "pitch": ...}

    @rx.var(deps=["ui_language", "tts_list_revision"], auto_deps=False)
    def tts_editor_engine_options(self) -> List[str]:
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
        """'Off' when the agent is muted, the edited engine otherwise."""
        from ..lib.i18n import tts_key_to_label
        if not self.tts_agent_enabled(self.editor_agent_id):  # type: ignore[attr-defined]
            return tts_key_to_label("off", lang=_lang(self))
        return tts_key_to_label(self.editor_tts_engine, lang=_lang(self))

    @rx.var(deps=["editor_tts_engine", "_editor_tts_settings"], auto_deps=False)
    def editor_tts_available_voices(self) -> List[str]:
        """Voices of the edited engine: live catalogue (or the static one while
        the container is down) plus every voice already saved for it."""
        from ..lib.settings import persisted_settings
        from ..lib.tts_engines import get_engine, voice_names

        engine = get_engine(self.editor_tts_engine)
        voices = set(voice_names(engine)) if engine else set()
        saved = persisted_settings().get("tts_agent_voices_per_engine", {}).get(self.editor_tts_engine, {})
        voices |= {str(cfg.get("voice")) for cfg in saved.values() if isinstance(cfg, dict) and cfg.get("voice")}
        current = self._editor_tts_settings.get("voice", "")
        if current:
            voices.add(str(current))
        starred = sorted(v for v in voices if v.startswith("★"))
        return starred + sorted(v for v in voices if not v.startswith("★"))

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
        agent = self.editor_agent_id  # type: ignore[attr-defined]
        code = str(self.tts_agents.get(agent, {}).get("language", "") or "auto")
        return TTS_LANGUAGE_CODE_TO_LABEL.get(code, "Auto")

    @rx.var(auto_deps=False)
    def tts_language_labels(self) -> List[str]:
        """Static list of labels for the agent-editor language dropdown."""
        from ..lib.config import TTS_LANGUAGE_LABELS
        return TTS_LANGUAGE_LABELS

    @rx.var(deps=["editor_tts_engine"], auto_deps=False)
    def editor_tts_supports_language(self) -> bool:
        """True if the edited engine honours the language setting (greys out
        the dropdown for engines that ignore it)."""
        from ..lib.tts_engines import get_engine
        eng = get_engine(self.editor_tts_engine)
        return bool(eng and eng.supports_language)

    def set_editor_agent_tts_language(self, label: str) -> None:
        from ..lib.config import TTS_LANGUAGE_LABEL_TO_CODE
        self._set_tts_agent(self.editor_agent_id, "language", TTS_LANGUAGE_LABEL_TO_CODE.get(label, "auto"))  # type: ignore[attr-defined]

    def _load_editor_tts_settings(self) -> None:
        """Load the edited agent's voice settings for the edited engine:
        the user's saved values, else the agent's default from agents.json."""
        from ..lib.agent_config import get_tts_voice_default
        from ..lib.settings import persisted_settings

        agent_id = self.editor_agent_id  # type: ignore[attr-defined]
        if not agent_id:
            self._editor_tts_settings = {}
            return
        saved = persisted_settings().get("tts_agent_voices_per_engine", {}).get(self.editor_tts_engine, {}).get(agent_id)
        self._editor_tts_settings = dict(saved) if saved else dict(get_tts_voice_default(agent_id, self.editor_tts_engine))

    def _save_editor_tts_settings(self) -> None:
        """Persist the edited agent's voice settings for the edited engine."""
        from ..lib.settings import load_settings

        agent_id = self.editor_agent_id  # type: ignore[attr-defined]
        if not agent_id:
            return
        settings = load_settings() or {}
        per_engine = settings.setdefault("tts_agent_voices_per_engine", {}).setdefault(self.editor_tts_engine, {})
        per_engine[agent_id] = {
            key: self._editor_tts_settings[key]
            for key in ("voice", "speed", "pitch") if key in self._editor_tts_settings
        }
        self._write_settings_file(settings)  # type: ignore[attr-defined]

    def set_editor_tts_engine(self, label: str) -> None:
        """'Off' mutes the agent (every engine); an engine unmutes it and
        loads its voice settings for that engine."""
        from ..lib.i18n import tts_label_to_key
        agent_id = self.editor_agent_id  # type: ignore[attr-defined]
        key = tts_label_to_key(label)
        if key == "off":
            self._set_tts_agent(agent_id, "enabled", False)
            return
        self._set_tts_agent(agent_id, "enabled", True)
        self.editor_tts_engine = key
        self._load_editor_tts_settings()

    def set_editor_agent_tts_voice(self, voice: str):
        self._editor_tts_settings["voice"] = voice
        self._save_editor_tts_settings()

    def set_editor_agent_tts_speed(self, speed: str):
        self._editor_tts_settings["speed"] = speed
        self._save_editor_tts_settings()

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
    def narrator_engine_options(self) -> List[str]:
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
        return tts_key_to_label(self.narrator_voice_engine, lang=_lang(self))

    @rx.var(deps=["narrator_voice_engine"], auto_deps=False)
    def narrator_voice_options(self) -> List[str]:
        """The edited engine's own voices (lib-SSOT ``voice_names``)."""
        from ..lib.tts_engines import get_engine, voice_names
        engine = get_engine(self.narrator_voice_engine)
        return voice_names(engine) if engine else []

    @rx.var(deps=["narrator_voices", "narrator_voice_engine"], auto_deps=False)
    def narrator_voice_display(self) -> str:
        """Saved voice for the edited engine, else its first own voice."""
        saved = self.narrator_voices.get(self.narrator_voice_engine, "")
        options = self.narrator_voice_options
        if saved and saved in options:
            return saved
        return options[0] if options else ""

    def set_narrator_voice_engine(self, label: str) -> None:
        from ..lib.i18n import tts_label_to_key
        self.narrator_voice_engine = tts_label_to_key(label)

    def set_narrator_voice(self, voice: str) -> None:
        engine = self.narrator_voice_engine
        # Re-assign the dict so Reflex flags it dirty.
        self.narrator_voices = {**self.narrator_voices, engine: voice}
        self._save_settings()  # type: ignore[attr-defined]
        self.add_debug(f"🔊 Narrator voice ({engine}): {voice}")  # type: ignore[attr-defined]

    def open_narrator_settings(self) -> None:
        self.narrator_settings_open = True

    def close_narrator_settings(self) -> None:
        self.narrator_settings_open = False
