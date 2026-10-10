"""XTTS v2 (Coqui) — voice cloning + built-in speakers, runs as Docker container."""
from __future__ import annotations

from typing import Any

from ..base import TTSEngine, shared_voice_names


class XTTSEngine(TTSEngine):
    key = "xtts"
    label_short = "XTTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True

    # XTTS allocates statically at model load — no dynamic peak above
    # idle, so the base default (no calibration VRAM reserve) applies.
    display_order = 20
    in_default_escalation = True

    image_name = "xtts-rtx8000"

    default_port = 5051
    startup_timeout_s = 60

    @property
    def voices_fallback(self) -> dict[str, str]:
        # While the container is down: the cloned voices of the shared tree
        # first (★ prefix), then a subset of the speakers bundled with the
        # model — live discovery in get_voices() returns all 58 of them.
        return {f"★ {name}": name for name in shared_voice_names()} | {
            "Claribel Dervla":  "Claribel Dervla",
            "Daisy Studious":   "Daisy Studious",
            "Gracie Wise":      "Gracie Wise",
            "Tammie Ema":       "Tammie Ema",
            "Alison Dietlinde": "Alison Dietlinde",
        }

    def get_voices(self) -> dict[str, str]:
        """Fetch current XTTS voices from the container. Custom voices
        are prefixed with "★ " in the display name so the user can tell
        them apart from the 58 bundled speakers. Returns {} when the
        service is unreachable; caller falls back to ``voices_fallback``."""
        data = self._fetch_voices_json()
        if not data:
            return {}
        voices = {f"★ {name}": name for name in data.get("custom", [])}
        voices.update({name: name for name in data.get("builtin", [])})
        return voices

    def _model_ready(self, health: dict[str, Any]) -> bool:
        # The "custom_voices" field only XTTS' /health returns tells it apart
        # from MOSS / Qwen3.
        return super()._model_ready(health) and "custom_voices" in health

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Render ``text`` via the XTTS container. Speed/pitch are
        ignored here — the central post-processor applies them with
        ffmpeg afterwards (see ``needs_speed_postprocess=True``)."""
        return self._synthesize_via_http(
            "/tts", {"text": text, "speaker": voice, "language": language}, "ogg",
        )

    def calibration_setup(self, debug: Any) -> bool:
        # Do NOT load the container during calibration — same contract as
        # qwen3local/fishspeech. The peak VRAM is the single source of
        # truth from the stress burn-in cache (resolve_tts_reserve); the
        # calibration plans the TTS GPU around that reserve while the
        # container stays cold. Loading it here would double-count the
        # idle footprint against the reserve and push LLM layers off the
        # card. The burn-in itself starts/stops the container on a cache
        # miss — calibration never needs the live service.
        debug(
            f"   🔊 {self.label_short}: reserving via stress burn-in cache "
            f"(container not loaded)"
        )
        return True
