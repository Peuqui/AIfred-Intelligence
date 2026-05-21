"""Qwen3-TTS local container — streaming voice cloning on a single HBM GPU."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import TTSEngine


class Qwen3LocalEngine(TTSEngine):
    key = "qwen3local"
    label_short = "Qwen3-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    suitable_for_channels = True

    # qwen-tts allocates KV-cache + decoder buffers dynamically during
    # generate() — idle ~5 GB, long-bubble peak ~7 GB. The LLM calibration
    # has to plan permanently around the peak, otherwise a long TTS call
    # in production would OOM the V100. Adjustable via env var; see
    # config.QWEN3_TTS_VRAM_RESERVE_MB.
    @property
    def calibration_vram_reserve_mb(self) -> int:
        from ..config import QWEN3_TTS_VRAM_RESERVE_MB
        return QWEN3_TTS_VRAM_RESERVE_MB

    @property
    def service_url(self) -> str:
        from ..config import QWEN3_TTS_SERVICE_URL
        return QWEN3_TTS_SERVICE_URL

    @property
    def docker_compose_path(self) -> Path:
        from ..config import QWEN3_TTS_DOCKER_COMPOSE_PATH
        return Path(QWEN3_TTS_DOCKER_COMPOSE_PATH)

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
        from ..config import QWEN3_TTS_VOICES_FALLBACK
        return dict(QWEN3_TTS_VOICES_FALLBACK)

    def get_voices(self) -> dict[str, str]:
        from ..config import get_qwen3local_voices
        return get_qwen3local_voices()

    def is_running(self) -> bool:
        import requests
        try:
            r = requests.get(f"{self.service_url}/health", timeout=2)
            return bool(r.ok and r.json().get("model_loaded"))
        except (OSError, ValueError):
            return False

    def start(self) -> tuple[bool, str]:
        from ..process_utils import start_qwen3local_container
        return start_qwen3local_container()

    def stop(self) -> tuple[bool, str]:
        from ..process_utils import stop_qwen3local_container
        return stop_qwen3local_container()

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_qwen3local_ready
        return ensure_qwen3local_ready(timeout=timeout or 240)

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_qwen3local
        return generate_speech_qwen3local(text, speed, voice, language)

    def calibration_setup(self, debug: Any) -> bool:
        # No test-inference any more — the calibration_vram_reserve_mb
        # is the safer source of truth (qwen-tts has no graceful
        # degradation if VRAM gets tight mid-generate, so we reserve
        # the peak unconditionally instead of trying to measure it).
        ok, msg, _device = self.ensure_ready()
        if ok:
            debug(f"   🔊 {msg}")
        return ok

    def calibration_teardown(self, debug: Any) -> None:
        self.stop()
        debug(f"   🔊 {self.label_short} container stopped")
