"""Storage-management mixin — der „Speicher"-Tab im Agent-Editor.

Verwaltet die ungeräumten lokalen Datei-Stores (Chat-Exporte +
Sandbox-Outputs) MANUELL: auflisten, öffnen, in den GitHub-Showcase
übernehmen, löschen. Die Datei-Operationen selbst liegen als SSOT in
``lib/storage_manager.py`` — dieses Mixin ist nur die State-/Event-Schicht.
"""

from __future__ import annotations

import reflex as rx


class StorageMixin(rx.State, mixin=True):
    """State für den Speicher-Tab im Agent-Editor."""

    storage_files: list[dict] = []
    # ID, für die gerade eine Lösch-Bestätigung aussteht (zweistufig).
    storage_confirm_delete_id: str = ""
    # Zuletzt in den Showcase kopierter Dateiname (für einen UI-Hinweis).
    storage_last_showcase: str = ""
    # Angehakte file_ids (Mehrfachauswahl) + Bestätigung für „alles löschen".
    storage_selected: list[str] = []
    storage_confirm_clear: bool = False

    def load_storage_files(self) -> None:
        """Datei-Liste aus dem SSOT neu laden. Normale Methode (kein
        Event-Decorator), damit ``set_agent_editor_tab`` sie direkt beim
        Tab-Wechsel aufrufen kann."""
        from ..lib.storage_manager import list_managed_files
        self.storage_files = list_managed_files()
        self.storage_confirm_delete_id = ""
        self.storage_selected = []
        self.storage_confirm_clear = False

    @rx.event
    def refresh_storage_files(self) -> None:
        """Explizites Neuladen (Refresh-Button)."""
        self.load_storage_files()

    @rx.event
    def storage_open_file(self, url: str):
        """Datei in neuem Browser-Tab öffnen."""
        return rx.call_script(f'window.open("{url}", "_blank");')

    @rx.event
    def storage_request_delete(self, file_id: str) -> None:
        """Lösch-Bestätigung anfordern (zweistufig)."""
        self.storage_confirm_delete_id = file_id

    @rx.event
    def storage_cancel_delete(self) -> None:
        self.storage_confirm_delete_id = ""

    @rx.event
    def storage_confirm_delete(self, file_id: str) -> None:
        """Datei endgültig löschen + Liste neu laden."""
        from ..lib.storage_manager import delete_managed_file
        if delete_managed_file(file_id):
            self.add_debug(f"🗑️ Storage: deleted {file_id}")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"⚠️ Storage: delete failed for {file_id}")  # type: ignore[attr-defined]
        self.load_storage_files()

    @rx.event
    def storage_toggle_select(self, file_id: str) -> None:
        """Eine Datei an-/abhaken (Mehrfachauswahl)."""
        if file_id in self.storage_selected:
            self.storage_selected = [x for x in self.storage_selected if x != file_id]
        else:
            self.storage_selected = [*self.storage_selected, file_id]

    @rx.event
    def storage_select_all(self) -> None:
        """Alle aktuell gelisteten Dateien anhaken."""
        self.storage_selected = [str(f["id"]) for f in self.storage_files]

    @rx.event
    def storage_delete_selected(self) -> None:
        """Die angehakten Dateien löschen + Liste neu laden."""
        if not self.storage_selected:
            return
        from ..lib.storage_manager import delete_managed_files
        n = delete_managed_files(list(self.storage_selected))
        self.add_debug(f"🗑️ Storage: {n} selected files deleted")  # type: ignore[attr-defined]
        self.load_storage_files()

    @rx.event
    def storage_request_clear(self) -> None:
        """Bestätigung für „alles löschen" anfordern."""
        self.storage_confirm_clear = True

    @rx.event
    def storage_cancel_clear(self) -> None:
        self.storage_confirm_clear = False

    @rx.event
    def storage_clear_all(self) -> None:
        """ALLE verwalteten Dateien löschen (nach Bestätigung) + neu laden."""
        from ..lib.storage_manager import delete_managed_files
        n = delete_managed_files([str(f["id"]) for f in self.storage_files])
        self.add_debug(f"🗑️ Storage: all {n} files deleted")  # type: ignore[attr-defined]
        self.load_storage_files()

    @rx.event
    def storage_copy_to_showcase(self, file_id: str) -> None:
        """Export-HTML in den GitHub-Showcase (docs/examples/) übernehmen."""
        from ..lib.storage_manager import copy_to_showcase
        name = copy_to_showcase(file_id)
        if name:
            self.storage_last_showcase = name
            self.add_debug(f"📺 Storage: copied to showcase: {name}")  # type: ignore[attr-defined]
        else:
            self.add_debug(f"⚠️ Storage: showcase copy failed for {file_id}")  # type: ignore[attr-defined]
