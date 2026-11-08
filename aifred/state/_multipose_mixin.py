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
import sqlite3
from typing import TYPE_CHECKING, Any

import reflex as rx

from ..lib.enrollment import POSES

logger = logging.getLogger(__name__)


class MultiposeMixin(rx.State, mixin=True):
    """UI state for the Multi-Pose enrollment modal (the session itself lives in ``lib.enrollment``)."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here (mypy checks them against the real ones where AIState combines
        # the mixins).
        ui_language: rx.Field[str]

    multipose_open: rx.Field[bool] = rx.field(False)
    # 0 = neue Person, > 0 = bestehende Identity erweitern
    multipose_target_face_id: rx.Field[int] = rx.field(0)
    multipose_name: rx.Field[str] = rx.field("")
    multipose_source_id: rx.Field[str] = rx.field("")
    # Die Einlern-Sitzung im Server (lib.enrollment); eine zur Zeit, eine neue ersetzt sie.
    _multipose_token: str = ""
    # Step-Pointer (0..len(POSES)-1). Wenn step == len(POSES), ist die Sequenz durch und der Save-Button erscheint.
    multipose_step: rx.Field[int] = rx.field(0)
    # Pro Capture: {"key": Pose, "crop_url": Ausschnitt (im Server), "detection_score": float} — kein Embedding.
    multipose_captures: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    # Vorschau VOR dem Capture (Live-Bild der Kamera, damit der User den Bildausschnitt sieht, bevor er auslöst).
    multipose_live_preview_url: rx.Field[str] = rx.field("")
    multipose_status: rx.Field[str] = rx.field("")
    multipose_busy: rx.Field[bool] = rx.field(False)
    # Quellen-Dropdown
    multipose_source_options: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)

    # ── Computed ─────────────────────────────────────────────────

    @rx.var
    def multipose_is_existing(self) -> bool:
        return self.multipose_target_face_id > 0

    @rx.var
    def multipose_progress_label(self) -> str:
        """„2 / 5" — Fortschritt anzeigen."""
        return f"{min(self.multipose_step + 1, len(POSES))} / {len(POSES)}"

    @rx.var
    def multipose_current_pose_key(self) -> str:
        """i18n-Key für die Anweisung der aktuellen Pose. Wenn alle
        Posen durch sind, ein "summary"-Key."""
        if self.multipose_step >= len(POSES):
            return "multipose_pose_done"
        return f"multipose_pose_{POSES[self.multipose_step]}"

    @rx.var
    def multipose_finished(self) -> bool:
        return self.multipose_step >= len(POSES)

    @rx.var
    def multipose_can_save(self) -> bool:
        """Mindestens eine Capture nötig + Name (bei neuer Person)."""
        has_captures = len(self.multipose_captures) > 0
        if self.multipose_target_face_id > 0:
            return has_captures
        return has_captures and len((self.multipose_name or "").strip()) > 0

    # ── Lifecycle ────────────────────────────────────────────────

    def _multipose_show(self, session: Any) -> None:
        """Den Stand der Sitzung (``lib.enrollment.Session``) übernehmen."""
        self.multipose_step = session.step
        self.multipose_captures = [
            {"key": taken.pose, "crop_url": f"/api/vision/enroll/{session.token}/captures/{index}",
             "detection_score": taken.score}
            for index, taken in enumerate(session.captures)
        ]

    @rx.event
    async def open_multipose(self, face_id: int = 0, name: str = "") -> None:
        """Modal öffnen und eine Einlern-Sitzung beginnen. ``face_id=0`` → neue Person, sonst bestehende
        Identity erweitern (``name`` nur für die Anzeige)."""
        from ..lib.enrollment import start

        try:
            session = start(int(face_id))
        except KeyError:  # the person went meanwhile
            self.multipose_status = f"⚠️ face {face_id} not found"
            return
        self._multipose_token = session.token
        self._multipose_show(session)
        self.multipose_open = True
        self.multipose_target_face_id = int(face_id)
        self.multipose_name = name
        self.multipose_live_preview_url = ""
        self.multipose_status = ""
        self.multipose_busy = False
        await self._refresh_multipose_sources()

    @rx.event
    async def close_multipose(self) -> None:
        from ..lib.enrollment import cancel

        cancel(self._multipose_token)
        self.multipose_open = False
        self.multipose_captures = []
        self.multipose_live_preview_url = ""

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
        """Aktuelle Pose aufnehmen (``lib.enrollment.capture``: größtes Gesicht des frischesten Bildes)."""
        from ..lib.enrollment import capture
        from ..lib.user_error import UserError, said

        if self.multipose_busy or self.multipose_step >= len(POSES):
            return
        if not self.multipose_source_id:
            self.multipose_status = "⚠️ Keine Bildquelle ausgewählt."
            return
        self.multipose_busy = True
        self.multipose_status = ""
        try:
            self._multipose_show(await capture(self._multipose_token, self.multipose_source_id))
        except UserError as e:  # no face, the session replaced elsewhere, the camera gone
            self.multipose_status = f"⚠️ {said(e, self.ui_language)}"
        except Exception as e:  # camera and face model: the dialog shows why
            logger.warning("multipose capture failed: %s", e, exc_info=True)
            self.multipose_status = f"⚠️ {e}"
        self.multipose_busy = False

    @rx.event
    def multipose_retry_current(self) -> None:
        """Letzte Capture verwerfen + Schritt zurücksetzen."""
        from ..lib.enrollment import SessionGone, undo
        from ..lib.user_error import said

        self.multipose_status = ""
        try:
            self._multipose_show(undo(self._multipose_token))
        except SessionGone as e:
            self.multipose_status = f"⚠️ {said(e, self.ui_language)}"

    @rx.event
    def multipose_skip_current(self) -> None:
        """Aktuelle Pose überspringen (kein Capture)."""
        from ..lib.enrollment import SessionGone, skip
        from ..lib.user_error import said

        try:
            self._multipose_show(skip(self._multipose_token))
        except SessionGone as e:
            self.multipose_status = f"⚠️ {said(e, self.ui_language)}"

    # ── Save ──────────────────────────────────────────────────────

    @rx.event
    def multipose_finish(self) -> None:
        """Alle Captures speichern (``lib.enrollment.finish``: Person anlegen wenn neu, Embeddings + Ausschnitte,
        Erkenner neu laden)."""
        from ..lib.enrollment import finish
        from ..lib.i18n import t
        from ..lib.user_error import UserError, said

        if not self.multipose_captures:
            return
        try:
            _face_id, name, count = finish(self._multipose_token, self.multipose_name)
        except (UserError, sqlite3.Error, OSError) as e:  # session gone, no capture or name, store or crop file
            logger.warning("multipose finish failed: %s", e)
            self.multipose_status = f"⚠️ {said(e, self.ui_language)}"
            return
        self.multipose_status = t("multipose_saved", lang=self.ui_language, name=name, count=count)
        self.multipose_open = False
        self.multipose_captures = []

    # ── Internals ────────────────────────────────────────────────

    async def _refresh_multipose_sources(self) -> None:
        """Source-Dropdown füllen + Default auf erste verfügbare Cam."""
        from ..lib.frame_sources import list_all
        sources = list_all()
        # User-Alias ("Büro") hat Vorrang vor dem Hardware-Namen — eine
        # Query, SSoT für die Präzedenz im Store.
        try:
            from ..lib.vision_store import VisionStore
            labels = VisionStore().source_labels()
        except sqlite3.Error:  # no aliases: the hardware names stand
            labels = {}
        opts: list[dict[str, str]] = []
        available: list[str] = []
        for src in sources:
            try:
                info = src.info()
            except Exception as e:  # noqa: BLE001 — one broken source must not empty the list
                logger.warning("multipose: source %r left out, info() failed: %s", src, e)
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
        """Ein frisches Bild der gewählten Kamera (``lib.enrollment.fresh_face_frame``) als Live-Preview im Modal."""
        from ..lib.enrollment import fresh_face_frame

        if not self.multipose_source_id:
            return
        try:
            frame = await fresh_face_frame(self.multipose_source_id)
            self.multipose_live_preview_url = "data:image/jpeg;base64," + base64.b64encode(bytes(frame.image_bytes)).decode("ascii")
        except Exception as e:  # camera: the preview stays empty, logged
            logger.warning("multipose live-preview failed: %s", e, exc_info=True)
            self.multipose_live_preview_url = ""
