"""Fish Audio S2 Pro — 5B Dual-AR, voice cloning, 80+ languages.

License: Fish Audio Research License — research/non-commercial only.
"""
from __future__ import annotations

from typing import Any

from .base import TTSEngine, shared_voice_names


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
    health_path = "/v1/health"
    startup_timeout_s = 600

    @property
    def voices_fallback(self) -> dict[str, str]:
        # The shared voice tree is mounted at /app/references (wav+txt pairs).
        return {name: name for name in shared_voice_names()}

    def get_voices(self) -> dict[str, str]:
        """Fish-Speech uses static reference files from /app/references —
        no live discovery endpoint we want to use. The on-disk
        voice tree is the source of truth (``shared_voice_names``)."""
        return dict(self.voices_fallback)

    def _model_ready(self, health: dict[str, Any]) -> bool:
        # /v1/health answers 200 with an empty body once the model is loaded —
        # that alone is the readiness signal.
        return True

    def _device(self, health: dict[str, Any]) -> str:
        # Upstream /v1/health does not report a device; assume the card the
        # container was pinned to.
        from ..process_utils import get_tts_gpu_uuid
        return "cuda:0" if get_tts_gpu_uuid() else "cpu"

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
