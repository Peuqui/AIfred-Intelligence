"""Ereignisquellen für den Edge-AI-Trigger.

Die Kamera erkennt Person/Fahrzeug/Tier on-device. Eine Quelle meldet, wann
eine Klasse aktiv WIRD (steigende Flanke), und beantwortet, ob eine Klasse
gerade aktiv ist (der Burst läuft, solange die Person gemeldet wird). Die
Auswertung dahinter (``vision_edge_ai``) kennt nur dieses Protokoll — die
Abfrage der Reolink-API ist eine Implementierung, ein ONVIF-Abonnement eine
andere.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .config import (
    VISION_EDGE_AI_BACKOFF_MAX_DOUBLINGS,
    VISION_EDGE_AI_BACKOFF_MAX_SEC,
    VISION_EDGE_AI_EVENT_SOURCES,
)
from .reolink_ai import ReolinkAIError
from .vision_edge_onvif import OnvifPullPointSource
from .vision_watch_types import EdgeAiConfig

logger = logging.getLogger(__name__)


class EdgeAiEventSource(Protocol):
    """Ereignisquelle der Kamera-Erkennungen (``person``/``vehicle``/``animal``)."""

    async def wait_rising(self) -> list[str]:
        """Wartet, bis mindestens eine Klasse aktiv wird, und liefert die
        neu aktiven Klassen. Verbindungsfehler werden gemeldet (Warnung) und
        mit Backoff wiederholt — die Methode kehrt nur mit einer Flanke zurück."""
        ...

    async def is_active(self, cls: str) -> bool:
        """Ob die Klasse JETZT aktiv ist."""
        ...

    async def aclose(self) -> None:
        """Verbindung/Abonnement freigeben."""
        ...


class ReolinkPollSource:
    """Fragt ``GetAiState`` der Reolink-Kamera im festen Takt ab. Ein HTTP-GET
    auf persistenter Session (der frühere Session-Storm kam vom Re-Login, nicht
    vom State-Poll)."""

    def __init__(
        self, source_id: str, client: Any, channel: int, interval_sec: float,
    ) -> None:
        self._source_id = source_id
        self._client = client
        self._channel = channel
        self._interval = interval_sec
        self._prev: dict[str, bool] = {}
        self._fails = 0
        self._after_edge = False

    async def wait_rising(self) -> list[str]:
        if self._after_edge:
            self._after_edge = False
            await asyncio.sleep(self._interval)
        while True:
            try:
                state = await self._client.get_ai_state(self._channel)
            except ReolinkAIError as e:
                # Exponentielles Backoff: selbst wenn der Client-seitige
                # Re-Login mal nicht greift, hämmern wir die Kamera nicht
                # mit Logins zu (das war die Sturm-Ursache).
                self._fails += 1
                backoff = min(
                    self._interval * 2 ** min(self._fails, VISION_EDGE_AI_BACKOFF_MAX_DOUBLINGS),
                    VISION_EDGE_AI_BACKOFF_MAX_SEC,
                )
                logger.warning(
                    "edge-ai poll failed for %s (#%d, retry in %.0fs): %s",
                    self._source_id, self._fails, backoff, e,
                )
                await asyncio.sleep(backoff)
                continue
            self._fails = 0
            rising = [
                cls for cls, active in state.items()
                if active and not self._prev.get(cls, False)
            ]
            self._prev = state
            if rising:
                self._after_edge = True
                return rising
            await asyncio.sleep(self._interval)

    async def is_active(self, cls: str) -> bool:
        state = await self._client.get_ai_state(self._channel)
        return bool(state.get(cls))

    async def aclose(self) -> None:
        # Der Client ist pro Kamera geteilt und gehört vision_snap.
        return None


def create_event_source(
    source_id: str, edge: EdgeAiConfig, client: Any,
) -> EdgeAiEventSource:
    """Ereignisquelle gemäß ``EdgeAiConfig.event_source``. Eine unbekannte
    Angabe ist ein Konfigurationsfehler."""
    if edge.event_source == "onvif":
        return OnvifPullPointSource(source_id, edge.host, edge.onvif_port, edge.cred)
    if edge.event_source == "poll":
        return ReolinkPollSource(source_id, client, edge.channel, edge.poll_interval_sec)
    raise ValueError(
        f"edge_ai_event_source {edge.event_source!r} unbekannt "
        f"(erlaubt: {', '.join(VISION_EDGE_AI_EVENT_SOURCES)})"
    )
