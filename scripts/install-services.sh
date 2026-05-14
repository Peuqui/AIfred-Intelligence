#!/bin/bash
# AIfred Intelligence - Systemd Services Installation Script

set -e  # Exit on error

echo "🚀 AIfred Intelligence - Systemd Services Installation"
echo "======================================================"
echo

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
    echo "❌ Dieses Script muss mit sudo ausgeführt werden:"
    echo "   sudo ./scripts/install-services.sh"
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

# venv-Check — die Hauptservice braucht $PROJECT_DIR/venv/bin/python
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "❌ Python-venv nicht gefunden: $PROJECT_DIR/venv"
    echo "   Bitte zuerst ./scripts/setup-python.sh ausführen."
    exit 1
fi

# Check if service files exist
if [ ! -f "$SYSTEMD_DIR/aifred-chromadb.service" ] || [ ! -f "$SYSTEMD_DIR/aifred-intelligence.service" ]; then
    echo "❌ Service-Dateien nicht gefunden in: $SYSTEMD_DIR"
    exit 1
fi

# Helper: Service-Datei templaten und installieren
install_service() {
    local src="$1"
    local dst="/etc/systemd/system/$(basename "$src")"
    sed -e "s|__USER__|$ACTUAL_USER|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        "$src" > "$dst"
    echo "   ✅ Installiert: $(basename "$src")"
}

echo "1️⃣  Installiere Haupt-Services (chromadb + aifred-intelligence)..."
install_service "$SYSTEMD_DIR/aifred-chromadb.service"
install_service "$SYSTEMD_DIR/aifred-intelligence.service"
echo
echo "   Konfiguriert mit:"
echo "      User:        $ACTUAL_USER"
echo "      Projekt-Dir: $PROJECT_DIR"
echo

# Corpus-Search-Server (Korpus-Such-API, z.B. Bibel/Lexika)
if [ -f "$SYSTEMD_DIR/aifred-corpus-server.service" ]; then
    echo "2️⃣  Installiere aifred-corpus-server.service (Korpus-Such-API)..."
    install_service "$SYSTEMD_DIR/aifred-corpus-server.service"
    INSTALL_CORPUS=1
else
    INSTALL_CORPUS=0
fi
echo

echo "3️⃣  Lade Systemd neu..."
systemctl daemon-reload
echo "   ✅ Systemd neu geladen"
echo

echo "4️⃣  Aktiviere Services beim Systemstart..."
systemctl enable aifred-chromadb.service
systemctl enable aifred-intelligence.service
[ "$INSTALL_CORPUS" = "1" ] && systemctl enable aifred-corpus-server.service
echo "   ✅ Services aktiviert"
echo

echo "5️⃣  Starte Services..."
# ChromaDB nur wenn Docker da ist — sonst schlägt der ExecStart fehl.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    systemctl start aifred-chromadb.service
    echo "   ✅ ChromaDB gestartet"
    sleep 2
else
    echo "   ⚠️  Docker oder 'docker compose' fehlt — aifred-chromadb.service nicht gestartet."
    echo "      ChromaDB manuell starten, sobald Docker installiert ist:"
    echo "        sudo systemctl start aifred-chromadb.service"
fi
systemctl start aifred-intelligence.service
echo "   ✅ AIfred Intelligence gestartet"
if [ "$INSTALL_CORPUS" = "1" ]; then
    systemctl start aifred-corpus-server.service
    echo "   ✅ Corpus-Server gestartet"
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
    sudo -u "$ACTUAL_USER" mkdir -p "$USER_BIN"
    chmod +x "$RESTART_SCRIPT"
    sudo -u "$ACTUAL_USER" ln -sf "$RESTART_SCRIPT" "$USER_BIN/llama-swap-restart"
    echo "   ✅ Symlink: $USER_BIN/llama-swap-restart -> $RESTART_SCRIPT"
    # PATH-Hinweis falls ~/bin nicht in PATH
    if ! sudo -u "$ACTUAL_USER" bash -c 'echo "$PATH"' | tr ':' '\n' | grep -qx "$USER_BIN"; then
        echo "   ⚠️  $USER_BIN ist nicht im PATH — fuege in ~/.bashrc hinzu:"
        echo "       export PATH=\"\$HOME/bin:\$PATH\""
    fi
else
    echo "   ⚠️  $RESTART_SCRIPT nicht gefunden — Symlink uebersprungen"
fi
echo

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

echo "✅ Installation abgeschlossen!"
echo
echo "📊 Nützliche Befehle:"
echo "   Logs ansehen:    journalctl -u aifred-intelligence.service -f"
echo "   Service neu starten: sudo systemctl restart aifred-intelligence.service"
echo "   Service stoppen:     sudo systemctl stop aifred-intelligence.service"
echo "   Status prüfen:       systemctl status aifred-intelligence.service"
echo
echo "📚 Siehe systemd/README.md für weitere Informationen"
