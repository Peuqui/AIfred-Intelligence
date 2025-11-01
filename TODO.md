# AIfred Intelligence - TODO Liste

## 🚀 Priorität: Hoch

### History-Summarization (Automatische Context-Kompression)

**Zweck**: Verhindert Context-Overflow bei langen Konversationen

**Triggerpunkt**:
- Wenn `estimated_tokens(system_prompt + history + user_message) > context_window_limit`
- Berechnung VOR dem LLM-Call

**Strategie**:
1. **Berechne benötigte Token-Reduktion**:
   ```python
   overflow_tokens = estimated_tokens - context_window_limit
   target_reduction = overflow_tokens + buffer (z.B. 2048 Tokens Reserve)
   ```

2. **Summarize älteste History-Einträge**:
   - Nimm älteste N Messages (paarweise: User + AI)
   - Sende an Automatik-LLM: "Fasse diese Konversation zusammen (max X Tokens)"
   - Ersetze diese Messages durch 1 System-Message mit Summary

3. **Iterativ reduzieren bis Platz frei**:
   - Wenn ein Summary nicht reicht: Nächste N Messages summarizen
   - Stopp wenn: `estimated_tokens <= context_window_limit`

4. **Behalte neueste Messages unverändert**:
   - Letzten 2-4 Messages (1-2 Runden) nie summarizen
   - Wichtig für unmittelbaren Kontext

**Beispiel**:
```
Vor Summarization (10.000 Tokens):
1. User: "Wetter Berlin?"
2. AI: "15°C, sonnig..."
3. User: "Und München?"
4. AI: "18°C, bewölkt..."
5. User: "Unterschied?"
6. AI: "München 3°C wärmer..."
[Context Limit erreicht!]

Nach Summarization (4.000 Tokens):
1. System: "User fragte Wetter Berlin (15°C) und München (18°C), Unterschied besprochen"
2. User: "Unterschied?"
3. AI: "München 3°C wärmer..."
[6.000 Tokens gespart!]
```

**Implementation-Details**:
- Funktion: `async def summarize_history_if_needed(messages, context_limit) -> messages`
- Aufruf: In `agent_core.py` VOR dem LLM-Call
- LLM: Automatik-LLM verwenden (schnell + günstig)
- Prompt: "Fasse folgende Konversation prägnant zusammen (max {target_tokens} Tokens)"

**Vorteile**:
- ✅ Keine FIFO-Verluste mehr (ältere Infos bleiben als Summary)
- ✅ Mehr Platz für neue Research-Daten
- ✅ LLM behält Kontext über längere Sessions

**Nachteile**:
- ⚠️ Informationsverlust bei Details
- ⚠️ Extra LLM-Call (~2-3 Sekunden)
- ⚠️ Komplexität erhöht

---

## 🔧 Weitere TODOs

### Performance

#### ✅ LLM Pre-Loading (IMPLEMENTIERT - 01.11.2025)

**Status**: ✅ Implementiert und getestet

**Ergebnis**:
- Performance-Gewinn: **23.3 Sekunden (35% schneller)** bei Cache-Hits
- Automatik-LLM: Preload beim App-Start
- Haupt-LLM: Preload während Web-Scraping (parallel)

**Details**: Siehe Commits vom 01.11.2025

---

- [x] Paralleles Scraping optimieren (Timeout-Handling) ✅ DONE (01.11.2025 - Globaler Timeout 60s, keine API-spezifischen Overrides)
- [ ] Cache-Warming beim App-Start
- [x] **LLM Pre-Loading implementieren** ✅ DONE (01.11.2025)

### UI/UX
- [x] Quellen-Links in LLM-Antwort unterdrücken (redundant) ✅ DONE (vorherige Session)
- [x] **Progress-Indicator für lange Scraping-Vorgänge** ✅ DONE (01.11.2025)
  - **Status**: Vollständig implementiert
  - **Features**:
    - Phase 1: Automatik-Entscheidung (pulsierend)
    - Phase 2: Web-Scraping (Fortschrittsbalken X/Y URLs + Fehleranzahl)
    - Phase 3: Generiere Antwort (pulsierend)
    - Funktioniert auch bei Cache-Hit korrekt
  - **Dateien**: `aifred/aifred.py`, `aifred/state.py`, `aifred/lib/agent_core.py`

### Internationalisierung (i18n)

**Zweck**: AIfred für internationale Nutzer verfügbar machen

**Vorteile**:
- ✅ Englische/mehrsprachige User können AIfred nutzen
- ✅ Bessere LLM Performance pro Sprache (EN prompts für EN-only models)
- ✅ Open-Source ready für internationale Community
- ✅ Flexibilität bei LLM-Wahl

**Struktur**:
```
prompts/
├── de/
│   ├── system_rag.txt
│   ├── system_rag_cache_hit.txt
│   ├── decision_making.txt
│   └── ...
├── en/
│   ├── system_rag.txt
│   ├── system_rag_cache_hit.txt
│   ├── decision_making.txt
│   └── ...
└── config.yaml  # Language: de/en/auto
```

**Implementation**:
- [ ] Prompt-Ordnerstruktur nach Sprachen aufteilen
- [ ] Language-Parameter in Config (de/en/auto)
- [ ] Auto-Detection: User-Sprache erkennen (aus erster Frage)
- [ ] UI-Strings internationalisieren (Reflex i18n)
- [ ] Debug-Messages mehrsprachig

**Phasen**:
1. Phase 1: Deutsche + Englische Prompts (Hauptsprachen)
2. Phase 2: UI-Strings mehrsprachig
3. Phase 3: Auto-Detection implementieren
4. Phase 4: Weitere Sprachen (FR, ES, IT, etc.)

### Code Quality
- [ ] Unit-Tests für Context-Manager
- [ ] Integration-Tests für Cache-System

---

**Erstellt**: 30.10.2025
**Letztes Update**: 01.11.2025 (Session 2: Progress-Indicator, UI-Fixes, Cache-Hit Progress-Flow)
