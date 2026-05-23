"""DashScope Qwen3-TTS — cloud streaming TTS, no local GPU."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class DashScopeEngine(TTSEngine):
    key = "dashscope"
    label_short = "DashScope"
    runs_in_container = False
    needs_gpu = False
    needs_speed_postprocess = True
    supports_language = True
    # Cloud engine needs an API key — channel devices don't always have
    # that wired up, so we keep it out of the FreeEcho-style dropdowns.
    suitable_for_channels = False
    display_order = 50

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import DASHSCOPE_VOICES
        return dict(DASHSCOPE_VOICES)

    def get_voices(self) -> dict[str, str]:
        # DashScope has a fixed catalogue; no live discovery endpoint
        # AIfred currently uses, so we just return the static mapping.
        return self.voices_fallback

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_dashscope
        return generate_speech_dashscope(text, speed, voice, language)
