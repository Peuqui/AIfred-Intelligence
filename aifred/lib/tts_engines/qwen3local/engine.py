"""Qwen3-TTS local container — streaming voice cloning on a single HBM GPU."""
from __future__ import annotations


from ..base import TTSEngine, shared_voice_names


class Qwen3LocalEngine(TTSEngine):
    key = "qwen3local"
    label_short = "Qwen3-TTS"
    runs_in_container = True
    needs_gpu = True
    needs_speed_postprocess = True
    supports_language = True
    display_order = 10
    in_default_escalation = True

    image_name = "qwen3-tts-1.7b-base"
    compose_subdir = "qwen3-tts"

    default_port = 5052
    startup_timeout_s = 240
    max_parallel_requests = 1

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
        return {name: name for name in shared_voice_names()}

    def get_voices(self) -> dict[str, str]:
        """One entry per <name>.wav in /app/voices/ inside the container —
        the Qwen3 container exposes them via /voices, identical shape to
        MOSS. Returns {} on any failure (caller falls back)."""
        data = self._fetch_voices_json()
        return {name: name for name in data.get("voices", [])} if data else {}

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Qwen3-TTS — same /tts shape as XTTS/MOSS but with the full
        language name instead of the ISO code. Reference voices are
        warmed at container startup from /app/voices/*.wav. Speed/pitch
        are post-processed centrally via ffmpeg. WAV end-to-end (cleaner
        for downstream ffmpeg adjustments, and the Puck channel prefers WAV)."""
        qwen3_lang = self.language_map.get(language.lower(), "English")
        return self._synthesize_via_http(
            "/tts", {"text": text, "speaker": voice, "language": qwen3_lang}, "wav",
        )
