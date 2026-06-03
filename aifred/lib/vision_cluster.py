"""Bulk-Cluster-Builder für die Vigilantia-Analyse.

Gruppiert Events nach Source + Time-Bucket + pHash-Ähnlichkeit, damit
das VLM bei der Bulk-Analyse nur einmal pro Cluster läuft.

Ablauf:
1. Events sortiert nach (source_id, timestamp) holen
2. Pro Source: gleitendes Fenster, neue pHashes mit Cluster-Mitgliedern
   vergleichen (Hamming-Distanz). Ähnlich → gleicher Cluster.
3. Time-Bucket-Cap: nach ``BUCKET_SECONDS`` Sekunden wird ein neuer
   Cluster aufgemacht, auch wenn die Frames noch ähnlich sind —
   sonst kriegt man ewige Cluster („Person sitzt 8 h vor Cam").

Deterministische Cluster-ID: ``{source-slug}-{bucket-ts}-{hash-prefix}``,
damit derselbe Frame bei wiederholtem Bulk-Run im selben Cluster
landet.
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
    source_id: str
    bucket_start: datetime
    phashes: list[int] = field(default_factory=list)
    member_ids: list[int] = field(default_factory=list)


def _source_slug(source_id: str) -> str:
    return source_id.replace("/", "_").replace(":", "_")


def _bucket_key(ts: datetime) -> str:
    """Time-Bucket-Anker — auf BUCKET_SECONDS-Grenze gerundet."""
    epoch = int(ts.timestamp())
    bucket = epoch - (epoch % BUCKET_SECONDS)
    return datetime.fromtimestamp(bucket).strftime("%Y%m%dT%H%M%S")


def cluster_events(
    events: list[dict[str, Any]],
    *,
    threshold: int = PHASH_THRESHOLD,
    bucket_seconds: int = BUCKET_SECONDS,
) -> dict[int, str]:
    """Berechnet pHash + Cluster-ID für jede Event-ID. Returnt Mapping
    ``event_id → cluster_id``.

    Liest die Frame-JPEGs von Disk (frame_path). Events ohne lesbares
    Frame bekommen ``cluster_id = ""`` (= individuell, kein Cluster).
    """
    # Pro Source einen offenen Cluster-Pool, dann durch Events laufen.
    active_clusters: dict[str, _Cluster] = {}
    result: dict[int, str] = {}

    for event in events:
        eid = int(event["id"])
        source = str(event["source_id"])
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
        if ph == 0:
            result[eid] = ""
            continue
        try:
            ts = datetime.fromisoformat(str(event["timestamp"]))
        except (TypeError, ValueError):
            ts = datetime.now()

        # Aktiven Cluster für die Source finden (falls einer offen ist
        # UND time-bucket-mäßig noch passt).
        cur = active_clusters.get(source)
        if cur and (ts - cur.bucket_start).total_seconds() > bucket_seconds:
            # Zeit-Fenster überschritten → neuer Cluster
            cur = None
            active_clusters.pop(source, None)

        # Prüfen ob dieser Frame zu einem Member im aktiven Cluster passt
        match: _Cluster | None = None
        if cur:
            for member_hash in cur.phashes:
                if hamming_distance(ph, member_hash) <= threshold:
                    match = cur
                    break

        if match is None:
            # Neuen Cluster aufmachen
            slug = _source_slug(source)
            bkey = _bucket_key(ts)
            cluster_id = f"{slug}-{bkey}-{ph & 0xFFFF:04x}"
            new_cluster = _Cluster(
                cluster_id=cluster_id,
                source_id=source,
                bucket_start=ts,
                phashes=[ph],
                member_ids=[eid],
            )
            active_clusters[source] = new_cluster
            result[eid] = cluster_id
        else:
            match.phashes.append(ph)
            match.member_ids.append(eid)
            result[eid] = match.cluster_id

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
