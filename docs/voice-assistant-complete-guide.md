# AI Voice Assistant - Complete User Guide

**Version:** 2.0 - Multi-API Web-Search Release
**Stand:** 13. Oktober 2025

Complete guide for the AI Voice Assistant running on the AOOSTAR GEM10 Mini PC.

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Access](#access)
- [Usage Guide](#usage-guide)
- [Agent-Modi & Web-Recherche](#agent-modi--web-recherche) 🆕
- [Settings & Configuration](#settings--configuration)
- [LLM Model-Auswahl](#llm-model-auswahl) 🆕
- [TTS Engines](#tts-engines)
- [Troubleshooting](#troubleshooting)
- [Technical Details](#technical-details)

---

## Overview

The AI Voice Assistant is a web-based application that combines speech recognition, AI chat, and text-to-speech synthesis. It runs 24/7 as a systemd service and is accessible via HTTPS.

**System:** AOOSTAR GEM10 Mini PC (Ubuntu 24.04 LTS)
**Access URL:** https://narnia.spdns.de:8443
**Service:** `voice-assistant.service`

---

## Features

### ✅ Core Features
- 🎙️ **Voice Input** - Record audio directly in the browser
- 🗣️ **Speech Recognition** - Whisper (faster-whisper) transcription
- 🤖 **AI Chat** - Multiple Ollama models (llama3.2:3b, mistral, etc.)
- 🔊 **Text-to-Speech** - Edge TTS (cloud) or Piper TTS (local)
- 💬 **Chat History** - Conversation context preserved
- 📝 **Text Input** - Alternative to voice input

### ✅ Advanced Features
- 🔄 **Regenerate Audio** - Re-generate TTS with different settings without new question
- ⚡ **Speed Control** - Adjust TTS generation speed (1.0x - 2.0x)
- 🎭 **Multiple Voices** - 4 Edge TTS voices (German/English)
- 🌓 **Dark Mode** - Automatic based on system settings
- 🔒 **HTTPS/SSL** - Secure connection via Let's Encrypt
- 📱 **Mobile Friendly** - Responsive design for phones/tablets
- 💾 **Persistent Settings** - Model, TTS, Whisper Auswahl gespeichert
- 🎙️ **5 Whisper Models** - base, small, medium, large, turbo (wählbar)

### ✅ UI/UX Features
- 🎯 **Smart Button States** - Buttons disabled when not applicable
- 🚫 **Recording Protection** - Cannot click "Verwerfen" during recording
- 🔄 **App Load Reset** - Clean state on every page load
- 🎨 **Dynamic UI** - Voice selection appears/disappears based on TTS engine
- ℹ️ **LLM-Hilfe** - Collapsible mit Model-Empfehlungen

### 🆕 Agentische Features (v2.0)
- 🌐 **Multi-API Web-Search** - 4-stufiges Fallback-System
  - Brave Search API (2.000 Queries/Monat)
  - Tavily AI (1.000 Queries/Monat)
  - Serper.dev (2.500 Queries einmalig)
  - SearXNG Self-Hosted (unlimited) ✅
- 🔍 **Echtzeit Web-Recherche** - Aktuelle News, Events, Fakten
- 🤖 **4 Agent-Modi** - Eigenes Wissen, Schnell (5 Quellen), Ausführlich (10+ Quellen), Interaktiv
- 📊 **Context Building** - Web-Scraping + Multi-Source Aggregation
- 🎚️ **Recherche-Tiefe wählbar** - User-kontrolliert via Settings
- 🔒 **Privacy-First** - SearXNG als privacy-freundliche Meta-Suchmaschine
- 🧠 **Intelligente Tool-Nutzung** - AI erkennt wann Web-Recherche nötig ist

---

## Access

### Web Browser
```
https://narnia.spdns.de:8443
```

**Requirements:**
- Internet connection
- Modern web browser (Chrome, Firefox, Edge, Safari)
- Microphone access (for voice input)

**First Visit:**
1. Browser may warn about SSL certificate (Let's Encrypt)
2. Click "Advanced" → "Proceed to site"
3. Grant microphone permission when prompted

---

## Usage Guide

### 🎤 Voice Input Workflow

1. **Record Audio**
   - Click the microphone icon
   - Speak your question
   - Click stop when done
   - Wait ~1 second for audio to finalize

2. **Submit**
   - Click "✅ Audio senden"
   - Buttons are disabled during recording
   - Audio is transcribed via Whisper

3. **AI Response**
   - Question appears in "Eingabe" field
   - AI generates response (via Ollama)
   - Response appears in "AI Antwort" field
   - TTS audio plays automatically (if enabled)

4. **Listen to Response**
   - Audio plays automatically
   - Use playback controls (0.5x, 1x, 1.5x, 2x)
   - Download option available

### ⌨️ Text Input Workflow

1. Type your question in "Texteingabe" field
2. Click "✅ Text senden"
3. AI responds with text + audio (same as voice input)

### 🔄 Regenerate Audio

After receiving an AI response, you can regenerate the audio with different settings:

1. **Change TTS Engine**
   - Switch from Edge TTS to Piper TTS (or vice versa)
   - Click "🔄 Sprachausgabe neu generieren"
   - Same text, different voice!

2. **Change Voice** (Edge TTS only)
   - Select different voice from dropdown
   - Click "🔄 Sprachausgabe neu generieren"
   - Hear the same answer in a different voice!

3. **Change Speed**
   - Adjust speed slider (1.0 - 2.0)
   - Click "🔄 Sprachausgabe neu generieren"
   - Hear the same answer faster/slower!

### 🗑️ Clear Functions

**Verwerfen (Discard Audio):**
- Discards current audio recording only
- Cannot be clicked during recording (button disabled)

**Chat komplett löschen (Clear Chat):**
- Clears entire conversation
- Resets all fields
- Starts fresh session

---

## Agent-Modi & Web-Recherche

**Neu in v2.0:** Der Voice Assistant kann jetzt das Internet durchsuchen und aktuelle Informationen liefern!

### 🤖 Die 4 Agent-Modi

Wähle in den Settings unter "🤖 Agent-Modus" zwischen:

#### 1. **🧠 Eigenes Wissen** (Standard)
- AI nutzt nur ihr trainiertes Wissen (bis 2023)
- **Keine Web-Suche**
- Schnellste Antworten
- Gut für: Allgemeinwissen, Erklärungen, Definitionen

**Beispiel:**
> Frage: "Was ist Quantenphysik?"
> AI nutzt: Training Data, keine Web-Suche

#### 2. **⚡ Web-Suche Schnell** (5 Quellen)
- AI sucht automatisch im Internet
- **5 Web-Quellen** werden durchsucht
- Balance zwischen Speed & Aktualität
- Gut für: Schnelle News, Fakten-Checks

**Beispiel:**
> Frage: "Was sind die neuesten Nachrichten über Trump?"
> AI nutzt: Web-Suche → 5 URLs → Aktuelle Antwort mit Zeitstempeln!

#### 3. **🔍 Web-Suche Ausführlich** (10+ Quellen)
- AI macht tiefe Recherche
- **10+ Web-Quellen** werden durchsucht
- Beste Qualität & Genauigkeit
- Gut für: Detaillierte Informationen, Vergleiche

**Beispiel:**
> Frage: "Vergleiche die aktuellen Wirtschaftsdaten von USA und Deutschland"
> AI nutzt: Web-Suche → 10+ URLs → Umfassende Analyse!

#### 4. **💬 Interaktiv** (AI fragt nach)
- AI erkennt ob Web-Suche nötig ist
- **Fragt dich** ob sie recherchieren soll
- Du entscheidest: Schnell oder Ausführlich
- Gut für: Wenn du Kontrolle willst

**Beispiel:**
> Frage: "Was sind die neuesten KI-Entwicklungen?"
> AI: "Möchtest du dass ich dazu im Web recherchiere?"
> Du: "Ja, ausführlich bitte!"
> AI nutzt: Web-Suche Ausführlich → 10+ URLs

### 🌐 Multi-API Web-Search System

Der Assistant nutzt ein 4-stufiges Fallback-System:

**Stufe 1: Brave Search API** (Primary)
- 2.000 Queries/Monat kostenlos
- Privacy-focused, eigener Index
- Beste Qualität

**Stufe 2: Tavily AI** (Fallback 1)
- 1.000 Queries/Monat kostenlos
- RAG-optimiert (speziell für AI gebaut)
- Saubere Snippets

**Stufe 3: Serper.dev** (Fallback 2)
- 2.500 Queries einmalig kostenlos
- Google-powered (beste Abdeckung)
- Hohe Qualität

**Stufe 4: SearXNG** (Last Resort) ✅ **Läuft bereits!**
- **Unlimited** Queries (self-hosted)
- Privacy-first Meta-Suchmaschine
- Aggregiert Google, Bing, DuckDuckGo
- Keine API Keys nötig!

**Automatisches Fallback:**
Wenn eine API ihr Limit erreicht, wechselt das System automatisch zur nächsten!

### 📊 Wie Web-Recherche funktioniert

**Workflow:**

```
1. Du stellst Frage
   ↓
2. Agent-Modus entscheidet ob Web-Suche nötig
   ↓
3. Multi-API System sucht (5 oder 10+ URLs)
   ↓
4. Web-Scraper lädt Content von URLs
   ↓
5. Context Building: AI-lesbarer Text (max 4000 chars/Source)
   ↓
6. System Prompt: "NUTZE NUR DIESE RECHERCHE-DATEN!"
   ↓
7. AI antwortet mit aktuellen Informationen
   ↓
8. Antwort enthält Zeitstempel & Quellen-Zitate!
```

### ✅ Vorteile der Web-Recherche

**Vorher (ohne Web-Recherche):**
> Frage: "Neueste Nachrichten über Trump?"
> AI: "Entschuldigung, ich habe keinen Internet-Zugang..."
> ❌ Keine aktuellen Infos!

**Jetzt (mit Web-Recherche):**
> Frage: "Neueste Nachrichten über Trump?"
> AI: "Laut meiner aktuellen Recherche vom 13.10.2025 schreibt die Tagesschau, dass Präsident Trump heute Nationalgardisten in Chicago einsetzen will. Die FAZ berichtet vor 2 Stunden, dass Trump zusätzliche Zölle von 100% auf China-Importe ankündigt..."
> ✅ Aktuelle Infos mit Zeitstempeln!

### 🔒 Privacy & API Keys

**Ohne API Keys (Standard):**
- Nur SearXNG wird genutzt
- Unlimited Queries
- Privacy-first (self-hosted)
- Funktioniert einwandfrei! ✅

**Mit API Keys (Optional, mehr Performance):**
- Schnellere Antworten (APIs sind schneller als SearXNG)
- 3.000+ Queries/Monat statt nur SearXNG
- Bessere Quellen-Qualität
- Setup: Siehe [API_SETUP.md](API_SETUP.md)

### 🎯 Wann welchen Modus nutzen?

| Frage-Typ | Empfohlener Modus | Warum |
|-----------|-------------------|-------|
| "Was ist Quantenphysik?" | 🧠 Eigenes Wissen | Allgemeinwissen, zeitlos |
| "Wer war Einstein?" | 🧠 Eigenes Wissen | Historisch, kein Update nötig |
| "Neueste Trump News?" | ⚡ Schnell | Aktuelle News, 5 Quellen reichen |
| "Wetter heute?" | ⚡ Schnell | Aktuelle Info, schnelle Antwort |
| "Vergleiche Wirtschaftsdaten USA/DE" | 🔍 Ausführlich | Komplex, viele Daten nötig |
| "Aktuelle KI-Forschung?" | 🔍 Ausführlich | Detailliert, Multi-Source |
| "Ist das wahr: [Behauptung]?" | 💬 Interaktiv | Kontrolle über Recherche-Tiefe |

---

## Settings & Configuration

### 🤖 AI Model Selection

**Neu in v2.0:** Verbesserte Model-Auswahl mit In-UI Hilfe!

Klicke auf "ℹ️ Welches Model soll ich wählen?" für Empfehlungen.

**Empfohlene Modelle (installiert):**
- **qwen2.5:14b** ⭐⭐⭐⭐⭐ - **BESTE Wahl für Web-Recherche!**
  - Ignoriert Training Data komplett (RAG Score: 1.0)
  - Nutzt NUR Web-Recherche Ergebnisse
  - Größe: 9 GB, RAM: ~12 GB
  - Perfekt für: Aktuelle News, Fakten, Web-Recherche

- **qwen3:8b** ⭐⭐⭐⭐ - **Schnell & Gut!**
  - Balance zwischen Speed und Qualität
  - Größe: 5.2 GB, RAM: ~7 GB
  - Perfekt für: Schnelle Antworten, gute Recherche

- **llama3.1:8b** ⭐⭐⭐ - **Zuverlässig & Bewährt**
  - Stabile, zuverlässige Antworten
  - Größe: 4.9 GB, RAM: ~6 GB
  - Perfekt für: Allgemeine Konversation

- **command-r** ⭐⭐⭐⭐ - **Enterprise RAG**
  - Speziell für RAG/Dokumente gebaut
  - Größe: 18 GB, RAM: ~22 GB
  - Perfekt für: Komplexe Dokumente, Enterprise Use

- **llama2:13b** ⭐⭐⭐ - **Breites Wissen**
  - Klassisches Modell, viel Training
  - Größe: 7.4 GB, RAM: ~10 GB
  - Perfekt für: Allgemeinwissen, Erklärungen

- **llama3.2:3b** ⭐⭐ - **Tests & Schnell**
  - Sehr schnell, aber schwächer
  - Größe: 2 GB, RAM: ~3 GB
  - Perfekt für: Einfache Fragen, Tests

**Hardware:** AOOSTAR GEM10 mit 32 GB RAM kann **ALLE** Modelle problemlos ausführen! ✅

**Empfehlung für dein System:**
- **Standard:** qwen2.5:14b (für Web-Recherche)
- **Schnell:** qwen3:8b (wenn Speed wichtiger)
- **Enterprise:** command-r (für umfangreiche Dokumente)

### 🔊 Speech Output Settings

**Enable/Disable TTS:**
- Toggle "🔊 Sprachausgabe aktiviert"
- When disabled: Text-only responses (no audio)

**TTS Engine:**
- **Edge TTS (Cloud, beste Qualität)**
  - Microsoft cloud-based
  - 4 voices available (German/English)
  - High quality, natural sounding
  - Requires internet

- **Piper TTS (Lokal, sehr schnell)**
  - Local TTS (Thorsten voice)
  - Very fast generation
  - Works offline
  - German male voice only

**Speed Control:**
- Range: 1.0x - 2.0x
- Default: 1.25x (recommended for Edge TTS)
- **1.0x** = Normal speed
- **1.25x** = Recommended (Edge TTS speaks slowly by default)
- **1.5x** = Noticeably faster
- **2.0x** = Double speed

### 🎤 Voice Selection (Edge TTS only)

Available voices:
- **Deutsch (Katja)** - German female (default)
- **Deutsch (Conrad)** - German male
- **English (Jenny)** - US English female
- **English (Guy)** - US English male

*Note: Voice selection is hidden when Piper TTS is selected*

### 🎙️ Whisper Model Selection (Neu in v2.0)

Wähle zwischen 5 verschiedenen Whisper-Modellen für Spracherkennung:

- **base (142MB)** - Standard, schnell, multilingual ✅ Empfohlen
- **small (466MB)** - Besser, noch schnell
- **medium (1.5GB)** - Gute Qualität, langsamer
- **large-v2 (2.9GB)** - Sehr gute Qualität
- **large-v3-turbo (1.6GB)** - Beste Qualität, optimiert

**Empfehlung:**
- **Für die meisten Nutzer:** base (schnell, gut genug)
- **Für beste Qualität:** large-v3-turbo
- **Balance:** small

**Hinweis:** Modelle werden beim ersten Mal automatisch heruntergeladen!

---

## LLM Model-Auswahl

### 🏆 Welches Model ist das beste für mich?

**Für Web-Recherche & aktuelle Nachrichten:**
→ **qwen2.5:14b** (Score: 1.0, ignoriert Training Data komplett!)

**Für schnelle Antworten:**
→ **qwen3:8b** oder **llama3.1:8b**

**Für komplexe Dokumente/Enterprise:**
→ **command-r**

**Für Allgemeinwissen & Konversation:**
→ **llama3.1:8b** oder **llama2:13b**

### 📊 Vergleichstabelle

| Model | Größe | RAG Score | Speed | Beste für |
|-------|-------|-----------|-------|-----------|
| qwen2.5:14b | 9 GB | 1.0 🏆 | Mittel | **Web-Recherche** |
| qwen3:8b | 5.2 GB | 0.933 | Schnell | Balance |
| command-r | 18 GB | 0.92 | Langsam | Enterprise RAG |
| llama3.1:8b | 4.9 GB | 0.85 | Schnell | Allgemein |
| llama2:13b | 7.4 GB | 0.78 | Mittel | Wissen |
| llama3.2:3b | 2 GB | 0.70 | Sehr schnell | Tests |

**RAG Score:** Wie gut nutzt das Model Web-Recherche statt Training Data (1.0 = perfekt)

### 🧪 Context Adherence Test

**Test:** "Nutze nur Web-Recherche, nicht Training Data"

**qwen2.5:14b:**
> "Laut Quelle 1 (Tagesschau) vom 13.10.2025..."
> ✅ Perfekt! Nutzt NUR Recherche-Daten

**llama3.2:3b:**
> "Trump hat im Januar 2022..."
> ❌ Nutzt Training Data, ignoriert Recherche!

**Fazit:** qwen2.5:14b ist die beste Wahl für Web-Recherche!

### 💡 In-UI Hilfe

In der Web-UI findest du unter "🤖 AI Model (Ollama)" ein Collapsible:
→ **"ℹ️ Welches Model soll ich wählen?"**

Dort bekommst du eine Schnellübersicht mit Empfehlungen!

---

## TTS Engines

### Edge TTS (Recommended)

**Pros:**
- ✅ High quality, natural voices
- ✅ Multiple languages/voices
- ✅ Good speed control
- ✅ Female and male voices

**Cons:**
- ❌ Requires internet
- ❌ Slightly slower generation
- ❌ Cloud dependency

**Best For:**
- General use
- When quality matters
- Multiple language support needed

### Piper TTS (Local)

**Pros:**
- ✅ Very fast generation
- ✅ Works offline
- ✅ Local processing (privacy)
- ✅ No cloud dependency

**Cons:**
- ❌ Only German male voice (Thorsten)
- ❌ Less natural sounding
- ❌ No voice selection

**Best For:**
- Fast responses
- Offline use
- Privacy concerns
- German language only

---

## Troubleshooting

### Audio Recording Issues

**Problem:** Audio upload requires double-click

**Solution:**
- Wait ~1 second after stopping recording
- Then click "Audio senden"
- Buttons are disabled during recording

**Problem:** "Verwerfen" button not working

**Solution:**
- Button is disabled during recording (by design)
- Stop recording first, then click "Verwerfen"

### TTS Issues

**Problem:** Speed setting not working

**Solution:**
- Use "🔄 Sprachausgabe neu generieren" after changing speed
- Default 1.25x is recommended for Edge TTS

**Problem:** Voice selection not visible

**Solution:**
- Voice selection only works with Edge TTS
- Switch to "Edge TTS (Cloud)" to see voice options

### Service Issues

**Check service status:**
```bash
systemctl status voice-assistant.service
```

**Restart service:**
```bash
sudo systemctl restart voice-assistant.service
```

**View logs:**
```bash
journalctl -u voice-assistant.service -f
```

**Stop service:**
```bash
sudo systemctl stop voice-assistant.service
```

**Start service:**
```bash
sudo systemctl start voice-assistant.service
```

### Browser Issues

**Problem:** White theme on PC, dark on mobile

**Solution:**
- Enable Dark Mode in your operating system
- Or install browser extension like "Dark Reader"

**Problem:** Certificate warning

**Solution:**
- Click "Advanced" → "Proceed to site"
- Certificate is valid Let's Encrypt for narnia.spdns.de

---

## Technical Details

### Architecture (v2.0 mit Agent-System)

```
User Browser (HTTPS)
    ↓
Gradio Web Interface (Port 8443)
    ↓
├── Whisper (Speech-to-Text) - 5 Modelle wählbar
├── Agent-Modi Detection
│   ├── 🧠 Eigenes Wissen (kein Agent)
│   ├── ⚡ Web-Suche Schnell (5 Quellen)
│   ├── 🔍 Web-Suche Ausführlich (10+ Quellen)
│   └── 💬 Interaktiv (User wählt)
│       ↓
│   [Agent Activated]
│       ↓
│   Multi-API Search (4-stufiges Fallback)
│       ├── Brave Search API (2k/Monat)
│       ├── Tavily AI (1k/Monat)
│       ├── Serper.dev (2.5k einmalig)
│       └── SearXNG (unlimited) ✅
│       ↓
│   Web-Scraper (BeautifulSoup)
│       ↓
│   Context Builder (max 4000 chars/Source)
│       ↓
├── Ollama (AI Chat) - 6 Modelle, qwen2.5:14b empfohlen
│   └── System Prompt mit Web-Recherche Context
└── TTS Engine
    ├── Edge TTS (Cloud, 4 Voices)
    └── Piper TTS (Local, Thorsten)
```

### File Locations

**Main Application:**
```
/home/mp/Projekte/voice-assistant/mobile_voice_assistant.py
```

**Agent Tools (v2.0):**
```
/home/mp/Projekte/voice-assistant/agent_tools.py
```

**Service File:**
```
/etc/systemd/system/voice-assistant.service
```

**SSL Certificates:**
```
/home/mp/ai_env/privkey.pem
/home/mp/ai_env/fullchain.pem
```

**Piper Models:**
```
/home/mp/ai_env/piper_models/de_DE-thorsten-medium.onnx
```

**SearXNG Docker:**
```
/home/mp/MiniPCLinux/docker/searxng/
├── compose.yml
└── settings.yml
```

**Settings:**
```
/home/mp/Projekte/voice-assistant/assistant_settings.json
```

**Logs:**
```
journalctl -u voice-assistant.service -f
sudo tail -f /var/log/voice-assistant.log
```

### Dependencies

**Python Packages:**
- gradio - Web interface
- faster-whisper - Speech recognition (5 models)
- ollama - AI chat (6 models installiert)
- edge-tts - Cloud TTS (4 voices)
- piper-tts - Local TTS (Thorsten voice)
- beautifulsoup4 - Web scraping
- lxml - HTML parsing
- requests - HTTP requests

**System Services:**
- Ollama server (for AI models)
- systemd (service management)
- Docker (for SearXNG)

**Docker Containers:**
- SearXNG (Meta-Suchmaschine, unlimited queries)

### Performance

**Response Times (AOOSTAR GEM10, 32 GB RAM):**
- Audio transcription (Whisper base): ~1-3 seconds
- **Web-Recherche (Agent aktiviert):**
  - Schnell (5 Quellen): ~5-10 seconds
  - Ausführlich (10+ Quellen): ~15-25 seconds
- AI response (ohne Agent): ~2-5 seconds (model dependent)
  - qwen2.5:14b: ~15 seconds für 100 Wörter
  - qwen3:8b: ~8 seconds für 100 Wörter
  - llama3.1:8b: ~8 seconds für 100 Wörter
  - command-r: ~20+ seconds für 100 Wörter
- Edge TTS: ~1-2 seconds
- Piper TTS: ~0.5-1 seconds

**Resource Usage (qwen2.5:14b):**
- Memory: ~12-15 GB (mit Model geladen)
- CPU: Low (spikes während Inferenz)
- Storage: ~50 GB (alle Models + SearXNG)

**Hardware Specs:**
- System: AOOSTAR GEM10 Mini PC
- RAM: 32 GB (kann ALLE Models ausführen!)
- Storage: 1 TB M.2 SSD
- Docker: SearXNG Container (~100 MB)

### Security

- ✅ HTTPS/SSL encryption
- ✅ Let's Encrypt certificates
- ✅ No authentication (local network only)
- ✅ DynDNS for external access

---

## Advanced Usage

### Running Manually (for testing)

```bash
cd /home/mp/ai_env
/home/mp/ai_env/bin/python mobile_voice_assistant.py
```

**Note:** Stop systemd service first:
```bash
sudo systemctl stop voice-assistant.service
```

### Changing Models

Edit the code to add/remove models:
```python
models = ["llama3.2:3b", "mistral", "your-model-here"]
```

### Changing Voices

Edge TTS voices are defined in:
```python
voices = {
    "Deutsch (Katja)": "de-DE-KatjaNeural",
    "Your Voice": "voice-code-here"
}
```

---

## Changelog

### v2.0 - Multi-API Web-Search Release (13. Oktober 2025)

**🆕 Major Features:**
- ✅ **Multi-API Web-Search System** - 4-stufiges Fallback
  - Brave Search API (2.000/Monat)
  - Tavily AI (1.000/Monat)
  - Serper.dev (2.500 einmalig)
  - SearXNG Self-Hosted (unlimited) ✅
- ✅ **4 Agent-Modi** - Eigenes Wissen, Schnell, Ausführlich, Interaktiv
- ✅ **Neue AI Modelle** - qwen2.5:14b, qwen3:8b, llama3.1:8b, command-r
- ✅ **LLM Model-Auswahl Hilfe** - In-UI Collapsible mit Empfehlungen
- ✅ **5 Whisper Models** - base, small, medium, large, turbo
- ✅ **Persistent Settings** - Model, TTS, Whisper, Agent-Modus gespeichert
- ✅ **SearXNG Docker Setup** - Self-hosted Meta-Suchmaschine
- ✅ **Web-Scraping** - BeautifulSoup für Content-Extraktion
- ✅ **Context Building** - Max 4000 chars/Source, Multi-Source Aggregation

**🔧 Behobene Probleme:**
- ✅ AI sagt nicht mehr "Ich habe keinen Internet-Zugang"
- ✅ AI nutzt nicht mehr veraltete Training Data (2022) für aktuelle Fragen
- ✅ DuckDuckGo "0 URLs" Problem gelöst → Multi-API System
- ✅ Robuste Fallback-Mechanismen bei API Rate Limits
- ✅ Aggressiver System-Prompt für bessere Context Adherence

**📚 Dokumentation:**
- ✅ API_SETUP.md - Vollständige Setup-Anleitung
- ✅ LLM_COMPARISON.md - Technischer Modell-Vergleich
- ✅ LLM_HELP_UI.md - User-freundliche Hilfe
- ✅ IMPLEMENTATION_COMPLETE.md - Status-Zusammenfassung
- ✅ INDEX.md - Dokumentations-Übersicht
- ✅ Aktualisierte README & Complete Guide

### v1.x - Piper TTS & UI Improvements (Anfang Oktober 2025)

**New Features:**
- ✅ Piper TTS integration (local, fast)
- ✅ TTS Engine selection (Edge/Piper)
- ✅ Regenerate audio button
- ✅ Dynamic voice selection
- ✅ Speed control fixes
- ✅ Dark mode support
- ✅ Robust button state management

**Bug Fixes:**
- ✅ Fixed double-click audio submit issue
- ✅ Fixed "Verwerfen" button corruption
- ✅ Fixed recording state tracking
- ✅ Fixed speed parameter application

**UI Improvements:**
- ✅ Buttons disabled during recording
- ✅ App load event for clean initialization
- ✅ Session cleanup (capacity: 10)
- ✅ Better user feedback

---

## Support

For issues or questions:
- Check this documentation (especially [Agent-Modi & Web-Recherche](#agent-modi--web-recherche))
- View service logs: `journalctl -u voice-assistant.service -f`
- API Setup: See [API_SETUP.md](API_SETUP.md)
- Model Comparison: See [LLM_COMPARISON.md](LLM_COMPARISON.md)
- GitHub: https://github.com/Peuqui/AI-Voice-Assistant

**Hardware:**
- AOOSTAR GEM10 Mini PC
- 32 GB RAM (kann ALLE LLM Models ausführen!)
- 1 TB M.2 SSD

---

**Last Updated:** 13. Oktober 2025
**Version:** 2.0 - Multi-API Web-Search Release
**Maintained by:** Peuqui (with Claude Code assistance)
