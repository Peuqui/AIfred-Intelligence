#!/usr/bin/env python3
"""Batch-Scan einer Audio-Library zur Loudness-Messung.

Liest alle Audio-Files unter einem oder mehreren Pfaden, misst die
EBU-R128-Lautheit pro File mit ffmpeg und cached die Werte in
``data/loudness.sqlite``. Dadurch startet die Music-Wiedergabe später
ohne Latenz mit korrekter Pegel-Korrektur.

Aufruf::

    # Einen Folder scannen
    venv/bin/python scripts/scan_loudness.py data/media/audio/Klassik

    # Mehrere Folders
    venv/bin/python scripts/scan_loudness.py /mnt/nas/Musik /mnt/nas/Hoerbuecher

    # Bereits gemessene Files erneut messen (Mastering-Wechsel etc.)
    venv/bin/python scripts/scan_loudness.py --force data/media/audio

    # Nur Statistik (kein Scan)
    venv/bin/python scripts/scan_loudness.py --stats

Geschwindigkeit: ~5-15 s pro 4-min-Track auf moderner CPU. Eine
Library mit 1000 Files braucht damit grob 1-3 Stunden im Hintergrund.
Solange das Script läuft, bremst es AIfred nicht aus — Loudness-DB
wird nur per-File geschrieben.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Project-root ins sys.path damit ``aifred.*``-Imports laufen
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from aifred.lib.loudness import loudness_index  # noqa: E402


def _print_progress(scanned: int, analyzed: int, failed: int) -> None:
    sys.stdout.write(
        f"\r  scanned={scanned} analyzed={analyzed} failed={failed}   "
    )
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-Loudness-Scan für die AIfred-Audio-Library",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Pfade zu Audio-Folders (rekursiv gescannt)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bereits gemessene Files erneut messen",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Nur DB-Statistik anzeigen, kein Scan",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=1,
        help="Parallele ffmpeg-Prozesse (default: 1, sinnvoll: 2-6)",
    )
    args = parser.parse_args()

    if args.stats:
        s = loudness_index.stats()
        print(f"Loudness DB: {s['total']} files cached, {s['failed']} failed")
        return 0

    if not args.paths:
        parser.print_help()
        return 1

    total_start = time.monotonic()
    grand_scanned = grand_analyzed = grand_cached = grand_failed = 0

    for path in args.paths:
        if not path.exists():
            print(f"⚠️  {path}: not found, skipping")
            continue
        if not path.is_dir():
            print(f"⚠️  {path}: not a directory, skipping")
            continue
        print(f"📂 {path}")
        result = loudness_index.scan_directory(
            path,
            on_progress=_print_progress,
            force=args.force,
            workers=args.workers,
        )
        sys.stdout.write("\n")
        print(
            f"   scanned={result.scanned} analyzed={result.analyzed} "
            f"cached={result.cached} failed={result.failed} "
            f"in {result.elapsed_sec:.1f}s"
        )
        grand_scanned += result.scanned
        grand_analyzed += result.analyzed
        grand_cached += result.cached
        grand_failed += result.failed

    total_elapsed = time.monotonic() - total_start
    print(
        f"\n✅ Done: {grand_scanned} files seen, "
        f"{grand_analyzed} newly analyzed, "
        f"{grand_cached} already cached, "
        f"{grand_failed} failed "
        f"in {total_elapsed:.1f}s"
    )
    return 0 if grand_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
