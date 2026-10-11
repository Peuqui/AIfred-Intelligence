"""TTSClient: Frames, Fehlerabbildung und Steuerbefehle gegen einen httpx.MockTransport (kein Netz)."""

import asyncio
import json
import struct
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

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
    TTSClient,
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

    asyncio.run(collect(make_client(handler), voice="Alice", engine="edge", voices={"edge": {"voice": "x"}}, run="r1", wait=False))
    assert seen["text"] == "Hallo"
    assert seen["language"] == "de"
    assert seen["voice"] == "Alice"
    assert seen["engine"] == "edge"
    assert seen["voices"] == {"edge": {"voice": "x"}}
    assert seen["run"] == "r1"
    assert seen["wait"] is False
    assert seen["authorization"] is None  # speaking needs no token


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
        asyncio.run(client.engines())


def test_timeout_is_unreachable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TTSSuiteUnreachable):
        asyncio.run(make_client(handler).config())


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

    async def run() -> None:
        client = make_client(handler)
        await client.engines()
        await client.voices()
        await client.config()
        await client.set_config({"entries": []})
        await client.start("xtts")
        await client.stop("xtts")
        await client.reload()
        await client.sync_voices("dashscope")

    asyncio.run(run())
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
        asyncio.run(make_client(handler, token=None).start("xtts"))


def test_start_and_stop_pass_host_and_map_errors() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/stop"):
            return httpx.Response(409, json={"error": "engine_in_use", "detail": "reading runs"})
        return httpx.Response(204)

    async def run() -> None:
        client = make_client(handler)
        await client.start("xtts", host="aragon")
        await client.stop("xtts")

    with pytest.raises(EngineInUse, match="reading runs"):
        asyncio.run(run())
    assert seen == [("/engines/xtts/start", {"host": "aragon"}), ("/engines/xtts/stop", {})]


def test_set_config_sends_json_and_maps_invalid() -> None:
    sent: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "config_invalid", "detail": "no card fits"})

    with pytest.raises(ConfigInvalid, match="no card fits"):
        asyncio.run(make_client(handler).set_config({"entries": [{"engine": "xtts"}]}))
    assert sent == [{"entries": [{"engine": "xtts"}]}]


def test_engines_voices_config_are_decoded() -> None:
    engine = {
        "engine": "xtts", "host": None, "label": {"de": "XTTS", "en": "XTTS"},
        "label_short": {"de": "XTTS", "en": "XTTS"}, "enabled": True, "deactivated": False,
        "status": "running", "unit": "sentence", "unit_default": "paragraph", "needs_gpu": True,
        "cloud": False, "browser": False, "vram_mib": {"GPU-1": 4096},
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

    async def run() -> None:
        client = make_client(handler)
        engines = await client.engines()
        assert engines[0].engine == "xtts"
        assert engines[0].status == "running"
        assert engines[0].vram_mib == {"GPU-1": 4096}
        voices = await client.voices("xtts")
        assert (voices[0].name, voices[0].label) == ("Alice", "Alice (de)")
        assert await client.config() == config

    asyncio.run(run())
    assert seen_params[1] == {"engine": "xtts"}
