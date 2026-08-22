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
    # Vollständige, geordnete ID-Liste der gefilterten Menge (alle
    # Seiten) — treibt die seitenübergreifende Vollbild-Slideshow,
    # während das Raster seitenweise bleibt. Leichtgewichtig (nur IDs).
    casus_all_ids: list[int] = []
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
    # Auto-Refresh bei offenem Modal: zuletzt gesehene höchste Event-ID
    # + Poll-Drossel (Backend-Vars, kein Client-Sync).
    _casus_last_seen_event_id: int = 0
    _casus_last_poll_ts: float = 0.0
    # Bulk-Delete: zweistufig — erst Button "Alle löschen" → setzt
    # ``casus_confirm_delete_all=True`` und tauscht den Button gegen
    # „Wirklich löschen?" + „Abbrechen", damit nichts versehentlich
    # weggeklickt wird.
    casus_confirm_delete_all: bool = False
    # Single-Event-VLM-Analyse: ID des Events, das gerade analysiert
    # wird (0 = nichts läuft). Lässt die Zeile einen Spinner zeigen.
    casus_analyzing_event_id: int = 0
    # Volltext-Expand pro Zeile: event_id der Zeile, deren VLM-Beschreibung
    # ausgeklappt ist (0 = alle geklemmt). Klick auf den Text toggelt.
    casus_expanded_event_id: int = 0
    # Bild-Modal: globaler Index des aktuell groß angezeigten Events in
    # ``casus_all_ids`` (-1 = zu). Global statt seiten-lokal, damit die
    # Pfeil-Navigation seitenübergreifend durch ALLE Events blättert,
    # nicht nur durch die 50 der aktuellen Rasterseite.
    casus_image_index: int = -1
    # Bulk-Worker-State — alle Events ohne description durch das VLM
    # schicken, dedupliziert via pHash-Cluster (Story 3).
    casus_bulk_running: bool = False
    casus_bulk_total: int = 0
    casus_bulk_progress: int = 0
    casus_bulk_message: str = ""
    casus_bulk_cancel: bool = False
    # Cluster-Anzeige: bei True wird pro cluster_id nur der Repräsentant
    # (jüngster Eintrag) gezeigt + Badge „+N ähnliche". Idle off, weil
    # die meisten User „alle Events sehen" erwarten beim Modal-Open.
    casus_cluster_mode: bool = False
    # Hilfe-Modal — Klick auf die Glühbirne öffnet eine Erklärung
    # zu „Alle analysieren" + „Gruppiert".
    casus_help_open: bool = False

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
        # VLM-Power-Toggle braucht den realen Ollama-Status.
        try:
            from ..lib.vision_prewarm import is_vlm_loaded
            model = getattr(self, "vision_model_value", None)
            self.vlm_model_loaded = await is_vlm_loaded(model)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

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

    # ── Single-Event-Analyse ─────────────────────────────────────

    @rx.event
    async def casus_analyze_event(self, event_id: int) -> None:
        """VLM-Analyse für ein einzelnes Event. Klick im Casus.
        Schreibt Beschreibung in classification.description und
        lädt die Event-Liste neu, damit die Zeile aktualisiert wird."""
        if self.casus_analyzing_event_id:
            return  # einer reicht parallel
        eid = int(event_id)
        self.casus_analyzing_event_id = eid
        self.casus_status = ""
        try:
            from ..lib.vision_event_analysis import analyze_event_with_vlm
            text = await analyze_event_with_vlm(eid)
            self.casus_status = f"✓ Analysiert: {text[:60]}…" if len(text) > 60 else f"✓ {text}"
        except FileNotFoundError as e:
            self.casus_status = f"⚠️ Frame nicht mehr auf Disk: {e}"
        except Exception as e:  # noqa: BLE001
            logger.warning("casus_analyze_event %d failed: %s", eid, e)
            self.casus_status = f"⚠️ {e}"
        finally:
            self.casus_analyzing_event_id = 0
            self._refresh_events()

    # ── Bulk-VLM-Analyse ─────────────────────────────────────────

    @rx.event(background=True)  # type: ignore[operator]
    async def casus_bulk_start(self):
        """Bulk-Worker: alle Events ohne description durch pHash-Dedup
        clustern, pro Cluster einen VLM-Call, Beschreibung auf alle
        Mitglieder anwenden.

        Die Orchestrierung lebt headless in ``vision_bulk.run_bulk_describe``
        (SSOT, geteilt mit Nacht-Lauf + Chat-Hook). Hier bleibt nur die
        Reflex-Verdrahtung: Fortschrittsbalken + Abbrechen-Flag werden
        über zwei Callbacks an den Worker gebrückt. ``async with self``
        ist Reflex 0.8 standard für background-events.
        """
        from ..lib.vision_bulk import run_bulk_describe

        async with self:
            if self.casus_bulk_running:
                return
            self.casus_bulk_running = True
            self.casus_bulk_cancel = False
            self.casus_bulk_progress = 0
            self.casus_bulk_total = 0
            self.casus_bulk_message = "Prüfe VRAM …"

        async def _progress(processed: int, total: int, message: str | None) -> None:
            async with self:
                self.casus_bulk_total = total
                self.casus_bulk_progress = processed
                if message is not None:
                    self.casus_bulk_message = message

        async def _cancel() -> bool:
            async with self:
                return self.casus_bulk_cancel

        try:
            # check_vram=True: refuse to start when the VLM fits on no GPU.
            # Without free VRAM next to a large resident LLM Ollama silently
            # falls back to CPU offload — each call then runs for minutes and
            # the whole bulk run grinds (observed with the 397B loaded). The
            # pre-check turns that into an upfront message instead.
            result = await run_bulk_describe(
                progress_cb=_progress, cancel_cb=_cancel, check_vram=True,
            )
            async with self:
                if result.skipped:
                    self.casus_bulk_message = "Läuft bereits — bitte warten"
                elif result.aborted_vram:
                    self.casus_bulk_message = f"⚠️ {result.vram_message}"
                elif result.total_events == 0:
                    self.casus_bulk_message = "Keine Events zum Analysieren"
                elif result.cancelled:
                    self.casus_bulk_progress = result.processed
                    self.casus_bulk_message = (
                        f"Abgebrochen — {result.processed} / "
                        f"{result.total_clusters} Cluster analysiert "
                        f"({result.failed} Fehler)"
                    )
                else:
                    self.casus_bulk_progress = result.total_clusters
                    self.casus_bulk_message = (
                        f"Fertig — {result.total_clusters} Cluster analysiert"
                        + (f", {result.failed} Fehler" if result.failed else "")
                    )
                self._refresh_events()
        except Exception as e:  # noqa: BLE001
            logger.warning("bulk worker crashed: %s", e)
            async with self:
                self.casus_bulk_message = f"⚠️ Fehler: {e}"
        finally:
            async with self:
                self.casus_bulk_running = False
                self.casus_bulk_cancel = False

    @rx.event
    def casus_bulk_cancel_run(self) -> None:
        """Setzt das Cancel-Flag — der Background-Worker checkt es
        zwischen den Cluster-VLM-Calls."""
        if self.casus_bulk_running:
            self.casus_bulk_cancel = True
            self.casus_bulk_message = "Wird abgebrochen …"

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
        except Exception:  # noqa: BLE001
            return
        if latest == self._casus_last_seen_event_id:
            return
        if self.casus_tag_event_id != 0:
            return  # Tag-Modus aktiv — nachziehen beim nächsten Tick danach
        if self.casus_image_index >= 0:
            return  # Slideshow/Film offen — Refresh würde die Liste kapern
        self._casus_last_seen_event_id = latest
        self._refresh_events()

    def _resolve_casus_filters(self) -> tuple[str | None, list[str], int | None, bool]:
        """Aktive Filter-Vars in Store-Query-Parameter auflösen (SSOT für
        Event-Liste und Bulk-Delete): (source_id, event_types, face_id,
        unknown_only)."""
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
        return source_id, event_types, face_id, unknown_only

    @rx.event
    def casus_confirm_delete_all_now(self) -> None:
        """Zweite Stufe: bestätigtes Bulk-Delete. Löscht alle Events
        die den aktuell aktiven Filtern entsprechen — wenn keine
        Filter gesetzt sind, also wirklich alle."""
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            source_id, event_types, face_id, unknown_only = self._resolve_casus_filters()
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

    @rx.event
    def casus_show_image_at(self, index: int) -> None:
        """Event-Frame groß anzeigen (Klick aufs Thumbnail). Der Klick
        liefert den Rasterseiten-Index; wir lösen ihn über die Event-ID
        zum globalen Index in ``casus_all_ids`` auf, damit die Slideshow
        anschließend seitenübergreifend navigiert."""
        i = int(index)
        if i < 0 or i >= len(self.casus_events):
            return
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
        except Exception as e:  # noqa: BLE001
            logger.warning("casus film open failed: %s", e)
            return
        if not ids:
            ids = [int(event_id)]
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
            self.casus_image_index -= 1

    @rx.event
    def casus_image_older(self) -> None:
        """Älteres Event (Pfeil links = Vergangenheit) → höherer Index.
        Stoppt am Ende der gesamten gefilterten Liste."""
        if self.casus_image_index < len(self.casus_all_ids) - 1:
            self.casus_image_index += 1

    @rx.event
    def casus_close_image(self) -> None:
        """Bild-Modal schließen."""
        self.casus_image_index = -1

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
            # Leere source_ids überspringen — z.B. vlm_analysis-Events aus
            # einem Upload/manuellen Analyze ohne Kamera-Quelle. Ein leerer
            # Wert würde sonst ein <Select.Item value=""> erzeugen, das Radix
            # ablehnt ("must have a value prop that is not an empty string")
            # und damit das ganze Casus-Modal am Rendern hindert.
            sources = [s for s in store.list_event_source_ids() if str(s).strip()]
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
        Setzt ``casus_events`` + ``casus_total_count``.

        Cluster-Mode (``casus_cluster_mode=True``): die Server-Seite
        liefert eine größere Roh-Liste (5000 Events), Python gruppiert
        per ``cluster_id`` — pro Cluster der jüngste Member als
        Repräsentant + ``cluster_member_count`` als Badge-Zähler.
        Solo-Events (cluster_id leer) bleiben unverändert.
        """
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            source_id, event_types, face_id, unknown_only = self._resolve_casus_filters()

            if self.casus_cluster_mode:
                # Größere Liste holen + Python-side gruppieren.
                raw = store.list_events_with_summary(
                    source_id=source_id,
                    event_types=event_types or None,
                    face_id=face_id,
                    unknown_only=unknown_only,
                    limit=5000,
                    offset=0,
                )
                from collections import OrderedDict
                cluster_groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
                solo_events: list[dict[str, Any]] = []
                for e in raw:
                    cid = str(e.get("cluster_id") or "")
                    if cid:
                        cluster_groups.setdefault(cid, []).append(e)
                    else:
                        solo_events.append(e)
                # Repräsentant pro Cluster = erstes Element (= jüngster,
                # weil DESC sortiert). Badge-Zahl = Mitglieder.
                cluster_repr: list[dict[str, Any]] = []
                for cid, members in cluster_groups.items():
                    rep = dict(members[0])
                    rep["cluster_member_count"] = len(members)
                    cluster_repr.append(rep)
                # Solos bekommen member_count=0 (UI: kein Badge).
                for s in solo_events:
                    s_copy = dict(s)
                    s_copy["cluster_member_count"] = 0
                    cluster_repr.append(s_copy)
                # Wieder nach Timestamp DESC sortieren (Mischung von
                # Cluster-Reps + Solos).
                cluster_repr.sort(key=lambda x: x["timestamp"], reverse=True)
                total = len(cluster_repr)
                start = self.casus_page * _CASUS_PAGE_SIZE
                end = start + _CASUS_PAGE_SIZE
                self.casus_events = cluster_repr[start:end]
                self.casus_total_count = total
                # Slideshow läuft im Cluster-Modus über die Repräsentanten
                # (eine ID pro Vorkommnis), passend zur Rasteransicht.
                self.casus_all_ids = [int(r["id"]) for r in cluster_repr]
            else:
                offset = self.casus_page * _CASUS_PAGE_SIZE
                events = store.list_events_with_summary(
                    source_id=source_id,
                    event_types=event_types or None,
                    face_id=face_id,
                    unknown_only=unknown_only,
                    limit=_CASUS_PAGE_SIZE,
                    offset=offset,
                )
                # Im flachen Modus: keine Cluster-Badge.
                for e in events:
                    e["cluster_member_count"] = 0
                self.casus_events = events
                self.casus_total_count = store.count_events(
                    source_id=source_id,
                    event_types=event_types or None,
                    face_id=face_id,
                    unknown_only=unknown_only,
                )
                # Volle ID-Liste (alle Seiten) für die Slideshow.
                self.casus_all_ids = store.list_event_ids(
                    source_id=source_id,
                    event_types=event_types or None,
                    face_id=face_id,
                    unknown_only=unknown_only,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("casus events refresh failed: %s", e)
            self.casus_events = []
            self.casus_total_count = 0
            self.casus_all_ids = []
            self.casus_status = f"⚠️ {e}"

    # Statische Type-Filter-Werte. Labels rendert die UI per rx.match
    # gegen t()-Keys — so bleibt die Liste sprachreaktiv, ohne dass
    # der Mixin t() aufrufen muss (t() liefert eine Var, die in einer
    # Klassenkonstante nicht serialisierbar ist).
    casus_type_values: list[str] = [
        "all", "motion", "face", "face_known",
        "face_unsure", "face_unknown", "vlm",
    ]
