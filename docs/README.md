# AI Voice Assistant - Dokumentation

**Gradio Voice Interface mit Whisper + Ollama + Edge-TTS + Multi-API Web-Search**

**Version:** 2.0 - Multi-API Web-Search Release
**Stand:** 13. Oktober 2025

---

## 📚 Dokumentation

### 📖 Start hier:
- **[INDEX.md](INDEX.md)** - 🆕 Vollständige Dokumentations-Übersicht mit Implementierungs-Status

### Voice Assistant spezifisch:
- **[voice-assistant-complete-guide.md](voice-assistant-complete-guide.md)** - Vollständige Anleitung für Setup, Konfiguration und Entwicklung
- **[API_SETUP.md](API_SETUP.md)** - 🆕 Multi-API Web-Search Setup (Brave, Tavily, Serper, SearXNG)
- **[LLM_COMPARISON.md](LLM_COMPARISON.md)** - 🆕 Technischer Modell-Vergleich (qwen2.5:14b, qwen3:8b, command-r, etc.)
- **[LLM_HELP_UI.md](LLM_HELP_UI.md)** - 🆕 User-freundliche Model-Auswahl Hilfe

### Architektur & Design:
- **[architecture-agentic-features.md](architecture-agentic-features.md)** - Agent-System Architektur & Pipeline Design
- **[IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)** - 🆕 Status der Multi-API Implementation

### System-Setup & Integration:
Für die vollständige MiniPC-Setup-Dokumentation (Ubuntu 24.04, Docker, Services, Desktop-Konfiguration, etc.) siehe:

📦 **[MiniPC-Linux-Setup Repository](https://github.com/Peuqui/minipc-linux)**

Dort findest du:
- Komplettes Ubuntu 24.04 LTS Setup
- Docker & Systemd Service-Konfiguration
- Desktop & Browser-Setup (LightDM, GNOME, XFCE)
- Wake-on-LAN, Monitoring (Grafana), VPN, etc.
- Voice Assistant als Teil des Gesamtsystems

---

## 🚀 Quick Start

### Voraussetzungen:
- Ubuntu 24.04 LTS
- Python 3.12+
- Ollama installiert
- SSL-Zertifikat für HTTPS (für Mikrofonzugriff im Browser)

### Installation:

```bash
# 1. Python Virtual Environment
python3 -m venv ~/ai_env
source ~/ai_env/bin/activate

# 2. Dependencies
pip install gradio faster-whisper ollama edge-tts beautifulsoup4 lxml requests

# 3. Ollama Modelle (empfohlen für Web-Recherche)
ollama pull qwen2.5:14b  # Beste Wahl für RAG/Agentic (9 GB)
ollama pull qwen3:8b     # Schneller, immer noch gut (5.2 GB)
ollama pull llama3.1:8b  # Zuverlässig, bewährt (4.9 GB)

# 4. SearXNG Docker Setup (für Web-Recherche)
cd /home/mp/MiniPCLinux/docker/searxng
docker compose up -d

# 5. Voice Assistant starten
python mobile_voice_assistant.py
```

**Zugriff:** https://narnia.spdns.de:8443 (oder https://localhost:8443)

**Optional:** API Keys für mehr Performance (siehe [API_SETUP.md](API_SETUP.md))

---

## 📖 Weitere Informationen

**Vollständige Anleitung:** [voice-assistant-complete-guide.md](voice-assistant-complete-guide.md)

**System-Integration:** Siehe [MiniPC-Linux-Setup Repo](https://github.com/Peuqui/minipc-linux) für:
- Systemd Service Setup
- SSL-Zertifikat-Konfiguration
- Automatischer Start beim Boot
- Firewall-Regeln
- Monitoring & Logs

---

## 🎯 Features

### Basis Features:
- 🎤 **Audio-Aufnahme** direkt im Browser (Gradio Audio Widget)
- 🗣️ **Whisper Transcription** (faster-whisper, offline, 5 Modelle wählbar)
- 🤖 **Ollama Integration** (lokale KI-Modelle: qwen2.5:14b, qwen3:8b, llama3.1:8b, command-r, etc.)
- 🔊 **Dual-TTS** (Edge-TTS + Piper-TTS, umschaltbar)
- 🔒 **HTTPS** (erforderlich für Mikrofonzugriff)
- 📱 **Mobile-optimiert** (responsive Gradio UI)
- 💾 **Persistent Settings** (Model, TTS, Whisper Auswahl gespeichert)

### 🆕 Agentische Features (v2.0):
- 🌐 **Multi-API Web-Search** - 4-stufiges Fallback-System
  - Brave Search API (2.000/Monat)
  - Tavily AI (1.000/Monat)
  - Serper.dev (2.500 einmalig)
  - SearXNG Self-Hosted (unlimited) ✅
- 🔍 **Echtzeit Web-Recherche** - Aktuelle News, Events, Fakten
- 🤖 **Agent-Modi** - Eigenes Wissen, Schnell, Ausführlich, Interaktiv
- 📊 **Context Building** - Web-Scraping + Multi-Source Aggregation
- 🎚️ **Wählbare Recherche-Tiefe** - User-kontrolliert via Settings
- 🔒 **Privacy-First** - SearXNG als privacy-freundliche Meta-Suchmaschine

---

## 🛠️ Entwicklung

```bash
# Environment aktivieren
source ~/ai_env/bin/activate

# Dependencies aktualisieren
pip install --upgrade gradio faster-whisper ollama edge-tts beautifulsoup4 lxml requests

# Agent Tools testen
python -c "from agent_tools import search_web; print(search_web('test'))"

# Testen
python mobile_voice_assistant.py
```

### 🔍 Agent-System testen:

```bash
# Test SearXNG (sollte 10 URLs zurückgeben)
curl "http://localhost:8888/search?q=test&format=json" | jq '.results | length'

# Test Multi-API Search
python -c "
from agent_tools import search_web
result = search_web('neueste Nachrichten Donald Trump')
print(f'Success: {result[\"success\"]}')
print(f'Source: {result.get(\"source\")}')
print(f'URLs: {len(result.get(\"related_urls\", []))}')
"

# Logs prüfen
sudo journalctl -u voice-assistant -f | grep -E "(SearXNG|Brave|Tavily|Serper|Agent)"
```

---

## 🔗 Links

- **Code Repository:** https://github.com/Peuqui/AI-Voice-Assistant
- **System Setup:** https://github.com/Peuqui/minipc-linux
- **Ollama:** https://ollama.com
- **Gradio:** https://gradio.app
- **Whisper:** https://github.com/openai/whisper

---

## 📈 Was ist neu in v2.0? (Oktober 2025)

### Hauptfeatures:
- ✅ **Multi-API Web-Search System** - 4-stufiges Fallback (Brave, Tavily, Serper, SearXNG)
- ✅ **SearXNG Self-Hosted** - Unlimited queries, privacy-first
- ✅ **Agent-Modi** - User-kontrolliert (Eigenes Wissen, Schnell, Ausführlich)
- ✅ **LLM Model-Auswahl Hilfe** - In-UI Collapsible mit Empfehlungen
- ✅ **Neue AI Modelle** - qwen2.5:14b, qwen3:8b, llama3.1:8b, command-r

### Behobene Probleme:
- ✅ AI sagt nicht mehr "Ich habe keinen Internet-Zugang"
- ✅ AI nutzt nicht mehr veraltete Training Data (2022) für aktuelle Fragen
- ✅ DuckDuckGo "0 URLs" Problem gelöst
- ✅ Robuste Fallback-Mechanismen bei API Rate Limits

### Dokumentation:
- ✅ Vollständige API Setup Guides
- ✅ LLM Vergleichs-Tabellen
- ✅ Architektur-Dokumentation
- ✅ Troubleshooting Guides

**Siehe:** [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md) für Details

---

*Stand: 13. Oktober 2025*
*Version: 2.0 - Multi-API Web-Search Release*
*Erstellt mit Claude Code*
