# Architektur: Agentische Erweiterungen für Voice Assistant

**Version:** 1.0
**Datum:** 2025-10-13
**Status:** Planung

---

## 🎯 Ziel

Erweitere den AI Voice Assistant um agentische Fähigkeiten:
- 🔍 **Web-Suche** - Echtzeit-Informationen aus dem Internet (DuckDuckGo)
- 📰 **Internet-Recherche** - Multi-Source-Aggregation mit Web-Scraping
- 🤖 **Interaktiver Agent-Modus** - User wählt zwischen Web-Recherche und eigenem Wissen
- 🎚️ **Recherche-Tiefe wählbar** - Schnell (nur DuckDuckGo) oder Ausführlich (+ Web-Scraping)
- 🧠 **Context-Enrichment** - Anreicherung von Antworten mit aktuellen Daten
- 🔒 **Privacy-First** - DuckDuckGo als privacy-freundliche Suchmaschine

---

## 📋 Bestehende Architektur

### Aktuelle Pipeline (3 Stufen)

```
[Audio Input] → [STT (Whisper)] → [AI Inference (Ollama)] → [TTS (Edge/Piper)] → [Audio Output]
     ↓                ↓                      ↓                        ↓
  Audio File      User Text              AI Text                Audio File
  + Zeit          + Zeit                 + Zeit                 + Zeit
```

**Vorteile:**
- ✅ Klare Trennung der Verarbeitungsstufen
- ✅ Performance-Tracking auf jeder Stufe
- ✅ Robuste State-Management
- ✅ Gradio Event-Chaining

**Limitierungen für Agenten:**
- ❌ Keine Tool-Integration
- ❌ Keine Web-Suche
- ❌ Keine Multi-Step-Reasoning
- ❌ Keine externe Daten-Quellen

---

## 🏗️ Geplante Architektur: Agentische Pipeline

### Neue 5-Stufen-Pipeline mit Agent-Layer

```
[Audio Input]
    ↓
[STT (Whisper)] ────────────────→ User Text + STT Zeit
    ↓
[Intent Detection] ──────────────→ Intent: direct_answer | web_search | research | tool_call
    ↓
    ├─ [Direct Path] ────────────→ AI Inference (Standard)
    │
    ├─ [Agent Path]
    │   ├─ [Tool Selection] ─────→ Welche Tools benötigt?
    │   ├─ [Tool Execution] ─────→ Web Search, API Calls, etc.
    │   │   ├─ Web Search (DuckDuckGo/SearxNG)
    │   │   ├─ Web Scraping (BeautifulSoup/Playwright)
    │   │   ├─ Fact Checking (Multi-Source)
    │   │   └─ Data Aggregation
    │   └─ [Context Building] ───→ Kontext aus Tool-Ergebnissen
    │
    └─ [AI Inference + Context] ─→ AI Text + Inferenz Zeit
         ↓
[TTS (Edge/Piper)] ──────────────→ Audio File + TTS Zeit
    ↓
[Audio Output]
```

---

## 💡 Interaktiver Agent-Modus (User-Choice)

### Konzept: User entscheidet

Statt automatischer Agent-Aktivierung bieten wir dem User **Wahlmöglichkeiten** an:

#### **Flow 1: Agent-Nutzung wählen**

```
User: "Was sind die neuesten Entwicklungen in der KI?"
  ↓
[Intent Detection erkennt: Könnte aktuelle Infos benötigen]
  ↓
🤖 Agent fragt nach:
  "Möchtest du, dass ich dazu im Web recherchiere,
   oder soll ich mit meinem vorhandenen Wissen antworten?

   [🌐 Web-Recherche] [🧠 Eigenes Wissen]"
  ↓
User klickt: [🌐 Web-Recherche]
  ↓
→ Weiter zu Flow 2 (Recherche-Tiefe)
```

#### **Flow 2: Recherche-Tiefe wählen**

```
🤖 Agent fragt nach:
  "Welche Art von Recherche?

   [⚡ Schnell (nur DuckDuckGo, ~3s)]
   [🔍 Ausführlich (DuckDuckGo + Web-Scraping, ~10s)]"
  ↓
User wählt: [🔍 Ausführlich]
  ↓
Agent arbeitet:
  1. DuckDuckGo Suche → Top 3-5 URLs
  2. Web Scraping → Content von URLs extrahieren
  3. Context Building → AI-freundlich formatieren
  4. AI Inference → Antwort mit Quellen
  ↓
Antwort: "Basierend auf 3 Quellen (Wikipedia, ArXiv, Tech-Blog):
          Die neuesten KI-Entwicklungen umfassen..."
```

#### **Alternative: User wählt "Eigenes Wissen"**

```
User klickt: [🧠 Eigenes Wissen]
  ↓
Standard-Pipeline:
  AI Inference (ohne Web-Context) → Antwort aus Ollama-Wissen
  ↓
Antwort: "Basierend auf meinem Wissen bis Januar 2025:
          KI-Entwicklungen umfassen..."
```

### UI-Implementierung (Gradio)

#### **Modus-Auswahl in Settings (Sauber & Übersichtlich)**

User wählt den Recherche-Modus in den Einstellungen - spart Platz im Haupt-Interface!

```python
# In den Einstellungen (ganz unten mit den anderen Settings)
with gr.Row():
    with gr.Column():
        gr.Markdown("### 🤖 Agent-Einstellungen")

        # Recherche-Modus Auswahl (Radio-Buttons, immer sichtbar)
        research_mode = gr.Radio(
            choices=[
                "🧠 Eigenes Wissen (schnell)",
                "⚡ Web-Suche Schnell (mittel)",
                "🔍 Web-Suche Ausführlich (langsam)",
                "🤝 Interaktiv (variabel)"
            ],
            value="⚡ Web-Suche Schnell (mittel)",  # Default: Aktuell & schnell
            label="🎯 Recherche-Modus",
            info="Wähle, wie der Assistant Fragen beantwortet"
        )

        # Accordion mit Erklärungen (zugeklappt, optional)
        with gr.Accordion("ℹ️ Was bedeuten die Modi?", open=False):
            gr.Markdown("""
            **🧠 Eigenes Wissen** - Schnell, offline, AI-Wissen (Stand: Jan 2025)

            **⚡ Web-Suche Schnell** - Mittel, DuckDuckGo (1 Quelle), privacy-freundlich

            **🔍 Web-Suche Ausführlich** - Langsam, 3-5 Quellen analysiert, gründlich

            **🤝 Interaktiv** - Variabel, du wählst bei jeder Frage neu

            ---

            **Weitere Details:**
            - **Search Engine:** DuckDuckGo (keine Cookies, kein Tracking)
            - **Web-Scraping:** Nur bei "Ausführlich"-Modus
            - **Offline-Fähigkeit:** Nur bei "Eigenes Wissen"
            """)
```

**Vorteile dieser Lösung:**
- ✅ **Kompakt:** Accordion standardmäßig zugeklappt → spart Platz
- ✅ **Mobile-optimiert:** Nur 1 Zeile (Accordion-Header) wenn zu
- ✅ **Informativ:** Alle Details verfügbar wenn User sie braucht
- ✅ **Haupt-UI clean:** Nur Radio-Buttons sichtbar
- ✅ **Settings persistiert:** In `assistant_settings.json`
- ✅ **Intuitiv:** Standard-Pattern (Accordion) bekannt aus vielen UIs
- ✅ **4 Modi:** Inkl. "Interaktiv" für maximale Flexibilität

#### **Submit mit gewähltem Modus**

```python
# Audio Submit verwendet den gewählten Modus
audio_submit.click(
    # Stufe 1: STT
    chat_audio_step1_transcribe,
    inputs=[audio_input, whisper_model],
    outputs=[user_text, stt_time_state]
).then(
    # Stufe 2: AI mit Modus-basiertem Routing
    chat_audio_step2_with_mode,
    inputs=[
        user_text,
        stt_time_state,
        current_mode,  # ← Übergibt gewählten Modus
        model,
        history
    ],
    outputs=[ai_text, history, inference_time_state]
).then(
    # Stufe 3: TTS
    chat_audio_step3_tts,
    inputs=[ai_text, ...],
    outputs=[audio_output, history]
)
```

#### **Status-Anzeige während Recherche**

```python
agent_status = gr.Markdown("🤖 Agent Status: Bereit", visible=True)

# Updates während Recherche:
# "🤖 Recherchiere mit DuckDuckGo... 🔍"
# "🤖 Scrape 3 Webseiten... 📄"
# "🤖 Baue Context für AI... 🧩"
# "🤖 Generiere Antwort... 💬"
```

### Vorteile des Interaktiven Modus

1. **User-Kontrolle**
   - User entscheidet bewusst über Web-Zugriff
   - Transparenz über Datenquellen

2. **Performance-Wahl**
   - Schnelle Antwort vs. Ausführliche Recherche
   - User kann Zeit-Komplexität-Tradeoff selbst wählen

3. **Privacy-Bewusstsein**
   - User weiß, wann externe APIs kontaktiert werden
   - Opt-In statt Opt-Out

4. **Fallback-Sicherheit**
   - Bei Web-API-Fehlern: Immer Fallback zu "Eigenes Wissen"
   - Keine hängenden Anfragen

5. **Learning Experience**
   - User lernt, wann Agent-Recherche sinnvoll ist
   - Feedback über Recherche-Qualität

### Settings: Persistierung & Laden

```python
# Default Settings erweitern
DEFAULT_SETTINGS = {
    "model": "llama3.2:3b",
    "voice": "Deutsch (Katja)",
    "tts_speed": 1.25,
    "enable_tts": True,
    "tts_engine": "Edge TTS (Cloud, beste Qualität)",
    "whisper_model": "base (142MB, schnell, multilingual)",
    # Neu: Agent Settings
    "research_mode": "⚡ Web-Suche Schnell (mittel)"  # Default: Aktuell & schnell
}

def save_settings(..., research_mode):
    """Speichert Settings inkl. research_mode"""
    settings = {
        ...
        "research_mode": research_mode
    }
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

# Settings-Change Handler
research_mode.change(
    save_settings,
    inputs=[model, voice, tts_speed, enable_tts, tts_engine, whisper_model, research_mode]
)
```

**Settings werden gespeichert in:** `assistant_settings.json`

```json
{
  "model": "llama3.2:3b",
  "voice": "Deutsch (Katja)",
  "tts_speed": 1.25,
  "enable_tts": true,
  "tts_engine": "Edge TTS (Cloud, beste Qualität)",
  "whisper_model": "base (142MB, schnell, multilingual)",
  "research_mode": "⚡ Web-Suche Schnell (mittel)"
}
```

### Technische Implementation

#### **Modus-basiertes Routing (Einfach & Klar)**

Da User den Modus in Settings wählt, brauchen wir keine Intent-Detection mehr!

```python
def chat_audio_step2_with_mode(
    user_text: str,
    stt_time: float,
    research_mode: str,  # Gewählter Modus aus Settings
    model_choice: str,
    history: list
) -> tuple:
    """
    Routet basierend auf gewähltem Modus (aus Settings)

    Returns:
        (ai_text, history, inference_time, agent_time)
    """

    # Parse Modus
    if "Eigenes Wissen" in research_mode:
        # Standard-Pipeline ohne Agent
        return chat_audio_step2_ai(
            user_text, stt_time, model_choice, None, None, True, "Edge TTS", history
        )

    elif "Schnell" in research_mode:
        # Web-Suche nur mit DuckDuckGo
        return perform_agent_research(user_text, stt_time, "quick", model_choice, history)

    elif "Ausführlich" in research_mode:
        # Web-Suche + Web-Scraping
        return perform_agent_research(user_text, stt_time, "deep", model_choice, history)

    else:
        # Fallback: Eigenes Wissen
        return chat_audio_step2_ai(
            user_text, stt_time, model_choice, None, None, True, "Edge TTS", history
        )
```

**Vorteil:** Keine komplexe Intent-Detection nötig - User hat bereits gewählt!

#### **Research-Execution (Schnell vs. Ausführlich)**

```python
def perform_agent_research(
    user_text: str,
    research_depth: str,  # "quick" | "deep"
    model_choice: str,
    history: list
) -> tuple:
    """
    Führt Agent-Recherche durch
    """
    agent_start = time.time()
    tool_results = []

    # 1. DuckDuckGo Suche (immer)
    ddg_tool = DuckDuckGoSearchTool()
    ddg_result = ddg_tool.execute(user_text)
    tool_results.append(ddg_result)

    # 2. Web Scraping (nur bei "deep")
    if research_depth == "deep" and ddg_result.get('urls'):
        scraper_tool = WebScraperTool()
        for url in ddg_result['urls'][:3]:  # Top 3 URLs
            scraped = scraper_tool.execute(url)
            if scraped:
                tool_results.append(scraped)

    # 3. Context Building
    context = build_context(user_text, tool_results)

    # 4. AI Inference
    messages = [{'role': 'system', 'content': context}]
    messages.append({'role': 'user', 'content': user_text})

    inference_start = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - inference_start

    agent_time = time.time() - agent_start

    ai_text = response['message']['content']

    # History mit Research-Info
    sources_count = len(tool_results)
    research_label = "Schnell" if research_depth == "quick" else "Ausführlich"
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, {research_label}, {sources_count} Quellen)"

    history.append([user_with_time, ai_text])

    return ai_text, history, inference_time, agent_time
```

---

## 🔧 Technische Komponenten

### 1. Intent Detection Layer

**Funktion:** Erkennt, ob die User-Anfrage Tools benötigt

**Implementation:**
```python
def detect_intent(user_text: str) -> dict:
    """
    Analysiert User-Anfrage und erkennt Intent

    Returns:
        {
            'intent': 'direct_answer' | 'web_search' | 'research' | 'tool_call',
            'tools_needed': ['web_search', 'scraper', ...],
            'keywords': ['Python', 'Tutorial', ...],
            'reasoning': 'User fragt nach aktuellen Informationen...'
        }
    """
```

**Erkennungs-Strategien:**
1. **Keyword-basiert** (schnell)
   - "aktuelle", "neueste", "heute", "jetzt" → Web-Suche
   - "suche", "finde", "recherchiere" → Research
   - "wetter", "börse", "nachrichten" → API-Call

2. **LLM-basiert** (präzise)
   - Kleines lokales Modell (llama3.2:1b) klassifiziert Intent
   - Prompt: "Brauch ich Web-Suche für: {user_text}? Antworte nur ja/nein"

3. **Hybrid** (empfohlen)
   - Keywords als Fast-Path
   - LLM für unklare Fälle

**Performance:**
- Target: < 100ms für Intent Detection
- Keywords: ~10ms
- LLM (1b): ~50-100ms

---

### 2. Agent-Tool-System

#### Tool-Architektur

```python
class BaseTool:
    """Base class für alle Agent-Tools"""
    def __init__(self):
        self.name = ""
        self.description = ""

    def execute(self, query: str, **kwargs) -> dict:
        """Führt Tool aus und gibt Ergebnis zurück"""
        raise NotImplementedError

class WebSearchTool(BaseTool):
    """Web-Suche via DuckDuckGo oder SearxNG"""

class WebScraperTool(BaseTool):
    """Extrahiert Content von Webseiten"""

class FactCheckTool(BaseTool):
    """Verifiziert Informationen aus mehreren Quellen"""

class NewsAggregatorTool(BaseTool):
    """Sammelt aktuelle Nachrichten zu einem Thema"""
```

#### Verfügbare Tools (Phase 1)

##### 1. **WebSearchTool** - DuckDuckGo Instant Answer API

**Quelle:** https://api.duckduckgo.com/
**Vorteil:** Kostenlos, keine API-Keys, Rate-Limit-freundlich
**Nachteil:** Begrenzte Ergebnisse (meist nur Top-1 Antwort)

```python
import requests

def search_duckduckgo(query: str) -> dict:
    """
    DuckDuckGo Instant Answer API

    Returns:
        {
            'answer': 'Python is a programming language...',
            'abstract': 'Längerer Text...',
            'url': 'https://wikipedia.org/...',
            'source': 'Wikipedia'
        }
    """
    url = "https://api.duckduckgo.com/"
    params = {
        'q': query,
        'format': 'json',
        'no_html': 1,
        'skip_disambig': 1
    }
    response = requests.get(url, params=params, timeout=5)
    return response.json()
```

**Use Cases:**
- Faktenfragen: "Was ist Python?"
- Definitionen: "Was bedeutet Machine Learning?"
- Schnelle Infos: "Hauptstadt von Frankreich?"

**Performance:** ~500ms - 2s

---

##### 2. **WebSearchTool** - SearxNG (Self-Hosted, optional)

**Quelle:** Eigene SearxNG-Instanz oder öffentliche Instanz
**Vorteil:** Privacy, aggregiert mehrere Suchmaschinen, volle Kontrolle
**Nachteil:** Benötigt Setup/Hosting

```python
def search_searxng(query: str, searx_url: str = "https://searx.be") -> list:
    """
    SearxNG Meta-Suchmaschine

    Returns:
        [
            {
                'title': 'Python Tutorial',
                'url': 'https://...',
                'content': 'Beschreibung...',
                'engine': 'google'
            },
            ...
        ]
    """
    url = f"{searx_url}/search"
    params = {
        'q': query,
        'format': 'json',
        'categories': 'general'
    }
    response = requests.get(url, params=params, timeout=10)
    return response.json()['results']
```

**Use Cases:**
- Multi-Source-Recherche
- Privacy-sensitive Anfragen
- Aggregierte Ergebnisse

**Performance:** ~2-5s (aggregiert mehrere Quellen)

---

##### 3. **WebScraperTool** - BeautifulSoup (leichtgewichtig)

**Funktion:** Extrahiert Text-Content von Webseiten

```python
from bs4 import BeautifulSoup
import requests

def scrape_webpage(url: str, max_chars: int = 5000) -> dict:
    """
    Extrahiert Haupt-Content einer Webseite

    Returns:
        {
            'title': 'Seitentitel',
            'content': 'Haupttext...',
            'url': 'https://...',
            'word_count': 1234
        }
    """
    response = requests.get(url, timeout=10, headers={
        'User-Agent': 'Mozilla/5.0 (AI Voice Assistant Bot)'
    })
    soup = BeautifulSoup(response.text, 'html.parser')

    # Entferne Skripte, Styles, Navigation
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()

    # Extrahiere Text
    text = soup.get_text(separator=' ', strip=True)

    # Kürze auf max_chars
    if len(text) > max_chars:
        text = text[:max_chars] + "..."

    return {
        'title': soup.title.string if soup.title else '',
        'content': text,
        'url': url,
        'word_count': len(text.split())
    }
```

**Use Cases:**
- Content-Extraktion von Such-Ergebnissen
- Artikel-Zusammenfassung
- Detailierte Recherche

**Performance:** ~1-3s pro Seite

**Limitierungen:**
- Keine JavaScript-Rendering (statisches HTML only)
- Blockiert von manchen Sites (Rate-Limiting, Cloudflare)

---

##### 4. **NewsAggregatorTool** - NewsAPI (optional, API-Key)

**Quelle:** https://newsapi.org/ (Free Tier: 100 requests/day)

```python
def fetch_news(query: str, api_key: str, language: str = 'de') -> list:
    """
    Holt aktuelle Nachrichten zu einem Thema

    Returns:
        [
            {
                'title': 'Nachrichtentitel',
                'description': 'Kurzbeschreibung...',
                'url': 'https://...',
                'source': 'Tagesschau',
                'published_at': '2025-10-13T10:00:00Z'
            },
            ...
        ]
    """
    url = "https://newsapi.org/v2/everything"
    params = {
        'q': query,
        'apiKey': api_key,
        'language': language,
        'sortBy': 'publishedAt',
        'pageSize': 5
    }
    response = requests.get(url, params=params, timeout=10)
    return response.json()['articles']
```

**Alternative (kostenlos):** RSS-Feeds von Tagesschau, Spiegel, etc.

---

### 3. Context Builder

**Funktion:** Erstellt optimierten Kontext für AI aus Tool-Ergebnissen

```python
def build_context(user_text: str, tool_results: list) -> str:
    """
    Baut strukturierten Kontext für AI aus Tool-Ergebnissen

    Args:
        user_text: Ursprüngliche User-Frage
        tool_results: Liste von Tool-Outputs

    Returns:
        Formatierter Kontext-String für AI
    """
    context = f"# User-Frage: {user_text}\n\n"
    context += "# Recherche-Ergebnisse:\n\n"

    for i, result in enumerate(tool_results, 1):
        context += f"## Quelle {i}: {result.get('source', 'Unbekannt')}\n"
        context += f"{result.get('content', '')}\n\n"

    context += "# Aufgabe:\n"
    context += "Beantworte die User-Frage basierend auf den Recherche-Ergebnissen. "
    context += "Zitiere Quellen wenn möglich. Sei präzise und fasse zusammen.\n"

    return context
```

**Strategie:**
1. Tool-Ergebnisse sammeln
2. Nach Relevanz sortieren
3. Auf Token-Limit optimieren (llama3.2:3b → 8K context window)
4. Formatieren für optimale AI-Nutzung

---

### 4. Agent-Pipeline-Integration

**Neue Funktion:** `chat_audio_step2_agent_ai()`

Ersetzt `chat_audio_step2_ai()` bei Agent-Anfragen:

```python
def chat_audio_step2_agent_ai(
    user_text: str,
    stt_time: float,
    model_choice: str,
    history: list
) -> tuple:
    """
    Agentische AI-Antwort mit Tool-Integration

    Pipeline:
        1. Intent Detection
        2. Tool Selection & Execution
        3. Context Building
        4. AI Inference mit Context

    Returns:
        (ai_text, history, inference_time, agent_time, tools_used)
    """

    # 1. Intent Detection
    intent = detect_intent(user_text)

    if intent['intent'] == 'direct_answer':
        # Fallback zu Standard-Pipeline
        return chat_audio_step2_ai(...)

    # 2. Tool Execution
    agent_start = time.time()
    tool_results = []

    for tool_name in intent['tools_needed']:
        tool = get_tool(tool_name)
        result = tool.execute(user_text, keywords=intent['keywords'])
        tool_results.append(result)

    # 3. Context Building
    context = build_context(user_text, tool_results)

    # 4. AI Inference mit Context
    messages = [{'role': 'system', 'content': context}]
    # ... History hinzufügen ...
    messages.append({'role': 'user', 'content': user_text})

    inference_start = time.time()
    response = ollama.chat(model=model_choice, messages=messages)
    inference_time = time.time() - inference_start

    agent_time = time.time() - agent_start

    ai_text = response['message']['content']

    # History mit Agent-Info
    tools_str = ", ".join(intent['tools_needed'])
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, Tools: {tools_str})"

    history.append([user_with_time, ai_text])

    return ai_text, history, inference_time, agent_time, intent['tools_needed']
```

---

## 🎨 UI/UX Erweiterungen

### 1. Agent-Status-Anzeige

**Neue UI-Komponenten:**

```python
with gr.Row():
    agent_status = gr.Markdown("🤖 Agent Status: Bereit", visible=True)
    agent_progress = gr.HTML("", visible=False)  # Fortschrittsanzeige
```

**Live-Updates während Agent-Arbeit:**

```
🤖 Agent Status: Suche Web-Informationen... 🔍
🤖 Agent Status: Analysiere 3 Quellen... 📊
🤖 Agent Status: Generiere Antwort... 💬
```

### 2. Timing-Anzeige erweitern

**Vorher:**
```
User: Wie wird das Wetter? (STT: 1.2s)
AI: Ich kann leider keine Live-Wetterdaten abrufen. (Inferenz: 2.3s, TTS: 1.5s)
```

**Nachher (mit Agent):**
```
User: Wie wird das Wetter in Berlin? (STT: 1.2s, Agent: 3.5s, Tools: web_search)
AI: Aktuell 15°C in Berlin, bewölkt. Quelle: DuckDuckGo. (Inferenz: 2.3s, TTS: 1.5s)
```

### 3. Settings: Agent An/Aus

```python
enable_agent = gr.Checkbox(
    value=True,
    label="🤖 Agentische Fähigkeiten aktiviert",
    info="Ermöglicht Web-Suche und Internet-Recherche"
)

agent_tools = gr.CheckboxGroup(
    choices=["Web-Suche (DuckDuckGo)", "Web-Scraping", "News-Aggregation"],
    value=["Web-Suche (DuckDuckGo)"],
    label="🛠️ Verfügbare Agent-Tools",
    info="Wähle, welche Tools der Agent nutzen darf"
)
```

---

## 📊 Performance-Ziele

| Komponente | Target Latenz | Akzeptabel | Kritisch |
|-----------|---------------|------------|----------|
| Intent Detection | < 100ms | < 200ms | > 500ms |
| Web Search (DDG) | < 2s | < 5s | > 10s |
| Web Scraping (pro URL) | < 3s | < 7s | > 15s |
| Context Building | < 100ms | < 300ms | > 1s |
| AI Inference (mit Context) | < 5s | < 10s | > 20s |
| **Gesamt (Schnell-Modus)** | **< 5s** | **< 8s** | **> 15s** |
| **Gesamt (Ausführlich-Modus)** | **< 12s** | **< 20s** | **> 30s** |

**Vergleich der Modi:**

| Modus | Komponenten | Geschätzte Zeit |
|-------|-------------|-----------------|
| **Standard** (ohne Agent) | STT + AI + TTS | ~3-5s |
| **Schnell** (nur DDG) | STT + Intent + DDG + AI + TTS | ~5-8s |
| **Ausführlich** (DDG + Scraping) | STT + Intent + DDG + Scrape (3x) + AI + TTS | ~12-20s |

**Breakdown Ausführlich-Modus (Beispiel):**
- STT: 1s
- Intent Detection: 0.1s
- DuckDuckGo: 2s
- Web Scraping (3 URLs): 3-9s (parallel möglich!)
- Context Building: 0.2s
- AI Inference: 3-5s
- TTS: 2s
- **Gesamt:** ~11-20s

**Optimierungen:**
1. **Caching** - Häufige Anfragen cachen (Redis/In-Memory)
2. **Parallel Execution** - Tools parallel ausführen
3. **Streaming** - AI-Antwort streamen während TTS läuft
4. **Timeout** - Tools nach 10s abbrechen

---

## 🔐 Sicherheit & Privacy

### 1. Rate Limiting

```python
from functools import lru_cache
import time

# Simple In-Memory Rate Limiter
tool_call_times = {}

def rate_limit(tool_name: str, max_calls_per_minute: int = 10):
    now = time.time()
    if tool_name not in tool_call_times:
        tool_call_times[tool_name] = []

    # Entferne alte Einträge (> 60s)
    tool_call_times[tool_name] = [
        t for t in tool_call_times[tool_name]
        if now - t < 60
    ]

    if len(tool_call_times[tool_name]) >= max_calls_per_minute:
        raise Exception(f"Rate limit erreicht für {tool_name}")

    tool_call_times[tool_name].append(now)
```

### 2. URL Whitelisting (optional)

```python
ALLOWED_DOMAINS = [
    'wikipedia.org',
    'github.com',
    'stackoverflow.com',
    # ... trusted sources
]

def is_url_allowed(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    return any(domain.endswith(allowed) for allowed in ALLOWED_DOMAINS)
```

### 3. Content Filtering

```python
def sanitize_content(text: str) -> str:
    """Entfernt sensible Daten aus Web-Content"""
    # Entferne potenzielle Skripte
    text = re.sub(r'<script.*?</script>', '', text, flags=re.DOTALL)
    # Entferne Email-Adressen (optional)
    # text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)
    return text
```

---

## 📦 Dependencies (Neue Pakete)

```bash
# Aktuelle Requirements
pip install gradio faster-whisper ollama edge-tts

# Neue Requirements für Agent-Features
pip install beautifulsoup4      # Web Scraping
pip install requests            # HTTP Requests
pip install lxml                # HTML Parsing (schneller als html.parser)

# Optional (für erweiterte Features)
pip install playwright          # JavaScript-rendering für komplexe Sites
pip install feedparser          # RSS Feed Parsing für News
pip install redis               # Caching Layer
```

**Dateiänderung:** [requirements.txt](../requirements.txt) aktualisieren

---

## 🚀 Implementierungs-Phasen

### **Phase 1: Foundation (Woche 1-2)**

**Ziele:**
- ✅ Intent Detection implementieren
- ✅ Tool-System Grundgerüst
- ✅ DuckDuckGo Web-Search Integration
- ✅ Basis Agent-Pipeline

**Deliverables:**
1. `agent_tools.py` - Tool-System
2. `intent_detector.py` - Intent Detection
3. `mobile_voice_assistant.py` - Erweitert mit Agent-Path
4. Testing: Einfache Web-Suche-Anfragen

**Test-Fragen:**
- "Was ist Python?"
- "Wie wird das Wetter heute?"
- "Aktuelle Nachrichten zu AI"

---

### **Phase 2: Advanced Tools (Woche 3-4)**

**Ziele:**
- ✅ Web Scraping (BeautifulSoup)
- ✅ Multi-Source-Aggregation
- ✅ Context-Optimierung
- ✅ Caching Layer

**Deliverables:**
1. Erweitertes Tool-Set
2. Context Builder Optimierung
3. Performance-Tuning
4. Caching-Strategie

**Test-Fragen:**
- "Fasse diesen Artikel zusammen: [URL]"
- "Vergleiche Python und JavaScript basierend auf aktuellen Quellen"

---

### **Phase 3: UI/UX & Polish (Woche 5)**

**Ziele:**
- ✅ Agent-Status UI
- ✅ Settings-Erweiterung
- ✅ Performance-Optimierung
- ✅ Error Handling
- ✅ Documentation

**Deliverables:**
1. Vollständiges Agent-UI
2. User-Guide für Agent-Features
3. Performance-Benchmarks
4. Deployment-Ready

---

## 📈 Erfolgs-Metriken

### Quantitative Metriken

1. **Antwort-Qualität:**
   - Fact-Check Pass-Rate: > 90%
   - Quellen-Zitation: > 80% der Antworten
   - User-Zufriedenheit: > 4/5 Sterne

2. **Performance:**
   - Durchschnittliche Agent-Latenz: < 10s
   - Cache-Hit-Rate: > 30%
   - Tool-Erfolgsrate: > 95%

3. **Usage:**
   - Agent-Nutzung: > 40% aller Anfragen
   - Tool-Verteilung: Web-Search 70%, Scraping 20%, News 10%

### Qualitative Metriken

1. **User Experience:**
   - Agent-Antworten wirken "aktuell" und "fundiert"
   - Quellen-Transparenz erhöht Vertrauen
   - Status-Updates geben Feedback während Wartezeit

2. **Robustheit:**
   - Graceful Degradation bei Tool-Failures
   - Fallback zu Standard-Antworten funktioniert
   - Keine Crashes durch externe API-Fehler

---

## 🔄 Risiken & Mitigation

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|--------|-------------------|--------|------------|
| Web-APIs offline | Mittel | Hoch | Fallback zu Standard-Antworten, Multi-Source |
| Rate-Limiting | Hoch | Mittel | Caching, eigene SearxNG-Instanz |
| Langsame Antworten | Mittel | Hoch | Timeout, Streaming, Performance-Tuning |
| Privacy-Bedenken | Niedrig | Hoch | Lokale Tools bevorzugen, User-Kontrolle |
| Content-Qualität | Mittel | Mittel | Multi-Source Verification, Whitelisting |

---

## 📚 Referenzen & Inspiration

### Ähnliche Projekte

1. **LangChain Agents:** https://python.langchain.com/docs/modules/agents/
   - Tool-Calling Patterns
   - Agent-Executors

2. **AutoGPT:** https://github.com/Significant-Gravitas/AutoGPT
   - Autonome Task-Execution

3. **Open Interpreter:** https://github.com/KillianLucas/open-interpreter
   - Local-First Agent-Architektur

### APIs & Services

1. **DuckDuckGo Instant Answer:** https://duckduckgo.com/api
2. **SearxNG:** https://github.com/searxng/searxng
3. **BeautifulSoup:** https://www.crummy.com/software/BeautifulSoup/
4. **NewsAPI:** https://newsapi.org/

---

## 📝 Nächste Schritte

### Sofort (diese Session):
1. ✅ Architektur-Dokument fertigstellen
2. ⏳ README.md To-Do-Liste aktualisieren mit detaillierten Phase-1-Tasks
3. ⏳ `agent_tools.py` Grundgerüst erstellen
4. ⏳ DuckDuckGo Web-Search Prototyp implementieren

### Nächste Session:
1. Intent Detection implementieren
2. Agent-Pipeline in `mobile_voice_assistant.py` integrieren
3. Erste Tests mit Live Web-Search
4. UI Status-Updates

---

**Erstellt mit:** Claude Code
**Letzte Aktualisierung:** 2025-10-13
