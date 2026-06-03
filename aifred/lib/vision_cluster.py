"""Clustering für die Vigilantia-Analyse — Source + Time-Bucket + pHash.

Gruppiert near-identische Frames zu einem Vorkommnis, damit das VLM nur
einmal pro Cluster läuft und Alerts/Abfragen pro Vorkommnis (nicht pro
Frame) dedupliziert werden.

Der Matching-Kern ist :class:`IncrementalClusterer` (zustandsbehaftet,
SSoT): pro Source ein offener Cluster; ein neuer pHash schließt sich an,
wenn die Hamming-Distanz zu einem Mitglied ≤ ``threshold`` ist und der
Time-Bucket (``bucket_seconds``) noch passt — sonst neuer Cluster. Die
Cluster-ID ``{source-slug}-{bucket-ts}-{hash-prefix}`` ist deterministisch.

* **Live**: der ``vision_watcher`` füttert den Clusterer beim Erkennen mit
  dem In-Memory-Frame und schreibt den ``cluster_id`` direkt ins Event.
* **Backfill**: :func:`cluster_events` liest Frames von Disk und clustert
  nur Events, die noch keinen ``cluster_id`` haben (Altbestand / Watcher
  war aus) — selber Kern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    VISION_CLUSTER_BUCKET_SECONDS as BUCKET_SECONDS,
    VISION_CLUSTER_PHASH_THRESHOLD as PHASH_THRESHOLD,
)
from .vision_phash import hamming_distance, phash_file
from .vision_store import VisionStore

logger = logging.getLogger(__name__)


@dataclass
class _Cluster:
    cluster_id: str
    bucket_start: datetime
    phashes: list[int] = field(default_factory=list)


def _source_slug(source_id: str) -> str:
    return source_id.replace("/", "_").replace(":", "_")


def _bucket_key(ts: datetime, bucket_seconds: int) -> str:
    """Time-Bucket-Anker — auf ``bucket_seconds``-Grenze gerundet."""
    epoch = int(ts.timestamp())
    bucket = epoch - (epoch % bucket_seconds)
    return datetime.fromtimestamp(bucket).strftime("%Y%m%dT%H%M%S")


class IncrementalClusterer:
    """Zustandsbehafteter per-Source-Open-Cluster-Matcher (SSoT fürs
    Clustering). ``assign`` nimmt einen *vorberechneten* pHash (Caller liest
    den Frame — Batch von Disk, Watcher aus dem Speicher) und liefert die
    deterministische ``cluster_id``, oder ``""`` bei ungültigem pHash (0).

    Annahme: pro Source kommen die Events zeitlich aufsteigend (Open-Cluster-
    Modell) — beim Watcher (Live) und beim Batch (ORDER BY timestamp) erfüllt.
    """

    def __init__(
        self,
        *,
        threshold: int = PHASH_THRESHOLD,
        bucket_seconds: int = BUCKET_SECONDS,
    ) -> None:
        self._threshold = threshold
        self._bucket_seconds = bucket_seconds
        self._active: dict[str, _Cluster] = {}

    def assign(self, source_id: str, timestamp: datetime, phash: int) -> str:
        if phash == 0:
            return ""
        cur = self._active.get(source_id)
        if cur and (timestamp - cur.bucket_start).total_seconds() > self._bucket_seconds:
            cur = None  # Time-Bucket überschritten → neuer Cluster
            self._active.pop(source_id, None)
        if cur is not None:
            for member_hash in cur.phashes:
                if hamming_distance(phash, member_hash) <= self._threshold:
                    cur.phashes.append(phash)
                    return cur.cluster_id
        cluster_id = (
            f"{_source_slug(source_id)}-{_bucket_key(timestamp, self._bucket_seconds)}"
            f"-{phash & 0xFFFF:04x}"
        )
        self._active[source_id] = _Cluster(cluster_id, timestamp, [phash])
        return cluster_id


def cluster_events(
    events: list[dict[str, Any]],
    *,
    threshold: int = PHASH_THRESHOLD,
    bucket_seconds: int = BUCKET_SECONDS,
) -> dict[int, str]:
    """Backfill: berechnet ``cluster_id`` für Events. Returnt Mapping
    ``event_id → cluster_id``.

    Events mit bereits gesetztem ``cluster_id`` (live geclustert) werden
    übernommen — kein erneutes Disk-Read, keine ID-Änderung. Für die übrigen
    wird der Frame von Disk gelesen und über den ``IncrementalClusterer``
    geclustert; ohne lesbaren Frame → ``""`` (individuell).
    """
    clusterer = IncrementalClusterer(threshold=threshold, bucket_seconds=bucket_seconds)
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
