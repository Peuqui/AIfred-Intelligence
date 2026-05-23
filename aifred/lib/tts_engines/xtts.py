"""XTTS v2 (Coqui) — voice cloning + built-in speakers, runs as Docker container."""
from __future__ import annotations

from typing import Any, Optional

from .base import TTSEngine


class XTTSEngine(TTSEngine):
    key = "xtts"
    label_short = "XTTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    suitable_for_channels = True

    # XTTS allocates statically at model load — no dynamic peak above
    # idle, so no extra reserve needed for LLM calibration.
    calibration_vram_reserve_mb = 0
    display_order = 20

    image_name = "xtts-rtx8000"

    @property
    def service_url(self) -> str:
        return "http://localhost:5051"

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
        import requests
        try:
            r = requests.get(f"{self.service_url}/voices", timeout=5)
            if r.ok:
                data = r.json()
                voices = {}
                for name in data.get("custom", []):
                    voices[f"★ {name}"] = name
                for name in data.get("builtin", []):
                    voices[name] = name
                return voices
        except (requests.RequestException, ValueError) as e:
            print(f"⚠️ Failed to fetch XTTS voices: {e}")
        return {}

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

    def start(self) -> tuple[bool, str]:
        from ..process_utils import start_xtts_container
        return start_xtts_container()

    def stop(self) -> tuple[bool, str]:
        from ..process_utils import stop_xtts_container
        return stop_xtts_container()

    def ensure_ready(self, timeout: int | None = None) -> tuple[bool, str, str]:
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
    ) -> Optional[str]:
        from ..audio_processing import generate_speech_xtts
        return generate_speech_xtts(text, speed, voice, language)

    def calibration_setup(self, debug: Any) -> bool:
        ok, msg, _device = self.ensure_ready(timeout=120)
        if not ok:
            return False
        debug(f"   🔊 {msg}")
        # XTTS responds to a short test inference to push its working
        # set up to the steady-state peak (idle ~2 GB, peak ~4 GB).
        import httpx
        try:
            debug("   🔊 Running test TTS for peak VRAM measurement...")
            r = httpx.post(
                f"{self.service_url}/tts",
                json={"text": "Dies ist ein Kalibrierungstest für den Sprachspeicher.", "language": "de"},
                timeout=60.0,
            )
            if r.is_success:
                debug("   🔊 Peak VRAM reached after test inference")
        except httpx.HTTPError:
            debug("   ⚠️ Test TTS failed, using idle VRAM (may underestimate)")
        return True
