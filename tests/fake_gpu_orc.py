"""A GPU-Orc stand-in for tests: answers /health, /status and /free as set up, records every /free."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aifred.lib import gpu_orc_client

CALLER_TIMEOUT_S = 690


class FakeGpuOrc:
    def __init__(self) -> None:
        self.frees: list[dict[str, Any]] = []  # the body of each POST /free
        self.free_timeouts: list[float] = []  # the read timeout each /free was sent with
        self.actions: list[dict[str, Any]] = []
        self.free = True
        self.already_running = False
        self.cards: list[dict[str, Any]] = []  # the "gpus" of GET /status
        self.refusal: tuple[int, str] | None = None  # (HTTP status, error) every /free answers instead

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={
                "status": "ok", "version": "0.2.0", "free_timeout_s": CALLER_TIMEOUT_S - 30,
                "caller_timeout_s": CALLER_TIMEOUT_S,
            })
        if path == "/status":
            queue = {"running": False, "waiting": 0, "running_since_s": None}
            return httpx.Response(200, json={"queue": queue, "gpus": self.cards, "errors": []})
        if path == "/free":
            self.frees.append(json.loads(request.content))
            self.free_timeouts.append(request.extensions["timeout"]["read"])
            if self.refusal is not None:
                return httpx.Response(self.refusal[0], json={"error": self.refusal[1]})
            return httpx.Response(200, json={
                "gpus": [], "already_running": self.already_running, "duration_s": 0.1,
                "actions": self.actions, "memory": [], "free": self.free, "status": None,
            })
        return httpx.Response(404, json={"error": f"no such path: {path}"})


def install_fake_gpu_orc(monkeypatch: pytest.MonkeyPatch, fake: FakeGpuOrc) -> None:
    client = gpu_orc_client.GpuOrcClient("http://gpu-orc.test", transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(gpu_orc_client, "_default_client", client)


def card(uuid: str, *holders: tuple[str, str, int | None]) -> dict[str, Any]:
    """A card of GET /status with ``(consumer, item, memory_mib)`` holders."""
    return {
        "index": 0, "uuid": uuid, "name": "Test GPU", "memory_total_mib": 32768, "memory_used_mib": 0,
        "holders": [
            {"consumer": consumer, "item": item, "memory_mib": mib, "busy": False, "persistent": None}
            for consumer, item, mib in holders
        ],
        "unattributed_mib": None,
    }
