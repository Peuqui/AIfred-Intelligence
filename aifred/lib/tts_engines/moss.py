"""MOSS-TTS — zero-shot voice cloning, batch-after-bubble rendering."""
from __future__ import annotations

from typing import Any

from .base import TTSEngine, shared_voice_names


class MOSSEngine(TTSEngine):
    key = "moss"
    default_speech_unit = "whole"
    label_short = "MOSS-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    display_order = 40

    image_name = "moss-tts-1.7b"
    compose_subdir = "moss-tts"

    default_port = 5055
    startup_timeout_s = 180
    restart_when_on_cpu = True

    @property
    def voices_fallback(self) -> dict[str, str]:
        return {name: name for name in shared_voice_names()}

    def get_voices(self) -> dict[str, str]:
        data = self._fetch_voices_json()
        return {name: name for name in data.get("voices", [])} if data else {}

    def _model_ready(self, health: dict[str, Any]) -> bool:
        # MOSS signature: "voices" list AND "sample_rate" (XTTS lacks sample_rate,
        # Qwen3 lacks voices on /health).
        return super()._model_ready(health) and "voices" in health and "sample_rate" in health

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """MOSS-TTS zero-shot voice cloning. Same /tts shape as XTTS.
        Speed/pitch are post-processed centrally via ffmpeg."""
        return self._synthesize_via_http(
            "/tts", {"text": text, "speaker": voice, "language": language}, "ogg",
        )
