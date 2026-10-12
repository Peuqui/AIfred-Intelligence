"""STT-Settings-Mixin — der „STT"-Tab im Agent-Editor.

Bedienoberfläche für die Konfiguration des STT-Dienstes: Engine, Modell und Genauigkeit je Gerät
(CPU, GPU) leben als SSOT im Dienst selbst (über den STTClient: status(), set_config()); dieses
Mixin liest und schreibt sie nur. Persistiert wird in AIfred nur das bevorzugte Gerät
(``stt_device`` in settings.json: auto, cpu, cuda), weil es eine Regel des Clients ist.
"""

from __future__ import annotations

import reflex as rx


def _loaded_label(engine: str | None, model: str | None) -> str:
    """„large-v3“ / „parakeet-fp32“ für das, was geladen ist (der Dienst nennt bei Parakeet schon die
    Engine im Modellnamen); ``""`` wenn nichts."""
    if not engine:
        return ""
    return model if model and model.startswith(engine) else f"{engine} {model}".strip()


class STTSettingsMixin(rx.State, mixin=True):
    """State für den STT-Tab im Agent-Editor."""

    stt_available: bool = False          # Service erreichbar?
    stt_available_models: list[str] = []
    stt_engines: list[str] = []
    stt_cpu_engine: str = ""
    stt_gpu_engine: str = ""
    stt_devices: list[str] = []          # was der Dienst als Gerät anbietet: cpu, cuda, auto
    stt_device: str = "auto"             # bevorzugtes Gerät dieses Clients (settings.json)
    stt_qualities: list[str] = []
    stt_cpu_quality: str = ""
    stt_gpu_quality: str = ""
    stt_gpu_model: str = ""
    stt_cpu_model: str = ""
    stt_num_speakers: int = 0
    stt_initial_prompt: str = "auto"
    stt_beam_size: int = 5
    stt_vad_filter: bool = True
    stt_condition_on_previous: bool = True
    stt_gpu_ttl_minutes: int = 30
    # Status-Anzeige: was wirklich im Speicher liegt ("" = nichts), „Engine Modell“
    stt_gpu_loaded_label: str = ""
    stt_cpu_loaded_label: str = ""
    stt_save_message: str = ""

    def load_stt_settings(self) -> None:
        """Config vom Whisper-Service lesen. Normale Methode (kein
        Event-Decorator), damit ``set_agent_editor_tab`` sie direkt beim
        Tab-Wechsel aufrufen kann."""
        from ..lib.stt_client import STTError, get_default_client

        self.stt_save_message = ""
        try:
            data = get_default_client().status()
        except STTError:
            self.stt_available = False
            return

        self.stt_available = True
        self.stt_available_models = data.get("available_models", [])
        self.stt_engines = data.get("engines", [])
        self.stt_devices = data.get("devices", [])
        self.stt_cpu_engine = data.get("cpu_engine", "")
        self.stt_gpu_engine = data.get("gpu_engine", "")
        self.stt_qualities = data.get("parakeet_qualities", [])
        self.stt_cpu_quality = data.get("cpu_quality", "")
        self.stt_gpu_quality = data.get("gpu_quality", "")
        self.stt_gpu_model = data.get("gpu_model", "")
        self.stt_cpu_model = data.get("cpu_model", "")
        self.stt_num_speakers = int(data.get("num_speakers", 0))
        self.stt_initial_prompt = data.get("initial_prompt", "auto")
        self.stt_beam_size = int(data.get("beam_size", 5))
        self.stt_vad_filter = bool(data.get("vad_filter", True))
        self.stt_condition_on_previous = bool(data.get("condition_on_previous_text", True))
        self.stt_gpu_ttl_minutes = int(data.get("gpu_ttl_minutes", 30))
        self.stt_gpu_loaded_label = _loaded_label(data.get("gpu_loaded_engine"), data.get("gpu_loaded_model"))
        self.stt_cpu_loaded_label = _loaded_label(data.get("cpu_loaded_engine"), data.get("cpu_loaded_model"))
        from ..lib.settings import persisted_settings
        self.stt_device = persisted_settings().get("stt_device", "auto")

    @rx.event
    def refresh_stt_settings(self) -> None:
        """Explizites Neuladen (Refresh-Button)."""
        self.load_stt_settings()

    @rx.event
    def stt_set_cpu_engine(self, value: str) -> None:
        self.stt_cpu_engine = value

    @rx.event
    def stt_set_gpu_engine(self, value: str) -> None:
        self.stt_gpu_engine = value

    @rx.event
    def stt_set_device(self, value: str) -> None:
        self.stt_device = value

    @rx.event
    def stt_set_cpu_quality(self, value: str) -> None:
        self.stt_cpu_quality = value

    @rx.event
    def stt_set_gpu_quality(self, value: str) -> None:
        self.stt_gpu_quality = value

    @rx.event
    def stt_set_gpu_model(self, value: str) -> None:
        self.stt_gpu_model = value

    @rx.event
    def stt_set_cpu_model(self, value: str) -> None:
        self.stt_cpu_model = value

    @rx.event
    def stt_set_num_speakers(self, value: str) -> None:
        try:
            self.stt_num_speakers = max(0, min(10, int(value)))
        except ValueError:
            self.stt_num_speakers = 0

    @rx.event
    def stt_set_initial_prompt(self, value: str) -> None:
        self.stt_initial_prompt = value

    @rx.event
    def stt_set_beam_size(self, value: str) -> None:
        try:
            self.stt_beam_size = max(1, min(20, int(value)))
        except ValueError:
            self.stt_beam_size = 5

    @rx.event
    def stt_set_vad_filter(self, value: bool) -> None:
        self.stt_vad_filter = value

    @rx.event
    def stt_set_condition_on_previous(self, value: bool) -> None:
        self.stt_condition_on_previous = value

    @rx.event
    def stt_set_gpu_ttl(self, value: str) -> None:
        try:
            self.stt_gpu_ttl_minutes = max(0, int(value))
        except ValueError:
            self.stt_gpu_ttl_minutes = 30

    @rx.event
    def save_stt_settings(self) -> None:
        """Alle Felder an den Service POSTen und Status neu lesen."""
        from ..lib.i18n import t
        from ..lib.settings import load_settings
        from ..lib.stt_client import STTError, get_default_client

        payload = {
            "cpu_engine": self.stt_cpu_engine,
            "gpu_engine": self.stt_gpu_engine,
            "cpu_quality": self.stt_cpu_quality,
            "gpu_quality": self.stt_gpu_quality,
            "gpu_model": self.stt_gpu_model,
            "cpu_model": self.stt_cpu_model,
            "num_speakers": self.stt_num_speakers,
            "initial_prompt": self.stt_initial_prompt,
            "beam_size": self.stt_beam_size,
            "vad_filter": self.stt_vad_filter,
            "condition_on_previous_text": self.stt_condition_on_previous,
            "gpu_ttl_minutes": self.stt_gpu_ttl_minutes,
        }
        try:
            get_default_client().set_config(payload)
        except STTError as e:
            self.stt_save_message = f"❌ {e}"
            return
        settings = load_settings() or {}
        settings["stt_device"] = self.stt_device
        self._write_settings_file(settings)  # type: ignore[attr-defined]
        self.load_stt_settings()
        self.stt_save_message = t("stt_saved", lang=self.ui_language)  # type: ignore[attr-defined]
