"""Vision-Cleanup-Task: Face-Crops und alte Vision-Events aufräumen.

Läuft 1× täglich um ``GARBAGE_COLLECTION_HOUR`` (03:00 lokal),
genauso wie die anderen Cleanup-Tasks. TTL ist konfigurierbar im
Plugin-Setting ``face_recognition.retention_days`` (Default 14).

Was wird aufgeräumt:

* **Face-Crops** unter ``DATA_DIR/vision/faces/<source>/<datum>/`` —
  ganze Tagesordner älter als TTL werden gelöscht.
* **Motion-Frames** unter ``DATA_DIR/vigilantia/motion/<source>/<datum>/`` —
  selbes Schema, gleiches TTL.
* **Vision-DB-Events** in ``vision_store.events`` mit ``timestamp``
  vor dem TTL-Cutoff (motion + face_known/unsure/unknown).

Idempotent: löscht nur leere/abgelaufene Verzeichnisse, lässt
aktuelle in Ruhe. Pattern entspricht ``cleanup_audio_state_task``
und ``cleanup_expired_cache_task``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .logging_utils import log_message

logger = logging.getLogger(__name__)

# Default-TTL wenn das Plugin-Setting fehlt oder ungültig ist.
DEFAULT_RETENTION_DAYS = 14


def _load_retention_days() -> int:
    """TTL aus dem Plugin-Setting ``face_recognition.retention_days``.
    Fallback auf ``DEFAULT_RETENTION_DAYS`` bei Fehler / fehlender Konfig."""
    try:
        import json
        from .config import PROJECT_ROOT  # type: ignore[attr-defined]
        cfg_path = PROJECT_ROOT / "aifred" / "plugins" / "tools" / "vision" / "settings.json"
    except Exception:  # noqa: BLE001
        cfg_path = (
            Path(__file__).parent.parent
            / "plugins" / "tools" / "vision" / "settings.json"
        )
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f) or {}
        days = (cfg.get("face_recognition") or {}).get("retention_days")
        if isinstance(days, (int, float)) and 1 <= days <= 3650:
            return int(days)
    except Exception as e:  # noqa: BLE001
        logger.debug("retention_days load failed: %s", e)
    return DEFAULT_RETENTION_DAYS


def _cleanup_dated_subdirs(base: Path, cutoff_date: datetime) -> int:
    """Löscht in ``base/<source>/<yyyy-mm-dd>/`` alle Tagesordner,
    deren Datum vor ``cutoff_date`` liegt. Gibt Anzahl gelöschter
    Verzeichnisse zurück."""
    if not base.exists():
        return 0
    removed = 0
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    for source_dir in base.iterdir():
        if not source_dir.is_dir():
            continue
        for day_dir in source_dir.iterdir():
            if not day_dir.is_dir():
                continue
            # Strikter Vergleich der ISO-Tagesstrings — kein
            # mtime-fallback, weil die Folder-Namen die Wahrheit
            # sind (gegen Filesystem-Restore robust).
            if day_dir.name < cutoff_str:
                try:
                    shutil.rmtree(day_dir)
                    removed += 1
                except OSError as e:
                    logger.warning("cleanup: failed to remove %s: %s", day_dir, e)
    return removed


def _cleanup_vision_db_events(cutoff: datetime) -> int:
    """Löscht face-/motion-Events aus dem vision_store, die vor dem
    Cutoff-Zeitpunkt liegen. Gibt Anzahl gelöschter Rows zurück."""
    try:
        from .vision_store import VisionStore
    except ImportError:
        return 0
    try:
        store = VisionStore()
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_store init for cleanup failed: %s", e)
        return 0
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    try:
        with store._conn() as conn:  # type: ignore[attr-defined]
            cur = conn.execute(
                "DELETE FROM events WHERE timestamp < ? AND "
                "event_type IN ('motion', 'face_known', 'face_unsure', 'face_unknown')",
                (cutoff_iso,),
            )
            return int(cur.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_store event cleanup failed: %s", e)
        return 0


async def cleanup_vision_task() -> None:
    """Daily-Worker: löscht Face-Crops, Motion-Frames und DB-Events
    älter als die im Plugin-Setting konfigurierte Aufbewahrungsdauer.
    """
    from .cleanup_utils import seconds_until_next_run
    from .config import DATA_DIR, GARBAGE_COLLECTION_HOUR

    log_message(
        f"🗑️ Vision-Cleanup task started "
        f"(slot: {GARBAGE_COLLECTION_HOUR:02d}:00 lokal, "
        f"TTL: aus face_recognition.retention_days, Default "
        f"{DEFAULT_RETENTION_DAYS}d)"
    )

    while True:
        try:
            await asyncio.sleep(seconds_until_next_run(GARBAGE_COLLECTION_HOUR))
            # Describe before pruning: every undescribed motion/face event
            # gets a clustered VLM description while the GPU is idle, so no
            # frame is ever pruned before it has been described (the 14d TTL
            # gives ample margin, but the order makes it correct by design).
            try:
                from .vision_bulk import run_bulk_describe
                described = await run_bulk_describe()
                if described.aborted_vram:
                    log_message(
                        f"🌙 Nightly describe skipped: {described.vram_message}"
                    )
                elif described.total_events:
                    log_message(
                        f"🌙 Nightly describe: {described.total_clusters} clusters "
                        f"from {described.total_events} events, "
                        f"{described.failed} failed"
                    )
            except Exception as exc:  # noqa: BLE001
                log_message(f"⚠️ Nightly describe error: {exc}")

            ttl_days = _load_retention_days()
            cutoff = datetime.now() - timedelta(days=ttl_days)
            crops_removed = _cleanup_dated_subdirs(
                DATA_DIR / "vision" / "faces", cutoff
            )
            frames_removed = _cleanup_dated_subdirs(
                DATA_DIR / "vigilantia" / "motion", cutoff
            )
            db_removed = _cleanup_vision_db_events(cutoff)
            if crops_removed or frames_removed or db_removed:
                log_message(
                    f"🗑️ Vision cleanup: "
                    f"{crops_removed} crop-Tagesordner, "
                    f"{frames_removed} motion-Tagesordner, "
                    f"{db_removed} DB-Events entfernt "
                    f"(TTL {ttl_days}d)"
                )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            log_message(f"⚠️ Vision cleanup task error: {exc}")
