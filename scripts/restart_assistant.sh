#!/bin/bash
echo "🔄 Starte AI Voice Assistant neu..."
sudo systemctl restart voice-assistant.service

echo ""
echo "✅ Service neu gestartet!"
echo ""
echo "📊 Status:"
sudo systemctl status voice-assistant.service --no-pager -l | head -15

echo ""
echo "📝 Letzte Logs:"
sleep 2
tail -20 /var/log/voice-assistant.log

echo ""
echo "✨ Fertig! Öffne https://narnia.spdns.de:8443 im Browser"
