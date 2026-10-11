"""Live-Frames einer Watch-Session: das frischeste Frame, ein Ring der
letzten Sekunden und ein Takt-Iterator für die Auswerte-Zyklen.

Der ``frame_consumer`` des Watchers ``push``t jedes Frame vom FrameHub hinein;
die Zyklen (Dauer-VLM, kontinuierliche Gesichtserkennung, Edge-AI-Trigger)
lesen daraus, statt eigene Frame-Queues zu halten — so gibt es nie einen
Frame-Rückstau, die Pipeline hängt immer am Live-Bild.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from .frame_sources import Frame


class LiveFrames:
    """Frischestes Frame + Vorlauf-Ringpuffer einer Quelle (asyncio-single-
    threaded, daher ohne Lock)."""

    def __init__(self, preroll_sec: float) -> None:
        self._preroll_sec = preroll_sec
        self._preroll: deque["Frame"] = deque()
        self._latest: "Frame | None" = None
        self._seq = 0
        self._new_frame = asyncio.Event()

    @property
    def latest(self) -> "Frame | None":
        return self._latest

    def push(self, frame: "Frame") -> None:
        self._latest = frame
        self._seq += 1
        self._new_frame.set()
        if self._preroll_sec > 0:
            self._preroll.append(frame)
            while (
                frame.timestamp - self._preroll[0].timestamp
            ).total_seconds() > self._preroll_sec:
                self._preroll.popleft()

    def preroll_snapshot(self) -> list["Frame"]:
        """Die Frames der letzten ``preroll_sec`` Sekunden (älteste zuerst)."""
        return list(self._preroll)

    async def fresh_frames(self, min_interval_sec: float) -> AsyncIterator["Frame"]:
        """Liefert jeweils das AKTUELL frischeste Frame — nie dasselbe zweimal
        und höchstens alle ``min_interval_sec`` (Schutz vor GPU-Überlast).
        Wartet per Event auf ein neues Frame, kein Polling."""
        last_seq = -1
        last_yield: float | None = None
        while True:
            if self._seq == last_seq:
                self._new_frame.clear()
                await self._new_frame.wait()
            if last_yield is not None:
                wait = min_interval_sec - (time.monotonic() - last_yield)
                if wait > 0:
                    await asyncio.sleep(wait)
            # Erneut lesen — zwischen wait() und hier kann es neuer geworden sein.
            frame, last_seq = self._latest, self._seq
            if frame is None:
                continue
            last_yield = time.monotonic()
            yield frame
