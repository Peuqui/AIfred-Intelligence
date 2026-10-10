"""DashScope Qwen-Audio 3.0 TTS — cloud TTS of Alibaba, no local GPU.

HTTP endpoint whose result is a URL to a WAV file, a catalog of system voices
and cloned voices enrolled for the flash model (``dashscope_enroll``). Replaced
the older Qwen3-TTS cloud engine (10.10.2026), whose cloned voices Alibaba
switched off.
"""
from __future__ import annotations

from ..base import TTSEngine, TTSFailure

# Display name → Alibaba voice id (docs: "Qwen-Audio TTS voice list", 10.10.2026).
# All of them speak German understandably despite the docs listing only
# Chinese and English; the voice carries the accent.
_VOICES: dict[str, str] = {
    "Mary":       "loongmary",          # English, warm British accent
    "Eva":        "loongeva_v3.6",      # English, American
    "John":       "loongjohn",          # English, American, male
    "Ling Xin":   "longanlingxin",      # companion, warm (plus model)
    "Lu Feng":    "longanlufeng",       # companion, bright, male (plus model)
    "Feng Yue":   "longanfengyue",
    "Yuan Fei":   "longanyuanfei",
    "Ling Xi":    "longanlingxi",
    "Xiao Xin":   "longanxiaoxin",
    "Huan":       "longanhuan_v3.6",
    "Li Dou":     "longjielidou_v3.6",  # child, male
    "Pao Pao":    "longpaopao_v3.6",    # child
    "Huo Huo":    "longhuohuo_v3.6",    # character, male
    "Chuan Shu":  "longchuanshu_v3.6",  # character, male, Sichuan accent
}
# The two flagship voices only work with the plus model.
_PLUS_VOICE_IDS = {"longanlingxin", "longanlufeng"}


def _network_error_types() -> tuple:
    """Exception types that signal a network/internet outage — as opposed to
    an API error where DashScope actually responded (auth, quota, bad voice).
    ConnectionError, TimeoutError and socket.gaierror are OSError subclasses;
    requests wraps low-level socket failures in its own ConnectionError/Timeout."""
    import socket

    import requests.exceptions as requests_errors
    return (ConnectionError, TimeoutError, socket.gaierror,
            requests_errors.ConnectionError, requests_errors.Timeout)


def _cloned_voices() -> dict[str, str]:
    """Enrolled cloned voices from the mapping: '★ Name' → voice id. Read live
    (cheap JSON read) so a freshly enrolled WAV shows up without a restart."""
    from ...dashscope_enroll import load_mapping
    return {
        f"★ {name}": entry["voice_id"]
        for name, entry in load_mapping().items()
        if entry.get("voice_id")
    }


class DashScopeAudio3Engine(TTSEngine):
    key = "dashscope_audio3"
    label_short = "DashScope Audio 3"
    runs_in_container = False
    needs_gpu = False
    needs_speed_postprocess = True
    supports_language = True
    display_order = 50
    in_default_escalation = True
    cloud = True
    default_voice = "Mary"

    base_url: str = "https://dashscope-intl.aliyuncs.com/api/v1"
    # Cloned voices are bound to the model they were enrolled for (dashscope_enroll
    # reads it from here).
    model_flash: str = "qwen-audio-3.0-tts-flash"
    model_plus: str = "qwen-audio-3.0-tts-plus"
    # The output is already as loud as the local engines (about -21 LUFS).
    output_gain: float = 1.0
    #: Seconds to wait for the synthesis and for the download of its WAV file.
    request_timeout_s: int = 60

    # ISO short code → DashScope language_type.
    language_map_dashscope: dict[str, str] = {
        "de": "German",   "en": "English", "fr": "French",   "es": "Spanish",
        "it": "Italian",  "pt": "Portuguese", "ru": "Russian",
        "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    }

    def is_running(self) -> bool:
        """Cloud engine: usable as soon as the API key is configured."""
        from ...credential_broker import broker
        return bool(broker.get("cloud_qwen", "api_key"))

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Cloned voices (★ …) on top of the system voices.
        return {**_cloned_voices(), **_VOICES}

    def get_voices(self) -> dict[str, str]:
        # Fixed catalog; no live discovery endpoint.
        return self.voices_fallback

    def generate_speech(
        self,
        text: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> str:
        """Synthesise ``text`` and fetch the WAV file. Needs the ``cloud_qwen``
        api_key credential. Speed/pitch are post-processed centrally via ffmpeg."""
        import requests
        from ...credential_broker import broker
        from ...logging_utils import log_message

        try:
            api_key = broker.get("cloud_qwen", "api_key")
            if not api_key:
                raise TTSFailure("engine", "DashScope API key not configured")
            # The ★ prefix of cloned voices may or may not be stripped before we get here.
            voices = self.voices_fallback
            voice_id = voices.get(voice) or voices.get(f"★ {voice}")
            if voice_id is None:
                raise TTSFailure("engine", f"{self.label_short}: unknown voice {voice!r}")
            model = self.model_plus if voice_id in _PLUS_VOICE_IDS else self.model_flash
            language_type = self.language_map_dashscope.get(language, "Auto")
            log_message(
                f"🎤 {self.label_short} TTS: voice={voice}, id={voice_id}, model={model}, "
                f"lang={language_type}, text_length={len(text)}"
            )

            reply = requests.post(
                f"{self.base_url}/services/audio/tts/SpeechSynthesizer",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "input": {"text": text, "voice": voice_id, "language_type": language_type},
                    "parameters": {"format": "wav", "sample_rate": 24000},
                },
                timeout=self.request_timeout_s,
            )
            if not reply.ok:
                raise TTSFailure("engine", f"{self.label_short}: HTTP {reply.status_code}: {reply.text[:200]}")
            audio_url = reply.json()["output"]["audio"]["url"]
            # The result URL is signed and handed out as http://; the same file is served over https.
            audio = requests.get(audio_url.replace("http://", "https://", 1), timeout=self.request_timeout_s)
            audio.raise_for_status()
            return self._store_wav(audio.content)
        except Exception as e:  # noqa: BLE001 — _failure sorts it into a TTSFailure
            raise self._failure(e) from e

    def _store_wav(self, wav_bytes: bytes) -> str:
        """One WAV file from DashScope → gain-adjusted file in the TTS audio
        folder; returns its URL path."""
        import io
        import os
        import wave
        from ...audio_processing import (
            _generate_tts_filename,
            _validate_audio_output,
            _apply_pcm_gain,
            _write_pcm_to_wav,
            TTS_AUDIO_DIR,
        )
        from ...logging_utils import log_message

        filename = _generate_tts_filename("wav")
        output_file = str(TTS_AUDIO_DIR / filename)
        with wave.open(io.BytesIO(wav_bytes)) as wav_stream:
            sample_rate = wav_stream.getframerate()
            pcm_data = wav_stream.readframes(wav_stream.getnframes())

        pcm_data = _apply_pcm_gain(pcm_data, self.output_gain)
        _write_pcm_to_wav(pcm_data, output_file, sample_rate)

        duration = len(pcm_data) / (sample_rate * 2)
        if not _validate_audio_output(output_file):
            raise TTSFailure("engine", f"{self.label_short} file missing or too small at {output_file}")
        size = os.path.getsize(output_file)
        log_message(f"✅ {self.label_short} TTS: Audio saved → {output_file} ({size:,} bytes, {duration:.1f}s)")
        return f"/_upload/tts_audio/{filename}"

    def _failure(self, error: Exception) -> TTSFailure:
        """A TTSFailure from whatever went wrong: a network/internet outage is
        "unreachable"; DashScope answering with an error (auth, quota, bad
        voice) is an engine failure."""
        if isinstance(error, TTSFailure):
            return error
        if isinstance(error, _network_error_types()):
            return TTSFailure("unreachable", f"{self.label_short}: {type(error).__name__}: {error}")
        return TTSFailure("engine", f"{self.label_short}: {type(error).__name__}: {error}")
