"""PuckStream — eine mpv-Decoder-Pipeline pro Puck-Target.

Pro aktivem ``puck:<room>``-Target läuft ein eigener mpv-Subprozess, der die
Audio-Quelle (lokale Datei oder HTTP-Stream) auf 48 kHz mono int16 PCM
resampled und in eine named FIFO schreibt. Eine Reader-Coroutine liest die
FIFO chunkweise und gibt die PCM-Bytes an die FreeEcho2-Channel-Bridge,
die sie als Binary-Frames an den Puck-WebSocket sendet.

Steuerung (pause/resume/seek/stop) läuft über einen mpv-IPC-Socket
(Unix-Domain) — ein Socket pro Stream. Position-Save geht (wie beim
LocalChannel) in ``audio_state.json`` über das gemeinsame ``audio_state``-
Modul, getriggert von einem Save-Loop pro Stream.

Lifecycle:
    PuckStream(room).start(uri, state_key, start_pos_sec)
        → mpv läuft, Reader-Task pumpt Audio an den Puck.
    .pause() / .resume() / .seek(...)
        → IPC-Commands.
    .stop()
        → mpv terminate, Reader cancellen, FIFO/Socket aufräumen,
          audio_end-Frame an den Puck.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..config import DATA_DIR
from ..logging_utils import log_message

MPV_BINARY = "/usr/bin/mpv"
SOCKET_WAIT_TIMEOUT_SEC = 5.0
COMMAND_TIMEOUT_SEC = 5.0
READ_CHUNK_SIZE = 64 * 1024   # ~666ms @ 48kHz mono int16 — gut für Latenz
DEFAULT_SAVE_INTERVAL_SEC = 60

PUCK_SAMPLE_RATE = 48000
PUCK_CHANNELS = 1
PUCK_SAMPLE_FORMAT = "s16"   # mpv-Notation; ergibt int16 little-endian


# Type alias: WS-Bridge in freeecho2_channel hat diese Form.
SendChunk = Callable[[str, bytes], Awaitable[bool]]
SendStart = Callable[[str, int, int, str], Awaitable[bool]]
SendEnd = Callable[[str], Awaitable[bool]]
SendHeartbeat = Callable[[str], Awaitable[bool]]

HEARTBEAT_INTERVAL_SEC = 5.0


class PuckStreamError(RuntimeError):
    """mpv konnte nicht starten oder die IPC-Verbindung schlug fehl."""


class PuckStream:
    """Eine mpv-Pipeline für genau einen Puck-Target.

    Nicht thread-safe — alle Methoden müssen vom asyncio-Loop aufgerufen
    werden. Concurrent calls auf derselben Instanz sind durch ``_lock``
    serialisiert.
    """

    def __init__(
        self,
        room: str,
        send_start: SendStart,
        send_chunk: SendChunk,
        send_end: SendEnd,
        send_heartbeat: Optional[SendHeartbeat] = None,
    ) -> None:
        self.room = room
        # Target-ID-Prefix muss zum PuckChannel passen — Single-Source-of-Truth
        from .puck import TARGET_PREFIX
        self.target_id = f"{TARGET_PREFIX}{room}"
        self._send_start = send_start
        self._send_chunk = send_chunk
        self._send_end = send_end
        self._send_heartbeat = send_heartbeat

        # Lebenszyklus-Pfade — eindeutig pro Raum
        safe_room = "".join(c if c.isalnum() else "_" for c in room) or "default"
        self._fifo_path = str(DATA_DIR / f"puck_{safe_room}.fifo")
        self._socket_path = str(DATA_DIR / f"puck_{safe_room}.sock")

        # Subprocess + Tasks + IPC
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._fifo_pump_task: Optional[asyncio.Task[None]] = None
        self._save_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._ipc_reader: Optional[asyncio.StreamReader] = None
        self._ipc_writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

        # State
        self._current_uri: Optional[str] = None
        self._current_state_key: Optional[str] = None
        self._save_interval: int = DEFAULT_SAVE_INTERVAL_SEC
        # Optional callback fired when mpv signals natural EOF (track ended).
        # Used by PuckChannel.play_queue() to advance to the next item.
        self._on_eof_cb: Optional[Callable[[], Awaitable[None]]] = None
        self._stopping = False

        # Backpressure: Pump-Task wartet vor jedem Read auf dieses Event.
        # Initial gesetzt → kein Block. Bei flow=pause vom Puck: clear() →
        # pump hängt → mpv blockiert beim FIFO-write (OS-Pipe-Backpressure).
        # Bei flow=resume: set() → pump läuft weiter. Orthogonal zum
        # User-_pause (das geht via mpv-IPC).
        self._flow_resumed: asyncio.Event = asyncio.Event()
        self._flow_resumed.set()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def configure_save_interval(self, seconds: int) -> None:
        if seconds > 0:
            self._save_interval = seconds

    # ── Lifecycle ────────────────────────────────────────────

    async def start(
        self,
        uri: str,
        state_key: Optional[str],
        start_pos_sec: Optional[float],
        audio_type: str = "music",
    ) -> dict[str, Any]:
        """Starte mpv für ``uri`` und beginne PCM-Pump zum Puck.

        ``audio_type`` ist der Hint an den Puck (music/speech/alarm).
        Beeinflusst dort VU-Pattern und LED-Verhalten.
        """
        async with self._lock:
            if self.is_running:
                # Replace-Semantik: laufender Stream wird durch neuen ersetzt
                await self._cleanup_unlocked()

            await self._make_fifo()

            args = [
                MPV_BINARY,
                "--idle=no",
                "--no-video",
                "--no-terminal",
                "--no-input-default-bindings",
                "--keep-open=no",
                "--demuxer-max-bytes=512MiB",
                "--network-timeout=30",
                f"--audio-samplerate={PUCK_SAMPLE_RATE}",
                f"--audio-channels={PUCK_CHANNELS}",
                f"--audio-format={PUCK_SAMPLE_FORMAT}",
                "--ao=pcm",
                f"--ao-pcm-file={self._fifo_path}",
                "--ao-pcm-waveheader=no",   # raw PCM, kein WAV-Header
                f"--input-ipc-server={self._socket_path}",
            ]
            if start_pos_sec and start_pos_sec > 0:
                args.append(f"--start={float(start_pos_sec)}")
            args.append(uri)

            try:
                Path(self._socket_path).unlink(missing_ok=True)
            except OSError:
                pass

            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Wait for IPC socket to appear before we try to connect
            socket_ready = False
            for _ in range(int(SOCKET_WAIT_TIMEOUT_SEC * 20)):
                if Path(self._socket_path).exists():
                    socket_ready = True
                    break
                # mpv may exit early (bad URI etc.) — abort if so
                if self._proc.returncode is not None:
                    raise PuckStreamError(
                        f"mpv exited rc={self._proc.returncode} before IPC socket"
                    )
                await asyncio.sleep(0.05)
            if not socket_ready:
                raise PuckStreamError(
                    f"mpv did not create IPC socket at {self._socket_path}"
                )

            self._ipc_reader, self._ipc_writer = await asyncio.open_unix_connection(
                self._socket_path
            )
            self._reader_task = asyncio.create_task(
                self._ipc_read_loop(),
                name=f"puck-{self.room}-ipc-reader",
            )
            await self._send({"command": ["observe_property", 1, "eof-reached"]})

            self._current_uri = uri
            self._current_state_key = state_key
            self._stopping = False

            # Tell the puck what's coming + start pumping FIFO → WS
            await self._send_start(
                self.room, PUCK_CHANNELS, PUCK_SAMPLE_RATE, audio_type,
            )
            self._fifo_pump_task = asyncio.create_task(
                self._fifo_pump(),
                name=f"puck-{self.room}-fifo-pump",
            )
            self._save_task = asyncio.create_task(
                self._position_save_loop(),
                name=f"puck-{self.room}-position-save",
            )
            if self._send_heartbeat is not None:
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(),
                    name=f"puck-{self.room}-heartbeat",
                )

            log_message(
                f"PuckStream[{self.room}]: mpv started ({uri}, key={state_key})"
            )
            return {
                "uri": uri,
                "state_key": state_key,
                "start_pos_sec": float(start_pos_sec) if start_pos_sec else 0.0,
                "target": self.target_id,
            }

    async def stop(self) -> bool:
        """Beende den Stream sauber. Idempotent."""
        async with self._lock:
            if not self.is_running and self._fifo_pump_task is None:
                return False
            await self._cleanup_unlocked()
            return True

    async def _cleanup_unlocked(self) -> None:
        """Lock muss vom Caller gehalten werden.

        Kritische Reihenfolge:
        1. Position speichern (während IPC noch lebt)
        2. mpv terminieren — damit blockende ``os.read(fifo_fd)`` im
           pump-Task ein EOF bekommen und returnen können
        3. Tasks cancel + await
        4. IPC + FIFO + Socket aufräumen
        """
        self._stopping = True
        # Flow-Event freigeben, sonst hängt der pump-Task ewig im wait()
        # falls der Puck zuletzt flow=pause geschickt hat.
        self._flow_resumed.set()

        # 1. Position eines letzten Mal speichern (IPC noch da)
        if self._current_state_key and self._ipc_writer is not None:
            try:
                pos = await self._get_property("time-pos", default=None)
                dur = await self._get_property("duration", default=None)
                if pos is not None:
                    from ..audio_state import audio_state
                    audio_state.update(
                        key=self._current_state_key,
                        uri=self._current_uri or "",
                        pos_sec=float(pos),
                        duration_sec=float(dur) if dur is not None else None,
                    )
            except Exception:  # noqa: BLE001
                pass

        # 2. mpv beenden — der pump-Task wartet sonst ewig auf neue PCM-Bytes
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except ProcessLookupError:
                pass
        self._proc = None

        # 3. Tasks aufräumen
        for task in (self._fifo_pump_task, self._reader_task, self._save_task, self._heartbeat_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._fifo_pump_task, self._reader_task, self._save_task, self._heartbeat_task):
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
        self._fifo_pump_task = None
        self._reader_task = None
        self._save_task = None
        self._heartbeat_task = None

        # 4. IPC + Files
        if self._ipc_writer is not None:
            try:
                self._ipc_writer.close()
                await self._ipc_writer.wait_closed()
            except OSError:
                pass
        self._ipc_writer = None
        self._ipc_reader = None

        for path in (self._fifo_path, self._socket_path):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

        # Puck signalisieren dass der Stream aus ist
        try:
            await self._send_end(self.room)
        except Exception as exc:  # noqa: BLE001
            log_message(
                f"PuckStream[{self.room}]: send_end failed: {exc}", "warning"
            )

        self._current_uri = None
        self._current_state_key = None
        self._pending.clear()

    # ── Steuerung via mpv-IPC ────────────────────────────────

    async def pause(self) -> bool:
        if not self.is_running:
            return False
        try:
            await self._send({"command": ["set_property", "pause", True]})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def resume(self) -> bool:
        if not self.is_running:
            return False
        try:
            paused = await self._get_property("pause", default=None)
            if paused is False:
                return False  # nichts zu unpausen
            await self._send({"command": ["set_property", "pause", False]})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def seek(self, position_sec: float, relative: bool = False) -> bool:
        if not self.is_running:
            return False
        try:
            mode = "relative" if relative else "absolute"
            await self._send({"command": ["seek", float(position_sec), mode]})
            return True
        except Exception:  # noqa: BLE001
            return False

    async def status(self) -> dict[str, Any]:
        if not self.is_running:
            return {"running": False, "playing": False, "paused": False, "target": self.target_id}
        try:
            pos = await self._get_property("time-pos", default=None)
            dur = await self._get_property("duration", default=None)
            paused = await self._get_property("pause", default=False)
        except Exception:  # noqa: BLE001
            pos = dur = None
            paused = False
        return {
            "running": True,
            "playing": not paused,
            "paused": bool(paused),
            "state_key": self._current_state_key or "",
            "position_sec": float(pos) if pos is not None else 0.0,
            "duration_sec": float(dur) if dur is not None else None,
            "target": self.target_id,
        }

    # ── IPC primitives ───────────────────────────────────────

    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._ipc_writer is None:
            raise PuckStreamError(f"PuckStream[{self.room}]: IPC not connected")
        self._request_id += 1
        rid = self._request_id
        wrapped = {**payload, "request_id": rid}

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut

        try:
            line = (json.dumps(wrapped) + "\n").encode("utf-8")
            self._ipc_writer.write(line)
            await self._ipc_writer.drain()
            return await asyncio.wait_for(fut, timeout=COMMAND_TIMEOUT_SEC)
        finally:
            self._pending.pop(rid, None)

    async def _ipc_read_loop(self) -> None:
        if self._ipc_reader is None:
            return
        try:
            while True:
                line = await self._ipc_reader.readline()
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
                # mpv signalisiert das natürliche Ende — Stream zu Ende.
                if msg.get("event") == "property-change" and msg.get("name") == "eof-reached":
                    if msg.get("data") is True:
                        await self._on_eof()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log_message(f"PuckStream[{self.room}]: IPC read loop error: {exc}", "warning")

    async def _get_property(self, name: str, default: Any = None) -> Any:
        try:
            resp = await self._send({"command": ["get_property", name]})
            if resp.get("error") == "success":
                return resp.get("data", default)
        except Exception:  # noqa: BLE001
            pass
        return default

    async def _on_eof(self) -> None:
        if self._current_state_key:
            from ..audio_state import audio_state
            audio_state.mark_completed(self._current_state_key)
            log_message(f"PuckStream[{self.room}]: completed {self._current_state_key}")
        # Cleanup nicht hier — der fifo_pump merkt das natürliche Ende
        # (FIFO returns 0 bytes nach mpv-Exit) und stop() wird vom
        # PuckChannel gerufen wenn der Pump-Task fertig ist.

        # Fire optional EOF callback (PuckChannel.play_queue → advance).
        # Snapshotted to None first so a re-entrant start() (next track)
        # can install a fresh callback without race.
        cb = self._on_eof_cb
        self._on_eof_cb = None
        if cb is not None:
            try:
                await cb()
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"PuckStream[{self.room}]: on_eof callback error: {exc}",
                    "warning",
                )

    # ── FIFO-Pumpe: PCM von mpv → freeecho2 WS-Bridge ───────

    async def _make_fifo(self) -> None:
        try:
            Path(self._fifo_path).unlink(missing_ok=True)
        except OSError:
            pass
        try:
            os.mkfifo(self._fifo_path, mode=0o600)
        except FileExistsError:
            pass

    async def _fifo_pump(self) -> None:
        """Lese PCM aus der FIFO und schicke jeden Chunk an den Puck.

        Read auf einer FIFO blockiert bis ein Writer Daten reinschreibt
        (= mpv lebt). Wenn mpv exited, gibt's EOF → 0 bytes → Loop endet.

        Backpressure: vor jedem Read wartet der Pump auf
        ``_flow_resumed`` (Event). Wenn der Puck flow=pause schickt,
        clear()-t der Channel das Event → Pump hängt → mpv blockiert
        am FIFO-write (OS-Pipe-Backpressure, Pipe-Buffer ~64 KB). Bei
        flow=resume wird das Event wieder gesetzt → Pump pumpt weiter.
        """
        loop = asyncio.get_event_loop()
        try:
            fd = await loop.run_in_executor(
                None, lambda: os.open(self._fifo_path, os.O_RDONLY)
            )
        except OSError as exc:
            log_message(
                f"PuckStream[{self.room}]: FIFO open failed: {exc}", "error"
            )
            return

        try:
            while True:
                if self._stopping:
                    break

                # Backpressure: warte bis flow=resume (oder direkt durch
                # falls Event schon set ist). Während des wait() bleibt
                # mpv beim FIFO-write blockiert → keine PCM-Generation.
                if not self._flow_resumed.is_set():
                    log_message(
                        f"PuckStream[{self.room}]: flow=pause — pump waiting",
                    )
                    await self._flow_resumed.wait()
                    log_message(
                        f"PuckStream[{self.room}]: flow=resume — pump continuing",
                    )
                    if self._stopping:
                        break

                try:
                    chunk = await loop.run_in_executor(
                        None, lambda: os.read(fd, READ_CHUNK_SIZE)
                    )
                except OSError as exc:
                    log_message(
                        f"PuckStream[{self.room}]: FIFO read error: {exc}", "warning"
                    )
                    break
                if not chunk:
                    # mpv exited — natural end
                    break
                try:
                    ok = await self._send_chunk(self.room, chunk)
                except Exception as exc:  # noqa: BLE001
                    log_message(
                        f"PuckStream[{self.room}]: send_chunk error: {exc}", "warning"
                    )
                    break
                if not ok:
                    log_message(
                        f"PuckStream[{self.room}]: WS send returned False — aborting",
                        "warning",
                    )
                    break
        except asyncio.CancelledError:
            return
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    # ── Backpressure-Steuerung (vom Channel aufgerufen) ──────

    def notify_flow(self, state: str) -> None:
        """Vom PuckChannel aufgerufen wenn ein flow-Frame vom Puck kommt.

        ``state`` = "pause" → Pump hält an. "resume" → Pump läuft weiter.
        Andere States werden geloggt und ignoriert.
        """
        if state == "pause":
            self._flow_resumed.clear()
        elif state == "resume":
            self._flow_resumed.set()
        else:
            log_message(
                f"PuckStream[{self.room}]: unknown flow state '{state}'", "warning"
            )

    # ── Heartbeat während aktivem Stream ────────────────────
    #
    # Auch bei flow.pause (User-Pause via _pause-Wake oder Backpressure
    # durch vollen Ring) bleibt der Stream "aktiv" — User kann legitim
    # stundenlang pausieren wollen. Der Heartbeat erkennt nur ob der
    # Puck noch erreichbar ist (nicht ob er pausiert hat).
    #
    # Wenn ein send_heartbeat zwei Mal hintereinander fehlschlägt,
    # signalisiert der Stream sich selbst als verloren und räumt auf —
    # damit liegt kein hängender mpv-Prozess auf einem toten WS-Slot.
    # Send-Side-Timeout ist im freeecho2_channel.send_audio_start/chunk
    # eingebaut (10 s); wir nutzen denselben Helper.

    async def _heartbeat_loop(self) -> None:
        if self._send_heartbeat is None:
            return
        consecutive_failures = 0
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
                if self._stopping:
                    break
                try:
                    ok = await self._send_heartbeat(self.room)
                except Exception as exc:  # noqa: BLE001
                    log_message(
                        f"PuckStream[{self.room}]: heartbeat error: {exc}",
                        "warning",
                    )
                    ok = False
                if ok:
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                log_message(
                    f"PuckStream[{self.room}]: heartbeat failed "
                    f"({consecutive_failures}/2)",
                    "warning",
                )
                if consecutive_failures >= 2:
                    log_message(
                        f"PuckStream[{self.room}]: puck unreachable — "
                        f"triggering stream cleanup",
                        "error",
                    )
                    # Stream wird über stop() abgeräumt — das passiert in
                    # einem separaten Task um nicht aus dem heartbeat-loop
                    # heraus selbst-cancellation zu triggern.
                    asyncio.create_task(self.stop())
                    break
        except asyncio.CancelledError:
            return

    # ── Periodisches Position-Save ───────────────────────────

    async def _position_save_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._save_interval)
                if self._stopping or not self._current_state_key or self._ipc_writer is None:
                    continue
                pos = await self._get_property("time-pos", default=None)
                paused = await self._get_property("pause", default=False)
                if pos is None or paused:
                    continue
                dur = await self._get_property("duration", default=None)
                from ..audio_state import audio_state
                audio_state.update(
                    key=self._current_state_key,
                    uri=self._current_uri or "",
                    pos_sec=float(pos),
                    duration_sec=float(dur) if dur is not None else None,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log_message(
                f"PuckStream[{self.room}]: position-save loop error: {exc}", "warning"
            )
