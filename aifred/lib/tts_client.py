"""Thin HTTP client for the TTS-Suite service (own repo, default port 5090).

SSOT for every access to the speech service: URL, token, timeouts, the decoding of the
frame stream, the mapping of the service's error codes to exceptions and the start of the
service on demand. The client knows only the HTTP interface of the service
(``docs/INTERFACE.md`` in the TTS-Suite repo); what AIfred builds on top of it (voices of
agents, LLM tuning) lives with the callers.

Management calls (``engines``, ``config``, ``start``, ...) and ``ensure_ready`` are
synchronous (short calls, usable from threads and plain code; async callers wrap them in
``asyncio.to_thread``). Only ``speak`` and ``open_stream`` are async. A fresh ``httpx``
client is used per request (cheap on localhost), so the client holds no connection that is
bound to one event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import struct
import subprocess
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from .config import (
    TTS_SUITE_CONNECT_TIMEOUT_S,
    TTS_SUITE_CONTROL_TIMEOUT_S,
    TTS_SUITE_ENGINE_START_TIMEOUT_S,
    TTS_SUITE_SERVICE_UNIT,
    TTS_SUITE_START_POLL_S,
    TTS_SUITE_START_TIMEOUT_S,
    TTS_SUITE_STREAM_READ_TIMEOUT_S,
    TTS_SUITE_SYSTEMCTL_TIMEOUT_S,
    TTS_SUITE_TOKEN,
    TTS_SUITE_TOKEN_FILE,
    TTS_SUITE_URL,
)

FRAMES_MEDIA_TYPE = "application/x-tts-frames"
_HEADER_LENGTH = struct.Struct(">I")


# ---- Errors -----------------------------------------------------------------


class TTSSuiteError(Exception):
    """The service did not do what was asked; ``str(error)`` is a displayable reason."""

    code = ""
    status = 0

    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        if status is not None:
            self.status = status


@dataclass(frozen=True)
class TraceStep:
    """One step of an escalation: ``entry`` is ``engine@host`` (or ``engine``)."""

    entry: str
    outcome: str  # skipped | failed | spoke
    reason: str | None = None
    detail: str = ""


class NoEngineAvailable(TTSSuiteError):
    """No entry of the escalation list can speak; ``trace`` says why for each."""

    code = "no_engine"
    status = 503

    def __init__(self, detail: str, status: int | None = None, trace: tuple[TraceStep, ...] = ()) -> None:
        super().__init__(detail, status)
        self.trace = trace


class EngineStarting(TTSSuiteError):
    code = "starting"
    status = 503


class Overloaded(TTSSuiteError):
    code = "overloaded"
    status = 429


class UnknownEngine(TTSSuiteError):
    code = "unknown_engine"
    status = 404


class UnknownHost(TTSSuiteError):
    code = "unknown_host"
    status = 404


class InvalidRequest(TTSSuiteError):
    """The request's list is wrong: empty, a double entry, an engine without REST API on that host, an invalid unit."""

    code = "invalid_request"
    status = 422


class EngineInUse(TTSSuiteError):
    code = "engine_in_use"
    status = 409


class EngineControlFailed(TTSSuiteError):
    code = "engine_control_failed"
    status = 502


class ConfigInvalid(TTSSuiteError):
    code = "config_invalid"
    status = 400


class UnsupportedLanguage(TTSSuiteError):
    code = "unsupported_language"
    status = 422


class TextTooLong(TTSSuiteError):
    code = "text_too_long"
    status = 413


class Unauthorized(TTSSuiteError):
    code = "unauthorized"
    status = 401


class BadRequest(TTSSuiteError):
    code = "bad_request"
    status = 400


class UnknownServiceError(TTSSuiteError):
    """An answer that is no error of the service's interface (unknown code, no JSON)."""


class StreamBroken(TTSSuiteError):
    """The frame stream ended without a ``summary`` frame or in the middle of a frame."""


class TTSSuiteUnreachable(TTSSuiteError):
    """No connection to the service, or it did not answer within the timeout."""


class TTSSuiteStartFailed(TTSSuiteError):
    """The service was not running and could not be started (``systemctl`` failed, or ``/health`` stayed silent)."""


# The one table: error code of the service -> exception class.
ERROR_CLASSES: dict[str, type[TTSSuiteError]] = {
    cls.code: cls
    for cls in (
        NoEngineAvailable,
        EngineStarting,
        Overloaded,
        UnknownEngine,
        UnknownHost,
        InvalidRequest,
        EngineInUse,
        EngineControlFailed,
        ConfigInvalid,
        UnsupportedLanguage,
        TextTooLong,
        Unauthorized,
        BadRequest,
    )
}


def _trace_from_json(items: list[dict[str, Any]]) -> tuple[TraceStep, ...]:
    return tuple(
        TraceStep(item["entry"], item["outcome"], item.get("reason"), item.get("detail", ""))
        for item in items
    )


def _error_from_body(body: Any, status: int | None, raw_text: str) -> TTSSuiteError:
    """Build the exception for an error body ``{"error", "detail", "trace"?}``."""
    if not isinstance(body, dict) or "error" not in body:
        return UnknownServiceError(f"unexpected answer from TTS service: {raw_text[:200]}", status)
    code = body["error"]
    detail = str(body.get("detail", code))
    error_class = ERROR_CLASSES.get(code)
    if error_class is None:
        return UnknownServiceError(f"unknown error code '{code}': {detail}", status)
    if error_class is NoEngineAvailable:
        return NoEngineAvailable(detail, status, _trace_from_json(body.get("trace", [])))
    return error_class(detail, status)


def _error_from_response(response: httpx.Response) -> TTSSuiteError:
    text = response.text
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    return _error_from_body(body, response.status_code, text)


# ---- Types of the interface --------------------------------------------------


SPEECH_UNITS = ("sentence", "paragraph", "whole")


@dataclass(frozen=True)
class EngineInfo:
    """One row of the service's catalog (``GET /engines``): an engine on this machine
    (``host`` None) or, for an engine with a REST API, on one of the configured hosts. The
    catalog is no list the service speaks by: each request brings its own (``EngineRef``)."""

    engine: str
    host: str | None
    label: Mapping[str, str]
    label_short: Mapping[str, str]
    status: str  # running | stopped | starting | unreachable
    unit_default: str  # what the engine recommends as the unit of an entry
    needs_gpu: bool
    cloud: bool
    browser: bool
    installed: bool  # can be started: no container needed, or the Docker image is built
    start_hint: str | None  # why the last start of this entry failed; else None
    vram_mib: Mapping[str, int] | None  # {gpu_uuid: MiB}; {} = none; None = measurement failed


@dataclass(frozen=True)
class EngineRef:
    """One entry of the list a request brings. Nothing is optional: ``host=None`` is the
    explicit value "this machine"."""

    engine: str
    unit: str  # one of SPEECH_UNITS: how much text the entry takes at once
    host: str | None  # name of a host of the service's config; None = this machine


@dataclass(frozen=True)
class VoiceInfo:
    name: str
    label: str


@dataclass(frozen=True)
class Segment:
    """One piece of audio; a keep-alive marker has empty ``audio``."""

    audio: bytes
    media_type: str
    engine: str
    keepalive: bool


@dataclass(frozen=True)
class SpeakSummary:
    trace: tuple[TraceStep, ...]
    switch: str | None


# ---- Frame decoding ----------------------------------------------------------


class FrameDecoder:
    """Cuts frames out of a byte stream: ``uint32`` header length (big endian),
    UTF-8 JSON header (with ``bytes`` = body length), body. Chunks may split frames anywhere."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[dict[str, Any], bytes]]:
        self._buffer += chunk
        frames: list[tuple[dict[str, Any], bytes]] = []
        while True:
            frame = self._take_frame()
            if frame is None:
                return frames
            frames.append(frame)

    @property
    def pending(self) -> int:
        """Bytes of an incomplete frame (non-zero at the end of a stream means it broke off)."""
        return len(self._buffer)

    def _take_frame(self) -> tuple[dict[str, Any], bytes] | None:
        prefix = _HEADER_LENGTH.size
        if len(self._buffer) < prefix:
            return None
        (head_length,) = _HEADER_LENGTH.unpack_from(self._buffer)
        if len(self._buffer) < prefix + head_length:
            return None
        header = json.loads(bytes(self._buffer[prefix : prefix + head_length]))
        total = prefix + head_length + header["bytes"]
        if len(self._buffer) < total:
            return None
        body = bytes(self._buffer[prefix + head_length : total])
        del self._buffer[:total]
        return header, body


# ---- The stream of a reading -------------------------------------------------


class _FrameStream:
    """What a ``speak`` answer and a text stream share: decoding frames into segments and the summary."""

    def __init__(self) -> None:
        self._decoder = FrameDecoder()
        self._summary: SpeakSummary | None = None

    @property
    def summary(self) -> SpeakSummary:
        """Course of the escalation; available once the stream is exhausted."""
        if self._summary is None:
            raise RuntimeError("the summary is only available after the stream has ended")
        return self._summary

    def _handle_chunk(self, chunk: bytes) -> list[Segment]:
        segments = [self._handle_frame(header, body) for header, body in self._decoder.feed(chunk)]
        return [segment for segment in segments if segment is not None]

    def _check_ended_cleanly(self) -> None:
        if self._decoder.pending or self._summary is None:
            raise StreamBroken("TTS stream ended without a summary frame")

    def _handle_frame(self, header: dict[str, Any], body: bytes) -> Segment | None:
        kind = header["kind"]
        if kind == "audio":
            return Segment(body, header["media_type"], header["engine"], header["keepalive"])
        if kind == "summary":
            data = json.loads(body)
            self._summary = SpeakSummary(_trace_from_json(data["trace"]), data["switch"])
            return None
        if kind == "error":
            raise _error_from_body(json.loads(body), None, body.decode("utf-8", "replace"))
        raise UnknownServiceError(f"unknown frame kind '{kind}'")


class SpeechStream(_FrameStream):
    """The answer of ``speak``: iterate the segments, read ``summary`` afterwards.

    Keep-alive markers are passed on (``segment.keepalive``); the caller drops them.
    ``await aclose()`` aborts the reading and closes the HTTP answer.
    """

    def __init__(self, http: httpx.AsyncClient, response: httpx.Response) -> None:
        super().__init__()
        self._http = http
        self._response = response

    def __aiter__(self) -> AsyncIterator[Segment]:
        return self._segments()

    async def _segments(self) -> AsyncIterator[Segment]:
        try:
            async for chunk in self._response.aiter_raw():
                for segment in self._handle_chunk(chunk):
                    yield segment
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service connection lost during reading: {error!r}") from error
        finally:
            await self.aclose()
        self._check_ended_cleanly()

    async def aclose(self) -> None:
        await self._response.aclose()
        await self._http.aclose()


class TextStream(_FrameStream):
    """A reading over the WebSocket: text goes in pieces (``feed``, ``finish``), segments come back
    while the text is still being written. Sending and receiving are independent: one task reads
    the frames into a queue, the caller sends. A failure of the reading (error frame, lost
    connection) is raised while iterating, or by the next ``feed`` / ``finish``.
    ``await aclose()`` closes the connection, which aborts the reading.
    """

    def __init__(self, connection: ClientConnection) -> None:
        super().__init__()
        self._connection = connection
        # Segment = audio, exception = the reading failed, None = the stream ended
        self._queue: asyncio.Queue[Segment | TTSSuiteError | None] = asyncio.Queue()
        self._failure: TTSSuiteError | None = None
        self._receiver = asyncio.create_task(self._receive())

    async def feed(self, text: str) -> None:
        await self._send({"type": "text", "text": text})

    async def finish(self) -> None:
        """The text is complete; the segments go on until the summary."""
        await self._send({"type": "finish"})

    async def _send(self, message: dict[str, str]) -> None:
        try:
            await self._connection.send(json.dumps(message))
        except ConnectionClosed as error:
            # The service closes after an error frame: that frame is the real reason.
            if self._failure is not None:
                raise self._failure from error
            raise TTSSuiteUnreachable(f"TTS service closed the connection while sending: {error!r}") from error

    async def _receive(self) -> None:
        try:
            async for message in self._connection:
                if isinstance(message, str):
                    raise UnknownServiceError("TTS service sent a text message instead of a frame")
                for segment in self._handle_chunk(message):
                    self._queue.put_nowait(segment)
            self._check_ended_cleanly()
        except ConnectionClosed as error:
            self._fail(TTSSuiteUnreachable(f"TTS service connection lost during reading: {error!r}"))
        except TTSSuiteError as error:
            self._fail(error)
        else:
            self._queue.put_nowait(None)

    def _fail(self, error: TTSSuiteError) -> None:
        self._failure = error
        self._queue.put_nowait(error)

    def __aiter__(self) -> AsyncIterator[Segment]:
        return self._segments()

    async def _segments(self) -> AsyncIterator[Segment]:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                if isinstance(item, TTSSuiteError):
                    raise item
                yield item
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        self._receiver.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._receiver
        await self._connection.close()


# ---- The client --------------------------------------------------------------


def _systemctl_start() -> tuple[int, str]:
    """Start the user unit of the service; returns ``(exit code, stderr)``.

    ``XDG_RUNTIME_DIR`` is set because a process outside a login session (the AIfred
    service) does not have it, and ``systemctl --user`` cannot find the user bus without it.
    Tests replace this function: a real ``systemctl`` must never run there.
    """
    environment = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", TTS_SUITE_SERVICE_UNIT],
            env=environment,
            capture_output=True,
            text=True,
            timeout=TTS_SUITE_SYSTEMCTL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise TTSSuiteStartFailed(
            f"'systemctl --user start {TTS_SUITE_SERVICE_UNIT}' did not return within {TTS_SUITE_SYSTEMCTL_TIMEOUT_S}s",
        ) from error
    return result.returncode, result.stderr.strip()


class TTSClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        # Test seam: httpx.MockTransport serves both the sync and the async client.
        transport: httpx.MockTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token or None
        self._transport = transport

    def _timeout(self, read_timeout: float) -> httpx.Timeout:
        return httpx.Timeout(read_timeout, connect=TTS_SUITE_CONNECT_TIMEOUT_S)

    def _sync_http(self, read_timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self._timeout(read_timeout), transport=self._transport)

    def _async_http(self, read_timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout(read_timeout), transport=self._transport)

    def _auth_headers(self, needs_token: bool) -> dict[str, str]:
        if not needs_token:
            return {}
        if self._token is None:
            raise Unauthorized(
                "no TTS_SUITE_TOKEN set and no token file found; "
                "this command needs the token of the TTS service",
            )
        return {"Authorization": f"Bearer {self._token}"}

    def _call(
        self,
        method: str,
        path: str,
        *,
        needs_token: bool = False,
        params: Mapping[str, str] | None = None,
        json_body: Any = None,
        read_timeout: float = TTS_SUITE_CONTROL_TIMEOUT_S,
    ) -> Any:
        """Control request: JSON answer (``None`` for 204); errors as TTSSuiteError."""
        headers = self._auth_headers(needs_token)
        try:
            with self._sync_http(read_timeout) as http:
                response = http.request(method, path, params=params, json=json_body, headers=headers)
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service not reachable on {path}: {error!r}") from error
        if response.is_error:
            raise _error_from_response(response)
        return None if response.status_code == 204 else response.json()

    # ---- service on demand ----

    def _is_up(self) -> bool:
        """Does ``/health`` answer? A question, so a dead service is ``False``, not an error."""
        try:
            with self._sync_http(TTS_SUITE_CONNECT_TIMEOUT_S) as http:
                return http.get("/health").is_success
        except httpx.TransportError:
            return False

    def ensure_ready(self, timeout_s: float = TTS_SUITE_START_TIMEOUT_S) -> None:
        """Start the service (user unit) if ``/health`` does not answer, and wait for it.

        The service starts its engine containers itself.

        Raises:
            TTSSuiteStartFailed: ``systemctl`` failed, or ``/health`` stayed silent for ``timeout_s``.
        """
        if self._is_up():
            return
        exit_code, stderr = _systemctl_start()
        if exit_code != 0:
            raise TTSSuiteStartFailed(
                f"'systemctl --user start {TTS_SUITE_SERVICE_UNIT}' failed (exit {exit_code}): {stderr}",
            )
        deadline = monotonic() + timeout_s
        while not self._is_up():
            if monotonic() >= deadline:
                raise TTSSuiteStartFailed(
                    f"TTS service did not answer on /health within {timeout_s}s after start",
                )
            sleep(TTS_SUITE_START_POLL_S)

    # ---- read ----

    def engines(self) -> list[EngineInfo]:
        return [EngineInfo(**item) for item in self._call("GET", "/engines")]

    def voices(self, engine: str | None = None) -> list[VoiceInfo]:
        params = {"engine": engine} if engine else None
        return [VoiceInfo(**item) for item in self._call("GET", "/voices", params=params)]

    def config(self) -> dict[str, Any]:
        config: dict[str, Any] = self._call("GET", "/config")
        return config

    # ---- change (token) ----

    def set_config(self, config_json: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and store the configuration; returns the stored one."""
        stored: dict[str, Any] = self._call("POST", "/config", needs_token=True, json_body=config_json)
        return stored

    def start(self, engine: str, host: str | None = None) -> None:
        """Start the entry's container; the service answers when it serves (a cold start takes minutes)."""
        self._call(
            "POST", f"/engines/{engine}/start", needs_token=True, params=_host_params(host),
            read_timeout=TTS_SUITE_ENGINE_START_TIMEOUT_S,
        )

    def stop(self, engine: str, host: str | None = None) -> None:
        self._call("POST", f"/engines/{engine}/stop", needs_token=True, params=_host_params(host))

    def reload(self) -> dict[str, list[str]]:
        """Read the engine folders again; returns ``{"removed": [...], "added": [...]}``."""
        result: dict[str, list[str]] = self._call("POST", "/engines/reload", needs_token=True)
        return result

    def sync_voices(self, engine: str) -> list[VoiceInfo]:
        items = self._call("POST", "/voices/sync", needs_token=True, params={"engine": engine})
        return [VoiceInfo(**item) for item in items]

    # ---- speak ----

    async def speak(
        self,
        text: str,
        *,
        language: str,
        voice: str | None = None,
        speed: float | None = None,
        pitch: float | None = None,
        engines: Sequence[EngineRef],
        voices: Mapping[str, Mapping[str, Any]] | None = None,
        run: str | None = None,
        wait: bool = True,
    ) -> SpeechStream:
        """Start a reading of the whole text.

        ``engines`` is the list of this reading in escalation order (required; the service
        refuses an empty or wrong one with ``InvalidRequest``, ``UnknownEngine`` or
        ``UnknownHost``). Returns when the service has sent its first frame, so a
        reading that cannot start raises here (``NoEngineAvailable``, ``EngineStarting``, ...);
        a cold start of an engine is waited for. Failures after the first frame surface
        while iterating.
        """
        head = _speak_head(language, voice, speed, pitch, engines, voices, run, wait)
        head["text"] = text
        http = self._async_http(TTS_SUITE_STREAM_READ_TIMEOUT_S)
        try:
            request = http.build_request("POST", "/speak", json=head)
            response = await http.send(request, stream=True)
        except httpx.TransportError as error:
            await http.aclose()
            raise TTSSuiteUnreachable(f"TTS service not reachable on /speak: {error!r}") from error
        if response.is_error:
            await response.aread()
            error_to_raise = _error_from_response(response)
            await response.aclose()
            await http.aclose()
            raise error_to_raise
        return SpeechStream(http, response)

    async def open_stream(
        self,
        *,
        language: str,
        voice: str | None = None,
        speed: float | None = None,
        pitch: float | None = None,
        engines: Sequence[EngineRef],
        voices: Mapping[str, Mapping[str, Any]] | None = None,
        run: str | None = None,
        wait: bool = True,
    ) -> TextStream:
        """Open a reading whose text arrives in pieces (WebSocket ``/speak/stream``).

        ``engines`` as in ``speak``. Returns once the connection stands and the head is sent; the service reports a reading
        that cannot start (``UnsupportedLanguage``, ``NoEngineAvailable``, ...) as an error frame,
        which is raised while iterating or by the next ``feed``.
        """
        head = {"type": "start", **_speak_head(language, voice, speed, pitch, engines, voices, run, wait)}
        # Speaking needs the token only when the service listens beyond loopback; send it if there is one.
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            connection = await connect(
                _stream_url(self.base_url),
                additional_headers=headers,
                open_timeout=TTS_SUITE_CONNECT_TIMEOUT_S,
                max_size=None,  # a frame carries a whole audio segment, which can be large
            )
        except InvalidStatus as error:
            if error.response.status_code == 403:
                raise Unauthorized("TTS service refused the stream: a valid token is needed", 403) from error
            raise TTSSuiteUnreachable(f"TTS service refused the stream: {error!r}") from error
        except (OSError, TimeoutError, InvalidHandshake) as error:
            raise TTSSuiteUnreachable(f"TTS service not reachable on /speak/stream: {error!r}") from error
        stream = TextStream(connection)
        try:
            await connection.send(json.dumps(head))
        except ConnectionClosed as error:
            await stream.aclose()
            raise TTSSuiteUnreachable(f"TTS service closed the stream at start: {error!r}") from error
        return stream


def _speak_head(
    language: str,
    voice: str | None,
    speed: float | None,
    pitch: float | None,
    engines: Sequence[EngineRef],
    voices: Mapping[str, Mapping[str, Any]] | None,
    run: str | None,
    wait: bool,
) -> dict[str, Any]:
    """Head of a reading, shared by POST /speak and the start message of the WebSocket."""
    return {
        "language": language,
        "voice": voice,
        "speed": speed,
        "pitch": pitch,
        "engines": [asdict(ref) for ref in engines],
        "voices": dict(voices) if voices else {},
        "run": run,
        "wait": wait,
    }


def _stream_url(base_url: str) -> str:
    """http -> ws, https -> wss."""
    return base_url.replace("http", "ws", 1) + "/speak/stream"


def _host_params(host: str | None) -> dict[str, str] | None:
    return {"host": host} if host else None


# ---- Factory -----------------------------------------------------------------


def _read_token() -> str | None:
    """TTS_SUITE_TOKEN, else the first line of TTS_SUITE_TOKEN_FILE; ``None`` if neither exists."""
    if TTS_SUITE_TOKEN:
        return TTS_SUITE_TOKEN
    token_file = Path(TTS_SUITE_TOKEN_FILE)
    if not token_file.is_file():
        return None
    lines = token_file.read_text(encoding="utf-8").splitlines()
    return lines[0].strip() if lines and lines[0].strip() else None


_default_client: TTSClient | None = None


def get_tts_client() -> TTSClient:
    """The one client of the process (URL and token from config.py / .env)."""
    global _default_client
    if _default_client is None:
        _default_client = TTSClient(TTS_SUITE_URL, _read_token())
    return _default_client
