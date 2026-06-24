"""DashScope Qwen3-TTS — cloud streaming TTS, no local GPU."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


def _network_error_types() -> tuple:
    """Exception types that signal a network/internet outage — as opposed to
    an API error where DashScope actually responded (auth, quota, bad voice).
    Built lazily so a missing optional dep never breaks import. ConnectionError,
    TimeoutError and socket.gaierror are OSError subclasses; requests wraps
    low-level socket failures in its own ConnectionError/Timeout."""
    import socket
    types: tuple = (ConnectionError, TimeoutError, socket.gaierror)
    try:
        import requests.exceptions as _rexc
        types += (_rexc.ConnectionError, _rexc.Timeout)
    except Exception:
        pass
    return types


# Display name → voice id. Cloned voices use the qwen-tts-vc-* IDs that
# the VC model wants; built-in voices share name and id. The ★ prefix
# marks cloned voices in the UI; the dispatcher strips it before
# voice resolution.
_VOICES_BATCH: dict[str, str] = {
    # Custom cloned voices (enrolled via DashScope Voice Enrollment API)
    "★ AIfred":   "qwen-tts-vc-aifred-voice-20260215200351981-1e03",
    "★ Sokrates": "qwen-tts-vc-sokrates-voice-20260215200356508-96af",
    "★ Salomo":   "qwen-tts-vc-salomo-voice-20260215200400827-48f6",
    # Built-in voices (multilingual, all support German)
    "Cherry":    "Cherry",
    "Serena":    "Serena",
    "Ethan":     "Ethan",
    "Chelsie":   "Chelsie",
    "Momo":      "Momo",
    "Vivian":    "Vivian",
    "Moon":      "Moon",
    "Maia":      "Maia",
    "Kai":       "Kai",
    "Bella":     "Bella",
    "Jennifer":  "Jennifer",
    "Ryan":      "Ryan",
    "Aiden":     "Aiden",
    "Mia":       "Mia",
    "Vincent":   "Vincent",
    "Neil":      "Neil",
    "Elias":     "Elias",
    "Arthur":    "Arthur",
    "Stella":    "Stella",
    "Emilien":   "Emilien",
    "Andre":     "Andre",
    "Lenn":      "Lenn",
}


class DashScopeEngine(TTSEngine):
    key = "dashscope"
    label_short = "DashScope"
    runs_in_container = False
    needs_gpu = False
    needs_speed_postprocess = True
    supports_language = True
    display_order = 50

    # DashScope service endpoints + model identifiers.
    base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    model_flash: str = "qwen3-tts-flash"
    model_vc_batch: str = "qwen3-tts-vc-2026-01-22"
    # Volume boost (1.0 = unchanged, 2.0 = double, …). DashScope output is
    # noticeably quieter than the local engines; 3.0 brings it in line.
    output_gain: float = 3.0

    # ISO short code → DashScope language_type.
    language_map_dashscope: dict[str, str] = {
        "de": "German",   "en": "English", "fr": "French",   "es": "Spanish",
        "it": "Italian",  "pt": "Portuguese", "ru": "Russian",
        "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    }

    @property
    def suitable_for_channels(self) -> bool:  # type: ignore[override]
        """Cloud engine — only offer it in channel dropdowns (FreeEcho.2)
        when the DashScope API key is configured. Without a key every
        synthesis fails, so a keyless setup hides the dead option instead
        of letting a user pick an engine that cannot work. The TTS itself
        runs server-side, so the channel device needs no key of its own."""
        from ..credential_broker import broker
        return bool(broker.get("cloud_qwen", "api_key"))

    @property
    def voices_fallback(self) -> dict[str, str]:
        return dict(_VOICES_BATCH)

    def get_voices(self) -> dict[str, str]:
        # DashScope has a fixed catalogue; no live discovery endpoint.
        return self.voices_fallback

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> Optional[str]:
        """DashScope cloud TTS — streaming mode collects PCM chunks and
        writes them as a single WAV file. Requires the ``cloud_qwen``
        api_key credential. Speed/pitch are post-processed centrally
        via ffmpeg (the SDK has no native speed parameter)."""
        import base64
        import os
        from ..audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            _apply_pcm_gain,
            _write_pcm_to_wav,
            TTS_AUDIO_DIR,
        )
        from ..logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)

        try:
            import dashscope
            from ..credential_broker import broker
            api_key = broker.get("cloud_qwen", "api_key")
            if not api_key:
                log_message("❌ DashScope TTS: API key not configured")
                return None

            dashscope.base_http_api_url = self.base_url
            language_type = self.language_map_dashscope.get(language, "Auto")

            # Voice resolution: display name → voice id. The ★ prefix is
            # stripped centrally before we get here, so check both forms.
            voice_id = _VOICES_BATCH.get(voice) or _VOICES_BATCH.get(f"★ {voice}", voice)

            # VC model for cloned voices, flash model for built-in ones.
            is_cloned = voice_id.startswith("qwen-tts-vc-")
            model = self.model_vc_batch if is_cloned else self.model_flash

            log_message(f"🎤 DashScope TTS: voice={voice}, id={voice_id}, model={model}, lang={language_type}, text_length={len(text)}")

            # Streaming mode — PCM chunks (24 kHz, 16-bit mono).
            response = dashscope.MultiModalConversation.call(
                model=model,
                api_key=api_key,
                text=text,
                voice=voice_id,
                language_type=language_type,
                stream=True,
            )
            pcm_chunks: list[bytes] = []
            for chunk in response:
                if chunk.output and chunk.output.audio and chunk.output.audio.data:
                    pcm_chunks.append(base64.b64decode(chunk.output.audio.data))

            if not pcm_chunks:
                log_message("❌ DashScope TTS: No audio chunks received")
                return None

            pcm_data = _apply_pcm_gain(b"".join(pcm_chunks), self.output_gain)
            _write_pcm_to_wav(pcm_data, output_file)

            duration = len(pcm_data) / (24000 * 2)
            if _validate_audio_output(output_file):
                size = os.path.getsize(output_file)
                log_message(f"✅ DashScope TTS: Audio saved → {output_file} ({size:,} bytes, {duration:.1f}s)")
                return f"/_upload/tts_audio/{filename}"
            log_message(f"⚠️ DashScope TTS: File missing or too small at {output_file}")
            return None
        except ImportError:
            log_message("❌ DashScope TTS: dashscope SDK not installed. Run: pip install dashscope>=1.24.6")
            return None
        except Exception as e:
            # No fallback to another TTS engine (project rule) — surface the
            # cause instead. Distinguish a network/internet outage from a real
            # API/usage error so the debug console + log say "offline" plainly.
            # Both go to debug.log and the UI console via log_message.
            if isinstance(e, _network_error_types()):
                log_message(
                    f"❌ DashScope TTS: network/internet unreachable — "
                    f"{type(e).__name__}: {e}", "error",
                )
            else:
                log_message(f"❌ DashScope TTS error: {type(e).__name__}: {e}", "error")
            return None
