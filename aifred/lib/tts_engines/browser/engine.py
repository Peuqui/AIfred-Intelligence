"""Browser TTS — the user's browser speaks (Web Speech API, speechSynthesis).

No audio leaves the server: the escalation list hands the text to the page,
which speaks it with a voice of the device. So it only serves replies into
an open browser session (skipped for the Echo, the narrator, re-synthesis)
and stands last in the list: the server cannot tell whether the device has
a voice, so nothing could take over after it.
"""
from __future__ import annotations

from ..base import TTSEngine

#: The page picks a device voice for the reply's language.
BROWSER_AUTO_VOICE = "Auto"


class BrowserEngine(TTSEngine):
    key = "browser"
    label_short = "Browser"
    runs_in_container = False
    needs_gpu = False
    renders_in_browser = True
    # speechSynthesis takes rate and pitch itself — no ffmpeg.
    needs_speed_postprocess = False
    supports_language = True
    display_order = 90
    in_default_escalation = True
    default_voice = BROWSER_AUTO_VOICE

    @property
    def voices_fallback(self) -> dict[str, str]:
        # Which voices exist is known only to each device; the page chooses.
        return {BROWSER_AUTO_VOICE: BROWSER_AUTO_VOICE}

    def get_voices(self) -> dict[str, str]:
        return self.voices_fallback
