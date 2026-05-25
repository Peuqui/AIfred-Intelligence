"""Personarium — Identitäten-Verwaltungs-Modal.

Liste aller im ``vision_store.faces`` registrierten Personen mit
Avatar (letzter Crop), Anzahl Embeddings und letzter Sichtung.
Aktionen: Umbenennen, Löschen, einzelne Embeddings entfernen.

Reads gehen direkt über den ``VisionStore`` (kein API-Roundtrip),
Writes ebenfalls — die REST-Endpoints unter ``/api/vision/face/*``
sind für externe Konsumenten gedacht.
"""

from __future__ import annotations

import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


class PersonariumMixin(rx.State, mixin=True):
    """UI state for the Personarium identity management modal."""

    personarium_open: bool = False
    # Liste aller Identitäten — befüllt beim Modal-Open + nach jeder
    # Aktion (Umbenennen, Löschen). Format kommt von
    # ``VisionStore.list_faces_with_summary()``.
    personarium_faces: list[dict[str, Any]] = []
    # Status-Feedback nach Aktionen (Toast-Ersatz)
    personarium_status: str = ""
    # Inline-Edit-State: face_id der Zeile die gerade umbenannt wird
    # (0 = niemand), plus aktueller Input.
    personarium_edit_face_id: int = 0
    personarium_edit_name: str = ""

    @rx.event
    def open_personarium(self) -> None:
        """Öffne das Modal + lade die Identity-Liste."""
        self._refresh_personarium_faces()
        self.personarium_open = True
        self.personarium_status = ""

    @rx.event
    def close_personarium(self) -> None:
        self.personarium_open = False
        self.personarium_edit_face_id = 0
        self.personarium_edit_name = ""

    def _refresh_personarium_faces(self) -> None:
        """Lade Liste neu — wird nach jeder Aktion gerufen."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            self.personarium_faces = store.list_faces_with_summary()
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium refresh failed: %s", e)
            self.personarium_faces = []
            self.personarium_status = f"⚠️ {e}"

    @rx.event
    def personarium_start_rename(self, face_id: int, current_name: str) -> None:
        """Inline-Edit-Modus für eine Zeile starten."""
        self.personarium_edit_face_id = int(face_id)
        self.personarium_edit_name = current_name
        self.personarium_status = ""

    @rx.event
    def personarium_set_edit_name(self, value: str) -> None:
        self.personarium_edit_name = value

    @rx.event
    def personarium_cancel_rename(self) -> None:
        self.personarium_edit_face_id = 0
        self.personarium_edit_name = ""

    @rx.event
    def personarium_save_rename(self) -> None:
        """Persistiert den neuen Namen über VisionStore."""
        face_id = int(self.personarium_edit_face_id)
        new_name = (self.personarium_edit_name or "").strip()
        if face_id <= 0 or not new_name:
            return
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            store.rename_face(face_id, new_name)
            self.personarium_status = f"✓ Umbenannt: {new_name}"
        except ValueError as e:
            self.personarium_status = f"⚠️ {e}"
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium rename failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            return
        self.personarium_edit_face_id = 0
        self.personarium_edit_name = ""
        self._refresh_personarium_faces()
        # Recognizer-Cache invalidieren, damit der nächste Frame den
        # neuen Namen anzeigt.
        try:
            from ..lib.vision_filters.face_recognize import FaceRecognizer
            FaceRecognizer(store).invalidate()
        except Exception:  # noqa: BLE001
            pass

    @rx.event
    def personarium_delete_face(self, face_id: int) -> None:
        """Identity vollständig löschen (Face-Row + Embeddings +
        face_id-Refs in Events). Crops auf Disk bleiben und werden
        vom vision_cleanup-Task per TTL aufgeräumt."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            face = store.get_face_by_id(int(face_id))
            if not face:
                return
            info = store.delete_face_with_assets(int(face_id))
            self.personarium_status = (
                f"✓ '{face['name']}' gelöscht "
                f"({info['embeddings_deleted']} Embeddings)"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium delete failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            return
        self._refresh_personarium_faces()
        try:
            from ..lib.vision_filters.face_recognize import FaceRecognizer
            FaceRecognizer(store).invalidate()
        except Exception:  # noqa: BLE001
            pass
