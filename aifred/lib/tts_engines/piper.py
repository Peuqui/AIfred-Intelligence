"""Piper TTS — offline neural voices, runs as a Python subprocess."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .base import TTSEngine


# Display name → (ONNX model filename, language code). Models live in
# ``<repo>/piper_models/<filename>``. Adding a new voice: drop the .onnx
# file in that directory and add a line here.
_PIPER_VOICES: dict[str, tuple[str, str]] = {
    # Deutsch — männliche Stimmen
    "Deutsch (Thorsten)":  ("de_DE-thorsten-high.onnx", "de"),
    "Deutsch (Karlsson)":  ("de_DE-karlsson-low.onnx",  "de"),
    # Deutsch — weibliche Stimmen
    "Deutsch (Ramona)":    ("de_DE-ramona-low.onnx",    "de"),
    "Deutsch (Kerstin)":   ("de_DE-kerstin-low.onnx",   "de"),
    "Deutsch (Eva K)":     ("de_DE-eva_k-x_low.onnx",   "de"),
    "Deutsch (MLS)":       ("de_DE-mls-medium.onnx",    "de"),  # multi-speaker
}


def _piper_binary() -> Path:
    """Platform-specific path to the piper executable in our venv."""
    from ..config import PROJECT_ROOT
    if os.name == "nt":
        return PROJECT_ROOT / "venv" / "Scripts" / "piper.exe"
    return PROJECT_ROOT / "venv" / "bin" / "piper"


def _piper_default_model() -> Path:
    """Default model when the requested voice isn't found."""
    from ..config import PROJECT_ROOT
    return PROJECT_ROOT / "piper_models" / "de_DE-thorsten-medium.onnx"


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
        # Dropdown only needs the display names; the (model, lang) tuple
        # is used internally during synthesis.
        return {name: name for name in _PIPER_VOICES}

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
        import subprocess
        from ..audio_processing import _generate_tts_filename, TTS_AUDIO_DIR
        from ..config import PROJECT_ROOT
        from ..logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            voice_config = _PIPER_VOICES.get(voice)
            if voice_config:
                model_filename, _lang = voice_config
                model_path = PROJECT_ROOT / "piper_models" / model_filename
            else:
                model_path = _piper_default_model()
                log_message(f"⚠️ Piper: Voice '{voice}' not found, using default")

            # length_scale inverts speed: higher = slower (1.0 = normal,
            # 0.8 ≈ 1.25× faster, 0.5 = 2× faster).
            length_scale = 1.0 / speed
            log_message(f"🎤 Piper TTS: voice={voice}, speed={speed}, length_scale={length_scale}")

            result = subprocess.run(
                [str(_piper_binary()), "--model", str(model_path), "--output_file", output_file, "--length_scale", str(length_scale)],
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
