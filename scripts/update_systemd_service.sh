#!/bin/bash
# Dieses Script MUSS mit sudo ausgeführt werden!

if [ "$EUID" -ne 0 ]; then
    echo "❌ Bitte mit sudo ausführen:"
    echo "   sudo $0"
    exit 1
fi

echo "🔧 Aktualisiere systemd Service für neue Pfade..."
echo ""

# Backup erstellen
cp /etc/systemd/system/voice-assistant.service /etc/systemd/system/voice-assistant.service.backup-$(date +%Y%m%d-%H%M%S)
echo "✅ Backup erstellt"

# Neue Service-Datei schreiben
cat > /etc/systemd/system/voice-assistant.service << 'EOF'
[Unit]
Description=AI Voice Assistant Web Interface
After=network.target ollama.service

[Service]
Type=simple
User=mp
Group=mp
WorkingDirectory=/home/mp/Projekte/voice-assistant
Environment="PATH=/home/mp/Projekte/voice-assistant/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/mp/Projekte/voice-assistant/venv/bin/python -u /home/mp/Projekte/voice-assistant/mobile_voice_assistant.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/voice-assistant.log
StandardError=append:/var/log/voice-assistant.error.log

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Service-Datei aktualisiert"
echo ""

echo "📄 Neue Service-Konfiguration:"
cat /etc/systemd/system/voice-assistant.service

echo ""
echo "🔄 Systemd neu laden..."
systemctl daemon-reload

echo "🔄 Service neu starten..."
systemctl restart voice-assistant.service

echo ""
echo "⏳ Warte 3 Sekunden..."
sleep 3

echo ""
echo "📊 Service Status:"
systemctl status voice-assistant.service --no-pager -l | head -15

echo ""
echo "✨ Fertig! Voice Assistant läuft jetzt aus /home/mp/Projekte/voice-assistant/"
