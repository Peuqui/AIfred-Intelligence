# 🤖 AIfred Intelligence - Advanced AI Assistant

**Production-Ready AI Assistant with Multi-LLM Support, Web Research & Voice Interface**

AIfred Intelligence ist ein fortschrittlicher KI-Assistent mit automatischer Web-Recherche, Multi-Model-Support und History-Kompression für unbegrenzte Konversationen.

---

## ✨ Features

### 🎯 Core Features
- **Multi-Backend Support**: Ollama (GGUF), vLLM (AWQ), TabbyAPI (EXL2)
- **Qwen3 Thinking Mode**: Chain-of-Thought Reasoning für komplexe Aufgaben
- **Automatische Web-Recherche**: KI entscheidet selbst wann Recherche nötig ist
- **History Compression**: Intelligente Kompression bei 70% Context-Auslastung
- **Voice Interface**: Speech-to-Text und Text-to-Speech Integration
- **Vector Cache**: ChromaDB-basierter Semantic Cache für Web-Recherchen (Docker)
- **Per-Backend Settings**: Jedes Backend merkt sich seine bevorzugten Modelle

### 🔧 Technical Highlights
- **Reflex Framework**: React-Frontend aus Python generiert
- **WebSocket Streaming**: Echtzeit-Updates ohne Polling
- **Adaptive Temperature**: KI wählt Temperature basierend auf Fragetyp
- **Token Management**: Dynamische Context-Window-Berechnung
- **Debug Console**: Umfangreiches Logging und Monitoring
- **ChromaDB Server Mode**: Thread-safe Vector DB via Docker (0.0 distance für exakte Matches)
- **GPU Detection**: Automatische Erkennung und Warnung bei inkompatiblen Backend-GPU-Kombinationen ([docs/GPU_COMPATIBILITY.md](docs/GPU_COMPATIBILITY.md))

---

## 🚀 Installation

### Voraussetzungen
- Python 3.10+
- **LLM Backend** (wähle eins):
  - **Ollama** (einfach, GGUF-Modelle) - empfohlen für Start
  - **vLLM** (schnell, AWQ-Modelle) - beste Performance (requires Compute Capability 7.5+)
  - **TabbyAPI** (ExLlamaV2/V3, EXL2-Modelle) - experimentell
- 8GB+ RAM (12GB+ empfohlen für größere Modelle)
- Docker (für ChromaDB Vector Cache)
- **GPU**: NVIDIA GPU empfohlen (siehe [GPU Compatibility Guide](docs/GPU_COMPATIBILITY.md))

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

5. **LLM Models installieren**:

**Option A: Alle Models (Empfohlen)**
```bash
# Master-Script für beide Backends
./scripts/download_all_models.sh
```

**Option B: Nur Ollama (GGUF) - Einfachste Installation**
```bash
# Ollama Models (GGUF Q4/Q8)
./scripts/download_ollama_models.sh

# Empfohlene Core-Modelle:
# - qwen3:30b-instruct (18GB) - Haupt-LLM, 256K context
# - qwen3:8b (5.2GB) - Automatik, optional thinking
# - qwen2.5:3b (1.9GB) - Ultra-schnelle Automatik
```

**Option C: Nur vLLM (AWQ) - Beste Performance**
```bash
# vLLM installieren (falls noch nicht geschehen)
pip install vllm

# vLLM Models (AWQ Quantization)
./scripts/download_vllm_models.sh

# Empfohlene Modelle:
# - Qwen3-8B-AWQ (~5GB, 40K→128K mit YaRN)
# - Qwen3-14B-AWQ (~8GB, 32K→128K mit YaRN)
# - Qwen2.5-14B-Instruct-AWQ (~8GB, 128K native)

# vLLM Server starten mit YaRN (64K context)
./venv/bin/vllm serve Qwen/Qwen3-14B-AWQ \
  --quantization awq_marlin \
  --port 8001 \
  --rope-scaling '{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768}' \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.85

# Oder als systemd Service
sudo cp vllm_qwen3_awq.service /etc/systemd/system/
sudo systemctl enable vllm_qwen3_awq
sudo systemctl start vllm_qwen3_awq
```

**Option C: TabbyAPI (EXL2) - Experimentell**
```bash
# Noch nicht vollständig implementiert
# Siehe: https://github.com/theroyallab/tabbyAPI
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

## ⚙️ Backend-Wechsel & Settings

### Multi-Backend Support

AIfred unterstützt verschiedene LLM-Backends, die in der UI dynamisch gewechselt werden können:

- **Ollama**: GGUF-Modelle (Q4/Q8), einfachste Installation
- **vLLM**: AWQ-Modelle (4-bit), beste Performance mit AWQ Marlin Kernel
- **TabbyAPI**: EXL2-Modelle (ExLlamaV2/V3), experimentell

### GPU Compatibility Detection

AIfred erkennt automatisch beim Start deine GPU und warnt vor inkompatiblen Backend-Konfigurationen:

- **Tesla P40 / GTX 10 Series** (Pascal): Nutze Ollama (GGUF) - vLLM/AWQ wird nicht unterstützt
- **RTX 20+ Series** (Turing/Ampere/Ada): vLLM (AWQ) empfohlen für beste Performance

Detaillierte Informationen: [GPU_COMPATIBILITY.md](docs/GPU_COMPATIBILITY.md)

### Settings-Persistenz

Settings werden in `~/.config/aifred/settings.json` gespeichert:

**Per-Backend Modell-Speicherung:**
- Jedes Backend merkt sich seine zuletzt verwendeten Modelle
- Beim Backend-Wechsel werden automatisch die richtigen Modelle wiederhergestellt
- Beim ersten Start werden Defaults aus `aifred/lib/config.py` verwendet

**Beispiel Settings-Struktur:**
```json
{
  "backend_type": "vllm",
  "enable_thinking": true,
  "backend_models": {
    "ollama": {
      "selected_model": "qwen3:8b",
      "automatik_model": "qwen2.5:3b"
    },
    "vllm": {
      "selected_model": "Qwen/Qwen3-8B-AWQ",
      "automatik_model": "Qwen/Qwen3-4B-AWQ"
    }
  }
}
```

### Qwen3 Thinking Mode

**Chain-of-Thought Reasoning für komplexe Aufgaben:**

- **Thinking Mode ON**: Temperature 0.6, generiert `<think>...</think>` Blocks
- **Thinking Mode OFF**: Temperature 0.7, direkte Antworten ohne CoT
- Toggle erscheint nur bei Qwen3/QwQ-Modellen in der UI
- Funktioniert mit allen Backends (Ollama, vLLM, TabbyAPI)

**Empfohlene Modelle für Thinking Mode:**
- `qwen3:8b`, `qwen3:14b`, `qwen3:30b` (Ollama)
- `Qwen/Qwen3-8B-AWQ`, `Qwen/Qwen3-4B-AWQ` (vLLM)
- `qwq:32b` (dediziertes Reasoning-Modell, nur Ollama)

---

## 🏗️ Architektur

### Directory Structure
```
AIfred-Intelligence/
├── aifred/
│   ├── backends/          # LLM Backend Adapters
│   │   ├── base.py           # Abstract Base Class
│   │   ├── ollama.py         # Ollama Backend (GGUF)
│   │   ├── vllm.py           # vLLM Backend (AWQ)
│   │   └── tabbyapi.py       # TabbyAPI Backend (EXL2)
│   ├── components/        # Reflex UI Components
│   ├── lib/              # Core Libraries
│   │   ├── agent_core.py     # Haupt-Agent-Logik
│   │   ├── context_manager.py # History-Kompression
│   │   ├── config.py         # Default Settings
│   │   ├── settings.py       # Settings Persistence
│   │   └── vector_cache.py   # ChromaDB Vector Cache
│   └── state.py          # Reflex State Management
├── prompts/              # System Prompts
├── scripts/              # Utility Scripts
│   ├── download_all_models.sh     # Multi-Backend Model Downloader
│   ├── download_ollama_models.sh  # Ollama GGUF Models
│   ├── download_vllm_models.sh    # vLLM AWQ Models
│   ├── run_aifred.sh              # Development Runner
│   └── chroma_maintenance.py      # Vector Cache Maintenance
├── docs/                 # Documentation
│   ├── vllm/                      # vLLM-specific docs
│   └── GPU_COMPATIBILITY.md       # GPU compatibility matrix
└── CHANGELOG.md          # Project Changelog
```

### History Compression System

Bei 70% Context-Auslastung werden automatisch ältere Konversationen komprimiert:

- **Trigger**: 70% des Context Windows belegt
- **Kompression**: 3 Frage-Antwort-Paare → 1 Summary
- **Effizienz**: ~6:1 Kompressionsrate
- **FIFO**: Maximal 10 Summaries (älteste werden gelöscht)
- **Safety**: Mindestens 1 aktuelle Konversation bleibt sichtbar

### Vector Cache & RAG System

AIfred nutzt ein mehrstufiges Cache-System basierend auf **semantischer Ähnlichkeit** (Cosine Distance). **Neu in v1.3.0**: Pure Semantic Deduplication ohne Zeit-Abhängigkeit + intelligente Cache-Nutzung bei expliziten Recherche-Keywords.

#### Cache-Entscheidungs-Logik

**Phase 0: Explizite Recherche-Keywords** (NEW in v1.3.0)
```
User Query: "recherchiere Python" / "google Python" / "suche im internet Python"
└─ Explizites Keyword erkannt → Cache-Check ZUERST
   ├─ Distance < 0.05 (praktisch identisch)
   │  └─ ✅ Cache-Hit (0.15s statt 100s) - Zeigt Alter transparent an
   └─ Distance ≥ 0.05 (nicht identisch)
      └─ Neue Web-Recherche (User will neue Daten)
```

**Phase 1a: Direct Cache Hit Check**
```
User Query → ChromaDB Similarity Search
├─ Distance < 0.5 (HIGH Confidence)
│  └─ ✅ Use Cached Answer (sofort, keine Zeit-Checks mehr!)
├─ Distance 0.5-1.2 (MEDIUM Confidence) → Continue to Phase 1b (RAG)
└─ Distance > 1.2 (LOW Confidence) → Continue to Phase 2 (Research Decision)
```

**Phase 1b: RAG Context Check**
```
Cache Miss (d ≥ 0.5) → Query for RAG Candidates (0.5 ≤ d < 1.2)
├─ Found RAG Candidates?
│  ├─ YES → Automatik-LLM checks relevance for each candidate
│  │   ├─ Relevant (semantic match) → Inject as System Message Context
│  │   │   Example: "Python" → "FastAPI" ✅ (FastAPI is Python framework)
│  │   └─ Not Relevant → Skip
│  │       Example: "Python" → "Weather" ❌ (no connection)
│  └─ NO → Continue to Phase 2
└─ LLM Answer with RAG Context (Source: "Cache+LLM (RAG)")
```

**Phase 2: Research Decision**
```
No Direct Cache Hit & No RAG Context
└─ Automatik-LLM decides: Web Research needed?
   ├─ YES → Web Research + Cache Result
   └─ NO  → Pure LLM Answer (Source: "LLM-Trainingsdaten")
```

#### Semantic Deduplication (v1.3.0)

**Beim Speichern in Vector Cache:**
```
New Research Result → Check for Semantic Duplicates
└─ Distance < 0.3 (semantisch ähnlich)
   └─ ✅ IMMER Update (zeitunabhängig!)
      - Löscht alten Eintrag
      - Speichert neuen Eintrag
      - Garantiert: Neueste Daten werden verwendet
```

**Vorher (v1.2.0):**
- Zeit-basierte Logik: < 5min = Skip, ≥ 5min = Update
- Führte zu Race Conditions und Duplikaten

**Jetzt (v1.3.0):**
- Rein semantisch: Distance < 0.3 = IMMER Update
- Keine Zeit-Checks mehr → Konsistentes Verhalten

#### Cache Distance Thresholds

| Distance | Confidence | Behavior | Example |
|----------|-----------|----------|---------|
| `0.0 - 0.05` | EXACT | Explizite Recherche nutzt Cache | Identische Query |
| `0.05 - 0.5` | HIGH | Direct cache hit | "Python tutorial" vs "Python Anleitung" |
| `0.5 - 1.2` | MEDIUM | RAG candidate (relevance check via LLM) | "Python" vs "FastAPI" |
| `1.2+` | LOW | Cache miss → Research decision | "Python" vs "Weather" |

#### ChromaDB Maintenance Tool (v1.3.0)

**Neues Wartungstool** für Vector Cache:
```bash
# Stats anzeigen
python3 chroma_maintenance.py --stats

# Duplikate finden
python3 chroma_maintenance.py --find-duplicates

# Duplikate entfernen (Dry-Run)
python3 chroma_maintenance.py --remove-duplicates

# Duplikate entfernen (Execute)
python3 chroma_maintenance.py --remove-duplicates --execute

# Alte Einträge löschen (> 30 Tage)
python3 chroma_maintenance.py --remove-old 30 --execute
```

#### RAG (Retrieval-Augmented Generation) Mode

**How it works**:
1. Query finds related cache entries (distance 0.5-1.2)
2. Automatik-LLM checks if cached content is relevant to current question
3. Relevant entries are injected as system message: "Previous research shows..."
4. Main LLM combines cached context + training knowledge for enhanced answer

**Example Flow**:
```
User: "Was ist Python?" → Web Research → Cache Entry 1 (d=0.0)
User: "Was ist FastAPI?" → RAG finds Entry 1 (d=0.7)
  → LLM checks: "Python" relevant for "FastAPI"? YES (FastAPI uses Python)
  → Inject Entry 1 as context → Enhanced LLM answer
  → Source: "Cache+LLM (RAG)"
```

**Benefits**:
- Leverages related past research without exact cache hits
- Avoids false context (LLM filters irrelevant entries)
- Multi-level context awareness (cache + conversation history)

#### Configuration

Cache behavior in `aifred/lib/config.py`:

```python
# Cache Distance Thresholds
CACHE_DISTANCE_DUPLICATE = 0.5   # < 0.5 = potential cache hit
CACHE_DISTANCE_MEDIUM = 0.5      # 0.5-1.2 = RAG range
CACHE_DISTANCE_RAG = 1.2         # < 1.2 = similar enough for RAG context

# Time Thresholds
CACHE_TIME_THRESHOLD = 300       # 5 minutes (in seconds)
```

**RAG Relevance Check**: Uses Automatik-LLM with dedicated prompt (`prompts/de/rag_relevance_check.txt`)

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

# LLM Settings (Default Models)
DEFAULT_SETTINGS = {
    "model": "qwen3:30b-instruct",      # Haupt-LLM (Tesla P40 optimiert)
    "automatik_model": "qwen3:8b",      # Automatik-Entscheidungen
}

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