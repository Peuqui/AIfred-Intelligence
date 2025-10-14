#!/bin/bash
clear
echo "👀 Live Monitor - AI Voice Assistant"
echo "====================================="
echo ""
echo "Zeigt alle Anfragen mit Model, TTS Engine und Voice."
echo "Drücke Ctrl+C zum Beenden."
echo ""
echo "Warte auf Aktivität..."
echo ""

# Monitor die Log-Datei - zeige Blöcke mit allen Infos
tail -f /var/log/voice-assistant.log | grep --line-buffered -E "^====|🤖 AI Model:|🎙️ TTS Engine:|🎤 Voice:|⚡ TTS Speed:|💬 User:"
