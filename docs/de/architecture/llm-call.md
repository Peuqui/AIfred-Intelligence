# LLM-Call-Architektur

> **English version:** [llm-call.md](../../en/architecture/llm-call.md)

**Alle LLM-Inferenzpfade in AIfred Intelligence und wie sie zusammenhängen.**

---

## Überblick

Jeder LLM-Call in AIfred läuft durch eine einheitliche Chunk-Processing-Pipeline (`run_llm_stream()` in `llm_pipeline.py`). Diese Pipeline übernimmt TTFT-Messung, Tool-Call-Tracking, URL-Sammlung, Extraktion der Sandbox-Ausgabe, Verarbeitung der Thinking-Blöcke und Inferenz-Metadaten — egal ob der Call aus der Chat-UI, einer Multi-Agent-Debatte, der Vision-Pipeline oder dem Message Hub kommt.

```
                          +---------------------+
                          |    User / E-Mail     |
                          +----------+----------+
                                     |
              +----------------------+----------------------+
              |                      |                      |
     +--------v--------+   +--------v--------+    +--------v--------+
     |   Chat (UI)     |   |  Vision (Bild)  |    |  Message Hub    |
     |  send_message() |   | _process_vision |    | process_inbound |
     | _chat_mixin.py  |   | _chat_mixin.py  |    | msg_processor.py|
     +--------+--------+   +--------+--------+    +--------+--------+
              |                      |                      |
              |               +------v------+        +------v------+
              |               |  call_llm() |        |_call_engine |
              |               |llm_engine.py|        |  > call_llm |
              |               +------+------+        +------+------+
              |                      |                      |
     +--------v----------+          |                      |
     |_run_agent_direct   |         |                      |
     |   _response()      |         |                      |
     | multi_agent.py     |         |                      |
     +--------+-----------+         |                      |
              |                     |                      |
     +--------v----------+         |                      |
     |_stream_agent_to   |         |                      |
     |   _history()      |         |                      |
     | multi_agent.py    |         |                      |
     +--------+----------+         |                      |
              |                     |                      |
              +---------------------+----------------------+
                                    |
                          +---------v---------+
                          | run_llm_stream()  |   <-- EINHEITLICHE PIPELINE
                          | llm_pipeline.py   |
                          +---------+---------+
                                    |
                          +---------v---------+
                          | chat_stream()     |
                          | llm_client.py     |
                          +---------+---------+
                                    |
                          +---------v---------+
                          |  Backend (LLM)    |
                          | Ollama / llama.cpp|
                          | vLLM              |
                          | Cloud API         |
                          +-------------------+
```

---

## Pipeline-Events

`run_llm_stream()` liefert diese Event-Typen:

| Event-Typ | Payload | Beschreibung |
|---|---|---|
| `content` | `text: str` | Token aus der LLM-Antwort |
| `ttft` | `value: float` | Time-to-first-token (Sekunden) |
| `tool_call_start` | `name: str` | Tool-Name bekannt, Argumente werden noch gestreamt |
| `tool_call` | `name: str, arguments: str` | Vollständiger Tool-Call mit Argumenten |
| `tool_result` | `result: str` | Ergebnis der Tool-Ausführung |
| `thinking` | `text: str` | Chain-of-Thought-Block |
| `done` | `metrics: dict` | Stream-Ende mit Backend-Metriken |
| `pipeline_result` | `result: PipelineResult` | Endgültiges, aggregiertes Ergebnis |

`PipelineResult` enthält: vollständigen Antworttext, bereinigten Text (ohne Thinking), Thinking-HTML, Inferenz-Metadaten, TTFT, Timing, getrackte URLs, Sandbox-URLs.

---

## Call-Pfade im Detail

### 1. Normaler Chat (OwnKnowledge)

Der User sendet eine Nachricht mit `research_mode="none"`.

```
_chat_mixin.py: send_message()
  |-- Intent-Erkennung (detect_query_intent_and_addressee)
  |     Liefert: intent, addressee, detected_language
  |-- History-Kompression (bei >70 % Context-Auslastung)
  |-- run_generic_agent_direct_response("aifred", research_mode="none")
  |
  v
multi_agent.py: _run_agent_direct_response()
  |-- LLMClient-Init (Backend + URL aus dem State)
  |-- Modellwahl des Agenten (state._effective_model_id)
  |-- num_ctx (get_agent_num_ctx)
  |-- System-Prompt (get_agent_direct_prompt)
  |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=False)
  |     -> Nur Memory-Tools (store_memory, recall)
  |-- Messages (build_messages_from_llm_history mit Perspektive)
  |-- Temperatur (automatisch aus dem Intent oder manuell)
  |-- LLM-Optionen (build_llm_options)
  |
  v
multi_agent.py: _stream_agent_to_history()
  |-- State-Setup: set_current_agent, vLLM-Modell sicherstellen, TTS-Init
  |
  v
llm_pipeline.py: run_llm_stream()  <-- PIPELINE
  |-- chat_stream_with_retry() -> llm_client.chat_stream()
  |-- Chunk-Verarbeitung mit Tracking
  |-- PipelineResult
```

### 2. Automatik-Modus

Identisch mit OwnKnowledge, außer:

- `research_tools_enabled=True` in `prepare_agent_toolkit()`
- Das Toolkit enthält: web_search, web_fetch, calculate, execute_code, Dokument-Tools usw.
- Der System-Prompt enthält Tool-Beschreibungen
- **Das LLM entscheidet selbstständig**, ob es Tools verwendet

### 3. Quick/Deep Research

Erzwungene Web-Recherche **vor** dem LLM-Call:

```
_run_agent_direct_response(..., research_mode="quick")
  |-- _execute_forced_research(state, query, "quick")
  |     |-- research_tools.py: execute_research()
  |     |     1. Query-Generierung (Automatik-LLM erzeugt Suchbegriffe)
  |     |     2. Websuche (SearXNG/Tavily/Brave, Round-Robin pro Query)
  |     |     3. URL-Ranking (LLM bewertet die Relevanz)
  |     |     4. Web-Scraping (Top-N-URLs)
  |     |     5. Kontextaufbau
  |     |
  |     v
  |     state._research_context = aufbereitete Rechercheergebnisse
  |
  |-- inject_before_question(messages, wrap_untrusted_data(research_context))
  |-- _stream_agent_to_history() -> run_llm_stream()
```

**quick** = Top 3 URLs. **deep** = Top 7 URLs. Jede Recherche läuft frisch (kein Ergebnis-Cache).

### 4. Vision-Pipeline (Bild-Upload)

Umgeht `_run_agent_direct_response()` und ruft `call_llm()` direkt auf:

```
_chat_mixin.py: _process_vision_request()
  |-- Wahl des Vision-Modells (optionale -speed-Variante bei llamacpp)
  |-- call_llm(agent="vision", multimodal_content=[image_parts])
  |     |-- System-Prompt: get_agent_system_prompt("vision", prompt_key)
  |     |-- temperature = vision_temperature (manuell)
  |     |-- enable_thinking = vision_thinking
  |     v
  |     run_llm_stream()  <-- PIPELINE
  |
  |-- Antwort in chat_history + llm_history gespeichert
  |-- generate_session_title() (async)
```

### 5. Multi-Agent-Debattenmodi

Alle Debattenmodi verwenden für jeden Agenten-Zug `_stream_agent_to_history()` -> `run_llm_stream()`. Die **Perspektiv-Transformation** in `build_messages_from_llm_history()` sorgt dafür, dass jeder Agent die Unterhaltung aus seiner eigenen Sicht sieht:
- Eigene Nachrichten -> `role: "assistant"`
- Nachrichten anderer Agenten -> `role: "user"` (mit Labels wie `[SOKRATES]:`)

#### Auto-Konsens

```
run_sokrates_analysis(mode="auto_consensus")
  |
  Schleife (max_debate_rounds):
    |-- SOKRATES-Kritik
    |     Prompt: sokrates_critic_prompt(round_num=N)
    |     _stream_agent_to_history("sokrates") -> run_llm_stream()
    |
    |-- ABSTIMMUNG: count_lgtm_votes()
    |     [LGTM] = Zustimmung, [WEITER] = Override (erzwingt erneute Überarbeitung)
    |     Konsens bei 2/3 (Mehrheit) oder 3/3 (einstimmig)
    |
    |-- Bei Konsens -> BREAK
    |
    |-- AIFRED-Überarbeitung
          Prompt: aifred_refinement_prompt
          _stream_agent_to_history("aifred") -> run_llm_stream()
```

#### Tribunal

```
run_tribunal()
  |
  Feste Rundenzahl (kein vorzeitiger Ausstieg):
    |-- SOKRATES (Ankläger): sokrates_tribunal_prompt
    |-- AIFRED (Verteidigung): aifred_defense_prompt
  |
  Nach allen Runden:
    |-- SALOMO (Richter): salomo_judge_prompt
```

#### Advocatus Diaboli

Wie Auto-Konsens, aber Sokrates verwendet `get_sokrates_devils_advocate_prompt()`. Die Antwort wird über `parse_pro_contra()` in PRO- und CONTRA-Abschnitte zerlegt.

#### Symposion

```
run_symposion()
  |
  Für jeden Agenten in state.symposion_agents:
    |-- System-Prompt + Toolkit für diesen Agenten
    |-- Messages mit agentenspezifischer Perspektive
    |-- _stream_agent_to_history(agent_id) -> run_llm_stream()
```

### 6. Direkte Ansprache eines Agenten

Der User schreibt "Sokrates, was meinst du?" -> Die Intent-Erkennung erkennt `addressee="sokrates"`.

```
run_generic_agent_direct_response(agent_id="sokrates", ...)
  |-- Agenten-Config: get_agent_config("sokrates")
  |     -> display_name="Sokrates", Emoji, Modell, Temperatur
  |-- _run_agent_direct_response(agent="sokrates")
  |     -> perspective="sokrates" (eigene Nachrichten = assistant)
  |     -> _stream_agent_to_history("sokrates") -> run_llm_stream()
```

Genauso für Salomo und eigene Agenten.

### 7. Message Hub (eingehende E-Mail)

Vollständig zustandslos — kein Reflex-State nötig, nur die Settings-Datei und der Session-Speicher.

```
imap_listener.py: E-Mail über IMAP IDLE empfangen
  |
  v
message_processor.py: process_inbound(InboundMessage)
  |
  |-- 1. detect_target_agent(text) -> "aifred" / "sokrates" / "salomo"
  |-- 2. Session finden oder anlegen (routing_table)
  |-- 3. Eingehende Nachricht in der Session speichern
  |       Toast-Benachrichtigung im Browser (auch ohne geöffnete Session)
  |-- 4. _call_engine(email_context, session_id, agent)
  |       |-- Einstellungen aus settings.json (kein State)
  |       |-- LLM-History aus der Session-Datei
  |       |-- num_ctx: get_model_native_context() (kein State)
  |       |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |       |       -> Alle Plugins (Web, E-Mail, EPIM, Dokumente usw.)
  |       |-- call_llm(external_toolkit=toolkit, num_ctx_manual=True)
  |       |     |
  |       |     v
  |       |     run_llm_stream()  <-- PIPELINE
  |       |       Chunks werden gesammelt (kein UI-Streaming)
  |       |       Tool-Call/-Result -> Debug-Sink
  |       |
  |       v
  |       response_clean wird zurückgegeben
  |
  |-- 5. Antwort in der Session speichern
  |-- 6. Auto-Reply über SMTP (falls aktiviert)
  |-- 7. generate_session_title() (bei der ersten Nachricht)
```

### 8. Generierung des Session-Titels

Verwendet **nicht** `run_llm_stream()` — einfaches, nicht streamendes `client.chat()` mit 30 s Timeout.

```
llm_engine.py: generate_session_title()
  |-- Eingabe: user_text (max. 500 Zeichen) + ai_response (max. 500 Zeichen)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Modell: aifred_model (aus den Einstellungen)
  |-- Optionen: temperature=0.3, num_predict=300, enable_thinking=False
  |-- llm_client.chat() (NICHT streamend, 30 s Timeout)
  |-- Bereinigung: Anführungszeichen, Satzzeichen, max. 80 Zeichen
  |-- update_session_title(session_id, title)
```

### 9. Intent-Erkennung

Verwendet ebenfalls **nicht** `run_llm_stream()` — nutzt das kleine Automatik-Modell.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Modell: automatik_model (klein, schnell)
  |-- Context: AUTOMATIK_LLM_NUM_CTX (4096)
  |-- Liefert: (intent, addressee, detected_language)
  |     intent aus: FAKTISCH, KREATIV, GEMISCHT
  |     addressee aus: None, aifred, sokrates, salomo
  |     language aus: de, en
```

---

## Thinking-Modi & Reasoning-Effort-Stufen

Jeder Agent hat statt eines einfachen An/Aus-Schalters ein Thinking-Modus-Dropdown (🧠).
Die Optionsliste ist modellspezifisch: `off` und `on` gibt es immer;
Modelle, deren Chat-Template eine abgestufte Denktiefe unterstützt, bringen
zusätzliche Einträge mit (z. B. DeepSeek-V4: `max` = Think Max — `on` ist die
Default-Stufe des Templates, Think High).

Der Mechanismus ist vollständig modellagnostisch — keine Modellnamen sind hardcodiert:

```
GGUF (tokenizer.chat_template)
  |-- gguf_utils.detect_reasoning_levels()
  |     Durchsucht das eingebettete Jinja-Template nach Vergleichen mit der
  |     Variable `reasoning_effort` (== / != / in [...]). Wogegen das
  |     Template vergleicht, IST die Menge der unterstützten Stufen, weil
  |     chat_template_kwargs 1:1 als Jinja-Variablen übergeben werden.
  |
  |-- model_vram_cache.json: "reasoning_levels" (persistiert)
  |     Wird beim Modellwechsel/Start lazy befüllt (Header lesen, ms) und
  |     bei der Kalibrierung zwangsweise aufgefrischt (ein erneuter Download kann es ändern).
  |
  |-- State: {agent}_thinking (bool) + {agent}_reasoning_effort (str)
  |     Dropdown-Zuordnung: off -> thinking=False; on -> thinking=True,
  |     effort=""; <level> -> thinking=True, effort=<level>.
  |
  v
LLMOptions.reasoning_effort
  |-- llamacpp/_build_extra_body(): chat_template_kwargs =
  |     {"enable_thinking": ..., "reasoning_effort": "<level>"}
  |-- ollama: ignoriert effort (nur bool `think`)
```

Jedes Modell, dessen Template `reasoning_effort` mit String-Literalen vergleicht
(DeepSeek-V4 `max`, `low/medium/high` im Stil von gpt-oss, künftige
Modelle), bekommt seine Stufen automatisch erkannt. Templates mit einem
anderen Variablennamen bräuchten eine Erweiterung in
`detect_reasoning_levels()` — bewusst konservativ, um Fehlalarme
zu vermeiden.

---

## Zusammenfassung: Was durch die Pipeline läuft

| Pfad | Einstiegspunkt | Pipeline? | Hinweise |
|---|---|---|---|
| OwnKnowledge | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Toolkit nur mit Memory |
| Automatik | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Volles Toolkit, Agent entscheidet |
| Quick/Deep | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Recherche-Kontext vorab eingefügt |
| Vision | `_process_vision_request` | `call_llm` -> `run_llm_stream` | Multimodal, eigenes Modell |
| Direkter Agent | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Agentenspezifische Perspektive |
| Auto-Konsens | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Mehrere Runden, Abstimmung |
| Tribunal | `run_tribunal` | `_stream_agent_to_history` -> `run_llm_stream` | 3 Agenten, Salomo richtet |
| Advocatus Diaboli | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Pro/Contra-Parsing |
| Symposion | `run_symposion` | `_stream_agent_to_history` -> `run_llm_stream` | N Agenten nacheinander |
| Message Hub | `process_inbound` | `call_llm` -> `run_llm_stream` | Zustandslos, Auto-Reply |
| Titel-Generierung | `generate_session_title` | **Nein** — `client.chat()` | Nicht streamend, 30 s Timeout |
| Intent | `detect_query_intent` | **Nein** — `client.chat()` | Automatik-Modell |

---

## Wichtige Dateien

| Datei | Rolle |
|---|---|
| `aifred/lib/llm_pipeline.py` | Einheitliche Chunk-Processing-Pipeline (`run_llm_stream`, `PipelineResult`) |
| `aifred/lib/multi_agent.py` | Chat-Streaming (`_stream_agent_to_history`), Debattenmodi, Agenten-Dispatch |
| `aifred/lib/llm_engine.py` | `call_llm()` (Einstieg für Vision + Hub), `generate_session_title()` |
| `aifred/lib/message_processor.py` | Message-Hub-Pipeline (`process_inbound`, `_call_engine`) |
| `aifred/lib/llm_client.py` | `LLMClient`, `build_llm_options()`, Backend-Abstraktion |
| `aifred/lib/message_builder.py` | `build_messages_from_llm_history()` — Perspektiv-Transformation |
| `aifred/lib/agent_memory.py` | `prepare_agent_toolkit()` — Memory- + Plugin-Tools |
| `aifred/lib/agent_config.py` | Agenten-Konfiguration (Anzeigenamen, Emojis, Modelle) |
| `aifred/lib/intent_detector.py` | Erkennung von Intent/Sprache/Adressat |
| `aifred/state/_chat_mixin.py` | UI-Einstiegspunkt (`send_message`, `_process_vision_request`) |
