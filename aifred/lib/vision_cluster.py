"""Clustering für die Vigilantia-Analyse — lückenbasiert pro Source.

Gruppiert Frames eines Vorkommnisses, damit das VLM nur einmal pro Cluster
läuft und Alerts/Abfragen pro Vorkommnis (nicht pro Frame) dedupliziert
werden.

Der Matching-Kern ist :class:`IncrementalClusterer` (zustandsbehaftet,
SSoT): pro Source ein offener Cluster. Ein neues Event schließt sich an,
solange die Lücke zum letzten Event ≤ ``gap_seconds`` ist UND die
Gesamtdauer ≤ ``max_seconds`` (Sicherheitsnetz gegen Dauerbewegung) —
sonst beginnt ein neues Vorkommnis. Die Cluster-ID
``{source-slug}-{start-ts}-{hash-prefix}`` ist deterministisch.

* **Live**: der ``vision_watcher`` füttert den Clusterer beim Erkennen mit
  dem In-Memory-Frame und schreibt den ``cluster_id`` direkt ins Event.
* **Backfill**: :func:`cluster_events` liest Frames von Disk und clustert
  nur Events, die noch keinen ``cluster_id`` haben (Altbestand / Watcher
  war aus) — selber Kern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    VISION_CLUSTER_GAP_SECONDS as GAP_SECONDS,
    VISION_CLUSTER_MAX_SECONDS as MAX_SECONDS,
)
from .vision_phash import phash_file
from .vision_store import VisionStore

logger = logging.getLogger(__name__)


@dataclass
class _Cluster:
    cluster_id: str
    start: datetime
    last_seen: datetime


def _source_slug(source_id: str) -> str:
    return source_id.replace("/", "_").replace(":", "_")


def _ts_key(ts: datetime) -> str:
    """Eindeutiger Anker je Vorkommnis — Startzeitpunkt sekundengenau."""
    return ts.strftime("%Y%m%dT%H%M%S")


class IncrementalClusterer:
    """Zustandsbehafteter per-Source-Open-Cluster-Matcher (SSoT fürs
    Clustering). ``assign`` nimmt einen *vorberechneten* pHash (Caller liest
    den Frame — Batch von Disk, Watcher aus dem Speicher) und liefert die
    deterministische ``cluster_id``, oder ``""`` bei ungültigem pHash (0).

    Lückenbasiert: solange die Bewegung mit ≤ ``gap_seconds`` Abstand
    weiterläuft, bleibt es dasselbe Vorkommnis; eine größere Lücke öffnet ein
    neues. ``max_seconds`` deckelt die Cluster-Dauer hart. Der pHash dient
    nur als ID-Suffix + Gültigkeitscheck (0 = unbrauchbar), nicht zum Splitten
    — sonst zerhackte eine bewegte Person nah an der Kamera ein Vorkommnis.

    Annahme: pro Source kommen die Events zeitlich aufsteigend — beim Watcher
    (Live) und beim Batch (ORDER BY timestamp) erfüllt.
    """

    def __init__(
        self,
        *,
        gap_seconds: float = GAP_SECONDS,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self._gap_seconds = gap_seconds
        self._max_seconds = max_seconds
        self._active: dict[str, _Cluster] = {}

    def assign(self, source_id: str, timestamp: datetime, phash: int) -> str:
        if phash == 0:
            return ""
        cur = self._active.get(source_id)
        if cur is not None:
            gap = (timestamp - cur.last_seen).total_seconds()
            age = (timestamp - cur.start).total_seconds()
            if gap <= self._gap_seconds and age <= self._max_seconds:
                cur.last_seen = timestamp
                return cur.cluster_id
        cluster_id = (
            f"{_source_slug(source_id)}-{_ts_key(timestamp)}-{phash & 0xFFFF:04x}"
        )
        self._active[source_id] = _Cluster(cluster_id, timestamp, timestamp)
        return cluster_id


def cluster_events(
    events: list[dict[str, Any]],
    *,
    gap_seconds: float = GAP_SECONDS,
    max_seconds: float = MAX_SECONDS,
) -> dict[int, str]:
    """Backfill: berechnet ``cluster_id`` für Events. Returnt Mapping
    ``event_id → cluster_id``.

    Events mit bereits gesetztem ``cluster_id`` (live geclustert) werden
    übernommen — kein erneutes Disk-Read, keine ID-Änderung. Für die übrigen
    wird der Frame von Disk gelesen und über den ``IncrementalClusterer``
    geclustert; ohne lesbaren Frame → ``""`` (individuell).
    """
    clusterer = IncrementalClusterer(gap_seconds=gap_seconds, max_seconds=max_seconds)
    result: dict[int, str] = {}

    for event in events:
        eid = int(event["id"])
        existing = str(event.get("cluster_id") or "")
        if existing:
            result[eid] = existing  # schon live geclustert → übernehmen
            continue
        frame_path = str(event.get("frame_path") or "")
        if not frame_path or not Path(frame_path).exists():
            result[eid] = ""
            continue
        try:
            ph = phash_file(frame_path)
        except Exception as e:  # noqa: BLE001
            logger.debug("phash failed for %s: %s", frame_path, e)
            result[eid] = ""
            continue
        try:
            ts = datetime.fromisoformat(str(event["timestamp"]))
        except (TypeError, ValueError):
            ts = datetime.now()
        result[eid] = clusterer.assign(str(event["source_id"]), ts, ph)

    return result


def write_clusters(
    store: VisionStore,
    mapping: dict[int, str],
) -> int:
    """Schreibt die berechneten cluster_ids in die DB. Returnt Anzahl
    geänderter Events (idempotent — wenn cluster_id schon gesetzt war
    und gleich bleibt, no-op)."""
    updated = 0
    for event_id, cluster_id in mapping.items():
        if store.set_event_cluster(event_id, cluster_id):
            updated += 1
    return updated
