"""Personarium — Identitäten-Verwaltungs-Modal (Reflex-Kopie; die Regeln stehen in ``lib/personarium.py``, auch für
die Vue-Seite und ``/api/vision/face/*``).

Liste aller Personen mit Avatar (letzter Crop), Anzahl Embeddings und letzter Sichtung; Umbenennen, Löschen,
einzelne Embeddings entfernen; offene Gesichter zuordnen und lernen, verwerfen, neu erkennen.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import reflex as rx

logger = logging.getLogger(__name__)


class PersonariumMixin(rx.State, mixin=True):
    """UI state for the Personarium identity management modal."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here (mypy checks them against the real ones where AIState combines
        # the mixins).
        ui_language: rx.Field[str]

    personarium_open: rx.Field[bool] = rx.field(False)
    # Liste aller Identitäten — befüllt beim Modal-Open + nach jeder
    # Aktion (Umbenennen, Löschen). Format kommt von
    # ``VisionStore.list_faces_with_summary()``.
    personarium_faces: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    # Status-Feedback nach Aktionen (Toast-Ersatz)
    personarium_status: rx.Field[str] = rx.field("")
    # Inline-Edit-State: face_id der Zeile die gerade umbenannt wird
    # (0 = niemand), plus aktueller Input.
    personarium_edit_face_id: rx.Field[int] = rx.field(0)
    personarium_edit_name: rx.Field[str] = rx.field("")
    # Embedding-Manager: face_id der Identität, deren Embeddings gerade
    # inline angezeigt werden (0 = keine), + die Embedding-Liste
    # ({id, quality, created_at, crop_url} — ohne den numpy-Vektor).
    personarium_manage_face_id: rx.Field[int] = rx.field(0)
    personarium_embeddings: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    # „Unzugeordnete Gesichter" — face_unknown/face_unsure-Events ohne
    # Identity, eine Karte je Crop-Datei (``member_ids`` = alle Events
    # dahinter). Zum Nachtaggen + Embedding-Lernen (lib.face_enroll).
    personarium_untagged: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    # Mehrfachauswahl im Nachtag-Grid: event_ids der angehakten Karten,
    # plus die zweistufige Bestätigung für „alle verwerfen". Gleiche
    # Mechanik wie Speicher-Tab und Chatliste (ui.helpers.bulk_delete_bar).
    personarium_untagged_selected: rx.Field[list[int]] = rx.field(default_factory=list)
    personarium_confirm_dismiss_all: rx.Field[bool] = rx.field(False)
    # Tag-Modus: event_id der Karte, die gerade zugeordnet wird (0 = keine),
    # Auswahl im Dropdown (face_id als String oder "__new__") + Name-Input.
    personarium_tag_event_id: rx.Field[int] = rx.field(0)
    personarium_tag_value: rx.Field[str] = rx.field("")
    personarium_tag_new_name: rx.Field[str] = rx.field("")
    # Läuft gerade ein Enroll (InsightFace auf dem Frame)? → Spinner.
    personarium_tag_busy: rx.Field[bool] = rx.field(False)
    # Läuft gerade der manuelle Re-Match über alle Unzugeordneten?
    personarium_rematch_busy: rx.Field[bool] = rx.field(False)

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
        from ..lib.personarium import persons

        try:
            self.personarium_faces = persons()
        except sqlite3.Error as e:
            logger.warning("personarium refresh failed: %s", e)
            self.personarium_faces = []
            self.personarium_status = f"⚠️ {e}"

    @rx.var
    def personarium_untagged_unknown(self) -> list[dict[str, Any]]:
        """Nachtag-Karten OHNE Kandidaten — niemand erkannt."""
        return [
            e for e in self.personarium_untagged
            if e.get("event_type") != "face_unsure"
        ]

    @rx.var
    def personarium_untagged_unsure(self) -> list[dict[str, Any]]:
        """Nachtag-Karten MIT Kandidaten — die Erkennung vermutet jemanden,
        traut sich aber nicht. Getrennt vom Rest, weil sonst der Eindruck
        entsteht, eine längst bekannte Person sei unerkannt geblieben:
        hier geht es nur um die Bestätigung einer wackligen Aufnahme."""
        return [
            e for e in self.personarium_untagged
            if e.get("event_type") == "face_unsure"
        ]

    @rx.var
    def personarium_tag_options(self) -> list[dict[str, str]]:
        """Dropdown-Optionen fürs Nachtaggen — value/label Python-seitig
        als Strings gebaut. Radix-Select vergleicht den kontrollierten Wert
        strikt (===) mit den Item-Values; ein numerischer Item-Value aus dem
        Frontend matcht den String im State nie → leerer Trigger."""
        return [
            {"value": str(f.get("id", 0)), "label": str(f.get("name", ""))}
            for f in self.personarium_faces
        ]

    def _reload_personarium_embeddings(self) -> None:
        """Embeddings der gerade verwalteten Identität laden (``lib.personarium.embeddings``, ohne den Vektor)."""
        from ..lib.personarium import embeddings

        fid = int(self.personarium_manage_face_id)
        if fid <= 0:
            self.personarium_embeddings = []
            return
        try:
            self.personarium_embeddings = [{**row, "quality": round(row["quality"], 2)} for row in embeddings(fid)]
        except (KeyError, sqlite3.Error) as e:  # the person went meanwhile, or the store failed
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
        from ..lib.personarium import delete_embedding

        try:
            delete_embedding(int(embedding_id))
        except (KeyError, sqlite3.Error) as e:  # already gone, or the store failed
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
        """Neuer Name über ``lib.personarium.rename`` (die Erkennung nutzt ihn sofort)."""
        from ..lib.i18n import t
        from ..lib.personarium import rename

        face_id = int(self.personarium_edit_face_id)
        new_name = (self.personarium_edit_name or "").strip()
        if face_id <= 0 or not new_name:
            return
        try:
            rename(face_id, new_name)
        except (KeyError, ValueError, sqlite3.Error) as e:  # gone, a name another person has, or the store failed
            logger.warning("personarium rename failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            return
        self.personarium_status = t("personarium_renamed", lang=self.ui_language, name=new_name)
        self.personarium_edit_face_id = 0
        self.personarium_edit_name = ""
        self._refresh_personarium_faces()

    @rx.event
    def personarium_delete_face(self, face_id: int) -> None:
        """Identity vollständig löschen (``lib.personarium.delete``: Person, Embeddings, Bezüge der Events).
        Crops auf Disk bleiben und werden vom vision_cleanup-Task per TTL aufgeräumt."""
        from ..lib.i18n import t
        from ..lib.personarium import delete

        try:
            name, embeddings_deleted = delete(int(face_id))
        except KeyError:  # deleted meanwhile (another page): the list shows it
            self._refresh_personarium_faces()
            return
        except sqlite3.Error as e:
            logger.warning("personarium delete failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            return
        self.personarium_status = t("personarium_deleted", lang=self.ui_language, name=name, count=embeddings_deleted)
        self._refresh_personarium_faces()

    # ── Unzugeordnete Gesichter (Nachtaggen + Lernen) ────────────────

    def _refresh_personarium_untagged(self) -> None:
        """Alle offenen Gesichter (``lib.personarium.open_faces``): unbekannte und unsichere, eine Karte je
        Crop-Datei, ohne Deckel. Häkchen auf Karten, die es nicht mehr gibt, fallen weg."""
        from ..lib.personarium import open_faces

        try:
            cards = open_faces()
        except sqlite3.Error as e:
            logger.warning("personarium untagged load failed: %s", e)
            self.personarium_untagged = []
            return
        self.personarium_untagged = [{**asdict(card), "count": len(card.member_ids)} for card in cards]
        alive = {card.id for card in cards}
        self.personarium_untagged_selected = [eid for eid in self.personarium_untagged_selected if eid in alive]

    @rx.event
    async def personarium_rematch_untagged(self):
        """Manueller Re-Match (``lib.personarium.start_rematch``, einer zur Zeit für alle Oberflächen): alle
        offenen Gesichter erneut gegen die Embeddings erkennen, sichere Treffer zuordnen (keine neuen Embeddings)."""
        from ..lib.personarium import RematchRunning, rematch, start_rematch

        try:
            run = start_rematch()
        except RematchRunning:  # started here or on the Vue page
            self.personarium_status = rematch().message(self.ui_language)
            return
        self.personarium_rematch_busy = True
        yield
        await run
        self.personarium_status = rematch().message(self.ui_language)
        self.personarium_rematch_busy = False
        self._refresh_personarium_untagged()
        self._refresh_personarium_faces()

    def _dismiss_cards(self, card_ids: list[int]) -> None:
        from ..lib.i18n import t
        from ..lib.personarium import dismiss

        try:
            count = dismiss(card_ids)
        except (KeyError, sqlite3.Error) as e:  # a card assigned meanwhile, or the store failed
            logger.warning("personarium dismiss failed: %s", e)
            self.personarium_status = f"⚠️ {e}"
            self._refresh_personarium_untagged()
            return
        self.personarium_status = t("personarium_dismissed", lang=self.ui_language, count=count)
        self.personarium_untagged_selected = []
        self._refresh_personarium_untagged()

    @rx.event
    def personarium_dismiss_untagged(self, event_id: int) -> None:
        """Genau diese Karte aus den Vorschlägen nehmen (zu schlechter Crop) — die Events bleiben im Casus."""
        self._dismiss_cards([int(event_id)])

    @rx.event
    def personarium_toggle_untagged(self, event_id: int) -> None:
        """Eine Karte an-/abhaken (Mehrfachauswahl)."""
        eid = int(event_id)
        if eid in self.personarium_untagged_selected:
            self.personarium_untagged_selected = [
                x for x in self.personarium_untagged_selected if x != eid
            ]
        else:
            self.personarium_untagged_selected = [
                *self.personarium_untagged_selected, eid,
            ]

    @rx.event
    def personarium_select_all_untagged(self) -> None:
        """Alle gerade gelisteten Karten anhaken — beide Blöcke."""
        self.personarium_untagged_selected = [
            int(e["id"]) for e in self.personarium_untagged
        ]

    @rx.event
    def personarium_clear_untagged_selection(self) -> None:
        """Alle Häkchen entfernen."""
        self.personarium_untagged_selected = []

    @rx.event
    def personarium_dismiss_selected(self) -> None:
        """Die angehakten Aufnahmen verwerfen."""
        if self.personarium_untagged_selected:
            self._dismiss_cards(list(self.personarium_untagged_selected))

    @rx.event
    def personarium_request_dismiss_all(self) -> None:
        """Bestätigung für „alle verwerfen" anfordern."""
        self.personarium_confirm_dismiss_all = True

    @rx.event
    def personarium_cancel_dismiss_all(self) -> None:
        """Bestätigung zurücknehmen."""
        self.personarium_confirm_dismiss_all = False

    @rx.event
    def personarium_dismiss_all_untagged(self) -> None:
        """Alle gerade gelisteten Aufnahmen verwerfen."""
        self.personarium_confirm_dismiss_all = False
        if self.personarium_untagged:
            self._dismiss_cards([int(e["id"]) for e in self.personarium_untagged])

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
        self.personarium_tag_value = value

    @rx.event
    def personarium_set_tag_new_name(self, value: str) -> None:
        self.personarium_tag_new_name = value or ""

    @rx.event
    async def personarium_save_tag(self):
        """Zuordnen + Lernen über ``lib.personarium.assign``: bestehende Person via face_id, neue via Name."""
        from ..lib.personarium import assign

        event_id = int(self.personarium_tag_event_id)
        choice = self.personarium_tag_value
        if event_id <= 0 or not choice:
            return
        self.personarium_tag_busy = True
        yield
        try:
            if choice == "__new__":
                done = await assign(event_id, name=self.personarium_tag_new_name)
            else:
                done = await assign(event_id, face_id=int(choice))
        except Exception as e:  # the Personarium shows why: face model, files and store
            logger.warning("personarium tag+learn failed: %s", e, exc_info=True)
            self.personarium_status = f"⚠️ {e}"
            self.personarium_tag_busy = False
            return
        self.personarium_status = done.message(self.ui_language)
        self.personarium_tag_busy = False
        self.personarium_tag_event_id = 0
        self.personarium_tag_value = ""
        self.personarium_tag_new_name = ""
        self._refresh_personarium_faces()
        self._refresh_personarium_untagged()
        # Aufgeklapptes Embedding-Grid sofort nachziehen — das frisch
        # gelernte Embedding soll ohne Zu-/Aufklappen erscheinen.
        self._reload_personarium_embeddings()
