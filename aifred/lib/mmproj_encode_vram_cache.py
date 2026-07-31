"""mmproj Encode-Buffer VRAM Cache (Vision-Modelle mit nativem ``--mmproj``).

Persistenter Cache für den CLIP-/Encoding-Buffer-Peak, den ein
Vision-Hauptmodell bei der ERSTEN Bildanalyse alloziert. llama-fit-params
kann diesen Anteil nicht modellieren (das Tool kennt ``--mmproj`` nicht),
und statisch berechenbar ist er auch nicht — er hängt von der
mmproj-Architektur und der Probe-Auflösung ab. Gemessen wird er deshalb
einmalig als VRAM-Delta um den 4K-Vision-Probe der Kalibrierung
(Burn-In in :mod:`calibration.verifier`); ab dem zweiten Lauf geht der
Wert als statischer Zuschlag in die fit-params-Projektion
(:mod:`calibration.projection`), der adaptive Bias bleibt Sicherheitsnetz.

Key: mmproj-Dateiname. Invalidierung über mtime der mmproj-Datei und die
konfigurierte Probe-Auflösung (ändert sich eine von beiden, wird neu
gemessen). Store-Mechanik: :class:`vram_peak_cache.JsonPeakCache`
(geteilt mit VLM- und TTS-Cache).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, LLAMACPP_VISION_PROBE_RESOLUTION
from .vram_peak_cache import JsonPeakCache

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "mmproj_encode_vram_cache.json"

_store = JsonPeakCache(CACHE_FILE, "mmproj_encode_vram_cache")


def get(mmproj_path: Path) -> Optional[int]:
    """Gemessener Encode-Buffer-Peak in MiB für diese mmproj-Datei —
    ``None`` bei Miss, veralteter mtime oder geänderter Probe-Auflösung."""
    try:
        mtime = mmproj_path.stat().st_mtime
    except OSError:
        return None
    entry = _store.get_entry(mmproj_path.name)
    if entry is None:
        return None
    if entry.get("mmproj_mtime") != mtime:
        return None
    if tuple(entry.get("resolution", ())) != tuple(LLAMACPP_VISION_PROBE_RESOLUTION):
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def put(mmproj_path: Path, peak_mb: int) -> None:
    """Persistiert eine Burn-In-Messung für diese mmproj-Datei."""
    try:
        mtime = mmproj_path.stat().st_mtime
    except OSError:
        return
    _store.put_entry(mmproj_path.name, {
        "peak_mb": int(peak_mb),
        "resolution": list(LLAMACPP_VISION_PROBE_RESOLUTION),
        "mmproj_mtime": mtime,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    })
    logger.info(
        "mmproj_encode_vram_cache: stored %s peak=%d MiB (probe %sx%s)",
        mmproj_path.name, peak_mb, *LLAMACPP_VISION_PROBE_RESOLUTION,
    )


def clear() -> int:
    """Alle Messungen verwerfen — die nächste Kalibrierung misst neu."""
    return _store.clear()
