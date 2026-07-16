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
Store mechanics live in :class:`vram_peak_cache.JsonPeakCache` (shared
with the VLM cache).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import DATA_DIR
from .vram_peak_cache import JsonPeakCache

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "tts_vram_cache.json"

_store = JsonPeakCache(CACHE_FILE, "tts_vram_cache")


def get(engine_key: str) -> Optional[int]:
    """Return cached peak MiB for ``engine_key`` — or ``None`` on miss."""
    entry = _store.get_entry(engine_key)
    if entry is None:
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def put(engine_key: str, peak_mb: int, source: str = "stress_burnin") -> None:
    """Persist a measurement. Overwrites any previous entry for
    ``engine_key`` — we only keep the latest measurement per engine."""
    _store.put_entry(engine_key, {
        "peak_mb": int(peak_mb),
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
    })
    logger.info(
        "tts_vram_cache: stored engine=%s peak=%d MiB",
        engine_key, peak_mb,
    )


def clear() -> int:
    """Drop all cached measurements. Returns count of removed entries.
    Used by the UI reset button."""
    return _store.clear()


def clear_one(engine_key: str) -> bool:
    """Drop a single engine's measurement. Returns True if there was
    something to remove."""
    return _store.clear_one(engine_key)
