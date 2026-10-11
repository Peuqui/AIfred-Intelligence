"""Die Lesung einer Antwort in ein Browser-Fenster (Fake-Dienst mit WebSocket auf Loopback):
Segmente -> Playlist in Reihenfolge, Pause-Marker -> Pause-Eintrag, Regeln für den Browser-Eintrag,
Abbruch, Fehler nach dem ersten Ton."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from fake_tts_service import (
    FakeStreamingService,
    FakeTTSService,
    audio_frame,
    catalog_row,
    error_frame,
    install_fake_service,
    keepalive_frame,
    pause_frame,
    serve_stream,
    summary_frame,
)

import aifred.lib.tts_client as tts_client
from aifred.lib import browser_reading
from aifred.lib.browser_reading import BrowserReading

CATALOG = [
    catalog_row("xtts"),
    catalog_row("edge", cloud=True, needs_gpu=False),
    catalog_row("browser", browser=True, needs_gpu=False),
]
TRACE_XTTS = [{"entry": "xtts", "outcome": "spoke", "reason": None, "detail": ""}]


class Page:
    """What the browser would get: the playlist pushes, the toasts, the debug lines."""

    def __init__(self) -> None:
        self.playlist: list[tuple[str, str]] = []
        self.toasts: list[tuple[str, str]] = []
        self.report: list[str] = []
        self.first_outputs = 0

    def push(self, session_id: str, kind: str, url: str, **options: Any) -> None:
        assert session_id == "s1"
        self.playlist.append((kind, url))

    def first_output(self) -> None:
        self.first_outputs += 1

    def reading(self) -> BrowserReading:
        return BrowserReading(
            session_id="s1", agent="aifred", language="de", ui_language="de", push=self.push,
            report=self.report.append, notify=lambda kind, message: self.toasts.append((kind, message)),
            on_first_output=self.first_output,
        )


def kinds(page: Page) -> list[str]:
    return [kind for kind, _ in page.playlist]


async def read(page: Page, chunks: list[str], *, between: Callable[[], Any] | None = None) -> BrowserReading:
    reading = page.reading()
    reading.start()
    for chunk in chunks:
        reading.feed(chunk)
        await asyncio.sleep(0.05)
        if between is not None:
            between()
    reading.finish()
    await asyncio.wait_for(reading.wait(), timeout=10)
    return reading


@pytest.fixture
def fake():
    return FakeTTSService(CATALOG)


def served(monkeypatch, tmp_path, fake, stream, listed):
    return serve_stream(monkeypatch, tmp_path, fake, stream, listed=listed)


class TestThroughTheService:
    def test_segments_come_to_the_playlist_in_order_with_pauses_and_without_keepalives(self, monkeypatch, tmp_path, fake):
        monkeypatch.setattr(browser_reading, "BROWSER_PARAGRAPH_PAUSE_MS", 500)
        stream = FakeStreamingService(
            after_text=[[audio_frame(b"one"), keepalive_frame(), pause_frame(500), audio_frame(b"two")]],
            after_finish=[audio_frame(b"three"), summary_frame(TRACE_XTTS)],
        )

        async def scenario() -> tuple[Page, BrowserReading]:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts", "browser"]):
                page = Page()
                return page, await read(page, ["Erster Absatz. ", "Zweiter ", "Absatz."])

        page, reading = asyncio.run(scenario())
        assert kinds(page) == ["tts", "tts_pause", "tts", "tts"]
        assert page.playlist[1][1] == "500"
        assert [(tmp_path / url.rsplit("/", 1)[-1]).read_bytes() for kind, url in page.playlist if kind == "tts"] == [
            b"one", b"two", b"three",
        ]
        assert reading.audio_urls == [url for kind, url in page.playlist if kind == "tts"]
        assert page.first_outputs == 1
        assert page.toasts == []
        assert reading.note == "XTTS · lokal"

    def test_the_request_carries_the_entries_above_the_browser_the_voices_and_the_pause(
        self, monkeypatch, tmp_path, fake,
    ):
        monkeypatch.setattr(browser_reading, "BROWSER_PARAGRAPH_PAUSE_MS", 300)
        stream = FakeStreamingService(after_finish=[audio_frame(b"x"), summary_frame(TRACE_XTTS)])

        async def scenario() -> None:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts", "browser", "edge"]):
                await read(Page(), ["Ein Satz."])

        asyncio.run(scenario())
        head = stream.started
        # the entry BEHIND the browser (edge) is never reached: it does not go to the service
        assert head["engines"] == [{"engine": "xtts", "unit": "sentence", "host": None}]
        assert head["paragraph_pause_ms"] == 300
        assert head["language"] == "de"
        assert head["voices"] == {"xtts": {"voice": "Alice", "speed": 1.0, "pitch": 1.0}}
        assert stream.texts == ["Ein Satz."]

    def test_only_aifreds_own_content_is_filtered_across_chunk_borders(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService(after_finish=[audio_frame(b"x"), summary_frame(TRACE_XTTS)])
        chunks = ["Vorher <thi", "nk>geheime Gedan", "ken</think> Danach **fett** (ST", "T: 2.5s) und weiter."]

        async def scenario() -> None:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts"]):
                await read(Page(), chunks)

        asyncio.run(scenario())
        sent = "".join(stream.texts)
        assert "geheim" not in sent and "STT" not in sent
        assert sent.split() == ["Vorher", "Danach", "**fett**", "und", "weiter."]  # Markdown is the service's business

    def test_the_text_goes_to_the_service_while_the_reply_is_still_being_written(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService(
            after_text=[[audio_frame(b"early")]], after_finish=[summary_frame(TRACE_XTTS)],
        )

        async def scenario() -> list[str]:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts"]):
                page = Page()
                reading = page.reading()
                reading.start()
                reading.feed("Erster Satz ist da. ")
                await asyncio.sleep(0.5)
                early = kinds(page)  # nothing finished yet, the sound is already on the playlist
                reading.feed("Rest.")
                reading.finish()
                await asyncio.wait_for(reading.wait(), timeout=10)
                return early

        assert asyncio.run(scenario()) == ["tts"]


class TestBrowserEntry:
    def test_the_browser_as_topmost_entry_speaks_sentence_by_sentence_and_the_service_is_not_asked(
        self, monkeypatch, tmp_path, fake,
    ):
        stream = FakeStreamingService()

        async def scenario() -> Page:
            async with served(monkeypatch, tmp_path, fake, stream, ["browser", "xtts"]):
                page = Page()
                await read(page, ["Das ist der erste Satz. Das ", "ist der zweite **fette** Satz. Rest"])
                return page

        page = asyncio.run(scenario())
        assert stream.started == {}  # no connection to the service
        assert kinds(page) == ["tts_speak"] * 3
        import json
        spoken = [json.loads(url) for _, url in page.playlist]
        assert [u["text"] for u in spoken] == ["Das ist der erste Satz.", "Das ist der zweite fette Satz.", "Rest"]
        assert spoken[0]["language"] == "de" and spoken[0]["rate"] == 1.0
        assert page.toasts == []

    def test_the_unit_whole_gives_one_utterance_at_the_end(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService()

        async def scenario() -> Page:
            async with served(monkeypatch, tmp_path, fake, stream, ["browser"]) as settings:
                settings["tts_toggles_per_engine"] = {"browser": {"unit": "whole"}}
                page = Page()
                await read(page, ["Erster Satz ist da. Zweiter Satz ist da."])
                return page

        page = asyncio.run(scenario())
        assert kinds(page) == ["tts_speak"]

    def test_the_service_fails_before_the_first_sound_then_the_browser_speaks_everything(
        self, monkeypatch, tmp_path, fake,
    ):
        stream = FakeStreamingService(on_start=[error_frame("no_engine", "nobody can speak")])

        async def scenario() -> tuple[Page, BrowserReading]:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts", "browser"]):
                page = Page()
                reading = await read(page, ["Der erste Satz ist lang genug. ", "Der zweite Satz auch. ", "Und noch ein dritter."])
                return page, reading

        page, reading = asyncio.run(scenario())
        import json
        assert kinds(page) == ["tts_speak"] * 3
        assert [json.loads(url)["text"] for _, url in page.playlist] == [
            "Der erste Satz ist lang genug.", "Der zweite Satz auch.", "Und noch ein dritter.",
        ]
        assert reading.audio_urls == []
        assert [kind for kind, _ in page.toasts] == ["warning"]
        assert "nobody can speak" in page.toasts[0][1]

    def test_the_service_fails_after_finish_but_before_the_first_sound_then_the_browser_speaks(
        self, monkeypatch, tmp_path, fake,
    ):
        """Unit "whole" at the service: the error comes only after the text is complete."""
        stream = FakeStreamingService(after_finish=[error_frame("no_engine", "late")])

        async def scenario() -> Page:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts", "browser"]):
                page = Page()
                await read(page, ["Ein einziger langer Satz ist alles."])
                return page

        page = asyncio.run(scenario())
        assert kinds(page) == ["tts_speak"]

    def test_the_service_cannot_be_started_then_the_browser_speaks(self, monkeypatch, tmp_path, fake):
        monkeypatch.setattr(tts_client, "_systemctl_start", lambda: (1, "no unit"))

        async def scenario() -> Page:
            install_fake_service(monkeypatch, tmp_path, fake, listed=["xtts", "browser"], base_url="http://127.0.0.1:9")
            monkeypatch.setattr(tts_client.TTSClient, "_is_up", lambda self: False)
            page = Page()
            await read(page, ["Nichts geht, aber der Browser spricht."])
            return page

        page = asyncio.run(scenario())
        assert kinds(page) == ["tts_speak"]
        assert page.toasts[0][0] == "warning"

    def test_without_a_browser_entry_a_failure_before_the_first_sound_is_shown_not_hidden(
        self, monkeypatch, tmp_path, fake,
    ):
        stream = FakeStreamingService(on_start=[error_frame("no_engine", "nobody can speak")])

        async def scenario() -> Page:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts"]):
                page = Page()
                await read(page, ["Ein Satz, den niemand spricht."])
                return page

        page = asyncio.run(scenario())
        assert page.playlist == []
        assert [kind for kind, _ in page.toasts] == ["error"]

    def test_nothing_switched_on_is_shown(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService()

        async def scenario() -> Page:
            async with served(monkeypatch, tmp_path, fake, stream, []):
                page = Page()
                await read(page, ["Ein Satz."])
                return page

        page = asyncio.run(scenario())
        assert page.playlist == []
        assert [kind for kind, _ in page.toasts] == ["error"]


class TestBreakOffAndAbort:
    def test_a_break_off_after_the_first_sound_is_not_taken_over_by_the_browser(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService(
            after_text=[[audio_frame(b"one")]], after_finish=[error_frame("no_engine", "engine died")],
        )

        async def scenario() -> tuple[Page, BrowserReading]:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts", "browser"]):
                page = Page()
                return page, await read(page, ["Erster Satz ist da. ", "Zweiter Satz kommt noch."])

        page, reading = asyncio.run(scenario())
        assert kinds(page) == ["tts"]  # no tts_speak: AIfred does not know what was spoken
        assert [kind for kind, _ in page.toasts] == ["error"]
        assert "engine died" in page.toasts[0][1]
        assert reading.audio_urls and len(reading.audio_urls) == 1

    def test_abort_closes_the_stream(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService(after_text=[[audio_frame(b"one")]])

        async def scenario() -> bool:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts"]):
                page = Page()
                reading = page.reading()
                reading.start()
                reading.feed("Ein Satz, der noch nicht zu Ende ist. ")
                await asyncio.sleep(0.5)
                reading.abort()
                await asyncio.wait_for(reading.wait(), timeout=5)
                await asyncio.wait_for(stream.closed.wait(), timeout=5)
                return stream.finished

        assert asyncio.run(scenario()) is False  # the service saw the connection close, no "finish" came

    def test_text_after_the_end_of_the_reading_is_ignored(self, monkeypatch, tmp_path, fake):
        stream = FakeStreamingService(on_start=[error_frame("no_engine", "nope")])

        async def scenario() -> None:
            async with served(monkeypatch, tmp_path, fake, stream, ["xtts"]):
                page = Page()
                reading = page.reading()
                reading.start()
                reading.feed("Ein Satz ist da. ")
                await asyncio.wait_for(reading.wait(), timeout=5)
                reading.feed("Spät.")
                reading.finish()

        asyncio.run(scenario())
