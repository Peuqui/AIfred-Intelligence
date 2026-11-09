"""Stand-in for the TTS reading of a reply (lib/browser_reading.py), for the tests of the spoken
reply (lib/reply_speech.py) and of the turn that uses it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from aifred.lib.browser_reading import BrowserReading

if TYPE_CHECKING:
    _ReadingBase = BrowserReading
else:
    _ReadingBase = object


class FakeReading(_ReadingBase):
    """Stand-in for the TTS reading: records what it is fed; finishing puts one segment on the
    playlist (with ``browser_voice`` one part the browser speaks). ``over`` ends the reading (all of
    it handed to the playlist)."""

    made: ClassVar[list[FakeReading]] = []
    browser_voice: ClassVar[bool] = False
    ends_on_finish: ClassVar[bool] = False   # the reading is over as soon as the text is

    def __init__(
        self, *, session_id: str, agent: str, language: str, ui_language: str,
        push: Callable[..., None], report: Callable[[str], None], notify: Callable[[str, str], None],
        on_first_output: Callable[[], None],
    ) -> None:
        self.session_id = session_id
        self.agent = agent
        self.language = language
        self._push = push
        self.fed: list[str] = []
        self.audio_urls: list[str] = []
        self.browser_speech: list[dict[str, Any]] = []
        self.note = ""
        self.aborted = False
        self.broke_off = False   # set by a test: the service stopped after the first sound
        self.finished = False
        self.over = asyncio.Event()
        FakeReading.made.append(self)

    def start(self) -> None:
        pass

    def feed(self, chunk: str) -> None:
        self.fed.append(chunk)

    def finish(self) -> None:
        self.finished = True
        if self.browser_voice:
            self.browser_speech.append({"text": "Hallo.", "language": self.language, "voice": "Auto", "rate": None, "pitch": None})
            self._push(self.session_id, "tts_speak", utterance=self.browser_speech[-1])
            if self.ends_on_finish:
                self.over.set()
            return
        url = f"/_upload/tts_audio/{self.agent}-{len(FakeReading.made)}.wav"
        self.audio_urls.append(url)
        self.note = "Edge · Cloud"
        self._push(self.session_id, "tts", url=url)
        if self.ends_on_finish:
            self.over.set()

    def abort(self) -> None:
        self.aborted = True
        self.over.set()

    async def wait(self) -> None:
        await self.over.wait()
