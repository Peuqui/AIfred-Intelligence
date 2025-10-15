#!/bin/bash
# Systemd Service Template Generator für AIfred Intelligence
# Dieses Script erstellt eine portable systemd service-Datei basierend auf dem aktuellen Verzeichnis

if [ "$EUID" -ne 0 ]; then
    echo "❌ Bitte mit sudo ausgeführt werden:"
    echo "   sudo $0"
    exit 1
fi

# Ermittle aktuelles Projekt-Verzeichnis (ein Level über scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
MAIN_SCRIPT="$PROJECT_DIR/aifred_intelligence.py"

# Ermittle aktuellen Benutzer (der das Script mit sudo aufgerufen hat)
REAL_USER="${SUDO_USER:-$USER}"

echo "🔧 Erstelle systemd Service für AIfred Intelligence..."
echo "   📁 Projekt: $PROJECT_DIR"
echo "   👤 Benutzer: $REAL_USER"
echo ""

# Backup erstellen (falls Service existiert)
if [ -f /etc/systemd/system/aifred-intelligence.service ]; then
    cp /etc/systemd/system/aifred-intelligence.service /etc/systemd/system/aifred-intelligence.service.backup-$(date +%Y%m%d-%H%M%S)
    echo "✅ Backup erstellt"
fi

# Neue Service-Datei schreiben (mit aktuellen Pfaden!)
cat > /etc/systemd/system/aifred-intelligence.service << EOF
[Unit]
Description=AIfred Intelligence - AI Assistant with Agent Research
After=network.target ollama.service

[Service]
Type=simple
User=$REAL_USER
Group=$REAL_USER
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_PYTHON -u $MAIN_SCRIPT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Service-Datei aktualisiert"
echo ""

echo "📄 Neue Service-Konfiguration:"
cat /etc/systemd/system/aifred-intelligence.service

echo ""
echo "🔄 Systemd neu laden..."
systemctl daemon-reload

echo "🔄 Service neu starten..."
systemctl restart aifred-intelligence.service

echo ""
echo "⏳ Warte 3 Sekunden..."
sleep 3

echo ""
echo "📊 Service Status:"
systemctl status aifred-intelligence.service --no-pager -l | head -15

echo ""
echo "✨ Fertig! AIfred Intelligence läuft jetzt aus $PROJECT_DIR"
