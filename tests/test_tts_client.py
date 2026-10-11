"""TTSClient: Frames, Fehlerabbildung und Steuerbefehle gegen einen httpx.MockTransport (kein Netz)."""

import asyncio
import contextlib
import json
import os
import struct
import subprocess
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

import aifred.lib.tts_client as tts_client
from aifred.lib.tts_client import (
    ERROR_CLASSES,
    BadRequest,
    ConfigInvalid,
    EngineControlFailed,
    EngineInUse,
    EngineStarting,
    FrameDecoder,
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
    UnknownServiceError,
    UnsupportedLanguage,
)

TOKEN = "secret"


# ---- Test helpers: encode frames like the service does ---------------------------


def frame(header: dict[str, Any], body: bytes = b"") -> bytes:
    head = json.dumps({**header, "bytes": len(body)}).encode("utf-8")
    return struct.pack(">I", len(head)) + head + body


def audio(data: bytes, engine: str = "xtts@mini", keepalive: bool = False) -> bytes:
    return frame({"kind": "audio", "media_type": "audio/wav", "engine": engine, "keepalive": keepalive}, data)


def summary(trace: list[dict[str, Any]] | None = None, switch: str | None = None) -> bytes:
    return frame({"kind": "summary"}, json.dumps({"trace": trace or [], "switch": switch}).encode())


def error_frame(code: str, detail: str) -> bytes:
    return frame({"kind": "error"}, json.dumps({"error": code, "detail": detail}).encode())


async def chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def frames_response(data: bytes, size: int = 7) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": tts_client.FRAMES_MEDIA_TYPE}, content=chunks(data, size))


def make_client(handler: Callable[[httpx.Request], httpx.Response], token: str | None = TOKEN) -> TTSClient:
    return TTSClient("http://tts.test", token, transport=httpx.MockTransport(handler))


async def collect(client: TTSClient, **kwargs: Any) -> tuple[list[tts_client.Segment], tts_client.SpeakSummary]:
    stream = await client.speak("Hallo", language="de", **kwargs)
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

    asyncio.run(collect(make_client(handler), voice="Alice", engines=["edge", "xtts"], voices={"edge": {"voice": "x"}}, run="r1", wait=False))
    assert seen["text"] == "Hallo"
    assert seen["language"] == "de"
    assert seen["voice"] == "Alice"
    assert seen["engines"] == ["edge", "xtts"]
    assert "engine" not in seen
    assert seen["voices"] == {"edge": {"voice": "x"}}
    assert seen["run"] == "r1"
    assert seen["wait"] is False
    assert seen["authorization"] is None  # speaking needs no token


def test_speak_without_engines_sends_an_empty_list() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return frames_response(summary())

    asyncio.run(collect(make_client(handler)))
    assert seen["engines"] == []  # empty = the service uses its whole list


def test_error_frame_after_first_frame_raises_while_iterating() -> None:
    data = audio(b"first") + error_frame("engine_in_use", "busy")

    received: list[bytes] = []

    async def run_collecting() -> None:
        stream = await make_client(lambda _: frames_response(data)).speak("Hallo", language="de")
        async for segment in stream:
            received.append(segment.audio)

    with pytest.raises(EngineInUse):
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
        stream = await make_client(lambda _: frames_response(audio(b"x") + summary())).speak("Hallo", language="de")
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
        stream = await make_client(handler).speak("Hallo", language="de")
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
    ("engine_in_use", 409, EngineInUse),
    ("engine_control_failed", 502, EngineControlFailed),
    ("config_invalid", 400, ConfigInvalid),
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
        make_client(handler).config()


# ---- Control commands -----------------------------------------------------------


def test_token_header_only_on_changing_commands() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/engines":
            return httpx.Response(200, json=[])
        if request.url.path == "/voices":
            return httpx.Response(200, json=[])
        if request.url.path == "/config" and request.method == "GET":
            return httpx.Response(200, json={})
        if request.url.path == "/config":
            return httpx.Response(200, json={"stored": True})
        if request.url.path == "/engines/reload":
            return httpx.Response(200, json={"removed": [], "added": []})
        if request.url.path == "/voices/sync":
            return httpx.Response(200, json=[])
        return httpx.Response(204)

    client = make_client(handler)
    client.engines()
    client.voices()
    client.config()
    client.set_config({"entries": []})
    client.start("xtts")
    client.stop("xtts")
    client.reload()
    client.sync_voices("dashscope")
    bearer = f"Bearer {TOKEN}"
    assert seen == [
        ("GET", "/engines", None),
        ("GET", "/voices", None),
        ("GET", "/config", None),
        ("POST", "/config", bearer),
        ("POST", "/engines/xtts/start", bearer),
        ("POST", "/engines/xtts/stop", bearer),
        ("POST", "/engines/reload", bearer),
        ("POST", "/voices/sync", bearer),
    ]


def test_changing_command_without_token_fails_before_the_request() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(Unauthorized):
        make_client(handler, token=None).start("xtts")


def test_start_and_stop_pass_host_and_map_errors() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/stop"):
            return httpx.Response(409, json={"error": "engine_in_use", "detail": "reading runs"})
        return httpx.Response(204)

    client = make_client(handler)
    client.start("xtts", host="aragon")
    with pytest.raises(EngineInUse, match="reading runs"):
        client.stop("xtts")
    assert seen == [("/engines/xtts/start", {"host": "aragon"}), ("/engines/xtts/stop", {})]


def test_set_config_sends_json_and_maps_invalid() -> None:
    sent: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "config_invalid", "detail": "no card fits"})

    with pytest.raises(ConfigInvalid, match="no card fits"):
        make_client(handler).set_config({"entries": [{"engine": "xtts"}]})
    assert sent == [{"entries": [{"engine": "xtts"}]}]


def test_engines_voices_config_are_decoded() -> None:
    engine = {
        "engine": "xtts", "host": None, "label": {"de": "XTTS", "en": "XTTS"},
        "label_short": {"de": "XTTS", "en": "XTTS"}, "enabled": True, "deactivated": False,
        "status": "running", "unit": "sentence", "unit_default": "paragraph", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": False, "start_hint": "image missing",
        "vram_mib": {"GPU-1": 4096},
    }
    config = {"voices_dir": "/v", "entries": [{"engine": "xtts", "host": None, "enabled": True}]}
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        if request.url.path == "/engines":
            return httpx.Response(200, json=[engine])
        if request.url.path == "/voices":
            return httpx.Response(200, json=[{"name": "Alice", "label": "Alice (de)"}])
        return httpx.Response(200, json=config)

    client = make_client(handler)
    engines = client.engines()
    assert engines[0].engine == "xtts"
    assert engines[0].status == "running"
    assert engines[0].installed is False
    assert engines[0].start_hint == "image missing"
    assert engines[0].vram_mib == {"GPU-1": 4096}
    voices = client.voices("xtts")
    assert (voices[0].name, voices[0].label) == ("Alice", "Alice (de)")
    assert client.config() == config
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


def test_systemctl_start_runs_the_user_unit_with_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(tts_client.subprocess, "run", fake_run)
    assert tts_client._systemctl_start() == (0, "")
    assert seen["command"] == ["systemctl", "--user", "start", tts_client.TTS_SUITE_SERVICE_UNIT]
    assert seen["environment"]["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


# ---- WebSocket stream (open_stream) against a local fake server on loopback ----------

SocketHandler = Callable[[ServerConnection], Any]


@contextlib.asynccontextmanager
async def fake_service(handler: SocketHandler, token: str | None = TOKEN) -> AsyncIterator[TTSClient]:
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield TTSClient(f"http://127.0.0.1:{port}", token)


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
            stream = await client.open_stream(language="de", voice="Alice", engines=["edge"], voices={"edge": {"voice": "x"}}, run="r1", wait=False)
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
    assert (start["language"], start["voice"], start["engines"], start["run"], start["wait"]) == ("de", "Alice", ["edge"], "r1", False)
    assert start["voices"] == {"edge": {"voice": "x"}}
    assert first == {"type": "text", "text": "Hallo "}
    assert second == {"type": "text", "text": "Welt."}
    assert finish == {"type": "finish"}


def test_stream_sends_token_as_bearer_header() -> None:
    seen: list[str | None] = []

    async def handler(socket: ServerConnection) -> None:
        seen.append(socket.request.headers.get("Authorization"))
        await socket.recv()
        await socket.send(summary())
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de")
            assert [s async for s in stream] == []

    asyncio.run(run())
    assert seen == [f"Bearer {TOKEN}"]


def test_error_frame_before_first_segment() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(error_frame("unsupported_language", "no voice for fr"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="fr")
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
            stream = await client.open_stream(language="fr")
            await asyncio.sleep(0.2)
            await stream.feed("Bonjour")

    with pytest.raises(UnsupportedLanguage):
        asyncio.run(run())


def test_error_frame_after_first_segment() -> None:
    received: list[bytes] = []

    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"first"))
        await socket.send(error_frame("engine_in_use", "busy"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de")
            async for segment in stream:
                received.append(segment.audio)

    with pytest.raises(EngineInUse):
        asyncio.run(run())
    assert received == [b"first"]


def test_stream_without_summary_is_broken() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(audio(b"x"))
        await socket.close()

    async def run() -> None:
        async with fake_service(handler) as client:
            stream = await client.open_stream(language="de")
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
            stream = await client.open_stream(language="de")
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
            stream = await client.open_stream(language="de")
            iterator = stream.__aiter__()
            assert (await anext(iterator)).audio == b"x"
            await stream.aclose()
            await asyncio.wait_for(closed.wait(), 5)

    asyncio.run(run())


def test_refused_connection_is_unreachable() -> None:
    async def run() -> None:
        async with fake_service(lambda socket: socket.close()) as client:
            pass
        await client.open_stream(language="de")  # the server is gone: nobody listens on the port

    with pytest.raises(TTSSuiteUnreachable):
        asyncio.run(run())
