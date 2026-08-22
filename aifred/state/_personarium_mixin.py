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
    # „Unzugeordnete Gesichter" — face_unknown/face_unsure-Events ohne
    # Identity, dedupliziert pro Vorkommnis (cluster_id). Zum Nachtaggen
    # + Embedding-Lernen (lib.face_enroll).
    personarium_untagged: list[dict[str, Any]] = []
    # Tag-Modus: event_id der Karte, die gerade zugeordnet wird (0 = keine),
    # Auswahl im Dropdown (face_id als String oder "__new__") + Name-Input.
    personarium_tag_event_id: int = 0
    personarium_tag_value: str = ""
    personarium_tag_new_name: str = ""
    # Läuft gerade ein Enroll (InsightFace auf dem Frame)? → Spinner.
    personarium_tag_busy: bool = False

    @rx.event
    def open_personarium(self) -> None:
        """Öffne das Modal + lade Identity-Liste und unzugeordnete
        Gesichter."""
        self._refresh_personarium_faces()
        self._refresh_personarium_untagged()
        self.personarium_open = True
        self.personarium_status = ""

    @rx.event
    def close_personarium(self) -> None:
        self.personarium_open = False
        self.personarium_edit_face_id = 0
        self.personarium_edit_name = ""
        self.personarium_tag_event_id = 0
        self.personarium_tag_value = ""
        self.personarium_tag_new_name = ""

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
                    "id": int(r.get("id") or 0),
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
            from ..lib.vision_filters.face_recognize import bump_enrollment_epoch
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            store.delete_embedding(int(embedding_id))
            bump_enrollment_epoch()  # frisch erkennen
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
        # Alle lebenden Recognizer neu laden lassen, damit der nächste
        # Frame den neuen Namen anzeigt.
        from ..lib.vision_filters.face_recognize import bump_enrollment_epoch
        bump_enrollment_epoch()

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
        from ..lib.vision_filters.face_recognize import bump_enrollment_epoch
        bump_enrollment_epoch()

    # ── Unzugeordnete Gesichter (Nachtaggen + Lernen) ────────────────

    def _refresh_personarium_untagged(self) -> None:
        """face_unknown/face_unsure-Events ohne Identity laden — ein
        Eintrag pro Vorkommnis (cluster_id-Dedupe), nur mit Crop."""
        try:
            from ..lib.vision_store import VisionStore
            rows = VisionStore().list_events_with_summary(
                event_types=["face_unknown", "face_unsure"],
                unknown_only=True,
                limit=100,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium untagged load failed: %s", e)
            self.personarium_untagged = []
            return
        seen_clusters: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            if not r.get("crop_url"):
                continue
            cid = str(r.get("cluster_id") or "")
            if cid:
                if cid in seen_clusters:
                    continue
                seen_clusters.add(cid)
            out.append({
                "id": int(r["id"]),
                "crop_url": str(r["crop_url"]),
                "event_type": str(r["event_type"]),
                "matched_name": str(r.get("matched_name") or ""),
                "source_name": str(r.get("source_name") or ""),
                "date_display": str(r.get("date_display") or ""),
                "time_display": str(r.get("time_display") or ""),
            })
            if len(out) >= 24:
                break
        self.personarium_untagged = out

    @rx.event
    def personarium_start_tag(self, event_id: int) -> None:
        """Tag-Modus für eine Gesichts-Karte öffnen."""
        self.personarium_tag_event_id = int(event_id)
        self.personarium_tag_value = ""
        self.personarium_tag_new_name = ""

    @rx.event
    def personarium_cancel_tag(self) -> None:
        self.personarium_tag_event_id = 0
        self.personarium_tag_value = ""
        self.personarium_tag_new_name = ""

    @rx.event
    def personarium_set_tag_value(self, value: str) -> None:
        # Radix liefert die item-value je nach Pfad als int (trotz .to(str)
        # im Frontend) — ohne Cast verwirft Reflex die Zuweisung ans
        # str-Feld und der Speichern-Klick läuft ins Leere.
        self.personarium_tag_value = str(value) if value is not None else ""

    @rx.event
    def personarium_set_tag_new_name(self, value: str) -> None:
        self.personarium_tag_new_name = value or ""

    @rx.event
    async def personarium_save_tag(self):
        """Zuordnen + Lernen: Embedding aus dem gespeicherten Frame
        nachrechnen (lib.face_enroll), der Person anhängen und das
        Event zuordnen. Bestehende Person via face_id, neue via Name."""
        event_id = int(self.personarium_tag_event_id)
        choice = self.personarium_tag_value
        if event_id <= 0 or not choice:
            return
        self.personarium_tag_busy = True
        yield
        try:
            from ..lib.face_enroll import enroll_face_from_event
            if choice == "__new__":
                result = await enroll_face_from_event(
                    event_id, name=self.personarium_tag_new_name,
                )
            else:
                result = await enroll_face_from_event(
                    event_id, face_id=int(choice),
                )
            from ..lib.formatting import format_number
            self.personarium_status = (
                f"✓ Gesicht als '{result['name']}' gelernt "
                f"(Qualität {format_number(result['quality'], 2)})"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("personarium tag+learn failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            self.personarium_tag_busy = False
            return
        self.personarium_tag_busy = False
        self.personarium_tag_event_id = 0
        self.personarium_tag_value = ""
        self.personarium_tag_new_name = ""
        self._refresh_personarium_faces()
        self._refresh_personarium_untagged()
