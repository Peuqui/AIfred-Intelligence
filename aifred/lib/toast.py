"""Toast-Meldungen der Oberfläche — eine Stelle für Dauer, Position und Art.

Ein Toast blendet sich nach ``UI_TOAST_DURATION_MS`` von selbst aus und
verschiebt das Layout nicht (anders als ein eingebauter Hinweiskasten). Aus
einem Event-Handler zurückgeben oder per ``yield`` ausgeben.
"""

from __future__ import annotations

from typing import Callable

import reflex as rx
from reflex.event import EventSpec

from .config import UI_TOAST_DURATION_MS, UI_TOAST_POSITION

# ``warning`` = bernsteinfarben (Problem, das den Betrieb nicht stoppt), ``error`` =
# rot, ``info`` / ``success`` wie üblich.
TOAST_KINDS = ("info", "success", "warning", "error")


def toast(kind: str, message: str, *, duration_ms: int | None = None) -> EventSpec:
    """Event, der einen Toast der Art ``kind`` anzeigt."""
    if kind not in TOAST_KINDS:
        raise ValueError(f"unbekannte Toast-Art: {kind!r} (erlaubt: {', '.join(TOAST_KINDS)})")
    make: Callable[..., EventSpec] = getattr(rx.toast, kind)
    return make(
        message,
        duration=UI_TOAST_DURATION_MS if duration_ms is None else duration_ms,
        position=UI_TOAST_POSITION,
    )
