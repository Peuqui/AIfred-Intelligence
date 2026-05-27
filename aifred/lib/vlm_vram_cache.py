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
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from .config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "vlm_vram_cache.json"

_cache: dict[str, Any] | None = None
_cache_mtime: float = 0.0
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    """Load cache from disk with mtime-based invalidation."""
    global _cache, _cache_mtime
    try:
        mtime = CACHE_FILE.stat().st_mtime
    except FileNotFoundError:
        _cache = {}
        _cache_mtime = 0.0
        return _cache
    if _cache is None or mtime != _cache_mtime:
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _cache = json.load(f) or {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("vlm_vram_cache: load failed (%s) — using empty", e)
            _cache = {}
        _cache_mtime = mtime
    return _cache


def _save(data: dict[str, Any]) -> None:
    """Write cache to disk and refresh in-memory copy."""
    global _cache, _cache_mtime
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(CACHE_FILE)
    _cache = data
    _cache_mtime = CACHE_FILE.stat().st_mtime


def get(model_id: str, num_ctx: int) -> Optional[int]:
    """Return cached peak MiB for ``(model_id, num_ctx)`` — or ``None``
    on miss/mismatch."""
    with _lock:
        data = _load()
    entry = data.get(model_id)
    if not isinstance(entry, dict):
        return None
    if int(entry.get("num_ctx", -1)) != int(num_ctx):
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def put(model_id: str, num_ctx: int, peak_mb: int, source: str = "stress_prewarm") -> None:
    """Persist a measurement. Overwrites any previous entry for ``model_id``
    (we only keep the latest measurement per model)."""
    with _lock:
        data = _load()
        data[model_id] = {
            "num_ctx": int(num_ctx),
            "peak_mb": int(peak_mb),
            "measured_at": datetime.now().isoformat(timespec="seconds"),
            "source": source,
        }
        _save(data)
    logger.info(
        "vlm_vram_cache: stored model=%s num_ctx=%d peak=%d MiB",
        model_id, num_ctx, peak_mb,
    )


def clear() -> int:
    """Drop all cached measurements. Returns count of removed entries.
    Used by the UI reset button — the next calibration re-measures via
    stress prewarm."""
    with _lock:
        data = _load()
        count = len(data)
        _save({})
    logger.info("vlm_vram_cache: cleared %d entries", count)
    return count


def clear_one(model_id: str) -> bool:
    """Drop a single model's measurement. Returns True if there was
    something to remove."""
    with _lock:
        data = _load()
        if model_id not in data:
            return False
        del data[model_id]
        _save(data)
    logger.info("vlm_vram_cache: cleared model=%s", model_id)
    return True
