"""Zonen-Masken für Vision-Quellen.

Pro Zelle ein Typ (4-wertig), gemalt im JS-Canvas-Editor:

* ``0`` normal      — Bewegung zählt hier (Default)
* ``1`` ignorieren  — Bewegung in der Zone wird unterdrückt (Pixel bleiben)
* ``2`` schwärzen   — Pixel werden geschwärzt (DSGVO) UND Bewegung unterdrückt
* ``3`` ROI         — sobald irgendeine ROI-Zelle existiert, zählt Bewegung
                      AUSSCHLIESSLICH in den ROI-Zellen (alles andere ignoriert)

Daraus ergeben sich drei abgeleitete Masken:

* **keep** (wo Bewegung zählt): mit ROI → nur ``==3``; ohne ROI → nur ``==0``.
* **blackout** (Pixel schwärzen): ``==2`` — unabhängig vom ROI, immer DSGVO.
* **observed_pixels**: Anzahl der keep-Pixel — Bezugsgröße für den
  Bewegungs-Schwellwert, damit Maskieren die Empfindlichkeit im beobachteten
  Bereich nicht senkt.

Die Maske liegt pro Quelle in ``plugins/tools/vision/settings.json`` unter
``zone_masks[<source_id>]``::

    "zone_masks": {
      "<source_id>": {
        "enabled": true,
        "cols": 48, "rows": 27,
        "cells": "0011223300..."   # cols*rows Ziffern aus {0,1,2,3}
      }
    }

``enabled=false`` lässt die Maske gespeichert, wendet sie aber nicht an
(Schnell-Gegenprobe). Das Raster wird per Nearest-Neighbor auf die Frame-
Auflösung skaliert (auflösungsunabhängig).
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

# Zell-Typen
_IGNORE = 1
_BLACKOUT = 2
_ROI = 3


@dataclass
class ZoneMask:
    """Zonen-Typen einer Quelle als Raster (0/1/2/3, siehe Modul-Docstring)."""

    source_id: str
    cols: int
    rows: int
    grid: np.ndarray  # (rows, cols) uint8 mit Werten 0..3
    # Cache je Frame-Größe: (keep-Maske, Anzahl beobachteter Pixel).
    _cache: dict[tuple[int, int], tuple[np.ndarray, int]] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def blacks_out(self) -> bool:
        """Gibt es Schwärzungs-Zellen (DSGVO)?"""
        return bool((self.grid == _BLACKOUT).any())

    def _keep(self, h: int, w: int) -> tuple[np.ndarray, int]:
        """Keep-Maske (1 = Bewegung zählt) + Anzahl beobachteter Pixel,
        auf (h, w) skaliert und gecacht."""
        cached = self._cache.get((h, w))
        if cached is None:
            if (self.grid == _ROI).any():
                keep_grid = self.grid == _ROI            # ROI: nur hier
            else:
                keep_grid = self.grid == 0               # sonst: alles außer ignore/schwärzen
            keep = cv2.resize(
                keep_grid.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST
            )
            cached = (keep, int(np.count_nonzero(keep)))
            self._cache[(h, w)] = cached
        return cached

    def apply_to_motion(self, foreground: np.ndarray) -> np.ndarray:
        """Foreground-Maske auf die keep-Zone beschränken (Rest auf 0)."""
        h, w = foreground.shape[:2]
        keep, _ = self._keep(h, w)
        result: np.ndarray = cv2.bitwise_and(foreground, foreground, mask=keep)
        return result

    def observed_pixels(self, h: int, w: int) -> int:
        """Anzahl der Pixel, in denen Bewegung gezählt wird (Schwellwert-Bezug)."""
        return self._keep(h, w)[1]

    def blackout(self, img: np.ndarray) -> np.ndarray:
        """Pixel der Schwärzungs-Zellen (``==2``) auf Schwarz setzen. Kopie."""
        h, w = img.shape[:2]
        bo = cv2.resize(
            (self.grid == _BLACKOUT).astype(np.uint8), (w, h),
            interpolation=cv2.INTER_NEAREST,
        )
        out = img.copy()
        out[bo == 1] = 0
        return out


def load_zone_mask(source_id: str) -> ZoneMask | None:
    """Zonen-Maske einer Quelle aus der vision-settings.json laden.

    ``None`` wenn keine (aktive, nicht-leere) Maske konfiguriert ist —
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
    try:
        cols = int(entry["cols"])
        rows = int(entry["rows"])
        cells = str(entry["cells"])
    except (KeyError, TypeError, ValueError):
        return None
    if cols <= 0 or rows <= 0 or len(cells) != cols * rows:
        logger.warning("zone_mask for %s: malformed grid, ignoring", source_id)
        return None
    raw = np.frombuffer(cells.encode("ascii"), dtype=np.uint8).astype(np.int16)
    grid = np.clip(raw - ord("0"), 0, 3).astype(np.uint8).reshape(rows, cols)
    if not grid.any():
        return None  # nur Nullen → wie keine Maske
    return ZoneMask(source_id=source_id, cols=cols, rows=rows, grid=grid)
