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
import threading
from typing import TYPE_CHECKING, Any, Optional

from ..config import FREEECHO2_KEEPALIVE_SEC
from ..formatting import format_number
from ..logging_utils import log_message
from ._freeecho2_stream import PCM_CHUNK_BYTES

# 48 kHz, mono, int16: das Format aller Ströme zum Puck (SSOT für Dauer-Rechnungen)
PCM_BYTES_PER_SEC = 96000
PCM_BYTES_PER_MS = PCM_BYTES_PER_SEC // 1000
# Länge des Stille-Chunks, der den Strom offen hält, solange das nächste Segment fehlt
KEEPALIVE_SILENCE_MS = 20
KEEPALIVE_SILENCE = bytes(PCM_BYTES_PER_MS * KEEPALIVE_SILENCE_MS)


def silence_pcm(duration_ms: int) -> bytes:
    """Echte Null-Samples (nie einfach pausieren: der Puck beendet einen Strom ohne Chunks)."""
    return bytes(PCM_BYTES_PER_MS * duration_ms)


def _fmt_mib(num_bytes: int) -> str:
    """Bytes als MiB mit 1 Nachkomma (locale-aware Tausender/Dezimal)."""
    return f"{format_number(num_bytes / (1024 * 1024), 1)} MiB"

if TYPE_CHECKING:
    from ._freeecho2_stream import FreeEcho2Stream


# Audio-Types, die ein Ansage/Alarm pausieren muss (der Puck nimmt sie nur im Leerlauf
# oder in der Pause an) — der Puck bestätigt das serverseitig angestoßene _pause
PAUSABLE_TYPES = frozenset({"music", "tts"})

# Audio-Types, die aus einem fertigen Puffer (TTSBuffer) gepumpt werden
BUFFER_TYPES = frozenset({"tts", "alarm", "notification"})

# Audio-Types bei denen Pause = Stop (semantisch transient)
TRANSIENT_TYPES = frozenset({"alarm", "notification"})


class TTSBuffer:
    """In-Memory PCM-Buffer mit Cursor fuer Pause/Resume mid-TTS.

    Server-Side: nach TTS-Render wird das ganze PCM hier gehalten,
    pro Chunk an die WS-Bridge gepumpt. Bei Pause: Cursor merken.
    Bei Resume: ab Cursor weiter pumpen — KEIN Re-Render.

    ``growing=True``: satzweises Streaming — ein Erzeuger (``append``, am Ende
    ``close``) füllt den Puffer, während der Pump schon sendet. Erzeuger und Pump
    laufen evtl. in verschiedenen Event-Loops/Threads: alles hier ist thread-sicher.
    """

    # Gleiche Frame-Groesse wie der Musik-Stream (SSOT, Begruendung dort)
    CHUNK_SIZE = PCM_CHUNK_BYTES

    def __init__(self, pcm_data: bytes = b"", *, growing: bool = False) -> None:
        self._pcm = bytearray(pcm_data)
        self._cursor = 0  # Bytes bereits gepumpt
        self._closed = not growing
        self.failed = False  # der Erzeuger ist abgebrochen: Strom ohne Ende-Ton schließen
        # Erzeuger-Task (satzweises Streaming); discard() bricht ihn ab
        self.producer: "asyncio.Task[None] | None" = None
        self._lock = threading.Lock()
        self._event = asyncio.Event()
        self._waiter_loop: asyncio.AbstractEventLoop | None = None

    @property
    def total_bytes(self) -> int:
        return len(self._pcm)

    @property
    def known_size(self) -> int | None:
        """Gesamtgröße für audio_start, ``None`` solange der Puffer noch wächst."""
        return len(self._pcm) if self._closed else None

    @property
    def remaining_bytes(self) -> int:
        return len(self._pcm) - self._cursor

    @property
    def done(self) -> bool:
        return self._closed and self._cursor >= len(self._pcm)

    def next_chunk(self) -> bytes:
        """Liefere den naechsten Chunk und schiebe den Cursor weiter
        (leer, wenn gerade nichts da ist — bei wachsendem Puffer ggf. nur vorerst)."""
        with self._lock:
            end = min(self._cursor + self.CHUNK_SIZE, len(self._pcm))
            chunk = bytes(self._pcm[self._cursor:end])
            self._cursor = end
            return chunk

    def append(self, pcm: bytes) -> None:
        with self._lock:
            self._pcm.extend(pcm)
        self._notify()

    def close(self, *, failed: bool = False) -> None:
        with self._lock:
            self._closed = True
            self.failed = self.failed or failed
        self._notify()

    async def wait_for_data(self, timeout_sec: float) -> bool:
        """``True``, sobald Daten da sind oder der Puffer geschlossen ist; ``False``
        nach ``timeout_sec`` (der Erzeuger arbeitet noch)."""
        with self._lock:
            self._waiter_loop = asyncio.get_running_loop()
            if self._cursor < len(self._pcm) or self._closed:
                return True
            self._event.clear()
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return False
        return True

    def discard(self) -> None:
        """Puffer verwerfen: einen noch laufenden Erzeuger abbrechen (thread-sicher)."""
        task = self.producer
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)

    def _notify(self) -> None:
        loop = self._waiter_loop
        if loop is not None:
            loop.call_soon_threadsafe(self._event.set)


class AudioOrchestrator:
    """Per-Room State-Machine fuer Audio-Output via FreeEcho.2.

    Zwei Use-Case-Klassen (Protokoll v2: jeder Strom = Flag → audio_start →
    Chunks (0..n) → audio_end):

    1. **Stream-Audio** (music): mpv-Subprocess pumpt PCM via FIFO →
       fifo_pump_task → WS. Pause/Resume via mpv-IPC.
    2. **Buffer-Audio** (tts, alarm, notification): Server haelt vorgerendertes
       PCM in ``TTSBuffer`` (bei der Tuerklingel leer), pumpt chunks direkt.
       Pause merkt Cursor (tts), bei alarm/notification = Stop.

    Beginn- und Ende-Ton spielt der Puck selbst (``start_tone`` im Flag,
    ``end_tone`` im audio_end); der Server wählt nur, ob.
    """

    def __init__(self, room: str, bridge: Any) -> None:
        self.room = room
        self.bridge = bridge  # FreeEchoChannel-Plugin-Instance mit send_*-Methoden
        self._active_type: Optional[str] = None  # music/tts/alarm/notification
        self._stream: Optional["FreeEcho2Stream"] = None  # nur fuer music
        self._tts_buffer: Optional[TTSBuffer] = None  # nur fuer tts
        self._tts_pump_task: Optional[asyncio.Task[bool]] = None
        self._end_tone: bool = False  # end_tone des Buffer-Stroms im audio_end
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

    async def play_tts(self, pcm_data: "bytes | TTSBuffer") -> None:
        """Starte einen TTS-PCM-Stream (standalone) und warte bis er durch ist.

        Konsens-Spec audio-pipeline.md "Variante (ii)": **kein** Takeover
        ueber laufender Music. TTS ist immer eine eigenstaendige Source —
        wenn vorher Music war, wird die durch ``_reset_active_unlocked``
        sauber beendet (mpv terminiert, audio_end gesendet, Position via
        consumed_ms vom Puck in audio_state.json gespeichert). User holt
        Music spaeter via ``audio_resume`` zurueck (Pre-Roll greift).

        Frame-Sequenz: audio_flag(tts) + audio_start + chunks + audio_end,
        ohne Beginn- und Ende-Ton. ``pcm_data`` ist fertiges PCM oder ein
        (wachsender) ``TTSBuffer`` für satzweises Streaming.
        """
        await self._play_buffer("tts", pcm_data, start_tone=False, end_tone=False)

    async def play_alarm(
        self, tts_pcm: "bytes | TTSBuffer | None" = None, *, start_tone: bool, end_tone: bool,
    ) -> bool:
        """Alarm: der Puck spielt seine lokale Alarm-WAV (``start_tone``), danach
        optional Sprache (``tts_pcm``) und der Ende-Ton (``end_tone``).

        Wenn der Use-Case einen längeren/aufdringlichen Wecker verlangt,
        loopt der **Caller** play_alarm() mehrfach. _stop bricht den Loop ab.
        Rückgabe wie bei ``_play_buffer``.
        """
        return await self._play_buffer(
            "alarm", tts_pcm, start_tone=start_tone, end_tone=end_tone,
        )

    async def play_notification(
        self, tts_pcm: "bytes | TTSBuffer | None" = None, *, start_tone: bool, end_tone: bool,
    ) -> bool:
        """Ansage: Beginn-Ton (``start_tone``), Sprache (``tts_pcm``), Ende-Ton
        (``end_tone``). Ohne Sprache folgen die beiden Töne direkt aufeinander
        (Türklingel): Strom mit null Chunks. Beide Flags sind Pflicht, der Puck
        lehnt die Frames sonst als Protokollfehler ab. Rückgabe wie bei ``_play_buffer``.
        """
        return await self._play_buffer(
            "notification", tts_pcm, start_tone=start_tone, end_tone=end_tone,
        )

    async def _play_buffer(
        self, audio_type: str, source: "bytes | TTSBuffer | None", *,
        start_tone: bool, end_tone: bool,
    ) -> bool:
        """Gemeinsamer Ablauf aller Buffer-Audioarten (SSOT): Flag → audio_start →
        Chunks (0..n) → audio_end. Wartet **synchron**, bis der Pump-Task durch ist
        (oder via pause/stop gecancelt) — erst dann sind alle Frames am Wire, sonst
        rast der Caller los und triggert STATE→IDLE vor den PCM-Bytes.

        Rückgabe: ``True`` bei natürlichem Ende (der Puck quittiert dann mit ``_done``),
        ``False`` bei Abbruch (Stopp/Pause, Sende-Fehler): nach einem Abbruch sendet
        der Puck KEIN ``_done`` — der Aufrufer darf nicht darauf warten."""
        async with self._lock:
            await self._reset_active_unlocked()
            self._tts_buffer = source if isinstance(source, TTSBuffer) else TTSBuffer(source or b"")
            self._active_type = audio_type
            self._end_tone = end_tone
            self._paused = False

            await self.bridge.send_audio_flag(self.room, audio_type, start_tone=start_tone)
            await self.bridge.send_audio_start(
                self.room, total_size=self._tts_buffer.known_size,
            )
            self._tts_pump_task = asyncio.create_task(
                self._pump_tts_buffer(),
                name=f"freeecho2-{self.room}-{audio_type}-pump",
            )
            log_message(
                f"AudioOrchestrator[{self.room}]: → {audio_type} active "
                f"({_fmt_mib(self._tts_buffer.total_bytes)}"
                f"{'' if self._tts_buffer.known_size is not None else ' so far, streaming'}, "
                f"start_tone={start_tone}, end_tone={end_tone})"
            )
            task = self._tts_pump_task

        # AUSSERHALB des Locks warten — sonst koennten pause/stop nicht
        # eingreifen (sie wuerden auf den Lock blockieren). CancelledError
        # ist normal: pause/stop hat den Pump-Task waehrend des Streams
        # abgebrochen, Cursor bleibt fuer spaeteres resume erhalten.
        try:
            return await task
        except asyncio.CancelledError:
            return False

    async def pause_for_announcement(self, timeout_sec: float) -> None:
        """Vor Ansage/Alarm: einen laufenden pausierbaren Strom (music/tts) per
        server-initiiertem ``_pause`` am Puck anhalten und seine Bestätigung abwarten.

        Der Puck nimmt notification/alarm nur im Leerlauf oder in der Pause an.
        Die Bestätigung läuft durch denselben Weg wie ein gesprochenes „Bitte
        Pause“ (Stream stoppen, Position speichern); „Bitte weiter“ lädt danach
        wie gewohnt ab dieser Position neu. KEIN ``_resume`` von hier. Nicht unter
        ``_lock`` aufrufen: die Bestätigung stoppt den Orchestrator selbst.
        Raises, wenn der Puck nicht bestätigt (die Ansage wird dann verworfen).
        """
        if self._active_type not in PAUSABLE_TYPES or self._paused:
            return
        log_message(f"AudioOrchestrator[{self.room}]: pausing {self._active_type} for announcement")
        await self.bridge.pause_stream(self.room, timeout_sec)

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
        if self._tts_buffer is not None:
            self._tts_buffer.discard()   # laufenden Erzeuger (satzweises Streaming) abbrechen
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

        # audio_end-Marker an Puck (Protokoll v2: jeder Strom hatte ein audio_start);
        # ein Stopp/Abbruch spielt keinen Ende-Ton
        if self._active_type is not None:
            try:
                await self.bridge.send_audio_end(self.room, end_tone=False)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"AudioOrchestrator[{self.room}]: send_audio_end error: {exc}",
                    "warning",
                )

        self._active_type = None
        self._paused = False

    async def _pump_tts_buffer(self) -> bool:
        """Background-Task: pumpt TTSBuffer als standalone-TTS in WS.

        Endet wenn Buffer geschlossen und leer ODER Task gecancelt wird (pause/stop).
        Bei normalem Ende: schickt audio_end, Rückgabe ``True``. Bei Sende-Fehler oder
        abgebrochenem Erzeuger: audio_end ohne Ende-Ton, ``False``. Bei cancel: KEIN
        audio_end — der Buffer kann noch resumed werden.

        Wächst der Puffer noch (satzweises Streaming) und das nächste Segment ist nicht
        rechtzeitig fertig, hält ein Stille-Chunk alle ``FREEECHO2_KEEPALIVE_SEC`` den
        Strom offen (das Inaktivitäts-Timeout des Pucks löst nie aus; es gibt hörbare
        Stille bis zum nächsten Segment).
        """
        buffer = self._tts_buffer
        total = buffer.total_bytes if buffer is not None else 0
        sent_chunks = 0
        sent_bytes = 0
        keepalives = 0
        log_message(
            f"AudioOrchestrator[{self.room}]: TTS pump start "
            f"({_fmt_mib(total)}, {total} bytes)"
        )
        try:
            while buffer is not None and not buffer.done:
                chunk = buffer.next_chunk()
                if not chunk:
                    # Der Erzeuger hat noch nichts Neues: kurz warten, sonst Keepalive
                    if await buffer.wait_for_data(FREEECHO2_KEEPALIVE_SEC):
                        continue
                    chunk = KEEPALIVE_SILENCE
                    keepalives += 1
                ok = await self.bridge.send_audio_chunk(self.room, chunk)
                if not ok:
                    log_message(
                        f"AudioOrchestrator[{self.room}]: TTS chunk send "
                        f"failed — abort pump after {sent_chunks} chunks / "
                        f"{sent_bytes} bytes",
                        "warning",
                    )
                    # Stream sauber schließen; ein Abbruch spielt keinen Ende-Ton
                    await self.bridge.send_audio_end(self.room, end_tone=False)
                    return False
                sent_chunks += 1
                sent_bytes += len(chunk)
            if buffer is not None and buffer.failed:
                log_message(
                    f"AudioOrchestrator[{self.room}]: TTS producer failed — stream closed "
                    f"after {sent_chunks} chunks without end tone",
                    "error",
                )
                await self.bridge.send_audio_end(self.room, end_tone=False)
                return False
            # Normal beendet — Stream voll durchgelaufen
            await self.bridge.send_audio_end(self.room, end_tone=self._end_tone)
            log_message(
                f"AudioOrchestrator[{self.room}]: TTS pump complete "
                f"({sent_chunks} chunks / {sent_bytes} bytes, {keepalives} keepalives) — audio_end sent"
            )
            async with self._lock:
                if self._active_type in BUFFER_TYPES:
                    self._active_type = None
                    self._tts_buffer = None
                    self._tts_pump_task = None
            return True
        except asyncio.CancelledError:
            # Pause oder stop — KEIN audio_end, Buffer-Cursor bleibt
            log_message(
                f"AudioOrchestrator[{self.room}]: TTS pump cancelled after "
                f"{sent_chunks} chunks / {sent_bytes} bytes"
            )
            raise

