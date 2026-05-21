"""Edge TTS — Microsoft cloud, always-on, used as fallback."""
from __future__ import annotations


from .base import TTSEngine


class EdgeEngine(TTSEngine):
    key = "edge"
    label_short = "Edge"
    runs_in_container = False
    needs_gpu = False
    # Edge respects the rate parameter natively — no ffmpeg post.
    needs_speed_postprocess = False
    suitable_for_channels = True

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import EDGE_TTS_VOICES
        return dict(EDGE_TTS_VOICES)

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback

    # Edge's generate_speech is async-only with a different signature
    # (`generate_speech_edge(text, voice, rate)`); for now its dispatch
    # stays in audio_processing.generate_tts. Phase-3 migration only
    # consolidates the sync-friendly engines.
