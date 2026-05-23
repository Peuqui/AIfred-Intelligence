"""MOSS-TTS — zero-shot voice cloning, batch-after-bubble rendering."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class MOSSEngine(TTSEngine):
    key = "moss"
    label_short = "MOSS-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    suitable_for_channels = True
    calibration_vram_reserve_mb = 0  # static allocation, no peak above idle
    display_order = 40

    image_name = "moss-tts-1.7b"
    compose_subdir = "moss-tts"

    @property
    def service_url(self) -> str:
        return "http://localhost:5055"

    @property
    def voices_fallback(self) -> dict[str, str]:
        return {
            "AIfred":   "AIfred",
            "Salomo":   "Salomo",
            "Sokrates": "Sokrates",
        }

    def get_voices(self) -> dict[str, str]:
        import requests
        try:
            r = requests.get(f"{self.service_url}/voices", timeout=5)
            if r.ok:
                return {name: name for name in r.json().get("voices", [])}
        except (requests.RequestException, ValueError) as e:
            print(f"⚠️ Failed to fetch MOSS-TTS voices: {e}")
        return {}

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
