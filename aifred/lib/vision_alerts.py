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
    try:
        rec = store.get_source(source_id) if store else None
        if rec and rec.get("display_name"):
            return str(rec["display_name"])
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
    """Emit a face detection as a proactive AlertEvent — but only while armed.
    Best-effort: never raises into the watcher's hot path."""
    if event_type not in _ALERT_EVENT_TYPES:
        return
    if not _vigilantia_armed():
        return
    try:
        from .alert_bus import AlertEvent, get_default_dispatcher
        ts = timestamp or datetime.now()
        alias = _source_alias(source_id, store)
        title, body = _compose(event_type, alias, name, ts)
        severity = "warning" if event_type in ("face_unknown", "face_unsure") else "info"
        ev = AlertEvent(
            producer="vision",
            category=event_type,
            source_id=source_id,
            severity=severity,
            title=title,
            body=body,
            # One alert per happening; fall back to source+type if unclustered.
            dedup_key=cluster_id or f"{source_id}:{event_type}",
            media=frame_path or None,
            timestamp=ts,
        )
        await get_default_dispatcher().emit(ev)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision alert emit failed for %s: %s", source_id, e)
