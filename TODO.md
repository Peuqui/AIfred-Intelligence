# AIfred Intelligence - TODO Liste

## ✅ Erledigte Features (02.11.2025 - Session 4)

### History-Summarization mit intelligenter Kompression
- [x] **Vollständige Implementation der History-Kompression** ✅ DONE
  - Triggert bei 70% Context-Auslastung (konfigurierbar)
  - Komprimiert 3 Frage-Antwort-Paare → 1 Summary
  - FIFO-System: Max. 10 Summaries (älteste werden gelöscht)
  - Safety-Checks: Mindestens 1 aktuelles Gespräch bleibt sichtbar
  - Umfangreiches Logging mit Token-Metriken
  - 6:1 Kompressionsrate bei faktischen Inhalten
- [x] **Bug-Fixes** ✅ DONE
  - Vergleichsoperator-Bug behoben (< statt <=)
  - LLMMessage/LLMOptions Format korrigiert
  - HTTP-Timeout für Ollama hinzugefügt (60s)
  - Chat-Löschungs-Problem behoben

## 🚀 Next Features (Priorität)

### 1. TTS-Streaming (Text-to-Speech während AI noch schreibt)
**Phase 1** (Aktuell): Ohne Streaming (stabil)
**Phase 2** (Geplant): Satz-basiertes Streaming
**Phase 3** (Experimentell): Token-Streaming mit ML-Betonungskorrektur

### 2. Internationalisierung (i18n)
- [ ] Deutsche + Englische Prompts
- [ ] UI-Strings mehrsprachig
- [ ] Auto-Detection der User-Sprache
- [ ] Weitere Sprachen (FR, ES, IT)

### 3. Performance & Testing
- [ ] Unit-Tests für Context-Manager
- [ ] Integration-Tests für Cache-System
- [ ] Weitere Performance-Optimierungen

## 📦 Deployment-Ready
- ✅ Vollständig portabel (SQLite, relative Pfade)
- ✅ Systemd-Service vorbereitet
- ✅ Produktive Config-Werte gesetzt
- ✅ Ollama-Integration stabil

---

**Erstellt**: 30.10.2025
**Letztes Update**: 02.11.2025 (History-Kompression fertiggestellt)