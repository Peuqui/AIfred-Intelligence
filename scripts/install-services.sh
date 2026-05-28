#!/bin/bash
# AIfred Intelligence - Systemd Services Installation Script
#
# Modes:
#   sudo ./scripts/install-services.sh                  normal install/update —
#                                                       differing files are
#                                                       backed up + overwritten
#   sudo ./scripts/install-services.sh --no-overwrite   keep existing files;
#                                                       only create missing
#                                                       ones, warn on diffs
#        ./scripts/install-services.sh --dry-run        show what WOULD change,
#                                                       no disk writes, no
#                                                       systemctl side-effects

set -e  # Exit on error

# ── Argument parsing ────────────────────────────────────────────
DRY_RUN=0
NO_OVERWRITE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)         DRY_RUN=1 ;;
        --no-overwrite|-N)    NO_OVERWRITE=1 ;;
        --help|-h)
            sed -n '2,11p' "$0"
            exit 0
            ;;
        *)
            echo "❌ Unknown flag: $arg"
            sed -n '2,11p' "$0"
            exit 1
            ;;
    esac
done

if [ "$DRY_RUN" = "1" ]; then
    echo "🔬 AIfred Intelligence - Systemd Services Installation (DRY-RUN)"
    echo "================================================================"
    echo "   No files written. No systemctl side-effects. Shows planned changes."
    [ "$NO_OVERWRITE" = "1" ] && echo "   --no-overwrite is honored in the simulation."
    echo
elif [ "$NO_OVERWRITE" = "1" ]; then
    echo "🛡  AIfred Intelligence - Systemd Services Installation (NO-OVERWRITE)"
    echo "====================================================================="
    echo "   Existing service files are kept untouched. Only missing files are"
    echo "   created. Differences are reported, not applied."
    echo
else
    echo "🚀 AIfred Intelligence - Systemd Services Installation"
    echo "======================================================"
    echo
fi

# Check if running with sudo — except in dry-run, where we don't touch /etc/.
if [ "$DRY_RUN" != "1" ] && [ "$EUID" -ne 0 ]; then
    echo "❌ Dieses Script muss mit sudo ausgeführt werden:"
    echo "   sudo ./scripts/install-services.sh"
    echo "   (oder ./scripts/install-services.sh --dry-run für eine Vorschau ohne sudo)"
    exit 1
fi

# Get the actual user (not root when using sudo)
ACTUAL_USER=${SUDO_USER:-$USER}
if [ -z "$ACTUAL_USER" ] || [ "$ACTUAL_USER" = "root" ]; then
    echo "❌ Konnte den eigentlichen User nicht ermitteln."
    echo "   Bitte via 'sudo ./scripts/install-services.sh' starten,"
    echo "   nicht aus einer root-Shell."
    exit 1
fi
echo "📋 Installation für User: $ACTUAL_USER"
echo

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYSTEMD_DIR="$PROJECT_DIR/systemd"

echo "📂 Projekt-Verzeichnis: $PROJECT_DIR"
echo

# venv-Check — die Hauptservice braucht $PROJECT_DIR/venv/bin/python.
# Im --dry-run wird nichts ausgefuehrt, also ist ein fehlendes venv
# kein Blocker — nur ein Hinweis. Im Real-Run hard-failen wir.
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        echo "⚠️  Python-venv fehlt: $PROJECT_DIR/venv  (im Real-Run hard-fail)"
        echo "   In einem echten Setup würde install-all.sh das venv vorher anlegen."
    else
        echo "❌ Python-venv nicht gefunden: $PROJECT_DIR/venv"
        echo "   Bitte zuerst ./scripts/setup-python.sh ausführen."
        exit 1
    fi
fi

# Check if service files exist
if [ ! -f "$SYSTEMD_DIR/aifred-chromadb.service" ] || [ ! -f "$SYSTEMD_DIR/aifred-intelligence.service" ]; then
    echo "❌ Service-Dateien nicht gefunden in: $SYSTEMD_DIR"
    exit 1
fi

# Docker-Binary auflösen — das aifred-chromadb.service Template enthält den
# Placeholder __DOCKER_BIN__, weil docker nicht überall unter /usr/bin liegt
# (Snap-Docker → /snap/bin/docker, manueller Install → /usr/local/bin/docker).
# Fallback /usr/bin/docker, falls docker noch gar nicht im PATH ist (Service
# läuft sowieso erst, wenn Docker installiert ist — der Fix kommt dann via
# 'sudo systemctl edit aifred-chromadb.service' oder Re-Run dieses Scripts).
DOCKER_BIN="$(command -v docker || true)"
if [ -z "$DOCKER_BIN" ]; then
    DOCKER_BIN="/usr/bin/docker"
    echo "ℹ️  docker noch nicht im PATH — Fallback auf $DOCKER_BIN."
    echo "   Falls Docker später unter anderem Pfad landet (z.B. /snap/bin/docker),"
    echo "   installiere danach nochmal: sudo $SCRIPT_DIR/install-services.sh"
fi

# Update-Modus: bei Re-Run werden Service-Dateien nicht stumm überschrieben.
# Drei Fälle pro Datei:
#   * existiert nicht          → schreiben (return 0 = "geschrieben")
#   * existiert + identisch    → silent skip (return 2 = "unchanged")
#   * existiert + abweichend   → Backup .pre-aifred-update.<timestamp> + schreiben
#                                (return 0 = "geschrieben")
# Ein globales SERVICES_CHANGED-Flag sammelt, ob daemon-reload / restart nötig
# sind. Idempotenter Re-Run kann so problemlos täglich laufen ohne den Service
# zu unterbrechen.
SERVICES_CHANGED=0

# Render a service template to stdout (substitutes __USER__, __PROJECT_DIR__,
# __DOCKER_BIN__). Separating render from write lets us cmp the rendered
# output against the on-disk file without touching disk first.
_render_service_template() {
    local src="$1"
    sed -e "s|__USER__|$ACTUAL_USER|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__DOCKER_BIN__|$DOCKER_BIN|g" \
        "$src"
}

# Helper: Service-Datei templaten und installieren — idempotent.
install_service() {
    local src="$1"
    local name="$(basename "$src")"
    local dst="/etc/systemd/system/${name}"
    local tmp; tmp="$(mktemp)"
    _render_service_template "$src" > "$tmp"

    if [ ! -f "$dst" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "   📝 WOULD create: ${name}"
            rm -f "$tmp"
        else
            mv "$tmp" "$dst"
            chmod 644 "$dst"
            echo "   ✅ Neu installiert: ${name}"
        fi
        SERVICES_CHANGED=1
        return 0
    fi

    if cmp -s "$tmp" "$dst"; then
        rm -f "$tmp"
        echo "   = Unverändert:    ${name}"
        return 0
    fi

    # Datei existiert und unterscheidet sich.
    if [ "$NO_OVERWRITE" = "1" ]; then
        echo "   🛡  Behalten:      ${name}  (--no-overwrite — siehe Diff:)"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        echo "      (Repo-Stand würde sonst installiert; lokale Anpassungen unverändert.)"
        rm -f "$tmp"
        return 0
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD update: ${name}  (current → new diff):"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        echo "      (would backup to ${name}.pre-aifred-<timestamp>)"
        rm -f "$tmp"
    else
        local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
        local backup="${dst}.pre-aifred-${stamp}"
        cp -p "$dst" "$backup"
        mv "$tmp" "$dst"
        chmod 644 "$dst"
        echo "   ♻️  Aktualisiert:  ${name}  (Backup: $(basename "$backup"))"
    fi
    SERVICES_CHANGED=1
}

# Helper: Drop-in-Konfig (override.conf, hardening.conf, …) idempotent
# unter /etc/systemd/system/<service>.d/ installieren. Logik identisch
# zu install_service: schreibt nur bei Mismatch und legt vorher ein
# Backup an.
install_dropin() {
    local svc_name="$1"      # z.B. aifred-intelligence.service
    local src="$2"           # Pfad zu Source-Drop-in
    local dst_dir="/etc/systemd/system/${svc_name}.d"
    local name="$(basename "$src")"
    local dst="${dst_dir}/${name}"
    [ "$DRY_RUN" = "1" ] || mkdir -p "$dst_dir"
    local tmp; tmp="$(mktemp)"
    _render_service_template "$src" > "$tmp"

    if [ ! -f "$dst" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "   📝 WOULD create dropin: ${svc_name}.d/${name}"
            rm -f "$tmp"
        else
            mv "$tmp" "$dst"
            chmod 644 "$dst"
            echo "   ✅ Drop-in neu:    ${svc_name}.d/${name}"
        fi
        SERVICES_CHANGED=1
        return 0
    fi

    if cmp -s "$tmp" "$dst"; then
        rm -f "$tmp"
        echo "   = Drop-in unverändert: ${svc_name}.d/${name}"
        return 0
    fi

    if [ "$NO_OVERWRITE" = "1" ]; then
        echo "   🛡  Drop-in behalten: ${svc_name}.d/${name}  (--no-overwrite — Diff:)"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        rm -f "$tmp"
        return 0
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD update dropin: ${svc_name}.d/${name}  (current → new diff):"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        rm -f "$tmp"
    else
        local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
        local backup="${dst}.pre-aifred-${stamp}"
        cp -p "$dst" "$backup"
        mv "$tmp" "$dst"
        chmod 644 "$dst"
        echo "   ♻️  Drop-in update: ${svc_name}.d/${name}  (Backup: $(basename "$backup"))"
    fi
    SERVICES_CHANGED=1
}

echo "1️⃣  Installiere Haupt-Services (chromadb + aifred-intelligence)..."
install_service "$SYSTEMD_DIR/aifred-chromadb.service"
install_service "$SYSTEMD_DIR/aifred-intelligence.service"

# Drop-in für aifred-intelligence.service installieren, sofern vorhanden.
# Der Drop-in (hardening.conf) erweitert REFLEX_HOT_RELOAD_EXCLUDE_PATHS um
# aifred_vector_cache/scripts/deploy/docs/docker/tests/systemd. Ohne diesen
# Drop-in killt Granian den Reflex-Worker bei jeder ChromaDB-Embed-Batch
# (chroma.sqlite3-Schreibvorgang triggert Hot-Reload), wodurch Indexing-Jobs
# nach der ersten Batch sterben. Außerdem: leert Requires= und setzt Wants=
# damit ChromaDB-Recreate (Docker-Auto-Update) keinen Cascade-Stop von
# AIfred auslöst.
DROPIN_DIR="$SYSTEMD_DIR/aifred-intelligence.service.d"
if [ -d "$DROPIN_DIR" ]; then
    for dropin in "$DROPIN_DIR"/*.conf; do
        [ -f "$dropin" ] || continue
        install_dropin "aifred-intelligence.service" "$dropin"
    done
fi

echo
echo "   Konfiguriert mit:"
echo "      User:        $ACTUAL_USER"
echo "      Projekt-Dir: $PROJECT_DIR"
echo "      Docker-Bin:  $DOCKER_BIN"
echo

# Corpus-Search-Server (FastAPI Korpus-Such-API).
# Default-Ja: dient als Backend für die Korpus-/Judaica-Such-UI hinter nginx.
# Standalone-AIfred funktioniert auch ohne, aber das Repo enthält die Service-
# Datei und das passende Python-Script (scripts/corpus_search_server.py) —
# wenn der User opt-out will, kann er N sagen.
INSTALL_CORPUS=0
if [ -f "$SYSTEMD_DIR/aifred-corpus-server.service" ]; then
    echo "2️⃣  Corpus-Search-Server (FastAPI Korpus-Such-API)"
    echo "   Default-installation — Backend für Korpus-/Judaica-Such-UI."
    if [ -t 0 ]; then
        read -p "   Installieren? (J/n): " -n 1 -r CORPUS_REPLY
        echo
        if [[ ! $CORPUS_REPLY =~ ^[Nn]$ ]]; then
            install_service "$SYSTEMD_DIR/aifred-corpus-server.service"
            INSTALL_CORPUS=1
        else
            echo "   ⏭️  übersprungen"
        fi
    else
        # Nicht-interaktiv: default-installieren (passt zum Default-Ja).
        install_service "$SYSTEMD_DIR/aifred-corpus-server.service"
        INSTALL_CORPUS=1
        echo "   ✅ Nicht-interaktiver Aufruf — default-installiert"
    fi
fi
echo

# Idempotent ensure: enable always (no-op if already enabled), and prefer
# start over restart unless we actually changed the unit file. Restarting
# a healthy service for no reason interrupts a running calibration etc.
ensure_active() {
    local svc="$1"
    if [ "$DRY_RUN" = "1" ]; then
        local state; state="$(systemctl is-active "$svc" 2>&1 || true)"
        if [ "$state" = "active" ]; then
            if [ "$SERVICES_CHANGED" = "1" ]; then
                echo "   📝 WOULD restart: ${svc}  (unit changed, currently $state)"
            else
                echo "   = ${svc} already active, nothing to do"
            fi
        else
            echo "   📝 WOULD start: ${svc}  (currently $state)"
        fi
        return 0
    fi
    if systemctl is-active --quiet "$svc"; then
        if [ "$SERVICES_CHANGED" = "1" ]; then
            systemctl restart "$svc"
            echo "   🔄 Neu gestartet: ${svc}  (Unit-Datei hat sich geändert)"
        else
            echo "   = ${svc} läuft bereits, kein Restart nötig"
        fi
    else
        systemctl start "$svc"
        echo "   ✅ Gestartet:    ${svc}"
    fi
}

echo "3️⃣  Lade Systemd neu..."
if [ "$SERVICES_CHANGED" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD: systemctl daemon-reload  (unit files changed)"
    else
        systemctl daemon-reload
        echo "   ✅ Systemd neu geladen"
    fi
else
    echo "   = Keine Unit-Änderungen, daemon-reload übersprungen"
fi
echo

echo "4️⃣  Aktiviere Services beim Systemstart..."
if [ "$DRY_RUN" = "1" ]; then
    for s in aifred-chromadb.service aifred-intelligence.service; do
        if systemctl is-enabled --quiet "$s" 2>/dev/null; then
            echo "   = $s already enabled"
        else
            echo "   📝 WOULD enable: $s"
        fi
    done
    [ "$INSTALL_CORPUS" = "1" ] && {
        if systemctl is-enabled --quiet aifred-corpus-server.service 2>/dev/null; then
            echo "   = aifred-corpus-server.service already enabled"
        else
            echo "   📝 WOULD enable: aifred-corpus-server.service"
        fi
    }
else
    systemctl enable aifred-chromadb.service
    systemctl enable aifred-intelligence.service
    [ "$INSTALL_CORPUS" = "1" ] && systemctl enable aifred-corpus-server.service
    echo "   ✅ Services aktiviert"
fi
echo

echo "5️⃣  Starte / Aktualisiere Services..."
# ChromaDB nur wenn Docker da ist — sonst schlägt der ExecStart fehl.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    ensure_active aifred-chromadb.service
    [ "$DRY_RUN" = "1" ] || sleep 2
else
    echo "   ⚠️  Docker oder 'docker compose' fehlt — aifred-chromadb.service nicht gestartet."
    echo "      ChromaDB manuell starten, sobald Docker installiert ist:"
    echo "        sudo systemctl start aifred-chromadb.service"
fi
ensure_active aifred-intelligence.service
if [ "$INSTALL_CORPUS" = "1" ]; then
    ensure_active aifred-corpus-server.service
fi
echo

echo "6️⃣  Verlinke llama-swap-restart in ~/bin..."
# llama-swap-restart ist das Wartungs-Skript fuer Modell-Wechsel und
# Config-Updates. Es verschwindet sonst gerne im scripts/-Ordner.
# Symlink macht es global ausfuehrbar wenn ~/bin im PATH liegt.
USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
USER_BIN="$USER_HOME/bin"
RESTART_SCRIPT="$PROJECT_DIR/scripts/llama-swap-restart.sh"
if [ -f "$RESTART_SCRIPT" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        if [ -L "$USER_BIN/llama-swap-restart" ] && \
           [ "$(readlink "$USER_BIN/llama-swap-restart" 2>/dev/null)" = "$RESTART_SCRIPT" ]; then
            echo "   = Symlink already in place: $USER_BIN/llama-swap-restart"
        else
            echo "   📝 WOULD symlink: $USER_BIN/llama-swap-restart -> $RESTART_SCRIPT"
        fi
    else
        sudo -u "$ACTUAL_USER" mkdir -p "$USER_BIN"
        chmod +x "$RESTART_SCRIPT"
        sudo -u "$ACTUAL_USER" ln -sf "$RESTART_SCRIPT" "$USER_BIN/llama-swap-restart"
        echo "   ✅ Symlink: $USER_BIN/llama-swap-restart -> $RESTART_SCRIPT"
        # PATH-Hinweis falls ~/bin nicht in PATH
        if ! sudo -u "$ACTUAL_USER" bash -c 'echo "$PATH"' | tr ':' '\n' | grep -qx "$USER_BIN"; then
            echo "   ⚠️  $USER_BIN ist nicht im PATH — fuege in ~/.bashrc hinzu:"
            echo "       export PATH=\"\$HOME/bin:\$PATH\""
        fi
    fi
else
    echo "   ⚠️  $RESTART_SCRIPT nicht gefunden — Symlink uebersprungen"
fi
echo

# Dry-run endet hier — status + verification beziehen sich auf die
# Real-Installation. Der User sieht oben in den 6 Steps was passieren würde.
if [ "$DRY_RUN" = "1" ]; then
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Dry-Run beendet. Keine Disk-Writes, keine Service-Side-Effects."
    if [ "$SERVICES_CHANGED" = "1" ]; then
        echo "  ⚠️  Einige Service-Files würden geschrieben/aktualisiert."
        echo "  Real ausführen: sudo $0"
    else
        echo "  ✅ Alle Service-Files bereits aktuell. Real-Run würde nichts ändern."
    fi
    echo "═══════════════════════════════════════════════════════════════"
    exit 0
fi

echo "7️⃣  Prüfe Service-Status..."
echo
echo "--- ChromaDB Status ---"
systemctl status aifred-chromadb.service --no-pager -l || true
echo
echo "--- AIfred Intelligence Status ---"
systemctl status aifred-intelligence.service --no-pager -l || true
if [ "$INSTALL_CORPUS" = "1" ]; then
    echo
    echo "--- AIfred Corpus-Server Status ---"
    systemctl status aifred-corpus-server.service --no-pager -l || true
fi
echo

# ────────────────────────────────────────────────────────────────
# Lauffähigkeits-Verifikation aller gestarteten Services
# 'systemctl status' allein reicht NICHT — der Befehl gibt auch Exit 0
# zurück wenn der Service "loaded but failed" ist. Wir prüfen explizit
# is-active + die offenen TCP-Ports.
# ────────────────────────────────────────────────────────────────
echo "8️⃣  Verifiziere Lauffähigkeit der Services..."
echo
SERVICE_FAILURES=()

check_service_active() {
    local svc="$1"
    if systemctl is-active --quiet "$svc"; then
        echo "   ✅ $svc aktiv"
    else
        echo "   ❌ $svc NICHT aktiv (is-active = $(systemctl is-active "$svc" 2>&1 || true))"
        echo "      → journalctl -u $svc -e --no-pager"
        SERVICE_FAILURES+=("$svc nicht aktiv")
    fi
}

# ChromaDB nur prüfen, wenn wir ihn gestartet haben.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    check_service_active aifred-chromadb.service
    # Port-Probe (Container hört intern auf 8000). Beim Frischstart kann
    # zusätzlich der Image-Pull (chromadb/chroma:latest ~200 MB) Zeit
    # kosten — daher großzügige 120s Toleranz statt 30s.
    chroma_port_ok=0
    for _ in $(seq 1 60); do
        (echo > /dev/tcp/localhost/8000) &>/dev/null && { chroma_port_ok=1; break; }
        sleep 2
    done
    if [ "$chroma_port_ok" = "1" ]; then
        echo "   ✅ Port 8000 (ChromaDB HTTP) offen"
    else
        echo "   ❌ Port 8000 (ChromaDB) nicht erreichbar nach 120s"
        echo "      → docker logs aifred-chromadb"
        SERVICE_FAILURES+=("ChromaDB Port 8000 nicht erreichbar")
    fi
fi

check_service_active aifred-intelligence.service

# AIfred-Backend braucht beim Erststart ~30-90s (Bun-Setup, Reflex compiliert
# das Frontend, lädt Modelle aus config.py etc.). Großzügige 120s pollen.
aifred_port_ok=0
for _ in $(seq 1 60); do
    (echo > /dev/tcp/localhost/8002) &>/dev/null && { aifred_port_ok=1; break; }
    sleep 2
done
if [ "$aifred_port_ok" = "1" ]; then
    echo "   ✅ Port 8002 (AIfred-Backend) offen"
else
    echo "   ❌ Port 8002 (AIfred-Backend) nicht erreichbar nach 120s"
    echo "      → journalctl -u aifred-intelligence.service -e --no-pager"
    SERVICE_FAILURES+=("AIfred-Backend Port 8002 nicht erreichbar")
fi

# Frontend-Port 3002: beim Erststart 2-5 Min realistisch (Bun-Setup, Vite-Build).
# Hier 60s pollen — Warnung statt Failure, Backend reicht für CLI/API.
frontend_port_ok=0
for _ in $(seq 1 30); do
    (echo > /dev/tcp/localhost/3002) &>/dev/null && { frontend_port_ok=1; break; }
    sleep 2
done
if [ "$frontend_port_ok" = "1" ]; then
    echo "   ✅ Port 3002 (AIfred-Frontend) offen"
else
    echo "   ⚠️  Port 3002 (AIfred-Frontend) noch nicht offen — Erststart-Build kann 2-5 Min dauern"
    echo "      → journalctl -u aifred-intelligence.service -f"
fi

if [ "$INSTALL_CORPUS" = "1" ]; then
    check_service_active aifred-corpus-server.service
fi
echo

if [ ${#SERVICE_FAILURES[@]} -gt 0 ]; then
    echo "❌ Installation abgeschlossen, aber Services laufen nicht sauber:"
    for f in "${SERVICE_FAILURES[@]}"; do
        echo "   • $f"
    done
    echo
    SERVICE_EXIT=1
else
    echo "✅ Installation abgeschlossen — alle Services laufen!"
    SERVICE_EXIT=0
fi
echo
echo "📊 Nützliche Befehle:"
echo "   Logs ansehen:    journalctl -u aifred-intelligence.service -f"
echo "   Service neu starten: sudo systemctl restart aifred-intelligence.service"
echo "   Service stoppen:     sudo systemctl stop aifred-intelligence.service"
echo "   Status prüfen:       systemctl status aifred-intelligence.service"
echo
echo "📚 Siehe systemd/README.md für weitere Informationen"

# Exit-Code propagieren — install-all.sh kann darauf reagieren.
exit "${SERVICE_EXIT:-0}"
