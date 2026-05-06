"""Audio-Output-Channel-Registry.

Public API:

    from aifred.lib.audio_channels import resolve, all_targets, register

Eingebaute Channels (LocalChannel, BrowserChannel, PuckChannel) werden
beim Import dieses Moduls automatisch registriert. Externe Channels
können via ``register(channel)`` hinzugefügt werden — nützlich für
Tests oder zukünftige Plugin-Erweiterungen.

``resolve(target_id)`` liefert den ersten Channel, der ``can_handle()``
True meldet — Reihenfolge ist Registrierungs-Reihenfolge. ``None`` wenn
kein Channel passt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import AudioFormat, AudioOutputChannel, TargetInfo
from .browser import BrowserChannel
from .local import LocalChannel
from .puck import PuckChannel

if TYPE_CHECKING:
    from ..plugin_base import PluginContext

__all__ = [
    "AudioFormat",
    "AudioOutputChannel",
    "TargetInfo",
    "register",
    "resolve",
    "all_targets",
    "all_channels",
]

_REGISTRY: list[AudioOutputChannel] = []


def register(channel: AudioOutputChannel) -> None:
    """Füge einen Channel zur Registry hinzu. Idempotent gegen Re-Imports."""
    for existing in _REGISTRY:
        if existing.name == channel.name:
            return
    _REGISTRY.append(channel)


def resolve(target_id: str) -> AudioOutputChannel | None:
    """Finde den Channel der ``target_id`` bedienen kann. None wenn keiner."""
    for ch in _REGISTRY:
        if ch.can_handle(target_id):
            return ch
    return None


def all_targets(ctx: "PluginContext") -> list[TargetInfo]:
    """Sammle Live-Targets aller registrierten Channels."""
    targets: list[TargetInfo] = []
    for ch in _REGISTRY:
        try:
            targets.extend(ch.list_targets(ctx))
        except Exception:  # noqa: BLE001
            # Discovery-Fehler eines Channels darf nie die Liste killen
            pass
    return targets


def all_channels() -> list[AudioOutputChannel]:
    """Liste aller registrierten Channels (read-only Snapshot)."""
    return list(_REGISTRY)


# ── Auto-Registrierung der eingebauten Channels ──────────────────────
register(LocalChannel())
register(BrowserChannel())
register(PuckChannel())
