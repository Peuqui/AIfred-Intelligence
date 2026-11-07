"""STT-Settings-Mixin — der „STT"-Tab im Agent-Editor: ob der Dienst erreichbar ist.

AIfred fragt den Dienst immer mit Gerät ``auto`` (``audio_processing.transcribe_audio_auto``).
Gerät, Engine, Modell, Genauigkeit je Gerät, die Reihenfolge der Automatik, Puffer und Burn-in
liegen als SSOT im STT-Dienst und werden auf dessen eigener Oberfläche (``/ui/``) eingestellt.
"""

from __future__ import annotations

import reflex as rx


class STTSettingsMixin(rx.State, mixin=True):
    """State für den STT-Tab im Agent-Editor."""

    stt_available: rx.Field[bool] = rx.field(False)          # Dienst erreichbar?

    @rx.event
    def load_stt_settings(self) -> None:
        """Erreichbarkeit des Dienstes lesen (auch beim Wechsel auf den Tab)."""
        from ..lib.stt_client import STTError, get_default_client

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
