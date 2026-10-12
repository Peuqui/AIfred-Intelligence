"""Voices of AIfred's agents for a reading (SSOT).

The TTS service knows engines, not agents: AIfred resolves voice, speed and pitch of the
speaking agent for each engine of a request's list and sends them in ``SpeakRequest.voices``.
Where AIfred has no voice for an engine, the engine stays out of ``voices``; the service then
falls back to its own defaults (``/config`` -> ``engines[...].voice``) and reports an engine
that has none in the trace of the reading. AIfred keeps no default voice of its own, and sends
speed and pitch only where the user (or ``agents.json``) has set them: what is not set is left to
the service's own tuning of that voice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tts_client import EngineRef


class NoVoiceConfigured(LookupError):
    """Neither the user nor ``agents.json`` names a voice for this agent on this engine."""


def parse_speed_factor(raw: object) -> float | None:
    """``"1.25x"``/``"1.25"``/``1.25`` -> 1.25; ``None``/``""``/garbage -> ``None``.

    Only the PARSER is shared; whether a caller falls back to a default or fails loud is its
    own policy.
    """
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace("x", ""))
    except (ValueError, TypeError):
        return None


def configured_voice(engine_key: str, agent: str) -> tuple[str, float | None, float | None]:
    """Voice, speed and pitch of an agent for an engine; the voice is ``""`` if none is configured,
    speed and pitch are ``None`` if nobody has set them.

    Order: the user's setting for this engine and this agent, then the one of agent ``aifred``,
    then the agent's default from ``agents.json``, then the one of ``aifred``.

    Raises:
        ValueError: speed or pitch is set but not a number.
    """
    from .agent_config import get_tts_voice_default
    from .settings import persisted_settings

    user_voices = persisted_settings().get("tts_agent_voices_per_engine", {}).get(engine_key, {})
    user_cfg = user_voices.get(agent) or user_voices.get("aifred") or {}
    default_cfg = get_tts_voice_default(agent, engine_key)
    if not default_cfg.get("voice"):
        default_cfg = get_tts_voice_default("aifred", engine_key)

    voice = str(user_cfg.get("voice") or default_cfg.get("voice") or "")
    values: list[float | None] = []
    for key in ("speed", "pitch"):
        raw = user_cfg.get(key) or default_cfg.get(key)
        value = parse_speed_factor(raw)
        if raw and value is None:
            raise ValueError(f"invalid {key} {raw!r} for agent {agent!r} on {engine_key}")
        values.append(value)
    return voice, values[0], values[1]


def resolve_voice(engine_key: str, agent: str) -> tuple[str, float | None, float | None]:
    """Like :func:`configured_voice`, but a missing voice is an error.

    Raises:
        NoVoiceConfigured: no voice for this agent on this engine.
        ValueError: speed or pitch is set but not a number.
    """
    voice, speed, pitch = configured_voice(engine_key, agent)
    if not voice:
        raise NoVoiceConfigured(f"no voice configured for agent {agent!r} on {engine_key}")
    return voice, speed, pitch


def voices_for(engines: Sequence[EngineRef], agent: str) -> dict[str, dict[str, Any]]:
    """``SpeakRequest.voices`` of a reading: ``{engine_key: {voice, speed, pitch}}`` for the engines
    of the list, in the voice of ``agent``. An engine without a configured voice is left out."""
    voices: dict[str, dict[str, Any]] = {}
    for ref in engines:
        if ref.engine in voices:
            continue
        try:
            voice, speed, pitch = resolve_voice(ref.engine, agent)
        except NoVoiceConfigured:
            continue
        voices[ref.engine] = {
            key: value for key, value in (("voice", voice), ("speed", speed), ("pitch", pitch)) if value is not None
        }
    return voices
