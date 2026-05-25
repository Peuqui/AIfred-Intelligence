"""Video-Hub — SSOT für jeden FrameSource im Prozess.

Eine einzige Klasse hält pro Source genau einen V4L2-Reader offen
und multiplexed die Frames an alle Konsumenten (Browser-Tabs,
Watcher, Snapshots). Damit verschwindet die V4L2-Exklusivitäts-
Klemme strukturell: egal wie viele Tabs / Watcher / Snapshots
gleichzeitig zugreifen, es gibt immer genau einen Read.

Lifecycle:
* ``subscribe(source_id)`` startet den Reader-Task lazy beim ersten
  Subscriber.
* Nach dem letzten Unsubscribe wird ein Shutdown nach ``GRACE_SEC``
  geplant. Kommt in der Zeit ein neuer Subscriber, wird der Shutdown
  abgebrochen — der Reader läuft weiter.
* Reader-fps richtet sich nach dem schnellsten Subscriber; ein
  späterer höher-frequenter Subscriber löst einen Reader-Restart aus.

Konsumenten:
* ``api.py`` :func:`_mjpeg_stream` → ``hub.subscribe(...)``
* :mod:`vision_watcher` → ``hub.subscribe(...)`` statt eigenes
  ``source.stream(...)``.
* ``api.py`` :func:`vision_snapshot_endpoint` → ``hub.snapshot(...)``
  als One-Shot-Subscribe.

Das schon vorhandene :mod:`frame_bus` bleibt als Primitive intern
genutzt, aber Konsumenten gehen jetzt durch den Hub.
"""

from __future__ import annotations

import asyncio
import logging
from threading import Lock
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from .frame_sources.base import Frame, FrameSource

logger = logging.getLogger(__name__)

# Wartezeit nach dem letzten Unsubscribe, bevor der Reader-Task
# beendet wird. Erlaubt schnelle Tab-Reloads ohne komplettes
# V4L2-Stream-Tear-Down + erneutes Setup.
GRACE_SEC = 5.0

# Default-fps wenn ein Subscriber 0 oder Negatives anfordert.
_DEFAULT_FPS = 2.0


class _HubChannel:
    """Per-Source-Zustand im Hub."""

    def __init__(self, source: "FrameSource") -> None:
        self.source = source
        self.reader_task: asyncio.Task[None] | None = None
        self.subscribers: list[asyncio.Queue["Frame"]] = []
        self.shutdown_task: asyncio.Task[None] | None = None
        # Effektive Stream-Parameter des laufenden Readers.
        self.current_fps: float = 0.0
        self.current_w: int = 0
        self.current_h: int = 0
        self.lock = asyncio.Lock()

    def is_reading(self) -> bool:
        return self.reader_task is not None and not self.reader_task.done()

    async def _ensure_reader(
        self, fps: float, width: int, height: int
    ) -> None:
        """Reader-Task starten oder neu starten wenn Parameter höher
        sind als das, was gerade läuft."""
        need_restart = False
        if not self.is_reading():
            need_restart = True
        else:
            if fps > self.current_fps + 1e-3:
                need_restart = True
            if width > 0 and width != self.current_w:
                need_restart = True
            if height > 0 and height != self.current_h:
                need_restart = True

        if not need_restart:
            return

        # Alten Reader (falls vorhanden) sauber beenden.
        old = self.reader_task
        if old is not None and not old.done():
            old.cancel()
            try:
                await old
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.reader_task = None

        self.current_fps = max(self.current_fps, fps, _DEFAULT_FPS)
        if width > 0:
            self.current_w = width
        if height > 0:
            self.current_h = height
        self.reader_task = asyncio.create_task(
            self._reader_loop(), name=f"frame-hub:{self.source.source_id}"
        )
        logger.info(
            "hub reader started for %s @ %.1f fps (%dx%d)",
            self.source.source_id,
            self.current_fps,
            self.current_w,
            self.current_h,
        )

    async def _reader_loop(self) -> None:
        """Konsumiert source.stream() und verteilt jeden Frame an alle
        Subscriber. Backpressure-Policy: drop_oldest pro Subscriber-Queue.
        """
        try:
            async for frame in self.source.stream(
                fps=self.current_fps,
                width=self.current_w,
                height=self.current_h,
            ):
                # Snapshot der Subscriber-Liste — concurrent subscribe/
                # unsubscribe darf den Loop nicht aus dem Tritt bringen.
                for q in list(self.subscribers):
                    try:
                        q.put_nowait(frame)
                    except asyncio.QueueFull:
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            q.put_nowait(frame)
                        except asyncio.QueueFull:
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "hub reader for %s crashed", self.source.source_id
            )

    def _schedule_shutdown(self) -> None:
        """Reader-Stop nach GRACE_SEC schedulen. Wird abgebrochen wenn
        in der Zwischenzeit ein neuer Subscriber dazukommt."""
        if self.shutdown_task and not self.shutdown_task.done():
            return
        self.shutdown_task = asyncio.create_task(
            self._delayed_shutdown(),
            name=f"frame-hub-shutdown:{self.source.source_id}",
        )

    async def _delayed_shutdown(self) -> None:
        try:
            await asyncio.sleep(GRACE_SEC)
        except asyncio.CancelledError:
            return
        if self.subscribers:
            return  # neuer Subscriber kam dazu
        task = self.reader_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.reader_task = None
        self.current_fps = 0.0
        self.current_w = 0
        self.current_h = 0
        logger.info("hub reader stopped for %s", self.source.source_id)


class FrameHub:
    """Singleton-Hub. Pro Prozess eine Instanz via :func:`get_default_hub`.

    Tests können eigene Instanzen mit Test-Sources erzeugen.
    """

    def __init__(self) -> None:
        self._channels: dict[str, _HubChannel] = {}
        self._lock = Lock()

    def _get_channel(self, source: "FrameSource") -> _HubChannel:
        with self._lock:
            ch = self._channels.get(source.source_id)
            if ch is None:
                ch = _HubChannel(source)
                self._channels[source.source_id] = ch
            return ch

    def is_reading(self, source_id: str) -> bool:
        """True wenn aktuell ein Reader-Task für diese Source läuft.
        Genutzt von :meth:`V4L2Source.is_available` als race-freier
        Verfügbarkeitscheck (statt cv2-Probe der mit dem Reader
        kollidieren würde)."""
        ch = self._channels.get(source_id)
        return ch is not None and ch.is_reading()

    async def subscribe(
        self,
        source: "FrameSource",
        *,
        name: str = "anon",
        fps: float = _DEFAULT_FPS,
        width: int = 0,
        height: int = 0,
    ) -> AsyncIterator["Frame"]:
        """Async-Iterator über Frames der Source. Beim Verlassen
        (break/return/Exception) wird der Subscriber automatisch
        deregistriert."""
        ch = self._get_channel(source)
        # Shutdown abbrechen, falls einer geplant ist
        if ch.shutdown_task and not ch.shutdown_task.done():
            ch.shutdown_task.cancel()
            ch.shutdown_task = None
        async with ch.lock:
            await ch._ensure_reader(fps, width, height)
        q: asyncio.Queue["Frame"] = asyncio.Queue(maxsize=4)
        ch.subscribers.append(q)
        logger.debug(
            "hub subscribe %s name=%s (subs=%d)",
            source.source_id, name, len(ch.subscribers),
        )
        try:
            while True:
                frame = await q.get()
                yield frame
        finally:
            if q in ch.subscribers:
                ch.subscribers.remove(q)
            logger.debug(
                "hub unsubscribe %s name=%s (subs=%d)",
                source.source_id, name, len(ch.subscribers),
            )
            if not ch.subscribers:
                ch._schedule_shutdown()

    async def snapshot(
        self,
        source: "FrameSource",
        *,
        width: int = 0,
        height: int = 0,
        timeout: float = 5.0,
    ) -> "Frame":
        """One-Shot: holt den nächsten Frame aus dem Hub-Stream und
        kehrt zurück. Wenn noch kein Reader läuft, wird einer mit der
        angeforderten Auflösung gestartet (und nach Grace-Period
        wieder beendet, falls keine weiteren Konsumenten kommen).

        ``timeout``: wenn binnen N Sekunden kein Frame kommt, RuntimeError.
        """
        async def _first_frame() -> "Frame":
            async for frame in self.subscribe(
                source, name="snapshot", fps=_DEFAULT_FPS, width=width, height=height,
            ):
                return frame
            raise RuntimeError("hub stream ended without yielding a frame")

        return await asyncio.wait_for(_first_frame(), timeout=timeout)

    async def shutdown(self) -> None:
        """Alle Reader stoppen — für Prozess-Exit / Tests."""
        with self._lock:
            channels = list(self._channels.values())
        for ch in channels:
            task = ch.reader_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass


# Modul-Singleton ────────────────────────────────────────────────────────

_default_hub: FrameHub | None = None
_default_lock = Lock()


def get_default_hub() -> FrameHub:
    """Singleton-Hub. Tests konstruieren eigene Instanzen direkt."""
    global _default_hub
    if _default_hub is not None:
        return _default_hub
    with _default_lock:
        if _default_hub is None:
            _default_hub = FrameHub()
        return _default_hub
