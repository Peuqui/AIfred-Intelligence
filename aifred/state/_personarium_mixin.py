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
    # Embedding-Manager: face_id der Identität, deren Embeddings gerade
    # inline angezeigt werden (0 = keine), + die Embedding-Liste
    # ({id, quality, created_at, crop_url} — ohne den numpy-Vektor).
    personarium_manage_face_id: int = 0
    personarium_embeddings: list[dict[str, Any]] = []

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

    def _reload_personarium_embeddings(self) -> None:
        """Embeddings der gerade verwalteten Identität laden (ohne den
        numpy-Vektor — der gehört nicht in den Reflex-State)."""
        fid = int(self.personarium_manage_face_id)
        if fid <= 0:
            self.personarium_embeddings = []
            return
        try:
            from ..lib.vision_store import VisionStore
            rows = VisionStore().list_embeddings(fid)
            self.personarium_embeddings = [
                {
                    "id": int(r.get("id")),
                    "quality": round(float(r.get("quality_score", 0.0) or 0.0), 2),
                    "created_at": str(r.get("created_at", "") or "")[:19].replace("T", " "),
                    "crop_url": str(r.get("crop_url", "") or ""),
                }
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium embeddings load failed: %s", e)
            self.personarium_embeddings = []

    @rx.event
    def personarium_open_embeddings(self, face_id: int) -> None:
        """Embedding-Manager für eine Identität ein-/ausklappen."""
        fid = int(face_id)
        if self.personarium_manage_face_id == fid:
            self.personarium_manage_face_id = 0
            self.personarium_embeddings = []
            return
        self.personarium_manage_face_id = fid
        self._reload_personarium_embeddings()

    @rx.event
    def personarium_delete_embedding(self, embedding_id: int) -> None:
        """Ein einzelnes Embedding löschen (Crop bleibt auf Disk, wird vom
        Cleanup-TTL erfasst). Aktualisiert Liste + Identity-Count."""
        try:
            from ..lib.vision_filters.face_recognize import FaceRecognizer
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            store.delete_embedding(int(embedding_id))
            FaceRecognizer(store).invalidate()  # frisch erkennen
        except Exception as e:  # noqa: BLE001
            logger.warning("delete embedding failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            return
        self._reload_personarium_embeddings()
        self._refresh_personarium_faces()

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
