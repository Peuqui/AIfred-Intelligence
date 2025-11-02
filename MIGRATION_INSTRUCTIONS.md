# 🚀 AIfred Intelligence - Migration Instructions

## Für die nächste Claude-Instanz auf dem Mini-PC

### 📋 Quick Start

Du bekommst ein fertiges AIfred-Intelligence Verzeichnis zum Deployment auf einen Mini-PC.

### 1. System vorbereiten

```bash
# Ollama installieren (falls noch nicht vorhanden)
curl -fsSL https://ollama.com/install.sh | sh

# Benötigte Modelle laden
ollama pull qwen3:8b
ollama pull qwen2.5:3b
ollama pull phi3:mini
```

### 2. Projekt kopieren

```bash
# Zielverzeichnis erstellen
sudo mkdir -p /opt/aifred
sudo chown $USER:$USER /opt/aifred

# Projekt kopieren (OHNE .venv!)
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='.web' \
  /pfad/zum/source/AIfred-Intelligence/ \
  /opt/aifred/

cd /opt/aifred
```

### 3. Python Environment aufsetzen

```bash
# Virtual Environment erstellen
python3 -m venv venv

# Aktivieren
source venv/bin/activate

# Dependencies installieren
pip install -r requirements.txt
```

### 4. Konfiguration

Erstelle `.env` Datei:
```bash
cat > .env << 'EOF'
# API Keys für Web-Recherche (WICHTIG!)
BRAVE_API_KEY=dein_brave_api_key_hier
TAVILY_API_KEY=dein_tavily_api_key_hier

# Ollama (Standard-Werte, anpassen falls nötig)
OLLAMA_BASE_URL=http://localhost:11434
EOF
```

### 5. Test-Start

```bash
# Manuell testen
./venv/bin/python -m reflex run --frontend-port 3002 --backend-port 8001

# Browser öffnen: http://localhost:3002
# Mit Ctrl+C beenden wenn alles funktioniert
```

### 6. Systemd Service einrichten

```bash
# User für Service anlegen
sudo useradd -r -s /bin/false aifred
sudo chown -R aifred:aifred /opt/aifred

# Service-File erstellen
sudo tee /etc/systemd/system/aifred.service << 'EOF'
[Unit]
Description=AIfred Intelligence
After=network.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=aifred
Group=aifred
WorkingDirectory=/opt/aifred
Environment="PATH=/opt/aifred/venv/bin"
ExecStart=/opt/aifred/venv/bin/python -m reflex run --frontend-port 3002 --backend-port 8001
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Service aktivieren
sudo systemctl daemon-reload
sudo systemctl enable aifred.service
sudo systemctl start aifred.service

# Status prüfen
sudo systemctl status aifred

# Logs anschauen
sudo journalctl -u aifred -f
```

### 7. Firewall (falls nötig)

```bash
# Port 3002 für Web-UI öffnen
sudo ufw allow 3002/tcp comment 'AIfred Web UI'
```

## 🔧 Wichtige Hinweise

### History Compression
- Ist vollständig implementiert und produktiv konfiguriert
- Triggert bei 70% Context-Auslastung
- Komprimiert 3 Frage-Antwort-Paare → 1 Summary
- Config in `aifred/lib/config.py` anpassbar

### Debug & Logs
- Debug-Log: `logs/aifred_debug.log`
- System-Logs: `sudo journalctl -u aifred -f`
- Bei Problemen: Debug-Console in der UI nutzen

### Bekannte Funktionen
- **Automatische Web-Recherche**: KI entscheidet selbst
- **Cache-System**: Recherchen werden gecached
- **Voice Interface**: STT/TTS funktioniert
- **Temperature-Anpassung**: Automatisch basierend auf Fragetyp

### Ports
- **3002**: Frontend (Reflex/React)
- **8001**: Backend (FastAPI)
- **11434**: Ollama

## 📦 Backup

Wichtige Daten für Backup:
```bash
/opt/aifred/.env              # API Keys
/opt/aifred/logs/             # Logs (optional)
/opt/aifred/.web/reflex.db   # Reflex State DB
```

## 🚨 Troubleshooting

### Service startet nicht
```bash
# Logs checken
sudo journalctl -u aifred -n 50

# Ollama prüfen
systemctl status ollama
curl http://localhost:11434/api/tags

# Ports prüfen
ss -tulpn | grep -E '3002|8001|11434'
```

### Ollama Verbindung failed
```bash
# Ollama restart
sudo systemctl restart ollama

# Environment Variable prüfen
grep OLLAMA_BASE_URL .env
```

### Web-Recherche funktioniert nicht
- API Keys in `.env` prüfen
- Internetverbindung testen
- Debug-Log checken: `tail -f logs/aifred_debug.log`

## ✅ Fertig!

AIfred läuft jetzt unter: **http://mini-pc-ip:3002**

Bei Fragen: Schau in die Dokumentation unter `docs/` oder die README.md

---

**Stand**: 02.11.2025
**Version**: 2.0.0 (Production-Ready mit History Compression)