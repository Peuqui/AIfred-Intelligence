"""Gemeinsamer JSON-IPC-Client für mpv (SSOT).

Ein Client = ein mpv-Prozess = ein Unix-Socket. Der Browser-Player
(``AudioManager``) und jeder ``FreeEcho2Stream`` halten je eine EIGENE
Instanz mit eigenem Socket, eigener Read-Loop und eigener
Request-ID-Verwaltung — es gibt keinen geteilten Zustand zwischen den
Consumern, sie können sich nicht gegenseitig blockieren.

Konsolidiert das vorher doppelt implementierte Muster: Kommando senden
mit request_id-Future-Korrelation, readline-Dispatch-Schleife,
``eof-reached``-Beobachtung → ``on_eof``-Callback, ``get_property``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .logging_utils import log_message


class MpvIpcClient:
    """JSON-IPC-Client für genau einen mpv-Prozess.

    Args:
        command_timeout_sec: Timeout für eine Kommando-Antwort.
        error_factory: Baut die consumer-spezifische Exception
            (``MpvError`` bzw. ``FreeEcho2StreamError`` mit Room-Prefix).
        log_prefix: Prefix für Log-Zeilen der Read-Loop.
        read_error_log_level: Log-Level für Read-Loop-Fehler
            (Browser-Player loggt "error", FreeEcho2 "warning").
        on_eof: Async-Callback, wenn mpv ``eof-reached=True`` meldet.
    """

    def __init__(
        self,
        *,
        command_timeout_sec: float,
        error_factory: Callable[[str], Exception],
        log_prefix: str,
        read_error_log_level: str,
        on_eof: Callable[[], Awaitable[None]],
    ) -> None:
        self._command_timeout_sec = command_timeout_sec
        self._error_factory = error_factory
        self._log_prefix = log_prefix
        self._read_error_log_level = read_error_log_level
        self._on_eof = on_eof

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._writer is not None

    @property
    def reader_task(self) -> Optional["asyncio.Task[None]"]:
        """Read-Loop-Task — für Consumer, die ihn in ihre eigene
        Cancel/Await-Reihenfolge einsortieren (FreeEcho2-Cleanup)."""
        return self._reader_task

    async def connect(self, socket_path: str | Path, *, task_name: str) -> None:
        """Socket verbinden, Read-Loop starten, ``eof-reached`` abonnieren."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(socket_path)
        )
        self._reader_task = asyncio.create_task(self._read_loop(), name=task_name)
        await self.send({"command": ["observe_property", 1, "eof-reached"]})

    async def close_writer(self) -> None:
        """Nur die Verbindung schließen (Task-Handling macht der Consumer)."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._writer = None
        self._reader = None
        self._reader_task = None

    async def close(self) -> None:
        """Read-Loop stoppen und Verbindung schließen."""
        task = self._reader_task
        self._reader_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task
        await self.close_writer()

    def clear_pending(self) -> None:
        self._pending.clear()

    # ── IPC primitives ────────────────────────────────────────

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON command, await its response."""
        if self._writer is None:
            raise self._error_factory("IPC not connected")

        self._request_id += 1
        rid = self._request_id
        wrapped = {**payload, "request_id": rid}

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut

        try:
            line = (json.dumps(wrapped) + "\n").encode("utf-8")
            self._writer.write(line)
            await self._writer.drain()
            return await asyncio.wait_for(fut, timeout=self._command_timeout_sec)
        finally:
            self._pending.pop(rid, None)

    async def get_property(self, name: str, default: Any = None) -> Any:
        """Best-effort Property-Read — Fehler liefern ``default``."""
        try:
            resp = await self.send({"command": ["get_property", name]})
            if resp.get("error") == "success":
                return resp.get("data", default)
        except Exception:  # noqa: BLE001
            pass
        return default

    async def _read_loop(self) -> None:
        """Continuously dispatch responses + events from the socket."""
        if self._reader is None:
            return
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                rid = msg.get("request_id")
                if rid is not None and rid in self._pending:
                    fut = self._pending[rid]
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                if msg.get("event") == "property-change":
                    if msg.get("name") == "eof-reached" and msg.get("data") is True:
                        await self._on_eof()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log_message(
                f"{self._log_prefix}: IPC read loop error: {exc}",
                self._read_error_log_level,
            )
