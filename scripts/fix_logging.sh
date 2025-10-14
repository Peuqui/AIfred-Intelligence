#!/bin/bash
echo "🔧 Behebe Python Logging für Voice Assistant Service..."
echo ""

# Backup erstellen
echo "1️⃣  Erstelle Backup..."
sudo cp /etc/systemd/system/voice-assistant.service /etc/systemd/system/voice-assistant.service.backup-$(date +%Y%m%d-%H%M%S)

# Prüfe ob PYTHONUNBUFFERED schon vorhanden ist
if grep -q "PYTHONUNBUFFERED" /etc/systemd/system/voice-assistant.service; then
    echo "✅ PYTHONUNBUFFERED ist bereits gesetzt"
else
    echo "2️⃣  Füge PYTHONUNBUFFERED hinzu..."
    sudo sed -i '/Environment="PATH/a Environment="PYTHONUNBUFFERED=1"' /etc/systemd/system/voice-assistant.service
fi

# Prüfe ob python -u schon verwendet wird
if grep -q "python -u" /etc/systemd/system/voice-assistant.service; then
    echo "✅ Python -u Flag ist bereits gesetzt"
else
    echo "3️⃣  Füge -u Flag zu Python hinzu..."
    sudo sed -i 's|bin/python /home/mp|bin/python -u /home/mp|' /etc/systemd/system/voice-assistant.service
fi

echo ""
echo "4️⃣  Systemd neu laden..."
sudo systemctl daemon-reload

echo ""
echo "5️⃣  Service neu starten..."
sudo systemctl restart voice-assistant.service

echo ""
echo "✅ Fertig! Warte 3 Sekunden..."
sleep 3

echo ""
echo "📊 Service Status:"
sudo systemctl status voice-assistant.service --no-pager -l | head -10

echo ""
echo "📝 Letzte Logs:"
tail -15 /var/log/voice-assistant.log

echo ""
echo "✨ Logging sollte jetzt funktionieren!"
echo "   Teste mit: ~/ai_env/monitor_usage.sh"
