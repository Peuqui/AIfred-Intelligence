"""Sprach-Erzeugung für Kanäle mit eigenem Lautsprecher (SSOT).

Text → 48-kHz-PCM in einem wachsenden ``TTSBuffer``: Der erste Satz wird erzeugt
und läuft los, während die übrigen noch synthetisiert werden. Welche Engine auf
welchem Rechner spricht, entscheidet die Eskalationsliste
(``tts_escalation.SpeechRun``); in welcher Sprache, bestimmt der Aufrufer; wie fein
der Text zerlegt wird, ist die Einstellung der sprechenden Engine
(``tts_engines.speech_unit_for``). Kanäle liefern nur noch das PCM an ihr Gerät.
"""

from __future__ import annotations

import asyncio

from .audio_channels._audio_orchestrator import TTSBuffer, silence_pcm
from .logging_utils import log_message
from .tts_escalation import NoSpeechAvailable, SpeechRun

PCM_SAMPLE_RATE = 48000


async def _convert_to_pcm(audio_path: str, label: str) -> bytes | None:
    """Audiodatei → rohes PCM (mono, int16, ``PCM_SAMPLE_RATE``)."""
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


async def synthesize_pcm(text: str, agent: str, run: SpeechRun) -> bytes | None:
    """Ein Satz → PCM; ``None`` (laut geloggt), wenn kein Eintrag der Liste
    sprechen kann oder die Konvertierung scheitert."""
    from .config import PROJECT_ROOT

    try:
        url = await run.synthesize(text, agent)
    except NoSpeechAvailable as exc:
        log_message(f"[{run.label}] {exc} — device stays silent", "error")
        return None
    # The sentence file stays (run.audio_urls): keep_spoken_audio puts it on the
    # bubble; old files go with the TTS audio cleanup like the browser's.
    tts_path = PROJECT_ROOT / "data" / url.removeprefix("/_upload/")
    pcm = await _convert_to_pcm(str(tts_path), run.label)
    if not pcm:
        log_message(f"[{run.label}] TTS conversion failed", "error")
        return None
    return pcm


def _speakable_segments(segments: "list[str | int]", language: str) -> "list[str | int]":
    """Texte von Markdown, Emojis, Code, Links usw. befreien (SSOT: ``clean_text_for_tts``,
    dieselbe wie im Browser-Chat) — einmal hier statt in jedem TTS-Server. Was danach leer
    ist, entfällt; Stille ohne Sprache davor ergibt keinen Sinn und entfällt ebenfalls."""
    from .audio_processing import clean_text_for_tts, reset_content_hint_flags

    # Der Reiniger ist für den Strom EINER Antwort gebaut (Hinweise wie "Hier steht Code."
    # kommen je Antwort einmal): frisch beginnen. Läuft in einem Zug, ohne await.
    reset_content_hint_flags()
    speakable: list[str | int] = []
    for segment in segments:
        if isinstance(segment, int):
            if speakable:
                speakable.append(segment)
            continue
        cleaned = clean_text_for_tts(segment, language)
        if cleaned:
            speakable.append(cleaned)
    while speakable and isinstance(speakable[-1], int):
        speakable.pop()
    return speakable


async def keep_spoken_audio(run: SpeechRun, session_id: str | None) -> None:
    """What the device spoke goes onto the reply's bubble like a browser reply:
    the sentences joined into the session's audio, with who spoke. Without a
    session (a tool's announcement has no bubble of its own) nothing to keep."""
    if not session_id or not run.audio_urls:
        return
    from .audio_processing import save_audio_to_session
    from .session_storage import attach_reply_audio

    session_audio = await asyncio.to_thread(save_audio_to_session, list(run.audio_urls), session_id)
    if session_audio is None:
        log_message(f"[{run.label}] spoken audio not saved to session {session_id[:8]}", "warning")
        return
    note = run.note(run.language)
    if await asyncio.to_thread(attach_reply_audio, session_id, session_audio, note):
        log_message(f"[{run.label}] spoken audio kept on the bubble ({len(run.audio_urls)} sentence(s))")
    else:
        log_message(f"[{run.label}] no reply bubble without audio in session {session_id[:8]}", "warning")


async def start_speech_stream(
    segments: "list[str | int]", agent: str, run: SpeechRun, session_id: str | None = None,
) -> TTSBuffer | None:
    """Den ersten Satz erzeugen und den Puffer zurückgeben; die übrigen Segmente
    (Texte als ``str``, Stille als ``int`` ms) erzeugt ein Hintergrund-Task und hängt
    sie an, während der Abnehmer schon sendet. ``None``, wenn nichts zu sprechen ist
    oder der erste Satz scheitert. Die Texte werden vor der Synthese bereinigt.
    Ist alles erzeugt, kommt das Gesprochene an die Blase der Antwort in
    ``session_id`` (:func:`keep_spoken_audio`)."""
    segments = _speakable_segments(segments, run.language)
    if not segments:
        log_message(f"[{run.label}] nothing to speak", "warning")
        return None
    first = segments[0]
    assert isinstance(first, str)  # _speakable_segments: Stille steht nie am Anfang
    pcm = await synthesize_pcm(first, agent, run)
    if pcm is None:
        return None
    buffer = TTSBuffer(growing=True)
    buffer.append(pcm)
    rest = segments[1:]
    if rest:
        buffer.producer = asyncio.create_task(
            _produce_speech(buffer, rest, agent, run, session_id),
            name=f"speech-producer-{run.label}",
        )
    else:
        buffer.close()
        await keep_spoken_audio(run, session_id)
    return buffer


async def _produce_speech(
    buffer: TTSBuffer, segments: "list[str | int]", agent: str, run: SpeechRun, session_id: str | None,
) -> None:
    """Erzeuger des satzweisen Streamings: hängt Sprache und Stille an den Puffer.
    Fällt eine Engine aus, übernimmt der nächste Eintrag der Liste (``SpeechRun``);
    erst wenn keiner mehr kann, endet der Strom ohne Ende-Ton (laut geloggt)."""
    try:
        failed = False
        for segment in segments:
            if isinstance(segment, int):
                buffer.append(silence_pcm(segment))
                continue
            pcm = await synthesize_pcm(segment, agent, run)
            if pcm is None:
                failed = True
                break
            buffer.append(pcm)
        buffer.close(failed=failed)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — der Strom muss in jedem Fall sauber enden
        log_message(f"[{run.label}] speech producer error: {exc!r}", "error")
        buffer.close(failed=True)
    # Also a stream that broke off: what was spoken until then stays listenable.
    await keep_spoken_audio(run, session_id)
