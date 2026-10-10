"""Fish Audio S2 Pro — 5B Dual-AR, voice cloning, 80+ languages.

License: Fish Audio Research License — research/non-commercial only.
"""
from __future__ import annotations

from typing import Any

from .base import TTSEngine


class FishSpeechEngine(TTSEngine):
    key = "fishspeech"
    label_short = "Fish-Speech"
    runs_in_container = True
    needs_gpu = True
    # Speed isn't a native parameter for Fish-Speech — like Qwen3 / MOSS,
    # the audio rate is adjusted via ffmpeg post-processing.
    needs_speed_postprocess = True
    display_order = 30

    image_name = "fish-speech-s2-pro"
    compose_subdir = "fish-speech"

    default_port = 5053

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Voices come from the shared docker/tts/voices/ tree (mounted at /app/references).
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
        docker/tts/voices/ tree is the source of truth, and
        the static voices_fallback mirrors its contents."""
        return dict(self.voices_fallback)

    def is_running(self) -> bool:
        import requests
        from ..config import TTS_HEALTH_TIMEOUT_S
        try:
            # Fish-Speech's /v1/health just returns 200 OK with an empty
            # body once the model finished loading — that's the readiness
            # signal we treat as "model_loaded=true".
            r = requests.get(f"{self.service_url}/v1/health", timeout=TTS_HEALTH_TIMEOUT_S)
            return r.ok
        except (OSError, ValueError):
            return False

    def _start_local(self) -> tuple[bool, str]:
        from ..process_utils import start_fishspeech_container
        return start_fishspeech_container()

    def _stop_local(self) -> tuple[bool, str]:
        from ..process_utils import stop_fishspeech_container
        return stop_fishspeech_container()

    def _ensure_ready_local(self, timeout: int | None) -> tuple[bool, str, str]:
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
    ) -> str:
        """Fish Audio S2 Pro — voice cloning via server-side reference_id.
        The container reads ``/app/references/<voice>/<voice>.wav`` +
        ``.lab`` transcript itself (we mount the shared docker/tts/voices/
        read-only at that path), so the wire payload is just the voice
        id — no per-request base64 round-trip of a 1 MB WAV. Speed/pitch
        are post-processed centrally via ffmpeg."""
        return self._synthesize_via_http(
            "/v1/tts",
            {"text": text, "format": "wav", "reference_id": voice, "normalize": True, "streaming": False},
            "wav",
        )

    def calibration_setup(self, debug: Any) -> bool:
        # Same pattern as Qwen3: do NOT load the container during
        # calibration. The reserve is the burn-in peak (resolve_tts_reserve
        # in tts_stress_burnin.py), which already covers idle + growth;
        # loading the container would count its idle footprint twice.
        debug(
            f"   🔊 {self.label_short}: container not loaded — "
            f"calibration reserves the burn-in peak"
        )
        return True

    def calibration_teardown(self, debug: Any) -> None:
        # Container was not started in calibration_setup — nothing to
        # stop. The pre-calibration cleanup (Step 0 in
        # _calibrate_llamacpp) already cleared any leftovers.
        pass
