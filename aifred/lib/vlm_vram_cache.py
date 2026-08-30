"""VLM VRAM-Footprint Cache.

Persistent cache for peak VRAM measurements from the VLM stress-prewarm.
Key: ``(model_id, num_ctx)``. Value: peak MiB observed during stress
inference + a ``headroom_mb`` field that the calibration adds on top.

Resolution order in :func:`resolve_vlm_reserve_mb`:

1. **Static table** in :data:`config.VLM_VRAM_BUDGET_MB` — hand-measured,
   highest priority. Bypasses the cache entirely.
2. **JSON cache** at ``data/vlm_vram_cache.json`` — populated by stress
   prewarm runs from previous calibrations.
3. **Stress prewarm** (cold path) — performed by the caller when neither
   table nor cache match. The result is written back to the cache.

Cache invalidation: a new entry is required whenever ``num_ctx`` changes
(KV-cache scales linearly with context window). Old entries with the
same ``model_id`` but different ``num_ctx`` are kept — useful when the
user switches context sizes back and forth.
Store mechanics live in :class:`vram_peak_cache.JsonPeakCache` (shared
with the TTS cache).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import DATA_DIR
from .vram_peak_cache import JsonPeakCache

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "vlm_vram_cache.json"

_store = JsonPeakCache(CACHE_FILE, "vlm_vram_cache")


def get(model_id: str, num_ctx: int) -> Optional[int]:
    """Return cached peak MiB for ``(model_id, num_ctx)`` — or ``None``
    on miss/mismatch."""
    entry = _store.get_entry(model_id)
    if entry is None:
        return None
    if int(entry.get("num_ctx", -1)) != int(num_ctx):
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def get_any(model_id: str) -> Optional[tuple[int, int]]:
    """Gemessene Spitze fuer ``model_id`` OHNE Kontext-Abgleich, als
    ``(peak_mb, num_ctx)``.

    :func:`get` verwirft absichtlich Treffer mit abweichendem ``num_ctx``
    — fuer eine Reservierungsentscheidung waere ein Wert aus anderem
    Kontext falsch. Fuer die ANZEIGE gilt das nicht: "8.988 MiB, gemessen
    bei 24.576 Kontext" ist eine ehrliche Auskunft, "noch nicht vermessen"
    dagegen schlicht unwahr, wenn eine Messung vorliegt.
    """
    entry = _store.get_entry(model_id)
    if entry is None:
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak), int(entry.get("num_ctx", 0))


def put(model_id: str, num_ctx: int, peak_mb: int, source: str = "stress_prewarm") -> None:
    """Persist a measurement. Overwrites any previous entry for ``model_id``
    (we only keep the latest measurement per model)."""
    _store.put_entry(model_id, {
        "num_ctx": int(num_ctx),
        "peak_mb": int(peak_mb),
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
    })
    logger.info(
        "vlm_vram_cache: stored model=%s num_ctx=%d peak=%d MiB",
        model_id, num_ctx, peak_mb,
    )


def clear() -> int:
    """Drop all cached measurements. Returns count of removed entries.
    Used by the UI reset button — the next calibration re-measures via
    stress prewarm."""
    return _store.clear()


def clear_one(model_id: str) -> bool:
    """Drop a single model's measurement. Returns True if there was
    something to remove."""
    return _store.clear_one(model_id)
