"""MOSS-TTS — zero-shot voice cloning, batch-after-bubble rendering."""
from __future__ import annotations


from .base import TTSEngine


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

    @property
    def voices_fallback(self) -> dict[str, str]:
        return {
            "AIfred":   "AIfred",
            "Salomo":   "Salomo",
            "Sokrates": "Sokrates",
        }

    def get_voices(self) -> dict[str, str]:
        data = self._fetch_voices_json()
        return {name: name for name in data.get("voices", [])} if data else {}

    def is_running(self) -> bool:
        import requests
        from ..config import TTS_HEALTH_TIMEOUT_S
        try:
            r = requests.get(f"{self.service_url}/health", timeout=TTS_HEALTH_TIMEOUT_S)
            if not (r.ok and r.json().get("model_loaded")):
                return False
            data = r.json()
            # MOSS-specific signature: has "voices" list AND "sample_rate"
            # (XTTS lacks sample_rate, Qwen3 lacks voices on /health).
            return "voices" in data and "sample_rate" in data
        except (OSError, ValueError):
            return False

    def _start_local(self) -> tuple[bool, str]:
        from ..process_utils import start_moss_container
        return start_moss_container(self.gpu_uuid)

    def _stop_local(self) -> tuple[bool, str]:
        from ..process_utils import stop_moss_container
        return stop_moss_container()

    def _ensure_ready_local(self, timeout: int | None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_moss_ready
        return ensure_moss_ready(timeout=timeout or self.startup_timeout_s, gpu_uuid=self.gpu_uuid)

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
