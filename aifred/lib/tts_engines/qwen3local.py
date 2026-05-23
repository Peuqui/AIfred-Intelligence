"""Qwen3-TTS local container — streaming voice cloning on a single HBM GPU."""
from __future__ import annotations

import os
from typing import Any, Optional

from .base import TTSEngine


class Qwen3LocalEngine(TTSEngine):
    key = "qwen3local"
    label_short = "Qwen3-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    suitable_for_channels = True
    display_order = 10

    image_name = "qwen3-tts-1.7b-base"
    compose_subdir = "qwen3-tts"

    # qwen-tts allocates KV-cache + decoder buffers dynamically during
    # generate() — idle ~5 GB, long-bubble peak ~7 GB. The LLM calibration
    # has to plan permanently around the peak, otherwise a long TTS call
    # in production would OOM the V100. Empirically: idle ~5.3 GB,
    # long-bubble peak ~6.7 GB. 7.5 GB sits a bit above the observed peak
    # so even an unusually long bubble can't tip the LLM over its budget.
    # Tunable via env QWEN3_TTS_VRAM_RESERVE_MB.
    @property
    def calibration_vram_reserve_mb(self) -> int:
        return int(os.environ.get("QWEN3_TTS_VRAM_RESERVE_MB", "7680"))

    @property
    def service_url(self) -> str:
        return "http://localhost:5052"

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
        return {
            "AIfred":   "AIfred",
            "HAL9000":  "HAL9000",
            "Salomo":   "Salomo",
            "Sokrates": "Sokrates",
        }

    def get_voices(self) -> dict[str, str]:
        """One entry per <name>.wav in /app/voices/ inside the container —
        the Qwen3 container exposes them via /voices, identical shape to
        MOSS. Returns {} on any failure (caller falls back)."""
        import requests
        try:
            r = requests.get(f"{self.service_url}/voices", timeout=5)
            if r.ok:
                return {name: name for name in r.json().get("voices", [])}
        except (requests.RequestException, ValueError) as e:
            print(f"⚠️ Failed to fetch Qwen3-TTS voices: {e}")
        return {}

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
        """Qwen3-TTS — same /tts shape as XTTS/MOSS but with the full
        language name instead of the ISO code. Reference voices are
        warmed at container startup from /app/voices/*.wav. Speed/pitch
        are post-processed centrally via ffmpeg."""
        import os
        import requests
        from ..audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            TTS_AUDIO_DIR,
        )
        from ..logging_utils import log_message

        # WAV end-to-end (cleaner for downstream ffmpeg adjustments, and
        # the Puck channel already prefers WAV).
        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        qwen3_lang = self.language_map.get(language.lower(), "English")

        try:
            log_message(f"🎤 Qwen3-TTS: speaker={voice}, language={qwen3_lang}, text_length={len(text)}")
            r = requests.post(
                f"{self.service_url}/tts",
                json={"text": text, "speaker": voice, "language": qwen3_lang},
                timeout=None,
            )
            if r.status_code == 200:
                with open(output_file, "wb") as fh:
                    fh.write(r.content)
                if _validate_audio_output(output_file):
                    size = os.path.getsize(output_file)
                    log_message(f"✅ Qwen3-TTS: Audio saved → {output_file} ({size} bytes)")
                    return f"/_upload/tts_audio/{filename}"
                log_message(f"⚠️ Qwen3-TTS: File missing or too small at {output_file}")
                return None
            err = r.text[:200] if r.text else f"HTTP {r.status_code}"
            log_message(f"❌ Qwen3-TTS Error: {err}")
            return None
        except requests.exceptions.ConnectionError:
            log_message("❌ Qwen3-TTS: Service not running. Start with: cd docker/tts/qwen3-tts && docker compose up -d")
            return None
        except Exception as e:
            log_message(f"❌ Qwen3-TTS Exception: {e}")
            return None

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
