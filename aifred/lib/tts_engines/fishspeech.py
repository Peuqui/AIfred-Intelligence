"""Fish Audio S2 Pro — 5B Dual-AR, voice cloning, 80+ languages.

License: Fish Audio Research License — research/non-commercial only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import TTSEngine


class FishSpeechEngine(TTSEngine):
    key = "fishspeech"
    label_short = "Fish-Speech"
    runs_in_container = True
    needs_gpu = True
    # Speed isn't a native parameter for Fish-Speech — like Qwen3 / MOSS,
    # the audio rate is adjusted via ffmpeg post-processing.
    needs_speed_postprocess = True
    # Lizenz steht der kommerziellen Nutzung im Weg; bewusst nicht in
    # Channel-Dropdowns aufnehmen, damit Endgeräte (FreeEcho.2) den
    # Engine nicht versehentlich anbieten.
    suitable_for_channels = False

    @property
    def calibration_vram_reserve_mb(self) -> int:
        # Upstream calls for "at least 24 GB" — we reserve the upper end
        # permanently so a long generation can't OOM the V100.
        from ..config import FISH_SPEECH_VRAM_RESERVE_MB
        return FISH_SPEECH_VRAM_RESERVE_MB

    @property
    def service_url(self) -> str:
        from ..config import FISH_SPEECH_SERVICE_URL
        return FISH_SPEECH_SERVICE_URL

    @property
    def docker_compose_path(self) -> Path:
        from ..config import FISH_SPEECH_DOCKER_COMPOSE_PATH
        return Path(FISH_SPEECH_DOCKER_COMPOSE_PATH)

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import FISH_SPEECH_VOICES_FALLBACK
        return dict(FISH_SPEECH_VOICES_FALLBACK)

    def get_voices(self) -> dict[str, str]:
        from ..config import get_fishspeech_voices
        return get_fishspeech_voices()

    def is_running(self) -> bool:
        import requests
        try:
            # Fish-Speech's /v1/health just returns 200 OK with an empty
            # body once the model finished loading — that's the readiness
            # signal we treat as "model_loaded=true".
            r = requests.get(f"{self.service_url}/v1/health", timeout=2)
            return r.ok
        except (OSError, ValueError):
            return False

    def start(self) -> tuple[bool, str]:
        from ..process_utils import start_fishspeech_container
        return start_fishspeech_container()

    def stop(self) -> tuple[bool, str]:
        from ..process_utils import stop_fishspeech_container
        return stop_fishspeech_container()

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_fishspeech_ready
        # 600 s default — first start has to pull ~8 GB of weights from
        # HuggingFace before the model can load.
        return ensure_fishspeech_ready(timeout=timeout or 600)

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_fishspeech
        return generate_speech_fishspeech(text, speed, voice, language)
