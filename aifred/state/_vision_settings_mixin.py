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

    @rx.event
    def open_vision_settings(self) -> None:
        """Open the modal (called from the Plugin-Tab gear icon)."""
        # Populate from disk + run Ollama discovery before showing
        settings = _load_settings()
        self.vision_mode_value = str(settings.get("vision_mode", "on-demand"))
        vlm = settings.get("vlm", {})
        self.vision_model_value = str(vlm.get("model", "qwen3-vl:4b-instruct-q8_0"))
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
        self.vision_settings_open = True

    @rx.event
    def close_vision_settings(self) -> None:
        """Close the modal (backdrop click or close button)."""
        self.vision_settings_open = False

    @rx.event
    def set_vision_mode_value(self, value: str) -> None:
        if value not in ("off", "on-demand", "live"):
            return
        self.vision_mode_value = value
        settings = _load_settings()
        settings["vision_mode"] = value
        _save_settings(settings)

    @rx.event
    def set_vision_model_value(self, value: str) -> None:
        if not value:
            return
        self.vision_model_value = value
        settings = _load_settings()
        settings.setdefault("vlm", {})["model"] = value
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
