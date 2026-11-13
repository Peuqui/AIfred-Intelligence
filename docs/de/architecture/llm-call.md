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
     | _chat_mixin.py  |   | _chat_mixin.py  |    |message_processor|
     +--------+--------+   +--------+--------+    +--------+--------+
              |                      |                      |
              |               +------v------+        +------v------+
              |               |  call_llm() |        |  run_turn() |
              |               |llm_engine.py|        |turn_service |
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
| `tool_progress` | `message: str` | Fortschrittszeile eines streamenden Tools (web_search, search_documents, ...) |
| `tool_artifacts` | `artifacts: list` | Bubble-Artefakte, die ein Tool liefert (an der Stelle platziert, an der das Tool lief) |
| `tool_result` | `name: str, result: str` | Ergebnis der Tool-Ausführung |
| `thinking` | `text: str` | Chain-of-Thought-Block |
| `debug` | `message: str` | Debug-Meldung des Backends (Trunkierungswarnung, Context-Guard, ...) |
| `progress` | `phase: str` | Arbeit des Backends zwischen den Runden: `compress`, solange die Werkzeugschleife komprimiert, `""` beendet sie |
| `done` | `metrics: dict` | Stream-Ende mit Backend-Metriken |
| `pipeline_result` | `result: PipelineResult` | Endgültiges, aggregiertes Ergebnis |

`PipelineResult` enthält: vollständigen Antworttext (`text`, inkl. `<think>`-Blöcken), bereinigten Text (`text_clean`), Inferenz-Metadaten (`metadata_dict`, `metadata_display`, `debug_msg`, `metrics`), `ttft`, `inference_time`, die gemessene Arbeit (`work`), Bubble-Artefakte (`artifacts` — Quellen aus `web_fetch`, Sandbox-Ausgabe, VLM-Ausgabe), `final_text` (Antwort nach dem letzten Tool-Call), `history_notes` sowie die Flags `silent_reply` und `truncated`.

`history_notes` sind Zeilen, die nur hinter der Antwort in der `llm_history` stehen, nicht im Chat. Die Werkzeug-Aufrufe selbst überleben die Runde nicht. Damit das Modell in der Folgerunde weiß, was es getan hat, hängt `run_llm_stream()` hier die Zeile aus `ToolKit.call_note()` an (Text: `prompts/{de,en}/shared/tool_calls_marker.txt`): schreibende Aufrufe mit ihren Angaben (geheime Werte geschwärzt), lesende nur gezählt, `✗` = fehlgeschlagen. Lange Werte werden einzeln und mit `…` gekürzt (`TOOL_CALL_NOTE_VALUE_CHARS`), nie der Aufruf als Ganzes: Das Modell liest die Notiz als seinen Aufruf, und ein mitten im Datum abgeschnittener Wert brachte es dazu, einen gelungenen Eintrag zu widerrufen. `shared/tool_instructions.txt` verbietet dem Modell, solche Zeilen selbst zu schreiben.

---

## Call-Pfade im Detail

### 1. Normaler Chat

Jeder Agent bekommt seinen vollen Werkzeugsatz und entscheidet selbst, ob er Werkzeuge nutzt (Websuche, Rechnen, Dokumente, …).

```
_chat_mixin.py: send_message()
  |-- Intent-Erkennung (detect_query_intent_and_addressee)
  |     Liefert: addressee, detected_language, mode_switch_updates,
  |              is_pure_command, raw_response, load_time
  |-- History-Kompression (bei >85 % Context-Auslastung)
  |-- run_generic_agent_direct_response("aifred")
  |
  v
multi_agent.py: _run_agent_direct_response()
  |-- LLMClient-Init (Backend + URL aus dem State)
  |-- Modellwahl des Agenten (state._effective_model_id)
  |-- num_ctx (get_agent_num_ctx)
  |-- System-Prompt (get_agent_direct_prompt)
  |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |     -> Memory-Tools + alle Plugin-Tools (web_search, web_fetch, calculate,
  |        execute_code, Dokument-Tools, …); abgerufene Erinnerungen kommen
  |        als Kontext vor die Frage
  |-- Messages (build_messages_from_llm_history mit Perspektive)
  |-- Temperatur des Agenten (resolve_agent_temperature, eigene je Agent)
  |-- LLM-Optionen (build_llm_options)
  |
  v
multi_agent.py: _stream_agent_to_history()
  |-- State-Setup: state._set_current_agent, vLLM-Modell sicherstellen, TTS-Init
  |
  v
llm_pipeline.py: run_llm_stream()  <-- PIPELINE
  |-- llm_client.chat_stream() (Wiederholung bei Netz- und Serverfehlern im Backend: worth_retrying)
  |-- Chunk-Verarbeitung mit Tracking
  |-- PipelineResult
```

### 2. Vision-Pipeline (Bild-Upload)

Modellwahl: `_image_describer()` (Regel A, siehe [Vision-Routing](vision-routing.md)).
Kann das Hauptmodell nicht sehen (oder Symposion mit ≥2 Agenten), beschreibt
`_describe_images()` die Bilder einmal, die Beschreibung kommt in den
Nutzer-Turn, und es läuft die normale Text-Pipeline. Nur wenn das Hauptmodell
selbst sieht, umgeht VL Direct `_run_agent_direct_response()` und ruft
`call_llm()` direkt auf:

```
_chat_mixin.py: _process_vision_request()
  |-- Modell = effektives Hauptmodell (AIfred)
  |-- Handelnder Agent = aktiver Agent (Memory, Tools, Persönlichkeit)
  |-- Toolkit: prepare_agent_toolkit(research_tools_enabled=True)
  |-- call_llm(agent=<handelnder Agent>, multimodal_content=content_parts,
  |            external_toolkit=..., vision_task_addon=...)
  |     |-- System-Prompt: get_agent_system_prompt(agent, "task")
  |     |     + vision_task_addon
  |     |-- Temperatur aus dem AIfred-Bucket
  |     |-- LLM-Optionen: build_llm_options(state, agent, ...)
  |     v
  |     run_llm_stream()  <-- PIPELINE
  |
  |-- Antwort in chat_history + llm_history gespeichert
  |-- _generate_session_title() (Fire-and-forget-Task)
```

### 3. Multi-Agent-Debattenmodi

Alle Debattenmodi verwenden für jeden Agenten-Zug `_stream_agent_to_history()` -> `run_llm_stream()`. Die **Perspektiv-Transformation** in `build_messages_from_llm_history()` sorgt dafür, dass jeder Agent die Unterhaltung aus seiner eigenen Sicht sieht:
- Eigene Nachrichten -> `role: "assistant"`
- Nachrichten anderer Agenten -> `role: "user"` (mit Labels wie `[SOKRATES]:`)

#### Auto-Konsens

```
run_sokrates_analysis(mode="auto_consensus")
  |
  Schleife (max_debate_rounds):
    |-- SOKRATES-Kritik
    |     Prompt: get_sokrates_critic_prompt(round_num=N)
    |     _stream_agent_to_history("sokrates") -> run_llm_stream()
    |
    |-- SALOMO-Synthese
    |     Prompt: get_salomo_mediator_prompt(round_num=N)
    |
    |-- ABSTIMMUNG: count_lgtm_votes(aifred, sokrates, salomo)
    |     [LGTM] = Zustimmung, [WEITER] = Override (erzwingt erneute Überarbeitung)
    |     Konsens bei 2/3 (Mehrheit) oder 3/3 (einstimmig)
    |
    |-- Bei Konsens -> BREAK
    |
    |-- AIFRED-Überarbeitung
          Prompt: get_aifred_refinement_prompt(...)
          _stream_agent_to_history("aifred") -> run_llm_stream()
```

#### Tribunal

```
run_tribunal()
  |
  Feste Rundenzahl (kein vorzeitiger Ausstieg):
    |-- SOKRATES (Ankläger): get_sokrates_tribunal_prompt(round_num=N)
    |-- AIFRED (Verteidigung): get_aifred_defense_prompt(...)
  |
  Nach allen Runden:
    |-- SALOMO (Richter): get_salomo_judge_prompt()
```

#### Symposion

```
run_symposion()
  |
  Für jeden Agenten in state.symposion_agents:
    |-- System-Prompt + Toolkit für diesen Agenten
    |-- Messages mit agentenspezifischer Perspektive
    |-- _stream_agent_to_history(agent_id) -> run_llm_stream()
```

### 4. Direkte Ansprache eines Agenten

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

### 5. Message Hub (eingehende E-Mail, Scheduler, Alarme)

Vollständig zustandslos — kein Reflex-State nötig. Seit 07.11.2026 läuft die Nachricht als Zug des
Zug-Dienstes (`lib/turn_service.py`), derselbe, den die Vue-Oberfläche für den Chat nutzt: Absicht,
Moduswechsel, Kompression, Diskussionsmodi und dieselbe Blase wie im Browser.

```
email_channel/__init__.py: listener_loop() — E-Mail über IMAP IDLE empfangen
  |
  v
message_processor.py: dispatch_inbound() -> process_inbound(InboundMessage)
  |
  |-- 1. Kalibrier-Sperre (Absage über den Kanal), Stufe des Absenders,
  |       Bereinigung, Nutzer-Zuordnung
  |-- 2. Session finden oder anlegen (routing_table)
  |       hub_notification_scope: Toast "received" im Browser
  |-- 3. _turn_origin(): Blase (build_inbound_chat_entry), was das Modell liest
  |       (Kontext des Plugins, eingepackt mit wrap_external_message — der Zaun
  |       bleibt in der llm_history), Stufe, Vertrauen, Kanal-Metadaten,
  |       metadata["wake_agent"] als fester Agent (nur wenn konfiguriert)
  |-- 4. turn_service.run_turn(TurnRequest(origin=...)) im Task des Kanals
  |       |-- Nutzer-Eintrag sofort in die Session (Chat + llm_history)
  |       |-- Absicht: Adressat, Sprache, Moduswechsel (eine Bestätigung bei
  |       |     reinem Befehl); Routing: fester Agent > Adressat > active_agent
  |       |-- Kompression, Plan (Diskussionsmodus der Session)
  |       |-- je Agent: agent_toolkit() -> prepare_agent_toolkit mit Stufe,
  |       |     Vertrauen und Metadaten der Herkunft
  |       |-- run_llm_stream()  <-- PIPELINE (Token-Ereignisse an offene Tabs;
  |       |     vorgelesen wird im Browser nicht, der Kanal spricht selbst)
  |       |-- Antworten als Blasen gespeichert, Titel im Hintergrund
  |       v
  |       TurnOutcome: Status, Agent, Antworten (text_clean, silent)
  |
  |-- 5. outbound_text(): alle Antworten des Zugs, bereinigt; mit Namenszeile,
  |       wenn ein anderer Agent als AIfred oder mehrere sprachen
  |-- 6. Auto-Reply über plugin.send_reply() (wenn der Channel always_reply
  |       hat oder sein Auto-Reply-Schalter an ist — E-Mail: SMTP)
```

### 6. Generierung des Session-Titels

Verwendet **nicht** `run_llm_stream()` — einfaches, nicht streamendes `client.chat()` mit hartem Timeout (`SESSION_TITLE_TIMEOUT_SECONDS`, 300 s).

```
llm_engine.py: generate_session_title()
  |-- Eingabe: user_text (max. 500 Zeichen) + ai_response (max. 500 Zeichen)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Modell: model_override, sonst das Automatik-Modell aus den Einstellungen
  |     (fällt auf aifred_model zurück, wenn "(wie AIfred-LLM)" gewählt ist)
  |-- Optionen: temperature=0.3, num_predict=SESSION_TITLE_NUM_PREDICT (2000),
  |     enable_thinking=False, num_ctx=AUTOMATIK_LLM_NUM_CTX
  |-- llm_client.chat() (NICHT streamend, Timeout SESSION_TITLE_TIMEOUT_SECONDS)
  |-- Bereinigung: Thinking-Blöcke, Anführungszeichen, Satzzeichen am Ende, max. 80 Zeichen
  |-- update_session_title(session_id, title)
```

### 7. Intent-Erkennung

Verwendet ebenfalls **nicht** `run_llm_stream()` — nutzt das kleine Automatik-Modell.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Modell: automatik_model (klein, schnell)
  |-- Context: AUTOMATIK_LLM_NUM_CTX (12288), wenn sich das Automatik-Modell
  |     von AIfreds unterscheidet; beim selben Modell AIfreds max_context (kein Reload)
  |-- Optionen: temperature=0.2, enable_thinking=False
  |-- Liefert: (addressee, detected_language, mode_switch_updates,
  |            is_pure_command, raw_response, load_time)
  |     addressee aus: None, aifred, sokrates, salomo
  |     language aus: de, en
  |     mode_switch_updates: gewünschte Konfigurationsänderung ({} wenn keine)
  |     is_pure_command: Nachricht ist NUR ein Modus-Wechsel-Befehl
```

---

## Thinking-Modi & Reasoning-Effort-Stufen

Jeder Agent hat statt eines einfachen An/Aus-Schalters ein Thinking-Modus-Dropdown (🧠).
Die Optionsliste zeigt nur, was das Chat-Template des Modells kann: `off`, wo
sich das Denken abschalten lässt, `on` (die Standard-Stufe des Templates) und
die Stufen des Templates, wo das Modell denkt, und nur `off` bei einem Modell
ohne Denkphase. Bei einem Modell, das sich nicht abschalten lässt, gibt es kein
`on`: Der Standard steht als Stufe an seinem Platz, mit dem Zusatz „Standard“.
Beispiele: GLM-5.3 denkt immer → `low`, `high`, `max (Standard)`;
DeepSeek-V4 → `off`, `on`, `max`; Qwen3-VL Instruct → `off`. Modelle ohne
llama-swap-Eintrag (Ollama, Cloud) bieten `off`/`on`.

Der Mechanismus ist vollständig modellagnostisch — keine Modellnamen sind hardcodiert:

```
GGUF (tokenizer.chat_template)
  |-- gguf_utils.detect_reasoning_levels()
  |     Durchsucht das eingebettete Jinja-Template nach Vergleichen mit der
  |     Variable `reasoning_effort` (== / != / in [...]). Wogegen das
  |     Template vergleicht, IST die Menge der unterstützten Stufen, weil
  |     chat_template_kwargs 1:1 als Jinja-Variablen übergeben werden.
  |
  |-- gguf_utils.detect_reasoning_info() -> ReasoningInfo
  |     Stufen + Standard, dazu `thinks` (das Template hat eine Denkphase:
  |     <think>, Stufen oder ein Schalter) und `switchable` (das Template liest
  |     den Schalter, den AIfred schickt: `enable_thinking`; ein mitgelieferter
  |     Prompt-Encoder verzweigt nach `thinking_mode`, den vLLMs Tokenizer
  |     daraus ableitet).
  |
  |-- model_vram_cache.json: reasoning_levels, reasoning_default, thinks,
  |     thinking_switchable (persistiert; resolve_reasoning_info, Cache zuerst)
  |     Wird beim ersten Gebrauch befüllt (Header lesen, ms) und bei der
  |     Kalibrierung zwangsweise aufgefrischt (ein erneuter Download kann es ändern).
  |
  |-- State: agent_tuning[agent].thinking_by_model {Modell: Wahl}
  |     Eine Wahl je Zeile UND Modell: "off", "on" (Stufe, die das Template
  |     selbst wählt) oder eine Stufe. Das Modell zählt als Basismodell
  |     (agent_settings.thinking_model_key: ohne llama-swap-Varianten wie
  |     -visiond/-tts-…/-vlm-…/-speed, ohne Rechner-Präfix). Ein Modell ohne
  |     Eintrag denkt, wie toggles.thinking des Agenten in agents.json sagt.
  |     Lesen nur über agent_settings.thinking_mode / persisted_thinking_mode
  |     (mit dem Modell, an das die Anfrage geht), Schreiben nur über
  |     set_thinking_mode (das Dropdown). Ein Modellwechsel kopiert nichts:
  |     das andere Modell hat seinen eigenen Eintrag. effective_thinking_mode
  |     macht aus der gespeicherten Wahl eine, die das Modell ausführen kann
  |     (keine Denkphase → off; off bei einem immer denkenden Modell → dessen
  |     Standard; unbekannte Stufe → Standard), ohne das Gespeicherte umzuschreiben.
  |
  v
build_llm_options(state, agent, model_id, …) -> LLMOptions.reasoning_effort
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
| Normaler Chat | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Volles Toolkit, Agent entscheidet |
| Vision | `_process_vision_request` | `call_llm` -> `run_llm_stream` | Multimodal, handelt als aktiver Agent |
| Direkter Agent | `_run_agent_direct_response` | `_stream_agent_to_history` -> `run_llm_stream` | Agentenspezifische Perspektive |
| Auto-Konsens | `run_sokrates_analysis` | `_stream_agent_to_history` -> `run_llm_stream` | Mehrere Runden, Abstimmung |
| Tribunal | `run_tribunal` | `_stream_agent_to_history` -> `run_llm_stream` | 3 Agenten, Salomo richtet |
| Symposion | `run_symposion` | `_stream_agent_to_history` -> `run_llm_stream` | N Agenten nacheinander |
| Message Hub | `process_inbound` | `turn_service.run_turn` -> `stream_call` -> `run_llm_stream` | Zustandslos, derselbe Zug wie der Vue-Chat, Auto-Reply |
| Titel-Generierung | `generate_session_title` | **Nein** — `client.chat()` | Nicht streamend, 300 s Timeout |
| Intent | `detect_query_intent_and_addressee` | **Nein** — `client.chat()` | Automatik-Modell |

---

## Wichtige Dateien

| Datei | Rolle |
|---|---|
| `aifred/lib/llm_pipeline.py` | Einheitliche Chunk-Processing-Pipeline (`run_llm_stream`, `PipelineResult`) |
| `aifred/lib/multi_agent.py` | Chat-Streaming (`_stream_agent_to_history`), Debattenmodi, Agenten-Dispatch |
| `aifred/lib/llm_engine.py` | `call_llm()` (Bild im Reflex-Chat), `generate_session_title()` |
| `aifred/lib/turn_service.py` | Zug des Servers (Vue-Chat, Kanäle, Scheduler, Alarme): `run_turn`, `TurnOrigin`, `TurnOutcome` |
| `aifred/lib/message_processor.py` | Message-Hub-Pipeline (`process_inbound`, `outbound_text`) |
| `aifred/lib/llm_client.py` | `LLMClient`, `build_llm_options()`, Backend-Abstraktion |
| `aifred/lib/message_builder.py` | `build_messages_from_llm_history()` — Perspektiv-Transformation |
| `aifred/lib/agent_memory.py` | `prepare_agent_toolkit()` — Memory- + Plugin-Tools |
| `aifred/lib/agent_config.py` | Agenten-Konfiguration (Anzeigenamen, Emojis, Modelle) |
| `aifred/lib/intent_detector.py` | Erkennung von Sprache, Adressat und Moduswechsel |
| `aifred/state/_chat_mixin.py` | UI-Einstiegspunkt (`send_message`, `_process_vision_request`) |
