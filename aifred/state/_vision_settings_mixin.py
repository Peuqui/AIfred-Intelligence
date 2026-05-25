"""Vision-Plugin Settings-Mixin — UI state for the vision-settings modal.

Reactive state for the Vision-Plugin settings that the user can change
at runtime via the gear-icon modal in the Plugin tab:

* ``vision_mode_value``    — off / on-demand / live
* ``vision_model_value``   — which Ollama VLM tag to use for the
                              webcam pipeline (watch + side-channel)

The settings.json file under ``aifred/plugins/tools/vision/`` is the
single source of truth — this mixin reads on modal-open and writes on
each change. The plugin re-reads the file fresh per call (see
``_load_settings`` in ``aifred/plugins/tools/vision/__init__.py``), so
edits propagate without a restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


_VISION_SETTINGS_PATH = (
    Path(__file__).parent.parent / "plugins" / "tools" / "vision" / "settings.json"
)


def _load_settings() -> dict[str, Any]:
    if not _VISION_SETTINGS_PATH.exists():
        return {}
    try:
        parsed = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("vision settings.json unreadable: %s", e)
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    _VISION_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class VisionSettingsMixin(rx.State, mixin=True):
    """UI state for the Vision-Plugin settings modal."""

    vision_settings_open: bool = False
    vision_mode_value: str = "on-demand"
    vision_model_value: str = "qwen3-vl:4b-instruct-q8_0"
    vision_available_models: list[str] = []
    # Gesichts­erkennung an/aus — SSOT in
    # ``plugins/tools/vision/settings.json`` ``face_recognition.enabled``.
    # Wirkt auf den Watcher (run_face_detect_on_motion).
    face_recognition_enabled: bool = True
    # Aufbewahrungsdauer der Face-Crops + Motion-Frames + Vision-DB-
    # Events in Tagen. Cleanup-Task läuft täglich um 03:00 lokal.
    face_retention_days: int = 14

    def _refresh_vision_settings(self) -> None:
        """Lade Plugin-Settings + Ollama-Modellliste in den State.
        Wird sowohl vom Settings-Modal als auch vom Live-Preview-Popup
        gerufen, damit das Modell-Dropdown im Popup-Header auch ohne
        vorheriges Öffnen des Settings-Modals befüllt ist."""
        settings = _load_settings()
        self.vision_mode_value = str(settings.get("vision_mode", "on-demand"))
        vlm = settings.get("vlm", {})
        self.vision_model_value = str(vlm.get("model", "qwen3-vl:4b-instruct-q8_0"))
        fr = settings.get("face_recognition", {}) or {}
        self.face_recognition_enabled = bool(fr.get("enabled", True))
        rd = fr.get("retention_days")
        if isinstance(rd, (int, float)) and 1 <= rd <= 3650:
            self.face_retention_days = int(rd)
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            if self.vision_model_value and self.vision_model_value not in models:
                models = [self.vision_model_value] + models
            self.vision_available_models = models
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings: ollama discovery failed: %s", e)
            self.vision_available_models = (
                [self.vision_model_value] if self.vision_model_value else []
            )

    @rx.event
    def open_vision_settings(self) -> None:
        """Open the modal (called from the Plugin-Tab gear icon)."""
        self._refresh_vision_settings()
        self.vision_settings_open = True

    @rx.event
    def close_vision_settings(self) -> None:
        """Close the modal (backdrop click or close button)."""
        self.vision_settings_open = False

    @rx.event
    async def set_vision_mode_value(self, value: str) -> None:
        if value not in ("off", "on-demand", "live"):
            return
        self.vision_mode_value = value
        settings = _load_settings()
        settings["vision_mode"] = value
        _save_settings(settings)
        # When the user flips to "live", honour the contract immediately:
        # load the VLM into VRAM with keep_alive=-1 so it stays there.
        # Without this, the model wouldn't appear in nvidia-smi until the
        # first vision_analyze call (or the next calibration run).
        if value == "live":
            try:
                from ..lib.vision_prewarm import prewarm_vlm
                await prewarm_vlm()
            except Exception as e:  # noqa: BLE001
                logger.warning("prewarm on mode-switch failed: %s", e)

    @rx.event
    async def set_vision_model_value(self, value: str) -> None:
        """Modell wechseln + altes Modell aus dem VRAM entladen.
        Sonst hängen beim Hin-und-Her-Schalten zwei Modelle parallel
        im Speicher, bis Ollamas keep_alive abläuft."""
        if not value:
            return
        old_model = self.vision_model_value
        self.vision_model_value = value
        settings = _load_settings()
        settings.setdefault("vlm", {})["model"] = value
        _save_settings(settings)
        if old_model and old_model != value:
            try:
                from ..lib.vision_prewarm import unload_vlm_model
                await unload_vlm_model(old_model)
            except Exception as e:  # noqa: BLE001
                logger.warning("unload of old VLM model failed: %s", e)

    @rx.event
    def set_face_recognition_enabled(self, value: bool) -> None:
        """Toggle für Face-Recognition. Schreibt in
        ``plugins/tools/vision/settings.json`` unter
        ``face_recognition.enabled``. Wirkt beim nächsten
        Watcher-Start (config.run_face_detect_on_motion)."""
        self.face_recognition_enabled = bool(value)
        settings = _load_settings()
        settings.setdefault("face_recognition", {})["enabled"] = bool(value)
        _save_settings(settings)

    @rx.event
    def set_face_retention_days(self, value: str) -> None:
        """Tage, die Face-Crops + Motion-Frames + Vision-DB-Events
        aufbewahrt werden. Wirkt beim nächsten Cleanup-Lauf
        (täglich 03:00 lokal)."""
        try:
            days = int(value)
        except (TypeError, ValueError):
            return
        if days < 1 or days > 3650:
            return
        self.face_retention_days = days
        settings = _load_settings()
        settings.setdefault("face_recognition", {})["retention_days"] = days
        _save_settings(settings)

    @rx.event
    def rescan_vision_models(self) -> None:
        """Re-run Ollama discovery (after the user did `ollama pull` externally)."""
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            if self.vision_model_value and self.vision_model_value not in models:
                models = [self.vision_model_value] + models
            self.vision_available_models = models
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings rescan failed: %s", e)
