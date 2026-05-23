"""eSpeak — minimal robotic offline TTS."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


# All catalogued eSpeak voices. Format: display_name → (espeak_voice_id, language).
# Standard voices (de, en, ...) are always available. mbrola voices need
# the corresponding ``mbrola`` + ``mbrola-deX`` apt packages and get
# filtered out at runtime if not installed (see :meth:`get_voices`).
_ESPEAK_VOICES_ALL: dict[str, tuple[str, str]] = {
    # Deutsch — Standard eSpeak (robotic, always available)
    "Deutsch Standard":         ("de",      "de"),
    "Deutsch Männlich 1":       ("de+m1",   "de"),
    "Deutsch Männlich 2":       ("de+m2",   "de"),
    "Deutsch Weiblich 1":       ("de+f1",   "de"),
    "Deutsch Weiblich 2":       ("de+f2",   "de"),
    # Deutsch — mbrola (more natural, requires apt packages)
    "Deutsch mbrola-2 (M)":     ("mb/mb-de2", "de"),
    "Deutsch mbrola-3 (F)":     ("mb/mb-de3", "de"),
    "Deutsch mbrola-4 (M)":     ("mb/mb-de4", "de"),
    "Deutsch mbrola-5 (F)":     ("mb/mb-de5", "de"),
    "Deutsch mbrola-6 (M)":     ("mb/mb-de6", "de"),
    "Deutsch mbrola-7 (F)":     ("mb/mb-de7", "de"),
    # Englisch — Standard eSpeak (always available)
    "Englisch Standard":        ("en",      "en"),
    "Englisch US":              ("en-us",   "en"),
    "Englisch UK":              ("en-gb",   "en"),
    # Englisch — mbrola
    "Englisch mbrola UK (M)":   ("mb/mb-en1", "en"),
    "Englisch mbrola US-1 (F)": ("mb/mb-us1", "en"),
    "Englisch mbrola US-2 (M)": ("mb/mb-us2", "en"),
    "Englisch mbrola US-3 (M)": ("mb/mb-us3", "en"),
}


def _detect_available_voices() -> dict[str, tuple[str, str]]:
    """Filter ``_ESPEAK_VOICES_ALL`` down to the voices actually
    installed on this host. mbrola voices are kept only when
    ``espeak-ng --voices=mb`` lists them."""
    import subprocess
    mbrola_available: set[str] = set()
    try:
        espeak_cmd = "espeak-ng"
        try:
            subprocess.run([espeak_cmd, "--version"], capture_output=True, timeout=2)
        except (FileNotFoundError, OSError):
            espeak_cmd = "espeak"
        result = subprocess.run(
            [espeak_cmd, "--voices=mb"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n")[1:]:
                parts = line.split()
                # File column (index 4) holds the voice id like "mb/mb-de2".
                if len(parts) >= 5 and parts[4].startswith("mb/"):
                    mbrola_available.add(parts[4])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    available: dict[str, tuple[str, str]] = {}
    for name, (voice_id, lang) in _ESPEAK_VOICES_ALL.items():
        if voice_id.startswith("mb/"):
            if voice_id in mbrola_available:
                available[name] = (voice_id, lang)
        else:
            available[name] = (voice_id, lang)
    return available


# Cached at module load — re-detection on every dropdown render would
# fork espeak-ng each time.
_AVAILABLE_VOICES: dict[str, tuple[str, str]] = _detect_available_voices()


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
        # Dropdown only needs display names; the (voice_id, lang) tuple
        # is used internally during synthesis.
        return {name: name for name in _AVAILABLE_VOICES}

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
        from ..logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            voice_config = _AVAILABLE_VOICES.get(voice)
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
