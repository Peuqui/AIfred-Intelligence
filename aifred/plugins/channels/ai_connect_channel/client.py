"""Minimal AI-Connect Bridge client for the AIfred channel plugin.

Speaks the Bridge's WebSocket protocol directly (register, message,
list_peers, history). The plugin owns this one connection and with it the
peer identity; it deliberately does not import the AI-Connect repository
(plugin atomicity: no code dependency outside of ``aifred/``).
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any, Awaitable, Callable

import websockets

from ....lib.logging_utils import log_message

# Sender of the Bridge's own notices; no peer can have this name
BRIDGE_SENDER = "Bridge"
PING_INTERVAL_SECONDS = 25
REQUEST_TIMEOUT_SECONDS = 5.0
RECONNECT_DELAY_START_SECONDS = 2.0
RECONNECT_DELAY_MAX_SECONDS = 30.0


class BridgeFatalError(Exception):
    """Reconnecting cannot help: wrong token, or the name is held by another session."""


class BridgeClient:
    """One registered connection to the Bridge, with auto-reconnect."""

    def __init__(self, host: str, port: int, token: str, peer_name: str) -> None:
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self.peer_name = peer_name
        self._ws: websockets.ClientConnection | None = None
        # Answers to requests, keyed by the response type ("peer_list", "history")
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_lock = asyncio.Lock()
        # Incoming peer messages are handled one after the other by a worker,
        # so a long LLM turn never blocks the receive loop (and with it the
        # answers to list_peers / history requests).
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def run(self, on_message: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Stay connected until cancelled; raises BridgeFatalError when
        reconnecting is pointless."""
        worker = asyncio.create_task(self._dispatch_loop(on_message))
        delay = RECONNECT_DELAY_START_SECONDS
        try:
            while True:
                try:
                    await self._session()
                    delay = RECONNECT_DELAY_START_SECONDS
                except (OSError, websockets.exceptions.WebSocketException) as exc:
                    log_message(f"AI-Connect: connection to {self._uri} lost ({exc}), retry in {delay:.0f} s", "warning")
                finally:
                    self._ws = None
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, RECONNECT_DELAY_MAX_SECONDS)
        finally:
            worker.cancel()

    async def _session(self) -> None:
        try:
            ws = await websockets.connect(
                self._uri,
                ping_interval=60,
                ping_timeout=300,
                additional_headers={"Authorization": f"Bearer {self._token}"},
            )
        except websockets.exceptions.InvalidStatus as exc:
            if exc.response.status_code == HTTPStatus.UNAUTHORIZED:
                raise BridgeFatalError("the Bridge refused the token") from exc
            raise
        async with ws:
            await ws.send(json.dumps({"type": "register", "name": self.peer_name}))
            ping = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    self._handle(ws, json.loads(raw))
            finally:
                ping.cancel()

    def _handle(self, ws: websockets.ClientConnection, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind == "registered":
            self._ws = ws
            log_message(f"AI-Connect: registered as '{self.peer_name}' at {self._uri}")
        elif kind == "message":
            self._inbox.put_nowait(data)
        elif kind == "unread":
            for message in data.get("messages", []):
                self._inbox.put_nowait(message)
        elif kind == "replaced":
            raise BridgeFatalError(f"another session took over the peer name '{self.peer_name}'")
        elif kind in self._pending:
            future = self._pending[kind]
            if not future.done():
                future.set_result(data)
        elif kind == "error":
            log_message(f"AI-Connect: the Bridge reported an error: {data.get('error')}", "error")

    async def _dispatch_loop(self, on_message: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        while True:
            message = await self._inbox.get()
            try:
                await on_message(message)
            except Exception as exc:  # noqa: BLE001 — one bad message must not stop the worker
                log_message(f"AI-Connect: handling a message from {message.get('from')} failed: {exc!r}", "error")

    async def _ping_loop(self, ws: websockets.ClientConnection) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await ws.send(json.dumps({"type": "ping"}))

    async def _send(self, data: dict[str, Any]) -> None:
        if self._ws is None:
            raise ConnectionError("not connected to the Bridge")
        await self._ws.send(json.dumps(data))

    async def send_message(self, to: str, content: str) -> None:
        await self._send({"type": "message", "to": to, "content": content, "context": None})

    async def _request(self, data: dict[str, Any], response_type: str) -> dict[str, Any]:
        async with self._request_lock:
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[response_type] = future
            try:
                await self._send(data)
                return await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_SECONDS)
            finally:
                self._pending.pop(response_type, None)

    async def list_peers(self) -> list[dict[str, Any]]:
        response = await self._request({"type": "list_peers"}, "peer_list")
        peers: list[dict[str, Any]] = response.get("peers", [])
        return peers

    async def history(self, peer: str, limit: int) -> list[dict[str, Any]]:
        response = await self._request({"type": "history", "peer": peer, "limit": limit}, "history")
        messages: list[dict[str, Any]] = response.get("messages", [])
        return messages
