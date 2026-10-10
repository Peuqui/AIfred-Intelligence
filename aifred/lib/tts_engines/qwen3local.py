"""Qwen3-TTS local container — streaming voice cloning on a single HBM GPU."""
from __future__ import annotations

from typing import Any

from .base import TTSEngine, shared_voice_names


class Qwen3LocalEngine(TTSEngine):
    key = "qwen3local"
    label_short = "Qwen3-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    display_order = 10

    image_name = "qwen3-tts-1.7b-base"
    compose_subdir = "qwen3-tts"

    default_port = 5052
    startup_timeout_s = 240
    max_parallel_requests = 1

    @property
    def language_map(self) -> dict[str, str]:
        # Qwen3-TTS-12Hz-1.7B-Base supports these 10 languages by full name.
        return {
            "de": "German",   "en": "English", "zh": "Chinese", "ja": "Japanese",
            "ko": "Korean",   "fr": "French",  "ru": "Russian", "pt": "Portuguese",
            "es": "Spanish",  "it": "Italian",
        }

    @property
    def voices_fallback(self) -> dict[str, str]:
        return {name: name for name in shared_voice_names()}

    def get_voices(self) -> dict[str, str]:
        """One entry per <name>.wav in /app/voices/ inside the container —
        the Qwen3 container exposes them via /voices, identical shape to
        MOSS. Returns {} on any failure (caller falls back)."""
        data = self._fetch_voices_json()
        return {name: name for name in data.get("voices", [])} if data else {}

    def is_running(self) -> bool:
        import requests
        from ..config import TTS_HEALTH_TIMEOUT_S
        try:
            r = requests.get(f"{self.service_url}/health", timeout=TTS_HEALTH_TIMEOUT_S)
            return bool(r.ok and r.json().get("model_loaded"))
        except (OSError, ValueError):
            return False

    def _start_local(self) -> tuple[bool, str]:
        from ..process_utils import start_qwen3local_container
        return start_qwen3local_container(self.gpu_uuid)

    def _stop_local(self) -> tuple[bool, str]:
        from ..process_utils import stop_qwen3local_container
        return stop_qwen3local_container()

    def _ensure_ready_local(self, timeout: int | None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_qwen3local_ready
        return ensure_qwen3local_ready(timeout=timeout or self.startup_timeout_s, gpu_uuid=self.gpu_uuid)

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Qwen3-TTS — same /tts shape as XTTS/MOSS but with the full
        language name instead of the ISO code. Reference voices are
        warmed at container startup from /app/voices/*.wav. Speed/pitch
        are post-processed centrally via ffmpeg. WAV end-to-end (cleaner
        for downstream ffmpeg adjustments, and the Puck channel prefers WAV)."""
        qwen3_lang = self.language_map.get(language.lower(), "English")
        return self._synthesize_via_http(
            "/tts", {"text": text, "speaker": voice, "language": qwen3_lang}, "wav",
        )

    def calibration_setup(self, debug: Any) -> bool:
        # Do NOT load the container during calibration. The reserve is the
        # burn-in peak (resolve_tts_reserve in tts_stress_burnin.py, idle +
        # growth + headroom) — loading the container would count its idle
        # footprint twice and crowd LLM layers off the card. With the
        # container left cold we subtract exactly the measured reserve.
        debug(
            f"   🔊 {self.label_short}: container not loaded — "
            f"calibration reserves the burn-in peak"
        )
        return True

    def calibration_teardown(self, debug: Any) -> None:
        # Container was not started in calibration_setup, so nothing
        # to stop here. The pre-calibration cleanup (Step 0 in
        # _calibrate_llamacpp) has already stopped any leftover.
        pass
