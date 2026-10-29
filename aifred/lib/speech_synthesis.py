"""Speech output for channels with their own speaker (SSOT).

Text -> 48 kHz PCM in a growing ``TTSBuffer``: the first segment of the TTS service plays
while the others are still being generated. Which engine on which machine speaks, how the
text is cut (the unit of the speaking entry), the cleaning of Markdown, code and links, the
announcement of a voice change and the keep-alive markers are the service's business
(``tts_client``); the language is the caller's. What stays here: AIfred's own text filter
(``tts_text_filter``), the voices of the agent (``tts_voices``), the conversion of the
delivered audio to PCM for the device, the silence of the pause markers and keeping what was
spoken on the reply's bubble. Channels only hand the PCM to their device.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from .audio_channels._audio_orchestrator import (
    KEEPALIVE_SILENCE,
    TTSBuffer,
    silence_pcm,
)
from .config import REPLAY_PARAGRAPH_PAUSE_MS
from .debug_bus import debug
from .logging_utils import log_message
from .tts_client import (
    SpeakSummary,
    SpeechStream,
    TTSSuiteError,
    get_tts_client,
    new_run_id,
)
from .tts_service import escalation_report, request_engines, spoken_note
from .tts_text_filter import strip_ui_only_content
from .tts_voices import voices_for

PCM_SAMPLE_RATE = 48000


async def _report_escalation(summary: SpeakSummary, label: str) -> None:
    """The service's account of a reading (who spoke, who failed or was skipped, change of voice) to the debug bus: log file and, in a session, the console."""
    try:
        lines = await asyncio.to_thread(escalation_report, summary, label)
    except TTSSuiteError as error:
        # The reading is over and its audio delivered: a service that cannot name the entries now must not
        # cost the end of the output (the device waits for it), but it is said.
        debug(f"⚠️ [{label}] TTS escalation report failed: {type(error).__name__}: {error}")
        return
    for line in lines:
        debug(line)


async def _convert_to_pcm(audio_path: str, label: str) -> bytes | None:
    """Audio file -> raw PCM (mono, int16, ``PCM_SAMPLE_RATE``)."""
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", str(PCM_SAMPLE_RATE), "-ac", "1",
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return stdout
        log_message(f"[{label}] ffmpeg error: {stderr.decode()[:200]}", "error")
    except FileNotFoundError:
        log_message(f"[{label}] ffmpeg not found", "error")
    return None


@dataclass
class SpokenReading:
    """What one reading left behind for the bubble: the audio of every segment (in order, as URLs
    in the TTS audio folder) and, once the service has finished, its summary."""

    audio_urls: list[str] = field(default_factory=list)
    summary: SpeakSummary | None = None


def speakable_text(paragraphs: Sequence[str]) -> str:
    """The paragraphs without AIfred's own interface content, joined by blank lines into ONE text
    (the service cuts it); empty paragraphs are dropped."""
    filtered = (strip_ui_only_content(paragraph) for paragraph in paragraphs)
    return "\n\n".join(paragraph for paragraph in filtered if paragraph)


async def open_reading(text: str, *, language: str, agent: str, paragraph_pause_ms: int) -> SpeechStream:
    """Start the service if needed and start a reading of ``text`` in the voice of ``agent``, with AIfred's
    list (the service skips the browser entry). Raises ``TTSSuiteError`` if the reading cannot start."""
    await asyncio.to_thread(get_tts_client().ensure_ready)
    engines = await asyncio.to_thread(request_engines)
    return await get_tts_client().speak(
        text, language=language, engines=engines, paragraph_pause_ms=paragraph_pause_ms,
        voices=voices_for(engines, agent), run=new_run_id(),
    )


async def _speech_pcm(
    text: str, *, language: str, agent: str, paragraph_pause_ms: int, label: str, reading: SpokenReading,
) -> AsyncIterator[bytes]:
    """The PCM of one reading in speaking order, piece by piece as the service delivers it.

    A keep-alive marker becomes a few ms of silence (keeps the stream to the device open), a pause
    marker the silence it asks for (``paragraph_pause_ms`` of the request, delivered by the service at
    each paragraph border), both in the order the service delivers them.
    """
    from .audio_processing import TTS_AUDIO_DIR, store_tts_audio

    stream = await open_reading(text, language=language, agent=agent, paragraph_pause_ms=paragraph_pause_ms)
    async for segment in stream:
        if segment.keepalive:
            yield KEEPALIVE_SILENCE
            continue
        if segment.pause_ms:
            yield silence_pcm(segment.pause_ms)
            continue
        url = await asyncio.to_thread(store_tts_audio, segment.audio, segment.media_type, agent, segment.engine)
        pcm = await _convert_to_pcm(str(TTS_AUDIO_DIR / url.rsplit("/", 1)[-1]), label)
        if not pcm:
            raise RuntimeError(f"[{label}] conversion of the spoken audio failed")
        reading.audio_urls.append(url)
        yield pcm
    reading.summary = stream.summary
    await _report_escalation(stream.summary, label)


async def store_reading_audio(stream: SpeechStream, agent: str) -> list[str]:
    """The audio of a reading as files, in speaking order. Markers (keep-alive, pause) carry no audio and
    are skipped: whoever wants the pause makes it (the replay and the narrator join the files)."""
    from .audio_processing import store_tts_audio

    urls: list[str] = []
    async for segment in stream:
        if segment.keepalive or segment.pause_ms:
            continue
        urls.append(await asyncio.to_thread(store_tts_audio, segment.audio, segment.media_type, agent, segment.engine))
    return urls


async def speak_to_files(text: str, *, language: str, agent: str) -> SpokenReading:
    """A complete text read into audio files (replay of a bubble): the segments in order, no markers
    (the replay is one audio, ``REPLAY_PARAGRAPH_PAUSE_MS``). Raises ``TTSSuiteError``."""
    stream = await open_reading(text, language=language, agent=agent, paragraph_pause_ms=REPLAY_PARAGRAPH_PAUSE_MS)
    reading = SpokenReading()
    reading.audio_urls.extend(await store_reading_audio(stream, agent))
    reading.summary = stream.summary
    await _report_escalation(stream.summary, f"Replay {agent}")
    return reading


async def keep_spoken_audio(reading: SpokenReading, language: str, label: str, session_id: str | None) -> None:
    """What the device spoke goes onto the reply's bubble like a browser reply: the segments joined
    into the session's audio, with who spoke. Without a session (a tool's announcement has no
    bubble of its own) nothing to keep."""
    if not session_id or not reading.audio_urls:
        return
    from .audio_processing import save_audio_to_session
    from .session_storage import attach_reply_audio

    session_audio = await asyncio.to_thread(save_audio_to_session, list(reading.audio_urls), session_id)
    if session_audio is None:
        log_message(f"[{label}] spoken audio not saved to session {session_id[:8]}", "warning")
        return
    note = await asyncio.to_thread(spoken_note, reading.summary.trace, language) if reading.summary else ""
    if await asyncio.to_thread(attach_reply_audio, session_id, session_audio, note):
        log_message(f"[{label}] spoken audio kept on the bubble ({len(reading.audio_urls)} segment(s))")
    else:
        log_message(f"[{label}] no reply bubble without audio in session {session_id[:8]}", "warning")


async def start_speech_stream(
    paragraphs: Sequence[str], paragraph_pause_ms: int, agent: str, language: str, label: str,
    session_id: str | None = None,
) -> TTSBuffer | None:
    """Start a reading and return the buffer once the first segment is converted; the rest is
    appended by a task while the receiver already sends. ``None`` (loudly logged) if there is
    nothing to speak or the service cannot speak: the device stays silent. When the reading is
    over, what was spoken goes onto the bubble of the reply in ``session_id``."""
    text = speakable_text(paragraphs)
    if not text:
        log_message(f"[{label}] nothing to speak", "warning")
        return None
    reading = SpokenReading()
    try:
        pieces = _speech_pcm(
            text, language=language, agent=agent, paragraph_pause_ms=paragraph_pause_ms, label=label, reading=reading,
        )
        first = await anext(pieces, None)
    except TTSSuiteError as error:
        log_message(f"[{label}] {type(error).__name__}: {error} — device stays silent", "error")
        return None
    except RuntimeError as error:
        log_message(f"{error} — device stays silent", "error")
        return None
    if first is None:
        log_message(f"[{label}] the TTS service delivered no audio — device stays silent", "error")
        return None
    buffer = TTSBuffer(growing=True)
    buffer.append(first)
    buffer.producer = asyncio.create_task(
        _produce_speech(buffer, pieces, reading, language, label, session_id), name=f"speech-producer-{label}",
    )
    return buffer


async def _produce_speech(
    buffer: TTSBuffer, pieces: AsyncIterator[bytes], reading: SpokenReading, language: str, label: str,
    session_id: str | None,
) -> None:
    """Producer of the growing buffer: appends the rest of the reading. A failure (service error,
    conversion, anything else) ends the stream without end tone, loudly logged."""
    failed = False
    try:
        async for piece in pieces:
            buffer.append(piece)
    except asyncio.CancelledError:
        buffer.close(failed=True)
        raise
    except Exception as error:  # noqa: BLE001 — the stream must end cleanly in every case
        log_message(f"[{label}] speech stream broke off: {type(error).__name__}: {error}", "error")
        failed = True
    buffer.close(failed=failed)
    # Also a stream that broke off: what was spoken until then stays listenable.
    await keep_spoken_audio(reading, language, label, session_id)
