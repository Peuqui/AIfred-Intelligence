"""Sprach-Erzeugung für Kanäle mit eigenem Lautsprecher (SSOT).

Text → 48-kHz-PCM in einem wachsenden ``TTSBuffer``: Der erste Satz wird erzeugt
und läuft los, während die übrigen noch synthetisiert werden. Dazu gehört das
Bereitstellen der GPU-TTS-Engine (SSOT: ``tts_engine_manager``). Welche Engine
spricht und in welcher Sprache, bestimmt der Aufrufer (Kanal-Einstellung); wie
fein der Text zerlegt wird, ist die Einstellung der Engine
(``tts_engines.speech_unit_for``). Kanäle liefern nur noch das PCM an ihr Gerät.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from .audio_channels._audio_orchestrator import TTSBuffer, silence_pcm
from .logging_utils import log_message

PCM_SAMPLE_RATE = 48000


def backend_type() -> str:
    """Der aktive LLM-Backend-Typ."""
    from ..state._base import _global_backend_state
    return str(_global_backend_state.get("backend_type") or "llamacpp")


def _gpu_engine_or_empty(engine: str) -> str:
    """GPU-Engines behalten ihren Schlüssel, leichte/Cloud-Engines werden zu ``""`` —
    der SSOT muss trotzdem laufen, damit ein noch geladener GPU-TTS-Container
    beim Wechsel auf Edge/Piper/eSpeak abgeräumt wird."""
    from .tts_engine_manager import GPU_ENGINES
    return engine if engine in GPU_ENGINES else ""


def gpu_tts_combo_fits(engine: str) -> bool:
    """True, wenn die GPU-TTS-Engine neben dem aktiven LLM tatsächlich passt, also
    das Modell eine kalibrierte TTS-Variante im vram-cache hat.

    Leichte/Cloud-Engines brauchen keine GPU und passen immer; ist die Modell-ID
    unbekannt, wird optimistisch True geliefert. Schützt den aufgeschobenen
    TTS-Wechsel: Bei einem GPU-füllenden Modell (z. B. dem 397B) steht die Kombo
    im Cache auf FAIL — ein Wechsel würde das LLM verdrängen, sein Basisprofil neu
    laden und das TTS trotzdem ohne VRAM lassen ("produced no audio")."""
    if not _gpu_engine_or_empty(engine):
        return True
    from .model_vram_cache import is_tts_variant_calibrated
    from .settings import load_settings
    backend = backend_type()
    model_id = str(
        (load_settings() or {}).get("backend_models", {}).get(backend, {}).get("aifred", "")
    )
    if not model_id:
        return True
    return is_tts_variant_calibrated(model_id, engine)


async def _drain_with_session(generator_factory: Callable[[], Generator[str, None, Any]]) -> Any:
    """Den Generator des Engine-Managers im Executor-Thread leer lesen (Meldungen →
    Debug-Konsole) und seinen Rückgabewert liefern. Der Session-Kontext des Aufrufers
    wird in den Thread getragen, damit ``debug()`` in die richtige Session routet."""
    from .debug_bus import _current_session, debug

    caller_session_id = _current_session.get()

    def run() -> Any:
        token = _current_session.set(caller_session_id) if caller_session_id else None
        try:
            generator = generator_factory()
            try:
                while True:
                    debug(f"🔊 {next(generator)}")
            except StopIteration as stop:
                return stop.value
        finally:
            if token is not None:
                _current_session.reset(token)

    return await asyncio.get_running_loop().run_in_executor(None, run)


async def ensure_tts_state(engine: str) -> bool:
    """TTS-Zustand vor der LLM-Inferenz sicherstellen (SSOT: ``ensure_tts_state``).

    True = aufgeschoben (LLM ist ohne TTS geladen, der Aufrufer inferiert zuerst).
    False = TTS ist bereit, das LLM lädt mit dem richtigen Profil."""
    from .tts_engine_manager import ensure_tts_state as manager_ensure

    wanted_gpu = _gpu_engine_or_empty(engine)
    backend = backend_type()
    result = await _drain_with_session(
        lambda: manager_ensure(wanted_tts=wanted_gpu, backend_type=backend, check_defer=True)
    )
    return bool(result.deferred) if result else False


async def force_tts_switch(engine: str) -> None:
    """TTS-Wechsel nach aufgeschobener Inferenz erzwingen: TTS wechseln, dann das LLM
    mit dem TTS-kalibrierten Profil neu starten (blockierend, nacheinander)."""
    from .tts_engine_manager import force_tts_switch as manager_switch

    wanted_gpu = _gpu_engine_or_empty(engine)
    backend = backend_type()
    await _drain_with_session(lambda: manager_switch(wanted_gpu, backend))


async def ensure_tts_ready(engine: str, label: str) -> None:
    """TTS-Engine jetzt synchron bereitstellen — für proaktive Pushes (Vision-Alert,
    Announce-API), die OHNE vorausgehende LLM-Inferenz kommen: TTS-State
    sicherstellen und, falls das große LLM die GPU blockiert, den Wechsel
    LLM → TTS sofort erzwingen (wie bei einer echten Anfrage, nur ohne Antwort)."""
    if await ensure_tts_state(engine):
        log_message(f"[{label}] Proactive TTS switch (no prior inference — loading TTS engine)")
        await force_tts_switch(engine)


async def _run_tts(text: str, agent: str, engine: str, language: str, label: str) -> str | None:
    """Audiodatei aus Text erzeugen, absoluter Pfad oder ``None``.

    Stimme, Tempo und Tonhöhe: Nutzer-Einstellung (je Engine + Agent), dann
    Standard des Agenten, dann der Standard des Systems. Unabhängig vom
    TTS-Schalter des Browsers. Die Bereitschaft der Engine stellen
    ``ensure_tts_state``/``force_tts_switch`` VOR dem Aufruf sicher."""
    from .agent_config import get_tts_voice_default
    from .audio_processing import generate_tts
    from .config import PROJECT_ROOT, PUCK_TTS_FALLBACK_VOICE
    from .settings import load_settings
    from .tts_engines import parse_speed_factor

    # Hat der Agent weder Einstellung noch Standard, erbt er die Stimme von "aifred".
    user_voices = (load_settings() or {}).get("tts_agent_voices_per_engine", {}).get(engine, {})
    user_cfg = user_voices.get(agent) or user_voices.get("aifred", {})
    default_cfg = get_tts_voice_default(agent, engine)
    if not default_cfg.get("voice"):
        default_cfg = get_tts_voice_default("aifred", engine)

    voice = ""
    if isinstance(user_cfg, dict):
        voice = str(user_cfg.get("voice", ""))
    elif isinstance(user_cfg, str):
        voice = user_cfg
    if not voice:
        voice = str(default_cfg.get("voice", PUCK_TTS_FALLBACK_VOICE))

    speed_str = str(default_cfg.get("speed", "1.0"))
    pitch_str = str(default_cfg.get("pitch", "1.0"))
    if isinstance(user_cfg, dict):
        speed_str = str(user_cfg.get("speed") or speed_str)
        pitch_str = str(user_cfg.get("pitch") or pitch_str)

    # Parser = SSOT (geteilt mit dem Browser-Pfad); ungültig → laut abbrechen.
    speed = parse_speed_factor(speed_str)
    pitch = parse_speed_factor(pitch_str)
    if speed is None or pitch is None:
        log_message(
            f"[{label}] Invalid TTS speed/pitch in settings for agent '{agent}' "
            f"(speed='{speed_str}', pitch='{pitch_str}') — no TTS", "error",
        )
        return None

    try:
        result: str | None = await generate_tts(
            text, voice, speed, engine, pitch=pitch, agent=agent, language=language,
        )
    except Exception as exc:  # noqa: BLE001 — jede Engine-Störung endet laut im Log
        log_message(f"[{label}] TTS ({engine}) failed: {exc}", "error")
        return None
    if not result:
        # Kein Rückfall auf eine andere Engine — das Gerät bleibt stumm.
        log_message(
            f"[{label}] TTS engine '{engine}' produced no audio — device stays silent "
            f"(no fallback by design)", "error",
        )
        return None
    if result.startswith("/_upload/"):
        return str(PROJECT_ROOT / "data" / result.removeprefix("/_upload/"))
    return result


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


async def synthesize_pcm(text: str, agent: str, engine: str, language: str, label: str) -> bytes | None:
    """Ein Satz → PCM; ``None`` (laut geloggt), wenn Erzeugung oder Konvertierung scheitert."""
    tts_path = await _run_tts(text, agent, engine, language, label)
    if not tts_path:
        log_message(f"[{label}] TTS failed", "error")
        return None
    try:
        pcm = await _convert_to_pcm(tts_path, label)
    finally:
        Path(tts_path).unlink(missing_ok=True)
    if not pcm:
        log_message(f"[{label}] TTS conversion failed", "error")
        return None
    return pcm


async def start_speech_stream(
    segments: "list[str | int]", agent: str, engine: str, language: str, label: str,
) -> TTSBuffer | None:
    """Den ersten Satz erzeugen und den Puffer zurückgeben; die übrigen Segmente
    (Texte als ``str``, Stille als ``int`` ms) erzeugt ein Hintergrund-Task und hängt
    sie an, während der Abnehmer schon sendet. ``None``, wenn nichts zu sprechen ist
    oder der erste Satz scheitert."""
    if not segments:
        log_message(f"[{label}] nothing to speak", "warning")
        return None
    first = segments[0]
    if not isinstance(first, str):
        raise ValueError("a speech stream starts with a sentence, not with silence")
    pcm = await synthesize_pcm(first, agent, engine, language, label)
    if pcm is None:
        return None
    buffer = TTSBuffer(growing=True)
    buffer.append(pcm)
    rest = segments[1:]
    if rest:
        buffer.producer = asyncio.create_task(
            _produce_speech(buffer, rest, agent, engine, language, label),
            name=f"speech-producer-{label}",
        )
    else:
        buffer.close()
    return buffer


async def _produce_speech(
    buffer: TTSBuffer, segments: "list[str | int]", agent: str, engine: str, language: str, label: str,
) -> None:
    """Erzeuger des satzweisen Streamings: hängt Sprache und Stille an den Puffer.
    Scheitert ein Satz, wird der Strom ohne Ende-Ton geschlossen (laut geloggt)."""
    try:
        for segment in segments:
            if isinstance(segment, int):
                buffer.append(silence_pcm(segment))
                continue
            pcm = await synthesize_pcm(segment, agent, engine, language, label)
            if pcm is None:
                buffer.close(failed=True)
                return
            buffer.append(pcm)
        buffer.close()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — der Strom muss in jedem Fall sauber enden
        log_message(f"[{label}] speech producer error: {exc!r}", "error")
        buffer.close(failed=True)
