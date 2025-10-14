#!/bin/bash
echo "🔍 AI Voice Assistant - Aktuelle Einstellungen"
echo "=============================================="
echo ""

if [ -f /home/mp/Projekte/voice-assistant/assistant_settings.json ]; then
    echo "📄 Settings-Datei gefunden:"
    cat /home/mp/Projekte/voice-assistant/assistant_settings.json
else
    echo "⚠️  Settings-Datei existiert noch nicht!"
    echo "   Pfad: /home/mp/Projekte/voice-assistant/assistant_settings.json"
    echo ""
    echo "   Die Datei wird beim ersten Ändern einer Einstellung erstellt."
fi

echo ""
echo "=============================================="
echo "📊 Service Status:"
systemctl is-active voice-assistant.service

echo ""
echo "📝 Letzte 10 Log-Zeilen:"
tail -10 /var/log/voice-assistant.log 2>/dev/null || echo "Keine Logs verfügbar"
