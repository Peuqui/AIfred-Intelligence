#!/bin/bash
# ChromaDB SQLite VACUUM — monatlich gegen Bloat aus Delete-Operationen.
#
# Hintergrund: SQLite gibt freie Pages nach DELETE nicht zurück ans OS.
# AIfred löscht regelmäßig in:
#   - aifred_documents     (de-index)
#   - research_cache       (Web-Cache-Cleanup)
#   - agent_memory_*       (Memory-Pruning pro Agent)
# Über Zeit summiert sich der freigemachte aber nicht zurückgegebene
# Speicherplatz. VACUUM rebuildet die DB-Datei und gibt freie Pages frei.
#
# Funktionsweise:
#   1. ChromaDB-Container kurz stoppen (exklusiver Lock auf SQLite nötig)
#   2. Throwaway-Container mit chroma-Image führt 'chroma vacuum' auf dem
#      Volume aus — kein Tool-Install auf dem Host nötig
#   3. Container neu starten
#
# Während Vacuum (typisch < 30s bei kleinen DBs):
#   - AIfred-Calls an ChromaDB schlagen kurz fehl
#   - Web-Search/Memory-Lookup geben "ChromaDB unavailable" Fehler zurück
#   → Daher in Tagesrand-Slot ausführen (nachts).

set -euo pipefail

CONTAINER=aifred-chromadb
VOLUME_HOST="/home/mp/Projekte/AIfred-Intelligence/aifred_vector_cache"
LOGFILE="/home/mp/Projekte/AIfred-Intelligence/data/logs/chromadb-vacuum.log"

mkdir -p "$(dirname "$LOGFILE")"
{
    echo
    echo "=== $(date -Iseconds) ChromaDB VACUUM ==="

    SIZE_BEFORE=$(du -sh "$VOLUME_HOST" 2>/dev/null | awk '{print $1}')
    echo "Size before: $SIZE_BEFORE"

    echo "Stopping $CONTAINER…"
    docker stop "$CONTAINER" >/dev/null

    echo "Running vacuum…"
    docker run --rm \
        -v "$VOLUME_HOST:/data" \
        chromadb/chroma:latest \
        chroma vacuum --path /data --force --timeout 300

    echo "Starting $CONTAINER…"
    docker start "$CONTAINER" >/dev/null

    SIZE_AFTER=$(du -sh "$VOLUME_HOST" 2>/dev/null | awk '{print $1}')
    echo "Size after:  $SIZE_AFTER"
    echo "Done."
} >> "$LOGFILE" 2>&1
