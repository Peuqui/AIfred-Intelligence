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

    def is_installed(self) -> bool:
        return self.docker_compose_path.exists()

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
        # Do NOT load the container during calibration. The fixed
        # ``calibration_vram_reserve_mb`` (7.5 GB, covers idle + peak
        # growth) IS the source of truth — loading the container would
        # double-count: the container's idle footprint (~5 GB) plus the
        # full reserve (7.5 GB) would subtract ~12.5 GB from the V100,
        # crowding LLM layers off the card. With the container left
        # cold, the V100 is free and we subtract exactly the reserve.
        #
        # At runtime, the container's idle + growth must stay below the
        # reserve, otherwise the LLM context fill (which can creep into
        # the reserved area) will collide with TTS allocations and OOM.
        debug(
            f"   🔊 {self.label_short}: reserving "
            f"{self.calibration_vram_reserve_mb} MB on TTS GPU "
            f"(container not loaded)"
        )
        return True

    def calibration_teardown(self, debug: Any) -> None:
        # Container was not started in calibration_setup, so nothing
        # to stop here. The pre-calibration cleanup (Step 0 in
        # _calibrate_llamacpp) has already stopped any leftover.
        pass
