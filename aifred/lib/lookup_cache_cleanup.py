"""Background-Task: llama.cpp Speculative-Decoding-Lookup-Caches einsammeln.

Die `--lookup-cache-dynamic`-Dateien von llama-server wachsen monoton mit
jedem n-gram, das indexiert wird. Format ist binaer (struct-by-struct),
versionslos, mit Hash-Map-Iteration-Reihenfolge — d.h. Truncation in
Python ist aufwaendig und bei llama.cpp-Updates fragil. Pragmatisch:
bei Ueberschreiten eines Groessen-Schwellwerts loeschen, llama.cpp baut
beim naechsten Start einen frischen Cache auf.

Wartungsslot: feste Uhrzeit (03:00 lokale Zeit) statt 12h-Intervall.
Vorhersagbar fuer den Nutzer, kein "Cleanup mitten in der Arbeit".
"""

from __future__ import annotations

import asyncio
import glob
import os

from .cleanup_utils import seconds_until_next_run
from .logging_utils import log_message


async def cleanup_lookup_cache_task() -> None:
    """Background-Task: Lookup-Cache-Dateien ueber Schwellwert loeschen.

    Laeuft taeglich um ``GARBAGE_COLLECTION_HOUR``:00 lokaler Zeit. Pro
    Match auf ``LOOKUP_CACHE_GLOB`` wird die Dateigroesse geprueft; bei
    ``> LOOKUP_CACHE_MAX_BYTES`` wird die Datei geloescht. llama-server
    baut beim naechsten Start automatisch einen neuen Cache auf.
    """
    from .config import (
        GARBAGE_COLLECTION_HOUR,
        LOOKUP_CACHE_GLOB,
        LOOKUP_CACHE_MAX_BYTES,
    )

    max_mb = LOOKUP_CACHE_MAX_BYTES / (1024 * 1024)
    log_message(
        f"🗑️ Lookup-Cache cleanup task started "
        f"(slot: {GARBAGE_COLLECTION_HOUR:02d}:00 lokal, "
        f"max: {max_mb:.0f} MB pro Datei)"
    )

    while True:
        try:
            sleep_seconds = seconds_until_next_run(GARBAGE_COLLECTION_HOUR)
            await asyncio.sleep(sleep_seconds)

            for path in glob.glob(LOOKUP_CACHE_GLOB):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size > LOOKUP_CACHE_MAX_BYTES:
                    try:
                        os.remove(path)
                        log_message(
                            f"🗑️ Lookup-Cache geloescht: {os.path.basename(path)} "
                            f"war {size / (1024 * 1024):.1f} MB "
                            f"(Limit: {max_mb:.0f} MB)"
                        )
                    except OSError as exc:
                        log_message(
                            f"⚠️ Lookup-Cache loeschen fehlgeschlagen: {path}: {exc}"
                        )
        except Exception as exc:  # noqa: BLE001
            log_message(f"⚠️ Lookup-Cache cleanup task error: {exc}")
            # Kurzer Schlaf, damit Endlos-Fehlerschleife nicht CPU saugt
            await asyncio.sleep(60)
