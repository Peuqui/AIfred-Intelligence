"""SSOT für Kamera-Snap-Standbilder — frische Vollbild-Frames ohne RTSP-Lag.

Kapselt das Kamera-spezifische (aktuell die Reolink-Snap-API) an EINER
Stelle für die *on-demand*-Snap-Pfade: das Snapshot-Tool und die
Multipose-Live-Preview. Beide brauchen sporadisch ein frisches Standbild
und teilen sich hier Client-Verwaltung (Token-Cache: ein Login pro Quelle
statt Login-Sturm), Kanal-Auflösung aus der ``rtsp_cameras``-Config und
den Frame-Bau.

NICHT hier: der Vigilantia-Watcher. Sein Client ist an die Watch-Session
gebunden (lebt mit ihr, macht ZUSÄTZLICH das ``get_ai_state``-Polling und
einen parallelen Dual-Lens-Snap mit gemeinsamem Zeitstempel). Das ist ein
eigener Lebenszyklus, kein Duplikat — er teilt sich nur den ``Frame``-Bau
über :func:`frame_from_snap`.

Andere Kamera-Marken später: Dies ist der EINE Dispatch-Punkt. Ein
Config-Feld (z.B. ``driver``) plus eine zweite Client-Klasse mit
``snap()``/``aclose()`` genügen — die Aufrufer bleiben unverändert.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Snap-Clients pro Source (Token-Cache). Geteilt von Snapshot-Tool und
# Multipose; via close_clients() freigegeben (Reolink-Session-Hygiene).
_clients: dict[str, Any] = {}


def frame_from_snap(
    source_id: str, jpeg: bytes, timestamp: Optional[datetime] = None,
) -> Any:
    """``Frame`` aus Snap-JPEG bauen — eine Wahrheit für alle Snap-Pfade
    (auch der Watcher nutzt dies). ``width/height=0``: der Caller dekodiert
    bei Bedarf; ``via=snap`` markiert die Herkunft."""
    from .frame_sources.base import Frame
    return Frame(
        source_id=source_id,
        timestamp=timestamp or datetime.now(),
        image_bytes=jpeg,
        format="jpeg",
        width=0,
        height=0,
        metadata={"kind": "rgb", "via": "snap"},
    )


def resolve_snap_channel(
    source_id: str, *, prefer_face: bool = False,
) -> Optional[int]:
    """Snap-Kanal aus der Kamera-Config — oder ``None``, wenn die Quelle
    nicht snap-fähig ist (keine ``cred`` / kein snapbarer Kanal).

    ``prefer_face=True`` (Gesichts-Enrollment, Multipose): bevorzugt das
    Zoom-/Tele-Objektiv für Gesichtsdetail — ``face_channel`` >
    ``snap_channel`` > ``channel`` (bei ``ai_camera``).
    ``prefer_face=False`` (Quelle 1:1, Snapshot-Tool): das eigene Objektiv
    der Quelle — ``snap_channel`` > ``channel`` (bei ``ai_camera``)."""
    from .frame_sources.rtsp_source import find_camera_config
    cam = find_camera_config(source_id)
    if not cam or not cam.get("cred"):
        return None
    keys = ("face_channel", "snap_channel") if prefer_face else ("snap_channel",)
    for k in keys:
        if cam.get(k) is not None:
            return int(cam[k])
    if str(cam.get("profile")) == "ai_camera":
        return int(cam.get("channel", 0))
    return None


def _get_client(source_id: str) -> Any | None:
    """Gecachten Snap-Client der Quelle (oder ``None``, wenn keine creds).
    Aktuell Reolink — hier wäre der Dispatch auf andere Marken."""
    from .frame_sources.rtsp_source import find_camera_config
    cam = find_camera_config(source_id)
    if not cam or not cam.get("cred"):
        return None
    client = _clients.get(source_id)
    if client is None:
        from .reolink_ai import ReolinkAIClient
        client = ReolinkAIClient(
            host=str(cam.get("host", "")),
            api_port=int(cam.get("api_port", 443)),
            cred=str(cam.get("cred", "")),
        )
        _clients[source_id] = client
    return client


async def snap_frames(
    source_id: str, n: int = 1, interval: float = 0.0, *,
    prefer_face: bool = False,
) -> Optional[list[Any]]:
    """``n`` frische Snap-Frames der Quelle (volle Linsen-Auflösung).

    ``None`` = nicht snap-fähig ODER Fehler — der Caller fällt dann auf den
    RTSP-/Hub-Frame zurück. Bewusst weich: Die Kamera-API kann ausfallen
    (Session-Limit), während RTSP läuft; ein Substream-Foto schlägt kein
    Foto."""
    ch = resolve_snap_channel(source_id, prefer_face=prefer_face)
    if ch is None:
        return None
    client = _get_client(source_id)
    if client is None:
        return None
    try:
        frames: list[Any] = []
        for i in range(max(1, n)):
            if i > 0 and interval > 0:
                await asyncio.sleep(interval)
            jpeg = await client.snap(ch)
            frames.append(frame_from_snap(source_id, jpeg))
        return frames
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "snap failed for %s: %s — caller falls back to RTSP/hub",
            source_id, e,
        )
        return None


async def close_clients(source_id: Optional[str] = None) -> None:
    """Snap-Clients schließen (Reolink ``aclose`` → Logout, gibt die
    Session frei) und aus dem Cache nehmen. Ohne Argument: alle. Wird z.B.
    beim Schließen des Multipose-Modals gerufen."""
    targets = [source_id] if source_id else list(_clients)
    for sid in targets:
        client = _clients.pop(sid, None)
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning("snap client close failed for %s: %s", sid, e)
