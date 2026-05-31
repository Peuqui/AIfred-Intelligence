"""Zonen-Masken für Vision-Quellen.

Markierte Bildbereiche (z.B. Bäume, Straße) werden bei der Bewegungs-
erkennung ignoriert und — je nach Modus — zusätzlich DSGVO-konform
geschwärzt. Die Maske liegt pro Quelle in
``plugins/tools/vision/settings.json`` unter ``zone_masks[<source_id>]``::

    "zone_masks": {
      "cam/v4l2_0": {
        "mode": "motion",          # Toggle, siehe unten
        "cols": 40, "rows": 24,    # Rasterauflösung
        "cells": "000111000..."    # cols*rows Zeichen, '1' = ignorieren
      }
    }

``mode`` ist der Toggle, den der User pro Maske setzt:

* ``"motion"``   → nur den Motion-Trigger in der Zone unterdrücken; das
                   Bild bleibt unverändert gespeichert.
* ``"blackout"`` → zusätzlich die Pixel der Zone schwärzen, bevor das Bild
                   gespeichert oder ans VLM gegeben wird (öffentlicher Raum
                   landet nie auf der Platte).

Das Raster ist auflösungsunabhängig: Es wird per Nearest-Neighbor auf die
jeweilige Frame-Größe skaliert. Der (spätere) JS-Canvas-Editor malt in
genau diese Rasterzellen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# aifred/lib/vision_filters/ -> parents[2] == aifred/ -> plugins/tools/vision/
_VISION_SETTINGS = (
    Path(__file__).resolve().parents[2] / "plugins/tools/vision/settings.json"
)

_VALID_MODES = ("motion", "blackout")


@dataclass
class ZoneMask:
    """Ignorier-Zonen einer Quelle als Raster (1 = ignorieren)."""

    source_id: str
    mode: str
    cols: int
    rows: int
    grid: np.ndarray  # (rows, cols) uint8, 1 = ignorieren
    # Keep-Masken-Cache je Frame-Größe (Quellen liefern konstante Auflösung).
    _keep_cache: dict[tuple[int, int], np.ndarray] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def suppresses_motion(self) -> bool:
        """In beiden Modi wird der Motion-Trigger in der Zone unterdrückt."""
        return self.mode in _VALID_MODES

    @property
    def blacks_out(self) -> bool:
        """Nur im ``blackout``-Modus werden die Pixel geschwärzt (DSGVO)."""
        return self.mode == "blackout"

    def _keep(self, h: int, w: int) -> np.ndarray:
        """Keep-Maske (1 = behalten, 0 = ignorieren), auf (h, w) skaliert."""
        cached = self._keep_cache.get((h, w))
        if cached is None:
            ignore = cv2.resize(self.grid, (w, h), interpolation=cv2.INTER_NEAREST)
            cached = (ignore == 0).astype(np.uint8)
            self._keep_cache[(h, w)] = cached
        return cached

    def apply_to_motion(self, foreground: np.ndarray) -> np.ndarray:
        """Foreground-Maske in den Ignorier-Zonen auf 0 setzen."""
        h, w = foreground.shape[:2]
        result: np.ndarray = cv2.bitwise_and(
            foreground, foreground, mask=self._keep(h, w)
        )
        return result

    def blackout(self, img: np.ndarray) -> np.ndarray:
        """Pixel der Ignorier-Zonen schwärzen. Gibt eine Kopie zurück."""
        h, w = img.shape[:2]
        out = img.copy()
        out[self._keep(h, w) == 0] = 0
        return out


def load_zone_mask(source_id: str) -> ZoneMask | None:
    """Zonen-Maske einer Quelle aus der vision-settings.json laden.

    ``None`` wenn keine (gültige, nicht-leere) Maske konfiguriert ist —
    dann läuft die Motion-Detection unverändert.
    """
    try:
        data = json.loads(_VISION_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("zone_mask: vision settings not readable (%s)", e)
        return None
    masks = data.get("zone_masks")
    if not isinstance(masks, dict):
        return None
    entry = masks.get(source_id)
    if not isinstance(entry, dict):
        return None
    # Schnell-Toggle aus dem Editor: deaktiviert → Maske bleibt gespeichert
    # (Zellen erhalten), wird aber nicht angewandt (Gegenprobe).
    if not entry.get("enabled", True):
        return None
    mode = str(entry.get("mode", "")).strip()
    if mode not in _VALID_MODES:
        return None
    try:
        cols = int(entry["cols"])
        rows = int(entry["rows"])
        cells = str(entry["cells"])
    except (KeyError, TypeError, ValueError):
        return None
    if cols <= 0 or rows <= 0 or len(cells) != cols * rows:
        logger.warning("zone_mask for %s: malformed grid, ignoring", source_id)
        return None
    grid = (np.frombuffer(cells.encode("ascii"), dtype=np.uint8) == ord("1"))
    grid = grid.astype(np.uint8).reshape(rows, cols)
    if not grid.any():
        return None  # nichts markiert → wie keine Maske
    return ZoneMask(source_id=source_id, mode=mode, cols=cols, rows=rows, grid=grid)
