"""A GPU-Orc stand-in for tests: a small real HTTP server in a thread that answers /health, /status, /loading,
/free, /hold, /hold/grant, /hold/end and /work as set up and records every /free, /hold and /work. The protocol itself (which
timeout, which body) is tested against the real GPU-Orc in its own repo; here the client of AIfred is the
caller, so the stand-in only has to speak HTTP."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from aifred.lib import gpu_orc_client

CALLER_TIMEOUT_S = 690
_running: list[ThreadingHTTPServer] = []


class FakeGpuOrc:
    def __init__(self) -> None:
        self.frees: list[dict[str, Any]] = []  # the body of each POST /free
        self.actions: list[dict[str, Any]] = []
        self.free = True
        self.already_running = False
        self.cards: list[dict[str, Any]] = []  # the "gpus" of GET /status
        self.loading: list[str] = []  # what GET /loading names
        # A hold is {"since", "reason"} plus optional "owner", "model", "model_server", "granted_by", "end_requested_by".
        self.held: dict[str, str | None] | None = None  # the active hold GET /loading names (POST /hold sets it)
        self.hold_requested: dict[str, str | None] | None = None  # the requested hold GET /loading names
        self.hold_bodies: list[dict[str, Any]] = []  # the whole body of each POST /hold
        self.grants: list[dict[str, Any]] = []  # the body of each POST /hold/grant
        self.end_requests: list[dict[str, Any]] = []  # the body of each POST /hold/end
        self.end_refusal: tuple[int, str] | None = None  # (HTTP status, error) every POST /hold/end answers instead
        self.forced_releases: list[str] = []  # the owner of each DELETE /hold with force
        self.holds: list[str] = []  # the reason of each POST /hold
        self.hold_owners: list[str] = []  # the owner of each POST /hold
        self.released_owners: list[str] = []  # the owner of each DELETE /hold
        self.work: dict[str, dict[str, Any]] = {}  # the leases POST /work holds, by owner
        self.work_calls: list[str] = []  # "begin <owner>" / "end <owner>" in the order they came
        self.requests: list[str] = []  # "<METHOD> <path>" of every request, in the order they came
        self.refusal: tuple[int, str] | None = None  # (HTTP status, error) every /free answers instead

    def serve(self) -> str:
        """Start the server on a free port of the loopback; its address. Stopped by :func:`stop_fake_gpu_orcs`."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                status, answer = fake.respond(self.command, self.path, body)
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _running.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def respond(self, method: str, path: str, body: Any) -> tuple[int, dict[str, Any]]:
        self.requests.append(f"{method} {path}")
        if path == "/health":
            return 200, {
                "status": "ok", "version": "0.2.0", "free_timeout_s": CALLER_TIMEOUT_S - 30,
                "caller_timeout_s": CALLER_TIMEOUT_S,
            }
        if path == "/status":
            queue = {"running": False, "waiting": 0, "running_since_s": None}
            return 200, {"queue": queue, "gpus": self.cards, "errors": []}
        if path == "/loading":
            return 200, {
                "loading": self.loading,
                "held": self._wire_hold(self.held, "active"),
                "hold_requested": self._wire_hold(self.hold_requested, "requested"),
            }
        if path == "/hold" and method == "POST":
            self.holds.append(body["reason"])
            self.hold_owners.append(body["owner"])
            self.hold_bodies.append(body)
            self.held = {
                "since": "2026-11-05T09:00:00+01:00", "reason": body["reason"], "owner": body["owner"],
                "model": body["model"], "model_server": body.get("model_server"),
            }
            # GPU-Orc answers every new hold as requested; without work it turns active right after (held)
            return 200, {"hold": self._wire_hold(self.held, "requested")}
        if path == "/hold/grant" and method == "POST":
            self.grants.append(body)
            hold = self.held or self.hold_requested
            if hold is None:
                return 404, {"error": "no hold"}
            if hold.get("owner", "somebody") != body["owner"]:
                return 409, {"error": "the hold belongs to another owner"}
            hold["granted_by"] = body["by"]
            return 200, {"hold": self._wire_hold(hold, "active" if hold is self.held else "requested")}
        if path == "/hold/end" and method == "POST":
            self.end_requests.append(body)
            if self.end_refusal is not None:
                return self.end_refusal[0], {"error": self.end_refusal[1]}
            hold = self.held or self.hold_requested
            if hold is None:
                return 404, {"error": "no hold"}
            if hold.get("owner", "somebody") != body["owner"]:
                return 409, {"error": "the hold belongs to another owner"}
            if hold is self.hold_requested:  # nothing on the cards: withdrawn at once
                self.hold_requested = None
                return 200, {"hold": None}
            hold["end_requested_by"] = hold.get("end_requested_by") or body["by"]  # the first request stays
            return 200, {"hold": self._wire_hold(hold, "active")}
        if path == "/hold" and method == "DELETE":
            hold = self.held or self.hold_requested
            if hold is not None and hold.get("owner", "somebody") != body["owner"] and not body["force"]:
                return 409, {"error": "the hold belongs to another owner"}
            self.released_owners.append(body["owner"])
            if body["force"]:
                self.forced_releases.append(body["owner"])
            self.held = self.hold_requested = None
            return 200, {"hold": None}
        if path == "/work" and method == "POST":
            self.work[body["owner"]] = body
            self.work_calls.append(f"begin {body['owner']}")
            return 200, {"work": list(self.work.values())}
        if path.startswith("/work/") and method == "DELETE":
            owner = urllib.parse.unquote(path.removeprefix("/work/"))
            self.work_calls.append(f"end {owner}")
            ended = self.work.pop(owner, None) is not None
            return 200, {"ended": ended, "work": list(self.work.values())}
        if path == "/free":
            self.frees.append(body)
            if self.refusal is not None:
                return self.refusal[0], {"error": self.refusal[1]}
            return 200, self._freed()
        return 404, {"error": f"no such path: {path}"}

    def _wire_hold(self, hold: dict[str, str | None] | None, state: str) -> dict[str, Any] | None:
        """A hold as GPU-Orc writes it: ``{owner, reason, model, model_server, since, state, free, granted_by, end_requested_by}``;
        ``free`` only once active."""
        if hold is None:
            return None
        return {
            "owner": hold.get("owner", "somebody"), "reason": hold["reason"], "model": hold.get("model"),
            "model_server": hold.get("model_server"), "since": hold["since"], "state": state,
            "free": self._freed() if state == "active" else None, "granted_by": hold.get("granted_by"),
            "end_requested_by": hold.get("end_requested_by"),
        }

    def _freed(self) -> dict[str, Any]:
        return {
            "gpus": [], "already_running": self.already_running, "duration_s": 0.1,
            "actions": self.actions, "memory": [], "free": self.free, "status": None,
        }


def stop_fake_gpu_orcs() -> None:
    while _running:
        server = _running.pop()
        server.shutdown()
        server.server_close()


def install_fake_gpu_orc(monkeypatch: pytest.MonkeyPatch, fake: FakeGpuOrc) -> None:
    client = gpu_orc_client.GpuOrcClient(fake.serve())
    monkeypatch.setattr(gpu_orc_client, "_default_client", client)


def current_client() -> gpu_orc_client.GpuOrcClient:
    """The client of this machine's GPU-Orc as the code under test gets it (a test that wants GPU-Orc on)."""
    client = gpu_orc_client.get_gpu_orc_client()
    assert client is not None
    return client


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
