"""Vision endpoints: snapshot/stream/events, face enrollment/Personarium,
event frames, zone masks and motion tuning."""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ..logging_utils import log_message
# resolve_source_resolution lives in vision_utils.py — shared with the
# plugin tools so popup-UI resolution and tool-call resolution match.
from ..vision_utils import resolve_source_resolution as _resolve_resolution
from .app import api_app
from .schemas import SystemActionResponse


@api_app.get("/vision/snapshot/{source_id:path}", tags=["Vision"])
async def vision_snapshot_endpoint(
    source_id: str, width: int = 0, height: int = 0
) -> Response:
    """Liefert einen frischen JPEG-Snapshot der genannten Frame-Source.

    Geht durch den FrameHub: wenn schon ein Stream / Watcher läuft,
    bekommt der Snapshot den nächsten Frame aus dem laufenden Loop —
    kein zweiter V4L2-Open. Wenn niemand sonst zugreift, startet der
    Hub einen kurzen Reader und beendet ihn nach Grace-Period.
    """
    from ..frame_hub import get_default_hub
    from ..frame_sources import get as get_source

    src = get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
    w, h = _resolve_resolution(source_id, width, height)
    hub = get_default_hub()
    try:
        frame = await hub.snapshot(src, width=w, height=h)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"snapshot failed: {e}") from e
    return Response(
        content=frame.image_bytes,
        media_type=f"image/{frame.format}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# Boundary string used in the multipart MJPEG response. Must match the
# `boundary=` parameter in the Content-Type header exactly.
_MJPEG_BOUNDARY = b"--frame"


def _encode_mjpeg_chunk(frame: Any) -> bytes:
    return bytes(
        _MJPEG_BOUNDARY
        + b"\r\nContent-Type: image/"
        + frame.format.encode()
        + b"\r\nContent-Length: "
        + str(len(frame.image_bytes)).encode()
        + b"\r\n\r\n"
        + frame.image_bytes
        + b"\r\n"
    )


class _MotionOverlay:
    """Brennt die rohe MOG2-Foreground-Maske halbtransparent blau in jeden
    Frame — fürs Bewegungs-Tuning im Zonen-Editor.

    Eigener Detector-State pro Stream (kein globaler Zustand über Requests),
    mit denselben MOG2-Params wie der Watcher, damit die sichtbaren Pixel
    repräsentativ sind. BEWUSST ohne zone_mask: der Nutzer soll auch das
    Rauschen sehen, das er gerade wegmaskieren will. Die Prozent-Auswertung
    (gegen die gemalte Maske) macht der Browser anhand der blauen Pixel —
    der Server bleibt zustandslos.
    """

    # Gesättigtes Blau (BGR), das nach dem Blend immer B >> R und B >> G hat;
    # der Browser erkennt die Foreground-Pixel daran zuverlässig wieder.
    _BLUE = (255.0, 40.0, 0.0)
    _ALPHA = 0.6  # Anteil Blau im Blend (Rest: Originalbild)

    def __init__(self) -> None:
        import numpy as np

        from ..vision_filters import MotionDetector
        from ..vision_watcher import WatchConfig

        cfg = WatchConfig()
        self._det = MotionDetector(
            history=cfg.motion_history_frames,
            var_threshold=cfg.motion_var_threshold,
            warmup_frames=cfg.motion_warmup_frames,
            return_mask=True,
        )
        self._np = np

    def apply(self, frame: Any) -> Any:
        import dataclasses

        import cv2

        np = self._np
        mask = self._det.process(frame).foreground_mask
        if mask is None or not mask.any():
            return frame
        img = cv2.imdecode(np.frombuffer(frame.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return frame
        if mask.shape[:2] != img.shape[:2]:
            mask = cv2.resize(
                mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST
            )
        sel = mask > 0
        blue = np.array(self._BLUE, dtype=np.float32)
        img[sel] = (
            (1.0 - self._ALPHA) * img[sel].astype(np.float32) + self._ALPHA * blue
        ).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return frame
        return dataclasses.replace(frame, image_bytes=buf.tobytes(), format="jpeg")


async def _mjpeg_stream(
    source: Any, fps: float, width: int = 0, height: int = 0,
    motion_overlay: bool = False,
) -> Any:
    """Async generator yielding MJPEG frames (multipart/x-mixed-replace).

    Geht durch den FrameHub — egal wie viele Browser-Tabs, Watcher
    oder Snapshots gerade aktiv sind, der Hub hält genau einen
    V4L2-Reader pro Source offen. Vorher gab es zwei Pfade (direkter
    source.stream vs. bus.subscribe), die je nach Watcher-Zustand
    umgeschaltet wurden und beim Toggle blackouts produzierten — der
    Hub löst das strukturell.

    ``fps`` ist die vom Browser gewünschte Anzeige-Rate. Der Hub
    selbst läuft mit ``max(fps aller Subscriber)``; dieser Stream
    drosselt clientseitig auf das vom User angeforderte Tempo —
    sonst wirkt das Bildrate-Dropdown nicht, weil der Watcher den
    Hub auf 10 fps zwingt und alle Frames an den Browser durch-
    gereicht werden.
    """
    import time
    from ..frame_hub import get_default_hub

    hub = get_default_hub()
    # Drossel-Intervall in Sekunden. Bei fps <= 0 (Manual-Modus) wird
    # dieser Pfad eh nicht genutzt — der Browser holt einzeln über
    # /vision/snapshot.
    interval = 1.0 / max(0.01, float(fps))
    last_emit = 0.0
    overlay = _MotionOverlay() if motion_overlay else None
    async for frame in hub.subscribe(
        source, name="mjpeg-live-preview", fps=fps, width=width, height=height,
    ):
        now = time.monotonic()
        if now - last_emit < interval:
            continue
        last_emit = now
        if overlay is not None:
            frame = overlay.apply(frame)
        yield _encode_mjpeg_chunk(frame)


@api_app.get("/vision/stream/{source_id:path}", tags=["Vision"])
async def vision_stream_endpoint(
    source_id: str, fps: float = 1.0, width: int = 0, height: int = 0,
    overlay: str = "",
) -> Any:
    """MJPEG-Live-Stream der genannten Frame-Source.

    Browser-side: einfach ``<img src="/api/vision/stream/cam/v4l2_0?fps=2">``.
    Der Server hält die Connection offen und liefert kontinuierlich
    JPEGs als multipart/x-mixed-replace. Bei ``fps=0`` setzen wir
    intern auf 1.0 als Minimum — wer wirklich manuelle Frames will,
    nutzt /vision/snapshot.

    Akzeptiert ``fps`` zwischen 0.1 und 30. Werte außerhalb werden
    geklammert. ``width``/``height`` überschreiben den persistierten
    Per-Source-Default; ``0/0`` fällt auf vision_store zurück (gleiche
    Resolve-Logik wie beim Snapshot-Endpoint).

    ``overlay=motion`` brennt die rohe Bewegungsmaske halbtransparent blau
    ein (Zonen-Editor-Tuning) — eigener MOG2-State pro Stream, zustandslos.
    """
    from starlette.responses import StreamingResponse
    from ..frame_sources import get as get_source

    src = get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"unknown source: {source_id}")
    # NOTE: deliberately NOT calling src.is_available() here. That
    # method opens cv2.VideoCapture to probe, which races against the
    # previous stream's release() during a stream-switch — the probe
    # fails with "can't open camera by index", we'd return 503, the
    # browser sees an error and shows black. The actual open in
    # source.stream() has a retry loop and handles the cleanup latency
    # properly. Letting the stream attempt the open is the right move.
    # Clamp + sanitize
    fps = max(0.1, min(30.0, float(fps) if fps else 1.0))
    w, h = _resolve_resolution(source_id, width, height)
    return StreamingResponse(
        _mjpeg_stream(src, fps, w, h, motion_overlay=(overlay == "motion")),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY.decode().lstrip('-')}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            # Tell nginx (and any other reverse proxy that honours this
            # header) to NOT buffer the response. Without it, nginx's
            # default 4 KB proxy_buffer holds back frames until the
            # buffer fills or times out — which is what makes 2-second
            # streams take 4 seconds to deliver and fresh streams
            # show black for a beat.
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
    )


@api_app.get("/vision/events/{source_id:path}", tags=["Vision"])
async def vision_events_endpoint(source_id: str) -> Any:
    """Server-Sent-Events stream of VLM analysis events for a source.

    Each line is a JSON object emitted by the watcher's continuous-VLM
    path:

        data: {"type":"vlm_analysis","timestamp":"…","description":"…", …}

    The browser opens this with ``new EventSource(...)`` and the
    teleprompter overlay appends each event as it arrives. Stream
    runs until the client disconnects; the watcher itself is
    started/stopped separately via the start/stop endpoints (or via
    the tool plugin's vision_start_watch / vision_stop_watch).
    """
    from starlette.responses import StreamingResponse
    from ..vision_event_bus import subscribe

    import json as _json

    async def _gen() -> Any:
        # Initial comment-line so the EventSource sees a successful
        # 200 and any reverse proxy flushes its first response chunk
        # (some proxies wait for the first byte before forwarding).
        yield b":\n\n"
        async for event in subscribe(source_id):
            payload = _json.dumps(event, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# Face Enrollment
# ============================================================


class FaceEnrollRequest(BaseModel):
    """Body fürs Inline-Enroll aus dem Live-Vorschau-Popup. Embedding
    kommt als base64-encoded float32-Array (512 dim für InsightFace
    buffalo_l), wie es im SSE-Event mitgegeben wird."""
    name: str = Field(..., min_length=1, description="Name der Person")
    source_id: str = Field(..., description="Frame-Source-ID, aus der das Embedding stammt")
    embedding_b64: str = Field(..., description="Base64-encodierter float32 numpy-array")


class FaceEnrollResponse(BaseModel):
    success: bool
    face_id: int
    name: str
    is_new: bool


@api_app.post("/vision/face/enroll", response_model=FaceEnrollResponse, tags=["Vision"])
async def vision_face_enroll(request: FaceEnrollRequest) -> FaceEnrollResponse:
    """Enrollen einer neuen Identity (oder weiteres Sample für eine
    bestehende). Wird vom Inline-Button bei ``face_unknown``-Zeilen im
    Live-Vorschau-Popup aufgerufen.

    Idempotent zur Name-Dedup: wenn schon eine ``face_id`` mit dem
    Namen existiert, wird das Embedding als zusätzliches Sample
    angefügt — kein zweiter Datensatz. So kann der User beim
    erneuten ``+ taggen`` einer bekannten Person das Modell mit
    weiteren Posen anreichern.
    """
    import base64 as _b64
    import numpy as _np
    from ..vision_store import VisionStore

    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="empty name")

    # Embedding dekodieren
    try:
        emb_bytes = _b64.b64decode(request.embedding_b64, validate=True)
        embedding = _np.frombuffer(emb_bytes, dtype=_np.float32)
        if embedding.size == 0:
            raise ValueError("empty embedding")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"invalid embedding: {e}"
        ) from e

    store = VisionStore()
    is_new = store.get_face_by_name(name) is None
    face_id = store.get_or_create_face(name, enrolled_by="popup")

    try:
        store.add_embedding(face_id, embedding, quality_score=1.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"add_embedding failed: {e}") from e

    log_message(
        f"🧑 Face enrolled: name='{name}' face_id={face_id} is_new={is_new} "
        f"source={request.source_id}"
    )

    # Alle lebenden Recognizer im Prozess informieren, damit der nächste
    # Frame die neue Identity sofort erkennt (eine frische Instanz zu
    # invalidieren wäre wirkungslos — siehe bump_enrollment_epoch).
    from ..vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()

    return FaceEnrollResponse(
        success=True, face_id=face_id, name=name, is_new=is_new,
    )


# ============================================================
# Personarium — Identity-Verwaltung
# ============================================================


class FaceSummary(BaseModel):
    """Eine Identity-Zeile fürs Personarium."""
    id: int
    name: str
    embedding_count: int
    last_seen: str
    crop_url: str
    notes: str = ""


class FaceSummaryList(BaseModel):
    faces: List[FaceSummary]


class FaceEvent(BaseModel):
    id: int
    timestamp: str
    source_id: str
    crop_url: str
    confidence: float
    confidence_band: str


class FaceDetailResponse(BaseModel):
    id: int
    name: str
    notes: str
    embedding_count: int
    events: List[FaceEvent]


class FaceRenameRequest(BaseModel):
    name: str = Field(..., min_length=1)


@api_app.get("/vision/face/list", response_model=FaceSummaryList, tags=["Vision"])
async def vision_face_list() -> FaceSummaryList:
    """Liste aller enrolled Identitäten mit Avatar (letzter Crop),
    Anzahl Embeddings und letzter Sichtung."""
    from ..vision_store import VisionStore
    store = VisionStore()
    rows = store.list_faces_with_summary()
    return FaceSummaryList(faces=[FaceSummary(**r) for r in rows])


@api_app.get(
    "/vision/face/{face_id}/details",
    response_model=FaceDetailResponse,
    tags=["Vision"],
)
async def vision_face_details(face_id: int) -> FaceDetailResponse:
    """Detail-View einer Identity: alle face-Events mit Crops."""
    from ..vision_store import VisionStore
    store = VisionStore()
    face = store.get_face_by_id(face_id)
    if not face:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    events = store.list_face_events(face_id, limit=50)
    emb_count = len(store.list_embeddings(face_id))
    return FaceDetailResponse(
        id=face_id,
        name=str(face["name"]),
        notes=str(face.get("notes") or ""),
        embedding_count=emb_count,
        events=[FaceEvent(**e) for e in events],
    )


@api_app.post(
    "/vision/face/{face_id}/rename",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_face_rename(face_id: int, request: FaceRenameRequest) -> SystemActionResponse:
    """Identity umbenennen. 409 wenn der neue Name schon vergeben ist."""
    from ..vision_store import VisionStore
    store = VisionStore()
    try:
        ok = store.rename_face(face_id, request.name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    return SystemActionResponse(success=True, message=f"renamed to '{request.name}'")


@api_app.delete(
    "/vision/face/{face_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_face_delete(face_id: int) -> SystemActionResponse:
    """Komplette Identity löschen: faces-Row + alle Embeddings + face_id-
    Refs in events auf NULL. Crops auf Disk bleiben — werden vom
    Cleanup-Task per TTL aufgeräumt."""
    from ..vision_store import VisionStore
    store = VisionStore()
    face = store.get_face_by_id(face_id)
    if not face:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found")
    info = store.delete_face_with_assets(face_id)
    # Alle lebenden Recognizer neu laden lassen, damit die Identity verschwindet
    from ..vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()
    return SystemActionResponse(
        success=True,
        message=f"deleted face {face_id} ({info['embeddings_deleted']} embeddings)",
    )


@api_app.delete(
    "/vision/face/embedding/{embedding_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_embedding_delete(embedding_id: int) -> SystemActionResponse:
    """Einzelnes Embedding löschen — nützlich um schlechte
    Enrollment-Samples (falsche Pose, schlechtes Licht) rauszuwerfen
    ohne die ganze Identity zu kippen."""
    from ..vision_store import VisionStore
    store = VisionStore()
    ok = store.delete_embedding(embedding_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"embedding {embedding_id} not found")
    from ..vision_filters.face_recognize import bump_enrollment_epoch
    bump_enrollment_epoch()
    return SystemActionResponse(success=True, message="embedding deleted")


@api_app.get("/vision/frame", tags=["Vision"])
async def vision_frame(id: int, w: int = 0, zoom: int = 0) -> Response:
    """Gespeichertes Event-Bild als JPEG ausliefern.

    ``id`` ist die Event-ID, ``w`` ein optionaler Ziel-Breiten-Parameter:
    >0 skaliert serverseitig herunter (Casus-Thumbnail nutzt w=80, das
    Bild-Modal lädt ohne ``w`` in Vollauflösung). ``zoom=1`` liefert statt
    des Weitwinkels den Tele-Snap des Events (404, wenn keiner existiert).
    Kein extra Auth-Gate — Zugriff ist von außen ohnehin durch den
    Basic-Auth-Reverse-Proxy und lokal durch Maschinenzugang geschützt
    (gleiches Niveau wie die face-crop-Auslieferung unter /_upload)."""
    from ..vision_store import VisionStore
    path_str = VisionStore().get_event_frame_path(id, zoom=bool(zoom))
    if not path_str:
        raise HTTPException(status_code=404, detail=f"no frame for event {id}")
    frame_file = Path(path_str)
    if not frame_file.is_file():
        raise HTTPException(status_code=404, detail="frame file missing on disk")
    data = frame_file.read_bytes()
    if w and w > 0:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None and arr.shape[1] > w:
            scale = w / float(arr.shape[1])
            arr = cv2.resize(
                arr, (w, max(1, int(arr.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
            ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                data = buf.tobytes()
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Pfad der vision-settings.json (Heimat der zone_masks).
_VISION_SETTINGS_PATH = (
    Path(__file__).resolve().parents[2] / "plugins/tools/vision/settings.json"
)


class ZoneMaskPayload(BaseModel):
    """Speicher-Payload des Zonen-Editors."""
    source_id: str
    cols: int = 0
    rows: int = 0
    cells: str = ""          # cols*rows Ziffern aus {0,1,2,3}
    enabled: bool = True     # Schnell-Toggle: aus = Maske bleibt, wirkt nicht


@api_app.get("/vision/zone-mask", tags=["Vision"])
async def get_zone_mask(source_id: str) -> Dict[str, Any]:
    """Gespeicherte Zonen-Maske einer Quelle (für den Editor zum Laden)."""
    import json
    try:
        data = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    entry = (data.get("zone_masks") or {}).get(source_id)
    if not isinstance(entry, dict):
        return {"exists": False, "source_id": source_id}
    return {
        "exists": True,
        "source_id": source_id,
        "cols": entry.get("cols", 0),
        "rows": entry.get("rows", 0),
        "cells": entry.get("cells", ""),
        "enabled": entry.get("enabled", True),
    }


@api_app.post(
    "/vision/zone-mask", response_model=SystemActionResponse, tags=["Vision"]
)
async def save_zone_mask(payload: ZoneMaskPayload) -> SystemActionResponse:
    """Zonen-Maske einer Quelle speichern (oder löschen bei mode=off /
    leerem Raster). Schreibt in zone_masks der vision-settings.json.

    Hinweis: Der Watcher lädt die Maske beim (Neu-)Start einer Quelle —
    eine geänderte Maske greift erst nach Re-Arm/Neustart der Quelle."""
    import json
    try:
        data = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    masks = data.get("zone_masks")
    if not isinstance(masks, dict):
        masks = {}
    painted = any(ch != "0" for ch in payload.cells)
    if not painted:
        # Nichts gemalt (alles 0) → Eintrag löschen.
        masks.pop(payload.source_id, None)
        msg = "zone mask cleared"
    else:
        if (
            payload.cols <= 0
            or payload.rows <= 0
            or len(payload.cells) != payload.cols * payload.rows
            or any(ch not in "0123" for ch in payload.cells)
        ):
            raise HTTPException(status_code=400, detail="invalid grid")
        masks[payload.source_id] = {
            "cols": payload.cols,
            "rows": payload.rows,
            "cells": payload.cells,
            "enabled": payload.enabled,
        }
        msg = "zone mask saved" if payload.enabled else "zone mask saved (disabled)"
    data["zone_masks"] = masks
    _VISION_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Live in den laufenden Watcher übernehmen — greift sofort, kein
    # Re-Arm/Neustart der Quelle nötig. No-op wenn die Quelle nicht läuft.
    try:
        from ..vision_watcher import get_default_watcher
        get_default_watcher().reload_zone_mask(payload.source_id)
    except Exception as e:  # noqa: BLE001
        log_message(f"⚠️ zone mask live-reload failed: {e}")
    return SystemActionResponse(success=True, message=msg)


class MotionMinPayload(BaseModel):
    """Speicher-Payload des Bewegungs-Schwellwert-Sliders im Zonen-Editor."""
    source_id: str
    motion_min_area_ratio: float    # 0.001–0.5 (Anteil bewegter Pixel)


@api_app.get("/vision/motion-min", tags=["Vision"])
async def get_motion_min(source_id: str) -> Dict[str, Any]:
    """Bewegungs-Schwellwert einer Quelle (für den Editor-Slider zum Laden).

    Fällt auf den globalen Default (0.02) zurück, wenn die Quelle (noch)
    keinen eigenen Wert hat."""
    from ..vision_store import VisionStore
    stored = VisionStore().get_source(source_id) or {}
    mma = (stored.get("settings") or {}).get("motion_min_area_ratio")
    value = (
        float(mma)
        if isinstance(mma, (int, float)) and 0.001 <= mma <= 0.5
        else 0.02
    )
    return {"source_id": source_id, "motion_min_area_ratio": value}


@api_app.post(
    "/vision/motion-min", response_model=SystemActionResponse, tags=["Vision"]
)
async def save_motion_min(payload: MotionMinPayload) -> SystemActionResponse:
    """Bewegungs-Schwellwert einer Quelle speichern (Editor-Slider beim
    Loslassen). Greift live im laufenden Watcher — kein Re-Arm nötig."""
    from ..vision_store import VisionStore
    mma = max(0.001, min(0.5, float(payload.motion_min_area_ratio)))
    VisionStore().patch_source_settings(
        payload.source_id, {"motion_min_area_ratio": mma}
    )
    try:
        from ..vision_watcher import get_default_watcher
        get_default_watcher().reload_motion_min(payload.source_id, mma)
    except Exception as e:  # noqa: BLE001
        log_message(f"⚠️ motion_min live-reload failed: {e}")
    return SystemActionResponse(success=True, message="motion min saved")


@api_app.get("/vision/zone-editor", tags=["Vision"])
async def zone_editor_page(source_id: str = "") -> HTMLResponse:
    """Standalone JS-Canvas-Zonen-Editor (HTML). Über /api ausgeliefert,
    damit er unabhängig vom frontend_path-Prefix erreichbar ist; die
    source_id kommt als Query-Param (das JS liest sie aus location.search)."""
    import json
    from ..formatting import format_number
    from ..i18n import TranslationManager, t
    editor = (
        Path(__file__).resolve().parents[3] / "assets" / "zone_editor.html"
    )
    try:
        html = editor.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"editor missing: {e}") from e
    # Kamera-Anzeigename (Alias > display_name > source_id) auflösen, damit
    # der Header den Standort zeigt statt der rohen source_id.
    src_name = ""
    if source_id:
        try:
            from ..vision_store import VisionStore
            src_name = VisionStore().source_labels().get(source_id, "")
        except Exception:  # noqa: BLE001
            src_name = ""
    # Übersetzungen in der aktuellen UI-Sprache als window.T injizieren —
    # zentral aus i18n.py, kein Duplikat im JS. Scannt alle zone_editor_*-Keys.
    keys = [
        k for k in TranslationManager._translations["de"]
        if k.startswith("zone_editor_")
    ]
    # Dezimaltrenner aus demselben format_number-Locale wie die App ableiten
    # (DE „1,5" → Komma, EN „1.5" → Punkt) — der Editor formatiert Prozente
    # damit konsistent zur restlichen UI.
    decimal_sep = format_number(1.1, 1)[1]
    # Kamera-Profil (SSoT: vision_profiles) — eine ai_camera triggert selbst,
    # dort wirken ignorieren/ROI-Zonen im Scharf-Betrieb nicht. Der Editor
    # zeigt dann einen Hinweis-Banner (window.AI_CAM).
    ai_cam = False
    if source_id:
        try:
            from ..frame_sources.rtsp_source import find_camera_config
            from ..vision_profiles import resolve_profile
            from ..vision_store import VisionStore
            cam_cfg = find_camera_config(source_id)
            rec = VisionStore().get_source(source_id) or {}
            profile_name = (
                (cam_cfg or {}).get("profile")
                or (rec.get("settings") or {}).get("profile")
                or ""
            )
            ai_cam = not resolve_profile(str(profile_name)).allow_local_detection
        except Exception:  # noqa: BLE001
            ai_cam = False
    inject = (
        "<script>window.T="
        + json.dumps({k: t(k) for k in keys}, ensure_ascii=False)
        + ";window.DEC="
        + json.dumps(decimal_sep)
        + ";window.SRC_NAME="
        + json.dumps(src_name, ensure_ascii=False)
        + ";window.AI_CAM="
        + json.dumps(ai_cam)
        + ";</script>"
    )
    return HTMLResponse(
        html.replace("<!--I18N-->", inject),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )
