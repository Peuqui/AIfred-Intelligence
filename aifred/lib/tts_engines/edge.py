"""Edge TTS — Microsoft cloud, always-on, used as fallback."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class EdgeEngine(TTSEngine):
    key = "edge"
    label_short = "Edge"
    runs_in_container = False
    needs_gpu = False
    # Edge respects the rate parameter natively — no ffmpeg post.
    needs_speed_postprocess = False
    suitable_for_channels = True
    display_order = 80

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import EDGE_TTS_VOICES
        return dict(EDGE_TTS_VOICES)

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback

    async def generate_speech_async(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        """Edge TTS — native async via edge-tts library. ``voice`` here
        is the display name; we map it through ``voices_fallback`` to
        the Microsoft voice id (``de-DE-KatjaNeural`` etc.). Speed is
        applied natively via the rate string (``+25%`` = 25% faster);
        pitch is not supported by Edge and gets ignored at this layer
        (the central ffmpeg post-processor handles pitch if needed —
        but for Edge we keep ``needs_speed_postprocess=False``)."""
        import concurrent.futures
        import os
        from ..audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            _edge_tts_sync,
            TTS_AUDIO_DIR,
        )
        from ..logging_utils import log_message

        # Map display name → Microsoft voice id (Cherry → de-DE-KatjaNeural).
        voice_id = self.voices_fallback.get(voice, "de-DE-KatjaNeural")

        # Edge TTS rate format: "+X%" / "-X%". 1.0 → "+0%", 1.25 → "+25%".
        rate_pct = round((speed - 1.0) * 100)
        rate = f"{rate_pct:+d}%"

        if not text or len(text.strip()) < 1:
            log_message("⚠️ Edge TTS: Empty text, skipping")
            return None

        log_message(f"🎤 Edge TTS: voice={voice_id}, rate={rate}, text_length={len(text)}")
        filename = _generate_tts_filename("mp3")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            # The upstream edge-tts library creates its own event loop, so
            # we run it in a dedicated thread to avoid clashing with Reflex.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_edge_tts_sync, text, voice_id, rate, output_file)
                success = future.result(timeout=None)
            if not success:
                log_message("❌ Edge TTS: Thread execution failed")
                return None
            if _validate_audio_output(output_file):
                size = os.path.getsize(output_file)
                log_message(f"✅ Edge TTS: Audio saved → {output_file} ({size} bytes)")
                return f"/_upload/tts_audio/{filename}"
            log_message(f"❌ Edge TTS: File missing or too small at {output_file}")
            return None
        except Exception as e:
            log_message(f"❌ Edge TTS Exception: {type(e).__name__}: {e}")
            import traceback
            log_message(f"Edge TTS Traceback: {traceback.format_exc()}")
            return None
