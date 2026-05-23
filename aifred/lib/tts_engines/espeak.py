"""eSpeak — minimal robotic offline TTS."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class EspeakEngine(TTSEngine):
    key = "espeak"
    label_short = "eSpeak"
    runs_in_container = False
    needs_gpu = False
    # eSpeak's -s flag controls speed natively.
    needs_speed_postprocess = False
    suitable_for_channels = True
    display_order = 70

    @property
    def voices_fallback(self) -> dict[str, str]:
        # ESPEAK_VOICES still in config (display → (espeak_voice_id, lang)
        # tuple). The dropdown only needs the display names.
        from ..config import ESPEAK_VOICES
        return {name: name for name in ESPEAK_VOICES}

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
        """eSpeak subprocess synth. Speed is applied natively via the
        words-per-minute ``-s`` flag."""
        import os
        import subprocess
        from ..audio_processing import _generate_tts_filename, TTS_AUDIO_DIR
        from ..config import ESPEAK_VOICES
        from ..logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            voice_config = ESPEAK_VOICES.get(voice)
            if voice_config:
                voice_lang, _ = voice_config
            else:
                voice_lang = "de"
                log_message(f"⚠️ eSpeak: Voice '{voice}' not found, using 'de'")

            # eSpeak speed = words per minute (default ~175, range 80-500).
            # 1.0 → 175 wpm, 1.25 → 220 wpm, 2.0 → 350 wpm.
            wpm = int(175 * speed)
            log_message(f"🎤 eSpeak TTS: voice={voice_lang}, speed={speed}, wpm={wpm}")

            # Prefer espeak-ng; fall back to legacy espeak if it isn't installed.
            espeak_cmd = "espeak"
            try:
                if subprocess.run(["espeak-ng", "--version"], capture_output=True, timeout=5).returncode == 0:
                    espeak_cmd = "espeak-ng"
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                pass

            result = subprocess.run(
                [espeak_cmd, "-v", voice_lang, "-s", str(wpm), "-w", output_file, text],
                capture_output=True,
                timeout=None,
            )
            if result.returncode == 0 and os.path.exists(output_file):
                log_message(f"✅ eSpeak TTS: Audio saved → {output_file} ({os.path.getsize(output_file)} bytes)")
                return f"/_upload/tts_audio/{filename}"
            err = result.stderr.decode() if result.stderr else "Unknown error"
            log_message(f"❌ eSpeak TTS Error: {err}")
            return None
        except FileNotFoundError:
            log_message("❌ eSpeak TTS: espeak/espeak-ng not installed. Run: sudo apt install espeak-ng")
            return None
        except Exception as e:
            log_message(f"❌ eSpeak TTS Exception: {e}")
            return None
