#!/usr/bin/env bash
# llama-swap-restart — Startet llama-swap neu, zeigt Startup-Ausgabe (inkl. Autoscan)
#
# Was es macht:
#   1. Stoppt llama-swap.service (systemctl) und wartet auf sauberen Stop
#   2. Killt evtl. zurueckgebliebene llama-server-Prozesse (SIGTERM, dann SIGKILL)
#   3. Wartet bis VRAM tatsaechlich freigegeben ist
#   4. Raeumt Orphan-Lookup-Caches in ~/.cache/ auf, deren Modell-Eintrag
#      nicht mehr in der llama-swap-config.yaml steht
#   5. Startet llama-swap.service neu und wartet auf "listening"-Log
#
# Installation: nach ~/bin/ verlinken (PATH-Eintrag), damit es ueberall greifbar ist:
#   ln -sf "$(realpath scripts/llama-swap-restart.sh)" ~/bin/llama-swap-restart
#   chmod +x scripts/llama-swap-restart.sh

echo "🔄 Stopping llama-swap..."
systemctl stop llama-swap

# Warte bis systemd den Service wirklich als inactive meldet (max 30s)
for i in $(seq 1 30); do
    state=$(systemctl is-active llama-swap 2>/dev/null)
    [ "$state" = "inactive" ] || [ "$state" = "failed" ] && break
    sleep 1
done

# Sicherstellen, dass keine llama-server Prozesse mehr laufen
remaining=$(pgrep -x llama-server 2>/dev/null | wc -l)
if [ "$remaining" -gt 0 ]; then
    echo "   ⚠️ $remaining llama-server Prozess(e) noch aktiv — sende SIGTERM..."
    pkill -x llama-server 2>/dev/null
    for i in $(seq 1 15); do
        remaining=$(pgrep -x llama-server 2>/dev/null | wc -l)
        [ "$remaining" -eq 0 ] && break
        sleep 1
    done
    if [ "$remaining" -gt 0 ]; then
        echo "   ⚠️ Erzwinge SIGKILL..."
        pkill -9 -x llama-server 2>/dev/null
        sleep 3
    fi
fi

echo "   ✓ Stopped"

# Warte bis llama-server VRAM tatsaechlich freigegeben ist (max 10s).
# Nach pkill ist der Prozess weg, aber der GPU-Treiber kann 1-2s brauchen,
# das VRAM zurueck in den Pool zu geben. Andere VRAM-User (XTTS, MOSS-TTS,
# AIfred-Subprozesse) sind legitim und werden hier ignoriert — die kuemmern
# llama-swap nichts. Beim Modell-Laden rechnet llama-swap selbst sein VRAM.
for i in $(seq 1 10); do
    gpu_apps=$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null)
    echo "$gpu_apps" | grep -q "llama-server" || break
    [ "$i" -eq 1 ] && echo "   ⏳ Warte auf llama-server VRAM-Freigabe..."
    sleep 1
done

# Finale Pruefung — nur auf llama-server, andere Prozesse interessieren nicht
gpu_apps=$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null)
if echo "$gpu_apps" | grep -q "llama-server"; then
    echo "   ⚠️ llama-server haelt noch VRAM nach 10s:"
    echo "$gpu_apps" | grep "llama-server"
    echo "   Versuche trotzdem zu starten..."
fi

# ── Lookup-Cache Orphan-Cleanup ────────────────────────────────────────
# Wenn Modelle aus der config entfernt wurden, bleiben deren persistente
# Speculative-Decoding-Cache-Dateien als Orphans im ~/.cache/ liegen.
# Hier am Restart-Punkt aufraeumen: sammle alle Cache-Pfade, die in der
# aktiven config noch referenziert werden — alle anderen Cache-Dateien
# im selben Ordner sind verwaist und werden geloescht.
config_yaml="/home/mp/.config/llama-swap/config.yaml"
if [ -f "$config_yaml" ]; then
    in_use=$(grep -oE -- '--lookup-cache-dynamic[[:space:]]+[^[:space:]]+' "$config_yaml" 2>/dev/null \
             | awk '{print $2}' | sort -u)
    orphans_removed=0
    for cache in /home/mp/.cache/llama_lookup_*.bin; do
        [ -f "$cache" ] || continue  # kein Match → Glob bleibt Pattern
        if ! echo "$in_use" | grep -qFx "$cache"; then
            size=$(du -h "$cache" 2>/dev/null | cut -f1)
            echo "🧹 Orphan-Cache geloescht: $(basename "$cache") ($size)"
            rm -f "$cache"
            orphans_removed=$((orphans_removed + 1))
        fi
    done
    [ "$orphans_removed" -eq 0 ] && echo "🧹 Lookup-Cache: keine Orphans gefunden"
fi

echo "🚀 Starting llama-swap..."
start_time=$(date '+%Y-%m-%d %H:%M:%S')
systemctl start llama-swap

if [ $? -ne 0 ]; then
    echo "❌ Start fehlgeschlagen!"
    exit 1
fi

# Warte bis Startup komplett ("listening" im Log), max 60s
timeout=60
elapsed=0
while ! journalctl -u llama-swap --since "$start_time" -o cat --no-pager 2>/dev/null | grep -q "listening"; do
    sleep 1
    elapsed=$((elapsed + 1))
    if [ $elapsed -ge $timeout ]; then
        echo "⚠️ Timeout nach ${timeout}s — llama-swap antwortet nicht."
        echo "Log:"
        journalctl -u llama-swap --since "$start_time" -o cat --no-pager
        exit 1
    fi
done

# Startup-Ausgabe anzeigen
echo ""
journalctl -u llama-swap --since "$start_time" -o cat --no-pager
echo ""
echo "✅ llama-swap bereit!"
