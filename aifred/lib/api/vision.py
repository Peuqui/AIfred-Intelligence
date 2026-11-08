"""Vision endpoints: snapshot/stream/events, face enrollment/Personarium,
event frames, zone masks and motion tuning."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message

# resolve_source_resolution lives in vision_utils.py — shared with the
# plugin tools so popup-UI resolution and tool-call resolution match.
from ..vision_settings import load_vision_settings, save_vision_settings
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
    except Exception as e:
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
        from ..vision_watch_types import WatchConfig

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
    import json as _json

    from starlette.responses import StreamingResponse

    from ..vision_event_bus import subscribe

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
# Personarium — Identity-Verwaltung
# ============================================================


class FaceSummary(BaseModel):
    """Eine Identity-Zeile fürs Personarium."""
    id: int
    name: str
    embedding_count: int
    last_seen: str
    last_seen_display: str = Field(description="The last sighting as the lists show a moment ('' = never seen)")
    crop_url: str
    notes: str = ""


class FaceSummaryList(BaseModel):
    faces: list[FaceSummary]


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
    events: list[FaceEvent]


class FaceRenameRequest(BaseModel):
    name: str = Field(..., min_length=1)


@api_app.get("/vision/face/list", response_model=FaceSummaryList, tags=["Vision"])
async def vision_face_list() -> FaceSummaryList:
    """Every person with its picture (the newest crop), how many embeddings it has and when it was seen last."""
    from ..personarium import persons

    return FaceSummaryList(faces=[FaceSummary(**row) for row in await asyncio.to_thread(persons)])


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
    """Rename a person (the recognition uses the new name at once); 404 for no such person, 409 for a name another
    person has."""
    from ..personarium import rename

    try:
        await asyncio.to_thread(rename, face_id, request.name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SystemActionResponse(success=True, message=f"renamed to '{request.name.strip()}'")


@api_app.delete(
    "/vision/face/{face_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_face_delete(face_id: int) -> SystemActionResponse:
    """Delete a person with its embeddings (its events keep no person; the crops stay until the cleanup's
    retention). ``message`` says it in the page's language. 404 for no such person."""
    from ..i18n import t
    from ..personarium import delete
    from ..prompt_loader import get_language

    try:
        name, embeddings_deleted = await asyncio.to_thread(delete, face_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found") from error
    log_message(f"👤 Person {face_id} ({name}) deleted with {embeddings_deleted} embeddings")
    return SystemActionResponse(success=True, message=t("personarium_deleted", lang=get_language(), name=name,
                                                         count=embeddings_deleted))


class FaceEmbedding(BaseModel):
    id: int
    quality: float
    created_at: str
    crop_url: str


@api_app.get("/vision/face/{face_id}/embeddings", response_model=list[FaceEmbedding], tags=["Vision"])
async def vision_face_embeddings(face_id: int) -> list[FaceEmbedding]:
    """A person's embeddings, newest first (without the vector); 404 for no such person."""
    from ..personarium import embeddings

    try:
        return [FaceEmbedding(**row) for row in await asyncio.to_thread(embeddings, face_id)]
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"face {face_id} not found") from error


@api_app.delete(
    "/vision/face/embedding/{embedding_id}",
    response_model=SystemActionResponse,
    tags=["Vision"],
)
async def vision_embedding_delete(embedding_id: int) -> SystemActionResponse:
    """Delete one embedding (a poor sample: wrong pose, bad light) without the person; 404 for no such one."""
    from ..personarium import delete_embedding

    try:
        await asyncio.to_thread(delete_embedding, embedding_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"embedding {embedding_id} not found") from error
    return SystemActionResponse(success=True, message="embedding deleted")


class OpenFace(BaseModel):
    id: int = Field(description="The card: its newest event")
    member_ids: list[int] = Field(description="Every event of the sighting (they share the crop)")
    crop_url: str
    event_type: str = Field(description="face_unsure: the recognition guesses matched_name")
    matched_name: str
    source_name: str
    date_display: str
    time_display: str


class OpenFacesDismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[int] = Field(min_length=1)


class OpenFaceAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    face_id: int | None = Field(None, description="An existing person by id, or:")
    name: str | None = Field(None, description="a person by name, made if new")


class ActionDone(BaseModel):
    message: str = Field(description="What happened, in the page's language")


@api_app.get("/vision/face/open", response_model=list[OpenFace], tags=["Vision"])
async def vision_open_faces() -> list[OpenFace]:
    """Every face no person is confirmed for and nobody dismissed, newest first, one card per crop file."""
    from dataclasses import asdict

    from ..personarium import open_faces

    return [OpenFace(**asdict(card)) for card in await asyncio.to_thread(open_faces)]


@api_app.post("/vision/face/open/dismiss", response_model=ActionDone, tags=["Vision"])
async def vision_open_faces_dismiss(dismissing: OpenFacesDismiss) -> ActionDone:
    """Take cards out of the open faces (their events stay in the Casus); 404 for a card no longer open."""
    from ..i18n import t
    from ..personarium import dismiss
    from ..prompt_loader import get_language

    try:
        dismissed = await asyncio.to_thread(dismiss, dismissing.cards)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no open face {error.args[0]}") from error
    return ActionDone(message=t("personarium_dismissed", lang=get_language(), count=dismissed))


@api_app.post("/vision/face/open/{card_id}/assign", response_model=ActionDone, tags=["Vision"])
async def vision_open_face_assign(card_id: int, assigning: OpenFaceAssign) -> ActionDone:
    """Assign the card's face to a person (``face_id``, or ``name`` for an existing or a new one) and learn it from
    the picture (the face model computes the embedding). 404 for a card no longer open, 422 for neither or both of
    ``face_id`` and ``name``, no such person, an empty name, or a face the model no longer finds."""
    from ..personarium import assign
    from ..prompt_loader import get_language

    if (assigning.face_id is None) == (assigning.name is None):
        raise HTTPException(status_code=422, detail="either face_id or name")
    try:
        done = await assign(card_id, face_id=assigning.face_id, name=assigning.name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no open face {card_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"👤 Open face {card_id} → {done.name} ({'learned' if done.learned else 'assigned'})")
    return ActionDone(message=done.message(get_language()))


class RematchState(BaseModel):
    running: bool
    message: str = Field(description="The state or the last outcome in words ('' before the first run)")


def _rematch_state() -> RematchState:
    from ..personarium import rematch
    from ..prompt_loader import get_language

    state = rematch()
    return RematchState(running=state.running, message=state.message(get_language()))


@api_app.get("/vision/face/rematch", response_model=RematchState, tags=["Vision"])
async def vision_face_rematch_state() -> RematchState:
    return _rematch_state()


@api_app.post("/vision/face/rematch", response_model=RematchState, status_code=202, tags=["Vision"])
async def vision_face_rematch() -> RematchState:
    """Recognise every open face again against the embeddings: sure matches are assigned (no new embeddings), the
    rest stays open. Answers at once, ``GET`` follows it; 409 while it runs."""
    from ..personarium import RematchRunning, start_rematch

    try:
        start_rematch()
    except RematchRunning as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _rematch_state()


@api_app.get("/vision/frame", tags=["Vision"])
async def vision_frame(id: int, w: int = 0, zoom: int = 0) -> Response:
    """Gespeichertes Event-Bild als JPEG ausliefern.

    ``id`` ist die Event-ID, ``w`` ein optionaler Ziel-Breiten-Parameter:
    >0 skaliert serverseitig herunter (Casus-Thumbnail nutzt w=80, das
    Bild-Modal lädt ohne ``w`` in Vollauflösung). ``zoom=1`` liefert statt
    des Weitwinkels den Tele-Snap des Events (404, wenn keiner existiert).
    Kein extra Auth-Gate — es gilt die Login-Cookie-Pflicht der API
    (lib/api/app.py), von außen zusätzlich die Anmeldung am Reverse-Proxy
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


class ZoneMaskPayload(BaseModel):
    """Speicher-Payload des Zonen-Editors."""
    source_id: str
    cols: int = 0
    rows: int = 0
    cells: str = ""          # cols*rows Ziffern aus {0,1,2,3}
    enabled: bool = True     # Schnell-Toggle: aus = Maske bleibt, wirkt nicht


@api_app.get("/vision/zone-mask", tags=["Vision"])
async def get_zone_mask(source_id: str) -> dict[str, Any]:
    """Gespeicherte Zonen-Maske einer Quelle (für den Editor zum Laden)."""
    entry = (load_vision_settings().get("zone_masks") or {}).get(source_id)
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
    data = load_vision_settings()
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
    save_vision_settings(data)
    # Live in den laufenden Watcher übernehmen — greift sofort, kein
    # Re-Arm/Neustart der Quelle nötig. No-op wenn die Quelle nicht läuft.
    try:
        from ..vision_watcher import get_default_watcher
        get_default_watcher().reload_zone_mask(payload.source_id)
    except (ValueError, sqlite3.Error) as e:  # saved; the watch takes it on its next start
        log_message(f"⚠️ zone mask live-reload failed: {e}")
    return SystemActionResponse(success=True, message=msg)


class MotionMinPayload(BaseModel):
    """Speicher-Payload des Bewegungs-Schwellwert-Sliders im Zonen-Editor."""
    source_id: str
    motion_min_area_ratio: float    # 0.001–0.5 (Anteil bewegter Pixel)


@api_app.get("/vision/motion-min", tags=["Vision"])
async def get_motion_min(source_id: str) -> dict[str, Any]:
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
    except sqlite3.Error as e:  # saved; the watch takes it on its next start
        log_message(f"⚠️ motion_min live-reload failed: {e}")
    return SystemActionResponse(success=True, message="motion min saved")


@api_app.get("/vision/zone-editor", tags=["Vision"])
async def zone_editor_page(source_id: str = "") -> HTMLResponse:
    """Standalone JS-Canvas-Zonen-Editor (HTML). Über /api ausgeliefert,
    damit er unabhängig vom frontend_path-Prefix erreichbar ist; die
    source_id kommt als Query-Param (das JS liest sie aus location.search)."""
    import json

    from ...theme import css_variables
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
        except sqlite3.Error:  # busy database: no source name
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
        except (ValueError, sqlite3.Error):  # settings file or event database: treated as a plain camera
            ai_cam = False
    inject = (
        f"<style>{css_variables()}</style>"
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
