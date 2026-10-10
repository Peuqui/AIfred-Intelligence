"""DashScope Qwen3-TTS — cloud streaming TTS, no local GPU."""
from __future__ import annotations


from .base import TTSEngine, TTSFailure


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


# Display name → voice id for DashScope's BUILT-IN voices (name == id, no
# enrollment needed). The CLONED voices are NOT hardcoded here anymore —
# they come live from the enrollment mapping (data/tts/dashscope_voices.json,
# see dashscope_enroll.py), so a freshly enrolled WAV shows up automatically
# with a ★-prefixed display name and its real qwen-tts-vc-* id.
_BUILTIN_VOICES: dict[str, str] = {
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


def _cloned_voices() -> dict[str, str]:
    """Enrolled cloned voices from the mapping: '★ Name' → qwen-tts-vc-* id.
    Read live (cheap JSON read) so a freshly enrolled WAV shows up without a
    restart. Never raises — a broken/missing mapping just means no clones."""
    try:
        from ..dashscope_enroll import load_mapping
        return {
            f"★ {name}": entry["voice_id"]
            for name, entry in load_mapping().items()
            if entry.get("voice_id")
        }
    except Exception:  # noqa: BLE001 — voice list must never break the engine
        return {}


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

    def is_running(self) -> bool:
        """Cloud engine: usable as soon as the API key is configured."""
        from ..credential_broker import broker
        return bool(broker.get("cloud_qwen", "api_key"))

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Live: enrolled cloned voices (★ …) on top of DashScope's built-ins.
        return {**_cloned_voices(), **_BUILTIN_VOICES}

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
    ) -> str:
        """DashScope cloud TTS — streaming mode collects PCM chunks and
        writes them as a single WAV file. Requires the ``cloud_qwen``
        api_key credential. Speed/pitch are post-processed centrally
        via ffmpeg (the SDK has no native speed parameter)."""
        import base64
        import io
        import os
        import wave
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
                raise TTSFailure("engine", "DashScope API key not configured")

            dashscope.base_http_api_url = self.base_url
            language_type = self.language_map_dashscope.get(language, "Auto")

            # Voice resolution: display name → voice id. The ★ prefix is
            # stripped centrally before we get here, so check both forms.
            _all = {**_cloned_voices(), **_BUILTIN_VOICES}
            voice_id = _all.get(voice) or _all.get(f"★ {voice}", voice)

            # VC model for cloned voices, flash model for built-in ones.
            is_cloned = voice_id.startswith("qwen-tts-vc-")
            model = self.model_vc_batch if is_cloned else self.model_flash

            log_message(f"🎤 DashScope TTS: voice={voice}, id={voice_id}, model={model}, lang={language_type}, text_length={len(text)}")

            # Streaming mode — the chunks form ONE WAV stream: the first chunk
            # starts with a RIFF header (sizes 0x7fffffff). Joined as raw PCM,
            # that header plays as a ~1 ms full-scale burst — a click at the
            # start of every synthesized sentence.
            response = dashscope.MultiModalConversation.call(
                model=model,
                api_key=api_key,
                text=text,
                voice=voice_id,
                language_type=language_type,
                stream=True,
            )
            wav_chunks: list[bytes] = []
            # stream=True always returns the generator; the SDK annotates
            # a plain Union without a stream overload.
            for chunk in response:  # type: ignore[union-attr]
                if chunk.output and chunk.output.audio and chunk.output.audio.data:
                    wav_chunks.append(base64.b64decode(chunk.output.audio.data))

            if not wav_chunks:
                raise TTSFailure("engine", "DashScope returned no audio chunks")

            with wave.open(io.BytesIO(b"".join(wav_chunks))) as wav_stream:
                sample_rate = wav_stream.getframerate()
                pcm_data = wav_stream.readframes(wav_stream.getnframes())

            pcm_data = _apply_pcm_gain(pcm_data, self.output_gain)
            _write_pcm_to_wav(pcm_data, output_file, sample_rate)

            duration = len(pcm_data) / (sample_rate * 2)
            if not _validate_audio_output(output_file):
                raise TTSFailure("engine", f"DashScope file missing or too small at {output_file}")
            size = os.path.getsize(output_file)
            log_message(f"✅ DashScope TTS: Audio saved → {output_file} ({size:,} bytes, {duration:.1f}s)")
            return f"/_upload/tts_audio/{filename}"
        except TTSFailure:
            raise
        except ImportError as e:
            raise TTSFailure("software", "dashscope SDK not installed (pip install dashscope>=1.24.6)") from e
        except Exception as e:
            # A network/internet outage is "unreachable"; DashScope answering
            # with an error (auth, quota, bad voice) is an engine failure.
            if isinstance(e, _network_error_types()):
                raise TTSFailure("unreachable", f"DashScope: {type(e).__name__}: {e}") from e
            raise TTSFailure("engine", f"DashScope: {type(e).__name__}: {e}") from e
