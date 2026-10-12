"""Thin HTTP client for the TTS-Suite service (own repo, default port 5090).

SSOT for every access to the speech service: URL, timeouts, the decoding of the
frame stream, the mapping of the service's error codes to exceptions and the start of the
service on demand. The client knows only the HTTP interface of the service
(``docs/INTERFACE.md`` in the TTS-Suite repo); what AIfred builds on top of it (voices of
agents, LLM tuning) lives with the callers.

AIfred only reads, speaks and asks: the client has no call that writes to the service (no
configuration, no start or stop of an engine, no voice sync, no token). Hosts, defaults and
the life of the engine containers are set in the service's own web interface; the service
starts and stops the containers itself when a request reaches it. The one request that is
not a reading is ``release``: AIfred ASKS the service to take containers off the cards (to
make room for a language model); the service decides per target and never stops a container
a reading holds.

Read calls (``engines``, ``voices``), ``release`` and ``ensure_ready`` are
synchronous (short calls, usable from threads and plain code; async callers wrap them in
``asyncio.to_thread``). Only ``speak`` and ``open_stream`` are async. A fresh ``httpx``
client is used per request (cheap on localhost), so the client holds no connection that is
bound to one event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import struct
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Literal

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidHandshake, InvalidStatus

from .config import (
    TTS_SUITE_CONNECT_TIMEOUT_S,
    TTS_SUITE_CONTROL_TIMEOUT_S,
    TTS_SUITE_RELEASE_TIMEOUT_S,
    TTS_SUITE_SERVICE_UNIT,
    TTS_SUITE_START_POLL_S,
    TTS_SUITE_START_TIMEOUT_S,
    TTS_SUITE_STREAM_READ_TIMEOUT_S,
    TTS_SUITE_SYSTEMCTL_TIMEOUT_S,
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


class UnsupportedLanguage(TTSSuiteError):
    code = "unsupported_language"
    status = 422


class TextTooLong(TTSSuiteError):
    code = "text_too_long"
    status = 413


class Unauthorized(TTSSuiteError):
    """The service demands a token (it listens beyond localhost); AIfred sends none."""

    code = "unauthorized"
    status = 401


class BadRequest(TTSSuiteError):
    code = "bad_request"
    status = 400


class UnknownServiceError(TTSSuiteError):
    """An answer that is no error of the service's interface (unknown code, no JSON)."""


class StreamBroken(TTSSuiteError):
    """The frame stream ended without a ``summary`` frame, in the middle of a frame, or carried a
    frame this client cannot read."""


class IncompatibleService(TTSSuiteError):
    """The service answered in a form this client does not know (fields missing or new): service
    and client were not updated together. The client stays strict on purpose; the message names the fields."""


class TTSSuiteUnreachable(TTSSuiteError):
    """No connection to the service, or it did not answer within the timeout."""


class TTSSuiteStartFailed(TTSSuiteError):
    """The service was not running and could not be started (``systemctl`` failed, or ``/health`` stayed silent)."""


# The one table: error code of the service -> exception class. Only codes a reading or a
# read call can bring; the service's codes for writing calls (``engine_in_use``,
# ``engine_control_failed``, ``config_invalid``) never reach AIfred, which does not write.
ERROR_CLASSES: dict[str, type[TTSSuiteError]] = {
    cls.code: cls
    for cls in (
        NoEngineAvailable,
        EngineStarting,
        Overloaded,
        UnknownEngine,
        UnknownHost,
        InvalidRequest,
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
    default_vram_mib: int | None  # what the engine takes when the config sets no budget; None = no budget
    default_max_parallel: int
    default_max_queue: int


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
class EngineTarget:
    """One catalog row AIfred asks the service to release. ``host=None`` is the explicit value
    "this machine"."""

    engine: str
    host: str | None


@dataclass(frozen=True)
class ReleaseResult:
    """What the service did for one target of a release request. ``outcome``: ``stopped`` (container
    taken down, VRAM free), ``not_running`` (nothing to release), ``in_use`` (a reading holds it, left
    alone), ``unreachable`` (the host does not answer), ``failed`` (``detail`` says why)."""

    engine: str
    host: str | None
    outcome: str
    detail: str


@dataclass(frozen=True)
class Segment:
    """One piece of audio, or a marker with empty ``audio``: a keep-alive marker
    (``keepalive``) or a pause marker (``pause_ms`` > 0: the caller makes that much silence)."""

    audio: bytes
    media_type: str
    engine: str
    keepalive: bool
    pause_ms: int


@dataclass(frozen=True)
class SpeakSummary:
    trace: tuple[TraceStep, ...]
    switch: str | None


def _decode(cls: type[Any], item: Mapping[str, Any], where: str) -> Any:
    """``cls(**item)`` for an answer of the service whose fields must be exactly those of ``cls``."""
    expected = {field.name for field in fields(cls)}
    if set(item) != expected:
        raise IncompatibleService(
            f"TTS service {where} differs from this client: missing {sorted(expected - set(item))}, "
            f"unknown {sorted(set(item) - expected)}"
        )
    return cls(**item)


# ---- Frame decoding ----------------------------------------------------------


class FrameDecoder:
    """Cuts frames out of a byte stream: ``uint32`` header length (big endian),
    UTF-8 JSON header (with ``bytes`` = body length), body. Chunks may split frames anywhere."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[dict[str, Any], bytes]]:
        return list(self.iter_frames(chunk))

    def iter_frames(self, chunk: bytes) -> Iterator[tuple[dict[str, Any], bytes]]:
        """The frames the chunk completes, one by one: a malformed later frame does not take away
        the ones before it."""
        self._buffer += chunk
        while (frame := self._take_frame()) is not None:
            yield frame

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

    def _handle_chunk(self, chunk: bytes) -> Iterator[Segment]:
        """The segments of the frames the chunk completes, in order. An error frame (or a frame this
        client cannot read) raises only after the segments before it were handed out: audio that
        arrived in the same read is not lost."""
        try:
            for header, body in self._decoder.iter_frames(chunk):
                segment = self._handle_frame(header, body)
                if segment is not None:
                    yield segment
        except (KeyError, ValueError, TypeError) as error:
            raise StreamBroken(f"TTS service sent a frame this client cannot read: {error!r}") from error

    def _check_ended_cleanly(self) -> None:
        if self._decoder.pending or self._summary is None:
            raise StreamBroken("TTS stream ended without a summary frame")

    def _handle_frame(self, header: dict[str, Any], body: bytes) -> Segment | None:
        kind = header["kind"]
        if kind == "audio":
            return Segment(body, header["media_type"], header["engine"], header["keepalive"], header["pause_ms"])
        if kind == "summary":
            data = json.loads(body)
            self._summary = SpeakSummary(_trace_from_json(data["trace"]), data["switch"])
            return None
        if kind == "error":
            raise _error_from_body(json.loads(body), None, body.decode("utf-8", "replace"))
        raise UnknownServiceError(f"unknown frame kind '{kind}'")


class SpeechStream(_FrameStream):
    """The answer of ``speak``: iterate the segments, read ``summary`` afterwards.

    Keep-alive markers (``segment.keepalive``) and pause markers (``segment.pause_ms`` > 0) are
    passed on; the caller decides what they mean for its output.
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
            while True:
                try:
                    message = await asyncio.wait_for(self._connection.recv(), TTS_SUITE_STREAM_READ_TIMEOUT_S)
                except ConnectionClosedOK:
                    break
                except TimeoutError as error:
                    # The service sends a keep-alive marker while a piece takes long: silence means it is gone.
                    raise StreamBroken(
                        f"TTS service sent nothing for {TTS_SUITE_STREAM_READ_TIMEOUT_S} s on the open reading",
                    ) from error
                if isinstance(message, str):
                    raise UnknownServiceError("TTS service sent a text message instead of a frame")
                for segment in self._handle_chunk(message):
                    self._queue.put_nowait(segment)
            self._check_ended_cleanly()
        except ConnectionClosed as error:
            self._fail(TTSSuiteUnreachable(f"TTS service connection lost during reading: {error!r}"))
        except TTSSuiteError as error:
            self._fail(error)
        except Exception as error:  # noqa: BLE001 — a task nobody awaits must not die silently: the reader would wait for ever
            self._fail(StreamBroken(f"TTS stream receiver failed: {type(error).__name__}: {error}"))
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
    """Start the system unit of the service (``tts-suite@<user>.service``); returns ``(exit code, stderr)``.

    A system unit like AIfred's own and Agent-Orc's: Polkit lets this user start it without sudo.
    Tests replace this function: a real ``systemctl`` must never run there.
    """
    unit = TTS_SUITE_SERVICE_UNIT.format(user=getpass.getuser())
    try:
        result = subprocess.run(
            ["systemctl", "start", unit],
            capture_output=True,
            text=True,
            timeout=TTS_SUITE_SYSTEMCTL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise TTSSuiteStartFailed(
            f"'systemctl start {unit}' did not return within {TTS_SUITE_SYSTEMCTL_TIMEOUT_S}s",
        ) from error
    return result.returncode, result.stderr.strip()


def new_run_id() -> str:
    """The id of one reading: the service remembers per id which engines failed, whether a code block is open
    and whether a change of voice was announced. One id per answer (all its pieces and its one list)."""
    return uuid.uuid4().hex


class TTSClient:
    def __init__(
        self,
        base_url: str,
        # Test seam: httpx.MockTransport serves both the sync and the async client.
        transport: httpx.MockTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def _timeout(self, read_timeout: float) -> httpx.Timeout:
        return httpx.Timeout(read_timeout, connect=TTS_SUITE_CONNECT_TIMEOUT_S)

    def _sync_http(self, read_timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self._timeout(read_timeout), transport=self._transport)

    def _async_http(self, read_timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout(read_timeout), transport=self._transport)

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        """Read request: JSON answer; errors as TTSSuiteError."""
        try:
            with self._sync_http(TTS_SUITE_CONTROL_TIMEOUT_S) as http:
                response = http.get(path, params=params)
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service not reachable on {path}: {error!r}") from error
        if response.is_error:
            raise _error_from_response(response)
        return response.json()

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
                f"'systemctl start {TTS_SUITE_SERVICE_UNIT.format(user=getpass.getuser())}' failed (exit {exit_code}): {stderr}",
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
        return [_decode(EngineInfo, item, "/engines") for item in self._get("/engines")]

    def voices(self, engine: str | None = None) -> list[VoiceInfo]:
        params = {"engine": engine} if engine else None
        return [_decode(VoiceInfo, item, "/voices") for item in self._get("/voices", params)]

    def voices_dir(self) -> Path:
        """The shared voices folder the service reads (its ``/config``): the one place of the voice files.

        Raises:
            IncompatibleService: ``/config`` carries no ``voices_dir``.
        """
        config = self._get("/config")
        if not isinstance(config, dict) or not isinstance(config.get("voices_dir"), str):
            raise IncompatibleService("the service's /config has no voices_dir")
        return Path(config["voices_dir"])

    # ---- release ----

    def release(self, targets: Sequence[EngineTarget] | Literal["all"]) -> list[ReleaseResult]:
        """Ask the service to take containers off the cards: ``"all"`` or the given catalog rows.

        The service decides per target (see ``ReleaseResult``); a refusal of one target
        (``in_use``, ``failed``, ...) is a result, not an error. Results come in the order of the
        targets. Blocking (the service stops containers): call in a thread from async code.

        Raises:
            InvalidRequest, UnknownEngine, UnknownHost: the whole request was refused (checked
                before the first stop).
            TTSSuiteUnreachable: the service does not answer.
        """
        wanted = targets if isinstance(targets, str) else [asdict(target) for target in targets]
        try:
            with self._sync_http(TTS_SUITE_RELEASE_TIMEOUT_S) as http:
                response = http.post("/release", json={"targets": wanted})
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service not reachable on /release: {error!r}") from error
        if response.is_error:
            raise _error_from_response(response)
        return [_decode(ReleaseResult, item, "/release") for item in response.json()]

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
        paragraph_pause_ms: int,
        voices: Mapping[str, Mapping[str, Any]] | None = None,
        run: str,
        wait: bool = True,
    ) -> SpeechStream:
        """Start a reading of the whole text.

        ``engines`` is the list of this reading in escalation order (required; the service
        refuses an empty or wrong one with ``InvalidRequest``, ``UnknownEngine`` or
        ``UnknownHost``). Returns when the service has sent its first frame, so a
        reading that cannot start raises here (``NoEngineAvailable``, ``EngineStarting``, ...);
        a cold start of an engine is waited for. Failures after the first frame surface
        while iterating.

        ``paragraph_pause_ms`` (required; 0 = no pause) is the silence the caller wants between
        two paragraphs: the service does not make it, it delivers a pause marker
        (``Segment.pause_ms``) at each paragraph border.
        """
        head = _speak_head(language, voice, speed, pitch, engines, paragraph_pause_ms, voices, run, wait)
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
        paragraph_pause_ms: int,
        voices: Mapping[str, Mapping[str, Any]] | None = None,
        run: str,
        wait: bool = True,
    ) -> TextStream:
        """Open a reading whose text arrives in pieces (WebSocket ``/speak/stream``).

        ``engines`` as in ``speak``. Returns once the connection stands and the head is sent; the service reports a reading
        that cannot start (``UnsupportedLanguage``, ``NoEngineAvailable``, ...) as an error frame,
        which is raised while iterating or by the next ``feed``.
        """
        head = {"type": "start", **_speak_head(language, voice, speed, pitch, engines, paragraph_pause_ms, voices, run, wait)}
        try:
            connection = await connect(
                _stream_url(self.base_url),
                open_timeout=TTS_SUITE_CONNECT_TIMEOUT_S,
                max_size=None,  # a frame carries a whole audio segment, which can be large
            )
        except InvalidStatus as error:
            if error.response.status_code == 403:
                raise Unauthorized("TTS service refused the stream: it demands a token", 403) from error
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
    paragraph_pause_ms: int,
    voices: Mapping[str, Mapping[str, Any]] | None,
    run: str,
    wait: bool,
) -> dict[str, Any]:
    """Head of a reading, shared by POST /speak and the start message of the WebSocket."""
    return {
        "language": language,
        "voice": voice,
        "speed": speed,
        "pitch": pitch,
        "engines": [asdict(ref) for ref in engines],
        "paragraph_pause_ms": paragraph_pause_ms,
        "voices": dict(voices) if voices else {},
        "run": run,
        "wait": wait,
    }


def _stream_url(base_url: str) -> str:
    """http -> ws, https -> wss."""
    return base_url.replace("http", "ws", 1) + "/speak/stream"


# ---- Factory -----------------------------------------------------------------


_default_client: TTSClient | None = None


def get_tts_client() -> TTSClient:
    """The one client of the process (URL from config.py / .env)."""
    global _default_client
    if _default_client is None:
        _default_client = TTSClient(TTS_SUITE_URL)
    return _default_client
