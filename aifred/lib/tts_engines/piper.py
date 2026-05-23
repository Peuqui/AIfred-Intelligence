"""Piper TTS — offline neural voices, runs as a Python subprocess."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class PiperEngine(TTSEngine):
    key = "piper"
    label_short = "Piper"
    runs_in_container = False
    needs_gpu = False
    # Piper applies its --length-scale internally — no ffmpeg needed.
    needs_speed_postprocess = False
    suitable_for_channels = True
    display_order = 60

    @property
    def voices_fallback(self) -> dict[str, str]:
        # PIPER_VOICES still lives in config (display → (model, lang)
        # tuple); the dropdown only needs the display names, so we map
        # to {name: name} here. The model lookup uses the original tuple.
        from ..config import PIPER_VOICES
        return {name: name for name in PIPER_VOICES}

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        """Piper subprocess synth. Speed is applied natively via
        ``--length_scale`` (no ffmpeg post-processing needed)."""
        import os
        import subprocess
        from ..audio_processing import (
            _generate_tts_filename,
            PIPER_BIN,
            TTS_AUDIO_DIR,
        )
        from ..config import PIPER_VOICES, PIPER_MODEL_PATH, PROJECT_ROOT
        from ..logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            voice_config = PIPER_VOICES.get(voice)
            if voice_config:
                model_filename, _lang = voice_config
                model_path = PROJECT_ROOT / "piper_models" / model_filename
            else:
                model_path = PIPER_MODEL_PATH
                log_message(f"⚠️ Piper: Voice '{voice}' not found, using default")

            # length_scale inverts speed: higher = slower (1.0 = normal,
            # 0.8 ≈ 1.25× faster, 0.5 = 2× faster).
            length_scale = 1.0 / speed
            log_message(f"🎤 Piper TTS: voice={voice}, speed={speed}, length_scale={length_scale}")

            result = subprocess.run(
                [str(PIPER_BIN), "--model", str(model_path), "--output_file", output_file, "--length_scale", str(length_scale)],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=None,
            )
            if result.returncode == 0 and os.path.exists(output_file):
                log_message(f"✅ Piper TTS: Audio saved → {output_file} ({os.path.getsize(output_file)} bytes)")
                return f"/_upload/tts_audio/{filename}"
            log_message(f"❌ Piper TTS Error: {result.stderr.decode()}")
            return None
        except Exception as e:
            log_message(f"❌ Piper TTS Exception: {e}")
            return None
