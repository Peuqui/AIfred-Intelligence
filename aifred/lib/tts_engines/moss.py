"""MOSS-TTS — zero-shot voice cloning, batch-after-bubble rendering."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import TTSEngine


class MOSSEngine(TTSEngine):
    key = "moss"
    label_short = "MOSS-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    suitable_for_channels = True
    calibration_vram_reserve_mb = 0  # static allocation, no peak above idle

    @property
    def service_url(self) -> str:
        from ..config import MOSS_TTS_SERVICE_URL
        return MOSS_TTS_SERVICE_URL

    @property
    def docker_compose_path(self) -> Path:
        from ..config import MOSS_TTS_DOCKER_COMPOSE_PATH
        return Path(MOSS_TTS_DOCKER_COMPOSE_PATH)

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import MOSS_TTS_VOICES_FALLBACK
        return dict(MOSS_TTS_VOICES_FALLBACK)

    def get_voices(self) -> dict[str, str]:
        from ..config import get_moss_voices
        return get_moss_voices() or {}

    def is_installed(self) -> bool:
        return self.docker_compose_path.exists()

    def is_running(self) -> bool:
        import requests
        try:
            r = requests.get(f"{self.service_url}/health", timeout=2)
            if not (r.ok and r.json().get("model_loaded")):
                return False
            data = r.json()
            # MOSS-specific signature: has "voices" list AND "sample_rate"
            # (XTTS lacks sample_rate, Qwen3 lacks voices on /health).
            return "voices" in data and "sample_rate" in data
        except (OSError, ValueError):
            return False

    def start(self) -> tuple[bool, str]:
        from ..process_utils import start_moss_container
        return start_moss_container()

    def stop(self) -> tuple[bool, str]:
        from ..process_utils import stop_moss_container
        return stop_moss_container()

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_moss_ready
        return ensure_moss_ready(timeout=timeout or 180)

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_moss
        return generate_speech_moss(text, speed, voice, language)
