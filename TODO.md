# AIfred Intelligence - TODO Liste

## ✅ Erledigte Features

### Session 4 (02.11.2025) - History-Summarization
- [x] **Vollständige Implementation der History-Kompression** ✅
  - Triggert bei 70% Context-Auslastung (konfigurierbar)
  - Komprimiert 3 Frage-Antwort-Paare → 1 Summary
  - FIFO-System: Max. 10 Summaries (älteste werden gelöscht)
  - Safety-Checks: Mindestens 1 aktuelles Gespräch bleibt sichtbar
  - Umfangreiches Logging mit Token-Metriken
  - 6:1 Kompressionsrate bei faktischen Inhalten
- [x] **Bug-Fixes** ✅
  - Vergleichsoperator-Bug behoben (< statt <=)
  - LLMMessage/LLMOptions Format korrigiert
  - HTTP-Timeout für Ollama hinzugefügt (60s)
  - Chat-Löschungs-Problem behoben

### Session 5 (10.11.2025) - Vector Cache & RAG
- [x] **ChromaDB Vector Cache Production Ready** ✅
  - Docker-based ChromaDB server mode
  - Semantic caching for web research results
  - Configurable distance thresholds
  - Auto-learning from web research
- [x] **RAG (Retrieval-Augmented Generation) Mode** ✅
  - LLM-based relevance detection for cache entries
  - Multi-level context awareness (cache + history)
  - Smart context injection for related queries
- [x] **Intelligent Cache Decision System** ✅
  - LLM-based cache filtering
  - Volatile keyword detection
  - Override logic for concept questions

### Session 6 (11.11.2025) - Cache Optimization
- [x] **Pure Semantic Deduplication** ✅
  - Removed time-based duplicate detection (5-minute threshold)
  - Always update semantic duplicates (distance < 0.3)
  - Fixed 10x Python duplicates issue
  - Consistent behavior, no race conditions
- [x] **Smart Cache for Explicit Research** ✅
  - Cache check before web research for keywords ("recherchiere", "google")
  - Distance < 0.05 → Use cache (0.15s instead of 100s)
  - Transparent cache age display
- [x] **ChromaDB Maintenance Tool** ✅
  - Stats display, duplicate detection/removal
  - Age-based cleanup, dry-run mode
- [x] **Automatik-LLM Optimization** ✅
  - Switched to qwen2.5:3b (from qwen3:8b)
  - 2.7x faster decisions (0.3s instead of 0.8s)
  - 63% less VRAM usage
- [x] **Bug Fixes** ✅
  - LLMResponse AttributeError in cache decision
  - Import errors after removing CACHE_TIME_THRESHOLD

## 🚀 High Priority Features

### 1. Voice Features (TTS/STT) 🎤
- [ ] **Streaming TTS**: Text-to-Speech während AI noch schreibt
  - Phase 1: Ohne Streaming (stabil) ✅
  - Phase 2: Satz-basiertes Streaming
  - Phase 3: Token-Streaming mit ML-Betonungskorrektur
- [ ] **Wake Word Detection**: "Hey Alfred" zum Aktivieren
- [ ] **Voice Commands**: Sprachbefehle für Navigation
- [ ] **Multi-Language TTS**: Verschiedene Stimmen/Sprachen
- [ ] **Emotion in Voice**: Anpassung der Stimme je nach Kontext

### 2. Internationalisierung (i18n) 🌍
- [ ] Deutsche + Englische Prompts
- [ ] UI-Strings mehrsprachig
- [ ] Auto-Detection der User-Sprache
- [ ] Weitere Sprachen (FR, ES, IT)

### 3. Vision Support 👁️
- [ ] Bildanalyse mit Multimodal-LLMs
- [ ] Screenshot-Analyse
- [ ] Dokument-OCR
- [ ] Diagramm-Verständnis

## 🔧 Medium Priority Features

### 4. UI/UX Verbesserungen 🎨
- [ ] **Dark/Light Mode Toggle**: Automatisch oder manuell
- [ ] **Markdown Tables**: Bessere Tabellen-Darstellung
- [ ] **Code Syntax Highlighting**: In Chat-Antworten
- [ ] **Export Funktionen**: Chat als PDF/Markdown exportieren
- [ ] **Keyboard Shortcuts**: Ctrl+Enter zum Senden, etc.
- [ ] **Mobile PWA**: Progressive Web App

### 5. AI Features 🤖
- [ ] **Multi-Agent Conversations**: Mehrere Spezial-Agents die zusammenarbeiten
- [ ] **Function Calling**: AI kann externe Tools/APIs aufrufen
- [ ] **Document Processing**: PDFs, Word, Excel direkt verarbeiten
- [ ] **Code Execution**: Python/JS Code direkt ausführen
- [ ] **Memory System**: Langzeit-Gedächtnis über Sessions hinweg

### 6. Performance & Scaling ⚡
- [ ] **Response Caching**: Häufige Fragen zwischenspeichern
- [ ] **Parallel LLM Calls**: Mehrere Modelle gleichzeitig fragen
- [ ] **Load Balancing**: Mehrere Ollama-Instanzen
- [ ] **GPU Monitoring**: GPU-Auslastung anzeigen
- [ ] **Token Usage Analytics**: Statistiken über Token-Verbrauch
- [ ] **Unit-Tests**: Context-Manager, Cache-System
- [ ] **Integration-Tests**: End-to-End Tests

## 📦 Nice-to-Have Features

### 7. Integration Features 🔗
- [ ] **Calendar Integration**: Termine verwalten
- [ ] **Email Integration**: Emails lesen/schreiben
- [ ] **Home Assistant**: Smart Home Steuerung
- [ ] **Git Integration**: Code-Reviews, PRs erstellen
- [ ] **Database Queries**: SQL direkt ausführen
- [ ] **Webhook Support**: Externe Events empfangen

### 8. Security & Privacy 🔐
- [ ] **User Authentication**: Login-System
- [ ] **Conversation Encryption**: Ende-zu-Ende Verschlüsselung
- [ ] **API Rate Limiting**: Schutz vor Überlastung
- [ ] **Audit Logging**: Alle Aktionen protokollieren
- [ ] **Data Retention Policies**: Automatisches Löschen alter Daten

### 9. Developer Tools 🛠️
- [ ] **Plugin System**: Eigene Plugins/Extensions
- [ ] **REST API**: Externe Programme können AIfred nutzen
- [ ] **WebSocket API**: Real-time Integration
- [ ] **CLI Tool**: Terminal-Interface für AIfred
- [ ] **SDK/Library**: Python/JS Library für Integration

### 10. Analytics & Monitoring 📊
- [ ] **Usage Dashboard**: Visualisierung der Nutzung
- [ ] **Response Time Metrics**: Performance-Monitoring
- [ ] **Error Tracking**: Automatische Fehlererfassung
- [ ] **Model Performance**: Vergleich verschiedener Modelle
- [ ] **Cost Tracking**: Bei Cloud-LLMs Kosten tracken

### 11. Collaboration Features 👥
- [ ] **Shared Conversations**: Links zum Teilen
- [ ] **Team Workspaces**: Mehrere User
- [ ] **Comments/Annotations**: Notizen zu Antworten
- [ ] **Version History**: Änderungen nachvollziehen
- [ ] **Real-time Collaboration**: Gemeinsam chatten

### 12. Fun Features 🎮
- [ ] **Personality Settings**: Verschiedene AI-Persönlichkeiten
- [ ] **Easter Eggs**: Versteckte Features
- [ ] **Achievements**: Gamification
- [ ] **AI Avatar**: Visueller Charakter
- [ ] **Sound Effects**: Audio-Feedback

## 📦 Deployment-Ready
- ✅ Vollständig portabel (SQLite, relative Pfade)
- ✅ Systemd-Service vorbereitet
- ✅ Produktive Config-Werte gesetzt
- ✅ Ollama-Integration stabil
- ✅ Polkit-Integration für sudo-lose Restarts

---

**Erstellt**: 30.10.2025
**Letztes Update**: 02.11.2025 (Feature-Liste erweitert)