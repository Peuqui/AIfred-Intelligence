"""Frame-Bus — asyncio Pub/Sub für Frame-Streams mit Backpressure.

Producer (Frame-Sources, die kontinuierlich Frames liefern) publishen
auf einen Topic (typischerweise ``source_id``). Consumer (Motion-Filter,
Face-Detect, VLM-Analyzer, Browser-Preview) subscriben pro Topic — jeder
bekommt seine eigene Queue mit eigener Backpressure-Politik.

Backpressure-Politik pro Subscriber: ``drop_oldest`` (Default, gut für
Realtime-Pipelines — neue Frames verdrängen alte) oder ``drop_newest``
(seltener, z.B. wenn der erste Frame eines Events erhalten bleiben soll).

Heartbeat: Sources können ihren letzten Publish-Zeitpunkt am Bus
hinterlassen, Consumer können das abfragen — nützlich um „Source liefert
nichts mehr" zu erkennen, ohne dass eine Queue voll laufen muss.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, AsyncIterator, Literal

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

DropPolicy = Literal["drop_oldest", "drop_newest"]


@dataclass
class _Subscription:
    """Interner Eintrag pro Subscriber. Nicht öffentlich."""

    topic: str
    queue: asyncio.Queue["Frame"]
    name: str
    drop_policy: DropPolicy
    dropped_count: int = 0


@dataclass
class BusStats:
    """Read-only Snapshot über den Bus-Zustand. Für UI/Diagnostik."""

    topics: list[str]
    subscribers_per_topic: dict[str, int]
    queue_lengths: dict[str, list[int]] = field(default_factory=dict)
    dropped_per_subscriber: dict[str, int] = field(default_factory=dict)
    last_publish: dict[str, datetime | None] = field(default_factory=dict)


class FrameBus:
    """In-Process Pub/Sub für Frames.

    Ein einziger Bus reicht für mehrere Sources — sie werden über das
    ``topic``-Feld unterschieden. Konvention: ``topic == source_id``.
    """

    def __init__(self) -> None:
        # topic → list of subscriptions
        self._subs: dict[str, list[_Subscription]] = defaultdict(list)
        # topic → wall-clock des letzten publish
        self._last_publish: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, frame: "Frame") -> None:
        """Publishe einen Frame an alle Subscriber des Topics.

        Bei vollem Subscriber-Queue gilt die Drop-Policy des Subscribers,
        Publishen blockiert NIE. Das ist die Kern-Backpressure-Garantie:
        eine langsame VLM-Pipeline kann nie eine schnelle Cam-Source
        ausbremsen.
        """
        async with self._lock:
            self._last_publish[topic] = frame.timestamp
            subs = list(self._subs.get(topic, []))
        # Lock vor dem Push freigeben — put_nowait() ist nicht-blockierend
        for sub in subs:
            self._deliver(sub, frame)

    def _deliver(self, sub: _Subscription, frame: "Frame") -> None:
        try:
            sub.queue.put_nowait(frame)
        except asyncio.QueueFull:
            sub.dropped_count += 1
            if sub.drop_policy == "drop_oldest":
                # Ältesten Frame entfernen und neuen einreihen.
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub.queue.put_nowait(frame)
                except asyncio.QueueFull:
                    # Theoretisch unmöglich nach get_nowait, aber abfangen.
                    logger.warning(
                        "FrameBus: queue still full after drop_oldest "
                        "(topic=%s, sub=%s)", sub.topic, sub.name
                    )
            # drop_newest: einfach den neuen verwerfen — nichts mehr zu tun.

    async def subscribe(
        self,
        topic: str,
        *,
        name: str = "anon",
        maxsize: int = 16,
        drop_policy: DropPolicy = "drop_oldest",
    ) -> AsyncIterator["Frame"]:
        """Subscribe auf ein Topic. Liefert einen async-Iterator.

        Beispiel::

            async for frame in bus.subscribe("cam/v4l2_0", name="vlm-analyzer"):
                await analyze(frame)

        Wird der Iterator verlassen (break/return/Exception), wird der
        Subscriber automatisch deregistriert.

        ``maxsize`` ist die Queue-Tiefe pro Subscriber. Bei kleiner Tiefe
        (z.B. 1 oder 2) sieht der Subscriber immer den frischesten Frame
        — bei größerer Tiefe ist Backlog möglich, was z.B. für eine Burst-
        Analyse mehrerer Frames hintereinander nützlich ist.
        """
        sub = _Subscription(
            topic=topic,
            queue=asyncio.Queue(maxsize=maxsize),
            name=name,
            drop_policy=drop_policy,
        )
        async with self._lock:
            self._subs[topic].append(sub)
        try:
            while True:
                frame = await sub.queue.get()
                yield frame
        finally:
            async with self._lock:
                if sub in self._subs.get(topic, []):
                    self._subs[topic].remove(sub)
                if not self._subs[topic]:
                    self._subs.pop(topic, None)

    def stats(self) -> BusStats:
        """Diagnostik-Snapshot. Hält das Lock NICHT — Werte können während
        des Lesens minimal driften, ist für eine Anzeige aber ok."""
        topics = list(self._subs.keys())
        subs_per_topic = {t: len(subs) for t, subs in self._subs.items()}
        queue_lengths = {
            t: [s.queue.qsize() for s in subs] for t, subs in self._subs.items()
        }
        dropped = {
            f"{t}#{s.name}": s.dropped_count
            for t, subs in self._subs.items()
            for s in subs
        }
        last_publish: dict[str, datetime | None] = {
            t: self._last_publish.get(t) for t in topics
        }
        return BusStats(
            topics=topics,
            subscribers_per_topic=subs_per_topic,
            queue_lengths=queue_lengths,
            dropped_per_subscriber=dropped,
            last_publish=last_publish,
        )

    def last_publish_age_seconds(self, topic: str) -> float | None:
        """Sekunden seit dem letzten Publish auf ``topic``. ``None`` wenn
        nie publisht wurde. Für Heartbeat-Checks: ``> N`` → Source tot."""
        last = self._last_publish.get(topic)
        if last is None:
            return None
        return (datetime.now() - last).total_seconds()


# Modul-globaler Default-Bus. Anwendungs-Code nutzt diesen, Tests können
# eigene Instanzen anlegen.
_default_bus = FrameBus()


def get_default_bus() -> FrameBus:
    """Den prozess-weiten Default-Bus holen."""
    return _default_bus
