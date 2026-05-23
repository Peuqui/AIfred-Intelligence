"""Fish Audio S2 Pro — 5B Dual-AR, voice cloning, 80+ languages.

License: Fish Audio Research License — research/non-commercial only.
"""
from __future__ import annotations

import os
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
    compose_subdir = "fish-speech"

    # Fish grows dynamically during generate() — measured ~19.6 GB idle
    # → ~23.5 GB peak on the V100, then stable. S2 Pro is officially
    # "requires at least 24 GB"; we pick 26 GB so the LLM can't creep
    # into the peak headroom while the container is idle. Tunable via
    # env FISH_SPEECH_VRAM_RESERVE_MB.
    @property
    def calibration_vram_reserve_mb(self) -> int:
        return int(os.environ.get("FISH_SPEECH_VRAM_RESERVE_MB", "26624"))

    @property
    def service_url(self) -> str:
        return "http://localhost:5053"

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Voices ship with the container in docker/tts/fish-speech/voices/.
        # The wav+txt pair convention is the same as MOSS / Qwen3.
        return {
            "AIfred":   "AIfred",
            "HAL9000":  "HAL9000",
            "Salomo":   "Salomo",
            "Sokrates": "Sokrates",
        }

    def get_voices(self) -> dict[str, str]:
        """Fish-Speech uses static reference files from /app/references —
        no live discovery endpoint we want to use. The on-disk
        docker/tts/fish-speech/voices/ tree is the source of truth, and
        the static voices_fallback mirrors its contents."""
        return dict(self.voices_fallback)

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
