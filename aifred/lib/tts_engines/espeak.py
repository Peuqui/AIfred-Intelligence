"""eSpeak — minimal robotic offline TTS."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class EspeakEngine(TTSEngine):
    key = "espeak"
    label_short = "eSpeak"
    runs_in_container = False
    needs_gpu = False
    # eSpeak's -s flag controls speed natively.
    needs_speed_postprocess = False
    suitable_for_channels = True
    display_order = 70

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import ESPEAK_VOICES
        return dict(ESPEAK_VOICES)

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_espeak
        return generate_speech_espeak(text, speed, voice)
