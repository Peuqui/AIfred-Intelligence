"""Audio Position State — SSOT for resume across audio source plugins.

JSON file persistence with thread-safe access. Used by audio_manager to
save playback positions and by tools (audio_resume,
audio_list_unfinished) to query them.

Schema (data/audio_state.json):
    {
      "<state_key>": {
        "uri": "/mnt/nas/foo.mp3",
        "pos_sec": 15825.3,
        "duration_sec": 39600.0,
        "last_played": "2026-05-04T22:13:00",
        "completed": false
      }
    }

state_key conventions chosen by the calling source plugin:
    audio_player local file:  "hoerbuecher/Tolkien_HdR_Buch1.mp3"
    audio_player http stream: "swr3"  (label only, resume not meaningful)
    youtube plugin:           "youtube:dQw4w9WgXcQ"
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import AUDIO_STATE_CLEANUP_AGE_DAYS, DATA_DIR
from .logging_utils import log_message

STATE_FILE = DATA_DIR / "audio_state.json"


class AudioState:
    """File-backed JSON store for audio resume positions."""

    def __init__(self, path: Path = STATE_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()
        self.cleanup_completed_old()

    # ── Internal ─────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log_message(f"AudioState: failed to load {self._path}: {exc}", "error")
            self._data = {}

    def _save_unlocked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            log_message(f"AudioState: failed to save: {exc}", "error")

    def cleanup_completed_old(self) -> int:
        """Remove completed entries older than AUDIO_STATE_CLEANUP_AGE_DAYS.

        Called once at construction (service startup) and periodically by
        the background cleanup task. Returns number of entries removed.
        """
        cutoff = datetime.now() - timedelta(days=AUDIO_STATE_CLEANUP_AGE_DAYS)
        removed = 0
        with self._lock:
            for key in list(self._data.keys()):
                entry = self._data[key]
                if not entry.get("completed"):
                    continue
                ts_str = entry.get("last_played", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    del self._data[key]
                    removed += 1
            if removed > 0:
                self._save_unlocked()
        if removed > 0:
            log_message(f"AudioState: cleaned {removed} old completed entries")
        return removed

    # ── Public API ───────────────────────────────────────────

    def update(
        self,
        *,
        key: str,
        uri: str,
        pos_sec: float,
        duration_sec: Optional[float] = None,
    ) -> None:
        """Update or insert position for a state_key."""
        with self._lock:
            existing = self._data.get(key, {})
            self._data[key] = {
                "uri": uri,
                "pos_sec": float(pos_sec),
                "duration_sec": (
                    float(duration_sec) if duration_sec is not None
                    else existing.get("duration_sec")
                ),
                "last_played": datetime.now().isoformat(timespec="seconds"),
                "completed": False,
            }
            self._save_unlocked()

    def mark_completed(self, key: str) -> None:
        """Mark item as fully played. Will be cleaned up after 7 days."""
        with self._lock:
            if key in self._data:
                self._data[key]["completed"] = True
                self._data[key]["last_played"] = datetime.now().isoformat(timespec="seconds")
                self._save_unlocked()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._data.get(key)
            return dict(entry) if entry else None

    def remove(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._save_unlocked()
                return True
            return False

    def list_unfinished(self) -> list[dict[str, Any]]:
        """All entries with completed=False, sorted by last_played desc."""
        with self._lock:
            entries = [
                {"key": k, **v}
                for k, v in self._data.items()
                if not v.get("completed")
            ]
        entries.sort(key=lambda e: e.get("last_played", ""), reverse=True)
        return entries

    def last_played_key(self) -> Optional[str]:
        """state_key of the most recently played unfinished item, or None."""
        unfinished = self.list_unfinished()
        return unfinished[0]["key"] if unfinished else None


audio_state = AudioState()


# ── Background cleanup task ──────────────────────────────

async def cleanup_audio_state_task() -> None:
    """Background task: prune old completed entries every N hours.

    Runs alongside the AIfred service. Mirrors the pattern used by
    cleanup_expired_cache_task() for the vector cache.
    """
    from .config import AUDIO_STATE_CLEANUP_INTERVAL_HOURS

    log_message(
        f"🗑️ AudioState cleanup task started "
        f"(interval: {AUDIO_STATE_CLEANUP_INTERVAL_HOURS}h, "
        f"age: {AUDIO_STATE_CLEANUP_AGE_DAYS}d)"
    )

    while True:
        try:
            await asyncio.sleep(AUDIO_STATE_CLEANUP_INTERVAL_HOURS * 3600)
            removed = audio_state.cleanup_completed_old()
            if removed > 0:
                log_message(
                    f"🗑️ AudioState cleanup: {removed} expired entries removed"
                )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            log_message(f"⚠️ AudioState cleanup task error: {exc}")
            # Continue running despite errors

