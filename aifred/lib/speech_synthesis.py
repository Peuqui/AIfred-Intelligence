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
from pathlib import Path

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
    tts_path = PROJECT_ROOT / "data" / url.removeprefix("/_upload/")
    try:
        pcm = await _convert_to_pcm(str(tts_path), run.label)
    finally:
        Path(tts_path).unlink(missing_ok=True)
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


async def start_speech_stream(
    segments: "list[str | int]", agent: str, run: SpeechRun,
) -> TTSBuffer | None:
    """Den ersten Satz erzeugen und den Puffer zurückgeben; die übrigen Segmente
    (Texte als ``str``, Stille als ``int`` ms) erzeugt ein Hintergrund-Task und hängt
    sie an, während der Abnehmer schon sendet. ``None``, wenn nichts zu sprechen ist
    oder der erste Satz scheitert. Die Texte werden vor der Synthese bereinigt."""
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
            _produce_speech(buffer, rest, agent, run),
            name=f"speech-producer-{run.label}",
        )
    else:
        buffer.close()
    return buffer


async def _produce_speech(
    buffer: TTSBuffer, segments: "list[str | int]", agent: str, run: SpeechRun,
) -> None:
    """Erzeuger des satzweisen Streamings: hängt Sprache und Stille an den Puffer.
    Fällt eine Engine aus, übernimmt der nächste Eintrag der Liste (``SpeechRun``);
    erst wenn keiner mehr kann, endet der Strom ohne Ende-Ton (laut geloggt)."""
    try:
        for segment in segments:
            if isinstance(segment, int):
                buffer.append(silence_pcm(segment))
                continue
            pcm = await synthesize_pcm(segment, agent, run)
            if pcm is None:
                buffer.close(failed=True)
                return
            buffer.append(pcm)
        buffer.close()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — der Strom muss in jedem Fall sauber enden
        log_message(f"[{run.label}] speech producer error: {exc!r}", "error")
        buffer.close(failed=True)
