"""XTTS v2 (Coqui) — voice cloning + built-in speakers, runs as Docker container."""
from __future__ import annotations

from typing import Any

from .base import TTSEngine


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

    image_name = "xtts-rtx8000"

    default_port = 5051

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Static fallback list when the /voices endpoint isn't reachable.
        # Custom-cloned voices first (★ prefix), then a small subset of
        # the bundled built-in speakers — enough to keep the UI usable
        # while the container is down. Live discovery in get_voices()
        # returns the full bundled list (58 speakers).
        return {
            "★ AIfred":         "AIfred",
            "★ Salomo":         "Salomo",
            "★ Sokrates":       "Sokrates",
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

    def is_running(self) -> bool:
        import requests
        try:
            r = requests.get(f"{self.service_url}/health", timeout=2)
            if not (r.ok and r.json().get("model_loaded")):
                return False
            # XTTS-specific: distinguish from MOSS/Qwen3 by the
            # "custom_voices" field that only XTTS' /health returns.
            return "custom_voices" in r.json()
        except (OSError, ValueError):
            return False

    def _start_local(self) -> tuple[bool, str]:
        from ..process_utils import start_xtts_container
        return start_xtts_container()

    def _stop_local(self) -> tuple[bool, str]:
        from ..process_utils import stop_xtts_container
        return stop_xtts_container()

    def _ensure_ready_local(self, timeout: int | None) -> tuple[bool, str, str]:
        from ..process_utils import ensure_xtts_ready
        # XTTS has its own CPU-fallback toggle; honour XTTS_FORCE_CPU=1
        # by skipping the ensure if explicitly forced (the LLM caller is
        # expected to know that XTTS won't take VRAM in that case).
        ok, msg = ensure_xtts_ready(timeout=timeout or 60)
        device = ""
        if ok and "cuda" in msg.lower():
            device = "cuda"
        elif ok and "cpu" in msg.lower():
            device = "cpu"
        return ok, msg, device

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
