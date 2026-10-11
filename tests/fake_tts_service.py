"""A fake TTS-Suite service for the speaking paths (Echo, announce, narrator): an
httpx.MockTransport that answers /health, /engines, /voices and /speak, like the real
service would. The real one is never touched (see conftest ``_no_real_tts_service``)."""

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

import aifred.lib.audio_processing as audio_processing
import aifred.lib.settings as settings_module
import aifred.lib.tts_client as tts_client
import aifred.lib.tts_service as tts_service
from aifred.lib.tts_client import TTSClient


def frame(header: dict[str, Any], body: bytes = b"") -> bytes:
    head = json.dumps({**header, "bytes": len(body)}).encode("utf-8")
    return struct.pack(">I", len(head)) + head + body


def audio_frame(data: bytes, engine: str = "xtts", media_type: str = "audio/wav") -> bytes:
    return frame({"kind": "audio", "media_type": media_type, "engine": engine, "keepalive": False}, data)


def keepalive_frame(engine: str = "xtts") -> bytes:
    return frame({"kind": "audio", "media_type": "", "engine": engine, "keepalive": True})


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
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {},
    }
    return {**base, **fields}


class FakeTTSService:
    """``script``: what each /speak call answers (frames), in order of the calls; the last one repeats.
    A script entry that is an ``int`` answers with that HTTP error status and a ``no_engine`` body."""

    def __init__(self, catalog: list[dict[str, Any]], voices: list[dict[str, str]] | None = None) -> None:
        self.catalog = catalog
        self.voices = voices if voices is not None else [{"name": "Alice", "label": "Alice"}]
        self.script: list[bytes | int] = []
        self.speaks: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/engines":
            return httpx.Response(200, json=self.catalog)
        if path == "/voices":
            return httpx.Response(200, json=self.voices)
        if path == "/speak":
            self.speaks.append(json.loads(request.content))
            step = self.script[min(len(self.speaks), len(self.script)) - 1]
            if isinstance(step, int):
                body = {"error": "no_engine", "detail": "no TTS entry can speak", "trace": []}
                return httpx.Response(step, json=body)
            return httpx.Response(200, headers={"content-type": tts_client.FRAMES_MEDIA_TYPE}, content=_in_pieces(step))
        return httpx.Response(404)


def install_fake_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, fake: FakeTTSService, listed: list[str] | None = None,
    agent_voices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make ``get_tts_client()`` talk to ``fake``, AIfred's list = ``listed`` engines (default: the
    catalog's engines on this machine, all on), the TTS audio folder = ``tmp_path``."""
    client = TTSClient("http://tts.test", None, transport=httpx.MockTransport(fake.handler))
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
