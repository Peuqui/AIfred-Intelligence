"""Multipose — geführtes Multi-Pose-Enrollment.

Statt einem einzelnen Crop wie beim Inline-Enroll erfasst der User
hier mehrere Posen (Frontal / Links / Rechts), damit der Recognizer
auch bei Kopfbewegung den Match findet. Jede Pose wird einzeln
aufgenommen: Anweisung → Live-Snapshot → Face-Detect → Embedding in
die Capture-Liste. Am Ende schreibt ``multipose_finish`` alle
Embeddings als Sample-Bundle in den ``VisionStore``.

Aufrufbar als „Neue Person via Multi-Pose" (face_id=0) oder als
„Mehr Posen für bestehende Person" aus dem Personarium (face_id > 0,
Name wird vorausgefüllt + read-only).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


# Posen-Definitionen. Reihenfolge = Step-Reihenfolge im Modal.
# Labels sind in der UI via t() lokalisiert; die ``key``-Strings sind
# stabil und werden in der DB nirgends gespeichert (Embeddings sind
# pose-agnostisch — die Pose-Info dient nur der Anleitung).
POSE_STEPS: list[dict[str, str]] = [
    {"key": "frontal", "label_key": "multipose_pose_frontal"},
    {"key": "left", "label_key": "multipose_pose_left"},
    {"key": "right", "label_key": "multipose_pose_right"},
    {"key": "up", "label_key": "multipose_pose_up"},
    {"key": "down", "label_key": "multipose_pose_down"},
]

# Pro Quelle ein wiederverwendeter Reolink-Snap-Client für die Modal-Session
# (Token-Cache → eine Anmeldung statt Login-Sturm beim Preview-Polling). Wird
# beim Schließen des Modals geschlossen + geleert. Modul-Level, weil Reflex-
# State keine Client-Objekte serialisieren kann.
_MULTIPOSE_SNAP_CLIENTS: dict[str, Any] = {}


class MultiposeMixin(rx.State, mixin=True):
    """UI state for the Multi-Pose enrollment modal."""

    multipose_open: bool = False
    # 0 = neue Person, > 0 = bestehende Identity erweitern
    multipose_target_face_id: int = 0
    multipose_name: str = ""
    multipose_source_id: str = ""
    # Step-Pointer (0..len(POSE_STEPS)-1). Wenn step == len(POSE_STEPS),
    # ist die Sequenz durch und der Save-Button erscheint.
    multipose_step: int = 0
    # Pro Capture: {"key": str, "preview_data_url": str, "embedding_b64": str,
    #               "detection_score": float}
    multipose_captures: list[dict[str, Any]] = []
    # Vorschau der letzten Aufnahme im Modal (data:image/jpeg;base64,…).
    # Wird nach erfolgreichem Capture gesetzt — der User sieht direkt,
    # was gespeichert wurde, und kann ggf. „wiederholen".
    multipose_preview_data_url: str = ""
    # Vorschau VOR dem Capture (Live-Bild des Hubs, damit der User den
    # Bildausschnitt sieht, bevor er auslöst).
    multipose_live_preview_url: str = ""
    multipose_status: str = ""
    multipose_busy: bool = False
    # Quellen-Dropdown
    multipose_source_options: list[dict[str, str]] = []

    # ── Computed ─────────────────────────────────────────────────

    @rx.var
    def multipose_is_existing(self) -> bool:
        return self.multipose_target_face_id > 0

    @rx.var
    def multipose_total_steps(self) -> int:
        return len(POSE_STEPS)

    @rx.var
    def multipose_progress_label(self) -> str:
        """„2 / 5" — Fortschritt anzeigen."""
        return f"{min(self.multipose_step + 1, len(POSE_STEPS))} / {len(POSE_STEPS)}"

    @rx.var
    def multipose_current_pose_key(self) -> str:
        """i18n-Key für die Anweisung der aktuellen Pose. Wenn alle
        Posen durch sind, ein "summary"-Key."""
        if self.multipose_step >= len(POSE_STEPS):
            return "multipose_pose_done"
        return POSE_STEPS[self.multipose_step]["label_key"]

    @rx.var
    def multipose_finished(self) -> bool:
        return self.multipose_step >= len(POSE_STEPS)

    @rx.var
    def multipose_can_save(self) -> bool:
        """Mindestens eine Capture nötig + Name (bei neuer Person)."""
        has_captures = len(self.multipose_captures) > 0
        if self.multipose_target_face_id > 0:
            return has_captures
        return has_captures and len((self.multipose_name or "").strip()) > 0

    # ── Lifecycle ────────────────────────────────────────────────

    @rx.event
    async def open_multipose(self, face_id: int = 0, name: str = "") -> None:
        """Modal öffnen. ``face_id=0`` → neue Person, sonst bestehende
        Identity erweitern. ``name`` ist nur für die Anzeige relevant —
        Persistiert wird über ``face_id``."""
        self.multipose_open = True
        self.multipose_target_face_id = int(face_id)
        self.multipose_name = name
        self.multipose_step = 0
        self.multipose_captures = []
        self.multipose_preview_data_url = ""
        self.multipose_live_preview_url = ""
        self.multipose_status = ""
        self.multipose_busy = False
        await self._refresh_multipose_sources()

    @rx.event
    async def close_multipose(self) -> None:
        self.multipose_open = False
        self.multipose_captures = []
        self.multipose_preview_data_url = ""
        self.multipose_live_preview_url = ""
        # Reolink-Snap-Clients der Session schließen.
        for client in list(_MULTIPOSE_SNAP_CLIENTS.values()):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        _MULTIPOSE_SNAP_CLIENTS.clear()

    @rx.event
    async def multipose_live_tick(self) -> None:
        """Vom rx.moment-Timer im offenen Modal: Live-Preview frisch nachladen,
        damit der User seine Pose in Echtzeit prüfen kann. No-op wenn das Modal
        zu ist oder gerade ein Capture läuft."""
        if not self.multipose_open or self.multipose_busy:
            return
        await self._refresh_live_preview()

    @rx.event
    def multipose_set_name(self, value: str) -> None:
        self.multipose_name = value or ""

    @rx.event
    async def multipose_set_source(self, value: str) -> None:
        self.multipose_source_id = value or ""
        # Live-Preview neu laden, damit der User direkt sieht, was die
        # gewählte Cam liefert.
        await self._refresh_live_preview()

    # ── Capture ──────────────────────────────────────────────────

    @rx.event
    async def multipose_capture(self) -> None:
        """Aktuelle Pose aufnehmen: Snapshot vom Hub, Face-Detect,
        Embedding in die Capture-Liste. Erweitert ``multipose_step``
        um 1, wenn das größte Gesicht erfolgreich detektiert wurde."""
        if self.multipose_busy:
            return
        if not self.multipose_source_id:
            self.multipose_status = "⚠️ Keine Bildquelle ausgewählt."
            return
        if self.multipose_step >= len(POSE_STEPS):
            return
        self.multipose_busy = True
        self.multipose_status = ""
        try:
            jpeg_bytes, detection = await self._snapshot_and_detect(
                self.multipose_source_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("multipose capture failed: %s", e)
            self.multipose_status = f"⚠️ {e}"
            self.multipose_busy = False
            return
        if detection is None:
            self.multipose_status = (
                "⚠️ Kein Gesicht erkannt — bitte mittig vor die Kamera."
            )
            self.multipose_busy = False
            return
        # Crop für die Vorschau: das größte Gesicht aus dem JPEG ausschneiden.
        preview_data_url = self._make_preview_data_url(jpeg_bytes, detection.bbox)
        emb_b64 = base64.b64encode(
            detection.embedding.astype("float32").tobytes()
        ).decode("ascii")
        current_step = self.multipose_step
        pose_key = (
            POSE_STEPS[current_step]["key"]
            if current_step < len(POSE_STEPS) else "extra"
        )
        self.multipose_captures = self.multipose_captures + [
            {
                "key": pose_key,
                "preview_data_url": preview_data_url,
                "embedding_b64": emb_b64,
                "detection_score": float(detection.detection_score),
            }
        ]
        self.multipose_preview_data_url = preview_data_url
        self.multipose_step = current_step + 1
        self.multipose_status = ""
        self.multipose_busy = False

    @rx.event
    def multipose_retry_current(self) -> None:
        """Letzte Capture verwerfen + Schritt zurücksetzen."""
        if not self.multipose_captures:
            return
        self.multipose_captures = self.multipose_captures[:-1]
        self.multipose_step = max(0, self.multipose_step - 1)
        self.multipose_preview_data_url = ""
        self.multipose_status = ""

    @rx.event
    def multipose_skip_current(self) -> None:
        """Aktuelle Pose überspringen (kein Capture)."""
        if self.multipose_step < len(POSE_STEPS):
            self.multipose_step += 1

    # ── Save ──────────────────────────────────────────────────────

    @rx.event
    def multipose_finish(self) -> None:
        """Alle Captures als Embeddings in den VisionStore schreiben.
        Neue Identity (face_id=0) → ``add_face`` + Embeddings, sonst
        nur Embeddings."""
        if not self.multipose_captures:
            return
        try:
            from ..lib.vision_store import VisionStore
            import numpy as np
            store = VisionStore()
            face_id = int(self.multipose_target_face_id)
            if face_id <= 0:
                name = (self.multipose_name or "").strip()
                if not name:
                    self.multipose_status = "⚠️ Bitte Namen eingeben."
                    return
                existing = store.get_face_by_name(name)
                if existing:
                    face_id = int(existing["id"])
                else:
                    face_id = store.add_face(name=name, enrolled_by="multipose")
            from ..lib.face_crop_store import get_default_store
            crop_store = get_default_store()
            for cap in self.multipose_captures:
                emb_bytes = base64.b64decode(cap["embedding_b64"])
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                # Crop pro Embedding ablegen (für den Embedding-Manager) — das
                # Vorschau-Crop liegt bereits als data-URL vor.
                crop_url = ""
                durl = str(cap.get("preview_data_url", ""))
                if durl.startswith("data:image") and "," in durl:
                    try:
                        crop_jpeg = base64.b64decode(durl.split(",", 1)[1])
                        crop_url = crop_store.save_raw(crop_jpeg, face_id)
                    except Exception:  # noqa: BLE001
                        crop_url = ""
                store.add_embedding(
                    face_id, emb,
                    quality_score=float(cap.get("detection_score", 0.0)),
                    crop_url=crop_url,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("multipose finish failed: %s", e)
            self.multipose_status = f"⚠️ {e}"
            return
        try:
            from ..lib.vision_filters.face_recognize import FaceRecognizer
            from ..lib.vision_store import VisionStore
            FaceRecognizer(VisionStore()).invalidate()
        except Exception:  # noqa: BLE001
            pass
        count = len(self.multipose_captures)
        self.multipose_status = f"✓ {count} Pose(n) gespeichert."
        self.multipose_open = False
        self.multipose_captures = []
        self.multipose_preview_data_url = ""

    # ── Internals ────────────────────────────────────────────────

    async def _refresh_multipose_sources(self) -> None:
        """Source-Dropdown füllen + Default auf erste verfügbare Cam."""
        try:
            from ..lib.frame_sources import list_all
            sources = list_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("multipose source listing failed: %s", e)
            self.multipose_source_options = []
            return
        # User-Alias ("Büro") hat Vorrang vor dem Hardware-Namen — eine
        # Query, SSoT für die Präzedenz im Store.
        try:
            from ..lib.vision_store import VisionStore
            labels = VisionStore().source_labels()
        except Exception:  # noqa: BLE001
            labels = {}
        opts: list[dict[str, str]] = []
        available: list[str] = []
        for src in sources:
            try:
                info = src.info()
            except Exception:  # noqa: BLE001
                continue
            label = labels.get(info.source_id) or info.display_name or info.source_id
            if not info.available:
                label = f"{label}  ✗"
            opts.append({"value": info.source_id, "label": label})
            if info.available:
                available.append(info.source_id)
        self.multipose_source_options = opts
        if available and (not self.multipose_source_id
                          or self.multipose_source_id not in available):
            self.multipose_source_id = available[0]
            await self._refresh_live_preview()

    async def _refresh_live_preview(self) -> None:
        """Ein Snapshot der gewählten Source als Live-Preview im Modal."""
        if not self.multipose_source_id:
            return
        try:
            jpeg_bytes = await self._snapshot_jpeg(self.multipose_source_id)
            self.multipose_live_preview_url = self._jpeg_to_data_url(jpeg_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning("multipose live-preview failed: %s", e)
            self.multipose_live_preview_url = ""

    async def _fresh_frame(self, source_id: str) -> Any:
        """Frischestes Frame der Quelle. Für Reolink (ai_camera) direkt über
        die Snap-API — kein RTSP-Pufferlag, immer aktuell, und über das
        Zoom-Objektiv (face_channel) mit mehr Gesichts-Pixeln. Der Snap-Client
        wird pro Quelle wiederverwendet (Token-Cache → kein Login-Sturm).
        Sonst Hub-Snapshot."""
        from ..lib.frame_sources.rtsp_source import find_camera_config
        cam = find_camera_config(source_id)
        if cam and str(cam.get("profile")) == "ai_camera" and cam.get("cred"):
            try:
                from datetime import datetime

                from ..lib.frame_sources.base import Frame
                from ..lib.reolink_ai import ReolinkAIClient
                client = _MULTIPOSE_SNAP_CLIENTS.get(source_id)
                if client is None:
                    client = ReolinkAIClient(
                        host=str(cam.get("host", "")),
                        api_port=int(cam.get("api_port", 443)),
                        cred=str(cam.get("cred", "")),
                    )
                    _MULTIPOSE_SNAP_CLIENTS[source_id] = client
                fc = cam.get("face_channel")
                ch = int(fc) if fc is not None else int(cam.get("channel", 0))
                jpeg = await client.snap(ch)
                return Frame(
                    source_id=source_id, timestamp=datetime.now(),
                    image_bytes=jpeg, format="jpeg", width=0, height=0,
                    metadata={"kind": "rgb", "via": "snap"},
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "multipose snap failed for %s: %s — hub fallback", source_id, e
                )
        from ..lib.frame_hub import get_default_hub
        from ..lib.frame_sources import get as get_source
        src = get_source(source_id)
        if src is None:
            raise RuntimeError(f"source '{source_id}' not found")
        return await get_default_hub().snapshot(src, timeout=5.0)

    async def _snapshot_jpeg(self, source_id: str) -> bytes:
        """Frisches JPEG der Source als Bytes (für die Live-Preview)."""
        frame = await self._fresh_frame(source_id)
        return bytes(frame.image_bytes)

    async def _snapshot_and_detect(
        self, source_id: str
    ) -> tuple[bytes, Any]:
        """Frisches Frame + größtes Gesicht detektieren.
        Returnt ``(jpeg_bytes, detection_or_none)``."""
        import asyncio
        from ..lib.vision_filters.face_detect import get_default_detector
        frame = await self._fresh_frame(source_id)
        detector = get_default_detector()
        detections = await asyncio.to_thread(detector.detect, frame)
        if not detections:
            return bytes(frame.image_bytes), None
        # Größtes Gesicht wählen (höchste bbox-Fläche). Bei mehreren
        # Personen vor der Cam ist das typischerweise die, die sich
        # für die Aufnahme positioniert hat.
        best = max(detections, key=lambda d: d.bbox[2] * d.bbox[3])
        return bytes(frame.image_bytes), best

    @staticmethod
    def _jpeg_to_data_url(jpeg_bytes: bytes) -> str:
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"

    @staticmethod
    def _make_preview_data_url(
        jpeg_bytes: bytes, bbox: tuple[int, int, int, int]
    ) -> str:
        """JPEG mit dem erkannten Gesicht croppen. Padding 30 % für
        Kopf/Frisur, dann wieder JPEG-encoden + base64."""
        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return MultiposeMixin._jpeg_to_data_url(jpeg_bytes)
            h, w = img.shape[:2]
            x, y, bw, bh = bbox
            pad_x = int(bw * 0.3)
            pad_y = int(bh * 0.3)
            x0 = max(0, x - pad_x)
            y0 = max(0, y - pad_y)
            x1 = min(w, x + bw + pad_x)
            y1 = min(h, y + bh + pad_y)
            crop = img[y0:y1, x0:x1]
            ok, enc = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                return MultiposeMixin._jpeg_to_data_url(jpeg_bytes)
            return MultiposeMixin._jpeg_to_data_url(bytes(enc.tobytes()))
        except Exception as e:  # noqa: BLE001
            logger.warning("multipose preview crop failed: %s", e)
            return MultiposeMixin._jpeg_to_data_url(jpeg_bytes)
