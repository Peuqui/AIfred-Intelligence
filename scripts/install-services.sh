#!/bin/bash
# AIfred Intelligence - Systemd Services Installation Script

set -e  # Exit on error

echo "🚀 AIfred Intelligence - Systemd Services Installation"
echo "======================================================"
echo

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
    echo "❌ Dieses Script muss mit sudo ausgeführt werden:"
    echo "   sudo ./systemd/install-services.sh"
    exit 1
fi

# Get the actual user (not root when using sudo)
ACTUAL_USER=${SUDO_USER:-$USER}
echo "📋 Installation für User: $ACTUAL_USER"
echo

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYSTEMD_DIR="$PROJECT_DIR/systemd"

echo "📂 Projekt-Verzeichnis: $PROJECT_DIR"
echo

# Check if service files exist
if [ ! -f "$SYSTEMD_DIR/aifred-chromadb.service" ] || [ ! -f "$SYSTEMD_DIR/aifred-intelligence.service" ]; then
    echo "❌ Service-Dateien nicht gefunden in: $SYSTEMD_DIR"
    exit 1
fi

echo "1️⃣  Erstelle Service-Dateien (mit aktuellen Pfaden)..."
# Replace placeholders in service files
sed -e "s|__USER__|$ACTUAL_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$SYSTEMD_DIR/aifred-chromadb.service" > /etc/systemd/system/aifred-chromadb.service

sed -e "s|__USER__|$ACTUAL_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$SYSTEMD_DIR/aifred-intelligence.service" > /etc/systemd/system/aifred-intelligence.service

echo "   ✅ Service-Dateien erstellt mit:"
echo "      User: $ACTUAL_USER"
echo "      Projekt: $PROJECT_DIR"
echo

echo "2️⃣  Lade Systemd neu..."
systemctl daemon-reload
echo "   ✅ Systemd neu geladen"
echo

echo "3️⃣  Aktiviere Services beim Systemstart..."
systemctl enable aifred-chromadb.service
systemctl enable aifred-intelligence.service
echo "   ✅ Services aktiviert"
echo

echo "4️⃣  Starte Services..."
systemctl start aifred-chromadb.service
echo "   ✅ ChromaDB gestartet"
sleep 2
systemctl start aifred-intelligence.service
echo "   ✅ AIfred Intelligence gestartet"
echo

echo "5️⃣  Verlinke llama-swap-restart in ~/bin..."
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

echo "6️⃣  Prüfe Service-Status..."
echo
echo "--- ChromaDB Status ---"
systemctl status aifred-chromadb.service --no-pager -l
echo
echo "--- AIfred Intelligence Status ---"
systemctl status aifred-intelligence.service --no-pager -l
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
