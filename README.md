# 🤖 AIfred Intelligence - Advanced AI Assistant

**Production-Ready AI Assistant with Multi-LLM Support, Web Research & Voice Interface**

AIfred Intelligence ist ein fortschrittlicher KI-Assistent mit automatischer Web-Recherche, Multi-Model-Support und History-Kompression für unbegrenzte Konversationen.

---

## ✨ Features

### 🎯 Core Features
- **Multi-LLM Support**: Ollama Backend mit verschiedenen Modellen (Qwen, Phi3, etc.)
- **Automatische Web-Recherche**: KI entscheidet selbst wann Recherche nötig ist
- **History Compression**: Intelligente Kompression bei 70% Context-Auslastung
- **Voice Interface**: Speech-to-Text und Text-to-Speech Integration
- **Cache-System**: Intelligentes Caching von Recherche-Ergebnissen

### 🔧 Technical Highlights
- **Reflex Framework**: React-Frontend aus Python generiert
- **WebSocket Streaming**: Echtzeit-Updates ohne Polling
- **Adaptive Temperature**: KI wählt Temperature basierend auf Fragetyp
- **Token Management**: Dynamische Context-Window-Berechnung
- **Debug Console**: Umfangreiches Logging und Monitoring

---

## 🚀 Installation

### Voraussetzungen
- Python 3.10+
- Ollama (für LLM Backend)
- 8GB+ RAM empfohlen

### Setup

1. **Repository klonen**:
```bash
git clone https://github.com/yourusername/AIfred-Intelligence.git
cd AIfred-Intelligence
```

2. **Virtual Environment erstellen**:
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# oder
venv\Scripts\activate     # Windows
```

3. **Dependencies installieren**:
```bash
pip install -r requirements.txt
```

4. **Umgebungsvariablen** (.env):
```env
# API Keys für Web-Recherche
BRAVE_API_KEY=your_key_here
TAVILY_API_KEY=your_key_here

# Ollama Konfiguration
OLLAMA_BASE_URL=http://localhost:11434
```

5. **Ollama Models installieren**:
```bash
ollama pull qwen3:8b        # Haupt-LLM
ollama pull qwen2.5:3b      # Automatik-LLM
ollama pull phi3:mini       # Backup/Test
```

6. **Starten**:
```bash
reflex run
```

Die App läuft dann unter: http://localhost:3002

---

## 🏗️ Architektur

### Directory Structure
```
AIfred-Intelligence/
├── aifred/
│   ├── backends/          # LLM Backend Adapters
│   ├── components/        # Reflex UI Components
│   ├── lib/              # Core Libraries
│   │   ├── agent_core.py     # Haupt-Agent-Logik
│   │   ├── context_manager.py # History-Kompression
│   │   ├── config.py         # Konfiguration
│   │   └── cache.py         # Cache-System
│   └── state.py          # Reflex State Management
├── prompts/              # System Prompts
├── logs/                 # Debug Logs
└── docs/                # Dokumentation
```

### History Compression System

Bei 70% Context-Auslastung werden automatisch ältere Konversationen komprimiert:

- **Trigger**: 70% des Context Windows belegt
- **Kompression**: 3 Frage-Antwort-Paare → 1 Summary
- **Effizienz**: ~6:1 Kompressionsrate
- **FIFO**: Maximal 10 Summaries (älteste werden gelöscht)
- **Safety**: Mindestens 1 aktuelle Konversation bleibt sichtbar

---

## 🔧 Konfiguration

Alle wichtigen Parameter in `aifred/lib/config.py`:

```python
# Deployment Mode (Production vs Development)
USE_SYSTEMD_RESTART = True  # True für Production, False für Development

# History Compression
HISTORY_COMPRESSION_THRESHOLD = 0.7  # 70% Context
HISTORY_MESSAGES_TO_COMPRESS = 6     # 3 Q&A Paare
HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION = 10

# LLM Settings
LLM_MAIN_MODEL = "qwen3:8b"
LLM_AUTOMATIK_MODEL = "qwen2.5:3b"

# Temperature Presets
TEMPERATURE_PRESETS = {
    "faktisch": 0.2,
    "gemischt": 0.5,
    "kreativ": 0.8
}
```

### HTTP Timeout Konfiguration

In `aifred/backends/ollama.py`:
- **HTTP Client Timeout**: 300 Sekunden (5 Minuten)
- Erhöht von 60s für große Research-Anfragen mit 30KB+ Context
- Verhindert Timeout-Fehler bei erster Token-Generation

### Restart-Button Verhalten

Der AIfred Restart-Button kann in zwei Modi arbeiten:

- **Production Mode** (`USE_SYSTEMD_RESTART = True`):
  - Startet den kompletten systemd-Service neu
  - Benötigt Polkit-Regel für sudo-lose Ausführung
  - Für produktive Systeme mit systemd

- **Development Mode** (`USE_SYSTEMD_RESTART = False`):
  - Soft-Restart: Löscht nur Caches und History
  - Behält laufende Instanz für Hot-Reload
  - Für lokale Entwicklung ohne Service

---

## 📦 Deployment

### Systemd Service

Für produktiven Betrieb als Service:

1. Service-File erstellen: `/etc/systemd/system/aifred-intelligence.service`
```ini
[Unit]
Description=AIfred Intelligence Voice Assistant
After=network.target ollama.service

[Service]
Type=simple
User=mp
WorkingDirectory=/home/mp/Projekte/AIfred-Intelligence
Environment="PATH=/home/mp/Projekte/AIfred-Intelligence/venv/bin"
ExecStart=/home/mp/Projekte/AIfred-Intelligence/venv/bin/python -m reflex run --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
Restart=always

[Install]
WantedBy=multi-user.target
```

2. Service aktivieren:
```bash
sudo systemctl daemon-reload
sudo systemctl enable aifred-intelligence
sudo systemctl start aifred-intelligence
```

3. **Optional: Polkit-Regel für Restart ohne sudo**

Für den Restart-Button in der Web-UI ohne Passwort-Abfrage:

`/etc/polkit-1/rules.d/50-aifred-restart.rules`:
```javascript
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.systemd1.manage-units") &&
        (action.lookup("unit") == "aifred-intelligence.service" ||
         action.lookup("unit") == "ollama.service") &&
        (action.lookup("verb") == "restart") &&
        (subject.user == "mp")) {
        return polkit.Result.YES;
    }
});
```

---

## 🛠️ Development

### Debug Logs
```bash
tail -f logs/aifred_debug.log
```

### Tests ausführen
```bash
pytest tests/
```

## 🔨 Troubleshooting

### Häufige Probleme

#### HTTP ReadTimeout bei Research-Anfragen
**Problem**: `httpx.ReadTimeout` nach 60 Sekunden bei großen Recherchen
**Lösung**: Timeout ist bereits auf 300s erhöht in `aifred/backends/ollama.py`
**Falls weiterhin Probleme**: Ollama Service neustarten mit `systemctl restart ollama`

#### Service startet nicht
**Problem**: AIfred Service startet nicht oder stoppt sofort
**Lösung**:
```bash
# Logs prüfen
journalctl -u aifred-intelligence -n 50
# Ollama Status prüfen
systemctl status ollama
```

#### Restart-Button funktioniert nicht
**Problem**: Restart-Button in Web-UI ohne Funktion
**Lösung**: Polkit-Regel prüfen in `/etc/polkit-1/rules.d/50-aifred-restart.rules`

---

## 📚 Dokumentation

Weitere Dokumentation im `docs/` Verzeichnis:
- [Architecture Overview](docs/architecture/)
- [API Documentation](docs/api/)
- [Migration Guide](docs/infrastructure/MIGRATION.md)

---

## 🤝 Contributing

Pull Requests sind willkommen! Für größere Änderungen bitte erst ein Issue öffnen.

---

## 📄 License

MIT License - siehe [LICENSE](LICENSE) file

---

**Version**: 2.0.0 (November 2025)
**Status**: Production-Ready 🚀