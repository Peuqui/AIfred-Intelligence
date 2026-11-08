"""Casus — Ereignis-Verwaltungs-Modal.

Chronologische Liste aller Vision-Events (motion / face_known /
face_unsure / face_unknown / vlm_analysis) mit Filtern (Quelle, Typ,
Identity) und Aktionen pro Zeile: Event löschen, unbekanntes Gesicht
nachträglich einer Person zuordnen.

Liest direkt aus ``VisionStore`` (keine API), Writes ebenfalls.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import reflex as rx

from ..lib.casus import TYPE_FILTERS, EventFilter
from ..lib.config import VISION_CASUS_PAGE_SIZE as _CASUS_PAGE_SIZE
from ..lib.user_error import UserError

logger = logging.getLogger(__name__)


class CasusMixin(rx.State, mixin=True):
    """UI state for the Casus event-management modal."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        ui_language: rx.Field[str]
        vlm_model_loaded: rx.Field[bool]

    casus_open: rx.Field[bool] = rx.field(False)
    # Aktuelle Seite an Events (filter-/seitenbasiert nachgeladen).
    casus_events: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)
    casus_total_count: rx.Field[int] = rx.field(0)
    casus_page: rx.Field[int] = rx.field(0)  # 0-basiert
    # Vollständige, geordnete ID-Liste der gefilterten Menge (alle
    # Seiten) — treibt die seitenübergreifende Vollbild-Slideshow,
    # während das Raster seitenweise bleibt. Leichtgewichtig (nur IDs).
    casus_all_ids: rx.Field[list[int]] = rx.field(default_factory=list)
    # Filter-Werte (UI-bindings). Default: alle.
    casus_filter_source: rx.Field[str] = rx.field("all")
    casus_filter_type: rx.Field[str] = rx.field("all")
    casus_filter_face: rx.Field[str] = rx.field("all")  # "all" | "unknown" | <face_id as str>
    # Filter-Dropdown-Optionen, beim Modal-Open befüllt.
    casus_source_options: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)
    casus_face_options: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)
    # Status-Feedback (Toast-Ersatz).
    casus_status: rx.Field[str] = rx.field("")
    # Tag-Modus pro Zeile: event_id der Zeile, die gerade getagged wird
    # (0 = niemand). Plus aktuell ausgewählte face_id im Dropdown.
    casus_tag_event_id: rx.Field[int] = rx.field(0)
    casus_tag_face_id: rx.Field[str] = rx.field("")
    # Auto-Refresh bei offenem Modal: zuletzt gesehene höchste Event-ID
    # + Poll-Drossel (Backend-Vars, kein Client-Sync).
    _casus_last_seen_event_id: int = 0
    _casus_last_poll_ts: float = 0.0
    # Bulk-Delete: zweistufig — erst Button "Alle löschen" → setzt
    # ``casus_confirm_delete_all=True`` und tauscht den Button gegen
    # „Wirklich löschen?" + „Abbrechen", damit nichts versehentlich
    # weggeklickt wird.
    casus_confirm_delete_all: rx.Field[bool] = rx.field(False)
    # Single-Event-VLM-Analyse: ID des Events, das gerade analysiert
    # wird (0 = nichts läuft). Lässt die Zeile einen Spinner zeigen.
    casus_analyzing_event_id: rx.Field[int] = rx.field(0)
    # Volltext-Expand pro Zeile: event_id der Zeile, deren VLM-Beschreibung
    # ausgeklappt ist (0 = alle geklemmt). Klick auf den Text toggelt.
    casus_expanded_event_id: rx.Field[int] = rx.field(0)
    # Bild-Modal: globaler Index des aktuell groß angezeigten Events in
    # ``casus_all_ids`` (-1 = zu). Global statt seiten-lokal, damit die
    # Pfeil-Navigation seitenübergreifend durch ALLE Events blättert,
    # nicht nur durch die 50 der aktuellen Rasterseite.
    casus_image_index: rx.Field[int] = rx.field(-1)
    # Bulk-Worker-State — alle Events ohne description durch das VLM
    # schicken, dedupliziert via pHash-Cluster (Story 3).
    casus_bulk_running: rx.Field[bool] = rx.field(False)
    casus_bulk_total: rx.Field[int] = rx.field(0)
    casus_bulk_progress: rx.Field[int] = rx.field(0)
    casus_bulk_message: rx.Field[str] = rx.field("")
    # Cluster-Anzeige: bei True wird pro cluster_id nur der Repräsentant
    # (jüngster Eintrag) gezeigt + Badge „+N ähnliche". Idle off, weil
    # die meisten User „alle Events sehen" erwarten beim Modal-Open.
    casus_cluster_mode: rx.Field[bool] = rx.field(False)
    # Hilfe-Modal — Klick auf die Glühbirne öffnet eine Erklärung
    # zu „Alle analysieren" + „Gruppiert".
    casus_help_open: rx.Field[bool] = rx.field(False)

    @rx.event
    def open_casus_help(self) -> None:
        self.casus_help_open = True

    @rx.event
    def close_casus_help(self) -> None:
        self.casus_help_open = False

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
    async def open_casus(self) -> None:
        """Modal öffnen — Filter-Optionen laden, erste Seite holen,
        VLM-Status für den Power-Toggle im Header abfragen."""
        self.casus_open = True
        self.casus_page = 0
        self.casus_status = ""
        self.casus_tag_event_id = 0
        self.casus_tag_face_id = ""
        self.casus_confirm_delete_all = False
        self._refresh_filter_options()
        self._refresh_events()
        # VLM-Power-Toggle braucht den realen Ollama-Status (is_vlm_loaded
        # fängt ihre Fehler selbst und meldet dann False).
        from ..lib.vision_prewarm import is_vlm_loaded
        model = getattr(self, "vision_model_value", None)
        self.vlm_model_loaded = await is_vlm_loaded(model)

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
    def casus_toggle_cluster_mode(self, value: bool) -> None:
        """Schalter „Gruppiert" — Cluster-Mitglieder mit gleicher
        cluster_id werden zu einer Zeile zusammengefasst."""
        self.casus_cluster_mode = bool(value)
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

    # ── Bildmodell: ein Ereignis, alle ohne Beschreibung (Läufe des Servers, lib.casus) ──

    @rx.event
    async def casus_analyze_event(self, event_id: int) -> None:
        """Ein Ereignis vom Bildmodell beschreiben lassen (``lib.casus.start_analysis``, eins zur Zeit für alle
        Oberflächen); danach steht die Beschreibung in der Zeile."""
        from ..lib.casus import RunInProgress, analysis, start_analysis
        from ..lib.vision_store import VisionStore

        eid = int(event_id)
        try:
            run = start_analysis(eid)
        except RunInProgress:  # started here or on the Vue page: one at a time
            return
        except UserError as e:  # no such event, or it kept no picture
            self.casus_status = f"⚠️ {e.message(self.ui_language)}"
            return
        self.casus_analyzing_event_id = eid
        self.casus_status = ""
        await run
        self.casus_analyzing_event_id = 0
        error = analysis().error
        if error:
            self.casus_status = f"⚠️ {error}"
        else:
            event = VisionStore().get_event(eid) or {}
            text = str((event.get("classification") or {}).get("description") or "")
            self.casus_status = f"✓ {text[:60]}…" if len(text) > 60 else f"✓ {text}"
        self._refresh_events()

    def _casus_show_bulk(self) -> None:
        from ..lib.casus import describe_all

        state = describe_all()
        self.casus_bulk_running = state.running
        self.casus_bulk_total = state.clusters
        self.casus_bulk_progress = state.processed
        self.casus_bulk_message = state.message(self.ui_language)

    @rx.event(background=True)
    async def casus_bulk_start(self):
        """Alle Ereignisse ohne Beschreibung beschreiben lassen (``lib.casus.start_describe_all``, ein Lauf zur Zeit
        für alle Oberflächen); der Fortschritt wird jede Sekunde übernommen."""
        import asyncio

        from ..lib.casus import RunInProgress, start_describe_all

        async with self:
            try:
                run = start_describe_all()
            except RunInProgress:  # started here or on the Vue page: its state shows
                run = None
            self._casus_show_bulk()
        if run is None:
            return
        while not run.done():
            await asyncio.wait({run}, timeout=1)
            async with self:
                self._casus_show_bulk()
        async with self:
            self._refresh_events()

    @rx.event
    def casus_bulk_cancel_run(self) -> None:
        """Den Lauf nach dem gerade beschriebenen Vorkommnis anhalten."""
        from ..lib.casus import cancel_describe_all

        cancel_describe_all()
        self._casus_show_bulk()

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

    def _casus_poll_new_events(self) -> None:
        """Vom globalen 500-ms-Tick gerufen (siehe _base): lädt die
        Ereignisliste nach, sobald NEUE Events in der DB liegen — billiger
        max(id)-Check, echtes Neuladen nur bei Änderung, und nie während
        der User gerade eine Zeile taggt (Refresh würde das Dropdown
        wegreißen)."""
        if not self.casus_open:
            return
        import time
        now = time.monotonic()
        if now - self._casus_last_poll_ts < 2.0:
            return
        self._casus_last_poll_ts = now
        try:
            from ..lib.vision_store import VisionStore
            latest = VisionStore().latest_event_id()
        except sqlite3.Error:  # busy database: the next poll retries
            return
        if latest == self._casus_last_seen_event_id:
            return
        if self.casus_tag_event_id != 0:
            return  # Tag-Modus aktiv — nachziehen beim nächsten Tick danach
        if self.casus_image_index >= 0:
            return  # Slideshow/Film offen — Refresh würde die Liste kapern
        self._casus_last_seen_event_id = latest
        self._refresh_events()

    def _casus_filter(self) -> EventFilter:
        """Die Filter der Oberfläche als ``lib.casus.EventFilter`` (SSOT für Liste und Löschen nach Filter);
        die Quelle "all" der Auswahl heißt dort "" (Radix nimmt keinen leeren Wert)."""
        source = self.casus_filter_source
        return EventFilter(source="" if source == "all" else source, type=self.casus_filter_type,
                           face=self.casus_filter_face)

    @rx.event
    def casus_confirm_delete_all_now(self) -> None:
        """Zweite Stufe: bestätigtes Bulk-Delete. Löscht alle Events
        die den aktuell aktiven Filtern entsprechen — wenn keine
        Filter gesetzt sind, also wirklich alle."""
        try:
            from ..lib.casus import delete_matching
            deleted = delete_matching(self._casus_filter())
            self.casus_status = f"✓ {deleted} Ereignis(se) gelöscht"
        except (sqlite3.Error, OSError) as e:  # the events and their frames on disk
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
        except (sqlite3.Error, OSError) as e:  # the event and its frames on disk
            logger.warning("casus delete failed: %s", e)
            self.casus_status = f"⚠️ {e}"
            return
        self._refresh_events()

    @rx.event
    def casus_toggle_expand(self, event_id: int) -> None:
        """Volltext einer VLM-Beschreibung aus-/einklappen (Klick auf den Text)."""
        eid = int(event_id)
        self.casus_expanded_event_id = 0 if self.casus_expanded_event_id == eid else eid

    @rx.var
    def casus_image_open(self) -> bool:
        """Ist das Bild-Modal offen?"""
        return self.casus_image_index >= 0

    @rx.var
    def casus_image_src(self) -> str:
        """Vollbild-URL des aktuell angezeigten Events (leer wenn zu).
        Indexiert die seitenübergreifende ``casus_all_ids``-Liste."""
        i = self.casus_image_index
        if i < 0 or i >= len(self.casus_all_ids):
            return ""
        return "/api/vision/frame?id=" + str(self.casus_all_ids[i])

    @rx.var(auto_deps=False, deps=["casus_image_index", "casus_all_ids"])
    def casus_image_zoom_src(self) -> str:
        """Zoom-URL des aktuell angezeigten Events — leer, wenn das Event
        keinen Tele-Snap hat (das Modal zeigt dann nur das Weitwinkel).
        Der Existenz-Check braucht ohnehin die DB (Primary-Key-Lookup),
        die URL selbst läuft über den Frame-Endpoint. deps explizit:
        Reflex' Auto-Analyse scheitert am Import im Funktionskörper."""
        i = self.casus_image_index
        if i < 0 or i >= len(self.casus_all_ids):
            return ""
        eid = self.casus_all_ids[i]
        try:
            from ..lib.vision_store import VisionStore
            has_zoom = bool(VisionStore().get_event_frame_path(eid, zoom=True))
        except sqlite3.Error as e:
            logger.warning("casus zoom lookup failed: %s", e)
            return ""
        return f"/api/vision/frame?id={eid}&zoom=1" if has_zoom else ""

    @rx.var
    def casus_image_counter(self) -> str:
        """Position in der GESAMTEN gefilterten Liste, z.B. „1340 / 1344".

        Nummerierung nach Intuition: höchste Zahl = jüngstes Event. Da
        ``casus_all_ids`` neueste-zuerst (Index 0 = jüngstes) sortiert ist,
        zählen wir invers — Index 0 → N, ältestes → 1. Damit geht „Pfeil
        rechts" (neuer, Richtung Gegenwart) auf eine höhere Zahl zu."""
        if self.casus_image_index < 0 or not self.casus_all_ids:
            return ""
        total = len(self.casus_all_ids)
        return f"{total - self.casus_image_index} / {total}"

    # Klick-Zoom im Bild-Modal, einheitlich mit der Lightbox
    # (aifred/ui/lightbox.py): Stufe pro Ansicht (1 = eingepasst, 2 = 200 %
    # Breite mit Scroll). Klick aufs Bild: 1 → 2 → Modal schließen;
    # Navigation/Öffnen/Schließen setzt die Stufen zurück.
    casus_image_scale_wide: rx.Field[int] = rx.field(1)
    casus_image_scale_zoom: rx.Field[int] = rx.field(1)

    @rx.event
    def casus_image_cycle_scale(self, which: str) -> None:
        """Zoom-Stufe der geklickten Ansicht weiterschalten; auf der
        letzten Stufe schließt der Klick das Bild-Modal."""
        scale = self.casus_image_scale_zoom if which == "zoom" else self.casus_image_scale_wide
        if scale >= 2:
            self._casus_close_image_modal()
        elif which == "zoom":
            self.casus_image_scale_zoom = scale + 1
        else:
            self.casus_image_scale_wide = scale + 1

    def _casus_reset_image_scale(self) -> None:
        self.casus_image_scale_wide = 1
        self.casus_image_scale_zoom = 1

    @rx.var
    def casus_image_at_newest(self) -> bool:
        """Steht die Slideshow am jüngsten Bild? → Pfeil rechts ausgrauen."""
        return self.casus_image_index == 0

    @rx.var
    def casus_image_at_oldest(self) -> bool:
        """Steht die Slideshow am ältesten Bild? → Pfeil links ausgrauen."""
        return self.casus_image_index >= len(self.casus_all_ids) - 1

    @rx.event
    def casus_show_image_at(self, index: int) -> None:
        """Event-Frame groß anzeigen (Klick aufs Thumbnail). Der Klick
        liefert den Rasterseiten-Index; wir lösen ihn über die Event-ID
        zum globalen Index in ``casus_all_ids`` auf, damit die Slideshow
        anschließend seitenübergreifend navigiert."""
        i = int(index)
        if i < 0 or i >= len(self.casus_events):
            return
        self._casus_reset_image_scale()
        eid = int(self.casus_events[i].get("id", 0))
        # Gruppiert-Modus: der Klick auf ein Vorkommnis öffnet dessen FILM
        # (alle Cluster-Bilder chronologisch) — das "+N"-Badge verspricht
        # genau diese Serie. Die Repräsentanten-Slideshow wäre hier das
        # falsche Versprechen (sie blättert zum NÄCHSTEN Vorkommnis).
        if self.casus_cluster_mode:
            self._open_film(eid)
            return
        try:
            self.casus_image_index = self.casus_all_ids.index(eid)
        except ValueError:
            self.casus_image_index = -1

    def _open_film(self, event_id: int) -> None:
        """„Film anschauen": Slideshow über ALLE Bilder des Vorkommnisses
        (Cluster) dieses Events, chronologisch von Anfang an — die
        Burst-Serie eines Vorbeigangs als Daumenkino. Nutzt die bestehende
        Bild-Slideshow; beim nächsten Listen-Refresh (Modal-Interaktion)
        wird ``casus_all_ids`` ohnehin wieder auf die Filteransicht
        gesetzt."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            ev = store.get_event(int(event_id))
            cid = str((ev or {}).get("cluster_id") or "")
            ids = store.list_cluster_event_ids(cid)
        except sqlite3.Error as e:
            logger.warning("casus film open failed: %s", e)
            return
        if not ids:
            ids = [int(event_id)]
        self._casus_reset_image_scale()
        self.casus_all_ids = ids
        # Ältestes Bild zuerst — Pfeil rechts spielt den Film vorwärts.
        self.casus_image_index = len(ids) - 1

    @rx.event
    def casus_open_film(self, event_id: int) -> None:
        self._open_film(int(event_id))

    @rx.event
    def casus_image_newer(self) -> None:
        """Neueres Event (Pfeil rechts = Zukunft). ``casus_all_ids`` ist
        neueste-zuerst sortiert → Richtung niedrigerer Index. Stoppt oben."""
        if self.casus_image_index > 0:
            self._casus_reset_image_scale()
            self.casus_image_index -= 1

    @rx.event
    def casus_image_older(self) -> None:
        """Älteres Event (Pfeil links = Vergangenheit) → höherer Index.
        Stoppt am Ende der gesamten gefilterten Liste."""
        if self.casus_image_index < len(self.casus_all_ids) - 1:
            self._casus_reset_image_scale()
            self.casus_image_index += 1

    def _casus_close_image_modal(self) -> None:
        self._casus_reset_image_scale()
        self.casus_image_index = -1

    @rx.event
    def casus_close_image(self) -> None:
        """Bild-Modal schließen."""
        self._casus_close_image_modal()

    @rx.event
    def casus_refresh(self) -> None:
        """Ereignisliste manuell neu laden (Aktualisieren-Button). Bewusst
        manuell statt Auto-Refresh: Casus ist Verwaltungs-Ansicht (filtern/
        taggen/löschen) — eine sich selbst aktualisierende Liste würde
        Scroll/Tagging stören. Der Live-Strom läuft im Vigilantia-Popover."""
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
        except sqlite3.Error as e:
            logger.warning("casus tag failed: %s", e)
            self.casus_status = f"⚠️ {e}"
            return
        self.casus_tag_event_id = 0
        self.casus_tag_face_id = ""
        self._refresh_events()

    # ── Internals ────────────────────────────────────────────────

    def _refresh_filter_options(self) -> None:
        """Quellen- und Personen-Auswahl aus ``lib.casus.choices`` (nur Quellen, für die Ereignisse vorliegen)."""
        from ..lib.casus import FACE_ALL, FACE_UNCONFIRMED, choices
        from ..lib.i18n import t

        lang = self.ui_language
        try:
            offered = choices(lang)
        except sqlite3.Error as e:
            logger.warning("casus filter-options refresh failed: %s", e)
            self.casus_status = f"⚠️ {e}"
            return
        self.casus_source_options = [{"value": "all", "label": t("casus_source_all", lang=lang)}] + [
            {"value": source, "label": label} for source, label in offered.sources
        ]
        self.casus_face_options = [
            {"value": FACE_ALL, "label": t("casus_face_all", lang=lang)},
            {"value": FACE_UNCONFIRMED, "label": t("casus_face_unconfirmed", lang=lang)},
        ] + [{"value": str(face_id), "label": name} for face_id, name in offered.faces]

    def _refresh_events(self) -> None:
        """Aktuelle Seite mit Filtern frisch holen (``lib.casus.page``; gruppiert: eine Zeile je Vorkommnis,
        gezählt in SQL). Setzt ``casus_events``, ``casus_total_count`` und ``casus_all_ids`` (flach: alle IDs der
        Filtermenge für die Diaschau; gruppiert öffnet ein Klick den Film des Vorkommnisses)."""
        from ..lib.casus import page
        from ..lib.vision_store import VisionStore

        events_filter = self._casus_filter()
        try:
            events, total = page(events_filter, grouped=self.casus_cluster_mode,
                                 offset=self.casus_page * _CASUS_PAGE_SIZE, limit=_CASUS_PAGE_SIZE)
            all_ids = ([int(event["id"]) for event in events] if self.casus_cluster_mode
                       else VisionStore().list_event_ids(**events_filter.query()))
        except sqlite3.Error as e:
            logger.warning("casus events refresh failed: %s", e)
            self.casus_events = []
            self.casus_total_count = 0
            self.casus_all_ids = []
            self.casus_status = f"⚠️ {e}"
            return
        self.casus_events = events
        self.casus_total_count = total
        self.casus_all_ids = all_ids

    # Statische Type-Filter-Werte. Labels rendert die UI per rx.match
    # gegen t()-Keys — so bleibt die Liste sprachreaktiv, ohne dass
    # der Mixin t() aufrufen muss (t() liefert eine Var, die in einer
    # Klassenkonstante nicht serialisierbar ist).
    casus_type_values: rx.Field[list[str]] = rx.field(default_factory=lambda: list(TYPE_FILTERS))
