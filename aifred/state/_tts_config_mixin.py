"""TTS configuration mixin for AIfred state.

Handles the spoken output on/off switch, the escalation list editor (entries
and hosts), autoplay, per-agent mute/language, the agent editor's voice
settings per engine and the narrator voices. Which engine actually speaks is
decided per reply by the escalation list (``lib.tts_escalation.SpeechRun``).

Does NOT contain TTS streaming/generation logic (see _tts_streaming_mixin.py).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

import reflex as rx

from ..lib.config import TTS_AUTOPLAY_DEFAULT, TTS_DEFAULT_ENGINE


def _lang(state: Any) -> str:
    return "de" if state.ui_language == "auto" else str(state.ui_language)


class TTSConfigMixin(rx.State, mixin=True):
    """Mixin for TTS configuration, the escalation list and voice settings."""

    # ── TTS Settings ──────────────────────────────────────────────
    enable_tts: bool = False
    tts_autoplay: bool = TTS_AUTOPLAY_DEFAULT  # Global, persisted ("tts_autoplay")
    tts_playback_rate: str = "1.0x"  # Browser playback rate (1.0 = neutral, speed via Agent Settings)
    tts_pitch: str = "1.0"  # Pitch adjustment (0.8 = lower, 1.0 = normal, 1.2 = higher)
    # Per agent, for every engine: {"enabled": bool, "language": "auto"|ISO code}
    tts_agents: Dict[str, Dict[str, Any]] = {}

    # The escalation list lives in settings.json (tts_escalation, tts_hosts);
    # bumping this revision recomputes every var derived from it.
    tts_list_revision: int = 0
    tts_new_entry_engine: str = ""
    tts_new_entry_host: str = ""
    tts_new_host_name: str = ""
    tts_new_host_address: str = ""

    # Narrator (narrate_file) voice PER ENGINE — voices are engine-bound.
    narrator_voices: Dict[str, str] = {}
    narrator_voice_engine: str = TTS_DEFAULT_ENGINE  # Engine whose narrator voice the modal edits
    narrator_settings_open: bool = False

    # ── Escalation list (computed) ────────────────────────────────

    @rx.var(
        deps=["tts_list_revision", "ui_language", "agent_tuning", "backend_type", "llamaswap_revision"],
        auto_deps=False,
    )
    def tts_escalation_rows(self) -> List[Dict[str, Any]]:
        """One row per list entry, in order — what the menu shows."""
        from ..lib.i18n import t
        from ..lib.tts_engines import speech_unit_for
        from ..lib.tts_escalation import escalation_entries, location_label, planned_tts_engine

        lang = _lang(self)
        reserved = planned_tts_engine(self.agent_tuning["aifred"].model_id)  # type: ignore[attr-defined]
        rows: List[Dict[str, Any]] = []
        for index, entry in enumerate(escalation_entries()):
            engine = entry.engine
            installed = engine.is_remote or engine.is_installed()
            rows.append({
                "index": index,
                "engine": engine.key,
                "label": f"{engine.label_short} · {location_label(entry, lang)}",
                "enabled": entry.enabled,
                "unit": speech_unit_for(engine.key),
                "reserved": entry.host is None and engine.key == reserved,
                "note": "" if installed else t("tts_entry_not_installed", lang=lang),
            })
        return rows

    @rx.var(deps=["tts_list_revision", "ui_language"], auto_deps=False)
    def tts_add_engine_options(self) -> List[str]:
        """Engines that can be added: every engine whose Docker image is built
        (or that needs none)."""
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_engines import TTS_ENGINES
        return [
            tts_key_to_label(key, lang=_lang(self))
            for key, engine in TTS_ENGINES.items()
            if not engine.runs_in_container or engine.is_installed()
        ]

    @rx.var(deps=["tts_list_revision", "ui_language"], auto_deps=False)
    def tts_add_host_options(self) -> List[str]:
        """Where a new entry runs: this machine or one of the hosts."""
        from ..lib.i18n import t
        from ..lib.settings import persisted_settings
        return [t("tts_location_this_machine", lang=_lang(self))] + [
            str(host["name"]) for host in persisted_settings()["tts_hosts"]
        ]

    @rx.var(deps=["tts_list_revision"], auto_deps=False)
    def tts_host_rows(self) -> List[Dict[str, str]]:
        from ..lib.settings import persisted_settings
        return [
            {"name": str(host["name"]), "address": str(host["address"])}
            for host in persisted_settings()["tts_hosts"]
        ]

    @rx.var(deps=["tts_list_revision"], auto_deps=False)
    def tts_speech_unit(self) -> str:
        """Speech unit of the topmost enabled entry — decides whether the
        browser streams sentence by sentence (the entry that really speaks is
        only known at the first sentence)."""
        from ..lib.tts_engines import speech_unit_for
        from ..lib.tts_escalation import first_enabled_entry
        entry = first_enabled_entry()
        return speech_unit_for(entry.engine.key) if entry else "whole"

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
        from ..lib.tts_engine_manager import ensure_tts_state
        from ..lib.tts_escalation import escalation_entries, planned_tts_engine

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

        # DashScope: enroll new or changed SSOT reference voices (idempotent via
        # WAV hash — instant when nothing changed) so cloned voices are ready.
        if self.enable_tts and any(
            entry.enabled and entry.engine.key == "dashscope" for entry in escalation_entries()
        ):
            from ..lib.credential_broker import broker
            from ..lib.dashscope_enroll import enroll_progress
            api_key = broker.get("cloud_qwen", "api_key")
            if api_key:
                for line in enroll_progress(api_key):
                    self.add_debug(f"🔊 {line}")  # type: ignore[attr-defined]
                    yield

    # ── Escalation list editing ───────────────────────────────────

    def _edit_tts_lists(self, change: Callable[[Dict[str, Any]], None]):
        """Apply ``change`` to the persisted lists, validate, save, and — when
        the planned engine moved — re-plan the VRAM. Generator."""
        import copy

        from ..lib.settings import persisted_settings
        from ..lib.tts_escalation import escalation_entries, planned_tts_engine

        model_id = self.agent_tuning["aifred"].model_id  # type: ignore[attr-defined]
        before = planned_tts_engine(model_id)
        settings = copy.deepcopy(persisted_settings())
        change(settings)
        try:
            escalation_entries(settings)
        except (ValueError, KeyError) as exc:
            self.add_debug(f"❌ TTS list: {exc}")  # type: ignore[attr-defined]
            return
        self._write_settings_file(settings)  # type: ignore[attr-defined]
        self.tts_list_revision += 1
        yield
        if planned_tts_engine(model_id, settings) != before:
            yield from self._apply_planned_tts()

    def move_tts_entry(self, index: int, delta: int):
        def change(settings: Dict[str, Any]) -> None:
            entries = settings["tts_escalation"]
            target = index + delta
            if 0 <= target < len(entries):
                entries[index], entries[target] = entries[target], entries[index]
        yield from self._edit_tts_lists(change)

    def set_tts_entry_enabled(self, index: int, enabled: bool):
        def change(settings: Dict[str, Any]) -> None:
            settings["tts_escalation"][index]["enabled"] = enabled
        yield from self._edit_tts_lists(change)

    def remove_tts_entry(self, index: int):
        def change(settings: Dict[str, Any]) -> None:
            del settings["tts_escalation"][index]
        yield from self._edit_tts_lists(change)

    def set_tts_new_entry_engine(self, label: str) -> None:
        self.tts_new_entry_engine = label

    def set_tts_new_entry_host(self, label: str) -> None:
        self.tts_new_entry_host = label

    def add_tts_entry(self):
        from ..lib.i18n import t, tts_label_to_key

        engine_key = tts_label_to_key(self.tts_new_entry_engine)
        host_label = self.tts_new_entry_host
        host = None if host_label in ("", t("tts_location_this_machine", lang=_lang(self))) else host_label

        def change(settings: Dict[str, Any]) -> None:
            entries = settings["tts_escalation"]
            if any(e["engine"] == engine_key and e["host"] == host for e in entries):
                raise ValueError(f"{engine_key}@{host or 'local'} is already in the list")
            entries.append({"engine": engine_key, "host": host, "enabled": True})
        yield from self._edit_tts_lists(change)

    def set_tts_entry_unit(self, engine_key: str, unit: str):
        """Speech unit of an engine (system-wide, every agent and channel)."""
        from ..lib.tts_engines import SPEECH_UNITS
        if unit not in SPEECH_UNITS:
            raise ValueError(f"speech unit must be one of {SPEECH_UNITS}, got {unit!r}")

        def change(settings: Dict[str, Any]) -> None:
            settings.setdefault("tts_toggles_per_engine", {}).setdefault(engine_key, {})["unit"] = unit
        yield from self._edit_tts_lists(change)

    def set_tts_new_host_name(self, value: str) -> None:
        self.tts_new_host_name = value.strip()

    def set_tts_new_host_address(self, value: str) -> None:
        self.tts_new_host_address = value.strip()

    def add_tts_host(self):
        name, address = self.tts_new_host_name, self.tts_new_host_address
        if not name or not address:
            self.add_debug("❌ TTS host: name and address are required")  # type: ignore[attr-defined]
            return

        def change(settings: Dict[str, Any]) -> None:
            settings["tts_hosts"].append({"name": name, "address": address, "ports": {}})
        yield from self._edit_tts_lists(change)
        self.tts_new_host_name = ""
        self.tts_new_host_address = ""

    def remove_tts_host(self, name: str):
        def change(settings: Dict[str, Any]) -> None:
            if any(entry["host"] == name for entry in settings["tts_escalation"]):
                raise ValueError(f"host {name!r} still has entries in the list — remove them first")
            settings["tts_hosts"] = [h for h in settings["tts_hosts"] if h["name"] != name]
        yield from self._edit_tts_lists(change)

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

    @rx.var(deps=["ui_language"], auto_deps=False)
    def tts_editor_engine_options(self) -> List[str]:
        """'Off' (agent muted) plus every engine."""
        from ..lib.config import TTS_ENGINE_KEYS
        from ..lib.i18n import tts_key_to_label
        return [tts_key_to_label(key, lang=_lang(self)) for key in TTS_ENGINE_KEYS]

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

    @rx.var(deps=["ui_language"], auto_deps=False)
    def narrator_engine_options(self) -> List[str]:
        """Engines whose narrator voice can be set (images built or none needed)."""
        from ..lib.i18n import tts_key_to_label
        from ..lib.tts_engines import TTS_ENGINES
        return [
            tts_key_to_label(key, lang=_lang(self))
            for key, engine in TTS_ENGINES.items()
            if not engine.runs_in_container or engine.is_installed()
        ]

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
