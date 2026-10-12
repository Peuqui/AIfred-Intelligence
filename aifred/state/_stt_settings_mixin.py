"""STT-Settings-Mixin — der „STT"-Tab im Agent-Editor.

AIfred stellt nur ein einziges Ding selbst ein: auf welchem Gerät es transkribieren lassen will
(``stt_device`` in settings.json: ``auto``, ``cpu`` oder ``cuda``) — eine Regel des Clients.
Engine, Modell, Genauigkeit je Gerät, die Reihenfolge der Automatik, Puffer und Burn-in liegen
als SSOT im STT-Dienst und werden auf dessen eigener Oberfläche (``/ui/``) eingestellt.
"""

from __future__ import annotations

import reflex as rx

#: Werte von ``device`` bei ``/transcribe`` und ihre Übersetzungsschlüssel
STT_DEVICE_LABEL_KEYS = {
    "auto": "stt_device_auto",
    "cpu": "stt_device_cpu",
    "cuda": "stt_device_cuda",
}


class STTSettingsMixin(rx.State, mixin=True):
    """State für den STT-Tab im Agent-Editor."""

    stt_available: bool = False          # Dienst erreichbar?
    stt_device: str = "auto"             # bevorzugtes Gerät dieses Clients (settings.json)

    @rx.var(auto_deps=False, deps=["stt_device", "ui_language"])
    def stt_device_label(self) -> str:
        from ..lib.i18n import t
        return t(STT_DEVICE_LABEL_KEYS[self.stt_device], lang=self.ui_language)  # type: ignore[attr-defined]

    @rx.var(auto_deps=False, deps=["ui_language"])
    def stt_device_labels(self) -> list[str]:
        from ..lib.i18n import t
        return [t(key, lang=self.ui_language) for key in STT_DEVICE_LABEL_KEYS.values()]  # type: ignore[attr-defined]

    def load_stt_settings(self) -> None:
        """Erreichbarkeit des Dienstes und das gespeicherte Gerät lesen. Normale Methode (kein
        Event-Decorator), damit ``set_agent_editor_tab`` sie direkt beim Tab-Wechsel aufrufen kann."""
        from ..lib.settings import persisted_settings
        from ..lib.stt_client import STTError, get_default_client

        self.stt_device = persisted_settings().get("stt_device", "auto")
        try:
            get_default_client().health()
        except STTError:
            self.stt_available = False
            return
        self.stt_available = True

    @rx.event
    def refresh_stt_settings(self) -> None:
        """Erneut prüfen (Refresh-Button)."""
        self.load_stt_settings()

    @rx.event
    def stt_set_device(self, label: str) -> None:
        from ..lib.i18n import t
        from ..lib.settings import load_settings

        device = next(
            key for key, label_key in STT_DEVICE_LABEL_KEYS.items()
            if t(label_key, lang=self.ui_language) == label  # type: ignore[attr-defined]
        )
        settings = load_settings() or {}
        settings["stt_device"] = device
        self._write_settings_file(settings)  # type: ignore[attr-defined]
        self.stt_device = device
