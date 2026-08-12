"""Verbindungs-/Registrierungs-Handling des FreeEcho.2-Channels.

WebSocket-Server (aiohttp) inkl. TLS-Setup, Register-Frame mit A6-Token-
Auth, Room-Slot-Takeover und dem kompletten per-Connection-Lifecycle
(Frame-Dispatch an Commands-/Pipeline-Mixin, Cleanup beim Disconnect).
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import TYPE_CHECKING

from . import alert_queue
from ._shared import (
    _DEFAULT_PATH,
    _DEFAULT_PORT,
    _REJECT_LOG_INTERVAL_SEC,
    _devices,
    _pending_wake_agent,
    _pipeline_tasks,
    _reject_log_last,
    _required_auth_token,
)
from .commands import CommandsMixin
from .pipeline import AudioPipelineMixin

if TYPE_CHECKING:
    from aiohttp.web import Request, WebSocketResponse


class ConnectionMixin(CommandsMixin, AudioPipelineMixin):
    """WebSocket-Server-Loop + per-Connection-Handling."""

    # ── Listener (WebSocket server) ───────────────────────────

    async def listener_loop(self) -> None:
        """Run WebSocket server for FreeEcho.2 devices."""
        import ssl
        from ....lib.credential_broker import broker

        port = int(broker.get("freeecho2", "port") or str(_DEFAULT_PORT))
        cert_file = broker.get("freeecho2", "ssl_cert") or ""
        key_file = broker.get("freeecho2", "ssl_key") or ""

        try:
            from aiohttp import web
        except ImportError:
            self.channel_log("aiohttp not installed, FreeEcho.2 disabled", "error")
            return

        app = web.Application()
        app.router.add_get(_DEFAULT_PATH, self._handle_ws)

        runner = web.AppRunner(app)
        await runner.setup()

        # TLS setup
        ssl_ctx = None
        if cert_file and key_file:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(cert_file, key_file)
            self.channel_log(f"TLS enabled (cert: {cert_file})")

        site = web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_ctx)
        await site.start()
        # Diesen Loop für den proaktiven Alarm-Pfad festhalten: enqueue_alert
        # marshallt Queue/Worker/Pump hierher, damit ws.send_bytes immer aus
        # dem Loop läuft, dem der WebSocket gehört (kein Cross-Loop-Abbruch).
        alert_queue._ws_loop = asyncio.get_running_loop()
        proto = "wss" if ssl_ctx else "ws"
        self.channel_log(f"WebSocket server listening on {proto}://0.0.0.0:{port}{_DEFAULT_PATH}")
        if not _required_auth_token():
            # A6: without an enforced token every reachable host can register
            # as a device and drive the voice pipeline + COMMUNICATE tools.
            self.channel_log(
                "register auth is OFF (no token set, or auth_required=false) "
                "— accepting UNAUTHENTICATED registrations (A6). Set the auth "
                "token in the FreeEcho.2 channel settings and the Puck web UI.",
                "warning",
            )

        try:
            # Keep running until cancelled
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.channel_log("Shutting down WebSocket server")
            # Close every active client connection with a proper Close
            # frame.  Without this the FreeEcho.2 keeps the TCP socket half-
            # open (aiohttp's cleanup() does not notify websocket peers
            # by itself) and only notices the disconnect on its next
            # send attempt — which can be minutes later.
            for room, ws in list(_devices.items()):
                try:
                    await ws.close(code=1001, message=b"server shutting down")
                except Exception as e:
                    self.channel_log(
                        f"Error closing WebSocket for {room}: {e}", "warning",
                    )
            _devices.clear()
            # Cancel all long-lived per-room alert workers so they don't
            # survive the server (each is a `while True` on queue.get()).
            for worker in alert_queue._alert_workers.values():
                if not worker.done():
                    worker.cancel()
            alert_queue._alert_workers.clear()
            alert_queue._alert_queues.clear()
            alert_queue._playback_done.clear()
            # Loop-Referenz zurücksetzen: Nach einem Worker-Respawn würde
            # run_on_ws_loop sonst run_coroutine_threadsafe auf den TOTEN
            # Loop schedulen (Coroutine hängt für immer). None → direktes
            # await beim Aufrufer, bis start() wieder läuft.
            alert_queue._ws_loop = None
        finally:
            await runner.cleanup()

    async def _handle_ws(self, request: Request) -> WebSocketResponse:
        """Handle a single FreeEcho.2 WebSocket connection."""
        from aiohttp import web, WSMsgType

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Kein Frame-Processing vor dem Register-Frame: Der Puck registriert
        # als Erstes nach dem Connect. Vorher liefen Text-/Audio-Frames unter
        # dem GETEILTEN Platzhalter room="unknown" — zwei unregistrierte
        # Verbindungen kollidierten auf demselben Pipeline-/Device-Slot
        # (Pipeline-Supersession, TTS-Fehlrouting). Jetzt: bis zum Register
        # werden Frames verworfen (geloggt).
        room: "str | None" = None
        # Letzte VON DIESER Verbindung gestartete Pipeline-Task — der finally
        # cancelt nur die eigene Task, nie den Dict-Eintrag: nach einem
        # Room-Takeover steht dort schon die Pipeline der NEUEN Verbindung.
        my_pipeline: "asyncio.Task | None" = None
        self.channel_log(f"FreeEcho.2 connection from {request.remote}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Register-Frame zuerst auswerten, damit room gesetzt ist.
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        data = None
                    if isinstance(data, dict) and data.get("type") == "register":
                        # A6: when an auth token is configured, the register
                        # frame must carry a matching "token" field — otherwise
                        # the connection is closed before it can claim a room
                        # slot or drive the STT→LLM→TTS pipeline. Constant-time
                        # compare; app close code 4401 (unauthorized).
                        expected_token = _required_auth_token()
                        if expected_token:
                            supplied = str(data.get("token") or "")
                            if not hmac.compare_digest(supplied, expected_token):
                                # Rate-limit the warning per remote — a
                                # reject-reconnect loop must not flood the log.
                                remote = str(request.remote)
                                now = asyncio.get_running_loop().time()
                                last = _reject_log_last.get(remote, 0.0)
                                if now - last >= _REJECT_LOG_INTERVAL_SEC:
                                    _reject_log_last[remote] = now
                                    self.channel_log(
                                        f"FreeEcho.2 register from {remote} "
                                        f"rejected: invalid or missing auth token "
                                        f"(further rejects from this host "
                                        f"suppressed for "
                                        f"{int(_REJECT_LOG_INTERVAL_SEC)}s)",
                                        "warning",
                                    )
                                await ws.close(code=4401, message=b"invalid token")
                                return ws
                        room = str(data.get("room") or "unknown")
                        existing = _devices.get(room)
                        if existing is not None and existing is not ws:
                            # Another connection already owns this room slot.
                            # Close the stale socket so it isn't silently
                            # starved of replies (TTS would otherwise route to
                            # the new socket). NOTE: without endpoint auth this
                            # cannot distinguish a reconnect from a hijack — see
                            # A6 in SECURITY_FINDINGS.md.
                            self.channel_log(
                                f"FreeEcho.2 room '{room}' slot taken over — closing previous socket",
                                "warning",
                            )
                            try:
                                await existing.close(code=1001, message=b"room slot taken over")
                            except Exception:
                                pass
                        _devices[room] = ws
                        self.channel_log(f"FreeEcho.2 registered: room={room}")
                    if room is None:
                        self.channel_log(
                            f"FreeEcho.2 text frame before register from {request.remote} — dropped",
                            "warning",
                        )
                        continue
                    await self._handle_text(ws, msg.data, room)

                elif msg.type == WSMsgType.BINARY:
                    if room is None:
                        self.channel_log(
                            f"FreeEcho.2 audio frame before register from {request.remote} — dropped",
                            "warning",
                        )
                        continue
                    bin_room: str = room
                    # Audio-Pipeline in eigene Task auslagern, sonst blockiert
                    # der lange STT/LLM/TTS-Lauf den async-for-Reader und
                    # ein nachfolgender "wake _stop"-Frame liegt im aiohttp-
                    # Queue bis die Pipeline schon durch ist (= Action-Button
                    # auf dem Puck wirkt nicht). Per-Room-Slot mit
                    # newest-wins-Supersession: falls noch eine alte Pipeline
                    # laeuft (sollte nicht, der Puck schickt sequenziell)
                    # wird die zuerst gecancelt.
                    previous = _pipeline_tasks.get(bin_room)
                    if previous is not None and not previous.done():
                        previous.cancel()
                    task = asyncio.create_task(
                        self._handle_audio(ws, msg.data, bin_room)
                    )
                    _pipeline_tasks[bin_room] = task
                    my_pipeline = task

                    def _cleanup_pipeline(t: asyncio.Task, r: str = bin_room) -> None:
                        if _pipeline_tasks.get(r) is t:
                            _pipeline_tasks.pop(r, None)

                    task.add_done_callback(_cleanup_pipeline)

                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break

        except Exception as e:
            self.channel_log(f"WebSocket error ({room}): {e}", "error")
        finally:
            if room is not None:
                # Laufende EIGENE Pipeline beim Disconnect canceln — sonst
                # spielt der Server noch TTS in einen toten Socket. Bewusst
                # my_pipeline statt _pipeline_tasks[room]: nach einem Takeover
                # gehört der Dict-Eintrag der neuen Verbindung, deren Query
                # ein alter Handler nicht killen darf.
                if my_pipeline is not None and not my_pipeline.done():
                    my_pipeline.cancel()
                # Only tear down this room's per-room state if THIS socket still
                # owns the slot (a takeover by a newer connection must not clobber it).
                if _devices.get(room) is ws:
                    del _devices[room]
                    # Cancel the long-lived alert worker (a `while True` blocked on
                    # queue.get() that is otherwise never stopped → one leaked task
                    # per room). It respawns on the next enqueue_alert if needed.
                    worker = alert_queue._alert_workers.pop(room, None)
                    if worker is not None and not worker.done():
                        worker.cancel()
                    alert_queue._alert_queues.pop(room, None)
                    alert_queue._playback_done.pop(room, None)
                    _pending_wake_agent.pop(room, None)
            self.channel_log(
                f"FreeEcho.2 disconnected: room={room if room is not None else '(unregistered)'}"
            )

        return ws
