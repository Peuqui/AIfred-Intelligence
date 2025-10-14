# 🎩 AIfred Intelligence

**AI at your service** • *Persönlicher Voice Assistant mit Multi-Model Support und Web-Recherche*

---

## 📖 Die Geschichte hinter dem Namen

**AIfred Intelligence** ist mehr als nur ein cleverer Wortspiel (A.I. = AIfred Intelligence = Artificial Intelligence).

Der Name ehrt **drei Generationen**:

1. **Alfred** - Mein Großvater
2. **Wolfgang Alfred** - Mein Vater
3. **Ich** - (werde selbst Großvater meines Sohnes sein)

Wie der legendäre Butler Alfred aus Batman, der immer loyal, intelligent und hilfsbereit an der Seite steht, soll auch dieser AI-Assistent ein zuverlässiger Begleiter sein.

*"AIfred Intelligence - AI at your service"* 🎩

---

## ✨ Features

### 🎙️ **Multi-Modal Voice Interface**
- **Spracheingabe** mit Whisper (faster-whisper)
- **Sprachausgabe** mit Edge TTS (Cloud) oder Piper TTS (lokal)
- **Text-Alternative** für schnelle Eingaben
- **STT-Korrektur**: Optional Transkription vor dem Senden bearbeiten

### 🤖 **Multi-Model AI Support (Ollama)**
- **qwen2.5:14b** - Beste RAG-Performance (100% Recherche, 0% Training)
- **qwen3:8b** - Balance zwischen Speed und Qualität
- **command-r** - Enterprise RAG für lange Dokumente
- **mixtral:8x7b** - Mixture-of-Experts (47B params, 8 Experten)
- **llama3.1:8b** / **llama3.2:3b** - Schnelle Allzweck-Modelle
- **mistral** - Optimiert für Code und Instruktionen

### 🔍 **Agentic Web Research (Multi-API)**
Intelligente 3-Stufen Web-Suche mit automatischem Fallback:

1. **Brave Search API** (Primary) - 2.000 Requests/Monat, privacy-focused
2. **Tavily AI** (Fallback) - 1.000 Requests/Monat, RAG-optimiert
3. **SearXNG** (Last Resort) - Unlimited, self-hosted

**4 Research-Modi:**
- 🧠 **Eigenes Wissen** - Schnell, offline, nur AI-Training
- ⚡ **Web-Suche Schnell** - 1 beste Quelle gescraped
- 🔍 **Web-Suche Ausführlich** - 3 beste Quellen gescraped
- 🤖 **Automatik** - KI entscheidet intelligent, ob Web-Recherche nötig ist

**AI-basierte URL-Bewertung:**
- AI bewertet alle gefundenen URLs (Score 1-10)
- Nur URLs mit Score ≥ 6 werden gescraped
- Intelligente Auswahl der relevantesten Quellen

### 💭 **Denkprozess-Transparenz**
- `<think>` Tags werden automatisch erkannt
- Als **Collapsible Accordion** im Chat anzeigbar (weiß auf anthrazit)
- Kompakte Darstellung ohne überflüssige Leerzeilen
- **Nicht in TTS** - Denkprozess wird nur angezeigt, nicht vorgelesen
- Zeigt AI's Reasoning-Prozess (perfekt für Debugging und Lernen!)

### 📊 **Chat History mit Context**
- Vollständiger Konversationsverlauf
- Timing-Informationen (STT, Agent, Inferenz, TTS)
- **Model-Wechsel Separator**: Zeigt an, wann KI-Modell gewechselt wurde
- Quellen-URLs immer sichtbar

### ⚙️ **Umfangreiche Einstellungen**
- **AI-Model Wechsel** on-the-fly
- **Stimmen-Auswahl** (Edge TTS: 10+ deutsche Stimmen)
- **TTS-Engine Toggle** (Edge Cloud vs. Piper Lokal)
- **TTS-Optimierung**: Emojis und `<think>` Tags werden automatisch aus Sprachausgabe entfernt
- **Geschwindigkeit** für TTS-Generierung
- **Whisper-Model Wahl** (tiny → large-v3)
- **Research-Mode** direkt bei Texteingabe
- **Input-Sperre**: Alle Eingaben deaktiviert während Verarbeitung läuft

---

## 🚀 Installation

### 1. **Voraussetzungen**
```bash
# Python 3.10+
python3 --version

# Ollama installieren
curl -fsSL https://ollama.com/install.sh | sh

# AI-Modelle herunterladen (z.B.)
ollama pull qwen2.5:14b
ollama pull llama3.2:3b
```

### 2. **Repository klonen**
```bash
git clone https://github.com/Peuqui/AIfred-Intelligence.git
cd AIfred-Intelligence
```

### 3. **Virtual Environment & Dependencies**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. **API Keys konfigurieren (Optional)**
```bash
# Kopiere .env.example zu .env
cp .env.example .env

# Editiere .env und füge API Keys ein:
# - Brave Search API: https://brave.com/search/api/
# - Tavily AI: https://tavily.com/
nano .env
```

**Hinweis:** Ohne API Keys läuft automatisch **SearXNG** als Fallback!

### 5. **SearXNG starten (Self-Hosted Search)**
```bash
cd docker/searxng
docker compose up -d
```

SearXNG läuft nun auf `http://localhost:8888`

### 6. **Voice Assistant starten**
```bash
cd /home/mp/Projekte/AIfred-Intelligence
source venv/bin/activate
python mobile_voice_assistant.py
```

Öffne Browser: `https://localhost:7860` (oder LAN-IP für mobile Geräte)

---

## 📁 Projekt-Struktur

```
AIfred-Intelligence/
├── mobile_voice_assistant.py    # Haupt-App (Gradio UI + Logic)
├── agent_tools.py                # Agent-System (Multi-API Search, Scraping)
├── requirements.txt              # Python Dependencies
├── .env.example                  # API Keys Template
├── .env                          # Deine API Keys (nicht in Git!)
├── settings.json                 # User Settings (Auto-generiert)
├── docker/
│   └── searxng/
│       ├── compose.yml           # SearXNG Docker Setup
│       └── settings.yml          # SearXNG Config (German)
└── docs/
    ├── LLM_COMPARISON.md         # Detaillierte Model-Vergleiche
    └── architecture-agentic-features.md
```

---

## 🎯 Nutzung

### Typischer Workflow:

1. **Aufnehmen**: Klicke auf Mikrofon → Sprich deine Frage → Stopp
2. **Auto-Transkription**: Text wird automatisch nach Stopp transkribiert
3. **Optional**: Mit "✏️ Text nach Transkription zeigen" kannst du vorher korrigieren
4. **AI antwortet**: Automatisch mit Sprachausgabe (falls aktiviert)
5. **Warten**: Alle Eingaben sind gesperrt bis AI komplett fertig ist (inkl. TTS)

**Wichtig**: Während die KI arbeitet, sind alle Eingabemöglichkeiten deaktiviert. So vermeidest du versehentliche Mehrfach-Anfragen in der Queue!

### Research-Modi wählen:

- **Schnelle Fragen** (z.B. "Was ist Photosynthese?"): 🧠 **Eigenes Wissen**
- **Aktuelle News** (z.B. "Neueste Trump News"): ⚡ **Web-Suche Schnell**
- **Tiefe Recherche** (z.B. "Vergleiche React vs. Vue 2024"): 🔍 **Web-Suche Ausführlich**
- **Automatische Entscheidung**: 🤖 **Automatik** - KI analysiert die Frage und entscheidet selbst, ob Web-Recherche benötigt wird

### AI-Model wechseln:

- **Schnell & Allgemein**: llama3.2:3b, llama3.1:8b
- **Web-Recherche**: qwen2.5:14b (beste RAG-Performance!)
- **Code schreiben**: mistral, mixtral:8x7b
- **Komplexe Tasks**: command-r, mixtral:8x7b

---

## 🛠️ Systemd Service (Optional)

Für Autostart beim Booten:

```bash
sudo systemctl enable voice-assistant.service
sudo systemctl start voice-assistant.service
sudo systemctl status voice-assistant.service
```

Service-Datei: `/etc/systemd/system/voice-assistant.service`

---

## 🔧 Technologie-Stack

- **Frontend**: Gradio 4.x (Python Web UI Framework)
- **AI Models**: Ollama (llama3, qwen, mistral, mixtral, command-r)
- **Speech-to-Text**: faster-whisper (OpenAI Whisper optimiert)
- **Text-to-Speech**:
  - Edge TTS (Microsoft Cloud, beste Qualität)
  - Piper TTS (lokal, Thorsten Stimme)
- **Web Search APIs**:
  - Brave Search API (Primary)
  - Tavily AI (Fallback)
  - SearXNG (Self-hosted, Last Resort)
- **Web Scraping**: BeautifulSoup4, Requests
- **Container**: Docker (SearXNG)

---

## 📊 Performance

### Typische Antwortzeiten:

**Eigenes Wissen (kein Agent):**
- STT: ~1s (base model)
- AI Inferenz: ~5-10s (llama3.2:3b) bis ~20-30s (qwen2.5:14b)
- TTS: ~2-3s (Edge TTS)
- **Total**: ~10-40s

**Web-Recherche Schnell (1 Quelle):**
- STT: ~1s
- Agent: ~15-30s (Search + Scraping + URL-Rating)
- AI Inferenz: ~20-40s (mit Context)
- TTS: ~2-3s
- **Total**: ~40-75s

**Web-Recherche Ausführlich (3 Quellen):**
- STT: ~1s
- Agent: ~60-120s (3x Scraping + Rating)
- AI Inferenz: ~30-60s (mit großem Context)
- TTS: ~2-3s
- **Total**: ~95-185s

---

## 🐛 Bekannte Einschränkungen

- **Model-Separator** erscheint nur bei tatsächlichem Model-Wechsel mit History
- **llama2:13b** hat nur ~78% RAG-Adhärenz (mischt Training Data)
- **llama3.2:3b** ignoriert RAG fast komplett (nicht für Web-Recherche!)

---

## 🤝 Beitragen

Falls du Verbesserungen hast:
1. Fork das Repository
2. Erstelle einen Feature Branch
3. Commit deine Änderungen
4. Öffne einen Pull Request

---

## 📜 Lizenz

MIT License - siehe [LICENSE](LICENSE) Datei.

---

## 🙏 Danksagungen

- **Meta** für Llama Models
- **Alibaba Cloud** für Qwen Models
- **Mistral AI** für Mistral & Mixtral
- **OpenAI** für Whisper
- **Microsoft** für Edge TTS
- **SearXNG Community** für Privacy-Friendly Meta-Search
- **Thorsten Müller** für deutsche Piper TTS Stimme

---

**AIfred Intelligence** - *AI at your service* 🎩

Benannt nach **Alfred** (Großvater) und **Wolfgang Alfred** (Vater)
