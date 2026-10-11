"""Zugriff auf die Vigilantia-Einstellungen (``plugins/tools/vision/settings.json``).

SSOT für Pfad und Lesen in den Watcher-Modulen. Eine fehlende Datei ist
eine frische Installation (leere Einstellungen); eine unlesbare oder
kaputte Datei ist ein Fehler und wird gemeldet, nicht still durch
Standardwerte ersetzt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VISION_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / "plugins" / "tools" / "vision" / "settings.json"
)


def load_vision_settings() -> dict[str, Any]:
    """Einstellungen laden. ``{}`` bei fehlender Datei, ``ValueError`` bei
    unlesbarer oder nicht-objekthafter Datei."""
    if not VISION_SETTINGS_PATH.exists():
        return {}
    try:
        parsed = json.loads(VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{VISION_SETTINGS_PATH} unlesbar: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{VISION_SETTINGS_PATH}: JSON-Objekt erwartet")
    return parsed


def save_vision_settings(data: dict[str, Any]) -> None:
    """Einstellungen vollständig schreiben (Aufrufer: lesen, ändern, speichern)."""
    VISION_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
