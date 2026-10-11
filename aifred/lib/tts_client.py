"""Thin async HTTP client for the TTS-Suite service (own repo, default port 5090).

SSOT for every access to the speech service: URL, token, timeouts, the decoding of the
frame stream and the mapping of the service's error codes to exceptions. The client knows
only the HTTP interface of the service (``docs/INTERFACE.md`` in the TTS-Suite repo);
what AIfred builds on top of it (voices of agents, LLM tuning) lives with the callers.

A fresh ``httpx.AsyncClient`` is used per request (cheap on localhost), so the client
holds no connection that is bound to one event loop.
"""

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import (
    TTS_SUITE_CONNECT_TIMEOUT_S,
    TTS_SUITE_CONTROL_TIMEOUT_S,
    TTS_SUITE_STREAM_READ_TIMEOUT_S,
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


# The one table: error code of the service -> exception class.
ERROR_CLASSES: dict[str, type[TTSSuiteError]] = {
    cls.code: cls
    for cls in (
        NoEngineAvailable,
        EngineStarting,
        Overloaded,
        UnknownEngine,
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


@dataclass(frozen=True)
class EngineInfo:
    engine: str
    host: str | None
    label: Mapping[str, str]
    label_short: Mapping[str, str]
    enabled: bool
    deactivated: bool
    status: str  # running | stopped | starting | unreachable
    unit: str
    unit_default: str
    needs_gpu: bool
    cloud: bool
    browser: bool
    vram_mib: Mapping[str, int] | None


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


class SpeechStream:
    """The answer of ``speak``: iterate the segments, read ``summary`` afterwards.

    Keep-alive markers are passed on (``segment.keepalive``); the caller drops them.
    ``await aclose()`` aborts the reading and closes the HTTP answer.
    """

    def __init__(self, http: httpx.AsyncClient, response: httpx.Response) -> None:
        self._http = http
        self._response = response
        self._decoder = FrameDecoder()
        self._summary: SpeakSummary | None = None

    @property
    def summary(self) -> SpeakSummary:
        """Course of the escalation; available once the stream is exhausted."""
        if self._summary is None:
            raise RuntimeError("the summary is only available after the stream has ended")
        return self._summary

    def __aiter__(self) -> AsyncIterator[Segment]:
        return self._segments()

    async def _segments(self) -> AsyncIterator[Segment]:
        try:
            async for chunk in self._response.aiter_raw():
                for header, body in self._decoder.feed(chunk):
                    segment = self._handle_frame(header, body)
                    if segment is not None:
                        yield segment
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service connection lost during reading: {error!r}") from error
        finally:
            await self.aclose()
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

    async def aclose(self) -> None:
        await self._response.aclose()
        await self._http.aclose()


# ---- The client --------------------------------------------------------------


class TTSClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token or None
        self._transport = transport

    def _http(self, read_timeout: float) -> httpx.AsyncClient:
        timeout = httpx.Timeout(read_timeout, connect=TTS_SUITE_CONNECT_TIMEOUT_S)
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport)

    def _auth_headers(self, needs_token: bool) -> dict[str, str]:
        if not needs_token:
            return {}
        if self._token is None:
            raise Unauthorized(
                "no TTS_SUITE_TOKEN set and no token file found; "
                "this command needs the token of the TTS service",
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(
        self,
        method: str,
        path: str,
        *,
        needs_token: bool = False,
        params: Mapping[str, str] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Control request: JSON answer (``None`` for 204); errors as TTSSuiteError."""
        headers = self._auth_headers(needs_token)
        try:
            async with self._http(TTS_SUITE_CONTROL_TIMEOUT_S) as http:
                response = await http.request(method, path, params=params, json=json_body, headers=headers)
        except httpx.TransportError as error:
            raise TTSSuiteUnreachable(f"TTS service not reachable on {path}: {error!r}") from error
        if response.is_error:
            raise _error_from_response(response)
        return None if response.status_code == 204 else response.json()

    # ---- read ----

    async def engines(self) -> list[EngineInfo]:
        return [EngineInfo(**item) for item in await self._call("GET", "/engines")]

    async def voices(self, engine: str | None = None) -> list[VoiceInfo]:
        params = {"engine": engine} if engine else None
        return [VoiceInfo(**item) for item in await self._call("GET", "/voices", params=params)]

    async def config(self) -> dict[str, Any]:
        config: dict[str, Any] = await self._call("GET", "/config")
        return config

    # ---- change (token) ----

    async def set_config(self, config_json: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and store the configuration; returns the stored one."""
        stored: dict[str, Any] = await self._call("POST", "/config", needs_token=True, json_body=config_json)
        return stored

    async def start(self, engine: str, host: str | None = None) -> None:
        await self._call("POST", f"/engines/{engine}/start", needs_token=True, params=_host_params(host))

    async def stop(self, engine: str, host: str | None = None) -> None:
        await self._call("POST", f"/engines/{engine}/stop", needs_token=True, params=_host_params(host))

    async def reload(self) -> dict[str, list[str]]:
        """Read the engine folders again; returns ``{"removed": [...], "added": [...]}``."""
        result: dict[str, list[str]] = await self._call("POST", "/engines/reload", needs_token=True)
        return result

    async def sync_voices(self, engine: str) -> list[VoiceInfo]:
        items = await self._call("POST", "/voices/sync", needs_token=True, params={"engine": engine})
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
        engine: str | None = None,
        voices: Mapping[str, Mapping[str, Any]] | None = None,
        run: str | None = None,
        wait: bool = True,
    ) -> SpeechStream:
        """Start a reading of the whole text.

        Returns when the service has sent its first frame, so a reading that cannot start
        raises here (``NoEngineAvailable``, ``EngineStarting``, ...); a cold start of an
        engine is waited for. Failures after the first frame surface while iterating.
        """
        head: dict[str, Any] = {
            "text": text,
            "language": language,
            "voice": voice,
            "speed": speed,
            "pitch": pitch,
            "engine": engine,
            "voices": dict(voices) if voices else {},
            "run": run,
            "wait": wait,
        }
        http = self._http(TTS_SUITE_STREAM_READ_TIMEOUT_S)
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
