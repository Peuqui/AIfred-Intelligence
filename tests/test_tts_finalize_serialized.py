"""Die gesprochene Antwort im Browser-Zustand (Mixin): Text -> Lesung -> Playlist, Vermerk und Audio an der
Blase, Abbruch, Fehler als Meldung, und die nächste Antwort wartet auf die Lesung der vorigen
(Symposion: sonst mischen sich die Audios auf der Playlist)."""

import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fake_tts_service import (
    FakeStreamingService,
    FakeTTSService,
    audio_frame,
    catalog_row,
    error_frame,
    serve_stream,
    summary_frame,
)

from aifred.lib import api, audio_processing
from aifred.state import _tts_streaming_mixin as mixin_module
from aifred.state._tts_streaming_mixin import (
    TTSStreamingMixin,
    abort_tts_reading,
    discard_tts_backend_state,
    get_tts_backend_state,
    take_tts_notices,
)

CATALOG = [catalog_row("xtts"), catalog_row("browser", browser=True, needs_gpu=False)]
TRACE = [{"entry": "xtts", "outcome": "spoke", "reason": None, "detail": ""}]


class _Reply:
    """The parts of the Reflex state the reading touches."""

    enable_tts = True
    tts_autoplay = True
    ui_language = "de"
    _last_detected_language = "de"
    tts_agents: ClassVar[dict[str, Any]] = {}
    media_audio_url = ""
    media_paused_for_tts = False
    _UI_YIELD_INTERVAL = 0.05

    def __init__(self, session_id: str = "mixin-test") -> None:
        self.session_id = session_id
        self._tts_streaming_active = False
        self._tts_finalize_spawned = False
        self._tts_streaming_agent = "aifred"
        self._js_chunk_buffer = ""
        self._ui_yield_ts = 0.0
        self.debug: list[str] = []
        self.chat = SimpleNamespace(chat_history=[
            {"role": "user", "content": "Frage", "agent": "", "has_audio": False},
            {"role": "assistant", "agent": "aifred", "content": "Antwort", "metadata": {}, "has_audio": False},
        ])
        self.saved = 0
        self.streaming = SimpleNamespace(current_ai_response="")

    def add_debug(self, message: str) -> None:
        self.debug.append(message)

    def _chat_sub(self) -> Any:
        return self.chat

    def _streaming_sub(self) -> Any:
        return self.streaming

    def _save_current_session(self) -> None:
        self.saved += 1

    def tts_agent_enabled(self, agent: str) -> bool:
        return True

    _resolve_tts_language = TTSStreamingMixin._resolve_tts_language
    _pause_media_for_tts = TTSStreamingMixin._pause_media_for_tts
    _tts_streaming_wanted = TTSStreamingMixin._tts_streaming_wanted
    stream_text_to_ui = TTSStreamingMixin.stream_text_to_ui
    flush_stream_to_ui = TTSStreamingMixin.flush_stream_to_ui
    _init_streaming_tts = TTSStreamingMixin._init_streaming_tts
    _abort_streaming_tts = TTSStreamingMixin._abort_streaming_tts
    _spawn_tts_finalize = TTSStreamingMixin._spawn_tts_finalize
    _wait_for_tts_finalize = TTSStreamingMixin._wait_for_tts_finalize
    _keep_reading_audio = TTSStreamingMixin._keep_reading_audio
    _finalize_streaming_tts_in_background = TTSStreamingMixin._finalize_streaming_tts_in_background


@pytest.fixture
def pushed(monkeypatch):
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(api, "browser_push", lambda session_id, kind, url, **options: events.append((kind, url)))
    joined: list[list[str]] = []

    def fake_save(urls: list[str], session_id: str) -> str:
        joined.append(list(urls))
        return "/_upload/audio/mixin-test/combined.wav"

    monkeypatch.setattr(audio_processing, "save_audio_to_session", fake_save)
    events_and_joined = SimpleNamespace(events=events, joined=joined)
    return events_and_joined


@pytest.fixture
def fake():
    return FakeTTSService(CATALOG)


def served(monkeypatch, tmp_path, fake, stream, listed=("xtts", "browser")):
    return serve_stream(monkeypatch, tmp_path, fake, stream, listed=list(listed))


def test_a_new_reading_ends_a_reading_that_is_still_open(monkeypatch, tmp_path, fake, pushed) -> None:
    """Two inits for one reply must not leave a task and a connection to the service waiting for text."""
    stream = FakeStreamingService(after_text=[], after_finish=[summary_frame(TRACE)])

    async def scenario() -> tuple[Any, Any, bool]:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply()
            reply._init_streaming_tts("aifred")
            first = get_tts_backend_state(reply.session_id).reading
            await asyncio.sleep(0.1)
            reply._init_streaming_tts("aifred")
            await asyncio.sleep(0.1)
            return first, get_tts_backend_state(reply.session_id).reading, first._task.done()  # type: ignore[union-attr]

    first, second, first_task_done = asyncio.run(scenario())
    assert first is not second and first.aborted and first_task_done


def test_a_reply_is_spoken_while_written_and_its_audio_and_note_land_on_the_bubble(
    monkeypatch, tmp_path, fake, pushed,
) -> None:
    stream = FakeStreamingService(
        after_text=[[audio_frame(b"one")]], after_finish=[audio_frame(b"two"), summary_frame(TRACE)],
    )

    async def scenario() -> _Reply:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply()
            reply._init_streaming_tts("aifred")
            assert reply.stream_text_to_ui("Der erste Satz ist da. ") in (True, False)
            await asyncio.sleep(0.3)
            reply.stream_text_to_ui("Der zweite folgt.")
            reply._spawn_tts_finalize()
            await get_tts_backend_state(reply.session_id).finalize_task  # type: ignore[misc]
            return reply

    reply = asyncio.run(scenario())
    assert [kind for kind, _ in pushed.events if kind == "tts"] == ["tts", "tts"]
    assert len(pushed.joined[0]) == 2
    bubble = reply.chat.chat_history[1]
    assert bubble["has_audio"] and bubble["tts_note"] == "XTTS · lokal"
    assert bubble["metadata"]["audio_urls"] == ["/_upload/audio/mixin-test/combined.wav"]
    assert ("bubble_audio", "/_upload/audio/mixin-test/combined.wav") in pushed.events
    assert ("bubble_tts_note", "XTTS · lokal") in pushed.events
    assert reply.streaming.current_ai_response != "" or reply._js_chunk_buffer != ""
    assert reply._tts_streaming_active is False
    assert reply.saved == 1


def test_what_the_browser_spoke_itself_lands_on_the_bubble_too(monkeypatch, tmp_path, fake, pushed) -> None:
    async def scenario() -> _Reply:
        async with served(monkeypatch, tmp_path, fake, FakeStreamingService(), listed=("browser",)):
            reply = _Reply()
            reply._init_streaming_tts("aifred")
            reply.stream_text_to_ui("Das spricht der Browser selbst.")
            reply._spawn_tts_finalize()
            await get_tts_backend_state(reply.session_id).finalize_task  # type: ignore[misc]
            return reply

    reply = asyncio.run(scenario())
    bubble = reply.chat.chat_history[1]
    assert bubble["has_audio"] and bubble["metadata"]["audio_urls"] == []
    assert bubble["metadata"]["browser_speech"][0]["text"] == "Das spricht der Browser selbst."
    assert bubble["tts_note"] == "Browser · Gerät"   # named by the client itself: no service involved
    assert any(kind == "bubble_browser_speech" for kind, _ in pushed.events)


def test_the_first_sound_pauses_the_media_player(monkeypatch, tmp_path, fake, pushed) -> None:
    stream = FakeStreamingService(after_finish=[audio_frame(b"one"), summary_frame(TRACE)])

    async def scenario() -> _Reply:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply()
            reply.media_audio_url = "/hoerbuch.mp3"
            reply._init_streaming_tts("aifred")
            reply.stream_text_to_ui("Ein Satz.")
            reply._spawn_tts_finalize()
            await get_tts_backend_state(reply.session_id).finalize_task  # type: ignore[misc]
            return reply

    assert asyncio.run(scenario()).media_paused_for_tts is True


def test_a_failure_the_user_must_know_becomes_a_notice_for_a_toast(monkeypatch, tmp_path, fake, pushed) -> None:
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]], after_finish=[error_frame("no_engine", "died")])

    async def scenario() -> list[tuple[str, str]]:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply("notice-test")
            reply._init_streaming_tts("aifred")
            reply.stream_text_to_ui("Der erste Satz ist da. ")
            await asyncio.sleep(0.3)
            reply.stream_text_to_ui("Der zweite.")
            reply._spawn_tts_finalize()
            await get_tts_backend_state(reply.session_id).finalize_task  # type: ignore[misc]
            return take_tts_notices("notice-test")

    notices = asyncio.run(scenario())
    assert [kind for kind, _ in notices] == ["error"] and "died" in notices[0][1]
    assert take_tts_notices("notice-test") == []  # taken once


def test_a_cancelled_reply_aborts_its_reading_and_the_stream_closes(monkeypatch, tmp_path, fake, pushed) -> None:
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]])

    async def scenario() -> bool:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply("abort-test")
            reply._init_streaming_tts("aifred")
            reply.stream_text_to_ui("Ein Satz, der nie zu Ende kommt. ")
            await asyncio.sleep(0.3)
            reply._abort_streaming_tts()
            reading = get_tts_backend_state("abort-test").reading
            assert reading is not None
            await asyncio.wait_for(reading.wait(), timeout=5)
            await asyncio.wait_for(stream.closed.wait(), timeout=5)
            return stream.finished

    assert asyncio.run(scenario()) is False
    assert pushed.joined == []  # nothing is kept on a bubble for an aborted reply


def test_leaving_the_session_aborts_the_reading(monkeypatch, tmp_path, fake, pushed) -> None:
    stream = FakeStreamingService(after_text=[[audio_frame(b"one")]])

    async def scenario() -> bool:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply("leave-test")
            reply._init_streaming_tts("aifred")
            reply.stream_text_to_ui("Ein Satz, der nie zu Ende kommt. ")
            await asyncio.sleep(0.3)
            reading = get_tts_backend_state("leave-test").reading
            assert reading is not None
            discard_tts_backend_state("leave-test")
            await asyncio.wait_for(reading.wait(), timeout=5)
            await asyncio.wait_for(stream.closed.wait(), timeout=5)
            return reading.aborted

    assert asyncio.run(scenario()) is True
    assert "leave-test" not in mixin_module._tts_backend_states
    abort_tts_reading("unknown-session")  # nothing to abort, no error


def test_next_reply_waits_for_the_previous_reading(monkeypatch, tmp_path, fake, pushed) -> None:
    stream = FakeStreamingService(after_finish=[audio_frame(b"one"), summary_frame(TRACE)])

    async def scenario() -> dict[str, Any]:
        async with served(monkeypatch, tmp_path, fake, stream):
            reply = _Reply("race-test")
            reply._init_streaming_tts("sokrates")
            reply.stream_text_to_ui("Antwort von Sokrates.")
            reply._spawn_tts_finalize()
            await reply._wait_for_tts_finalize()
            tts_state = get_tts_backend_state(reply.session_id)
            finalize_was_done = tts_state.finalize_task is not None and tts_state.finalize_task.done()
            reply._init_streaming_tts("pater")  # the next agent starts
            started = reply._tts_streaming_active
            reply._abort_streaming_tts()
            return {"finalize_was_done": finalize_was_done, "streaming_active": started}

    assert asyncio.run(scenario()) == {"finalize_was_done": True, "streaming_active": True}


def test_without_a_finalize_in_flight_nothing_waits() -> None:
    async def scenario() -> None:
        reply = _Reply("no-finalize")
        await asyncio.wait_for(reply._wait_for_tts_finalize(), timeout=0.1)

    asyncio.run(scenario())


def test_streaming_is_wanted_with_tts_and_autoplay_and_the_agents_voice_on() -> None:
    reply = _Reply()
    assert reply._tts_streaming_wanted("aifred") is True
    reply.tts_autoplay = False
    assert reply._tts_streaming_wanted("aifred") is False
