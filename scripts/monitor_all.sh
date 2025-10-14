#!/bin/bash
echo "👀 Live Monitor - ALLE Aktivitäten"
echo "==================================="
echo ""
echo "Zeigt alle Logs in Echtzeit."
echo "Drücke Ctrl+C zum Beenden."
echo ""

# Zeige alle neuen Logs
tail -f /var/log/voice-assistant.log
