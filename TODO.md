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

#### LLM Pre-Loading (Paralleles Model-Loading)

**Zweck**: Zeit sparen durch paralleles Laden von Haupt-LLM während Automatik-LLM arbeitet

**Problem**:
- Aktuell: Automatik-LLM fertig → dann erst Haupt-LLM laden → User wartet
- Legacy hatte das besser: Haupt-LLM lädt bereits im Hintergrund

**Strategie**:
1. **Während Intent-Detection läuft**: Haupt-LLM vorwärmen (dummy call)
2. **Während Query-Optimizer läuft**: Haupt-LLM im Speicher halten
3. **Während URL-Rating läuft**: Haupt-LLM ist bereits geladen
4. **Während Web-Scraping läuft**: Haupt-LLM bereit für sofortigen Einsatz

**Implementation**:
```python
# Beispiel: Parallel Pre-Loading
import asyncio

async def preload_haupt_llm():
    """Dummy call to load model into VRAM"""
    await llm_client.chat(
        model=haupt_model,
        messages=[{"role": "user", "content": "test"}],
        options={"num_predict": 1}  # Nur 1 Token generieren
    )

# Im Agent-Core:
# Starte Pre-Loading parallel zu Automatik-Aufgaben
asyncio.create_task(preload_haupt_llm())  # Fire & forget
```

**Zeiteinsparung**:
- Model-Loading: ~3-5 Sekunden (bei großen Models wie qwen3:8b)
- Gesamtersparnis: 3-5 Sekunden pro Web-Recherche

**Legacy-Referenz**: `gradio-legacy/agent_core.py` - hatte Model-Warming

---

- [ ] Paralleles Scraping optimieren (Timeout-Handling)
- [ ] Cache-Warming beim App-Start
- [ ] **LLM Pre-Loading implementieren** (siehe oben)

### UI/UX
- [ ] Quellen-Links in LLM-Antwort unterdrücken (redundant)
- [ ] Progress-Indicator für lange Scraping-Vorgänge

### Code Quality
- [ ] Unit-Tests für Context-Manager
- [ ] Integration-Tests für Cache-System

---

**Erstellt**: 30.10.2025
**Letztes Update**: 30.10.2025
