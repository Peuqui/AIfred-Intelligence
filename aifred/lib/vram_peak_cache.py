"""Shared core for the peak-VRAM JSON caches (VLM + TTS).

Thread-safe, mtime-invalidated JSON store. The concrete caches
(:mod:`vlm_vram_cache`, :mod:`tts_vram_cache`) define their own entry
schema and public API on top of this class — this module only owns the
load/save/locking mechanics so the pattern exists exactly once.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JsonPeakCache:
    """mtime-invalidated, thread-safe JSON cache keyed by string."""

    def __init__(self, cache_file: Path, label: str):
        self._file = cache_file
        self._label = label
        self._cache: dict[str, Any] | None = None
        self._mtime: float = 0.0
        self._lock = threading.Lock()

    def _load(self, strict: bool = False) -> dict[str, Any]:
        """Load cache from disk with mtime-based invalidation.

        ``strict=True`` raises on an unreadable/corrupt file instead of
        returning ``{}`` — writers must use it, otherwise their
        read-modify-write would persist only the new entry and wipe the
        rest of the cache.
        """
        try:
            mtime = self._file.stat().st_mtime
        except FileNotFoundError:
            self._cache = {}
            self._mtime = 0.0
            return self._cache
        if self._cache is None or mtime != self._mtime:
            try:
                with open(self._file, encoding="utf-8") as f:
                    self._cache = json.load(f) or {}
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("%s: load failed (%s)", self._label, e)
                if strict:
                    raise
                self._cache = {}
            self._mtime = mtime
        return self._cache

    def _save(self, data: dict[str, Any]) -> None:
        """Write cache to disk and refresh in-memory copy."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self._file)
        self._cache = data
        self._mtime = self._file.stat().st_mtime

    def get_entry(self, key: str) -> Optional[dict[str, Any]]:
        """Return the raw entry dict for ``key`` — or ``None`` on miss."""
        with self._lock:
            data = self._load()
        entry = data.get(key)
        return entry if isinstance(entry, dict) else None

    def put_entry(self, key: str, entry: dict[str, Any]) -> None:
        """Persist an entry, overwriting any previous one for ``key``."""
        with self._lock:
            data = self._load(strict=True)
            data[key] = entry
            self._save(data)

    def clear(self) -> int:
        """Drop all entries. Returns count of removed entries."""
        with self._lock:
            data = self._load()
            count = len(data)
            self._save({})
        logger.info("%s: cleared %d entries", self._label, count)
        return count

    def clear_one(self, key: str) -> bool:
        """Drop a single entry. Returns True if there was something to remove."""
        with self._lock:
            data = self._load(strict=True)
            if key not in data:
                return False
            del data[key]
            self._save(data)
        logger.info("%s: cleared %s", self._label, key)
        return True
