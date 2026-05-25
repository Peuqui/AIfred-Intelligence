"""Vision-Plugin Settings-Mixin — UI-State for the /vision-settings page.

Reactive state for the three Vision-Plugin settings that the user can
actually change at runtime:

* ``vision_mode_value``    — off / on-demand / live
* ``vision_model_value``   — which Ollama VLM tag to use for the
                              webcam pipeline (watch + analyze)
* ``vision_sync_value``    — if True, the vision pipeline uses the
                              same model that's chosen in the main
                              Vision-LLM dropdown (overrides
                              ``vision_model_value``)

The settings.json file under ``aifred/plugins/tools/vision/`` is the
single source of truth — this mixin reads on page-load and writes on
each change. The plugin loads the same file fresh per call (see
``_load_settings`` in ``aifred/plugins/tools/vision/__init__.py``), so
edits propagate without restart.
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
    """UI-State für /vision-settings."""

    vision_mode_value: str = "on-demand"
    vision_model_value: str = "qwen3-vl:4b-instruct-q8_0"
    vision_sync_value: bool = False
    vision_available_models: list[str] = []
    vision_settings_status: str = ""

    @rx.event
    def open_vision_settings(self):
        """Navigate to the vision-settings page (called from Plugin-Tab gear)."""
        return rx.redirect("/vision-settings")

    @rx.event
    def on_load_vision_settings(self) -> None:
        """Page-load: populate state from settings.json + Ollama-discovery."""
        settings = _load_settings()
        self.vision_mode_value = str(settings.get("vision_mode", "on-demand"))
        vlm = settings.get("vlm", {})
        self.vision_model_value = str(vlm.get("model", "qwen3-vl:4b-instruct-q8_0"))
        self.vision_sync_value = bool(vlm.get("sync_with_main_vision", False))
        # Live-discover all Ollama VL models so the dropdown reflects what's
        # actually pullable (incl. user-added 30B etc.)
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            # Make sure the currently-configured model is in the list even if
            # Ollama is momentarily unreachable — UI consistency.
            if self.vision_model_value and self.vision_model_value not in models:
                models = [self.vision_model_value] + models
            self.vision_available_models = models
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings: ollama discovery failed: %s", e)
            self.vision_available_models = [self.vision_model_value] if self.vision_model_value else []
        self.vision_settings_status = ""

    @rx.event
    def set_vision_mode_value(self, value: str) -> None:
        if value not in ("off", "on-demand", "live"):
            return
        self.vision_mode_value = value
        settings = _load_settings()
        settings["vision_mode"] = value
        _save_settings(settings)
        self.vision_settings_status = f"✓ Modus gespeichert: {value}"

    @rx.event
    def set_vision_model_value(self, value: str) -> None:
        if not value:
            return
        self.vision_model_value = value
        settings = _load_settings()
        settings.setdefault("vlm", {})["model"] = value
        _save_settings(settings)
        self.vision_settings_status = f"✓ Modell gespeichert: {value}"

    @rx.event
    def set_vision_sync_value(self, value: bool) -> None:
        self.vision_sync_value = bool(value)
        settings = _load_settings()
        settings.setdefault("vlm", {})["sync_with_main_vision"] = bool(value)
        _save_settings(settings)
        self.vision_settings_status = (
            f"✓ Sync mit Hauptmodell {'aktiv' if value else 'deaktiviert'}"
        )

    @rx.event
    def rescan_vision_models(self) -> None:
        """Re-run Ollama discovery (after the user did `ollama pull` externally)."""
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            if self.vision_model_value and self.vision_model_value not in models:
                models = [self.vision_model_value] + models
            self.vision_available_models = models
            self.vision_settings_status = f"✓ {len(models)} VLM gefunden"
        except Exception as e:  # noqa: BLE001
            self.vision_settings_status = f"⚠️ Discovery fehlgeschlagen: {e}"
