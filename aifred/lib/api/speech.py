"""The speech page: AIfred's list of speech engines, each agent's voice per engine, the narrator's voice and
whether speech recognition answers. Thin over ``lib/tts_settings.py``; the catalog of engines and voices
is the TTS service's, its being unreachable is shown, not hidden (``service_error``)."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from ..agent_config import AgentConfig
from ..logging_utils import log_message
from ..tts_client import TTSSuiteError
from ..user_error import Invalid, Missing, NotDone, Unavailable
from .app import api_app
from .schemas import ChoiceOption


class SpeechRow(BaseModel):
    index: int
    engine: str
    label: str = Field(description="Engine · place")
    enabled: bool
    unit: str
    unavailable: bool = Field(description="The service does not offer the entry (any more)")
    reserved: bool = Field(description="The main model's profile keeps room for this engine on this machine")
    note: str
    warn: bool = Field(description="Switched on, but its machine does not answer")
    status: str


class SpeechList(BaseModel):
    rows: list[SpeechRow]
    service_error: str = Field(description="Why labels and status are bare: the service does not answer (empty: it does)")


class SpeechChoices(BaseModel):
    engines: list[ChoiceOption] = Field(description="The engines a voice can be set for (empty while the service does not answer)")
    units: list[ChoiceOption]
    languages: list[ChoiceOption]
    speeds: list[str]
    pitches: list[str]
    service_error: str


class AgentSpeech(BaseModel):
    agent: str
    display_name: str
    emoji: str
    enabled: bool = Field(description="The agent speaks (on every engine)")
    language: str = Field(description="A code of the language choices, auto = the question's")
    engine: str
    voice: str
    speed: str
    pitch: str
    voices: list[str] = Field(description="The voices to choose from on this engine")


class AgentSpeechChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    language: str | None = None


class AgentVoiceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: str
    speed: str
    pitch: str


class ListMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    delta: int = Field(ge=-1, le=1)


class ListSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    enabled: bool


class UnitChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: str


class NarratorVoice(BaseModel):
    engine: str
    voice: str
    voices: list[str]


class NarratorChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: str


class SpeechRecognition(BaseModel):
    available: bool = Field(description="The STT service answers (device, engine and model are set on its own page)")


def _lang() -> str:
    from ..prompt_loader import get_language

    return get_language()


def _list() -> SpeechList:
    from ..runtime_settings import load_runtime_settings
    from ..tts_settings import list_view

    view = list_view(_lang(), load_runtime_settings().agent_tuning["aifred"].model_id)
    return SpeechList(rows=[SpeechRow(**asdict(row)) for row in view.rows], service_error=view.service_error)


async def _changed(change: Callable[[], bool], what: str) -> SpeechList:
    """Run a change of the list; 503 while the service does not answer (the change needs its catalog),
    422 for a change that cannot be right, 500 when settings.json could not be written."""
    try:
        saved = await asyncio.to_thread(change)
    except TTSSuiteError as error:  # the change needs the service's catalog
        raise Unavailable("tts_service_unreachable", reason=str(error)) from error
    if not saved:
        raise NotDone("settings_not_saved")
    log_message(f"🔊 Speech list: {what}")
    return await asyncio.to_thread(_list)


@api_app.get("/speech/list", response_model=SpeechList, tags=["Speech"])
async def speech_list() -> SpeechList:
    """AIfred's list in speaking order, with the status of each entry."""
    return await asyncio.to_thread(_list)


@api_app.post("/speech/list/refresh", response_model=SpeechList, tags=["Speech"])
async def speech_list_refresh() -> SpeechList:
    """The list as the service reports it right now (the catalog read fresh)."""
    from ..tts_service import catalog, reset_cache

    try:
        await asyncio.to_thread(catalog, fresh=True)
    except TTSSuiteError:
        reset_cache()   # the list then says that the service does not answer, not what it said before
    return await asyncio.to_thread(_list)


@api_app.post("/speech/list/move", response_model=SpeechList, tags=["Speech"])
async def speech_list_move(move: ListMove) -> SpeechList:
    from ..tts_settings import move_entry

    return await _changed(lambda: move_entry(move.index, move.delta), f"entry {move.index} moved by {move.delta}")


@api_app.post("/speech/list/switch", response_model=SpeechList, tags=["Speech"])
async def speech_list_switch(switch: ListSwitch) -> SpeechList:
    from ..tts_settings import enable_entry

    return await _changed(lambda: enable_entry(switch.index, switch.enabled), f"entry {switch.index} {'on' if switch.enabled else 'off'}")


@api_app.put("/speech/units/{engine}", response_model=SpeechList, tags=["Speech"])
async def speech_unit(engine: str, change: UnitChange) -> SpeechList:
    """The unit an engine speaks in, for every agent and channel."""
    from ..tts_settings import set_engine_unit

    return await _changed(lambda: set_engine_unit(engine, change.unit), f"{engine} speaks by {change.unit}")


@api_app.get("/speech/choices", response_model=SpeechChoices, tags=["Speech"])
async def speech_choices() -> SpeechChoices:
    from ..config import TTS_LANGUAGE_OPTIONS, TTS_PITCH_CHOICES, TTS_SPEED_CHOICES
    from ..i18n import t
    from ..tts_client import SPEECH_UNITS
    from ..tts_service import engine_label, unreachable_text, usable_engine_keys

    lang = _lang()

    def engines() -> list[ChoiceOption]:
        return [ChoiceOption(id=key, label=engine_label(key, lang)) for key in usable_engine_keys()]

    try:
        offered, service_error = await asyncio.to_thread(engines), ""
    except TTSSuiteError as error:
        offered, service_error = [], unreachable_text(error, lang)
    return SpeechChoices(
        engines=offered,
        units=[ChoiceOption(id=unit, label=t(f"tts_unit_{unit}", lang=lang)) for unit in SPEECH_UNITS],
        languages=[ChoiceOption(id=code, label=label) for code, label in TTS_LANGUAGE_OPTIONS],
        speeds=TTS_SPEED_CHOICES, pitches=TTS_PITCH_CHOICES, service_error=service_error,
    )


def _chat_agent(agent: str) -> AgentConfig:
    from ..agent_config import selectable_agents

    config = selectable_agents().get(agent)
    if config is None:
        raise Missing("no_chat_agent", agent=agent)
    return config


def _agent_speech(agent: str, engine: str) -> AgentSpeech:
    from ..reply_speech import agent_voice_enabled
    from ..settings import persisted_settings
    from ..tts_settings import agent_voice, voice_choices

    config = _chat_agent(agent)
    speech = persisted_settings().get("tts_agents", {})
    voice = agent_voice(agent, engine)
    current = str(voice.get("voice", ""))
    return AgentSpeech(
        agent=agent, display_name=config.display_name, emoji=config.emoji,
        enabled=agent_voice_enabled(speech, agent), language=str(speech.get(agent, {}).get("language") or "auto"),
        engine=engine, voice=current, speed=str(voice.get("speed", "1.0x")), pitch=str(voice.get("pitch", "1.0")),
        voices=voice_choices(engine, current),
    )


@api_app.get("/speech/agents", response_model=list[AgentSpeech], tags=["Speech"])
async def speech_agents(engine: str = Query(...)) -> list[AgentSpeech]:
    """Every chat agent's voice on ``engine``, its switch and language."""
    from ..agent_config import selectable_agents

    return await asyncio.to_thread(lambda: [_agent_speech(agent, engine) for agent in selectable_agents()])


@api_app.patch("/speech/agents/{agent}", response_model=AgentSpeech, tags=["Speech"])
async def speech_agent_change(agent: str, change: AgentSpeechChange, engine: str = Query(...)) -> AgentSpeech:
    """The agent's switch or language (both for every engine); answers with its voice on ``engine``."""
    from ..tts_settings import set_agent_language, set_agent_voice_enabled

    _chat_agent(agent)
    saved = True
    if change.enabled is not None:
        saved = set_agent_voice_enabled(agent, change.enabled)
    if change.language is not None:
        saved = set_agent_language(agent, change.language) and saved
    if not saved:
        raise NotDone("settings_not_saved")
    log_message(f"🔊 Speech of {agent}: {change.model_dump(exclude_none=True)}")
    return await asyncio.to_thread(_agent_speech, agent, engine)


@api_app.put("/speech/agents/{agent}/voices/{engine}", response_model=AgentSpeech, tags=["Speech"])
async def speech_agent_voice(agent: str, engine: str, change: AgentVoiceChange) -> AgentSpeech:
    from ..config import TTS_PITCH_CHOICES, TTS_SPEED_CHOICES
    from ..tts_settings import save_agent_voice

    _chat_agent(agent)
    if change.speed not in TTS_SPEED_CHOICES or change.pitch not in TTS_PITCH_CHOICES:
        raise Invalid("speed_pitch_choices", speed=change.speed, pitch=change.pitch)
    if not save_agent_voice(agent, engine, change.model_dump()):
        raise NotDone("settings_not_saved")
    log_message(f"🔊 Voice of {agent} on {engine}: {change.voice} {change.speed} {change.pitch}")
    return await asyncio.to_thread(_agent_speech, agent, engine)


def _narrator(engine: str) -> NarratorVoice:
    from ..tts_client import TTSSuiteUnreachable
    from ..tts_service import voice_names
    from ..tts_settings import narrator_voice

    try:
        voices = voice_names(engine)
    except TTSSuiteUnreachable as error:
        raise Unavailable("tts_service_unreachable", reason=str(error)) from error
    return NarratorVoice(engine=engine, voice=narrator_voice(engine, voices), voices=voices)


@api_app.get("/speech/narrator/{engine}", response_model=NarratorVoice, tags=["Speech"])
async def speech_narrator(engine: str) -> NarratorVoice:
    """The voice the narrator reads files with on ``engine``."""
    return await asyncio.to_thread(_narrator, engine)


@api_app.put("/speech/narrator/{engine}", response_model=NarratorVoice, tags=["Speech"])
async def speech_narrator_change(engine: str, change: NarratorChange) -> NarratorVoice:
    from ..tts_settings import set_narrator_voice

    offered = await asyncio.to_thread(_narrator, engine)
    if change.voice not in offered.voices:
        raise Invalid("no_voice", engine=engine, voice=change.voice)
    if not set_narrator_voice(engine, change.voice):
        raise NotDone("settings_not_saved")
    log_message(f"🔊 Narrator voice on {engine}: {change.voice}")
    return await asyncio.to_thread(_narrator, engine)


@api_app.get("/speech/recognition", response_model=SpeechRecognition, tags=["Speech"])
async def speech_recognition() -> SpeechRecognition:
    from ..stt_client import STTError, get_default_client

    try:
        await asyncio.to_thread(get_default_client().health)
    except STTError:
        return SpeechRecognition(available=False)
    return SpeechRecognition(available=True)
