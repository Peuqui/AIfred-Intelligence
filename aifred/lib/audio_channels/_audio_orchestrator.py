"""AudioOrchestrator — single-pump-pfad pro FreeEcho.2-Room (Phase 5.0).

Konsens-Spec siehe ``docs/de/architecture/audio-pipeline.md`` Sektion
"Audio-Bus-Refactor".

## Kernidee

Pro Room **eine** aktive Audio-Source. Type-Wechsel via ``audio_flag``-
Frame ohne Source-Reset (30 ms Linear-Fade Puck-side). Server pumpt
nie zwei PCM-Quellen parallel ans Wire — vermeidet Race-Conditions
und Audio-Salat am Speaker-Buffer.

## Audio-Type-Verhalten

| Type           | Server pumpt PCM? | Pause-Verhalten        |
|----------------|-------------------|------------------------|
| music          | ✓ mpv-FIFO        | echtes Pause           |
| tts            | ✓ TTS-Buffer      | echtes Pause (Cursor)  |
| alarm          | ✗ (Puck-lokal)    | = Stop                 |
| notification   | ✗ (Puck-lokal)    | = Stop                 |

## State-Machine

```
                    play_audio(music) → MUSIC_ACTIVE
                    play_audio(tts)   → TTS_ACTIVE
                    play_audio(alarm) → ALARM_ACTIVE  (Puck-lokaler Loop)
IDLE ─────────────► play_audio(notif) → NOTIF_ACTIVE  (Puck-lokaler Sound)

MUSIC_ACTIVE ─pause→ MUSIC_PAUSED ─resume→ MUSIC_ACTIVE
TTS_ACTIVE   ─pause→ TTS_PAUSED   ─resume→ TTS_ACTIVE
ANY          ─stop─────────────────────► IDLE
ALARM/NOTIF  ─pause─────────────────────► IDLE  (= stop)
```
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from ..formatting import format_number
from ..logging_utils import log_message


def _fmt_mib(num_bytes: int) -> str:
    """Bytes als MiB mit 1 Nachkomma (locale-aware Tausender/Dezimal)."""
    return f"{format_number(num_bytes / (1024 * 1024), 1)} MiB"

if TYPE_CHECKING:
    from ._freeecho2_stream import FreeEcho2Stream


# Audio-Types die Server-Pumping erfordern (vs. Puck-lokal)
PUMPED_TYPES = frozenset({"music", "tts"})

# Audio-Types bei denen Pause = Stop (semantisch transient)
TRANSIENT_TYPES = frozenset({"alarm", "notification"})


class TTSBuffer:
    """In-Memory PCM-Buffer mit Cursor fuer Pause/Resume mid-TTS.

    Server-Side: nach TTS-Render wird das ganze PCM hier gehalten,
    pro Chunk an die WS-Bridge gepumpt. Bei Pause: Cursor merken.
    Bei Resume: ab Cursor weiter pumpen — KEIN Re-Render.
    """

    # 512 KB Chunks — gleiche Groesse wie das alte send_reply
    CHUNK_SIZE = 512 * 1024

    def __init__(self, pcm_data: bytes) -> None:
        self._pcm = pcm_data
        self._cursor = 0  # Bytes bereits gepumpt

    @property
    def total_bytes(self) -> int:
        return len(self._pcm)

    @property
    def remaining_bytes(self) -> int:
        return len(self._pcm) - self._cursor

    @property
    def done(self) -> bool:
        return self._cursor >= len(self._pcm)

    def next_chunk(self) -> bytes:
        """Liefere den naechsten Chunk und schiebe den Cursor weiter."""
        if self.done:
            return b""
        end = min(self._cursor + self.CHUNK_SIZE, len(self._pcm))
        chunk = self._pcm[self._cursor:end]
        self._cursor = end
        return chunk


class AudioOrchestrator:
    """Per-Room State-Machine fuer Audio-Output via FreeEcho.2.

    Drei Use-Case-Klassen:

    1. **Stream-Audio** (music): mpv-Subprocess pumpt PCM via FIFO →
       fifo_pump_task → WS. Pause/Resume via mpv-IPC.
    2. **Buffer-Audio** (tts): Server haelt vorgerendertes PCM in
       ``TTSBuffer``, pumpt chunks direkt. Pause merkt Cursor, Resume
       pumpt weiter.
    3. **Puck-lokal** (alarm, notification): Server schickt nur
       ``audio_flag``-Frame mit Tupel-Parametern, kein PCM. Puck spielt
       seine lokale WAV in Loop / 1x. Pause = Stop.

    Bei TTS-Tail an alarm/notification: nach dem Puck-lokal-Frame wird
    sofort ein ``audio_flag(tts)`` + ``audio_start`` + chunks + ``audio_end``
    gesendet. Puck queued das, spielt sequenziell.
    """

    def __init__(self, room: str, bridge: Any) -> None:
        self.room = room
        self.bridge = bridge  # FreeEchoChannel-Plugin-Instance mit send_*-Methoden
        self._active_type: Optional[str] = None  # music/tts/alarm/notification
        self._stream: Optional["FreeEcho2Stream"] = None  # nur fuer music
        self._tts_buffer: Optional[TTSBuffer] = None  # nur fuer tts
        self._tts_pump_task: Optional[asyncio.Task[None]] = None
        self._paused: bool = False
        self._lock = asyncio.Lock()

    @property
    def active_type(self) -> Optional[str]:
        """Aktueller Audio-Type oder None wenn IDLE."""
        return self._active_type

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_idle(self) -> bool:
        return self._active_type is None

    # ── Public API: play / pause / resume / stop ────────────────────

    async def play_music(self, stream: "FreeEcho2Stream") -> None:
        """Music-Stream am Orchestrator anmelden.

        Der Caller (FreeEcho2Channel.play) hat ``stream.start(...)``
        bereits aufgerufen — start() managed seinen eigenen Replace-
        Lifecycle (alte mpv terminate + audio_end + neue mpv +
        audio_flag(music) + audio_start). Der ``stream``-Pointer ist
        in ``_streams`` pro Room gecacht, deshalb identisch zu einem
        evtl. vorher hier registrierten ``self._stream``.

        Hier also nur State-Tracking + Cleanup VON ANDEREN Sources
        (laufende TTS-Pumps): cancel any active pump-task. KEIN
        ``self._stream.stop()`` weil das die gerade gestartete mpv
        toeten + nochmal audio_end senden wuerde — Puck sieht dann
        audio_start direkt gefolgt von audio_end und wertet die Source
        als sofort-fertig (eos=1, ring leer).

        Auch KEIN audio_end fuer einen evtl. vorher aktiven TTS — das
        audio_start des neuen Music-Streams ist auf Puck-Seite der
        implicit Source-Reset. Wenn der TTS-pump sauber durch war, hat
        er audio_end ohnehin selbst gesendet; bei cancel droppen wir
        den Tail-Stream still und der neue Stream startet sauber.
        """
        async with self._lock:
            # Pending TTS-pump-Task abbrechen (Standalone oder Takeover).
            # Der Cancel raised CancelledError, beide Pump-Funktionen
            # leiten den durch ohne audio_end zu senden.
            if self._tts_pump_task is not None and not self._tts_pump_task.done():
                self._tts_pump_task.cancel()
                try:
                    await self._tts_pump_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._tts_pump_task = None
            self._tts_buffer = None
            self._takeover_active = False

            self._stream = stream
            self._active_type = "music"
            self._paused = False
            log_message(f"AudioOrchestrator[{self.room}]: → music active")

    async def play_tts(self, pcm_data: bytes) -> None:
        """Starte einen TTS-PCM-Stream (standalone) und warte bis er durch ist.

        Konsens-Spec audio-pipeline.md "Variante (ii)": **kein** Takeover
        ueber laufender Music. TTS ist immer eine eigenstaendige Source —
        wenn vorher Music war, wird die durch ``_reset_active_unlocked``
        sauber beendet (mpv terminiert, audio_end gesendet, Position via
        consumed_ms vom Puck in audio_state.json gespeichert). User holt
        Music spaeter via ``audio_resume`` zurueck (Pre-Roll greift).

        Frame-Sequenz: audio_flag(tts) + audio_start + chunks + audio_end.

        PCM wird im TTSBuffer gehalten — bei pause/resume bleibt der
        Cursor erhalten, kein Re-Render. Wartet **synchron** bis der
        Pump-Task durch ist (oder via pause/stop gecancelt). Returnt
        damit erst wenn alle Frames am Wire sind — sonst rast der
        Caller (z.B. send_reply → Speaking-Phase-Ende) los und
        triggert STATE→IDLE bevor die PCM-Bytes raus sind.
        """
        async with self._lock:
            await self._reset_active_unlocked()
            self._tts_buffer = TTSBuffer(pcm_data)
            self._active_type = "tts"
            self._paused = False

            await self.bridge.send_audio_flag(self.room, "tts")
            await self.bridge.send_audio_start(
                self.room, total_size=self._tts_buffer.total_bytes,
            )
            self._tts_pump_task = asyncio.create_task(
                self._pump_tts_buffer(),
                name=f"freeecho2-{self.room}-tts-pump",
            )
            log_message(
                f"AudioOrchestrator[{self.room}]: → tts active "
                f"({_fmt_mib(self._tts_buffer.total_bytes)})"
            )
            task = self._tts_pump_task

        # AUSSERHALB des Locks warten — sonst koennten pause/stop nicht
        # eingreifen (sie wuerden auf den Lock blockieren). CancelledError
        # ist normal: pause/stop hat den Pump-Task waehrend des Streams
        # abgebrochen, Cursor bleibt fuer spaeteres resume erhalten.
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def play_alarm(
        self, with_tts: bool, tts_pcm: Optional[bytes] = None,
    ) -> None:
        """Trigger einen Puck-lokalen Alarm-Sound (einmal abspielen).

        Server schickt nur das audio_flag-Tupel — KEIN PCM (Puck spielt
        seine UI-konfigurierte lokale WAV). Bei ``with_tts=True`` folgt
        SOFORT (gleicher Funktionsaufruf) der TTS-Tail-Stream.

        Wenn der Use-Case einen längeren/aufdringlichen Wecker verlangt,
        loopt der **Caller** play_alarm() mehrfach (kein Loop-Counter im
        Frame). _stop bricht dann den Caller-Loop ab.
        """
        task: Optional[asyncio.Task[None]] = None
        async with self._lock:
            await self._reset_active_unlocked()
            self._active_type = "alarm"
            self._paused = False

            await self.bridge.send_audio_flag(
                self.room, "alarm", with_tts=with_tts,
            )
            log_message(
                f"AudioOrchestrator[{self.room}]: → alarm "
                f"(with_tts={with_tts})"
            )

            # TTS-Tail: schick es direkt nach dem alarm-Tupel raus,
            # Puck queued das hinter den lokalen Sound.
            if with_tts and tts_pcm:
                self._tts_buffer = TTSBuffer(tts_pcm)
                await self.bridge.send_audio_flag(self.room, "tts")
                await self.bridge.send_audio_start(
                    self.room, total_size=self._tts_buffer.total_bytes,
                )
                self._tts_pump_task = asyncio.create_task(
                    self._pump_tts_buffer(),
                    name=f"freeecho2-{self.room}-tts-tail-pump",
                )
                task = self._tts_pump_task

        # Tail-Pump AUSSERHALB des Locks auspumpen lassen — so weiß der Caller
        # (Alert-Worker), dass audio_end raus ist, bevor er das done-Frame
        # schickt. pause/stop können den Pump weiter canceln (CancelledError
        # ist normal). Ohne Tail kehrt play_alarm sofort zurück (Wecker-Loop).
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def play_notification(
        self, with_tts: bool, tts_pcm: Optional[bytes] = None,
    ) -> None:
        """Trigger einen Puck-lokalen Notification-Sound.

        Analog zu ``play_alarm`` — nur einmal abspielen statt Loop.
        """
        task: Optional[asyncio.Task[None]] = None
        async with self._lock:
            await self._reset_active_unlocked()
            self._active_type = "notification"
            self._paused = False

            await self.bridge.send_audio_flag(
                self.room, "notification", with_tts=with_tts,
            )
            log_message(
                f"AudioOrchestrator[{self.room}]: → notification "
                f"(with_tts={with_tts})"
            )

            if with_tts and tts_pcm:
                self._tts_buffer = TTSBuffer(tts_pcm)
                await self.bridge.send_audio_flag(self.room, "tts")
                await self.bridge.send_audio_start(
                    self.room, total_size=self._tts_buffer.total_bytes,
                )
                self._tts_pump_task = asyncio.create_task(
                    self._pump_tts_buffer(),
                    name=f"freeecho2-{self.room}-tts-tail-pump",
                )
                task = self._tts_pump_task

        # Tail-Pump auspumpen lassen (siehe play_alarm) — Caller weiß danach,
        # dass audio_end raus ist und kann das done-Frame schicken.
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def pause(self) -> bool:
        """Type-aware pause.

        - music/tts → echtes Pause (mpv-IPC pause oder TTS-pump-Stop)
        - alarm/notification → = stop (transient, kein Resume sinnvoll)

        Returns ``True`` wenn etwas gepausen/gestoppt wurde, ``False``
        wenn IDLE.
        """
        async with self._lock:
            if self._active_type is None:
                return False

            if self._active_type in TRANSIENT_TYPES:
                await self._reset_active_unlocked()
                log_message(
                    f"AudioOrchestrator[{self.room}]: pause on transient "
                    f"({self._active_type}) → stop"
                )
                return True

            # music/tts → echtes Pause
            if self._paused:
                return False  # schon paused, idempotent

            if self._active_type == "music" and self._stream is not None:
                await self._stream.pause()
            elif self._active_type == "tts" and self._tts_pump_task is not None:
                if not self._tts_pump_task.done():
                    self._tts_pump_task.cancel()
                    try:
                        await self._tts_pump_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                self._tts_pump_task = None

            self._paused = True
            log_message(
                f"AudioOrchestrator[{self.room}]: paused ({self._active_type})"
            )
            return True

    async def resume(self) -> bool:
        """Resume eine pausierte music/tts-Source.

        No-op wenn nichts pausiert ist oder Type non-resumable
        (alarm/notification — die wurden bei pause schon gestoppt).
        """
        async with self._lock:
            if not self._paused or self._active_type is None:
                return False

            if self._active_type == "music" and self._stream is not None:
                await self._stream.resume()
            elif (
                self._active_type == "tts"
                and self._tts_buffer is not None
                and not self._tts_buffer.done
            ):
                # Pump-Task neu starten — nimmt vom Cursor-State des
                # bestehenden TTSBuffer auf, kein Re-Render.
                self._tts_pump_task = asyncio.create_task(
                    self._pump_tts_buffer(),
                    name=f"freeecho2-{self.room}-tts-pump-resume",
                )

            self._paused = False
            log_message(
                f"AudioOrchestrator[{self.room}]: resumed ({self._active_type})"
            )
            return True

    async def stop(self) -> bool:
        """Komplett-Reset — alle Sources verworfen, audio_end an Puck.

        Returns ``True`` wenn etwas gestoppt wurde, ``False`` wenn IDLE.
        """
        async with self._lock:
            if self._active_type is None:
                return False
            await self._reset_active_unlocked()
            # Design A: Terminal-Kontrakt audio_end + done — überall gleich.
            # _reset_active_unlocked hat audio_end geschickt (für music/tts);
            # done schließt die Turn-Grenze deterministisch ab (Puck → IDLE +
            # _done-ACK), symmetrisch zum Alert- und reaktiven Pfad.
            try:
                await self.bridge.send_done(self.room)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"AudioOrchestrator[{self.room}]: send_done error: {exc}",
                    "warning",
                )
            log_message(f"AudioOrchestrator[{self.room}]: stop → IDLE")
            return True

    # ── Internal: Reset + TTS-Pump ──────────────────────────────────

    async def _reset_active_unlocked(self) -> None:
        """Caller hat ``_lock``. Räumt alle Sub-Sources auf."""
        # TTS-pump-Task abbrechen
        if self._tts_pump_task is not None and not self._tts_pump_task.done():
            self._tts_pump_task.cancel()
            try:
                await self._tts_pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tts_pump_task = None
        self._tts_buffer = None

        # mpv-Stream beenden (falls aktiv)
        if self._stream is not None:
            try:
                await self._stream.stop()
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"AudioOrchestrator[{self.room}]: stream.stop error: {exc}",
                    "warning",
                )
            self._stream = None

        # audio_end-Marker an Puck (nur fuer Stream-/Buffer-Audio die
        # einen audio_start hatten — bei alarm/notification ohne Tail
        # gibt's auch kein audio_end laut Spec)
        if self._active_type in PUMPED_TYPES:
            try:
                await self.bridge.send_audio_end(self.room)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"AudioOrchestrator[{self.room}]: send_audio_end error: {exc}",
                    "warning",
                )

        self._active_type = None
        self._paused = False

    async def _pump_tts_buffer(self) -> None:
        """Background-Task: pumpt TTSBuffer als standalone-TTS in WS.

        Endet wenn Buffer leer ODER Task gecancelt wird (pause/stop).
        Bei normalem Ende: schickt audio_end. Bei cancel: KEIN audio_end
        — der Buffer kann noch resumed werden.
        """
        total = self._tts_buffer.total_bytes if self._tts_buffer is not None else 0
        sent_chunks = 0
        sent_bytes = 0
        log_message(
            f"AudioOrchestrator[{self.room}]: TTS pump start "
            f"({_fmt_mib(total)}, {total} bytes)"
        )
        try:
            while self._tts_buffer is not None and not self._tts_buffer.done:
                chunk = self._tts_buffer.next_chunk()
                if not chunk:
                    break
                ok = await self.bridge.send_audio_chunk(self.room, chunk)
                if not ok:
                    log_message(
                        f"AudioOrchestrator[{self.room}]: TTS chunk send "
                        f"failed — abort pump after {sent_chunks} chunks / "
                        f"{sent_bytes} bytes",
                        "warning",
                    )
                    return
                sent_chunks += 1
                sent_bytes += len(chunk)
            # Normal beendet — TTS-Stream voll durchgelaufen
            await self.bridge.send_audio_end(self.room)
            log_message(
                f"AudioOrchestrator[{self.room}]: TTS pump complete "
                f"({sent_chunks} chunks / {sent_bytes} bytes) — audio_end sent"
            )
            async with self._lock:
                if self._active_type == "tts":
                    self._active_type = None
                    self._tts_buffer = None
                    self._tts_pump_task = None
        except asyncio.CancelledError:
            # Pause oder stop — KEIN audio_end, Buffer-Cursor bleibt
            log_message(
                f"AudioOrchestrator[{self.room}]: TTS pump cancelled after "
                f"{sent_chunks} chunks / {sent_bytes} bytes"
            )
            raise

