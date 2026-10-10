"""Edge TTS — Microsoft cloud, always-on, used as fallback."""
from __future__ import annotations


from ..base import TTSEngine, TTSFailure


class EdgeEngine(TTSEngine):
    key = "edge"
    label_short = "Edge"
    runs_in_container = False
    needs_gpu = False
    # Edge respects the rate parameter natively — no ffmpeg post.
    needs_speed_postprocess = False
    display_order = 80
    in_default_escalation = True
    cloud = True

    # display name → Microsoft Neural Voice id. Static catalogue —
    # Edge has no live discovery endpoint we use.
    _VOICES: dict[str, str] = {
        # Deutschland (de-DE)
        "Deutsch (Katja)":       "de-DE-KatjaNeural",
        "Deutsch (Amala)":       "de-DE-AmalaNeural",
        "Deutsch (Seraphina)":   "de-DE-SeraphinaMultilingualNeural",
        "Deutsch (Conrad)":      "de-DE-ConradNeural",
        "Deutsch (Killian)":     "de-DE-KillianNeural",
        "Deutsch (Florian)":     "de-DE-FlorianMultilingualNeural",
        # Österreich (de-AT)
        "Österreich (Ingrid)":   "de-AT-IngridNeural",
        "Österreich (Jonas)":    "de-AT-JonasNeural",
        # Schweiz (de-CH)
        "Schweiz (Leni)":        "de-CH-LeniNeural",
        "Schweiz (Jan)":         "de-CH-JanNeural",
        # Englisch
        "Englisch (Jenny)":      "en-US-JennyNeural",
        "Englisch (Guy)":        "en-US-GuyNeural",
        # Weitere Sprachen
        "Französisch (Denise)":  "fr-FR-DeniseNeural",
        "Spanisch (Elvira)":     "es-ES-ElviraNeural",
    }

    @property
    def voices_fallback(self) -> dict[str, str]:
        return dict(self._VOICES)

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback

    async def generate_speech_async(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Edge TTS — native async via edge-tts library. ``voice`` here
        is the display name; we map it through ``voices_fallback`` to
        the Microsoft voice id (``de-DE-KatjaNeural`` etc.). Speed is
        applied natively via the rate string (``+25%`` = 25% faster);
        pitch is not supported by Edge and gets ignored at this layer
        (the central ffmpeg post-processor handles pitch if needed —
        but for Edge we keep ``needs_speed_postprocess=False``)."""
        import concurrent.futures
        import os
        from ...audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            _edge_tts_sync,
            TTS_AUDIO_DIR,
        )
        from ...logging_utils import log_message

        # Map display name → Microsoft voice id (Cherry → de-DE-KatjaNeural).
        voice_id = self.voices_fallback.get(voice, "de-DE-KatjaNeural")

        # Edge TTS rate format: "+X%" / "-X%". 1.0 → "+0%", 1.25 → "+25%".
        rate_pct = round((speed - 1.0) * 100)
        rate = f"{rate_pct:+d}%"

        if not text.strip():
            raise TTSFailure("software", "Edge TTS called with empty text")

        log_message(f"🎤 Edge TTS: voice={voice_id}, rate={rate}, text_length={len(text)}")
        filename = _generate_tts_filename("mp3")
        output_file = str(TTS_AUDIO_DIR / filename)

        # The upstream edge-tts library creates its own event loop, so
        # we run it in a dedicated thread to avoid clashing with Reflex.
        from edge_tts.exceptions import EdgeTTSException
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(_edge_tts_sync, text, voice_id, rate, output_file).result()
        except EdgeTTSException as exc:
            raise TTSFailure("engine", f"Edge TTS: {type(exc).__name__}: {exc}") from exc
        except OSError as exc:  # aiohttp connection errors are OSError subclasses
            raise TTSFailure("unreachable", f"Edge TTS: {type(exc).__name__}: {exc}") from exc
        if not _validate_audio_output(output_file):
            raise TTSFailure("engine", f"Edge TTS file missing or too small at {output_file}")
        size = os.path.getsize(output_file)
        log_message(f"✅ Edge TTS: Audio saved → {output_file} ({size} bytes)")
        return f"/_upload/tts_audio/{filename}"
