"""Piper TTS — offline neural voices, runs as a Python subprocess."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class PiperEngine(TTSEngine):
    key = "piper"
    label_short = "Piper"
    runs_in_container = False
    needs_gpu = False
    # Piper applies its --length-scale internally — no ffmpeg needed.
    needs_speed_postprocess = False
    suitable_for_channels = True

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import PIPER_VOICES
        return dict(PIPER_VOICES)

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
        from ..audio_processing import generate_speech_piper
        return generate_speech_piper(text, speed, voice)
