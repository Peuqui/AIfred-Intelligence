"""Vision-Cleanup-Task: Face-Crops und alte Vision-Events aufräumen.

Aufräumlauf (garbage collection), 1× täglich um ``GARBAGE_COLLECTION_HOUR``
(03:00 lokal), genauso wie die anderen Cleanup-Tasks. TTL ist konfigurierbar im
Plugin-Setting ``face_recognition.retention_days`` (Default 14). Zwei Teile mit
je eigenem Schalter (Einstellungen des Vision-Plugins, beide Standard an, auch
bei abgeschaltetem Plugin gelesen):

* Nachbeschreiben (``gc_describe``) gehört dem Plugin: nur bei Vision an.
* Löschen nach Frist (``gc_prune``) ist Kern: läuft auch bei Vision aus.

Was wird aufgeräumt:

* **Face-Crops** unter ``DATA_DIR/vision/faces/<source>/<datum>/`` —
  ganze Tagesordner älter als TTL werden gelöscht. Nur Ordner mit
  ISO-Datumsnamen zählen; ``faces/enroll/face_N/`` (dauerhafte
  Identitäts-Crops, gelöscht nur mit ihrem Embedding) bleibt unberührt.
* **Motion-Frames** unter ``DATA_DIR/vigilantia/motion/<source>/<datum>/`` —
  selbes Schema, gleiches TTL.
* **Burst-Filme** unter ``DATA_DIR/vigilantia/films/<source>/<datum>/`` und
  **aufgenommene Filme** (``vision_capture`` mit Dauer) unter ``…/recordings/<source>/<datum>/`` —
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
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, GARBAGE_COLLECTION_HOUR
from .logging_utils import log_exception, log_message
from .user_error import Invalid
from .vision_settings import load_vision_settings, save_vision_settings
from .vision_utils import FILMS_DIR, RECORDINGS_DIR

logger = logging.getLogger(__name__)

# Default-TTL wenn das Plugin-Setting nicht gesetzt ist, und was es sein darf.
DEFAULT_RETENTION_DAYS = 14
RETENTION_DAYS_RANGE = (1, 3650)

# Nur echte Tagesordner (ISO-Datum) unterliegen dem TTL. Alles andere —
# insbesondere ``faces/enroll/face_N/`` mit den dauerhaften
# Identitäts-Crops — wird nie angefasst. Vorher hing deren Überleben
# am lexikografischen Zufall ``"face_3" > "2026-…"``.
_DAY_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def retention_days() -> int:
    """TTL aus dem Plugin-Setting ``face_recognition.retention_days``.
    Fehlt der Wert, gilt ``DEFAULT_RETENTION_DAYS``; ein Lesefehler oder ein
    Wert außerhalb 1..3650 ist ein ``ValueError``."""
    days = (load_vision_settings().get("face_recognition") or {}).get("retention_days")
    if days is None:
        return DEFAULT_RETENTION_DAYS
    low, high = RETENTION_DAYS_RANGE
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not low <= days <= high:
        raise ValueError(
            f"face_recognition.retention_days must be a number {low}..{high}, is {days!r}"
        )
    return int(days)


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
            if not _DAY_DIR_RE.fullmatch(day_dir.name):
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
    except (sqlite3.Error, OSError) as e:  # the event database could not be opened
        logger.warning("vision_store init for cleanup failed: %s", e)
        return 0
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    try:
        with store._conn() as conn:
            cur = conn.execute(
                "DELETE FROM events WHERE timestamp < ? AND "
                "event_type IN ('motion', 'face_known', 'face_unsure', 'face_unknown')",
                (cutoff_iso,),
            )
            return int(cur.rowcount or 0)
    except sqlite3.Error as e:  # busy database: the next night retries
        logger.warning("vision_store event cleanup failed: %s", e)
        return 0


def gc_switch(name: str) -> bool:
    """One of the two switches of the daily cleanup (``face_recognition.gc_describe``, ``face_recognition.gc_prune``
    in the plugin's settings); on when never set. Read whether the plugin is on or off."""
    return bool((load_vision_settings().get("face_recognition") or {}).get(name, True))


# The settings form of the vision plugin (Settings, Plugins, gear) is the one way to change these three values: its
# fields are named like this and carry the values as text ("on"/"off" for the switches).
RETENTION_FORM_KEY = "VISION_RETENTION_DAYS"
GC_FORM_KEYS = {"gc_prune": "VISION_GC_PRUNE", "gc_describe": "VISION_GC_DESCRIBE"}
SWITCH_VALUES = ("on", "off")


def cleanup_form_values() -> dict[str, str]:
    """The three values as the plugin's form shows them (read whether the plugin is on or off)."""
    return {
        RETENTION_FORM_KEY: str(retention_days()),
        **{form_key: SWITCH_VALUES[0] if gc_switch(name) else SWITCH_VALUES[1] for name, form_key in GC_FORM_KEYS.items()},
    }


def save_cleanup_form_values(values: dict[str, str]) -> None:
    """Store what the form sends into ``face_recognition`` of the plugin's settings. ``Invalid`` (nothing written)
    for retention days that are no whole number in ``RETENTION_DAYS_RANGE`` or a switch that is neither on nor off."""
    low, high = RETENTION_DAYS_RANGE
    raw_days = values[RETENTION_FORM_KEY].strip()
    if not raw_days.isdigit() or not low <= int(raw_days) <= high:
        raise Invalid("number_range", value=raw_days, low=low, high=high)
    switches = {}
    for name, form_key in GC_FORM_KEYS.items():
        if values[form_key] not in SWITCH_VALUES:
            raise Invalid("unknown_option", field=form_key, value=values[form_key])
        switches[name] = values[form_key] == SWITCH_VALUES[0]
    settings = load_vision_settings()
    settings["face_recognition"] = {**(settings.get("face_recognition") or {}), "retention_days": int(raw_days), **switches}
    save_vision_settings(settings)


async def _describe_before_pruning() -> None:
    """Every undescribed motion/face event gets a clustered VLM description while the GPU is idle, so no frame is
    ever pruned before it has been described. Belongs to the vision plugin: only with it on, and with the switch."""
    from .plugin_registry import is_plugin_enabled

    if not (is_plugin_enabled("vision") and gc_switch("gc_describe")):
        return
    from .vision_bulk import run_bulk_describe
    # No VRAM pre-check — Ollama manages the side-channel VLM's VRAM
    # itself; a pre-check could falsely abort and silently skip the
    # whole backfill. A partial run is idempotent anyway.
    described = await run_bulk_describe(check_vram=False)
    if described.aborted_vram:
        log_message(f"🌙 Vision GC describe skipped: {described.vram_reason}")
    elif described.total_events:
        log_message(
            f"🌙 Vision GC describe: {described.total_clusters} clusters "
            f"from {described.total_events} events, {described.failed} failed"
        )


def _prune_expired() -> None:
    """Delete what is older than the retention. Core, not the plugin's: camera pictures must not lie around past
    their time because the plugin is off. Only its own switch stops it."""
    if not gc_switch("gc_prune"):
        return
    ttl_days = retention_days()
    cutoff = datetime.now() - timedelta(days=ttl_days)
    crops_removed = _cleanup_dated_subdirs(DATA_DIR / "vision" / "faces", cutoff)
    frames_removed = _cleanup_dated_subdirs(DATA_DIR / "vigilantia" / "motion", cutoff)
    films_removed = _cleanup_dated_subdirs(FILMS_DIR, cutoff) + _cleanup_dated_subdirs(RECORDINGS_DIR, cutoff)
    db_removed = _cleanup_vision_db_events(cutoff)
    if crops_removed or frames_removed or films_removed or db_removed:
        log_message(
            f"🗑️ Vision cleanup: "
            f"{crops_removed} crop day folders, "
            f"{frames_removed} motion day folders, "
            f"{films_removed} film day folders, "
            f"{db_removed} DB events removed "
            f"(TTL {ttl_days}d)"
        )


async def run_vision_gc() -> None:
    """One pass of the daily garbage collection: describe what is undescribed (plugin), then prune (core)."""
    try:
        await _describe_before_pruning()
    except Exception as exc:  # noqa: BLE001 — a failed describe run must not stop the pruning; logged
        log_exception("⚠️ Vision GC describe error", exc)
    _prune_expired()


async def vision_gc_task() -> None:
    """Daily worker of the garbage collection at ``GARBAGE_COLLECTION_HOUR``; runs whether the vision plugin is on
    or off (see ``run_vision_gc`` for what each part needs)."""
    from .cleanup_utils import seconds_until_next_run

    log_message(
        f"🗑️ Vision garbage collection started "
        f"(slot: {GARBAGE_COLLECTION_HOUR:02d}:00 local, "
        f"TTL: from face_recognition.retention_days, default "
        f"{DEFAULT_RETENTION_DAYS}d)"
    )

    while True:
        try:
            await asyncio.sleep(seconds_until_next_run(GARBAGE_COLLECTION_HOUR))
            await run_vision_gc()
        except Exception as exc:  # pragma: no cover  # noqa: BLE001 — the daily task must not end; logged
            log_exception("⚠️ Vision garbage collection error", exc)
