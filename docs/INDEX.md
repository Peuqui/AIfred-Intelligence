# Voice Assistant - Dokumentations-Übersicht

**Stand:** 2025-10-13
**Status:** ✅ Multi-API Web-Search implementiert und getestet

---

## 📚 Dokumentations-Index

### 🚀 Start & Setup

#### [README.md](README.md) - Schnellstart & Übersicht
- Voraussetzungen & Installation
- Quick Start Guide
- Grundlegende Features
- Links zu weiterführenden Ressourcen

**Status:** ⚠️ Veraltet - Vor Multi-API Update (Stand: Oktober 2024)
**Action:** Sollte auf Stand 2025-10-13 aktualisiert werden

---

### 🏗️ Architektur & Design

#### [architecture-agentic-features.md](architecture-agentic-features.md) - Agent-System Architektur
- Agentische Pipeline Design (5-Stufen)
- Tool-System Architektur
- Interaktiver Agent-Modus Konzept
- Performance-Ziele & Metriken

**Status:** ✅ Aktuell - Matches current implementation (Stand: 2025-10-13)

**Highlights:**
- Interaktiver Modus mit User-Choice (Settings-basiert)
- Tool-System mit DuckDuckGo, SearXNG, Web-Scraping
- 3 Modi: Eigenes Wissen, Schnell, Ausführlich

---

### 🔍 Web-Recherche & API Setup

#### [API_SETUP.md](API_SETUP.md) - Multi-API Search Setup Guide
**Status:** ✅ Aktuell - Vollständige Anleitung (Stand: 2025-10-13)

**Inhalt:**
- 4-Stufen Fallback-System erklärt
  1. Brave Search API (Primary) - 2.000/Monat
  2. Tavily AI (Fallback 1) - 1.000/Monat
  3. Serper.dev (Fallback 2) - 2.500 einmalig
  4. SearXNG (Last Resort) - Unlimited ✅
- SearXNG Docker Setup & Verwaltung
- API Key Setup für Brave, Tavily, Serper
- Query Economics erklärt
- Troubleshooting Guide

**Wichtig:**
- SearXNG läuft bereits auf `http://localhost:8888`
- API Keys sind optional (SearXNG reicht!)
- Getestet mit Trump-News Query ✅

#### [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md) - Implementierungs-Status
**Status:** ✅ Aktuell - Vollständige Zusammenfassung (Stand: 2025-10-13)

**Inhalt:**
- Was wurde implementiert (SearXNG, Multi-API, Tests)
- Test-Ergebnisse mit echten Beispielen
- Dateien erstellt/modifiziert
- Next Steps für User
- Erfolgs-Metriken Vergleich (Vorher/Nachher)

**Key Achievement:**
> "Die AI bekommt jetzt ECHTE, AKTUELLE URLs mit Zeitstempeln!"
> - tagesschau.de: "vor 3 Stunden - Präsident Trump..."
> - faz.net: "vor 2 Stunden - US-Präsident Trump..."

---

### 🤖 LLM Model-Auswahl

#### [LLM_COMPARISON.md](LLM_COMPARISON.md) - Technischer Vergleich
**Status:** ✅ Aktuell - Entwickler-Referenz (Stand: 2025-10-13)

**Inhalt:**
- Technische Übersichtstabelle (6 Modelle)
- RAG Score, Tool-Use, Speed, Speicher
- Use-Case Empfehlungen
- Benchmark Details (Context Adherence, Tool-Use Tests)
- Hardware-Anforderungen
- Performance-Messungen

**Top-Empfehlung:** `qwen2.5:14b` für Web-Recherche/Agentic

#### [LLM_HELP_UI.md](LLM_HELP_UI.md) - User-Freundliche Hilfe
**Status:** ✅ Aktuell - UI-optimiert (Stand: 2025-10-13)

**Inhalt:**
- Schnellübersicht-Tabelle (für UI Collapsible)
- Erweiterte Tabelle mit allen Metriken
- Use-Case Empfehlungen für Voice Assistant
- Performance-Vergleich (Mini-PC)
- Context Adherence Test mit Beispielen
- Finale Empfehlung mit Setup-Anleitung

**Bereits implementiert:** Collapsible in UI bei AI Model Dropdown!

---

### 📖 Vollständige Guides

#### [voice-assistant-complete-guide.md](voice-assistant-complete-guide.md) - Vollständige Anleitung
**Status:** ⚠️ Veraltet - Pre-Agent Features (Stand: 2024-10-10)

**Inhalt:**
- Setup & Installation
- Konfiguration
- Entwicklung
- Troubleshooting

**Action:** Sollte aktualisiert werden um:
- Multi-API Web-Search System
- Agent-Modi (Eigenes Wissen, Schnell, Ausführlich)
- SearXNG Docker Setup
- Neue LLM-Modelle (qwen2.5:14b, qwen3:8b, command-r)

---

## 📊 Aktueller Implementierungs-Status

### ✅ Fertiggestellt (2025-10-13)

1. **Multi-API Web-Search System**
   - ✅ SearXNG Docker läuft (`http://localhost:8888`)
   - ✅ 4-Stufen Fallback implementiert
   - ✅ agent_tools.py komplett neu geschrieben
   - ✅ Getestet mit Trump News Query
   - ✅ Liefert 10+ URLs mit aktuellen Zeitstempeln

2. **Agent-Modi in UI**
   - ✅ Settings mit 4 Modi (Eigenes Wissen, Schnell, Ausführlich, Interaktiv)
   - ✅ Accordion mit Erklärungen
   - ✅ Persistierung in `assistant_settings.json`
   - ✅ Modus-basiertes Routing implementiert

3. **LLM Model Auswahl Hilfe**
   - ✅ Collapsible UI-Hilfe bei AI Model Dropdown
   - ✅ Tabelle mit 6 Modellen + Empfehlungen
   - ✅ Dokumentation (LLM_COMPARISON.md, LLM_HELP_UI.md)

4. **Neue AI Modelle**
   - ✅ qwen2.5:14b (9 GB) - Empfohlen für RAG/Agentic
   - ✅ qwen3:8b (5.2 GB) - Balance Speed/Qualität
   - ✅ llama3.1:8b (4.9 GB) - Zuverlässig, bewährt
   - ✅ command-r (18 GB) - Enterprise RAG

5. **Dokumentation**
   - ✅ API_SETUP.md - Vollständige Setup-Anleitung
   - ✅ IMPLEMENTATION_COMPLETE.md - Status-Zusammenfassung
   - ✅ LLM_COMPARISON.md - Technischer Vergleich
   - ✅ LLM_HELP_UI.md - User-freundliche Hilfe
   - ✅ INDEX.md (diese Datei) - Dokumentations-Übersicht

### ⏳ Noch zu tun

1. **API Keys Setup** (optional)
   - Brave Search API Key
   - Tavily AI API Key
   - Serper.dev API Key
   - Siehe: [API_SETUP.md](API_SETUP.md)

2. **Service Restart** (benötigt sudo)
   ```bash
   sudo systemctl restart voice-assistant
   ```

3. **Testing mit Web-UI**
   - Teste mit: "Zeige mir die neuesten Nachrichten über Donald Trump"
   - Erwartung: AI nutzt Web-Recherche, zitiert echte Quellen
   - Verify: Logs prüfen (`sudo journalctl -u voice-assistant -f`)

4. **Dokumentation aktualisieren**
   - [ ] README.md auf Stand 2025-10-13 bringen
   - [ ] voice-assistant-complete-guide.md aktualisieren
   - [ ] Git Commit & Push

---

## 🎯 Quick Reference

### Für User (Schnellstart)

**Du willst den Voice Assistant nutzen?**
1. Start: [README.md](README.md) - Grundlegende Installation
2. Setup: [API_SETUP.md](API_SETUP.md) - Web-Suche konfigurieren
3. Model: [LLM_HELP_UI.md](LLM_HELP_UI.md) - Welches Model wählen?

**Bereits installiert auf deinem System:**
- ✅ SearXNG läuft (`http://localhost:8888`)
- ✅ Multi-API Fallback implementiert
- ✅ Agent-Modi in UI verfügbar
- ⏳ Service-Restart ausstehend

### Für Entwickler (Architektur)

**Du willst den Code verstehen/erweitern?**
1. Architektur: [architecture-agentic-features.md](architecture-agentic-features.md)
2. Implementation: [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)
3. Code:
   - `mobile_voice_assistant.py` - Main app
   - `agent_tools.py` - Multi-API search system
   - Docker: `/home/mp/MiniPCLinux/docker/searxng/`

### Für Troubleshooting

**Etwas funktioniert nicht?**
1. [API_SETUP.md - Troubleshooting](API_SETUP.md#troubleshooting)
2. [voice-assistant-complete-guide.md](voice-assistant-complete-guide.md) (falls verfügbar)
3. Logs: `sudo journalctl -u voice-assistant -f`

---

## 📈 Versions-Historie

### v2.0 - Multi-API Web-Search (2025-10-13)

**Major Features:**
- 🌐 4-stufiges Fallback Web-Search System
- 🔍 SearXNG Self-Hosted (unlimited queries)
- 🤖 Agent-Modi: Eigenes Wissen, Schnell, Ausführlich
- 📊 LLM Model Auswahl Hilfe (UI + Docs)
- ✅ Getestet & funktionstüchtig

**Behobene Probleme:**
- ✅ AI sagt nicht mehr "Ich habe keinen Internet-Zugang"
- ✅ AI nutzt nicht mehr Training Data (2022) für aktuelle Fragen
- ✅ DuckDuckGo "0 URLs" Problem gelöst
- ✅ Agent-Awareness durch aggressiveren System-Prompt

### v1.x - Basis Voice Assistant (2024-10)

**Features:**
- 🎤 Audio-Aufnahme mit Whisper STT
- 🤖 Ollama Integration (lokale LLMs)
- 🔊 Edge TTS / Piper TTS Sprachausgabe
- 📱 Mobile-optimierte Gradio UI
- 🔒 HTTPS Support

---

## 🔗 Externe Ressourcen

### Code Repositories
- **Voice Assistant:** https://github.com/Peuqui/AI-Voice-Assistant
- **System Setup:** https://github.com/Peuqui/minipc-linux

### Docker Locations
- **SearXNG:** `/home/mp/MiniPCLinux/docker/searxng/`
- **Andere Services:** `/home/mp/MiniPCLinux/docker/` (Portainer, Jellyfin, etc.)

### API Dokumentation
- **Brave Search:** https://brave.com/search/api/
- **Tavily AI:** https://www.tavily.com/
- **Serper.dev:** https://serper.dev/
- **SearXNG:** https://github.com/searxng/searxng

### Tech Stack
- **Gradio:** https://gradio.app
- **Ollama:** https://ollama.com
- **Whisper:** https://github.com/openai/whisper
- **Edge TTS:** https://github.com/rany2/edge-tts

---

## 📝 Dokumentations-Richtlinien

### Neue Docs hinzufügen

1. Erstelle Markdown-Datei in `/home/mp/Projekte/voice-assistant/docs/`
2. Füge Eintrag in diesem INDEX.md hinzu
3. Setze **Status** und **Stand** (Datum)
4. Verlinke verwandte Dokumente

### Docs aktualisieren

1. Aktualisiere Inhalt in entsprechendem Dokument
2. Ändere **Stand** Datum
3. Update Status in INDEX.md
4. Füge zu **Versions-Historie** hinzu (falls Major Change)

### Status-Flags

- ✅ **Aktuell** - Matches current implementation
- ⚠️ **Veraltet** - Needs update, contains outdated info
- ⏳ **WIP** - Work in Progress
- 📝 **Geplant** - Planned for future

---

**Letzte Aktualisierung:** 2025-10-13
**Autor:** Claude Code
**Version:** 2.0 - Multi-API Web-Search Release
