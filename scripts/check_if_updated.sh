#!/bin/bash
echo "🔍 Prüfe ob neuer Code läuft..."
echo ""

# Prüfe ob die neuen Debug-Ausgaben im Code sind
if grep -q "🚀 AI Voice Assistant startet" /home/mp/Projekte/voice-assistant/mobile_voice_assistant.py; then
    echo "✅ Neuer Code ist in der Datei vorhanden"
else
    echo "❌ Alter Code in der Datei"
    exit 1
fi

# Prüfe ob Service läuft
if systemctl is-active --quiet voice-assistant.service; then
    echo "✅ Service läuft"

    # Prüfe Startzeit des Service
    START_TIME=$(systemctl show voice-assistant.service -p ActiveEnterTimestamp --value)
    echo "   Started: $START_TIME"

    # Prüfe ob neue Logs vorhanden sind
    echo ""
    echo "📝 Suche nach neuen Debug-Ausgaben in Logs:"

    if grep -q "🚀 AI Voice Assistant startet" /var/log/voice-assistant.log 2>/dev/null; then
        echo "✅ NEUER CODE LÄUFT! Debug-Ausgaben gefunden."
        echo ""
        echo "Letzte Startup-Logs:"
        grep -A 8 "🚀 AI Voice Assistant startet" /var/log/voice-assistant.log | tail -10
    else
        echo "❌ ALTER CODE LÄUFT NOCH!"
        echo ""
        echo "⚠️  Du musst den Service neu starten mit:"
        echo "   sudo systemctl restart voice-assistant.service"
    fi
else
    echo "❌ Service läuft nicht"
fi
