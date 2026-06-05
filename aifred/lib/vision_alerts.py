"""Vision → proactive alerts (the first producer for the alert pipeline).

When the watcher recognises a face while Vigilantia is armed, this emits a
neutral AlertEvent to the shared dispatcher (see alert_bus). The dispatcher's
central rules decide whether/where it actually goes. The dedup_key is the
event's cluster_id, so repeated frames of one happening collapse to one alert.

Kept out of the watcher core: the watcher just calls emit_face_alert().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VISION_SETTINGS_PATH = (
    Path(__file__).parent.parent / "plugins" / "tools" / "vision" / "settings.json"
)

# Only these carry a "someone is here" meaning worth alerting on.
_ALERT_EVENT_TYPES = {"face_known", "face_unsure", "face_unknown"}


def _vigilantia_armed() -> bool:
    """Read the master arm flag (SSoT in the vision plugin settings)."""
    try:
        cfg = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
        return bool(cfg.get("vigilantia_armed", False))
    except (OSError, json.JSONDecodeError):
        return False


def _source_alias(source_id: str, store: Any) -> str:
    """Anzeigename der Kamera für den Alert. Geht über die SSoT
    :meth:`VisionStore.source_label` (Alias > display_name > source_id) —
    derselbe Name wie in den Event-Panels und im Zonen-Editor."""
    try:
        from .vision_store import VisionStore
        rec = store.get_source(source_id) if store else None
        if rec:
            return VisionStore.source_label(rec)
    except Exception:  # noqa: BLE001
        pass
    return source_id


def _compose(event_type: str, alias: str, name: str, ts: datetime) -> tuple[str, str]:
    """User-facing alert text (German — goes to the user's phone)."""
    when = ts.strftime("%H:%M")
    if event_type == "face_known":
        title = f"👤 {name or 'Bekannte Person'} erkannt"
    elif event_type == "face_unsure":
        title = f"👤 Mögliche Person ({name})" if name else "👤 Unsichere Erkennung"
    else:
        title = "🚨 Unbekannte Person erkannt"
    return title, f"{alias} · {when}"


async def _emit(
    *,
    source_id: str,
    category: str,
    severity: str,
    title: str,
    body: str,
    dedup_key: str,
    frame_path: str,
    timestamp: datetime,
) -> None:
    """Build + dispatch one AlertEvent. Best-effort — never raises into the
    watcher's hot path. The shared dispatcher's rules decide where it goes."""
    try:
        from .alert_bus import AlertEvent, get_default_dispatcher
        ev = AlertEvent(
            producer="vision",
            category=category,
            source_id=source_id,
            severity=severity,
            title=title,
            body=body,
            dedup_key=dedup_key,
            media=frame_path or None,
            timestamp=timestamp,
        )
        await get_default_dispatcher().emit(ev)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision alert emit failed for %s: %s", source_id, e)


async def emit_face_alert(
    *,
    source_id: str,
    event_type: str,
    frame_path: str,
    cluster_id: str,
    name: str = "",
    timestamp: datetime | None = None,
    store: Any = None,
) -> None:
    """Emit a face detection as a proactive AlertEvent — but only while armed."""
    if event_type not in _ALERT_EVENT_TYPES:
        return
    if not _vigilantia_armed():
        return
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    title, body = _compose(event_type, alias, name, ts)
    severity = "warning" if event_type in ("face_unknown", "face_unsure") else "info"
    await _emit(
        source_id=source_id,
        category=event_type,
        severity=severity,
        title=title,
        body=body,
        # One alert per happening; fall back to source+type if unclustered.
        dedup_key=cluster_id or f"{source_id}:{event_type}",
        frame_path=frame_path,
        timestamp=ts,
    )


async def emit_person_alert(
    *,
    source_id: str,
    frame_path: str,
    cluster_id: str,
    count: int = 1,
    timestamp: datetime | None = None,
    store: Any = None,
) -> None:
    """Emit a YOLO person detection (whole body) as a proactive AlertEvent —
    but only while armed. Coarser than faces: "a person is present", even
    with no recognisable face."""
    if not _vigilantia_armed():
        return
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    when = ts.strftime("%H:%M")
    title = "🚶 Person erkannt" if count == 1 else f"🚶 {count} Personen erkannt"
    await _emit(
        source_id=source_id,
        category="person",
        severity="warning",
        title=title,
        body=f"{alias} · {when}",
        # One alert per happening; fall back to source if unclustered.
        dedup_key=cluster_id or f"{source_id}:person",
        frame_path=frame_path,
        timestamp=ts,
    )


# Anzeigetitel je Objektklasse (SSoT für die Edge-AI-Objekt-Alerts).
_OBJECT_ALERT_TITLES = {
    "vehicle": "🚗 Fahrzeug erkannt",
    "animal": "🐾 Tier erkannt",
}


async def emit_object_alert(
    *,
    source_id: str,
    object_type: str,
    frame_path: str,
    cluster_id: str,
    timestamp: datetime | None = None,
    store: Any = None,
) -> None:
    """Emit an edge-AI object detection (vehicle/animal) as a proactive
    AlertEvent — but only while armed. Diese Klassen liefert die On-Device-
    KI der Kamera; AIfreds eigene YOLO-Pipeline klassifiziert sie nicht."""
    if not _vigilantia_armed():
        return
    title = _OBJECT_ALERT_TITLES.get(object_type)
    if title is None:
        return
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    when = ts.strftime("%H:%M")
    await _emit(
        source_id=source_id,
        category=object_type,
        severity="info",
        title=title,
        body=f"{alias} · {when}",
        dedup_key=cluster_id or f"{source_id}:{object_type}",
        frame_path=frame_path,
        timestamp=ts,
    )
