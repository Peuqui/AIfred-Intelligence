"""SSOT für Kamera-Snap-Standbilder — frische Vollbild-Frames ohne RTSP-Lag.

Kapselt das Kamera-spezifische (aktuell die Reolink-Snap-API) an EINER
Stelle für die *on-demand*-Snap-Pfade: das Snapshot-Tool und die
Multipose-Live-Preview. Beide brauchen sporadisch ein frisches Standbild
und teilen sich hier Client-Verwaltung (Token-Cache: ein Login pro Quelle
statt Login-Sturm), Kanal-Auflösung aus der ``rtsp_cameras``-Config und
den Frame-Bau.

Auch der Vigilantia-Watcher nutzt den geteilten Client (Zustandsabfrage
und Dual-Lens-Snap via :func:`snap_pair`); sein Watch-Stop gibt ihn nicht
frei, die Session lebt mit dem Prozess (Logout über :func:`close_clients`
beim Shutdown).

Andere Kamera-Marken später: Dies ist der EINE Dispatch-Punkt. Ein
Config-Feld (z.B. ``driver``) plus eine zweite Client-Klasse mit
``snap()``/``aclose()`` genügen — die Aufrufer bleiben unverändert.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Snap-Clients pro KAMERA (Schlüssel host:api_port), NICHT pro Source:
# zwei Quellen desselben Geräts (Weitwinkel + Zoom) teilen sich EINEN
# Client → EINE Reolink-Session statt zwei (Session-Limit) und kein
# Login-Sturm beim Umschalten zwischen den Linsen. Geteilt von Snapshot-
# Tool und Multipose; via close_clients() freigegeben.
#
# Use-after-close-Schutz: ``snap_frames`` awaited mitten im Snap — ein
# paralleles ``close_clients`` (Multipose-Modal zu) würde den Client unterm
# laufenden Snap wegschließen; der Snap re-loggt sich dann ein und die
# Session leakt (Reolink-Session-Limit!). Darum zählt ``_in_use`` aktive
# Snap-Läufe pro Key; ``close_clients`` schiebt das Schließen bei aktiver
# Nutzung auf (``_close_pending``), der letzte Nutzer schließt. Alles läuft
# auf DEM Event-Loop (keine Threads) — plain dict/set reichen, kein Lock.
_clients: dict[str, Any] = {}
_in_use: dict[str, int] = {}
_close_pending: set[str] = set()


def _client_key(cam: dict[str, Any]) -> str:
    return f"{cam.get('host', '')}:{cam.get('api_port', 443)}"


def frame_from_snap(
    source_id: str, jpeg: bytes, timestamp: datetime | None = None,
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
) -> int | None:
    """Snap-Kanal aus der Kamera-Config — oder ``None``, wenn die Quelle
    nicht snap-fähig ist (keine ``cred`` / kein snapbarer Kanal).

    Das EIGENE ``snap_channel`` der Quelle hat IMMER Vorrang — bei zwei
    Quellen desselben Geräts (Weitwinkel + separate Zoom-Quelle) zeigt damit
    jede ihre eigene Linse; sonst snappten beide denselben Kanal.
    ``prefer_face=True`` (Gesichts-Enrollment, Multipose) zieht ``face_channel``
    nur als FALLBACK heran — für eine Single-Source-Dual-Lens-Kamera OHNE
    eigenes ``snap_channel`` (dann Zoom-Objektiv fürs Gesichtsdetail).
    Reihenfolge: ``snap_channel`` > (``face_channel`` bei prefer_face) >
    ``channel`` (bei ``ai_camera``)."""
    from .frame_sources.rtsp_source import find_camera_config
    cam = find_camera_config(source_id)
    if not cam or not cam.get("cred"):
        return None
    keys = ("snap_channel", "face_channel") if prefer_face else ("snap_channel",)
    for k in keys:
        if cam.get(k) is not None:
            return int(cam[k])
    if str(cam.get("profile")) == "ai_camera":
        return int(cam.get("channel", 0))
    return None


def get_client(source_id: str) -> Any | None:
    """Öffentlicher Zugriff auf den pro-Kamera GETEILTEN Snap-/AI-Client.

    EINE Session pro physischer Kamera (host:api_port) für ALLE Konsumenten
    — Snapshot-Tool, Multipose UND der Vigilantia-Watcher (Polling + Snap).
    So entsteht nie mehr als ein Login pro Kamera (Token wird durch
    Wiederverwendung refreshed); das verhindert das „max session"-Limit, das
    bei separaten Clients pro Subsystem auftrat. ``None`` ohne creds."""
    return _get_client(source_id)


def _get_client(source_id: str) -> Any | None:
    """Gecachten Snap-Client der Kamera (oder ``None``, wenn keine creds)."""
    entry = _get_client_with_key(source_id)
    return entry[1] if entry else None


def _get_client_with_key(source_id: str) -> tuple[str, Any] | None:
    """Wie ``_get_client``, zusätzlich mit dem Cache-Key (für ``_in_use``).
    Aktuell Reolink — hier wäre der Dispatch auf andere Marken."""
    from .frame_sources.rtsp_source import find_camera_config
    cam = find_camera_config(source_id)
    if not cam or not cam.get("cred"):
        return None
    key = _client_key(cam)
    client = _clients.get(key)
    if client is None:
        from .reolink_ai import ReolinkAIClient
        client = ReolinkAIClient(
            host=str(cam.get("host", "")),
            api_port=int(cam.get("api_port", 443)),
            cred=str(cam.get("cred", "")),
        )
        _clients[key] = client
    return key, client


async def snap_frames(
    source_id: str, n: int = 1, interval: float = 0.0, *,
    prefer_face: bool = False,
) -> list[Any] | None:
    """``n`` frische Snap-Frames der Quelle (volle Linsen-Auflösung).

    ``None`` = nicht snap-fähig ODER Fehler — der Caller fällt dann auf den
    RTSP-/Hub-Frame zurück. Bewusst weich: Die Kamera-API kann ausfallen
    (Session-Limit), während RTSP läuft; ein Substream-Foto schlägt kein
    Foto."""
    ch = resolve_snap_channel(source_id, prefer_face=prefer_face)
    if ch is None:
        return None
    entry = _get_client_with_key(source_id)
    if entry is None:
        return None
    key, client = entry
    try:
        async with _in_use_guard(key):
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


@asynccontextmanager
async def _in_use_guard(key: str | None) -> AsyncIterator[None]:
    """Markiert einen laufenden Snap-Lauf auf dem Client ``key``; schließt
    ein währenddessen angefordertes ``close_clients`` beim letzten Nutzer
    nach. ``None`` (Client nicht im Cache, z. B. ein Test-Fake): kein Schutz."""
    if key is None:
        yield
        return
    _in_use[key] = _in_use.get(key, 0) + 1
    try:
        yield
    finally:
        _in_use[key] -= 1
        if _in_use[key] <= 0:
            del _in_use[key]
            if key in _close_pending:
                # close_clients kam während des Snaps — jetzt nachholen.
                _close_pending.discard(key)
                await _close_key(key)


async def snap_pair(
    client: Any,
    fallback: Any,
    *,
    wide_channel: int,
    face_channel: int | None,
) -> tuple[Any, Any]:
    """Wide- und Zoom-Objektiv FRISCH und parallel snappen. Jedes Bild
    bekommt den Zeitstempel seines Eingangs bei AIfred (die Kamera liefert
    weder EXIF noch Millisekunden, nur ein sekundengenaues HTTP-Date).
    Returnt ``(wide_frame, zoom_frame)``. Beide Linsen bleiben immer eigene
    Bilder, auch wenn sie sich ähneln — im Zoom soll das Detail erkennbar sein.

    Schlägt der Wide-Snap fehl, bleibt ``fallback`` (mit Warnung) als Wide
    erhalten; ohne ``face_channel`` oder bei fehlgeschlagenem Zoom-Snap ist
    der Zoom dasselbe Objekt wie der Wide. Der Aufrufer erkennt einen
    Fehlschlag daran, dass ``wide_frame is fallback`` gilt."""

    async def _snap_stamped(channel: int) -> tuple[bytes, datetime]:
        jpeg = bytes(await client.snap(channel))
        return jpeg, datetime.now()

    snaps = [_snap_stamped(wide_channel)]
    if face_channel is not None:
        snaps.append(_snap_stamped(face_channel))
    key = next((k for k, c in _clients.items() if c is client), None)
    async with _in_use_guard(key):
        results = await asyncio.gather(*snaps, return_exceptions=True)

    def _stamped(
        result: tuple[bytes, datetime] | BaseException,
    ) -> tuple[bytes, datetime] | None:
        if isinstance(result, BaseException):
            logger.warning(
                "edge-ai snap failed for %s: %s", fallback.source_id, result,
            )
            return None
        return result

    wide = _stamped(results[0])
    wide_frame = (
        frame_from_snap(fallback.source_id, wide[0], wide[1])
        if wide else fallback
    )
    zoom_frame = wide_frame
    if face_channel is not None:
        zoom = _stamped(results[1])
        if zoom:
            zoom_frame = replace(
                frame_from_snap(fallback.source_id, zoom[0], zoom[1]),
                metadata={"kind": "rgb", "via": "snap",
                          "lens": "zoom", "face_channel": face_channel},
            )
    return wide_frame, zoom_frame


async def _close_key(key: str) -> None:
    client = _clients.pop(key, None)
    if client is None:
        return
    try:
        await client.aclose()
    except Exception as e:  # noqa: BLE001
        logger.warning("snap client close failed for %s: %s", key, e)


async def close_clients(source_id: str | None = None) -> None:
    """Snap-Clients schließen (Reolink ``aclose`` → Logout, gibt die
    Session frei) und aus dem Cache nehmen. Ohne Argument: alle. Wird z.B.
    beim Schließen des Multipose-Modals gerufen.

    Läuft gerade ein Snap auf dem Client, wird das Schließen aufgeschoben
    (der letzte Snap-Nutzer schließt) — sonst re-loggt sich der laufende
    Snap auf dem geschlossenen Client ein und die Session leakt."""
    if source_id:
        from .frame_sources.rtsp_source import find_camera_config
        cam = find_camera_config(source_id)
        keys = [_client_key(cam)] if cam else []
    else:
        keys = list(_clients)
    for key in keys:
        if _in_use.get(key, 0) > 0:
            _close_pending.add(key)
            continue
        await _close_key(key)
