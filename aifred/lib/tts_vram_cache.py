"""TTS VRAM-Footprint Cache.

Persistent cache for peak VRAM measurements from the TTS stress burn-in.
Key: ``engine_key`` (e.g. ``qwen3local``, ``xtts``, ``fishspeech``,
``mossttsv2``). Value: peak MiB observed during stress synthesis plus
a ``headroom_mb`` field that the calibration adds on top.

Resolution order in :func:`resolve_tts_reserve`:

1. **JSON cache** at ``data/tts_vram_cache.json`` — populated by stress
   burn-in runs (manual via CLI or lazy on first calibration).
2. **Stress burn-in** (cold path) — performed by the caller when the
   cache misses. The measured peak is written back to the cache.

No TTL: once an engine is measured, the value sticks until the user
clicks the reset button in the UI or manually deletes the cache entry.
This mirrors the VLM cache pattern (:mod:`vlm_vram_cache`).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Optional

from .config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "tts_vram_cache.json"

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
            logger.warning("tts_vram_cache: load failed (%s) — using empty", e)
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


def get(engine_key: str) -> Optional[int]:
    """Return cached peak MiB for ``engine_key`` — or ``None`` on miss."""
    with _lock:
        data = _load()
    entry = data.get(engine_key)
    if not isinstance(entry, dict):
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def put(engine_key: str, peak_mb: int, source: str = "stress_burnin") -> None:
    """Persist a measurement. Overwrites any previous entry for
    ``engine_key`` — we only keep the latest measurement per engine."""
    with _lock:
        data = _load()
        data[engine_key] = {
            "peak_mb": int(peak_mb),
            "measured_at": datetime.now().isoformat(timespec="seconds"),
            "source": source,
        }
        _save(data)
    logger.info(
        "tts_vram_cache: stored engine=%s peak=%d MiB",
        engine_key, peak_mb,
    )


def clear() -> int:
    """Drop all cached measurements. Returns count of removed entries.
    Used by the UI reset button."""
    with _lock:
        data = _load()
        count = len(data)
        _save({})
    logger.info("tts_vram_cache: cleared %d entries", count)
    return count


def clear_one(engine_key: str) -> bool:
    """Drop a single engine's measurement. Returns True if there was
    something to remove."""
    with _lock:
        data = _load()
        if engine_key not in data:
            return False
        del data[engine_key]
        _save(data)
    logger.info("tts_vram_cache: cleared engine=%s", engine_key)
    return True
