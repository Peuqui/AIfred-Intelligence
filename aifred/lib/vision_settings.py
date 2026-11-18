"""Zugriff auf die Vigilantia-Einstellungen (``plugins/tools/vision/settings.json``).

SSOT für Pfad und Lesen in den Watcher-Modulen. Eine fehlende Datei ist
eine frische Installation (leere Einstellungen); eine unlesbare oder
kaputte Datei ist ein Fehler und wird gemeldet, nicht still durch
Standardwerte ersetzt.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .atomic_write import write_text_atomic

VISION_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / "plugins" / "tools" / "vision" / "settings.json"
)

_write_lock = threading.Lock()


def load_vision_settings() -> dict[str, Any]:
    """Einstellungen laden. ``{}`` bei fehlender Datei, ``ValueError`` bei
    unlesbarer oder nicht-objekthafter Datei."""
    if not VISION_SETTINGS_PATH.exists():
        return {}
    try:
        parsed = json.loads(VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{VISION_SETTINGS_PATH} unreadable: {e}") from e
    if not isinstance(parsed, dict):
        # Eine kaputte Datei ist für den Aufrufer eine Fehlerart, wie die unlesbare oben.
        raise ValueError(f"{VISION_SETTINGS_PATH}: JSON object expected")  # noqa: TRY004
    return parsed


def update_vision_settings(apply: Callable[[dict[str, Any]], None]) -> None:
    """Change the stored settings: ``apply`` edits the CURRENT file content in place, which is then written.

    The one writer of the file. Reading, changing and writing happen here under one lock, so a change never
    starts from a stale read (the zone mask and another setting saved at the same time both stay). The lock is
    needed because writers really run on several threads: the event loop (zone masks, arming) and worker
    threads (``asyncio.to_thread`` for the camera connections, the plugin form). Nothing is written when
    ``apply`` or the read raises. ``apply`` must stay quick and must not call back into this module."""
    with _write_lock:
        settings = load_vision_settings()
        apply(settings)
        write_text_atomic(VISION_SETTINGS_PATH, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
