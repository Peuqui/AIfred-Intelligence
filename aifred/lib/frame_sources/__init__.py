"""Frame-Source-Registry.

Public API::

    from aifred.lib.frame_sources import (
        Frame, FrameSource, SourceInfo,
        register, unregister, get,
        list_all, list_available, rescan,
    )

Eingebaute Source-Typen (V4L2-Webcam, …) werden beim Import dieses Moduls
automatisch entdeckt. Jeder Source-Typ-Modul (``v4l2_source.py``,
``rtsp_source.py``, …) exportiert eine ``discover()``-Funktion, die das
System scannt und konkrete ``FrameSource``-Instanzen registriert.

``rescan()`` ruft alle ``discover()``-Funktionen erneut auf — nützlich für
einen UI-Refresh-Button, wenn Geräte zur Laufzeit hinzukommen/abgezogen
werden.
"""

from __future__ import annotations

from .base import Frame, FrameSource, SourceInfo

__all__ = [
    "Frame",
    "FrameSource",
    "SourceInfo",
    "register",
    "unregister",
    "unregister_kind",
    "get",
    "list_all",
    "list_available",
    "rescan",
]

_REGISTRY: dict[str, FrameSource] = {}


def register(source: FrameSource) -> None:
    """Registriere oder ersetze eine Source. ``source.source_id`` ist der Key.

    Idempotent: ein zweiter Aufruf mit gleicher ``source_id`` ersetzt den
    Eintrag — wichtig für ``rescan()``, das neue Instanzen mit gleicher
    ID erzeugt.
    """
    _REGISTRY[source.source_id] = source


def unregister(source_id: str) -> None:
    """Entferne eine Source aus der Registry. No-Op wenn nicht vorhanden."""
    _REGISTRY.pop(source_id, None)


def unregister_kind(kind: str) -> None:
    """Entferne alle Sources eines ``kind`` (z.B. ``"webcam"``).

    Wird von ``discover()``-Implementierungen genutzt, um vor einem Re-Scan
    veraltete Einträge zu löschen.
    """
    to_remove = [sid for sid, src in _REGISTRY.items() if src.kind == kind]
    for sid in to_remove:
        _REGISTRY.pop(sid, None)


def get(source_id: str) -> FrameSource | None:
    """Hole eine spezifische Source. ``None`` wenn nicht registriert."""
    return _REGISTRY.get(source_id)


def list_all() -> list[FrameSource]:
    """Alle registrierten Sources (auch nicht-verfügbare). Read-only Snapshot."""
    return list(_REGISTRY.values())


def list_available() -> list[FrameSource]:
    """Nur die Sources, die gerade Frames liefern können."""
    return [s for s in _REGISTRY.values() if s.is_available()]


# Eingebaute Source-Module importieren — der Modul-Import triggert deren
# eigene ``discover()``-Funktion am Ende des Moduls. Damit ist beim
# Erstzugriff auf das Registry alles eingerichtet.
from . import v4l2_source as _v4l2_source  # noqa: E402
from . import rtsp_source as _rtsp_source  # noqa: E402


def rescan() -> None:
    """Re-scan aller Source-Typen für neu angeschlossene/entfernte Geräte.

    Ruft die ``discover()``-Funktion jedes Source-Typ-Moduls erneut auf.
    Jedes Modul kümmert sich selbst um Cleanup veralteter Einträge
    (typisch via ``unregister_kind()``).
    """
    _v4l2_source.discover()
    _rtsp_source.discover()
    # Künftige Source-Typen hier ergänzen:
    # _file_source.discover()
    # _screen_source.discover()
