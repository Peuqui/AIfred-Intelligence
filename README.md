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
- **Vector Cache**: ChromaDB-basierter Semantic Cache für Web-Recherchen (Docker)

### 🔧 Technical Highlights
- **Reflex Framework**: React-Frontend aus Python generiert
- **WebSocket Streaming**: Echtzeit-Updates ohne Polling
- **Adaptive Temperature**: KI wählt Temperature basierend auf Fragetyp
- **Token Management**: Dynamische Context-Window-Berechnung
- **Debug Console**: Umfangreiches Logging und Monitoring
- **ChromaDB Server Mode**: Thread-safe Vector DB via Docker (0.0 distance für exakte Matches)

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

6. **ChromaDB Vector Cache starten** (Docker):
```bash
cd docker
docker-compose up -d chromadb
cd ..
```

**Optional: SearXNG auch starten** (lokale Suchmaschine):
```bash
cd docker
docker-compose --profile full up -d
cd ..
```

**ChromaDB Cache zurücksetzen** (bei Bedarf):

*Option 1: Kompletter Neustart (löscht alle Daten)*
```bash
cd docker
docker-compose stop chromadb
cd ..
rm -rf aifred_vector_cache/
cd docker
docker-compose up -d chromadb
cd ..
```

*Option 2: Nur Collection löschen (während Container läuft)*
```bash
./venv/bin/python -c "
import chromadb
from chromadb.config import Settings

client = chromadb.HttpClient(
    host='localhost',
    port=8000,
    settings=Settings(anonymized_telemetry=False)
)

try:
    client.delete_collection('research_cache')
    print('✅ Collection gelöscht')
except Exception as e:
    print(f'⚠️ Fehler: {e}')
"
```

7. **Starten**:
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

Für produktiven Betrieb als Service sind vorkonfigurierte Service-Dateien im `systemd/` Verzeichnis verfügbar.

**⚠️ WICHTIG**: Die Umgebungsvariable `AIFRED_ENV=prod` **MUSS** gesetzt sein, damit AIfred auf dem MiniPC läuft und nicht auf den Entwicklungsrechner weiterleitet!

#### Schnellinstallation

```bash
# 1. Service-Dateien kopieren
sudo cp systemd/aifred-chromadb.service /etc/systemd/system/
sudo cp systemd/aifred-intelligence.service /etc/systemd/system/

# 2. Services aktivieren und starten
sudo systemctl daemon-reload
sudo systemctl enable aifred-chromadb.service aifred-intelligence.service
sudo systemctl start aifred-chromadb.service aifred-intelligence.service

# 3. Status prüfen
systemctl status aifred-chromadb.service
systemctl status aifred-intelligence.service
```

Siehe [systemd/README.md](systemd/README.md) für Details, Troubleshooting und Monitoring.

#### Service-Dateien (Referenz)

**1. ChromaDB Service** (`systemd/aifred-chromadb.service`):
```ini
[Unit]
Description=AIfred ChromaDB Vector Cache (Docker)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/mp/Projekte/AIfred-Intelligence/docker
ExecStart=/usr/bin/docker compose up -d chromadb
ExecStop=/usr/bin/docker compose stop chromadb
```

**2. AIfred Intelligence Service** (`systemd/aifred-intelligence.service`):
```ini
[Unit]
Description=AIfred Intelligence Voice Assistant
After=network.target ollama.service aifred-chromadb.service
Requires=ollama.service aifred-chromadb.service

[Service]
Type=simple
User=mp
Group=mp
WorkingDirectory=/home/mp/Projekte/AIfred-Intelligence
Environment="PATH=/home/mp/Projekte/AIfred-Intelligence/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="AIFRED_ENV=prod"
ExecStart=/home/mp/Projekte/AIfred-Intelligence/venv/bin/python -m reflex run --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Umgebungsvariable `AIFRED_ENV` erklärt**:
- `AIFRED_ENV=dev` (Standard): API-URL = `http://172.30.8.72:8002` (Hauptrechner/WSL mit RTX 3060)
- `AIFRED_ENV=prod`: API-URL = `https://narnia.spdns.de:8443` (MiniPC mit Tesla P40)

Ohne `AIFRED_ENV=prod` werden alle API-Requests an den Entwicklungsrechner weitergeleitet, auch wenn Nginx korrekt konfiguriert ist!

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

## 📝 Session Notes - 03. November 2025

### Internationalisierung (i18n) Implementierung
- Vollständige Übersetzungstabelle für UI-Strings
- Automatische Spracherkennung für Prompts (de/en basierend auf Nutzereingabe)
- Manueller UI-Sprachumschalter in den Einstellungen hinzugefügt
- Englische Prompt-Dateien vervollständigt (waren unvollständig)

### Netzwerk- und Konfigurationsanpassungen
- `api_url` in `rxconfig.py` auf lokale IP für Entwicklungsumgebung korrigiert
- Umgebungsabhängige Konfiguration: `AIFRED_ENV=dev` vs `AIFRED_ENV=prod`
- Problem behoben: Anfragen wurden zu Mini-PC weitergeleitet statt lokal verarbeitet
- Entwicklung: `http://172.30.8.72:3002` (mit RTX 3060), Produktion: `https://narnia.spdns.de:8443`

### Bugfixes
- Parameterfehler behoben: `cache_metadata` → `cache_info` in `get_decision_making_prompt()` Aufrufen
- Funktioniert jetzt korrekt mit der definierten Funktionssignatur

---

## 📄 License

MIT License - siehe [LICENSE](LICENSE) file

---

**Version**: 2.0.0 (November 2025)
**Status**: Production-Ready 🚀