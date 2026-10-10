"""DashScope Qwen-Audio 3.0 TTS — cloud TTS of Alibaba's newer model family.

Next to :mod:`.dashscope` (Qwen3-TTS), which keeps working for its built-in
voices. This family has its own HTTP endpoint (the result is a URL to a WAV
file, not a chunk stream) and its own voice catalog. Cloned voices are not
offered yet: they would have to be enrolled again for these models.
"""
from __future__ import annotations

from .base import TTSFailure
from .dashscope import DashScopeEngine

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


class DashScopeAudio3Engine(DashScopeEngine):
    key = "dashscope_audio3"
    label_short = "DashScope Audio 3"
    display_order = 51
    default_voice = "Mary"

    model_flash: str = "qwen-audio-3.0-tts-flash"
    model_plus: str = "qwen-audio-3.0-tts-plus"
    # The output is already as loud as the local engines (about -21 LUFS).
    output_gain: float = 1.0
    #: Seconds to wait for the synthesis and for the download of its WAV file.
    request_timeout_s: int = 60

    @property
    def voices_fallback(self) -> dict[str, str]:
        return dict(_VOICES)

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
        from ..credential_broker import broker
        from ..logging_utils import log_message

        try:
            api_key = broker.get("cloud_qwen", "api_key")
            if not api_key:
                raise TTSFailure("engine", "DashScope API key not configured")
            voice_id = _VOICES.get(voice)
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
