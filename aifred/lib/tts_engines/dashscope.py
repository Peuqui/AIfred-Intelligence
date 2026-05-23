"""DashScope Qwen3-TTS — cloud streaming TTS, no local GPU."""
from __future__ import annotations

from typing import Optional

from .base import TTSEngine


class DashScopeEngine(TTSEngine):
    key = "dashscope"
    label_short = "DashScope"
    runs_in_container = False
    needs_gpu = False
    needs_speed_postprocess = True
    supports_language = True
    # Cloud engine needs an API key — channel devices don't always have
    # that wired up, so we keep it out of the FreeEcho-style dropdowns.
    suitable_for_channels = False
    display_order = 50

    @property
    def voices_fallback(self) -> dict[str, str]:
        from ..config import DASHSCOPE_VOICES
        return dict(DASHSCOPE_VOICES)

    def get_voices(self) -> dict[str, str]:
        # DashScope has a fixed catalogue; no live discovery endpoint
        # AIfred currently uses, so we just return the static mapping.
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
        via ffmpeg (the SDK has no native speed parameter).

        Note: This is the non-realtime batch path; the realtime WebSocket
        variant (token-by-token streaming) lives in
        :class:`aifred.lib.audio_processing.DashScopeRealtimeTTS` because
        it has a different state model (LLM tokens feed in over time)."""
        import base64
        import os
        from ..audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            _apply_pcm_gain,
            _write_pcm_to_wav,
            TTS_AUDIO_DIR,
        )
        from ..config import (
            DASHSCOPE_TTS_MODEL, DASHSCOPE_TTS_VC_MODEL,
            DASHSCOPE_TTS_BASE_URL, DASHSCOPE_LANGUAGE_MAP, DASHSCOPE_VOICES,
            DASHSCOPE_TTS_GAIN,
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

            dashscope.base_http_api_url = DASHSCOPE_TTS_BASE_URL
            language_type = DASHSCOPE_LANGUAGE_MAP.get(language, "Auto")

            # Voice resolution: display name → voice id. The ★ prefix is
            # stripped centrally before we get here, so check both forms.
            voice_id = DASHSCOPE_VOICES.get(voice) or DASHSCOPE_VOICES.get(f"★ {voice}", voice)

            # VC model for cloned voices, flash model for built-in ones.
            is_cloned = voice_id.startswith("qwen-tts-vc-")
            model = DASHSCOPE_TTS_VC_MODEL if is_cloned else DASHSCOPE_TTS_MODEL

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

            pcm_data = _apply_pcm_gain(b"".join(pcm_chunks), DASHSCOPE_TTS_GAIN)
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
            log_message(f"❌ DashScope TTS Exception: {type(e).__name__}: {e}")
            return None
