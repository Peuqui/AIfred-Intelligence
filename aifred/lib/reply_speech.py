"""The spoken reply of a server turn: whether a reply is read aloud, in which language, and what
stays on its bubble.

While an agent's reply streams (``turn_service.stream_call``), its text goes to a
``BrowserReading`` (lib/browser_reading.py), which puts what the TTS service delivers on the
session's playlist: the events ``tts``, ``tts_pause`` and ``tts_speak`` of the session's event
stream. When the reply has become a bubble (``turn_service.finish_call``), what was spoken goes
onto it once the reading is over, and the clients get the bubble again (``message_updated``).

One reading per session at a time, also across turns: the next reply's reading starts when the
previous one is kept, or its audio would interleave on the playlist. ``stop`` ends the session's
reading and tells every client to stop playing.

The rules (``speech_wanted``, ``speech_language``, ``agent_voice_enabled``) hold for the browser
state too; it calls them with its own fields, so this module imports the API (the push bus) only
where it pushes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from . import session_events
from .browser_reading import BrowserReading
from .debug_bus import debug
from .logging_utils import log_exception, log_message

if TYPE_CHECKING:
    from .turn_service import AgentCall


class SpeechSettings(Protocol):
    """What the rules read: RuntimeSettings and the browser state."""

    enable_tts: bool
    tts_autoplay: bool
    tts_agents: dict[str, dict[str, Any]]


def agent_voice_enabled(tts_agents: dict[str, dict[str, Any]], agent: str) -> bool:
    """The agent's own voice switch; on unless it was switched off."""
    return bool(tts_agents.get(agent, {}).get("enabled", True))


def speech_wanted(settings: SpeechSettings, agent: str) -> bool:
    """Is the agent's reply read aloud while it is written? Speech on, autoplay on (nobody listens
    otherwise) and the agent's voice not switched off."""
    return bool(settings.enable_tts and settings.tts_autoplay) and agent_voice_enabled(settings.tts_agents, agent)


def speech_language(tts_agents: dict[str, dict[str, Any]], agent: str, detected: str, ui_language: str) -> str:
    """The language the agent speaks: its own setting, else the language of the question, else the
    interface's. A two-letter code ("de", "en", "zh", …); an empty or "auto" setting is none."""
    own = str(tts_agents.get(agent, {}).get("language", "") or "").lower()
    if own and own != "auto":
        return own
    return detected or ui_language


class ReplySpeech:
    """The reading of one model call's reply."""

    def __init__(self, session_id: str, call: AgentCall, reading: BrowserReading) -> None:
        self.session_id = session_id
        self.call = call
        self.reading = reading
        self._kept: asyncio.Task[None] | None = None

    def feed(self, text: str) -> None:
        self.reading.feed(text)

    def end(self, complete: bool) -> None:
        """The stream is over: a complete reply is read to its end, any other one is not."""
        if complete:
            self.reading.finish()
        else:
            self.reading.abort()

    def keep_on(self, entry: dict[str, Any]) -> None:
        """The reply has become the bubble ``entry``: what is spoken goes onto it when the reading is over."""
        self._kept = asyncio.create_task(self._keep(str(entry["timestamp"])), name=f"reply-speech-{self.session_id[:8]}")

    async def settled(self) -> None:
        """Until the reading is over and what was spoken is on the bubble."""
        await self.reading.wait()
        if self._kept is not None:
            await asyncio.wait({self._kept})

    async def _keep(self, timestamp: str) -> None:
        from .api.browser_bus import TTS_CHUNK_KINDS
        from .audio_processing import save_audio_to_session
        from .session_storage import keep_reply_audio

        reading = self.reading
        try:
            await reading.wait()
            if reading.aborted:
                return
            audio_urls: list[str] = []
            if reading.audio_urls:
                combined = await asyncio.to_thread(save_audio_to_session, list(reading.audio_urls), self.session_id)
                if combined is None:
                    debug(f"⚠️ TTS: combining {len(reading.audio_urls)} audio segment(s) failed, the bubble keeps no audio")
                else:
                    audio_urls = [combined]
            if not audio_urls and not reading.browser_speech:
                return
            entry = await asyncio.to_thread(
                keep_reply_audio, self.session_id, timestamp, audio_urls, reading.browser_speech, reading.note,
            )
            if entry is None:
                log_message(f"⚠️ TTS: the bubble {timestamp} of session {self.session_id[:8]} is gone, audio not kept", "warning")
                return
            # The combined audio supersedes the segments: a client that reconnects must not get them again.
            session_events.publish(self.session_id, "message_updated", {"entry": entry}, replaces=TTS_CHUNK_KINDS)
            log_message(f"🔊 TTS: kept on bubble {timestamp} ({len(audio_urls)} audio, {len(reading.browser_speech)} browser part(s))")
        except Exception as error:  # noqa: BLE001 — background task: an error here would vanish, it is logged with traceback
            log_exception("Keeping the spoken reply failed", error)


# The newest reading of each session (finished ones stay until the next replaces them).
_readings: dict[str, ReplySpeech] = {}


async def begin(
    session_id: str, call: AgentCall, settings: SpeechSettings, detected_language: str, ui_language: str,
) -> ReplySpeech | None:
    """The reading of the reply that is about to stream; None when it is not read aloud.

    Waits until the session's previous reading is kept: both would put audio on the same playlist.
    """
    from .api.browser_bus import browser_push

    agent = call.agent
    if not speech_wanted(settings, agent):
        return None
    previous = _readings.get(session_id)
    if previous is not None:
        log_message("🔊 TTS: waiting for the previous reply's reading")
        await previous.settled()
    reading = BrowserReading(
        session_id=session_id,
        agent=agent,
        language=speech_language(settings.tts_agents, agent, detected_language, ui_language),
        ui_language=ui_language,
        push=browser_push,
        report=debug,
        notify=lambda level, text: session_events.publish(session_id, "notice", {"level": level, "text": text}),
        # Pausing the media player for the reading belongs to the audio page, which is not served yet.
        on_first_output=lambda: None,
    )
    speech = ReplySpeech(session_id, call, reading)
    _readings[session_id] = speech
    log_message(f"🔊 TTS: reading for agent={agent}, language={reading.language}")
    reading.start()
    return speech


def keep_on(session_id: str, call: AgentCall, entry: dict[str, Any]) -> None:
    """The reply of ``call`` has become the bubble ``entry``; nothing when that reply is not read."""
    speech = _readings.get(session_id)
    if speech is not None and speech.call is call:
        speech.keep_on(entry)


def stop(session_id: str) -> None:
    """End the session's reading (the service stops) and tell its clients to stop playing."""
    from .api.browser_bus import TTS_CHUNK_KINDS

    speech = _readings.get(session_id)
    if speech is not None:
        speech.reading.abort()
    session_events.publish(session_id, "stop", {}, replaces=TTS_CHUNK_KINDS)


def discard(session_id: str) -> None:
    """The session is deleted: its reading ends, nothing is kept."""
    speech = _readings.pop(session_id, None)
    if speech is not None:
        speech.reading.abort()
