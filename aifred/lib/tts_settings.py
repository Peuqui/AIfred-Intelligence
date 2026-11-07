"""The settings of the spoken output, for every interface (the speech page of Vue, the Reflex copy until it
goes): the rows of AIfred's list, a change of the list as one transaction, each agent's switch, language
and voice per engine, the narrator's voice per engine.

The list, the unit per engine, the agents' voices and the narrator's live in settings.json; what exists
(engines, machines, voices, status) is the TTS service's catalog (``tts_service``).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .i18n import t
from .settings import persisted_settings, update_settings
from .tts_client import SPEECH_UNITS, TTSSuiteError

# ── The list ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ListRow:
    """One entry of the list as the editor shows it."""

    index: int
    engine: str
    label: str          # engine · place
    enabled: bool
    unit: str
    unavailable: bool   # the service does not offer the entry (any more)
    reserved: bool      # the main model's profile keeps room for this engine on this machine
    note: str           # not offered, not installed, or why its last start failed
    warn: bool          # switched on, but its machine does not answer
    status: str         # what it would do right now, in words


@dataclass(frozen=True)
class ListView:
    rows: list[ListRow]
    service_error: str   # why labels and status are bare: the service does not answer ("" = it does)


def list_view(lang: str, main_model: str) -> ListView:
    """The list in speaking order. It is shown from the settings while the service does not answer;
    then label, status and note are bare and ``service_error`` says why."""
    from .tts_llm_coordination import planned_tts_engine
    from .tts_service import (
        entries,
        entry_label,
        status_key,
        stored_entries,
        unreachable_text,
    )

    service_error = ""
    reserved = ""
    try:
        listed = entries()
        reserved = planned_tts_engine(main_model)
    except TTSSuiteError as error:
        service_error = unreachable_text(error, lang)
        listed = tuple(stored_entries())
    rows = []
    for index, entry in enumerate(listed):
        unavailable = entry.info is None and not service_error
        if unavailable:
            note = t("tts_entry_not_offered", lang=lang)
        elif entry.info is not None and not entry.info.installed:
            note = t("tts_entry_not_installed", lang=lang)
        else:
            note = (entry.info.start_hint or "") if entry.info is not None else ""
        rows.append(ListRow(
            index=index,
            engine=entry.engine,
            label=entry_label(entry, lang),
            enabled=entry.enabled,
            unit=entry.unit,
            unavailable=unavailable,
            reserved=entry.host is None and entry.engine == reserved,
            note=note,
            warn=entry.enabled and entry.info is not None and entry.info.status == "unreachable",
            status=t("tts_status_unreachable", lang=lang) if service_error else t(f"tts_status_{status_key(entry)}", lang=lang),
        ))
    return ListView(rows, service_error)


def change_list(change: Callable[[list[dict[str, Any]], dict[str, Any]], None]) -> bool:
    """Match the list with the catalog, apply ``change`` to copies of the list and of the units per
    engine, check, save both. False when settings.json could not be written. A moved planned engine
    needs nothing here: the next load of the model picks its profile (``planned_tts_engine``).

    Raises:
        TTSSuiteError: the service does not answer (the match needs its catalog).
        ValueError: the change cannot be right (a position the list no longer has, an unknown unit).
    """
    from .tts_service import catalog, check_stored_settings, matched_list

    listed = matched_list(catalog())
    units = copy.deepcopy(persisted_settings().get("tts_toggles_per_engine", {}))
    change(listed, units)
    check_stored_settings({"tts_escalation": listed})
    return update_settings({"tts_escalation": listed, "tts_toggles_per_engine": units})


def _entry_at(listed: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if not 0 <= index < len(listed):
        raise ValueError(f"the list has no entry at position {index} (it changed, reload the page)")
    return listed[index]


def move_entry(index: int, delta: int) -> bool:
    """The entry at ``index`` one place up (-1) or down (1); at an end it stays."""
    def change(listed: list[dict[str, Any]], _units: dict[str, Any]) -> None:
        _entry_at(listed, index)
        target = index + delta
        if 0 <= target < len(listed):
            listed[index], listed[target] = listed[target], listed[index]
    return change_list(change)


def enable_entry(index: int, enabled: bool) -> bool:
    def change(listed: list[dict[str, Any]], _units: dict[str, Any]) -> None:
        _entry_at(listed, index)["enabled"] = enabled
    return change_list(change)


def set_engine_unit(engine: str, unit: str) -> bool:
    """The unit an engine speaks in (system-wide, every agent and channel)."""
    def change(_listed: list[dict[str, Any]], units: dict[str, Any]) -> None:
        if unit not in SPEECH_UNITS:
            raise ValueError(f"speech unit must be one of {SPEECH_UNITS}, got {unit!r}")
        units.setdefault(engine, {})["unit"] = unit
    return change_list(change)


# ── The agents' voices ───────────────────────────────────────────────────

VOICE_FIELDS = ("voice", "speed", "pitch")


def agent_voice(agent: str, engine: str) -> dict[str, Any]:
    """The agent's voice on ``engine``: what the user saved, else the agent's default (agents.json)."""
    from .agent_config import get_tts_voice_default

    saved = persisted_settings().get("tts_agent_voices_per_engine", {}).get(engine, {}).get(agent)
    return dict(saved) if saved else dict(get_tts_voice_default(agent, engine))


def save_agent_voice(agent: str, engine: str, voice: dict[str, Any]) -> bool:
    """Keep the agent's voice on ``engine`` (its voice fields only)."""
    per_engine = copy.deepcopy(persisted_settings().get("tts_agent_voices_per_engine", {}))
    per_engine.setdefault(engine, {})[agent] = {key: voice[key] for key in VOICE_FIELDS if key in voice}
    return update_settings({"tts_agent_voices_per_engine": per_engine})


def voice_choices(engine: str, current: str) -> list[str]:
    """The voices to choose from on ``engine``: the service's, every one already saved for it and the
    current one; starred voices first. The saved ones alone while the service does not answer."""
    from .tts_client import TTSSuiteUnreachable
    from .tts_service import voice_names

    try:
        voices = set(voice_names(engine))
    except TTSSuiteUnreachable:
        voices = set()
    saved = persisted_settings().get("tts_agent_voices_per_engine", {}).get(engine, {})
    voices |= {str(config["voice"]) for config in saved.values() if isinstance(config, dict) and config.get("voice")}
    if current:
        voices.add(current)
    starred = sorted(voice for voice in voices if voice.startswith("★"))
    return starred + sorted(voice for voice in voices if not voice.startswith("★"))


def set_agent_voice_enabled(agent: str, enabled: bool) -> bool:
    """The agent speaks (on every engine) or stays silent."""
    return _set_agent_speech(agent, "enabled", enabled)


def set_agent_language(agent: str, language: str) -> bool:
    """The language the agent speaks: a code of ``TTS_LANGUAGE_OPTIONS``, "auto" = the question's."""
    from .config import TTS_LANGUAGE_CODE_TO_LABEL

    if language not in TTS_LANGUAGE_CODE_TO_LABEL:
        raise ValueError(f"unknown speech language {language!r}")
    return _set_agent_speech(agent, "language", language)


def _set_agent_speech(agent: str, key: str, value: Any) -> bool:
    agents = copy.deepcopy(persisted_settings().get("tts_agents", {}))
    agents[agent] = {**agents.get(agent, {}), key: value}
    return update_settings({"tts_agents": agents})


# ── The narrator ─────────────────────────────────────────────────────────


def narrator_voice(engine: str, choices: list[str]) -> str:
    """The narrator's voice on ``engine``: the saved one when the engine still offers it, else its first."""
    saved = str(persisted_settings().get("narrator_voices", {}).get(engine, ""))
    if saved and saved in choices:
        return saved
    return choices[0] if choices else ""


def set_narrator_voice(engine: str, voice: str) -> bool:
    voices = {**persisted_settings().get("narrator_voices", {}), engine: voice}
    return update_settings({"narrator_voices": voices})
