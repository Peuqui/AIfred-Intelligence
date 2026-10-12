"""One reply read aloud into an open browser window (SSOT of the browser's reading).

The text arrives while the language model writes (``feed``, ``finish``). AIfred's own filter
(``tts_text_filter``) takes out what only the interface shows; the rest goes to the TTS service
over one text stream (``tts_client.open_stream``). The service cuts the text, cleans it, picks the
engine, escalates on failure and announces a change of voice. What comes back is put on the
browser's playlist (the push bus) in the order the service delivers it: a segment as an audio
file, a pause marker as a pause entry. Keep-alive markers are dropped.

The browser's own voice (Web Speech API on the user's device, the entry ``browser`` of AIfred's
list) is not the service's to operate. Only the entries ABOVE it go to the service; the browser
speaks if

- it is the topmost active entry, or
- the service delivers nothing BEFORE the first sound (any ``TTSSuiteError``: unreachable, no
  engine can speak, unsupported language, ...). The whole text so far, and what follows, then goes
  to the browser, sentence by sentence (``tts_speak`` entries).

If the service breaks off AFTER the first sound, the rest is NOT spoken by the browser: AIfred does
not know which pieces were spoken. That is logged loudly and shown as a toast (``notify``).

``abort`` ends the reading (page left, reply cancelled): the stream is closed, so the service stops.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from .agent_config import load_agents
from .audio_processing import (
    clean_text_for_tts,
    extract_complete_paragraphs,
    extract_complete_sentences,
    reset_content_hint_flags,
    store_tts_audio,
)
from .config import BROWSER_PARAGRAPH_PAUSE_MS, TTS_BROWSER_ENGINE_KEY
from .i18n import t
from .logging_utils import log_message
from .tts_client import TextStream, TTSSuiteError, get_tts_client, new_run_id
from .tts_service import (
    browser_entry_unit,
    request_engines_before_browser,
    service_entries_before_browser,
    browser_note,
    escalation_report,
    spoken_note,
)
from .tts_text_filter import UiOnlyContentStream
from .tts_voices import configured_voice, voices_for


class BrowserSentences:
    """Cuts the (filtered) text for the browser's own voice: by sentence or paragraph as the browser
    entry's unit says, ``whole`` = one utterance at the end. The service does not do this for the
    browser, so this is the one place that does."""

    def __init__(self, unit: str, emit: Callable[[str], None]) -> None:
        self._unit = unit
        self._emit = emit
        self._buffer = ""

    def feed(self, text: str) -> None:
        self._buffer += text
        if self._unit == "whole":
            return
        extract = extract_complete_paragraphs if self._unit == "paragraph" else extract_complete_sentences
        complete, self._buffer = extract(self._buffer)
        for piece in complete:
            self._emit(piece)

    def finish(self) -> None:
        rest, self._buffer = self._buffer.strip(), ""
        if rest:
            self._emit(rest)


class BrowserReading:
    """The reading of one reply. ``push(session_id, kind, url, **)`` is the push bus; ``report``
    takes the English debug lines, ``notify(kind, message)`` a message for the user (toast),
    ``on_first_output`` is called once before the first entry reaches the playlist."""

    def __init__(
        self, *, session_id: str, agent: str, language: str, ui_language: str,
        push: Callable[..., None], report: Callable[[str], None], notify: Callable[[str, str], None],
        on_first_output: Callable[[], None],
    ) -> None:
        self.session_id = session_id
        self.agent = agent
        self.agent_label = load_agents()[agent].display_name
        self.language = language
        self._ui_language = ui_language
        self._push = push
        self._report = report
        self._notify = notify
        self._on_first_output = on_first_output
        self._filter = UiOnlyContentStream()
        self._outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._sent: list[str] = []  # filtered text the service has been given (the browser may need it)
        self._finished = False  # finish() was called
        self._ended = False  # the sender has taken the end of the text
        self._first_tone = False
        self._first_output_done = False
        self.audio_urls: list[str] = []
        self.browser_speech: list[dict[str, Any]] = []
        self.note = ""
        self.aborted = False  # abort() ended the reading
        self._task: asyncio.Task[None] | None = None

    # ---- the text in ----

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"browser-reading-{self.agent}")

    def feed(self, chunk: str) -> None:
        """Next piece of the reply as the model writes it (synchronous: called per token)."""
        if self._finished or self._task is None or self._task.done():
            return
        piece = self._filter.feed(chunk)
        if piece:
            self._outbox.put_nowait(piece)

    def finish(self) -> None:
        """The reply is complete."""
        if self._finished:
            return
        self._finished = True
        if self._task is None or self._task.done():
            return
        piece = self._filter.finish()
        if piece:
            self._outbox.put_nowait(piece)
        self._outbox.put_nowait(None)

    async def wait(self) -> None:
        """Until the reading is over (everything spoken or handed to the playlist, or failed)."""
        if self._task is not None:
            await asyncio.wait({self._task})

    def abort(self) -> None:
        """End the reading now: the task is cancelled, its stream closed (the service stops)."""
        self.aborted = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # ---- the reading ----

    async def _run(self) -> None:
        try:
            await self._read()
        except asyncio.CancelledError:
            self._report(f"🔊 [Browser {self.agent_label}] reading aborted")
            raise
        except Exception as error:  # noqa: BLE001 — a background task must not die silently
            log_message(f"❌ [Browser {self.agent_label}] reading failed: {type(error).__name__}: {error}", "error")
            self._report(f"❌ [Browser {self.agent_label}] reading failed: {type(error).__name__}: {error}")
            self._notify("error", t("tts_reading_service_failed_silent", lang=self._ui_language, reason=str(error)))

    async def _read(self) -> None:
        browser_unit = browser_entry_unit()
        if service_entries_before_browser():
            try:
                await self._read_with_service()
                return
            except TTSSuiteError as error:
                if not self._handle_service_failure(error, browser_unit):
                    return
        elif browser_unit is None:
            self._report(f"⚠️ [Browser {self.agent_label}] no TTS entry is switched on")
            self._notify("error", t("tts_reading_no_entry", lang=self._ui_language))
            return
        assert browser_unit is not None
        await self._read_with_browser(browser_unit)

    def _handle_service_failure(self, error: TTSSuiteError, browser_unit: str | None) -> bool:
        """Say what happened; ``True`` = the browser takes over."""
        reason = f"{type(error).__name__}: {error}"
        if self._first_tone:
            log_message(f"❌ [Browser {self.agent_label}] TTS service broke off after the first sound: {reason}", "error")
            self._report(f"❌ [Browser {self.agent_label}] TTS service broke off after the first sound: {reason}")
            self._notify("error", t("tts_reading_broke_off", lang=self._ui_language, reason=str(error)))
            return False
        if browser_unit is None:
            log_message(f"❌ [Browser {self.agent_label}] TTS service cannot speak: {reason}", "error")
            self._report(f"❌ [Browser {self.agent_label}] TTS service cannot speak, no browser entry: {reason}")
            self._notify("error", t("tts_reading_service_failed_silent", lang=self._ui_language, reason=str(error)))
            return False
        log_message(f"⚠️ [Browser {self.agent_label}] TTS service gave no sound ({reason}) — the browser speaks", "warning")
        self._report(f"⚠️ [Browser {self.agent_label}] TTS service gave no sound ({reason}) — the browser speaks")
        self._notify("warning", t("tts_reading_service_failed", lang=self._ui_language, reason=str(error)))
        return True

    # ---- through the service ----

    async def _read_with_service(self) -> None:
        client = get_tts_client()
        await asyncio.to_thread(client.ensure_ready)
        engines = await asyncio.to_thread(request_engines_before_browser)
        stream = await client.open_stream(
            language=self.language, engines=engines, paragraph_pause_ms=BROWSER_PARAGRAPH_PAUSE_MS,
            voices=voices_for(engines, self.agent), run=new_run_id(),
        )
        workers = [asyncio.ensure_future(self._consume(stream)), asyncio.ensure_future(self._send(stream))]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await stream.aclose()

    async def _send(self, stream: TextStream) -> None:
        while True:
            piece = await self._outbox.get()
            if piece is None:
                self._ended = True
                await stream.finish()
                return
            self._sent.append(piece)
            await stream.feed(piece)

    async def _consume(self, stream: TextStream) -> None:
        async for segment in stream:
            if segment.keepalive:
                continue
            if segment.pause_ms:
                self._playlist("tts_pause", str(segment.pause_ms))
                continue
            url = await asyncio.to_thread(store_tts_audio, segment.audio, segment.media_type, self.agent, segment.engine)
            self._first_tone = True
            self.audio_urls.append(url)
            self._playlist("tts", url, playback_rate="1.0x")
        self.note = await asyncio.to_thread(spoken_note, stream.summary.trace, self._ui_language)
        for line in await asyncio.to_thread(escalation_report, stream.summary, f"Browser {self.agent_label}"):
            self._report(line)

    # ---- through the browser's own voice ----

    async def _read_with_browser(self, unit: str) -> None:
        reset_content_hint_flags()
        voice, speed, pitch = configured_voice(TTS_BROWSER_ENGINE_KEY, self.agent)

        def speak(sentence: str) -> None:
            text = clean_text_for_tts(sentence, self.language)
            if text and text.strip():
                utterance = {"text": text, "language": self.language, "voice": voice, "rate": speed, "pitch": pitch}
                self.browser_speech.append(utterance)
                self._playlist("tts_speak", json.dumps(utterance, ensure_ascii=False))

        sentences = BrowserSentences(unit, speak)
        sentences.feed("".join(self._sent))  # nothing if the service was never asked
        while not self._ended:
            piece = await self._outbox.get()
            if piece is None:
                break
            sentences.feed(piece)
        sentences.finish()
        if self.browser_speech:
            self.note = browser_note(self._ui_language)

    # ---- the playlist ----

    def _playlist(self, kind: str, url: str, **options: Any) -> None:
        if not self._first_output_done:
            self._first_output_done = True
            self._on_first_output()
        self._push(self.session_id, kind, url, **options)
