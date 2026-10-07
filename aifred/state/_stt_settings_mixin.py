"""STT-Settings-Mixin — der „STT"-Tab im Agent-Editor.

Reine Bedienoberfläche für die Konfiguration des Whisper-Services: Die
Config lebt als SSOT im Service selbst (GET /status, POST /config auf
WHISPER_SERVICE_URL); dieses Mixin liest und schreibt sie nur. Nichts
davon wird in AIfred dupliziert oder persistiert.
"""

from __future__ import annotations

import reflex as rx


class STTSettingsMixin(rx.State, mixin=True):
    """State für den STT-Tab im Agent-Editor."""

    stt_available: bool = False          # Service erreichbar?
    stt_available_models: list[str] = []
    stt_engines: list[str] = []
    stt_engine: str = ""
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
    # Status-Anzeige: tatsächlich geladenes GPU-Modell (Degradierungskette!)
    stt_gpu_model_loaded: str = ""
    # Welches CPU-Modell wirklich im Speicher liegt ("" = keins) — je nach Engine Whisper-Modell oder Parakeet-Qualität
    stt_cpu_loaded_label: str = ""
    stt_save_message: str = ""

    def load_stt_settings(self) -> None:
        """Config vom Whisper-Service lesen. Normale Methode (kein
        Event-Decorator), damit ``set_agent_editor_tab`` sie direkt beim
        Tab-Wechsel aufrufen kann."""
        import requests
        from ..lib.config import WHISPER_SERVICE_URL

        self.stt_save_message = ""
        try:
            resp = requests.get(f"{WHISPER_SERVICE_URL}/status", timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            self.stt_available = False
            return

        self.stt_available = True
        self.stt_available_models = data.get("available_models", [])
        self.stt_engines = data.get("engines", [])
        self.stt_engine = data.get("engine", "")
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
        self.stt_gpu_model_loaded = data.get("gpu_model_loaded") or ""
        if data.get("parakeet_cpu_loaded"):
            self.stt_cpu_loaded_label = f"parakeet-{data['parakeet_cpu_loaded']}"
        elif data.get("cpu_loaded"):
            self.stt_cpu_loaded_label = str(self.stt_cpu_model)
        else:
            self.stt_cpu_loaded_label = ""

    @rx.event
    def refresh_stt_settings(self) -> None:
        """Explizites Neuladen (Refresh-Button)."""
        self.load_stt_settings()

    @rx.event
    def stt_set_engine(self, value: str) -> None:
        self.stt_engine = value

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
        import requests
        from ..lib.config import WHISPER_SERVICE_URL
        from ..lib.i18n import t

        payload = {
            "engine": self.stt_engine,
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
            resp = requests.post(
                f"{WHISPER_SERVICE_URL}/config", json=payload, timeout=5,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            self.stt_save_message = f"❌ {e}"
            return
        self.load_stt_settings()
        self.stt_save_message = t("stt_saved", lang=self.ui_language)  # type: ignore[attr-defined]
