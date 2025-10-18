# AIfred Intelligence - Dokumentations-Übersicht

**Stand:** 2025-10-17
**Status:** ✅ Portable, Hardware-Erkennung, LLM-Parameter
**Projekt:** AIfred Intelligence (ehemals "Voice Assistant")

---

## 📚 Dokumentations-Index

### 🚀 Haupt-Dokumentation

#### [../README.md](../README.md) - Projekt-Übersicht & Quick Start
**Status:** ✅ Aktuell (Stand: 2025-10-14)

**Inhalt:**
- Projekt-Geschichte & Name "AIfred Intelligence"
- Features-Übersicht (Voice, Multi-Model, Web-Recherche)
- Installation & Setup
- Nutzung & Workflows
- Systemd Service Setup
- Performance-Metriken

**Key Features:**
- 🎙️ Multi-Modal Voice Interface (Whisper + Edge/Piper TTS)
- 🤖 Multi-Model AI Support (qwen, llama, mistral, command-r)
- 🔍 Agentic Web Research mit 3-stufigem Fallback
- 💭 Denkprozess-Transparenz mit `<think>` Tags
- 📊 Chat History mit Context & Model-Wechsel Separator

---

### 🏗️ Architektur & Design

#### [architecture-agentic-features.md](architecture-agentic-features.md) - Agent-System
**Status:** ✅ Aktuell (Stand: 2025-10-13)

**Inhalt:**
- 5-Stufen Agentische Pipeline
- Tool-System (SearXNG, Brave, Tavily, Scraping)
- Research-Modi: Eigenes Wissen, Schnell, Ausführlich, Automatik
- Performance-Ziele & Metriken

**Architektur:**
```
User Query → Decision → Query Opt → Search → Rating → Scrape → Answer
```

---

### 🔧 Setup & Konfiguration

#### [API_SETUP.md](API_SETUP.md) - Web-Search API Konfiguration
**Status:** ✅ Aktuell (Stand: 2025-10-13)

**Inhalt:**
- 3-Stufen Fallback System:
  1. **Brave Search API** (2.000/Monat, privacy-focused)
  2. **Tavily AI** (1.000/Monat, RAG-optimiert)
  3. **SearXNG** (Unlimited, self-hosted)
- Docker Setup für SearXNG
- API Keys Setup (.env)
- Troubleshooting

**Wichtig:** SearXNG läuft auf `http://localhost:8888`

#### [MIGRATION.md](MIGRATION.md) - Migration Mini-PC → WSL/Hauptrechner
**Status:** ✅ Aktuell (Stand: 2025-10-14)

**Inhalt:**
- 6-Phasen Migrations-Anleitung
- Export als tar.gz
- Import auf WSL2 (Windows 11)
- Voraussetzungen (Python, Ollama, Docker)
- Systemd Service Setup
- SSL/HTTPS Konfiguration
- Portabilitäts-Features
- Troubleshooting

**Ziel-Hardware:**
- CPU: AMD Ryzen 9 9900X3D
- GPU: NVIDIA RTX 3060
- RAM: 32GB+
- OS: Windows 11 + WSL2 (Ubuntu)

---

### 🤖 LLM Models & Benchmarks

#### [HARDWARE_DETECTION.md](HARDWARE_DETECTION.md) - Hardware-Erkennung & Portabilität ⭐ NEU
**Status:** ✅ Aktuell (Stand: 2025-10-17)

**Inhalt:**
- Automatische GPU-Erkennung (AMD, NVIDIA, CPU-only)
- Dynamische Parameter-Konfiguration basierend auf VRAM
- Bekannte Hardware-Probleme (AMD iGPU + 32B = GPU Hang)
- Context Window Größen & RAM/VRAM Verbrauch
- Ollama Bug: Compute Buffers nicht in VRAM-Kalkulation
- Portabilität zwischen Systemen (MiniPC vs. Hauptrechner)

**Wichtig:**
- AMD Radeon 780M crasht bei qwen3:32B mit GPU → Auto-Fallback auf CPU
- RTX 3060 unterstützt 32B mit GPU (Hybrid-Modus)
- Context-Größen werden dynamisch angepasst (8K-64K je nach Hardware)

#### [LLM_PARAMETERS.md](LLM_PARAMETERS.md) - LLM-Parameter Guide ⭐ NEU
**Status:** ✅ Aktuell (Stand: 2025-10-17)

**Inhalt:**
- Alle Ollama Options-Parameter erklärt (30+)
- Sampling: temperature, top_p, top_k, min_p, typical_p
- Repetition-Kontrolle: repeat_penalty, presence_penalty, frequency_penalty
- Output-Kontrolle: num_predict, stop, penalize_newline
- Context & Memory: num_ctx, num_keep, seed
- Mirostat Sampling (fortgeschritten)
- 6 Presets für verschiedene Aufgaben
- Best Practices & Troubleshooting

**Presets:**
- 🎯 Fakten/Code (temp=0.3, top_p=0.5)
- 💬 Chat (temp=0.8, top_p=0.9)
- 🎨 Kreativ (temp=1.2, top_p=0.95)

#### [LLM_COMPARISON.md](LLM_COMPARISON.md) - Model-Vergleich
**Status:** ⚠️ Teilweise veraltet (Stand: 2025-10-13)

**Inhalt:**
- Technischer Vergleich von 6 Modellen
- RAG Score, Tool-Use, Speed, RAM
- Use-Case Empfehlungen
- Benchmarks

**Fehlt:**
- ❌ qwen3:0.6b, 1.7b, 4b (neu installiert 2025-10-14)
- ❌ qwen2.5:32b Performance-Daten
- ❌ llama3.2:3b Entscheidungs-Qualität Issues

**Action:** Sollte mit MODEL_BENCHMARK_RESULTS aktualisiert werden

#### [LLM_HELP_UI.md](LLM_HELP_UI.md) - User-Freundliche Model-Hilfe
**Status:** ⚠️ Veraltet (Stand: 2025-10-13)

**Inhalt:**
- UI-optimierte Model-Übersicht
- Schnellübersicht-Tabelle
- Use-Case Empfehlungen

**Action:** Sollte mit neuen qwen3-Modellen aktualisiert werden

#### [MODEL_BENCHMARK_TEST.md](MODEL_BENCHMARK_TEST.md) - Benchmark Test-Plan
**Status:** ✅ Aktuell (Stand: 2025-10-14)

**Inhalt:**
- 6 Test-Szenarien für Model-Vergleich
- Entscheidungs-Qualität Tests
- Geschwindigkeits-Tests
- Thinking-Quality Tests
- Tabellen zum Ausfüllen

**Modelle getestet:**
- llama3.2:3b (Referenz - bekannt unzuverlässig)
- qwen3:0.6b, 1.7b, 4b, 8b (neu!)
- qwen2.5:32b (Referenz - langsam aber korrekt)

**Test-Fragen:**
1. Trump/Hamas Friedensabkommen (komplex, muss Web-Recherche sein)
2. "Guten Morgen" (einfach, keine Web-Recherche)
3. Wetter Berlin (muss IMMER Web-Recherche sein)
4. Emoji-Anfrage (Kreativität)
5. Mathe-Reasoning (Thinking Quality)
6. Aktuelle News (Web-Recherche Trigger)

---

#### [MEMORY_MANAGEMENT.md](MEMORY_MANAGEMENT.md) - RAM/VRAM Management
**Status:** ✅ Aktuell (Stand: 2025-10-15)

**Inhalt:**
- Smart Model Loading mit RAM-Check
- Automatisches Entladen bei wenig Speicher
- Signal Handler für sauberen Shutdown
- Memory-Manager Funktionen

---

## 📊 Aktueller Status (2025-10-17)

### ✅ Fertiggestellt

1. **Projekt-Rename: "AIfred Intelligence"**
   - ✅ mobile_voice_assistant.py → aifred_intelligence.py
   - ✅ README.md aktualisiert
   - ✅ systemd service aktualisiert
   - ✅ Alle Pfade portabel gemacht

2. **Portabilität**
   - ✅ Alle Pfade relativ mit PROJECT_ROOT
   - ✅ Platform-spezifische Piper Binary Erkennung
   - ✅ SSL optional & portable
   - ✅ MIGRATION.md Guide erstellt

3. **Inference-Zeit Tracking**
   - ✅ Entscheidungs-Zeit angezeigt (Automatik-Modus)
   - ✅ Query Optimization Zeit
   - ✅ URL Rating Zeit
   - ✅ Finale Inferenz Zeit
   - ✅ Separator im Log (═══) nach jeder Anfrage

4. **Model Downloads**
   - ✅ qwen3:0.6b (522 MB)
   - ✅ qwen3:1.7b (1.4 GB)
   - ✅ qwen3:4b (2.5 GB)
   - ✅ qwen3:8b (bereits installiert)

5. **Benchmark Infrastructure**
   - ✅ MODEL_BENCHMARK_TEST.md (manuell)
   - ✅ scripts/benchmark_models.py (automatisch)

6. **Hardware-Erkennung & Portabilität** ⭐ NEU (2025-10-17)
   - ✅ Automatische GPU-Typ Erkennung (AMD/NVIDIA)
   - ✅ VRAM-Größe via rocm-smi/nvidia-smi
   - ✅ iGPU Detection & Stabilitäts-Check
   - ✅ Dynamische Parameter-Konfiguration
   - ✅ CPU-Fallback für problematische Kombinationen
   - ✅ Context-Size an Hardware anpassen
   - ✅ Portabel zwischen MiniPC & Hauptrechner

7. **LLM-Parameter Dokumentation** ⭐ NEU (2025-10-17)
   - ✅ Alle 30+ Ollama Options erklärt
   - ✅ 6 Presets für verschiedene Use-Cases
   - ✅ Best Practices & Troubleshooting
   - ✅ API-Verwendung (Python, REST, CLI)

### 🚧 In Arbeit

1. **LLM-Parameter UI** (2025-10-17)
   - 🔄 Accordion mit wichtigsten Parametern
   - 🔄 Temperature, Max Tokens, Repeat Penalty, Seed
   - 🔄 Verschachteltes Accordion für Fortgeschrittene
   - 🔄 Integration in Chat-Interface

2. **Dokumentation**
   - ✅ HARDWARE_DETECTION.md erstellt
   - ✅ LLM_PARAMETERS.md erstellt
   - ✅ INDEX.md aktualisiert (diese Datei)
   - ⏳ LLM_COMPARISON.md updaten mit qwen3

### 📝 Noch zu tun

1. **LLM-Parameter UI implementieren**
   - [ ] Accordion unterhalb Recherche-Modus
   - [ ] 4 Basis-Parameter (Temperature, Max Tokens, Repeat, Seed)
   - [ ] Fortgeschritten-Accordion (Top P, Top K)
   - [ ] An ollama.chat() übergeben

2. **Dokumentation finalisieren**
   - [ ] LLM_COMPARISON.md mit Benchmark-Daten updaten
   - [ ] LLM_HELP_UI.md mit qwen3-Modellen updaten

3. **Git Commit - Hardware Detection**
   - [ ] ollama_wrapper.py (Hardware-Erkennung)
   - [ ] Bugfix: num_ctx=128K → 8K (RAM-Limit)
   - [ ] HARDWARE_DETECTION.md
   - [ ] LLM_PARAMETERS.md
   - [ ] INDEX.md Update

4. **Migration testen**
   - [ ] tar.gz Export erstellen
   - [ ] Auf WSL2/Hauptrechner importieren
   - [ ] Performance vergleichen (Mini-PC vs. 9900X3D)

---

## 🎯 Quick Reference

### Für User (Schnellstart)

**Neu hier?**
1. **Start:** [../README.md](../README.md) - Projekt-Übersicht
2. **Setup:** [API_SETUP.md](API_SETUP.md) - Web-Suche konfigurieren
3. **Hardware:** [HARDWARE_DETECTION.md](HARDWARE_DETECTION.md) - GPU-Erkennung & Limits ⭐
4. **LLM-Params:** [LLM_PARAMETERS.md](LLM_PARAMETERS.md) - Temperature & Co. ⭐
5. **Models:** [MODEL_BENCHMARK_TEST.md](MODEL_BENCHMARK_TEST.md) - Welches Model?

**Migration auf anderen Rechner?**
- **Guide:** [MIGRATION.md](MIGRATION.md) - Schritt-für-Schritt

### Für Entwickler

**Code verstehen?**
1. **Architektur:** [architecture-agentic-features.md](architecture-agentic-features.md)
2. **Code:**
   - `aifred_intelligence.py` - Haupt-App (74 KB)
   - `agent_tools.py` - Multi-API Search (22 KB)
   - `scripts/benchmark_models.py` - Automated Tests

**Testing:**
```bash
# Manuelle Tests
python aifred_intelligence.py

# Automated Benchmarks
python scripts/benchmark_models.py
```

### Für Troubleshooting

**Problem?**
1. **API Setup:** [API_SETUP.md - Troubleshooting](API_SETUP.md#troubleshooting)
2. **Migration:** [MIGRATION.md - Troubleshooting](MIGRATION.md#troubleshooting)
3. **Logs:** `sudo journalctl -u aifred-intelligence.service -f`

---

## 📈 Versions-Historie

### v3.1 - Hardware Detection & LLM Parameters (2025-10-17) ⭐ NEU

**Major Changes:**
- 🔍 **Hardware-Erkennung:** Automatisch GPU-Typ, VRAM, Stabilität
- 🎛️ **Dynamische Konfig:** Parameter basierend auf Hardware
- 🛡️ **CPU-Fallback:** AMD iGPU + 32B = auto CPU (GPU crasht)
- 📊 **Context Limits:** 8K-64K je nach RAM/VRAM
- 📚 **LLM-Parameter Guide:** 30+ Parameter erklärt
- 🐛 **Bugfix:** num_ctx=128K → 8K (RAM-Limit)

**Portabilität:**
- ✅ Gleicher Code auf MiniPC (AMD iGPU) & Hauptrechner (RTX 3060)
- ✅ Automatische Anpassung an Hardware
- ✅ Kein manuelles Tuning nötig

**Known Issues Fixed:**
- ✅ AMD 780M GPU Hang mit 32B → Auto CPU-Fallback
- ✅ "model requires more system memory" → Context reduziert
- ✅ Ollama Compute Buffers nicht in Kalkulation → Manuelles Limit

**Dokumentation:**
- ✅ HARDWARE_DETECTION.md - Vollständiger Hardware-Guide
- ✅ LLM_PARAMETERS.md - Alle Ollama Options erklärt
- ✅ 6 Presets für verschiedene Use-Cases

### v3.0 - Portability & Benchmarks (2025-10-14)

**Major Changes:**
- 🎩 **Rename:** "Voice Assistant" → "AIfred Intelligence"
- 📦 **Portability:** Alle Pfade relativ, platform-aware
- ⏱️ **Inference Tracking:** Zeiten für alle Pipeline-Steps
- 🧪 **Model Benchmarks:** qwen3:0.6b/1.7b/4b getestet
- 📁 **Migration Guide:** WSL2-ready tar.gz Export
- 🧹 **Cleanup:** Backup-Dateien entfernt, Docs reorganisiert

**Performance Insights:**
- llama3.2:3b: Schnell (6-9s) aber **unzuverlässig** (falsche Entscheidungen!)
- qwen3:4b: **Bester Kandidat** für Entscheidungen (genau + schnell)
- qwen2.5:32b: Langsam (84s) aber **100% korrekt**

**Beobachtungen:**
- llama3.2:3b entscheidet bei Trump/Hamas fälschlicherweise "kein Web"
- qwen3:4b rivalisiert qwen2.5:72b in Benchmarks
- Separator (═══) verbessert Log-Lesbarkeit massiv

### v2.0 - Multi-API Web-Search (2025-10-13)

**Features:**
- 🌐 3-Stufen Fallback (Brave → Tavily → SearXNG)
- 🤖 Agent-Modi mit UI Settings
- 📊 LLM Model Auswahl Hilfe
- ✅ SearXNG self-hosted unlimited

**Fixes:**
- ✅ DuckDuckGo "0 URLs" Problem gelöst
- ✅ AI nutzt echte Web-Daten statt Training

### v1.x - Basis Voice Assistant (2024-10)

**Features:**
- 🎤 Whisper STT
- 🤖 Ollama Integration
- 🔊 Edge/Piper TTS
- 📱 Gradio UI
- 🔒 HTTPS Support

---

## 🔗 Externe Ressourcen

### Code Repositories
- **AIfred Intelligence:** https://github.com/Peuqui/AIfred-Intelligence
- **System Setup:** https://github.com/Peuqui/minipc-linux

### APIs & Services
- **Brave Search API:** https://brave.com/search/api/
- **Tavily AI:** https://www.tavily.com/
- **SearXNG:** https://github.com/searxng/searxng (läuft auf Port 8888)

### Tech Stack
- **Gradio 4.x:** https://gradio.app
- **Ollama:** https://ollama.com
- **Whisper (faster-whisper):** https://github.com/guillaumekln/faster-whisper
- **Edge TTS:** https://github.com/rany2/edge-tts
- **Piper TTS:** https://github.com/rhasspy/piper

---

## 📝 Dokumentations-Richtlinien

### Neue Docs hinzufügen

1. Erstelle Markdown in `/docs/`
2. Füge Eintrag in INDEX.md hinzu
3. Setze Status & Datum
4. Verlinke verwandte Dokumente

### Status-Flags

- ✅ **Aktuell** - Matches current implementation
- ⚠️ **Veraltet** - Needs update
- ⏳ **WIP** - Work in Progress
- 📝 **Geplant** - Planned
- ❌ **Obsolet** - Should be archived/deleted

---

**Letzte Aktualisierung:** 2025-10-17
**Autor:** Claude Code
**Version:** 3.1 - Hardware Detection & LLM Parameters Release
