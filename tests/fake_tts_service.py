"""A fake TTS-Suite service for the speaking paths (Echo, announce, narrator): an
httpx.MockTransport that answers /health, /engines, /voices and /speak, like the real
service would. The real one is never touched (see conftest ``_no_real_tts_service``)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

import aifred.lib.audio_processing as audio_processing
import aifred.lib.settings as settings_module
import aifred.lib.tts_client as tts_client
import aifred.lib.tts_service as tts_service
from aifred.lib.tts_client import TTSClient


def frame(header: dict[str, Any], body: bytes = b"") -> bytes:
    head = json.dumps({**header, "bytes": len(body)}).encode("utf-8")
    return struct.pack(">I", len(head)) + head + body


def audio_frame(data: bytes, engine: str = "xtts", media_type: str = "audio/wav") -> bytes:
    header = {"kind": "audio", "media_type": media_type, "engine": engine, "keepalive": False, "pause_ms": 0}
    return frame(header, data)


def keepalive_frame(engine: str = "xtts") -> bytes:
    return frame({"kind": "audio", "media_type": "", "engine": engine, "keepalive": True, "pause_ms": 0})


def pause_frame(pause_ms: int) -> bytes:
    """The pause marker the service delivers at a paragraph border."""
    return frame({"kind": "audio", "media_type": "", "engine": "", "keepalive": False, "pause_ms": pause_ms})


def summary_frame(trace: list[dict[str, Any]] | None = None, switch: str | None = None) -> bytes:
    body = json.dumps({"trace": trace or [], "switch": switch}).encode()
    return frame({"kind": "summary"}, body)


def error_frame(code: str, detail: str) -> bytes:
    return frame({"kind": "error"}, json.dumps({"error": code, "detail": detail}).encode())


async def _in_pieces(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    """The frames in small chunks that cut frames anywhere, like a real connection."""
    for start in range(0, len(data), size):
        yield data[start : start + size]


def catalog_row(engine: str, host: str | None = None, **fields: Any) -> dict[str, Any]:
    base = {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()},
        "status": "running", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {}, "idle_remaining_s": None,
        "default_vram_mib": None, "default_max_parallel": 1, "default_max_queue": 4,
    }
    return {**base, **fields}


class FakeTTSService:
    """``script``: what each /speak call answers (frames), in order of the calls; the last one repeats.
    A script entry that is an ``int`` answers with that HTTP error status and a ``no_engine`` body."""

    def __init__(self, catalog: list[dict[str, Any]], voices: list[dict[str, str]] | None = None) -> None:
        self.catalog = catalog
        self.voices = voices if voices is not None else [{"name": "Alice", "label": "Alice"}]
        self.voices_dir = "/srv/tts/voices"
        self.script: list[bytes | int] = []
        self.speaks: list[dict[str, Any]] = []
        self.releases: list[Any] = []  # the body of each POST /gpu/release
        self.release_outcome = "released"  # what every released item answers

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/engines":
            return httpx.Response(200, json=self.catalog)
        if path == "/voices":
            return httpx.Response(200, json=self.voices)
        if path == "/config":
            return httpx.Response(200, json={"voices_dir": self.voices_dir, "hosts": []})
        if path == "/speak":
            self.speaks.append(json.loads(request.content))
            step = self.script[min(len(self.speaks), len(self.script)) - 1]
            if isinstance(step, int):
                body = {"error": "no_engine", "detail": "no TTS entry can speak", "trace": []}
                return httpx.Response(step, json=body)
            return httpx.Response(200, headers={"content-type": tts_client.FRAMES_MEDIA_TYPE}, content=_in_pieces(step))
        if path == "/gpu/release":
            body = json.loads(request.content)
            self.releases.append(body)
            status = {"busy": 409, "failed": 500}.get(self.release_outcome, 200)
            results = [{"item": item, "outcome": self.release_outcome, "detail": ""} for item in body["items"]]
            return httpx.Response(status, json={"results": results})
        return httpx.Response(404)


def install_fake_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, fake: FakeTTSService, listed: list[str] | None = None,
    agent_voices: dict[str, Any] | None = None, base_url: str = "http://tts.test",
) -> dict[str, Any]:
    """Make ``get_tts_client()`` talk to ``fake``, AIfred's list = ``listed`` engines (default: the
    catalog's engines on this machine, all on), the TTS audio folder = ``tmp_path``. ``base_url``:
    where the WebSocket of a text stream connects (HTTP always goes to ``fake``)."""
    client = TTSClient(base_url, transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(tts_client, "_default_client", client)
    keys = listed if listed is not None else [row["engine"] for row in fake.catalog if row["host"] is None]
    settings = {
        "tts_escalation": [{"engine": key, "host": None, "enabled": True} for key in keys],
        "tts_toggles_per_engine": {},
        "tts_agent_voices_per_engine": agent_voices if agent_voices is not None else {
            key: {"aifred": {"voice": "Alice", "speed": "1.0", "pitch": "1.0"}} for key in keys
        },
    }
    monkeypatch.setattr(tts_service, "persisted_settings", lambda: settings)
    monkeypatch.setattr(settings_module, "persisted_settings", lambda: settings)
    monkeypatch.setattr(audio_processing, "TTS_AUDIO_DIR", tmp_path)
    tts_service.reset_cache()
    return settings


class FakeStreamingService:
    """The WebSocket ``/speak/stream`` of the service. ``after_text[n]``: frames sent after the n-th
    text message arrived (the audio comes while the text is still being written); ``after_finish``:
    frames sent after ``finish`` (the last of them normally a summary). ``on_start``: frames sent right
    after the start message (an error frame for a reading that cannot start; the connection then closes).
    ``started`` / ``texts`` record what the client sent, ``closed`` is set when the connection is over."""

    def __init__(
        self, after_text: list[list[bytes]] | None = None, after_finish: list[bytes] | None = None,
        on_start: list[bytes] | None = None,
    ) -> None:
        self.after_text = after_text or []
        self.after_finish = after_finish if after_finish is not None else [summary_frame()]
        self.on_start = on_start
        self.started: dict[str, Any] = {}
        self.texts: list[str] = []
        self.finished = False
        self.closed = asyncio.Event()

    async def handler(self, socket: ServerConnection) -> None:
        try:
            self.started = json.loads(await socket.recv())
            if self.on_start is not None:
                for data in self.on_start:
                    await socket.send(data)
                await socket.close()
                return
            async for raw in socket:
                message = json.loads(raw)
                if message["type"] == "text":
                    self.texts.append(message["text"])
                    if len(self.texts) <= len(self.after_text):
                        for data in self.after_text[len(self.texts) - 1]:
                            await socket.send(data)
                else:
                    self.finished = True
                    for data in self.after_finish:
                        await socket.send(data)
                    await socket.close()
                    return
        finally:
            self.closed.set()


@contextlib.asynccontextmanager
async def serve_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, fake: FakeTTSService, stream: FakeStreamingService,
    listed: list[str] | None = None, agent_voices: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Like ``install_fake_service``, with the text stream served by ``stream`` on a loopback port."""
    async with serve(stream.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield install_fake_service(
            monkeypatch, tmp_path, fake, listed=listed, agent_voices=agent_voices, base_url=f"http://127.0.0.1:{port}",
        )
