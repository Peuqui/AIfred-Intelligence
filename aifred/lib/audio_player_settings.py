"""Lese-SSOT für die audio_player-Plugin-Settings (settings.json).

Die Datei GEHÖRT dem audio_player-Plugin, aber vier Konsumenten brauchen
denselben Lesepfad: die Plugin-Tools selbst, das Browser-UI-Mixin, der
TTS-Listen-Filter (audio_processing) und der FreeEcho2-Voice-Resume.
Als lib-Helper, damit kein Plugin aus einem anderen importieren muss
(Plugin-Atomaritäts-Regel: Plugin→Plugin verboten, Plugin→lib erlaubt).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .logging_utils import log_message

AUDIO_PLAYER_SETTINGS_PATH = (
    Path(__file__).parent.parent / "plugins" / "tools" / "audio_player" / "settings.json"
)


def load_audio_player_settings() -> dict[str, Any]:
    """settings.json frisch lesen (kleine Datei, kein Hot-Path).

    Fehlende Datei = normaler First-Run; korrupte Datei wird geloggt
    statt still ``{}`` zu liefern (ohne sie fehlen Streams und
    Resume-Konfiguration).
    """
    if not AUDIO_PLAYER_SETTINGS_PATH.exists():
        return {}
    try:
        with open(AUDIO_PLAYER_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log_message("audio_player settings.json is not a JSON object — ignoring it", "warning")
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log_message(f"audio_player settings.json unreadable — {exc}", "warning")
        return {}


def build_configured_source_map() -> dict[str, Any]:
    """Source-Map-SSOT: auto-discovered lokale Ordner + settings-Streams.

    Genutzt vom audio_player-Plugin (Resolver, play_folder) und vom
    FreeEcho2-Voice-Resume (der vorher privat aus dem Plugin importierte).
    """
    from .audio_sources import build_source_map
    from .config import MEDIA_AUDIO_DIR
    streams = {
        label: src
        for label, src in load_audio_player_settings().get("sources", {}).items()
        if src.get("type") == "http_stream"
    }
    return build_source_map(MEDIA_AUDIO_DIR, streams)
