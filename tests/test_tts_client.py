"""TTSClient: Frames, Fehlerabbildung und Lesebefehle gegen einen httpx.MockTransport (kein Netz)."""

import asyncio
import contextlib
import getpass
import json
import struct
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import aifred.lib.tts_client as tts_client
from aifred.lib.tts_client import (
    ERROR_CLASSES,
    BadRequest,
    EngineRef,
    EngineStarting,
    FrameDecoder,
    IncompatibleService,
    InvalidRequest,
    NoEngineAvailable,
    Overloaded,
    StreamBroken,
    TextTooLong,
    TextStream,
    TTSClient,
    TTSSuiteStartFailed,
    TTSSuiteUnreachable,
    Unauthorized,
    UnknownEngine,
    UnknownHost,
    UnknownServiceError,
    UnsupportedLanguage,
    new_run_id,
)

# conftest replaces the module's function by a guard in every test; this test needs the real one.
REAL_SYSTEMCTL_START = tts_client._systemctl_start


# ---- Test helpers: encode frames like the service does ---------------------------


def frame(header: dict[str, Any], body: bytes = b"") -> bytes:
    head = json.dumps({**header, "bytes": len(body)}).encode("utf-8")
    return struct.pack(">I", len(head)) + head + body


def audio(data: bytes, engine: str = "xtts@mini", keepalive: bool = False) -> bytes:
    header = {"kind": "audio", "media_type": "audio/wav", "engine": engine, "keepalive": keepalive, "pause_ms": 0}
    return frame(header, data)


def pause(pause_ms: int) -> bytes:
    return frame({"kind": "audio", "media_type": "", "engine": "", "keepalive": False, "pause_ms": pause_ms})


def summary(trace: list[dict[str, Any]] | None = None, switch: str | None = None) -> bytes:
    return frame({"kind": "summary"}, json.dumps({"trace": trace or [], "switch": switch}).encode())


def error_frame(code: str, detail: str) -> bytes:
    return frame({"kind": "error"}, json.dumps({"error": code, "detail": detail}).encode())


async def chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def frames_response(data: bytes, size: int = 7) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": tts_client.FRAMES_MEDIA_TYPE}, content=chunks(data, size))


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> TTSClient:
    return TTSClient("http://tts.test", transport=httpx.MockTransport(handler))


LIST = [EngineRef("xtts", "sentence", None)]


async def collect(client: TTSClient, **kwargs: Any) -> tuple[list[tts_client.Segment], tts_client.SpeakSummary]:
    kwargs.setdefault("engines", LIST)
    kwargs.setdefault("paragraph_pause_ms", 0)
    stream = await client.speak("Hallo", language="de", **{"run": "r", **kwargs})
    segments = [segment async for segment in stream]
    return segments, stream.summary


# ---- Frames ---------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 2, 5, 1000])
def test_speak_decodes_frames_cut_anywhere(size: int) -> None:
    data = audio(b"RIFF-one") + audio(b"", keepalive=True) + audio(b"RIFF-two", engine="edge") + summary()
    segments, result = asyncio.run(collect(make_client(lambda _: frames_response(data, size))))
    assert [s.audio for s in segments] == [b"RIFF-one", b"", b"RIFF-two"]
    assert [s.keepalive for s in segments] == [False, True, False]
    assert segments[0].media_type == "audio/wav"
    assert segments[2].engine == "edge"
    assert result.trace == ()
    assert result.switch is None


def test_summary_carries_trace_and_switch() -> None:
    trace: list[dict[str, Any]] = [
        {"entry": "xtts@mini", "outcome": "failed", "reason": "timeout", "detail": "slow"},
        {"entry": "edge", "outcome": "spoke", "reason": None, "detail": ""},
    ]
    data = audio(b"x") + summary(trace, "timeout")
    _, result = asyncio.run(collect(make_client(lambda _: frames_response(data))))
    assert result.switch == "timeout"
    assert [(s.entry, s.outcome, s.reason, s.detail) for s in result.trace] == [
        ("xtts@mini", "failed", "timeout", "slow"),
        ("edge", "spoke", None, ""),
    ]


def test_speak_sends_head_as_json() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["authorization"] = request.headers.get("authorization")
        return frames_response(summary())

    asyncio.run(collect(make_client(handler), voice="Alice", engines=[EngineRef("edge", "paragraph", None), EngineRef("xtts", "whole", "Aragon")], voices={"edge": {"voice": "x"}}, run="r1", wait=False))
    assert seen["text"] == "Hallo"
    assert seen["language"] == "de"
    assert seen["voice"] == "Alice"
    assert seen["engines"] == [
        {"engine": "edge", "unit": "paragraph", "host": None},
        {"engine": "xtts", "unit": "whole", "host": "Aragon"},
    ]
    assert "engine" not in seen
    assert seen["voices"] == {"edge": {"voice": "x"}}
    assert seen["run"] == "r1"
    assert seen["wait"] is False
    assert seen["authorization"] is None  # AIfred sends no token
    assert seen["paragraph_pause_ms"] == 0


def test_speak_sends_the_list_as_given_without_checking_it() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return frames_response(summary())

    asyncio.run(collect(make_client(handler), engines=[]))
    assert seen["engines"] == []  # the service refuses an empty list (422), the client does not second-guess


def test_the_paragraph_pause_of_a_reading_is_required_and_sent() -> None:
    with pytest.raises(TypeError):
        asyncio.run(make_client(lambda _: frames_response(summary())).speak("Hallo", language="de", engines=LIST))  # type: ignore[call-arg]
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return frames_response(summary())

    asyncio.run(collect(make_client(handler), paragraph_pause_ms=750))
    assert seen["paragraph_pause_ms"] == 750


def test_a_pause_marker_is_a_segment_with_pause_ms_in_speaking_order() -> None:
    data = audio(b"one") + pause(500) + audio(b"", keepalive=True) + audio(b"two") + summary()
    segments, _ = asyncio.run(collect(make_client(lambda _: frames_response(data))))
    assert [(s.audio, s.keepalive, s.pause_ms) for s in segments] == [
        (b"one", False, 0), (b"", False, 500), (b"", True, 0), (b"two", False, 0),
    ]
    assert segments[1].media_type == "" and segments[1].engine == ""


def test_the_list_of_a_reading_is_required() -> None:
    with pytest.raises(TypeError):
        asyncio.run(make_client(lambda _: frames_response(summary())).speak("Hallo", language="de"))  # type: ignore[call-arg]


def test_the_run_id_of_a_reading_is_required_and_sent() -> None:
    with pytest.raises(TypeError):
        asyncio.run(make_client(lambda _: frames_response(summary())).speak(  # type: ignore[call-arg]
            "Hallo", language="de", engines=LIST, paragraph_pause_ms=0,
        ))
    assert new_run_id() != new_run_id()


def test_audio_before_an_error_frame_in_the_same_read_is_not_lost() -> None:
    data = audio(b"first") + audio(b"second") + error_frame("overloaded", "busy")
    received: list[bytes] = []

    async def run_collecting() -> None:
        stream = await make_client(lambda _: frames_response(data, 1000)).speak(
            "Hallo", language="de", engines=LIST, paragraph_pause_ms=0, run="r",
        )
        async for segment in stream:
            received.append(segment.audio)

    with pytest.raises(Overloaded):
        asyncio.run(run_collecting())
    assert received == [b"first", b"second"]


def test_a_frame_the_client_cannot_read_is_a_broken_stream_not_a_key_error() -> None:
    data = audio(b"first") + frame({"nothing": "useful"}, b"")
    received: list[bytes] = []

    async def run_collecting() -> None:
        stream = await make_client(lambda _: frames_response(data, 1000)).speak(
            "Hallo", language="de", engines=LIST, paragraph_pause_ms=0, run="r",
        )
        async for segment in stream:
            received.append(segment.audio)

    with pytest.raises(StreamBroken, match="cannot read"):
        asyncio.run(run_collecting())
    assert received == [b"first"]


def test_error_frame_after_first_frame_raises_while_iterating() -> None:
    data = audio(b"first") + error_frame("overloaded", "busy")

    received: list[bytes] = []

    async def run_collecting() -> None:
        stream = await make_client(lambda _: frames_response(data)).speak("Hallo", language="de", engines=LIST, paragraph_pause_ms=0, run="r")
        async for segment in stream:
            received.append(segment.audio)

    with pytest.raises(Overloaded):
        asyncio.run(run_collecting())
    assert received == [b"first"]


def test_stream_without_summary_is_an_error() -> None:
    with pytest.raises(StreamBroken):
        asyncio.run(collect(make_client(lambda _: frames_response(audio(b"x")))))


def test_stream_cut_in_the_middle_of_a_frame_is_an_error() -> None:
    data = audio(b"x") + summary()
    with pytest.raises(StreamBroken):
        asyncio.run(collect(make_client(lambda _: frames_response(data[:-3]))))


def test_summary_before_end_is_not_available() -> None:
    async def run() -> None:
        stream = await make_client(lambda _: frames_response(audio(b"x") + summary())).speak("Hallo", language="de", engines=LIST, paragraph_pause_ms=0, run="r")
        _ = stream.summary
        await stream.aclose()

    with pytest.raises(RuntimeError):
        asyncio.run(run())


def test_aclose_aborts_the_reading() -> None:
    closed = {"response": False}

    async def endless() -> AsyncIterator[bytes]:
        try:
            while True:
                yield audio(b"x")
                await asyncio.sleep(0)
        finally:
            closed["response"] = True

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    async def run() -> int:
        stream = await make_client(handler).speak("Hallo", language="de", engines=LIST, paragraph_pause_ms=0, run="r")
        count = 0
        async for _segment in stream:
            count += 1
            if count == 3:
                break
        await stream.aclose()
        return count

    assert asyncio.run(run()) == 3
    assert closed["response"]


def test_connection_lost_during_reading() -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield audio(b"x")
        raise httpx.ReadError("connection reset")

    with pytest.raises(TTSSuiteUnreachable):
        asyncio.run(collect(make_client(lambda _: httpx.Response(200, content=broken()))))


def test_frame_decoder_pending_bytes() -> None:
    decoder = FrameDecoder()
    assert decoder.feed(audio(b"abc")[:5]) == []
    assert decoder.pending == 5


# ---- Errors before the first frame ----------------------------------------------


ERROR_CASES = [
    ("no_engine", 503, NoEngineAvailable),
    ("starting", 503, EngineStarting),
    ("overloaded", 429, Overloaded),
    ("unknown_engine", 404, UnknownEngine),
    ("unknown_host", 404, UnknownHost),
    ("invalid_request", 422, InvalidRequest),
    ("engine_in_use", 409, tts_client.EngineInUse),
    ("engine_control_failed", 502, tts_client.EngineControlFailed),
    ("unsupported_language", 422, UnsupportedLanguage),
    ("text_too_long", 413, TextTooLong),
    ("unauthorized", 401, Unauthorized),
    ("bad_request", 400, BadRequest),
]


def test_error_table_is_complete() -> None:
    assert set(ERROR_CLASSES) == {code for code, _, _ in ERROR_CASES}


@pytest.mark.parametrize(("code", "status", "error_class"), ERROR_CASES)
def test_speak_error_status_before_first_frame(code: str, status: int, error_class: type[Exception]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": code, "detail": "because"})

    with pytest.raises(error_class) as raised:
        asyncio.run(collect(make_client(handler)))
    assert str(raised.value) == "because"
    assert raised.value.status == status  # type: ignore[attr-defined]


def test_no_engine_carries_trace() -> None:
    trace = [{"entry": "xtts@mini", "outcome": "skipped", "reason": "stopped", "detail": ""}]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "no_engine", "detail": "none", "trace": trace})

    with pytest.raises(NoEngineAvailable) as raised:
        asyncio.run(collect(make_client(handler)))
    assert [(s.entry, s.outcome, s.reason) for s in raised.value.trace] == [("xtts@mini", "skipped", "stopped")]


def test_unknown_error_code_is_an_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "brand_new", "detail": "?"})

    with pytest.raises(UnknownServiceError, match="brand_new") as raised:
        asyncio.run(collect(make_client(handler)))
    assert raised.value.status == 500


def test_non_json_error_body_is_an_error() -> None:
    with pytest.raises(UnknownServiceError, match="Bad Gateway"):
        asyncio.run(collect(make_client(lambda _: httpx.Response(502, text="Bad Gateway"))))


def test_connection_error_is_unreachable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(TTSSuiteUnreachable):
        asyncio.run(collect(client))
    with pytest.raises(TTSSuiteUnreachable):
        client.engines()


def test_timeout_is_unreachable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TTSSuiteUnreachable):
        make_client(handler).engines()


# ---- Read calls -----------------------------------------------------------------


def test_client_writes_nothing_but_the_start_on_a_given_card() -> None:
    """AIfred writes nothing into the service and stops no container there; its one command is the
    calibration's start of an engine on a given card."""
    for name in ("set_config", "start", "stop", "reload", "sync_voices", "config", "_auth_headers"):
        assert not hasattr(TTSClient, name), name
    assert not hasattr(tts_client, "_read_token")
    assert not hasattr(tts_client, "ConfigInvalid")


def test_start_on_gpu_sends_the_card_and_the_local_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret\n", encoding="utf-8")
    monkeypatch.setattr(tts_client, "TTS_SUITE_TOKEN_FILE", token_file)
    seen: list[tuple[str, str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.params.get("gpu"), request.headers.get("x-tts-suite-token")))
        return httpx.Response(204)

    make_client(handler).start_on_gpu("xtts", "4")
    assert seen == [("POST", "/engines/xtts/start", "4", "secret")]


def test_start_on_gpu_maps_the_refusals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(tts_client, "TTS_SUITE_TOKEN_FILE", token_file)
    for status, code, error in (
        (409, "engine_in_use", tts_client.EngineInUse),
        (502, "engine_control_failed", tts_client.EngineControlFailed),
        (401, "unauthorized", tts_client.Unauthorized),
    ):
        client = make_client(lambda _, s=status, c=code: httpx.Response(s, json={"error": c, "detail": "why"}))
        with pytest.raises(error, match="why"):
            client.start_on_gpu("xtts", "4")


def test_read_calls_are_get_requests_without_token() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json=[])

    client = make_client(handler)
    client.engines()
    client.voices()
    assert seen == [("GET", "/engines", None), ("GET", "/voices", None)]


def test_release_all_sends_the_word_all_and_decodes_the_results() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=[
            {"engine": "xtts", "host": None, "outcome": "stopped", "detail": ""},
            {"engine": "fish", "host": "Aragon", "outcome": "in_use", "detail": "a reading holds it"},
        ])

    results = make_client(handler).release("all")
    assert seen == {"path": "/release", "body": {"targets": "all"}, "authorization": None}
    assert results == [
        tts_client.ReleaseResult("xtts", None, "stopped", ""),
        tts_client.ReleaseResult("fish", "Aragon", "in_use", "a reading holds it"),
    ]


def test_release_sends_the_given_targets() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=[])

    make_client(handler).release([tts_client.EngineTarget("xtts", None), tts_client.EngineTarget("fish", "Aragon")])
    assert seen == {"targets": [{"engine": "xtts", "host": None}, {"engine": "fish", "host": "Aragon"}]}


def test_release_maps_a_refused_request_to_the_error_table() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "unknown_engine", "detail": "no engine 'nope'"})

    with pytest.raises(UnknownEngine, match="nope"):
        make_client(handler).release([tts_client.EngineTarget("nope", None)])


def test_release_without_a_service_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TTSSuiteUnreachable, match="/release"):
        make_client(handler).release("all")


def test_a_catalog_with_other_fields_is_an_incompatible_service_naming_them() -> None:
    row = {"engine": "xtts", "host": None, "extra_field": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    with pytest.raises(IncompatibleService) as raised:
        make_client(handler).engines()
    assert "extra_field" in str(raised.value) and "missing" in str(raised.value) and "/engines" in str(raised.value)


def test_engines_and_voices_are_decoded() -> None:
    engine = {
        "engine": "xtts", "host": None, "label": {"de": "XTTS", "en": "XTTS"},
        "label_short": {"de": "XTTS", "en": "XTTS"},
        "status": "running", "unit_default": "paragraph", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": False, "start_hint": "image missing",
        "vram_mib": {"GPU-1": 4096},
        "idle_remaining_s": 1500.0,
        "default_vram_mib": 6024, "default_max_parallel": 1, "default_max_queue": 4,
    }
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        if request.url.path == "/engines":
            return httpx.Response(200, json=[engine])
        if request.url.path == "/voices":
            return httpx.Response(200, json=[{"name": "Alice", "label": "Alice (de)"}])
        return httpx.Response(404)

    client = make_client(handler)
    engines = client.engines()
    assert (engines[0].engine, engines[0].host, engines[0].unit_default) == ("xtts", None, "paragraph")
    assert engines[0].status == "running"
    assert engines[0].installed is False
    assert engines[0].start_hint == "image missing"
    assert engines[0].vram_mib == {"GPU-1": 4096}
    assert (engines[0].default_vram_mib, engines[0].default_max_parallel, engines[0].default_max_queue) == (6024, 1, 4)
    voices = client.voices("xtts")
    assert (voices[0].name, voices[0].label) == ("Alice", "Alice (de)")
    assert seen_params[1] == {"engine": "xtts"}


# ---- Service on demand (ensure_ready) ---------------------------------------------


class FakeHealth:
    """Answers /health from a script: True = ok, False = connection refused."""

    def __init__(self, *answers: bool) -> None:
        self._answers = iter(answers)
        self.asked = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        self.asked += 1
        if not next(self._answers):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"status": "ok"})


def test_ensure_ready_does_not_start_a_running_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> tuple[int, str]:
        raise AssertionError("systemctl must not run")

    monkeypatch.setattr(tts_client, "_systemctl_start", forbidden)
    health = FakeHealth(True)
    make_client(health).ensure_ready(timeout_s=1)
    assert health.asked == 1


def test_ensure_ready_starts_the_service_once_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[int] = []

    def fake_systemctl() -> tuple[int, str]:
        started.append(1)
        return 0, ""

    monkeypatch.setattr(tts_client, "_systemctl_start", fake_systemctl)
    monkeypatch.setattr(tts_client, "sleep", lambda _seconds: None)
    health = FakeHealth(False, False, False, True)  # down, still starting, up
    make_client(health).ensure_ready(timeout_s=60)
    assert started == [1]
    assert health.asked == 4


def test_ensure_ready_reports_failed_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_client, "_systemctl_start", lambda: (5, "Unit tts-suite.service not found"))
    with pytest.raises(TTSSuiteStartFailed, match="not found"):
        make_client(FakeHealth(False)).ensure_ready(timeout_s=1)


def test_ensure_ready_times_out_when_health_stays_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(100))
    monkeypatch.setattr(tts_client, "_systemctl_start", lambda: (0, ""))
    monkeypatch.setattr(tts_client, "sleep", lambda _seconds: None)
    monkeypatch.setattr(tts_client, "monotonic", lambda: next(clock))
    with pytest.raises(TTSSuiteStartFailed, match="within 3s"):
        make_client(FakeHealth(*[False] * 20)).ensure_ready(timeout_s=3)


def test_systemctl_start_runs_the_system_unit_of_the_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(tts_client.subprocess, "run", fake_run)
    assert REAL_SYSTEMCTL_START() == (0, "")
    assert seen["command"] == ["systemctl", "start", f"tts-suite@{getpass.getuser()}.service"]


# ---- WebSocket stream (open_stream) against a local fake server on loopback ----------

SocketHandler = Callable[[ServerConnection], Any]


@contextlib.asynccontextmanager
async def fake_service(handler: SocketHandler) -> AsyncIterator[TTSClient]:
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield TTSClient(f"http://127.0.0.1:{port}")


def test_stream_url_follows_the_scheme() -> None:
    assert tts_client._stream_url("http://h:5090") == "ws://h:5090/speak/stream"
    assert tts_client._stream_url("https://h") == "wss://h/speak/stream"


def test_stream_segments_arrive_before_finish() -> None:
    received: list[dict[str, Any]] = []

    async def handler(socket: ServerConnection) -> None:
        received.append(json.loads(await socket.recv()))
        received.append(json.loads(await socket.recv()))  # first piece
        await socket.send(audio(b"one"))
        await socket.send(audio(b"", keepalive=True))
        # the audio is out before the client has sent "finish"
        received.append(json.loads(await socket.recv()))
        received.append(json.loads(await socket.recv()))
        await socket.send(audio(b"two"))
        await socket.send(summary([], "timeout"))
        await socket.close()

    async def run() -> tuple[list[tts_client.Segment], tts_client.SpeakSummary]:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", voice="Alice", engines=[EngineRef("edge", "sentence", None)], paragraph_pause_ms=300, voices={"edge": {"voice": "x"}}, run="r1", wait=False)
            assert isinstance(stream, TextStream)
            await stream.feed("Hallo ")
            segments: list[tts_client.Segment] = []
            iterator = stream.__aiter__()
            segments.append(await anext(iterator))  # arrives while finish is not sent
            segments.append(await anext(iterator))
            await stream.feed("Welt.")
            await stream.finish()
            segments.extend([segment async for segment in iterator])
            return segments, stream.summary

    segments, result = asyncio.run(run())
    assert [s.audio for s in segments] == [b"one", b"", b"two"]
    assert [s.keepalive for s in segments] == [False, True, False]
    assert result.switch == "timeout"
    start, first, second, finish = received
    assert start["type"] == "start"
    assert (start["language"], start["voice"], start["engines"], start["run"], start["wait"]) == (
        "de", "Alice", [{"engine": "edge", "unit": "sentence", "host": None}], "r1", False,
    )
    assert start["voices"] == {"edge": {"voice": "x"}}
    assert first == {"type": "text", "text": "Hallo "}
    assert second == {"type": "text", "text": "Welt."}
    assert finish == {"type": "finish"}


def test_stream_sends_no_authorization_header() -> None:
    seen: list[str | None] = []

    async def handler(socket: ServerConnection) -> None:
        seen.append(socket.request.headers.get("Authorization"))
        await socket.recv()
        await socket.send(summary())
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            assert [s async for s in stream] == []

    asyncio.run(run())
    assert seen == [None]


def test_error_frame_before_first_segment() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(error_frame("unsupported_language", "no voice for fr"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="fr", engines=LIST, paragraph_pause_ms=0, run="r")
            async for _ in stream:
                pass

    with pytest.raises(UnsupportedLanguage, match="no voice for fr"):
        asyncio.run(run())


def test_feed_after_refusal_raises_the_refusal() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(error_frame("unsupported_language", "no voice for fr"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="fr", engines=LIST, paragraph_pause_ms=0, run="r")
            await asyncio.sleep(0.2)
            await stream.feed("Bonjour")

    with pytest.raises(UnsupportedLanguage):
        asyncio.run(run())


def test_error_frame_after_first_segment() -> None:
    received: list[bytes] = []

    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"first"))
        await socket.send(error_frame("overloaded", "busy"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            async for segment in stream:
                received.append(segment.audio)

    with pytest.raises(Overloaded):
        asyncio.run(run())
    assert received == [b"first"]


def test_a_frame_the_client_cannot_read_ends_the_websocket_reading_instead_of_waiting_for_ever() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"first"))
        await socket.send(frame({"nothing": "useful"}, b""))
        with contextlib.suppress(ConnectionClosed):
            await socket.recv()   # stays open until the client has given up on the reading

    received: list[bytes] = []

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            async for segment in stream:
                received.append(segment.audio)

    with pytest.raises(StreamBroken, match="cannot read"):
        asyncio.run(asyncio.wait_for(run(), timeout=3))
    assert received == [b"first"]


def test_a_service_that_goes_silent_on_an_open_reading_is_a_broken_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_client, "TTS_SUITE_STREAM_READ_TIMEOUT_S", 0.3)

    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        with contextlib.suppress(ConnectionClosed):
            await socket.recv()   # never answers

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            async for _ in stream:
                pass

    with pytest.raises(StreamBroken, match="sent nothing"):
        asyncio.run(asyncio.wait_for(run(), timeout=5))


def test_stream_without_summary_is_broken() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"x"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            async for _ in stream:
                pass

    with pytest.raises(StreamBroken):
        asyncio.run(run())


def test_summary_before_end_is_not_available_on_text_stream() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.wait_closed()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            try:
                _ = stream.summary
            finally:
                await stream.aclose()

    with pytest.raises(RuntimeError):
        asyncio.run(run())


def test_aclose_aborts_the_stream() -> None:
    closed = asyncio.Event()

    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"x"))
        await socket.wait_closed()
        closed.set()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")
            iterator = stream.__aiter__()
            assert (await anext(iterator)).audio == b"x"
            await stream.aclose()
            await asyncio.wait_for(closed.wait(), 5)

    asyncio.run(run())


def test_refused_connection_is_unreachable() -> None:
    async def run() -> None:
        async with fake_service(lambda socket: socket.close()) as client:
            pass
        await client.open_stream(language="de", engines=LIST, paragraph_pause_ms=0, run="r")  # the server is gone: nobody listens on the port

    with pytest.raises(TTSSuiteUnreachable):
        asyncio.run(run())


def test_voices_dir_is_the_one_the_service_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from fake_tts_service import FakeTTSService, install_fake_service

    fake = FakeTTSService([])
    fake.voices_dir = "/data/voices"
    install_fake_service(monkeypatch, tmp_path, fake)
    assert tts_client.get_tts_client().voices_dir() == Path("/data/voices")
