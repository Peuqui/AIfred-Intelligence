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

A finished bubble is read aloud again on request (``read_bubble``, on the same playlist), every
reply of a session read into new audio without playing it (``regenerate_all``).

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
    """What the rules read of RuntimeSettings."""

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
    """The reading of one reply: of a model call while it streams (``call``), or of a finished bubble
    read aloud again (``call`` None). ``keep_partial``: a reading the service broke off after the first
    sound still goes onto the bubble (a new reply has no audio yet; a bubble read again keeps its old one)."""

    def __init__(self, session_id: str, call: AgentCall | None, reading: BrowserReading, *, keep_partial: bool) -> None:
        self.session_id = session_id
        self.call = call
        self.reading = reading
        self.keep_partial = keep_partial
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
        from .browser_push import TTS_CHUNK_KINDS

        reading = self.reading
        try:
            await reading.wait()
            if reading.aborted or (reading.broke_off and not self.keep_partial):
                return
            # The combined audio supersedes the segments: a client that reconnects must not get them again.
            await put_on_bubble(
                self.session_id, timestamp, reading.audio_urls, reading.browser_speech, reading.note, replaces=TTS_CHUNK_KINDS,
            )
        except Exception as error:  # noqa: BLE001 — background task: an error here would vanish, it is logged with traceback
            log_exception("Keeping the spoken reply failed", error)


async def put_on_bubble(
    session_id: str, timestamp: str, segments: list[str], browser_speech: list[dict[str, Any]], note: str,
    *, replaces: frozenset[str] = frozenset(),
) -> bool:
    """What was spoken goes onto the bubble ``timestamp``: the audio segments joined into the session's
    audio, the parts the browser spoke, who spoke; the clients get the bubble again
    (``message_updated``). False when nothing was spoken, joining failed or the bubble is gone."""
    from .audio_processing import save_audio_to_session
    from .session_storage import keep_reply_audio

    audio_urls: list[str] = []
    if segments:
        combined = await asyncio.to_thread(save_audio_to_session, list(segments), session_id)
        if combined is None:
            debug(f"⚠️ TTS: combining {len(segments)} audio segment(s) failed, the bubble keeps no audio")
        else:
            audio_urls = [combined]
    if not audio_urls and not browser_speech:
        return False
    entry = await asyncio.to_thread(keep_reply_audio, session_id, timestamp, audio_urls, browser_speech, note)
    if entry is None:
        log_message(f"⚠️ TTS: the bubble {timestamp} of session {session_id[:8]} is gone, audio not kept", "warning")
        return False
    session_events.publish(session_id, "message_updated", {"entry": entry}, replaces=replaces)
    log_message(f"🔊 TTS: kept on bubble {timestamp} ({len(audio_urls)} audio, {len(browser_speech)} browser part(s))")
    return True


# The newest reading of each session (finished ones stay until the next replaces them).
_readings: dict[str, ReplySpeech] = {}


async def begin(
    session_id: str, call: AgentCall, settings: SpeechSettings, detected_language: str, ui_language: str,
) -> ReplySpeech | None:
    """The reading of the reply that is about to stream; None when it is not read aloud.

    Waits until the session's previous reading is kept: both would put audio on the same playlist.
    """
    agent = call.agent
    if not speech_wanted(settings, agent):
        return None
    previous = _readings.get(session_id)
    if previous is not None:
        log_message("🔊 TTS: waiting for the previous reply's reading")
        await previous.settled()
    language = speech_language(settings.tts_agents, agent, detected_language, ui_language)
    speech = ReplySpeech(session_id, call, _reading(session_id, agent, language, ui_language), keep_partial=True)
    _readings[session_id] = speech
    speech.reading.start()
    return speech


# The shortest text of a bubble worth a reading (after the reading's filter).
MIN_READING_CHARS = 5


def bubble_reading_text(entry: dict[str, Any]) -> str:
    """What a reading of the bubble speaks: its text as the chat shows it, through the filter of the
    reading of a reply (``tts_text_filter.strip_ui_only_content``: no metrics line, no tags), empty
    when too little is left. Not the model's history: it ends with the tool note and holds only the
    last round's text (04.11.2026)."""
    from .tts_text_filter import strip_ui_only_content

    text = strip_ui_only_content(str(entry.get("content", "")))
    return text if len(text) >= MIN_READING_CHARS else ""


async def read_bubble(session_id: str, entry: dict[str, Any], text: str, settings: SpeechSettings, ui_language: str) -> None:
    """Read a finished bubble aloud again (its button): what plays now stops, ``text``
    (``bubble_reading_text``) is read at once, and what was spoken replaces the bubble's audio when
    the reading is over."""
    stop(session_id)
    previous = _readings.get(session_id)
    if previous is not None:
        await previous.settled()
    agent = str(entry["agent"])
    language = speech_language(settings.tts_agents, agent, "", ui_language)
    speech = ReplySpeech(session_id, None, _reading(session_id, agent, language, ui_language), keep_partial=False)
    _readings[session_id] = speech
    speech.reading.start()
    speech.feed(text)
    speech.end(complete=True)
    speech.keep_on(entry)


def _reading(session_id: str, agent: str, language: str, ui_language: str) -> BrowserReading:
    """The reading of one reply onto the session's playlist (the push bus of its event stream)."""
    from .browser_push import browser_push

    log_message(f"🔊 TTS: reading for agent={agent}, language={language}")
    return BrowserReading(
        session_id=session_id,
        agent=agent,
        language=language,
        ui_language=ui_language,
        push=browser_push,
        report=debug,
        notify=lambda level, text: session_events.publish(session_id, "notice", {"level": level, "text": text}),
        # Pausing the media player for the reading belongs to the audio page, which is not served yet.
        on_first_output=lambda: None,
    )


def keep_on(session_id: str, call: AgentCall, entry: dict[str, Any]) -> None:
    """The reply of ``call`` has become the bubble ``entry``; nothing when that reply is not read."""
    speech = _readings.get(session_id)
    if speech is not None and speech.call is call:
        speech.keep_on(entry)


def stop(session_id: str) -> None:
    """End the session's reading (the service stops) and tell its clients to stop playing."""
    from .browser_push import TTS_CHUNK_KINDS

    speech = _readings.get(session_id)
    if speech is not None:
        speech.reading.abort()
    session_events.publish(session_id, "stop", {}, replaces=TTS_CHUNK_KINDS)


def discard(session_id: str) -> None:
    """The session is deleted: its reading ends, a reading of every reply too, nothing is kept."""
    speech = _readings.pop(session_id, None)
    if speech is not None:
        speech.reading.abort()
    regeneration = _regenerations.pop(session_id, None)
    if regeneration is not None:
        regeneration.cancel()


# ── Every reply read into new audio ──────────────────────────────────────

# The running reading of every reply of each session (the button "Regenerate all").
_regenerations: dict[str, asyncio.Task[None]] = {}
_REGENERATING = frozenset({"speech_regenerating"})


def regenerating(session_id: str) -> bool:
    """Is every reply of the session being read into new audio?"""
    task = _regenerations.get(session_id)
    return task is not None and not task.done()


def regenerate_all(session_id: str, settings: SpeechSettings, ui_language: str) -> bool:
    """Read every reply of the session into new audio, without playing it (the voices may have
    changed): what plays now stops, then one bubble after the other, each gets its audio as soon as
    it is made (``message_updated``). The clients hear of it through ``speech_regenerating``
    (``running``). False when it runs already."""
    if regenerating(session_id):
        return False
    stop(session_id)
    _regenerations[session_id] = asyncio.create_task(
        _regenerate_all(session_id, settings, ui_language), name=f"speech-regenerate-{session_id[:8]}",
    )
    session_events.publish(session_id, "speech_regenerating", {"running": True}, replaces=_REGENERATING)
    return True


async def _regenerate_all(session_id: str, settings: SpeechSettings, ui_language: str) -> None:
    """A failure of the TTS service ends the run (every further bubble would fail the same way); what
    was made so far stays."""
    from .session_storage import load_session
    from .tts_client import TTSSuiteError
    from .tts_service import reading_failure_text

    try:
        session = await asyncio.to_thread(load_session, session_id)
        history = session["data"].get("chat_history", []) if session is not None else []
        replies = [entry for entry in history if entry.get("role") == "assistant"]
        debug(f"🔄 TTS: reading all {len(replies)} replies into new audio")
        made = 0
        for number, entry in enumerate(replies, 1):
            if await _read_into_audio(session_id, entry, settings, ui_language):
                made += 1
            else:
                debug(f"⚠️ TTS: reply {number}/{len(replies)} got no audio")
        debug(f"✅ TTS: {made}/{len(replies)} replies have new audio")
    except TTSSuiteError as error:
        log_message(f"❌ TTS: reading all replies again: service error: {error}", "error")
        debug(f"❌ TTS: {type(error).__name__}: {error}")
        session_events.publish(session_id, "notice", {"level": "error", "text": reading_failure_text(error, ui_language)})
    except Exception as error:  # noqa: BLE001 — background task: an error here would vanish, it is logged with traceback
        log_exception("Reading all replies again failed", error)
        debug(f"❌ TTS: reading all replies again failed: {error}")
    finally:
        _regenerations.pop(session_id, None)
        session_events.publish(session_id, "speech_regenerating", {"running": False}, replaces=_REGENERATING)


async def _read_into_audio(session_id: str, entry: dict[str, Any], settings: SpeechSettings, ui_language: str) -> bool:
    """One bubble read into audio files and the audio put on it; raises ``TTSSuiteError``."""
    from .speech_synthesis import speak_to_files
    from .tts_service import spoken_note

    text = bubble_reading_text(entry)
    if not text:
        return False
    agent = str(entry["agent"])
    reading = await speak_to_files(text, language=speech_language(settings.tts_agents, agent, "", ui_language), agent=agent)
    note = await asyncio.to_thread(spoken_note, reading.summary.trace, ui_language) if reading.summary else ""
    # The new audio replaces what the browser spoke of the bubble before.
    return await put_on_bubble(session_id, str(entry["timestamp"]), reading.audio_urls, [], note)
