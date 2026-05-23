"""Fish Audio S2 Pro — 5B Dual-AR, voice cloning, 80+ languages.

License: Fish Audio Research License — research/non-commercial only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

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
    display_order = 30

    image_name = "fish-speech-s2-pro"

    # Fish grows dynamically during generate() — measured ~19.6 GB idle
    # → ~23.5 GB peak on the V100, then stable. Same pattern as Qwen3:
    # do NOT load the container during calibration, just subtract the
    # fixed 26 GB reserve from the TTS GPU. The reserve covers the peak
    # with a ~2.5 GB headroom.
    @property
    def calibration_vram_reserve_mb(self) -> int:
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

    def calibration_setup(self, debug: Any) -> bool:
        # Same pattern as Qwen3: do NOT load the container during
        # calibration. The fixed 26 GB reserve covers idle (19.6 GB) +
        # peak growth (23.5 GB) with headroom. Loading would double-count
        # the idle footprint against the reserve and squeeze the V100
        # out of the LLM plan entirely.
        debug(
            f"   🔊 {self.label_short}: reserving "
            f"{self.calibration_vram_reserve_mb} MB on TTS GPU "
            f"(container not loaded)"
        )
        return True

    def calibration_teardown(self, debug: Any) -> None:
        # Container was not started in calibration_setup — nothing to
        # stop. The pre-calibration cleanup (Step 0 in
        # _calibrate_llamacpp) already cleared any leftovers.
        pass
