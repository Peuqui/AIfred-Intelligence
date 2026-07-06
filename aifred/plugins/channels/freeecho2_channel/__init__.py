"""FreeEcho.2 Channel Plugin — WebSocket server for voice terminals.

FreeEcho.2 devices (Echo Dot 2nd Gen with custom firmware) connect via
WebSocket and send audio after wake word detection. AIfred processes
the audio (STT → LLM → TTS) and streams the response back.

Protocol:
  Client → Server:
    Text:   {"type":"register","room":"wohnzimmer","capabilities":["audio_in","audio_out"]}
    Text:   {"type":"wake","room":"wohnzimmer"}
    Binary: Raw PCM audio (16kHz mono int16) after recording
  Server → Client:
    Binary: TTS audio (48kHz mono int16) for playback
    Text:   {"type":"status","message":"processing"}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import wave
import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine, Optional

from ....lib.config import get_tts_engine_channel_options
from ....lib.formatting import format_number
from ....lib.plugin_base import BaseChannel, CredentialField, load_tool_description


def _fmt_mib(num_bytes: int) -> str:
    """Bytes als MiB mit 1 Nachkomma (locale-aware Tausender/Dezimal)."""
    return f"{format_number(num_bytes / (1024 * 1024), 1)} MiB"


def _channel_tts_options() -> list[tuple[str, str]]:
    """Thin wrapper around the central SSOT so the CredentialField stays
    readable and the SSOT call is documented at the call site."""
    return get_tts_engine_channel_options()

if TYPE_CHECKING:
    from aiohttp.web import Request, WebSocketResponse
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# Connected FreeEcho.2 devices: room_name → WebSocketResponse
_devices: dict[str, WebSocketResponse] = {}
# A6 reject-log rate limit: remote-IP → monotonic timestamp of the last
# logged rejection. A puck with a wrong/missing token reconnects in a loop
# (old firmware retried instantly); without this cap every attempt writes a
# log line — flooding the debug log is itself a DoS vector.
_reject_log_last: dict[str, float] = {}
_REJECT_LOG_INTERVAL_SEC = 60.0


def _required_auth_token() -> str:
    """SSOT for the A6 auth decision: the expected register token, or ""
    when authentication is off.

    Auth is active when a token is configured AND the explicit switch
    FREEECHO2_AUTH_REQUIRED is not the literal "false" (fail-safe towards
    ON: unset/any other value keeps the check active)."""
    from ....lib.credential_broker import broker

    if (broker.get("freeecho2", "auth_required") or "").strip().lower() == "false":
        return ""
    return broker.get("freeecho2", "auth_token") or ""
# Pending TTS responses: room_name → asyncio.Future
_pending_responses: dict[str, asyncio.Future] = {}
# Wake-Word → Agent-Hint: room_name → agent_id
# Populated by wake events, consumed by the next audio event from the same room.
# A stale entry (wake without audio) is harmless: the FreeEcho.2 only sends audio
# directly after wake detection, and a new wake overwrites or clears this.
_pending_wake_agent: dict[str, str] = {}

# ── Proaktive Alarm-Warteschlange (server-owned, pro room) ───────────────
# "Smart server, stupid device": der Puck puffert nicht (Queue-0), der
# Server serialisiert. Pro room eine Queue + ein langlebiger Worker, der je
# Item den lokalen Chime + TTS abspielt und auf das Puck-Fertig-Signal
# (wake.agent=_done → _playback_done[room].set()) wartet, bevor das nächste
# Item rausgeht. So stauen sich N Alarme sauber auf und werden nacheinander
# angesagt — nichts wird verworfen. Entkoppelt vom Emit-Pfad (enqueue kehrt
# sofort zurück, blockiert den Vision-Watcher nicht).
_alert_queues: dict[str, "asyncio.Queue[tuple[str, bytes | None]]"] = {}
_alert_workers: dict[str, asyncio.Task] = {}
_playback_done: dict[str, asyncio.Event] = {}
# Event-Loop des WebSocket-Servers (aiohttp). Der proaktive Emit-Pfad
# (Vision-Watcher → announce-Tool) läuft im Chat-/Tool-Pipeline-Loop, der
# WebSocket lebt aber hier. Queue, Worker und das TTS-Pumpen müssen im
# ws-Loop laufen, sonst sendet der Pump aus einem fremden Loop
# (RuntimeError: "got Future attached to a different loop" → Pump-Abbruch
# mitten in der Ansage). enqueue_alert marshallt deshalb auf diesen Loop.
_ws_loop: "asyncio.AbstractEventLoop | None" = None
# Fallback-Marge für das _done-Warten: der Worker wartet content-abhängig
# = tatsächliche Wiedergabe-Dauer (aus der PCM-Größe) + diese Marge (Chime +
# Netz + Sicherheit). So wird eine lange Ansage NIE abgeschnitten (Timeout
# immer > echte Wiedergabe), und bei ausbleibendem _done erholt sich die
# Queue trotzdem zügig statt 2 Minuten zu hängen.
_PLAYBACK_DONE_MARGIN_SEC = 15.0
# 48 kHz, int16, mono → Bytes pro Sekunde Wiedergabe.
_PCM_BYTES_PER_SEC = 96000.0


async def run_on_ws_loop(coro: "Coroutine[Any, Any, Any]") -> Any:
    """Eine Coroutine auf dem WebSocket-Server-Loop ausführen, awaitbar aus
    JEDEM Loop. SSoT für proaktive Puck-Audio-Ausgabe.

    Jegliches ``ws.send_bytes`` (TTS-Pump, mpv-FIFO-Pump) und jede mpv-IPC-
    Operation muss in dem Loop laufen, dem der WebSocket gehört — sonst wirft
    asyncio "got Future attached to a different loop" und der Pump bricht
    mitten in der Ausgabe ab. Proaktive Aufrufer (Announce-Queue, Browser-
    Audio-Befehle) laufen im Chat-/Tool-Pipeline-Loop; ihre Orchestrator-/
    Stream-Arbeit wird hierüber auf den ws-Loop geschoben. Läuft der Aufrufer
    bereits im ws-Loop (reaktiver Puck-Request), wird direkt awaited."""
    try:
        current: "asyncio.AbstractEventLoop | None" = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if _ws_loop is not None and _ws_loop is not current:
        fut = asyncio.run_coroutine_threadsafe(coro, _ws_loop)
        return await asyncio.wrap_future(fut)
    return await coro


def _playback_done_event(room: str) -> asyncio.Event:
    evt = _playback_done.get(room)
    if evt is None:
        evt = asyncio.Event()
        _playback_done[room] = evt
    return evt


def signal_playback_done(room: str) -> None:
    """Vom WS-Handler bei ``wake.agent=_done`` gerufen — weckt den Worker für
    das nächste Queue-Item."""
    _playback_done_event(room).set()


async def _enqueue_on_ws_loop(room: str, audio_type: str, tts_pcm: "bytes | None") -> None:
    """Put + Worker-Sicherstellung — läuft IMMER im ws-Loop, damit Queue,
    Worker und das spätere ws.send_bytes loop-konsistent sind."""
    queue = _alert_queues.get(room)
    if queue is None:
        queue = asyncio.Queue()
        _alert_queues[room] = queue
    await queue.put((audio_type, tts_pcm))
    worker = _alert_workers.get(room)
    if worker is None or worker.done():
        _alert_workers[room] = asyncio.create_task(
            _alert_worker(room), name=f"freeecho2-alert-worker-{room}",
        )


async def enqueue_alert(room: str, audio_type: str, tts_pcm: "bytes | None") -> None:
    """Proaktiven Alarm in die room-Queue legen + Worker sicherstellen.
    Kehrt sofort zurück (entkoppelt vom Emit-Pfad).

    Der Emit-Pfad läuft typischerweise in einem anderen Event-Loop als der
    WebSocket; ``run_on_ws_loop`` schiebt Queue/Worker auf den ws-Loop, damit
    der Pump nicht cross-loop sendet."""
    await run_on_ws_loop(_enqueue_on_ws_loop(room, audio_type, tts_pcm))


async def _alert_worker(room: str) -> None:
    """Langlebiger per-room-Worker: spielt Queue-Items seriell, wartet je
    Item auf das _done-Signal des Pucks (mit Timeout). Musik-Pause/Resume
    um den Queue-Lauf kommt mit Firmware-Phase 2 dazu."""
    from ....lib import audio_channels
    from ....lib.logging_utils import log_message

    queue = _alert_queues[room]
    while True:
        audio_type, tts_pcm = await queue.get()
        try:
            ch = audio_channels.resolve(f"freeecho2:{room}")
            orc = (
                ch.get_orchestrator(room)
                if ch is not None and hasattr(ch, "get_orchestrator")
                else None
            )
            if orc is None:
                log_message(
                    f"[FreeEcho.2 {room}] alert worker: no orchestrator — drop",
                    "warning",
                )
                continue
            with_tts = tts_pcm is not None
            # play_* wartet jetzt bis der Tail-Pump durch ist (audio_end raus).
            if audio_type == "alarm":
                await orc.play_alarm(with_tts=with_tts, tts_pcm=tts_pcm)
            else:
                await orc.play_notification(with_tts=with_tts, tts_pcm=tts_pcm)
            # FRISCHES Event pro Item, publiziert erst NACH der Wiedergabe und
            # direkt vor send_done: Das frühere clear-then-wait auf dem
            # wiederverwendeten per-Room-Event konnte während der gesamten
            # Abspieldauer von einem verspäteten _done eines FRÜHEREN Turns
            # geweckt werden. Ein stale _done setzt jetzt das alte
            # Event-Objekt — nicht dieses.
            evt = asyncio.Event()
            _playback_done[room] = evt
            # Design A: kanonische Turn-Grenze. done sagt dem Puck "geh auf
            # IDLE + quittiere mit _done". Symmetrisch zum reaktiven Pfad
            # (audio_end + done in jedem Pfad).
            await orc.bridge.send_done(room)
            # Content-abhängiger Timeout: echte Wiedergabe-Dauer (PCM-Größe)
            # + Marge als Fallback. Normal weckt das _done des Pucks (auf das
            # done-Frame) den Worker sofort; der Timeout greift nur wenn der
            # Puck stumm bleibt.
            playback_sec = (len(tts_pcm) / _PCM_BYTES_PER_SEC) if tts_pcm else 0.0
            done_timeout = playback_sec + _PLAYBACK_DONE_MARGIN_SEC
            try:
                await asyncio.wait_for(evt.wait(), timeout=done_timeout)
            except asyncio.TimeoutError:
                log_message(
                    f"[FreeEcho.2 {room}] alert worker: no _done within "
                    f"{done_timeout:.0f}s (playback ~{playback_sec:.0f}s) — proceeding",
                    "warning",
                )
        except Exception as e:  # noqa: BLE001
            log_message(
                f"[FreeEcho.2 {room}] alert worker error: {e}", "warning",
            )
        finally:
            queue.task_done()

# Aktive Audio-Pipeline-Task pro Room. Der WebSocket-Reader startet
# _handle_audio() als Background-Task und legt die Referenz hier ab, sodass
# der Reader weiterhin Text-Frames (insbesondere "wake _stop") empfangen kann
# waehrend STT/LLM/TTS laeuft. _handle_command_token cancelt dieses Task
# direkt — fuer die Phase BEVOR process_inbound() sich im pipeline_registry
# registriert (STT, TTS-Engine-Setup) gibt es sonst keinen Cancel-Hook.
_pipeline_tasks: dict[str, asyncio.Task] = {}

# WebSocket server port
_DEFAULT_PORT = 9777
_DEFAULT_PATH = "/ws/freeecho2"


class FreeEchoChannel(BaseChannel):
    """FreeEcho.2 voice terminal channel via WebSocket."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "freeecho2"

    @property
    def display_name(self) -> str:
        return "FreeEcho.2"

    @property
    def description(self) -> str:
        return "WebSocket-Server für FreeEcho.2-Speakern: Sprachsteuerung mit Wake-Word, STT (Whisper) und TTS-Rückkanal."

    @property
    def icon(self) -> str:
        return "radio"

    @property
    def always_reply(self) -> bool:
        return True

    @property
    def has_allowlist(self) -> bool:
        return False

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="FREEECHO2_PORT",
                label_key="freeecho2_cred_port",
                placeholder="9777",
            ),
            CredentialField(
                env_key="FREEECHO2_AUTH_TOKEN",
                label_key="freeecho2_cred_auth_token",
                placeholder="shared secret",
                # Masked in the UI; empty field = keep stored value
                # (password semantics). Disabling auth is NOT done by
                # clearing the token but via the explicit toggle below.
                is_password=True,
            ),
            CredentialField(
                env_key="FREEECHO2_AUTH_REQUIRED",
                label_key="freeecho2_cred_auth_required",
                placeholder="true",
                # Explicit auth switch — only the literal "false" disables
                # the register-token check (fail-safe towards ON). Lives in
                # the plugin's settings.json (not a secret).
                options=[("true", "On"), ("false", "Off")],
            ),
            CredentialField(
                env_key="FREEECHO2_TTS_ENGINE",
                label_key="freeecho2_cred_tts_engine",
                placeholder="piper",
                # Pulled from the central TTS-engine SSOT so newly added
                # engines (Qwen3-TTS local etc.) show up here automatically.
                options=_channel_tts_options(),
            ),
        ]

    def is_configured(self) -> bool:
        return True  # No credentials needed — local WebSocket server

    def apply_credentials(self, values: dict[str, str]) -> None:
        from ....lib.credential_broker import broker

        broker.set_runtime("freeecho2", "enabled", "true")
        port = values.get("FREEECHO2_PORT", str(_DEFAULT_PORT))
        broker.set_runtime("freeecho2", "port", port)

        # A6: shared secret checked against the register frame's "token"
        # field. Password semantics: empty = keep the stored token (the
        # .env writer skips empty password values too). Auth on/off is the
        # explicit FREEECHO2_AUTH_REQUIRED switch, not "empty token".
        token_val = values.get("FREEECHO2_AUTH_TOKEN", "")
        if token_val:
            broker.set_runtime("freeecho2", "auth_token", token_val)
        broker.set_runtime(
            "freeecho2", "auth_required",
            values.get("FREEECHO2_AUTH_REQUIRED", ""),
        )

        ssl_cert = values.get("FREEECHO2_SSL_CERT", "")
        ssl_key = values.get("FREEECHO2_SSL_KEY", "")
        if ssl_cert:
            broker.set_runtime("freeecho2", "ssl_cert", ssl_cert)
        if ssl_key:
            broker.set_runtime("freeecho2", "ssl_key", ssl_key)

        # Engine setting is saved here, actual start happens on first FreeEcho.2 request
        # via ensure_engine_ready() in _run_tts()
        new_engine = values.get("FREEECHO2_TTS_ENGINE", "piper")
        broker.set_runtime("freeecho2", "tts_engine", new_engine)

        tts_voice = values.get("FREEECHO2_TTS_VOICE", "de_DE-thorsten-high")
        if tts_voice:
            broker.set_runtime("freeecho2", "tts_voice", tts_voice)

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
        global _ws_loop
        _ws_loop = asyncio.get_running_loop()
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
            for worker in _alert_workers.values():
                if not worker.done():
                    worker.cancel()
            _alert_workers.clear()
            _alert_queues.clear()
            _playback_done.clear()
            # Loop-Referenz zurücksetzen: Nach einem Worker-Respawn würde
            # run_on_ws_loop sonst run_coroutine_threadsafe auf den TOTEN
            # Loop schedulen (Coroutine hängt für immer). None → direktes
            # await beim Aufrufer, bis start() wieder läuft.
            _ws_loop = None
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
                # Laufende Pipeline beim Disconnect canceln — sonst spielt
                # der Server noch TTS in einen toten Socket.
                pending = _pipeline_tasks.get(room)
                if pending is not None and not pending.done():
                    pending.cancel()
                # Only tear down this room's per-room state if THIS socket still
                # owns the slot (a takeover by a newer connection must not clobber it).
                if _devices.get(room) is ws:
                    del _devices[room]
                    # Cancel the long-lived alert worker (a `while True` blocked on
                    # queue.get() that is otherwise never stopped → one leaked task
                    # per room). It respawns on the next enqueue_alert if needed.
                    worker = _alert_workers.pop(room, None)
                    if worker is not None and not worker.done():
                        worker.cancel()
                    _alert_queues.pop(room, None)
                    _playback_done.pop(room, None)
                    _pending_wake_agent.pop(room, None)
            self.channel_log(
                f"FreeEcho.2 disconnected: room={room if room is not None else '(unregistered)'}"
            )

        return ws

    async def _handle_text(self, ws: WebSocketResponse, data: str, room: str) -> None:
        """Handle text message from FreeEcho.2 device."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "register":
            # Never log the auth token in plain text — mask it (A6).
            log_msg = dict(msg)
            if log_msg.get("token"):
                log_msg["token"] = "*" * 8
            self.channel_log(f"[FreeEcho.2 {room}] Register: {log_msg}")

        elif msg_type == "flow":
            # Backpressure-Frame vom FreeEcho.2. State = "pause" wenn der Ring-
            # Buffer voll war (write-blocked) bzw. "resume" wenn fill_pct
            # < 30 fällt. Wir leiten das an die FreeEcho2Channel-Stream-Map
            # weiter, dort wird der pump-Task pausiert/fortgesetzt.
            state = msg.get("state", "")
            self.channel_log(f"[FreeEcho.2 {room}] flow={state}")
            try:
                from ....lib import audio_channels
                ch = audio_channels.resolve(f"freeecho2:{room}")
                if ch is not None and hasattr(ch, "notify_flow"):
                    ch.notify_flow(room, state)
            except Exception as exc:  # noqa: BLE001
                self.channel_log(
                    f"[FreeEcho.2 {room}] flow handling error: {exc}", "warning",
                )

        elif msg_type == "wake":
            wake_agent = msg.get("agent")
            # consumed_ms: echte User-Hoehrposition vom Puck (=
            # consumed frames since stream-start, in ms). Bei großem
            # Puck-Ring ist mpv-time-pos die Decode-Position und liegt
            # vor der Höhrposition. consumed_ms ueberschreibt den
            # Position-Save damit Pre-Roll auf die echte User-Position
            # basiert. Pflichtfeld in Wake-Frames vom Puck.
            consumed_ms = msg.get("consumed_ms")

            # Wake-Pfad in einen session_scope einhuellen — channel_log
            # mirror-t dann live in die UI-Debug-Konsole. Session-ID
            # kommt aus der Routing-Tabelle (vom letzten Inbound dieses
            # Pucks angelegt). Kein Scope wenn keine Route existiert
            # (z.B. allererster Puck-Wake nach Server-Start) — Logs
            # landen dann nur im File, kein Drama.
            from ....lib.debug_bus import session_scope
            from ....lib.routing_table import routing_table
            route = routing_table.get_route("freeecho2", room)
            sid = route.session_id if route else None

            with session_scope(sid):
                # Stream-Start-Offset snapshotten BEVOR der Stream
                # gestoppt wird. Der Puck zaehlt consumed_ms seit dem
                # letzten audio_start (= seit current stream-start),
                # also: track_pos = offset + consumed_ms/1000. Ohne den
                # Snapshot waere der offset nach _handle_command_token
                # nicht mehr verfuegbar (Stream-Slot leer).
                from ....lib import audio_channels
                ch = audio_channels.resolve(f"freeecho2:{room}")
                stream_offset = (
                    ch.get_stream_start_offset(room) if ch is not None else None
                )

                # Command tokens (leading underscore, e.g. "_stop") are
                # processed immediately on the WAKE event — no audio is
                # expected to follow. The FreeEcho.2 just sent us a
                # control signal, not the start of a query.
                if wake_agent and wake_agent.startswith("_"):
                    _pending_wake_agent.pop(room, None)
                    self._log_command_wake(room, wake_agent, consumed_ms)
                    # 1. Stream stoppen (cleanup schreibt mpv-time-pos in
                    #    audio_state als Backup-Position)
                    # 2. consumed_ms+offset ueberschreibt das mit der
                    #    echten Track-Position — Reihenfolge zaehlt
                    await self._handle_command_token(wake_agent, room)
                    self._override_position_with_consumed_ms(
                        room, consumed_ms, stream_offset_sec=stream_offset,
                    )
                    await ws.send_str(json.dumps({"type": "status", "message": "ready"}))
                    return

                # Normal wake (Audio-Query folgt): Music-Source wird vom Puck
                # vermutlich preempted. consumed_ms in audio_state schreiben
                # damit User später via audio_resume nahtlos zurueckkommt.
                self._override_position_with_consumed_ms(
                    room, consumed_ms, stream_offset_sec=stream_offset,
                )

                pos_str = self._fmt_consumed(consumed_ms)
                if wake_agent:
                    _pending_wake_agent[room] = str(wake_agent)
                    self.channel_log(
                        f"🎤 [FreeEcho.2 {room}] wake (agent={wake_agent}) "
                        f"@ {pos_str} — recording started"
                    )
                else:
                    _pending_wake_agent.pop(room, None)
                    self.channel_log(
                        f"🎤 [FreeEcho.2 {room}] wake @ {pos_str} "
                        f"— recording started"
                    )
                # Pre-signal: could trigger model warmup here
                # For now just acknowledge
                await ws.send_str(json.dumps({"type": "status", "message": "ready"}))

    @staticmethod
    def _fmt_consumed(consumed_ms: Any) -> str:
        """Format consumed_ms as ``m:ss (XXX,X s)`` — locale-aware via format_number."""
        if consumed_ms is None:
            return "?"
        try:
            ms = int(consumed_ms)
        except (TypeError, ValueError):
            return "?"
        secs = ms / 1000.0
        total = int(secs)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        return f"{ts} ({format_number(secs, 1)} s)"

    def _log_command_wake(self, room: str, token: str, consumed_ms: Any) -> None:
        """Schoenes pre-action log fuer Command-Wake-Words."""
        emoji = {
            "_stop":     "⏹️",
            "_pause":    "⏸️",
            "_resume":   "▶️",
            "_standby":  "🌙",
            "_activate": "💡",
        }.get(token, "🔘")
        pos_str = self._fmt_consumed(consumed_ms)
        self.channel_log(
            f"🎤 [FreeEcho.2 {room}] {emoji} command wake: "
            f"{token} @ {pos_str}"
        )

    async def _handle_command_token(self, token: str, room: str) -> None:
        """Verarbeite ein Command-Token (Wake-Word mit ``_``-Prefix) für diesen Raum.

        Per-Target — nur das Audio dieses FreeEcho.2 wird beeinflusst, andere
        Streams (anderer FreeEcho.2, Browser, lokal) laufen weiter.

        Server-Reaktion ist für ``_stop``, ``_pause``, ``_standby`` identisch:
        laufende Pipeline canceln + aktiven Stream stoppen. Der Unterschied
        liegt FreeEcho.2-lokal (Soft-Mute, LED, Quittungston). Position-Save
        passiert immer in ``_cleanup_unlocked`` vor dem mpv-Terminate, somit
        wirkt ``_pause`` automatisch via Smart-Resume.

        ``_resume`` cancelt zuerst eine eventuell hängende Pipeline (no-op
        wenn nichts läuft) bevor der neue Stream gestartet wird — sonst
        könnte eine alte LLM-Antwort den frisch gestarteten Resume-Stream
        überschreiben.

        Tokens:
        - ``_stop``     → Pipeline canceln + Stream stoppen
        - ``_pause``    → Pipeline canceln + Stream stoppen (Position wird gespeichert)
        - ``_standby``  → Pipeline canceln + Stream stoppen (FreeEcho.2-lokal Soft-Mute)
        - ``_resume``   → Pipeline canceln + Smart-Resume des letzten unfinished Items
        - ``_activate`` → no-op am Server (Soft-Mute aus, FreeEcho.2-lokal)
        """
        if token == "_done":
            # Puck meldet: proaktive Wiedergabe (Chime + TTS) komplett durch.
            # Weckt den Alarm-Worker für das nächste Queue-Item. KEIN
            # Cancel/Stop — das war ein natürliches Ende, kein Abbruch.
            signal_playback_done(room)
            self.channel_log(f"✅ [FreeEcho.2 {room}] _done: playback complete")

        elif token in ("_stop", "_pause", "_standby"):
            await self._cancel_pipeline_and_stop_stream(token, room)

        elif token == "_resume":
            # Smart-Resume: finde letztes unfinished Item, lade es mit
            # Pre-Roll auf diesen FreeEcho.2. Funktioniert in zwei Szenarien:
            #   a) gerade per _pause gestoppt → letzter Stream lädt neu
            #   b) Hörbuch lief vor Stunden, Server-Restart, etc. →
            #      audio_state kennt den letzten Key, alles wird neu gebaut
            cancelled = self._cancel_pipeline_for_room(room)
            cancel_info = "pipeline cancelled" if cancelled else "no pipeline running"
            self.channel_log(
                f"▶️ [FreeEcho.2 {room}] _resume: {cancel_info} — "
                f"loading last unfinished audio"
            )
            await self._smart_resume_on_freeecho2(room)

        elif token == "_activate":
            # No-op am Server — Soft-Mute aus ist FreeEcho.2-lokal. Kein Auto-
            # Resume; wenn der User wieder Audio will, sagt er es.
            self.channel_log(
                f"💡 [FreeEcho.2 {room}] _activate: ack "
                f"(freeecho2-local soft-mute off)"
            )

        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] unknown command token: {token}",
                "warning",
            )

    def _override_position_with_consumed_ms(
        self,
        room: str,
        consumed_ms: Any,
        stream_offset_sec: float | None = None,
    ) -> None:
        """Hoehrposition vom Puck in audio_state schreiben.

        Der Puck trackt ``consumed_frames_since_current_stream_start`` —
        nicht absolut. Bei einem Resume (Stream wurde mit start_pos>0
        gestartet) muss der Server-seitig bekannte Offset addiert werden:
        ``track_pos = stream_offset_sec + consumed_ms/1000``.

        ``stream_offset_sec`` kommt aus dem aktiven FreeEcho2Stream
        (Snapshot vor Stream-Stop). None bei Wake nach Standby/idle —
        dann fallback auf reines consumed_ms (was bei nicht-laufenden
        Streams typisch 0 ist und keine sinnvolle Update-Quelle).

        Schreibt den jüngsten unfinished audio_state-Eintrag —
        ``last_played_key()`` ist der zuletzt aktive Stream.
        """
        if consumed_ms is None:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] wake without consumed_ms "
                f"(no active stream — likely from standby), "
                f"position not updated",
            )
            return
        try:
            ms = int(consumed_ms)
        except (TypeError, ValueError):
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] consumed_ms invalid: {consumed_ms!r}",
                "warning",
            )
            return
        if ms < 0:
            return

        # Ohne aktiven Stream beim Snapshot ist consumed_ms (= seit
        # letztem audio_start) NICHT in eine absolute Track-Position
        # konvertierbar. Beispiel: Wake-Resume nach Pause — Puck schickt
        # noch den letzten consumed_ms vom alten Stream mit, aber wir
        # haben keinen Offset mehr. Saved Position aus audio_state ist
        # in diesem Fall authoritative — nicht ueberschreiben.
        if stream_offset_sec is None:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] consumed_ms={ms} ignored "
                f"(no active stream — saved position remains authoritative)"
            )
            return

        from ....lib.audio_state import audio_state
        key = audio_state.last_played_key()
        if not key:
            return  # nichts zu überschreiben — kein unfinished item

        entry = audio_state.get(key)
        if not entry:
            return
        offset = float(stream_offset_sec)
        pos_sec = offset + ms / 1000.0
        audio_state.update(
            key=key,
            uri=str(entry.get("uri", "")),
            pos_sec=pos_sec,
            duration_sec=entry.get("duration_sec"),
        )
        offset_info = (
            f" (offset {format_number(offset, 1)} s + "
            f"consumed {format_number(ms / 1000.0, 1)} s)"
            if offset > 0 else ""
        )
        self.channel_log(
            f"💾 [FreeEcho.2 {room}] saved position "
            f"{self._fmt_consumed(int(pos_sec * 1000))}{offset_info} "
            f"→ audio_state[{key}]"
        )



    def _cancel_pipeline_for_room(self, room: str) -> bool:
        """SSOT: cancele eine laufende LLM/TTS-Pipeline für die Session
        dieses FreeEcho.2. Idempotent — gibt False zurück wenn keine Route
        registriert ist oder keine Pipeline läuft.
        """
        from ....lib.pipeline_registry import cancel_pipeline
        from ....lib.routing_table import routing_table

        route = routing_table.get_route("freeecho2", room)
        if route is None:
            return False
        return bool(cancel_pipeline(route.session_id))

    async def _cancel_pipeline_and_stop_stream(self, token: str, room: str) -> None:
        """SSOT für ``_stop`` / ``_pause`` / ``_standby``: laufende Pipeline
        canceln (LLM-Stream, Tool-Calls, TTS-Generation, Chunk-Loop) und
        aktiven mpv-Stream auf diesem FreeEcho.2 stoppen.

        ``token`` dient nur dem Logging — die Reaktion ist identisch.
        """
        from ....lib import audio_channels

        target_id = f"freeecho2:{room}"
        # Erst direkt die _handle_audio-Task canceln. Das deckt die Phase
        # vor process_inbound ab (STT, TTS-Engine-Setup), in der
        # cancel_pipeline_for_room nichts findet weil pipeline_scope noch
        # nicht aktiv ist. CancelledError propagiert durch alle await's
        # bis ins finally-Cleanup von _handle_audio.
        task = _pipeline_tasks.get(room)
        task_cancelled = False
        if task is not None and not task.done():
            task.cancel()
            task_cancelled = True
        cancelled = self._cancel_pipeline_for_room(room) or task_cancelled

        stopped = False
        channel = audio_channels.resolve(target_id)
        if channel is not None:
            stopped = await channel.stop(target_id)
        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] no channel resolves '{target_id}' — "
                f"{token} stream-stop skipped",
                "warning",
            )

        # Aufbereitete Status-Zusammenfassung — was wurde tatsaechlich
        # ausgeloest. Beide false = nothing was running (idempotent stop).
        parts = []
        if cancelled:
            parts.append("pipeline cancelled")
        if stopped:
            parts.append("stream stopped")
        status = ", ".join(parts) if parts else "nothing to do (idle)"
        self.channel_log(
            f"✓ [FreeEcho.2 {room}] {token} done: {status}"
        )

    async def _smart_resume_on_freeecho2(self, room: str) -> None:
        """_resume-Handler: lade das letzte unfinished Audio auf diesen FreeEcho.2.

        Kein-State (Server-Restart) freundlich: greift auf audio_state.json
        zurück, nicht auf Live-Channel-State. Wenn nichts unfinished ist,
        wird das nur geloggt — der User hört nichts, aber das ist erwartet.
        """
        from ....lib import audio_channels
        from ....lib.audio_sources import SourceResolver, build_source_map
        from ....lib.audio_state import audio_state
        from ....lib.config import MEDIA_AUDIO_DIR

        target_id = f"freeecho2:{room}"
        channel = audio_channels.resolve(target_id)
        if channel is None:
            return

        key = audio_state.last_played_key()
        if not key:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] _resume: no unfinished audio in state",
            )
            return

        entry = audio_state.get(key)
        if not entry:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume: no entry for key={key}",
                "warning",
            )
            return

        saved_pos = float(entry.get("pos_sec", 0))
        duration = entry.get("duration_sec")

        # Source-Map aus den audio_player-Plugin-Settings nachbauen.
        # Wir greifen direkt auf die Settings zu, weil hier kein Tool-
        # Context vorhanden ist.
        try:
            from ...tools.audio_player import _load_settings
            settings = _load_settings()
            streams = {
                lbl: src for lbl, src in settings.get("sources", {}).items()
                if src.get("type") == "http_stream"
            }
            sources = build_source_map(MEDIA_AUDIO_DIR, streams)
            resolver = SourceResolver(sources)
            src = resolver.resolve(key)
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume: cannot resolve key={key}: {exc}",
                "warning",
            )
            return

        # Pre-Roll wie bei audio_resume — kürzer hier (3 s statt 7),
        # weil der User aktiv ein Wake-Wort gesagt hat und nicht von
        # einem Cold-Start kommt.
        pre_roll = 3.0
        apply_pre_roll = (
            pre_roll > 0
            and not src.is_stream
            and (duration is None or duration >= 60)
        )
        start_pos = max(0.0, saved_pos - pre_roll) if apply_pre_roll else saved_pos

        # Synthetischer PluginContext für den Channel — kein State,
        # weil das nicht aus einem Reflex-Tab kommt.
        from ....lib.plugin_base import PluginContext
        ctx = PluginContext(
            agent_id="aifred", lang="de",
            session_id="", source="freeecho2",
            metadata={"room": room},
        )
        result = await channel.play(src, target_id, start_pos, ctx)
        if result.get("success"):
            pre_roll_used = saved_pos - start_pos
            self.channel_log(
                f"▶️ [FreeEcho.2 {room}] _resume: started {key} "
                f"@ {format_number(start_pos, 1)} s "
                f"(pre-roll {format_number(pre_roll_used, 1)} s)"
            )
        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume failed: {result.get('error')}",
                "warning",
            )

    async def _handle_audio(self, ws: WebSocketResponse, audio_data: bytes, room: str) -> None:
        """Handle binary audio from FreeEcho.2 device.

        Audio is raw PCM: 16kHz, mono, int16 (little-endian).
        """
        from ....lib.envelope import InboundMessage
        from ....lib.message_processor import process_inbound

        import time as _fe2_time
        _fe2_t0 = _fe2_time.monotonic()

        num_samples = len(audio_data) // 2
        duration = num_samples / 16000.0
        audio_kb = len(audio_data) / 1024
        self.channel_log(
            f"📨 [FreeEcho.2 {room}] audio received: "
            f"{format_number(duration, 1)} s, "
            f"{format_number(audio_kb, 0)} KB ({num_samples} samples)"
        )

        # Resolve wake-word hint from the preceding "wake" event. Agent IDs with
        # a leading underscore are command tokens (e.g. "_stop") — handle those
        # here and skip the full STT/LLM/TTS pipeline. The FreeEcho.2 stops its own
        # playback locally on wake detection (like Action-Button), so no reply
        # is sent back. Concrete server-side command handlers (abort running
        # inference, cancel injected music/TTS, …) are TODO.
        wake_agent = _pending_wake_agent.pop(room, None)
        if wake_agent and wake_agent.startswith("_"):
            self.channel_log(f"[FreeEcho.2 {room}] Command '{wake_agent}' received — pipeline skipped (stub)")
            return

        # Convert raw PCM to WAV for STT
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data)
        wav_bytes = wav_buffer.getvalue()

        # Resolve session IMMEDIATELY so all debug messages (STT, TTS loading,
        # model switching) reach the browser UI via session_scope.
        from ....lib.routing_table import routing_table
        from ....lib.session_storage import create_empty_session
        from ....lib.config import MESSAGE_HUB_OWNER
        from ....lib.debug_bus import debug, session_scope
        from ....lib.message_processor import hub_notification_scope
        import secrets as _secrets

        route = routing_table.get_route("freeecho2", room)
        if route:
            session_id = route.session_id
        else:
            session_id = _secrets.token_hex(16)
            create_empty_session(session_id, owner=MESSAGE_HUB_OWNER)
            routing_table.set_route("freeecho2", room, session_id)

        # Heartbeat task — sends heartbeat every 5s while processing. The device
        # counts every silent ws_recv_timeout (200 ms) as a miss and gives up
        # after heartbeat_misses_max (75) * 200 ms = 15 s without ANY frame. STT
        # plus a cold model load can exceed that, so this lifeline must tick the
        # whole time. A send error is logged (not swallowed) so a broken socket
        # surfaces instead of the device silently timing out.
        heartbeat_running = True

        async def _heartbeat():
            while heartbeat_running:
                try:
                    await ws.send_str(json.dumps({"type": "heartbeat"}))
                except Exception as exc:  # noqa: BLE001
                    self.channel_log(
                        f"[FreeEcho.2 {room}] heartbeat send failed: "
                        f"{type(exc).__name__}: {exc}", "warning",
                    )
                    break
                await asyncio.sleep(5)

        # Track GPU TTS engines this pipeline acquires so the finally block
        # always releases them — even if the browser (or another channel)
        # tries to stop the engine mid-pipeline. Refcount > 0 makes
        # ensure_tts_state() defer the stop until our pipeline finishes.
        # The keep-alive task pings /keep_alive every 5 min so the container
        # idle-timer doesn't trigger during long inference / web research.
        acquired_tts_engines: list[str] = []
        tts_keepalive_task: Optional[asyncio.Task] = None
        heartbeat_task: Optional[asyncio.Task] = None
        wav_path: Optional[str] = None

        try:
            # Write the temp WAV INSIDE the try so the finally always unlinks it.
            # (Session resolution above can raise; creating the file earlier would
            # leak it on that path.)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as f:
                f.write(wav_bytes)
                wav_path = f.name
            self.channel_log(f"[FreeEcho.2 {room}] WAV prepared ({_fe2_time.monotonic()-_fe2_t0:.2f}s)")
            # session_scope routes debug() messages to this session's UI.
            # hub_notification_scope owns the toast lifecycle: writes "received"
            # on entry, "error" on any exception leaving the block, and "done"
            # on a clean return — covers the STT-empty path and any failure
            # in the TTS-setup phase. We hand off to process_inbound's own
            # scope via .delegate() before calling it (otherwise we'd write
            # a brief "done" between our scope ending and process_inbound's
            # scope opening).
            with session_scope(session_id), hub_notification_scope(
                session_id, f"FreeEcho.2 {room}", "FreeEcho.2", room,
            ) as hub:
                debug(f"📨 FreeEcho.2: Audio from {room} ({duration:.1f}s)")
                debug("🎤 STT running...")

                # Start the heartbeat BEFORE STT so the device's lifeline ticks
                # from the moment we own the audio — covers a cold Whisper run
                # AND the cold model load that follows. Send one "processing"
                # frame first so the device immediately knows the server works.
                await ws.send_str(json.dumps({"type": "processing"}))
                heartbeat_task = asyncio.create_task(_heartbeat())

                # Run STT
                text = await self._run_stt(wav_path)
                if not text:
                    self.channel_log(f"[FreeEcho.2 {room}] STT returned empty text", "warning")
                    debug("❌ STT: no text recognized")
                    await self.send_done(room, reason="stt_empty")
                    return  # hub scope writes "done" on exit → toast closes after 5 s

                self.channel_log(f"[FreeEcho.2 {room}] STT ({_fe2_time.monotonic()-_fe2_t0:.1f}s): {text}")

                # Flush user question to session immediately so browser shows it
                # BEFORE TTS setup (which can take 25s+) and LLM inference.
                # Uses the same SSOT function as process_inbound.
                from ....lib.message_processor import save_user_to_session
                _early_msg = InboundMessage(
                    channel="freeecho2", channel_id=room, sender=room,
                    text=text, timestamp=datetime.now(timezone.utc),
                    metadata={"room": room},
                )
                save_user_to_session(session_id, _early_msg)

                # Ensure TTS state (MOSS/XTTS loading, VRAM management).
                # Messages go to UI via debug() (session context propagated to executor).
                hub.update("processing")
                tts_deferred = await self._ensure_tts_state()

                # Acquire the active GPU TTS engine for the duration of this
                # pipeline so concurrent channels can't stop it mid-flight.
                from ....lib.tts_engine_manager import (
                    _detect_running_tts_engine, acquire_tts,
                )
                _active_engine = _detect_running_tts_engine()
                if _active_engine:
                    acquire_tts(_active_engine)
                    acquired_tts_engines.append(_active_engine)
                    # Start TTS keep-alive ping while pipeline runs.
                    from ....lib.tts_engine_manager import tts_keepalive_loop
                    tts_keepalive_task = asyncio.create_task(
                        tts_keepalive_loop(
                            acquired_tts_engines,
                            on_warn=lambda m: self.channel_log(
                                f"[FreeEcho.2 {room}] {m}", "warning"
                            ),
                        )
                    )

                self.channel_log(f"[FreeEcho.2 {room}] → process_inbound ({_fe2_time.monotonic()-_fe2_t0:.1f}s)")
                # Hand off the notification lifecycle to process_inbound's own
                # hub_notification_scope — its received → processing → done is
                # the user-visible progress now. Without delegate() our scope
                # would briefly write "done" between leaving this block and
                # process_inbound's scope opening, causing a toast flicker.
                hub.delegate()

            # Create inbound message and process through AIfred engine
            # (process_inbound creates its own session_scope)
            # wake_agent was resolved at the top of this handler.
            inbound = InboundMessage(
                channel="freeecho2",
                channel_id=room,
                sender=room,
                text=text,
                timestamp=datetime.now(timezone.utc),
                metadata={
                    "wav_path": wav_path,
                    "room": room,
                    "tts_deferred": tts_deferred,
                    "wake_agent": wake_agent,
                },
            )

            _devices[room] = ws

            # process_inbound calls send_reply automatically (via auto_reply)
            # User question already flushed to session above (early browser update)
            outbound = await process_inbound(inbound, user_saved=True)

            # Kalibrier-Gate: process_inbound hat abgelehnt (GPUs gehören der
            # Messung). Kein TTS möglich/erwünscht — stattdessen den LOKALEN
            # Notification-Sound des Pucks triggern (audio_flag ohne TTS),
            # damit der User hört, dass der Service gerade nicht kann,
            # statt vor einem stummen Puck zu stehen.
            if outbound is not None and outbound.metadata.get("calibration_gate"):
                try:
                    from ....lib import audio_channels
                    ch = audio_channels.resolve(f"freeecho2:{room}")
                    orc = (
                        ch.get_orchestrator(room)
                        if ch is not None and hasattr(ch, "get_orchestrator")
                        else None
                    )
                    if orc is not None:
                        await orc.play_notification(with_tts=False)
                except Exception as e:  # noqa: BLE001
                    self.channel_log(
                        f"[FreeEcho.2 {room}] calibration-gate notification failed: {e}",
                        "warning",
                    )

            total_time = _fe2_time.monotonic() - _fe2_t0
            self.channel_log(f"[FreeEcho.2 {room}] ← Pipeline complete ({total_time:.1f}s)")

            # Signal client: all done, go back to IDLE
            await self.send_done(room)

        except Exception as e:
            self.channel_log(f"[FreeEcho.2 {room}] Pipeline error: {e}", "error")
            try:
                await self.send_done(room, reason="error")
            except Exception:
                pass
        finally:
            # Stop the heartbeat — single source of truth for ALL exit paths
            # (empty-STT early return, clean finish, exception). Setting the
            # flag stops the loop; cancel() ends the pending asyncio.sleep(5).
            heartbeat_running = False
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
            # Stop the TTS keep-alive task before releasing engine refcount.
            if tts_keepalive_task is not None and not tts_keepalive_task.done():
                tts_keepalive_task.cancel()
            # Always release any TTS engine acquisitions — survives crashes
            # and external cancellations (e.g. Action-Button stop event).
            if acquired_tts_engines:
                from ....lib.tts_engine_manager import release_tts
                for _engine in acquired_tts_engines:
                    release_tts(_engine)
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

    async def _run_stt(self, wav_path: str) -> str:
        """Run Speech-to-Text via Whisper Docker service."""
        from ....lib.audio_processing import transcribe_audio

        loop = asyncio.get_event_loop()
        text, stt_time = await loop.run_in_executor(
            None, transcribe_audio, wav_path, "de", "cpu", False,
        )
        return text or ""

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send TTS audio back to the FreeEcho.2 device.

        Geht ueber den AudioOrchestrator des FreeEcho2Channels. Der
        macht alles in einem Aufruf: TTS-Takeover bei laufender Music
        (mpv-pause + audio_flag(tts) + pump + audio_flag(music) +
        mpv-resume) oder TTS-standalone. Kein eigenes Pause/Resume-
        Handling mehr — der Orchestrator ist Single-Source-of-Truth
        fuer Audio-State pro Room.
        """
        room = outbound.channel_id
        ws = _devices.get(room)
        if not ws:
            self.channel_log(f"[FreeEcho.2 {room}] No connected device for reply", "warning")
            return

        # silent_reply: bei erfolgreichem Audio-Tool (audio_play/folder/
        # resume) skippt der Channel die TTS-Bestaetigung. Music laeuft
        # direkt los, kein "DJ labert in den Song". Reply-Text ist
        # bereits in der Session gespeichert (Browser-UI sichtbar).
        if outbound.metadata.get("silent_reply"):
            self.channel_log(
                f"[FreeEcho.2 {room}] silent_reply — TTS-Bestaetigung skipped"
            )
            return

        # If TTS was deferred (LLM was loaded without TTS, used for fast inference),
        # now switch: unload LLM → load TTS engine → restart LLM with TTS profile.
        # Must happen BEFORE _run_tts() which needs the TTS engine running.
        if original and original.metadata.get("tts_deferred"):
            # Vorab-Check: passt das gewünschte GPU-TTS überhaupt zum aktiven
            # LLM? Bei einem GPU-füllenden Modell (z.B. dem 397B) steht die
            # TTS-Kombo im vram-cache auf FAIL — ein Switch würde das LLM
            # verdrängen, das Base-Profil neu laden und das TTS TROTZDEM ohne
            # VRAM lassen ("produced no audio"). Statt 20s blind zu thrashen:
            # erkennen, klar melden, Ansage überspringen.
            if not self._gpu_tts_combo_fits():
                self.channel_log(
                    f"[FreeEcho.2 {room}] GPU-TTS '{self._get_wanted_tts()}' has "
                    f"no calibrated variant for the active model — skipping voice "
                    f"output (a switch would evict the LLM and still fail for lack "
                    f"of VRAM). Use a cloud TTS (DashScope) with large models.",
                    "error",
                )
                return
            self.channel_log(f"[FreeEcho.2 {room}] Deferred TTS switch starting")
            await self._force_tts_switch()

        # Proaktive Pushes (Vision-Alert, freeecho2_announce) kommen OHNE
        # vorausgegangene LLM-Inferenz → es gibt kein tts_deferred-Flag, und
        # die TTS-Engine ist evtl. gar nicht geladen (GPUs idle). Genau wie
        # bei einem echten Puck-Request den TTS-State sicherstellen und ggf.
        # den Modell-Swap (LLM → TTS) erzwingen, BEVOR _run_tts läuft — sonst
        # bleibt die Ansage stumm.
        is_proactive = (
            (original is not None and original.sender == "system")
            or bool(outbound.metadata.get("proactive"))
        )
        if is_proactive:
            await self._ensure_tts_ready(room)

        # Generate TTS audio (agent-specific voice if configured)
        agent = original.target_agent if original else "aifred"
        tts_path = await self._run_tts(outbound.text, agent=agent)
        if not tts_path:
            self.channel_log(f"[FreeEcho.2 {room}] TTS failed", "error")
            return

        try:
            pcm_data = await self._convert_to_pcm(tts_path, 48000)
            if not pcm_data:
                self.channel_log(f"[FreeEcho.2 {room}] TTS conversion failed", "error")
                return

            from ....lib import audio_channels
            ch = audio_channels.resolve(f"freeecho2:{room}")
            if ch is None or not hasattr(ch, "get_orchestrator"):
                self.channel_log(
                    f"[FreeEcho.2 {room}] FreeEcho2Channel unavailable — cannot send TTS",
                    "error",
                )
                return
            orc = ch.get_orchestrator(room)
            if orc is None:
                self.channel_log(
                    f"[FreeEcho.2 {room}] orchestrator unavailable", "error",
                )
                return

            # Proaktive Push-Nachricht? Erkannt am dummy-inbound
            # ``sender == "system"`` aus message_processor.announce_to_channel.
            # In dem Fall: lokaler Chime vor dem TTS, damit der User nicht
            # aus dem Nichts angesprochen wird. Welcher Chime — alarm_wav
            # (auffaellig) oder notification_wav (sanft) — kommt per
            # metadata.audio_type aus dem Caller (alert_bus mappt severity →
            # audio_type; explizite scheduler-Sends koennen es selbst setzen).
            # Default "notification" wenn unklar.
            # Frame-Sequenz: audio_flag(alarm|notification, with_tts=True) +
            # audio_flag(tts) + audio_start + chunks + audio_end — der
            # Orchestrator macht alles in einem Aufruf.
            # Normal-Reply (User hat selbst getriggert) bleibt ohne Chime.
            # is_proactive ist oben schon bestimmt (TTS-State-Sicherstellung).
            secs = format_number(len(pcm_data) / 96000, 1)
            if is_proactive:
                audio_type = str(
                    outbound.metadata.get("audio_type") or "notification"
                )
                if audio_type not in ("alarm", "notification"):
                    self.channel_log(
                        f"[FreeEcho.2 {room}] unknown audio_type "
                        f"{audio_type!r} — falling back to notification",
                        "warning",
                    )
                    audio_type = "notification"
                self.channel_log(
                    f"[FreeEcho.2 {room}] Proactive push ({audio_type}): "
                    f"chime + TTS {_fmt_mib(len(pcm_data))} ({secs}s) "
                    f"→ alert queue"
                )
                # In die room-Queue legen statt direkt abspielen: der Worker
                # serialisiert (ein Alarm nach dem anderen, je nach _done),
                # und der Emit-Pfad (Vision-Watcher) wird NICHT blockiert.
                await enqueue_alert(room, audio_type, pcm_data)
            else:
                self.channel_log(
                    f"[FreeEcho.2 {room}] Sending TTS: {_fmt_mib(len(pcm_data))} "
                    f"({secs}s) via orchestrator"
                )
                await orc.play_tts(pcm_data)
            self.channel_log(f"[FreeEcho.2 {room}] TTS playback complete")
        finally:
            Path(tts_path).unlink(missing_ok=True)

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Stellt ``freeecho2_announce`` bereit: AIfred kann proaktiv eine
        gesprochene Ansage (Chime + TTS) auf einen oder alle verbundenen
        Pucks schicken. Läuft über denselben Queue-Pfad wie die Vision-
        Alerts (announce_to_channel → enqueue → Worker → _done)."""
        from ....lib.function_calling import Tool
        from ....lib.security import TIER_COMMUNICATE
        import json

        async def _execute_announce(
            message: str, target: str = "*", audio_type: str = "notification",
        ) -> str:
            from ....lib.message_processor import (
                announce_to_channel,
                resolve_announce_targets,
            )

            if audio_type not in ("alarm", "notification"):
                audio_type = "notification"
            rooms = resolve_announce_targets("freeecho2", target or "*")
            if not rooms:
                return json.dumps({
                    "success": False,
                    "error": "no FreeEcho.2 puck connected for target "
                             f"{target!r}",
                })
            meta = {"audio_type": audio_type, "proactive": True}
            reached = []
            for room in rooms:
                if await announce_to_channel(
                    "freeecho2", room, message, metadata=meta,
                ):
                    reached.append(room)
            return json.dumps({
                "success": bool(reached),
                "audio_type": audio_type,
                "rooms": reached,
            })

        return [
            Tool(
                name="freeecho2_announce",
                tier=TIER_COMMUNICATE,
                description=(
                    load_tool_description(__file__, "freeecho2_announce")
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "What to say out loud (it is spoken via TTS).",
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Where to play. Leave as '*' (default) to "
                                "reach all connected pucks — use this unless "
                                "the user explicitly names a specific room. "
                                "A bare room name targets one puck; '@group' a "
                                "configured group. Do NOT invent room names and "
                                "do NOT prefix with 'freeecho2:'. Unknown/"
                                "disconnected rooms are rejected."
                            ),
                        },
                        "audio_type": {
                            "type": "string",
                            "enum": ["notification", "alarm"],
                            "description": (
                                "'notification' = gentle chime (info, default); "
                                "'alarm' = urgent chime + LED (stranger, "
                                "intrusion, time-critical)."
                            ),
                        },
                    },
                    "required": ["message"],
                },
                executor=_execute_announce,
            ),
        ]

    # ── Public WS-Bridge — Audio-Bus-Frame-API ──────────────────────────
    #
    # Audio-Bus-Protokoll (Phase 5.0): siehe docs/de/architecture/
    # audio-pipeline.md "Audio-Bus-Refactor" für Frame-Sequenzen und
    # Tupel-Whitelist. Vier Methoden:
    #
    #   1. send_audio_flag(room, audio_type, **params)  — Type-Setting (LED+VU)
    #   2. send_audio_start(room, total_size?)          — PCM-Stream-Setup
    #   3. send_audio_chunk(room, bytes)                — beliebig oft
    #   4. send_audio_end(room)                         — End-Marker
    #
    # audio_flag und audio_start sind GETRENNT mit unterschiedlicher
    # Semantik (audio_flag = Type-Wechsel ohne Stream-Reset). Frame-
    # Sequenzen pro Use-Case sind in der Doku tabellarisch festgehalten.
    #
    # Per-Send-Timeout: wenn der FreeEcho.2 nicht mehr ACKt (WiFi-Drop,
    # Crash), würde Linux-TCP ~2 min brauchen um das zu bemerken — wir
    # geben nach 10 s auf und schließen die Verbindung, damit der Room-
    # Slot für den Reconnect frei wird.

    _CHUNK_SEND_TIMEOUT_SEC = 10.0

    # Whitelist-Validation für audio_flag/audio_start. Schema-Verletzung
    # wird server-seitig per ValueError geblockt BEVOR sie ans Wire geht
    # — die Firmware-FATAL-Pfade sehen wir damit nur bei echter
    # Network-Korruption, nicht bei Server-Logik-Bugs. Strikt:
    # unbekannte Felder, falsche Typen, fehlende Pflicht-Felder → raise.
    _AUDIO_TYPE_SCHEMA: dict[str, set[str]] = {
        "music":        set(),          # Stereo-VU am Puck
        "speech":       set(),          # Voice-VU (Hoerbuch / Podcast / Lesung)
        "tts":          set(),          # Voice-VU (XTTS-Generator-Output)
        "alarm":        {"with_tts"},   # einmal abspielen; Server loopt
        "notification": {"with_tts"},   # einmal abspielen
    }

    @classmethod
    def _validate_audio_flag(cls, audio_type: str, params: dict[str, Any]) -> None:
        """Strikt: validate audio_flag-Tupel gegen Whitelist. Raise ValueError."""
        if audio_type not in cls._AUDIO_TYPE_SCHEMA:
            raise ValueError(
                f"audio_flag: unknown audio_type {audio_type!r} "
                f"(allowed: {sorted(cls._AUDIO_TYPE_SCHEMA.keys())})"
            )
        expected = cls._AUDIO_TYPE_SCHEMA[audio_type]
        provided = set(params.keys())
        if extra := provided - expected:
            raise ValueError(
                f"audio_flag({audio_type!r}): unexpected fields {sorted(extra)}"
            )
        if missing := expected - provided:
            raise ValueError(
                f"audio_flag({audio_type!r}): missing required fields {sorted(missing)}"
            )
        # Type-Checks pro Feld
        if "with_tts" in params:
            v = params["with_tts"]
            if not isinstance(v, bool):
                raise ValueError(
                    f"audio_flag({audio_type!r}): with_tts must be bool, got {v!r}"
                )

    async def send_audio_flag(
        self, room: str, audio_type: str, **params: Any
    ) -> bool:
        """Schickt ein audio_flag-Frame: Type-Setting (LED + VU + Source-Verhalten).

        Wird verwendet für (siehe Doku audio-pipeline.md):
        - vor audio_start bei music/tts (initial setting)
        - alleine für alarm/notification (kein PCM danach, falls with_tts=false)
        - mid-stream für Type-Switch (z.B. music → tts während Music läuft;
          gleiche Source bleibt, 30 ms Linear-Fade auf Puck-Seite)

        ``audio_type`` muss in der Whitelist sein. ``params`` sind type-
        spezifisch (siehe ``_AUDIO_TYPE_SCHEMA``). Server-side strikt
        validiert — ungültige Tupel raisen ValueError BEVOR sie ans Wire
        gehen, damit die Firmware-FATAL-Pfade nur Network-Korruption
        fangen.
        """
        # Validate strikt — raise wenn nicht konform
        self._validate_audio_flag(audio_type, params)

        ws = _devices.get(room)
        if ws is None:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag: not connected", "warning",
            )
            return False
        if ws.closed:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag: WS closed (id={id(ws)}) "
                f"— stale handle in _devices",
                "warning",
            )
            return False
        payload: dict[str, Any] = {
            "type": "audio_flag",
            "audio_type": audio_type,
            **params,
        }
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps(payload)),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            self.channel_log(
                f"[FreeEcho.2 {room}] → audio_flag({audio_type}) sent "
                f"(ws id={id(ws)})"
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_flag error: {exc}", "warning",
            )
            return False

    async def send_audio_start(
        self,
        room: str,
        total_size: int | None = None,
        channels: int = 1,
    ) -> bool:
        """Signalisiert PCM-Stream-Setup an den FreeEcho.2.

        Wird IMMER nach einem ``audio_flag(music)`` oder ``audio_flag(tts)``
        gesendet, BEVOR die binary chunks fließen. Bei alarm/notification
        ohne TTS-Tail gibt's kein audio_start (Puck spielt lokale WAV).

        Format: 48 kHz int16 little-endian, Endpoint-Constraint der
        FreeEcho.2-Hardware (Rate ist fix, nicht verhandelbar). ``rate``
        wird nicht mitgeschickt — würde nur die Firmware-Whitelist-
        Validation FATAL triggern. ``channels`` ist 1 (mono, default für
        TTS/Speech) oder 2 (Stereo, für Music — echtes L/R an
        Stereo-BT-Speakern). Der Puck akzeptiert beide via
        ``freeecho2_client.c::audio_start``-Parser.

        ``total_size`` ist optional (typischerweise für TTS verfügbar,
        nicht für endlose Music-Streams). Puck nutzt es bisher nicht für
        Logik, kann aber für künftige Progress-LED genutzt werden.
        """
        ws = _devices.get(room)
        if ws is None:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start: not connected", "warning",
            )
            return False
        if ws.closed:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start: WS closed (id={id(ws)}) "
                f"— stale handle in _devices",
                "warning",
            )
            return False
        payload: dict[str, Any] = {"type": "audio_start"}
        if channels not in (1, 2):
            raise ValueError(
                f"send_audio_start: channels must be 1 or 2, got {channels!r}"
            )
        payload["channels"] = channels
        if total_size is not None:
            payload["total_size"] = int(total_size)
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps(payload)),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            self.channel_log(
                f"[FreeEcho.2 {room}] → audio_start sent "
                f"(total_size={total_size}, ws id={id(ws)})"
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_start error: {exc}", "warning",
            )
            return False

    async def send_audio_chunk(self, room: str, data: bytes) -> bool:
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_bytes(data),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            # KEIN WS-close mehr! Beim _resume kann der Puck kurz nicht
            # receive-ready sein (Source-Replace ~1-2s), TCP-Buffer voll,
            # send hängt 10s. Ein chunk-timeout heißt nur "dieser Stream
            # ist nicht mehr durchgekommen" — fifo_pump bricht via
            # ok=False ab, WS bleibt offen für Recovery (User-Wake,
            # neuer audio_play, etc.).
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_chunk timeout "
                f"({_fmt_mib(len(data))}) — stream abort, WS bleibt offen",
                "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_chunk error: {exc}", "warning",
            )
            return False

    async def send_heartbeat(self, room: str) -> bool:
        """Heartbeat während aktivem Streaming an den FreeEcho.2 schicken.

        Wird vom FreeEcho2Stream alle 5 s aufgerufen, auch wenn die FIFO-Pump
        gerade pausiert (flow.pause / User-_pause). Liefert False bei
        Send-Timeout — dann ist der FreeEcho.2 nicht mehr erreichbar und der
        Stream räumt sich selbst auf.
        """
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps({"type": "heartbeat"})),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            return False
        except Exception:  # noqa: BLE001
            return False

    async def send_audio_end(self, room: str) -> bool:
        ws = _devices.get(room)
        if ws is None:
            return False
        try:
            await asyncio.wait_for(
                ws.send_str(json.dumps({"type": "audio_end"})),
                timeout=self._CHUNK_SEND_TIMEOUT_SEC,
            )
            return True
        except asyncio.TimeoutError:
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_end timeout — stream abort, "
                f"WS bleibt offen", "warning",
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_audio_end error: {exc}", "warning",
            )
            return False

    async def send_done(self, room: str, reason: str | None = None) -> bool:
        """SSoT for the ``done`` frame — the canonical turn boundary.

        ``done`` tells the puck the whole turn is finished: go back to IDLE
        and reply with the ``_done`` token. Both the reactive reply pipeline
        and the proactive alert queue send it after their audio_end so the
        terminal contract is symmetric (audio_end + done in every path)."""
        ws = _devices.get(room)
        if ws is None:
            return False
        payload: dict[str, str] = {"type": "done"}
        if reason is not None:
            payload["reason"] = reason
        try:
            await ws.send_str(json.dumps(payload))
            self.channel_log(f"[FreeEcho.2 {room}] → done sent (reason={reason})")
            return True
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"[FreeEcho.2 {room}] send_done error: {exc}", "warning",
            )
            return False

    def _get_wanted_tts(self) -> str:
        """Get the TTS engine this plugin wants."""
        from ....lib.credential_broker import broker
        return broker.get("freeecho2", "tts_engine") or "piper"

    def _get_backend_type(self) -> str:
        """Get the current LLM backend type."""
        from ....state._base import _global_backend_state
        return _global_backend_state.get("backend_type") or "llamacpp"

    def _gpu_tts_combo_fits(self) -> bool:
        """True if the wanted GPU-TTS engine actually fits alongside the active
        LLM — i.e. the model has a calibrated TTS variant in the vram cache.

        Lightweight/cloud engines (Edge/Piper/DashScope) need no GPU, so they
        always fit. Returns True optimistically when the model id is unknown
        (don't block on missing info).

        Guards the deferred TTS switch: for a GPU-filling model (e.g. the 397B)
        the TTS combo is FAIL in the cache; switching anyway would evict the
        LLM, reload its base profile and still leave the TTS without VRAM
        ("produced no audio"). Better to detect + skip than to thrash for 20s.
        """
        from ....lib.tts_engine_manager import GPU_ENGINES
        wanted = self._get_wanted_tts()
        if wanted not in GPU_ENGINES:
            return True
        from ....lib.settings import load_settings
        from ....lib.model_vram_cache import is_tts_variant_calibrated
        settings = load_settings() or {}
        backend = self._get_backend_type()
        model_id = str(
            settings.get("backend_models", {}).get(backend, {}).get("aifred_model", "")
        )
        if not model_id:
            return True
        return is_tts_variant_calibrated(model_id, wanted)

    async def _ensure_tts_state(self) -> bool:
        """Ensure TTS state before LLM inference (SSOT: ensure_tts_state).

        Returns True if deferred (LLM loaded, caller should inferize first).
        Returns False if TTS is ready and LLM will load with correct profile.
        """
        from ....lib.tts_engine_manager import ensure_tts_state, GPU_ENGINES
        from ....lib.debug_bus import debug, _current_session

        wanted = self._get_wanted_tts()
        # Map lightweight engines to "" (no GPU TTS needed).
        # The SSOT still needs to run: if a GPU TTS container is in VRAM
        # but we switched to Edge/Piper/eSpeak, it must be cleaned up.
        wanted_gpu = wanted if wanted in GPU_ENGINES else ""

        backend_type = self._get_backend_type()

        # Capture session_id from the calling coroutine's context
        # so debug() in the executor thread can route to the session.
        caller_session_id = _current_session.get()

        def _run() -> bool:
            # Propagate session context into executor thread
            token = _current_session.set(caller_session_id) if caller_session_id else None
            try:
                gen = ensure_tts_state(
                    wanted_tts=wanted_gpu,
                    backend_type=backend_type,
                    check_defer=True,
                )
                deferred = False
                try:
                    while True:
                        msg = next(gen)
                        debug(f"🔊 {msg}")
                except StopIteration as e:
                    if e.value:
                        deferred = e.value.deferred
                return deferred
            finally:
                if token is not None:
                    _current_session.reset(token)

        return await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _force_tts_switch(self) -> None:
        """Force TTS switch after deferred inference (FreeEcho.2 optimization).

        Called after LLM used existing model. Now: switch TTS, then
        restart LLM with TTS-calibrated profile. All blocking, sequential.
        """
        from ....lib.tts_engine_manager import force_tts_switch, GPU_ENGINES
        from ....lib.debug_bus import debug, _current_session

        wanted = self._get_wanted_tts()
        # Map lightweight engines to "" — force_tts_switch needs GPU key or ""
        wanted_gpu = wanted if wanted in GPU_ENGINES else ""
        backend_type = self._get_backend_type()
        caller_session_id = _current_session.get()

        def _run() -> None:
            token = _current_session.set(caller_session_id) if caller_session_id else None
            try:
                gen = force_tts_switch(wanted_gpu, backend_type)
                try:
                    while True:
                        msg = next(gen)
                        debug(f"🔊 {msg}")
                except StopIteration:
                    pass
            finally:
                if token is not None:
                    _current_session.reset(token)

        await asyncio.get_event_loop().run_in_executor(None, _run)

    async def _ensure_tts_ready(self, room: str) -> None:
        """SSoT für 'TTS-Engine jetzt synchron bereitstellen' — für proaktive
        Pushes (Vision-Alert, freeecho2_announce), die OHNE vorausgehende
        LLM-Inferenz kommen.

        Nutzt dieselben Primitive wie der Chat-Flow (``_ensure_tts_state`` +
        ``_force_tts_switch``), nur ohne Inferenz dazwischen: TTS-State
        sicherstellen, und falls das große LLM die GPU blockiert (deferred),
        den Modell-Swap LLM → TTS sofort erzwingen. Identisches Verhalten wie
        bei einem echten Puck-Request, nur ohne Antwort-Generierung."""
        deferred = await self._ensure_tts_state()
        if deferred:
            self.channel_log(
                f"[FreeEcho.2 {room}] Proactive TTS switch "
                f"(no prior inference — loading TTS engine)"
            )
            await self._force_tts_switch()

    async def _run_tts(self, text: str, agent: str = "aifred") -> str | None:
        """Generate TTS audio file from text. Returns absolute file path.

        Uses the FreeEcho.2 plugin's TTS engine setting combined with
        per-agent voice configuration from agents.json (``tts_voices``
        block). Independent of the browser UI TTS toggle.

        TTS container readiness is ensured by _ensure_tts_state() / _force_tts_switch()
        BEFORE this method is called. No VRAM management here.
        """
        from ....lib.credential_broker import broker
        from ....lib.config import PROJECT_ROOT
        from ....lib.agent_config import get_tts_voice_default
        from ....lib.settings import load_settings

        # Engine from plugin settings
        engine = broker.get("freeecho2", "tts_engine") or "piper"

        # Voice priority: 1. User settings (per engine+agent), 2. Defaults, 3. Fallback.
        # Fall back to "aifred" (the system's default agent) when the
        # requested agent has neither user setting nor default — keeps the
        # historical behaviour where custom agents inherited aifred's voice.
        settings = load_settings() or {}
        user_voices = settings.get("tts_agent_voices_per_engine", {}).get(engine, {})
        user_cfg = user_voices.get(agent) or user_voices.get("aifred", {})
        default_cfg = get_tts_voice_default(agent, engine)
        if not default_cfg.get("voice"):
            default_cfg = get_tts_voice_default("aifred", engine)

        # User setting wins, then default, then hardcoded fallback
        voice = ""
        if isinstance(user_cfg, dict):
            voice = str(user_cfg.get("voice", ""))
        elif isinstance(user_cfg, str):
            voice = user_cfg
        if not voice:
            from ....lib.config import PUCK_TTS_FALLBACK_VOICE
            voice = str(default_cfg.get("voice", PUCK_TTS_FALLBACK_VOICE))

        speed_str = str(default_cfg.get("speed", "1.0"))
        if isinstance(user_cfg, dict) and user_cfg.get("speed"):
            speed_str = str(user_cfg["speed"])
        speed = float(speed_str.replace("x", ""))

        pitch_str = str(default_cfg.get("pitch", "1.0"))
        if isinstance(user_cfg, dict) and user_cfg.get("pitch"):
            pitch_str = str(user_cfg["pitch"])
        pitch = float(pitch_str)

        try:
            from ....lib.audio_processing import generate_tts
            result: str | None = await generate_tts(text, voice, speed, engine, pitch=pitch, agent=agent)
            if not result:
                # No fallback to another engine (project rule) — the device
                # stays silent. The engine already logged the specific cause
                # (e.g. network/internet unreachable) to debug.log; this line
                # makes the failure visible in the session debug console.
                self.channel_log(
                    f"TTS engine '{engine}' produced no audio — device stays "
                    f"silent (no fallback by design)", "error",
                )
                return None
            # Convert URL path (/_upload/tts_audio/xxx.wav) to absolute file path
            if result.startswith("/_upload/"):
                return str(PROJECT_ROOT / "data" / result.removeprefix("/_upload/"))
            return result
        except Exception as e:
            self.channel_log(f"TTS ({engine}) failed: {e}", "error")
            return None

    async def _convert_to_pcm(self, audio_path: str, target_rate: int) -> bytes | None:
        """Convert audio file to raw PCM (mono, int16, target_rate)."""
        # Use ffmpeg to convert any audio format to raw PCM
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ar", str(target_rate),
            "-ac", "1",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "pipe:1",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout
            self.channel_log(f"ffmpeg error: {stderr.decode()[:200]}", "error")
        except FileNotFoundError:
            self.channel_log("ffmpeg not found", "error")
        return None

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Format message for LLM context."""
        room = message.metadata.get("room", "unknown")
        return (
            f"Sprachnachricht von FreeEcho.2 Gerät im Raum '{room}'. "
            f"Der User hat gesprochen und die Sprache wurde per STT transkribiert. "
            f"Antworte kurz und prägnant — die Antwort wird per TTS vorgelesen."
        )


# Module-level singleton — auto-discovered by plugin registry
FreeEchoChannel_instance = FreeEchoChannel()
