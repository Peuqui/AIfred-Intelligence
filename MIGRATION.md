# AIfred Intelligence - Gradio → Reflex Migration

## Status: ✅ Phase 2.5 Complete - Gradio-Style UI Recreated

Stand: 2025-10-25 (UI Update)

---

## ✅ Was wurde portiert

### 1. Core Libraries (`aifred/lib/`)

Alle essentiellen Module von Gradio-Legacy wurden nach `aifred/lib/` portiert:

| Modul | Status | Beschreibung |
|-------|--------|--------------|
| `logging_utils.py` | ✅ Portiert | Debug-Logging, Console-Output |
| `prompt_loader.py` | ✅ Portiert | Lädt Prompts aus `/prompts/` |
| `agent_tools.py` | ✅ Portiert | Web Search, Scraping, Context-Building |
| `agent_core.py` | ✅ Portiert | Research Agent, Intent Detection, URL Rating |
| `formatting.py` | ✅ Portiert | Message Formatting |
| `message_builder.py` | ✅ Portiert | Chat History Management |
| `config.py` | ✅ Portiert | Configuration |

### 2. Prompts (`prompts/`)

✅ **Alle Prompts sind bereits vorhanden und kompatibel:**
- `decision_making.txt` - Mit lokalen Aktivitäten-Fix
- `url_rating.txt` - Mit generischer lokaler Relevanz
- `system_rag.txt` - Keine URLs in Inline-Zitaten
- `query_optimization.txt`
- `intent_detection.txt`
- `followup_intent_detection.txt`

### 3. Backend-System (`aifred/backends/`)

✅ **Bereits in Reflex vorhanden:**
- `base.py` - Abstract LLMBackend
- `ollama.py` - Ollama Adapter
- `vllm.py` - vLLM Adapter (OpenAI-kompatibel)
- `__init__.py` - BackendFactory

---

## 🔄 Was noch fehlt

### Phase 2: Research Integration ✅ COMPLETE

- [x] **Web Research in Reflex State integriert**
  - ✅ Research Cache Management (class-level Dict mit Lock)
  - ✅ `AIState.send_message()` mit Research-Integration
  - ✅ Decision-Making Logic (Automatik-Modus)
  - ✅ ThreadPoolExecutor für sync agent_core → async Reflex
  - ✅ Debug-Sync zwischen lib console und Reflex State

- [x] **Settings Management UI**
  - ✅ Automatik-LLM Auswahl (Dropdown)
  - ✅ Research Mode (none/quick/deep/automatik)
  - ✅ Temperature Slider
  - ✅ Haupt-LLM vs. Automatik-LLM Trennung

### Phase 2.5: Gradio-Style UI Recreation ✅ COMPLETE

- [x] **2-Column Layout wie Gradio**
  - ✅ Left Column: Audio placeholder, Text input, Research mode radio, LLM parameters accordion
  - ✅ Right Column: User/AI text display, TTS controls placeholder, Chat history
  - ✅ Header mit Titel und Subtitle
  - ✅ Bottom: Debug Console (accordion, 400px height)
  - ✅ Bottom: Settings Accordion (Backend, Haupt-LLM, Automatik-LLM)

- [x] **UI Components Functional**
  - ✅ Text input with disabled state während generation
  - ✅ Research mode radio buttons mit emoji icons
  - ✅ Temperature slider mit dynamischer Anzeige
  - ✅ Chat history display mit user/AI bubbles
  - ✅ Debug console mit auto-refresh toggle
  - ✅ Responsive 2-column grid layout

- [x] **Styling wie Gradio**
  - ✅ Ähnliche Farben (#2563eb für User, #e5e7eb für AI)
  - ✅ Rounded corners, padding, spacing
  - ✅ Background colors (#f9fafb für readonly fields, #f3f4f6 für page)
  - ✅ Emoji icons für bessere UX

### Phase 3: Audio Processing (Optional)

- [ ] **Audio Input (STT)**
  - Whisper STT Integration
  - Microphone recording UI
  - Audio waveform display

- [ ] **Audio Output (TTS)**
  - Edge TTS Integration
  - Audio playback controls
  - Voice selection UI

### Phase 4: Advanced Features

- [ ] Chat History Persistence (SQLite)
- [ ] Multi-Session Support
- [ ] Model Download/Management UI
- [ ] Performance Metrics Dashboard

---

## 📁 Verzeichnisstruktur

```
AIfred-Intelligence/
├── aifred/
│   ├── backends/           # ✅ Multi-Backend System
│   │   ├── base.py
│   │   ├── ollama.py
│   │   └── vllm.py
│   ├── lib/                # ✅ NEU - Portierte Gradio-Module
│   │   ├── __init__.py
│   │   ├── logging_utils.py
│   │   ├── prompt_loader.py
│   │   ├── agent_tools.py
│   │   ├── agent_core.py
│   │   ├── formatting.py
│   │   ├── message_builder.py
│   │   └── config.py
│   ├── aifred.py           # Reflex UI Components
│   └── state.py            # Reflex State Management
├── prompts/                # ✅ Alle Prompts vorhanden
│   ├── decision_making.txt
│   ├── url_rating.txt
│   ├── system_rag.txt
│   └── ...
├── gradio-legacy/          # ✅ Referenz-Code (alte Version)
│   ├── aifred_intelligence.py
│   ├── agent_core.py
│   └── ...
└── logs/                   # Debug-Logs
    └── aifred_debug.log
```

---

## 🔧 Technische Details

### Import-Struktur

**Alte Gradio-Version:**
```python
from lib.agent_core import perform_agent_research
from lib.logging_utils import debug_print
```

**Neue Reflex-Version:**
```python
from aifred.lib import perform_agent_research
from aifred.lib import debug_print
```

### Logging-System

**Console-Output für Reflex UI:**
```python
from aifred.lib import console_print, get_console_messages

# Schreiben
console_print("🌐 Web-Scraping startet...")

# Lesen (in Reflex State)
messages = get_console_messages()  # Liste aller Messages
```

**Debug-Log-File:**
- Pfad: `/home/mp/Projekte/AIfred-Intelligence/logs/aifred_debug.log`
- Automatische Rotation bei >1 MB
- Timestamp-Format: `HH:MM:SS.mmm`

### Prompt-System

**Prompts laden:**
```python
from aifred.lib import get_decision_making_prompt

prompt = get_decision_making_prompt(
    user_text="Aktivitäten in Kassel?",
    cache_metadata=""
)
```

### Web Research Integration (Phase 2)

**Research in Reflex State:**
```python
# aifred/state.py
class AIState(rx.State):
    # Research Settings
    research_mode: str = "automatik"  # "quick", "deep", "automatik", "none"
    automatik_model: str = "qwen3:4b"  # Für Decision/Query-Opt/URL-Rating
    session_id: str = ""

    # Research Cache (class-level, shared)
    _research_cache: Dict = {}
    _cache_lock: threading.Lock = threading.Lock()

    async def send_message(self):
        """Send message with optional web research"""
        # Phase 1: Research (wenn aktiviert)
        if self.research_mode != "none":
            # Run agent_core.perform_agent_research() in ThreadPool
            with ThreadPoolExecutor() as executor:
                research_result = executor.submit(
                    perform_agent_research,
                    user_text=user_msg,
                    mode=self.research_mode,
                    model_choice=self.selected_model,
                    automatik_model=self.automatik_model,
                    history=self.chat_history,
                    session_id=self.session_id,
                    ...
                ).result()

            # Sync debug messages from lib console
            self.sync_debug_from_lib()

        # Phase 2: LLM Response (with or without RAG context)
        if research_result and research_result.get('ai_response'):
            # Research lieferte RAG-Antwort
            full_response = research_result['ai_response']
        else:
            # Normaler Chat ohne Research
            full_response = await backend.chat_stream(...)
```

**Web Research Funktionen:**
```python
from aifred.lib import search_web, scrape_webpage, build_context

# Web-Suche (Multi-API Fallback: Brave → Tavily → SearXNG)
results = search_web("Wetter Berlin")

# URL scrapen (Trafilatura + Playwright Fallback)
content = scrape_webpage("https://wetter.com/berlin")

# Context bauen (für RAG)
context = build_context(user_text, tool_results)
```

**UI Settings:**
- Research Mode: none / quick (3 URLs) / deep (7 URLs) / automatik (KI entscheidet)
- Automatik-LLM: Separate LLM für Decision-Making, Query-Opt, URL-Rating
- Haupt-LLM: Für finale Antwort-Generierung

---

## 🧪 Testing

### Import-Tests

```bash
# Aktiviere venv
source venv/bin/activate

# Teste Imports
python -c "from aifred.lib import debug_print; print('✅ OK')"
python -c "from aifred.lib import search_web; print('✅ OK')"
python -c "from aifred.lib import perform_agent_research; print('✅ OK')"
```

### Reflex-Server starten

```bash
source venv/bin/activate
reflex run
```

Öffne: `http://192.168.0.252:3002`

---

## 📝 Nächste Schritte

### ✅ Phase 2 Complete - Bereit zum Testen!

Die Web-Research-Integration ist vollständig portiert. Nächste Schritte:

1. **Ollama starten & Testen**
   ```bash
   # Ollama starten
   systemctl start ollama

   # Models prüfen
   ollama list

   # Benötigte Models pullen
   ollama pull qwen3:8b
   ollama pull qwen3:4b

   # Reflex starten
   source venv/bin/activate
   reflex run
   ```

2. **Test-Szenarien**
   - **Research Mode: none** → Normaler Chat (kein Web Search)
   - **Research Mode: quick** → 3 URLs scraped
   - **Research Mode: deep** → 7 URLs scraped (mit Fallback)
   - **Research Mode: automatik** → KI entscheidet (Decision-Making)

3. **Debug-Console beobachten**
   - Web-Scraping-Fortschritt
   - URL-Rating Scores
   - Cache-Hits/Misses
   - LLM Performance Stats

### Phase 3: Optional Features

4. **Audio Processing portieren**
   - Whisper STT Integration (Voice Input)
   - Edge TTS Integration (Voice Output)
   - Audio UI Components

5. **Advanced Features**
   - Chat History Persistence (SQLite/Redis)
   - Multi-User Support
   - Model Download UI
   - Performance Dashboard

---

## 🔗 Referenzen

- **Gradio-Legacy Branch:** `origin/gradio-legacy`
- **Gradio-Legacy Code:** `/home/mp/Projekte/AIfred-Intelligence/gradio-legacy/`
- **Reflex Docs:** https://reflex.dev/docs/getting-started/introduction/
- **GitHub Repo:** https://github.com/Peuqui/AIfred-Intelligence

---

## ⚠️ Bekannte Warnungen

### Reflex Deprecation Warning
```
DeprecationWarning: rx.Base has been deprecated in version 0.8.15.
<class 'aifred.state.ChatMessage'> is subclassing rx.Base.
```

**Fix:** Migriere `ChatMessage` zu `pydantic.BaseModel` statt `rx.Base`

```python
# Vorher (alt)
class ChatMessage(rx.Base):
    role: str
    content: str

# Nachher (neu)
from pydantic import BaseModel

class ChatMessage(BaseModel):
    role: str
    content: str
```

---

**Erstellt:** 2025-10-25
**Autor:** Claude Code
**Version:** Phase 1 - Library Portierung Complete
