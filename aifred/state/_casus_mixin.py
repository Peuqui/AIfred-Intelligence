"""Casus — Ereignis-Verwaltungs-Modal.

Chronologische Liste aller Vision-Events (motion / face_known /
face_unsure / face_unknown / vlm_analysis) mit Filtern (Quelle, Typ,
Identity) und Aktionen pro Zeile: Event löschen, unbekanntes Gesicht
nachträglich einer Person zuordnen.

Liest direkt aus ``VisionStore`` (keine API), Writes ebenfalls.
"""

from __future__ import annotations

import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


# Konstanten — page size & filter-types. Bewusst klein gehalten, weil
# das Modal scrollbar ist und der User typischerweise filtert.
_CASUS_PAGE_SIZE = 50

# Filter-Type-Mapping: UI-String → event_types-Liste für die Query.
# ``""`` (leer) heißt: alle event_types.
_FILTER_TYPE_MAP: dict[str, list[str]] = {
    "all": [],
    "motion": ["motion"],
    "face": ["face_known", "face_unsure", "face_unknown"],
    "face_known": ["face_known"],
    "face_unsure": ["face_unsure"],
    "face_unknown": ["face_unknown"],
    "vlm": ["vlm_analysis"],
}


class CasusMixin(rx.State, mixin=True):
    """UI state for the Casus event-management modal."""

    casus_open: bool = False
    # Aktuelle Seite an Events (filter-/seitenbasiert nachgeladen).
    casus_events: list[dict[str, Any]] = []
    casus_total_count: int = 0
    casus_page: int = 0  # 0-basiert
    # Filter-Werte (UI-bindings). Default: alle.
    casus_filter_source: str = "all"
    casus_filter_type: str = "all"
    casus_filter_face: str = "all"  # "all" | "unknown" | <face_id as str>
    # Filter-Dropdown-Optionen, beim Modal-Open befüllt.
    casus_source_options: list[dict[str, str]] = []
    casus_face_options: list[dict[str, str]] = []
    # Status-Feedback (Toast-Ersatz).
    casus_status: str = ""
    # Tag-Modus pro Zeile: event_id der Zeile, die gerade getagged wird
    # (0 = niemand). Plus aktuell ausgewählte face_id im Dropdown.
    casus_tag_event_id: int = 0
    casus_tag_face_id: str = ""
    # Bulk-Delete: zweistufig — erst Button "Alle löschen" → setzt
    # ``casus_confirm_delete_all=True`` und tauscht den Button gegen
    # „Wirklich löschen?" + „Abbrechen", damit nichts versehentlich
    # weggeklickt wird.
    casus_confirm_delete_all: bool = False

    # ── Computed ─────────────────────────────────────────────────

    @rx.var
    def casus_page_label(self) -> str:
        """Anzeige-Label für die Pagination, z.B. "1–50 von 237"."""
        if self.casus_total_count == 0:
            return "0"
        first = self.casus_page * _CASUS_PAGE_SIZE + 1
        last = min(first + _CASUS_PAGE_SIZE - 1, self.casus_total_count)
        return f"{first}–{last} / {self.casus_total_count}"

    @rx.var
    def casus_has_prev(self) -> bool:
        return self.casus_page > 0

    @rx.var
    def casus_has_next(self) -> bool:
        return (self.casus_page + 1) * _CASUS_PAGE_SIZE < self.casus_total_count

    # ── Modal lifecycle ───────────────────────────────────────────

    @rx.event
    def open_casus(self) -> None:
        """Modal öffnen — Filter-Optionen laden + erste Seite holen."""
        self.casus_open = True
        self.casus_page = 0
        self.casus_status = ""
        self.casus_tag_event_id = 0
        self.casus_tag_face_id = ""
        self.casus_confirm_delete_all = False
        self._refresh_filter_options()
        self._refresh_events()

    @rx.event
    def close_casus(self) -> None:
        self.casus_open = False
        self.casus_tag_event_id = 0

    # ── Filter ────────────────────────────────────────────────────

    @rx.event
    def casus_set_filter_source(self, value: str) -> None:
        self.casus_filter_source = value or "all"
        self.casus_page = 0
        self.casus_confirm_delete_all = False
        self._refresh_events()

    @rx.event
    def casus_set_filter_type(self, value: str) -> None:
        self.casus_filter_type = value or "all"
        self.casus_page = 0
        self.casus_confirm_delete_all = False
        self._refresh_events()

    @rx.event
    def casus_set_filter_face(self, value: str) -> None:
        self.casus_filter_face = value or "all"
        self.casus_page = 0
        self.casus_confirm_delete_all = False
        self._refresh_events()

    @rx.event
    def casus_clear_filters(self) -> None:
        self.casus_filter_source = "all"
        self.casus_filter_type = "all"
        self.casus_filter_face = "all"
        self.casus_page = 0
        self.casus_confirm_delete_all = False
        self._refresh_events()

    # ── Bulk-Delete ──────────────────────────────────────────────

    @rx.event
    def casus_request_delete_all(self) -> None:
        """Erste Stufe: Button "Alle löschen" geklickt → in Confirm-
        Modus wechseln (zweiter Klick auf "Wirklich löschen?" führt
        dann erst aus). Verhindert versehentliches Massenlöschen."""
        self.casus_confirm_delete_all = True

    @rx.event
    def casus_cancel_delete_all(self) -> None:
        self.casus_confirm_delete_all = False

    @rx.event
    def casus_confirm_delete_all_now(self) -> None:
        """Zweite Stufe: bestätigtes Bulk-Delete. Löscht alle Events
        die den aktuell aktiven Filtern entsprechen — wenn keine
        Filter gesetzt sind, also wirklich alle."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            source = self.casus_filter_source
            source_id = source if source and source != "all" else None
            event_types = _FILTER_TYPE_MAP.get(self.casus_filter_type, [])
            face_filter = self.casus_filter_face
            face_id: int | None = None
            unknown_only = False
            if face_filter == "unknown":
                unknown_only = True
            elif face_filter and face_filter != "all":
                try:
                    face_id = int(face_filter)
                except (TypeError, ValueError):
                    face_id = None
            deleted = store.delete_events_filtered(
                source_id=source_id,
                event_types=event_types or None,
                face_id=face_id,
                unknown_only=unknown_only,
            )
            self.casus_status = f"✓ {deleted} Ereignis(se) gelöscht"
        except Exception as e:  # noqa: BLE001
            logger.warning("casus bulk-delete failed: %s", e)
            self.casus_status = f"⚠️ {e}"
        self.casus_confirm_delete_all = False
        self.casus_page = 0
        self._refresh_events()

    # ── Pagination ────────────────────────────────────────────────

    @rx.event
    def casus_prev_page(self) -> None:
        if self.casus_page > 0:
            self.casus_page -= 1
            self._refresh_events()

    @rx.event
    def casus_next_page(self) -> None:
        if (self.casus_page + 1) * _CASUS_PAGE_SIZE < self.casus_total_count:
            self.casus_page += 1
            self._refresh_events()

    # ── Aktionen ──────────────────────────────────────────────────

    @rx.event
    def casus_delete_event(self, event_id: int) -> None:
        """Einzelnes Event löschen."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            ok = store.delete_event(int(event_id))
            if ok:
                self.casus_status = "✓ Ereignis gelöscht"
            else:
                self.casus_status = "⚠️ Ereignis nicht gefunden"
        except Exception as e:  # noqa: BLE001
            logger.warning("casus delete failed: %s", e)
            self.casus_status = f"⚠️ {e}"
            return
        self._refresh_events()

    @rx.event
    def casus_start_tag(self, event_id: int) -> None:
        """Tag-Dropdown für eine Zeile öffnen."""
        self.casus_tag_event_id = int(event_id)
        self.casus_tag_face_id = ""
        self.casus_status = ""

    @rx.event
    def casus_cancel_tag(self) -> None:
        self.casus_tag_event_id = 0
        self.casus_tag_face_id = ""

    @rx.event
    def casus_set_tag_face(self, value: str) -> None:
        self.casus_tag_face_id = value or ""

    @rx.event
    def casus_save_tag(self) -> None:
        """Nachträgliches Taggen: Event einer Identity zuordnen.
        Falls ``casus_tag_face_id == "__clear__"`` wird die Zuordnung
        gelöscht (face_id → NULL)."""
        event_id = int(self.casus_tag_event_id)
        face_val = self.casus_tag_face_id
        if event_id <= 0 or not face_val:
            return
        face_id: int | None
        if face_val == "__clear__":
            face_id = None
        else:
            try:
                face_id = int(face_val)
            except (TypeError, ValueError):
                return
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            ok = store.set_event_face_id(event_id, face_id)
            if ok:
                self.casus_status = "✓ Zuordnung gespeichert"
            else:
                self.casus_status = "⚠️ Ereignis nicht gefunden"
        except Exception as e:  # noqa: BLE001
            logger.warning("casus tag failed: %s", e)
            self.casus_status = f"⚠️ {e}"
            return
        self.casus_tag_event_id = 0
        self.casus_tag_face_id = ""
        self._refresh_events()

    # ── Internals ────────────────────────────────────────────────

    def _refresh_filter_options(self) -> None:
        """Source- und Face-Dropdowns mit aktuellen DB-Inhalten füllen."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            sources = store.list_event_source_ids()
            self.casus_source_options = (
                [{"value": "all", "label": "Alle Quellen"}]
                + [{"value": s, "label": s} for s in sources]
            )
            faces = store.list_faces()
            face_opts: list[dict[str, str]] = [
                {"value": "all", "label": "Alle Personen"},
                {"value": "unknown", "label": "Nur unzugeordnete"},
            ]
            for f in faces:
                face_opts.append({
                    "value": str(int(f["id"])),
                    "label": str(f["name"]),
                })
            self.casus_face_options = face_opts
        except Exception as e:  # noqa: BLE001
            logger.warning("casus filter-options refresh failed: %s", e)
            self.casus_source_options = [{"value": "all", "label": "Alle Quellen"}]
            self.casus_face_options = [{"value": "all", "label": "Alle Personen"}]

    def _refresh_events(self) -> None:
        """Aktuelle Seite mit Filtern frisch aus dem Store holen.
        Setzt ``casus_events`` + ``casus_total_count``."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            source = self.casus_filter_source
            source_id = source if source and source != "all" else None
            event_types = _FILTER_TYPE_MAP.get(self.casus_filter_type, [])
            face_filter = self.casus_filter_face
            face_id: int | None = None
            unknown_only = False
            if face_filter == "unknown":
                unknown_only = True
            elif face_filter and face_filter != "all":
                try:
                    face_id = int(face_filter)
                except (TypeError, ValueError):
                    face_id = None
            offset = self.casus_page * _CASUS_PAGE_SIZE
            events = store.list_events_with_summary(
                source_id=source_id,
                event_types=event_types or None,
                face_id=face_id,
                unknown_only=unknown_only,
                limit=_CASUS_PAGE_SIZE,
                offset=offset,
            )
            self.casus_events = events
            self.casus_total_count = store.count_events(
                source_id=source_id,
                event_types=event_types or None,
                face_id=face_id,
                unknown_only=unknown_only,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("casus events refresh failed: %s", e)
            self.casus_events = []
            self.casus_total_count = 0
            self.casus_status = f"⚠️ {e}"

    # Statische Type-Filter-Werte. Labels rendert die UI per rx.match
    # gegen t()-Keys — so bleibt die Liste sprachreaktiv, ohne dass
    # der Mixin t() aufrufen muss (t() liefert eine Var, die in einer
    # Klassenkonstante nicht serialisierbar ist).
    casus_type_values: list[str] = [
        "all", "motion", "face", "face_known",
        "face_unsure", "face_unknown", "vlm",
    ]
