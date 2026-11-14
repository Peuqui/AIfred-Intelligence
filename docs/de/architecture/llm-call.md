# LLM-Call-Architektur

> **English version:** [llm-call.md](../../en/architecture/llm-call.md)

**Alle LLM-Inferenzpfade in AIfred Intelligence und wie sie zusammenhängen.**

---

## Überblick

Jeder Antwort-Call in AIfred läuft durch eine einheitliche Chunk-Processing-Pipeline (`run_llm_stream()` in `llm_pipeline.py`). Diese Pipeline übernimmt TTFT-Messung, Tool-Call-Tracking, URL-Sammlung, Extraktion der Sandbox-Ausgabe, Verarbeitung der Thinking-Blöcke und Inferenz-Metadaten, egal ob der Zug aus dem Chat der Vue-Oberfläche oder aus einem Kanal (E-Mail, Scheduler, Alarm) kommt und ob ein Agent allein antwortet oder mehrere diskutieren. Der Zug selbst ist `run_turn()` in `turn_service.py`; die Diskussionsmodi liegen in `turn_debate.py`.

```
                +----------------------------+
                |   Chat (Vue)   |  Kanal    |
                | submit_turn()  | process_  |
                |                | inbound() |
                +-------+--------+-----+-----+
                        |              |
                        +------+-------+
                               |
                      +--------v---------+
                      |   run_turn()     |   ein Zug: Nachricht bis zur
                      | turn_service.py  |   fertigen Antwort
                      +--------+---------+
                               |
        Absicht (Automatik-Modell), Moduswechsel, Routing,
        Bilder (_look_at_images), Kompression (_compress)
                               |
              +----------------+-----------------+
              |                                  |
     +--------v---------+              +---------v----------+
     |   _respond()     |              |  run_symposion()   |
     | direkte Antwort  |              |  turn_debate.py    |
     +--------+---------+              +---------+----------+
              |                                  |
     +--------v---------+                        |
     |   run_debate()   |  (nur in den           |
     |  turn_debate.py  |   Debattenmodi)        |
     +--------+---------+                        |
              |                                  |
              +----------------+-----------------+
                               |
                      +--------v---------+
                      |  stream_call()   |   jeder Modell-Call eines Zuges
                      | turn_service.py  |
                      +--------+---------+
                               |
                      +--------v---------+
                      | run_llm_stream() |   <-- EINHEITLICHE PIPELINE
                      | llm_pipeline.py  |
                      +--------+---------+
                               |
                      +--------v---------+
                      |  chat_stream()   |
                      |  llm_client.py   |
                      +--------+---------+
                               |
                      +--------v---------+
                      |  Backend (LLM)   |
                      | Ollama / llama.cpp|
                      | vLLM / Cloud-API |
                      +------------------+
```

Jeder Modell-Call eines Zuges läuft durch dieselben drei Schritte: bauen (`AgentCall`), streamen (`stream_call`), abschließen (`finish_call`: Blase und `llm_history` speichern, Clients benachrichtigen). Das gilt für die direkte Antwort (`_respond`) wie für jeden Sprecher einer Diskussion.

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

### 1. Ein Zug (Chat und Kanäle)

Ein Zug ist eine Nachricht der Sitzung bis zur fertigen Antwort. Er läuft als Task des Servers, unabhängig von einem offenen Tab; die Tabs lesen den Ereignisstrom der Sitzung (`session_events`). Eine Nachricht im Chat (`submit_turn`) und die eines Kanals (`process_inbound`, siehe Abschnitt 5) laufen denselben Weg. Läuft in der Sitzung schon ein Zug, wartet die Nachricht in der Warteschlange (`pending_messages`) und startet, wenn er endet.

```
turn_service.py: run_turn() -> _run()
  |-- Bilder der Nachricht (session_images), Prüfung: leer, Kalibrierung, Modell gesetzt
  |-- Nutzer-Eintrag sofort in die Sitzung (chat_history + llm_history)
  |-- _detect_intent(): Adressat, Sprache, Moduswechsel (ein Automatik-Aufruf, Abschnitt 7)
  |     Task eines Systems (Job, Alarm): keine Absicht; nur URL oder nur Bild: übersprungen
  |-- Moduswechsel anwenden; reiner Befehl -> Bestätigung statt Antwort, Zug zu Ende
  |-- _route(): fester Agent (Weckwort, Job) > Adressat > aktiver Agent der Sitzung
  |     (eine ausdrückliche Anrede bleibt als aktiver Agent hängen)
  |-- turn_debate.plan_turn(): wer spricht (TurnPlan)
  |-- _look_at_images(): wer die Bilder ansieht (Abschnitt 2)
  |-- _compress(): History-Kompression vor dem Zug (Abschnitt 3)
  |-- keine Antwort-Rolle (Symposion)   -> turn_debate.run_symposion()
  |   sonst _respond(Antwortender)      -> direkte Antwort
  |     danach, in einem Debattenmodus  -> turn_debate.run_debate()
  |-- _start_title(): Sitzungstitel im Hintergrund (Abschnitt 6)
```

Die direkte Antwort:

```
turn_service.py: _respond()
  |-- _direct_call(): baut den AgentCall
  |     |-- Modell des Agenten (agent_model: eigenes, sonst das von AIfred)
  |     |-- num_ctx (agent_context)
  |     |-- agent_toolkit() -> prepare_agent_toolkit(research_tools_enabled=True)
  |     |     -> Memory-Tools + alle Plugin-Tools; abgerufene Erinnerungen kommen
  |     |        als Kontext direkt vor die Frage (inject_before_question)
  |     |-- Denkwahl des Agenten für dieses Modell (thinking_mode, thinking_fields)
  |     |-- System-Prompt: prompt_loader.get_agent_direct_prompt(agent, lang, memory)
  |     |-- Messages: build_messages_from_llm_history(..., perspective=agent)
  |     |-- Temperatur des Agenten (resolve_agent_temperature, eigene je Agent)
  |     |-- LLM-Optionen: build_llm_options(view, agent, model, temperature, num_ctx)
  |-- stream_call(): run_llm_stream()  <-- PIPELINE
  |-- finish_call(mode="direct")
```

Sprechen mehrere Agenten, sorgt die **Perspektiv-Transformation** in `build_messages_from_llm_history()` dafür, dass jeder die Unterhaltung aus seiner Sicht sieht: eigene Antworten sind `role: "assistant"` (ohne Namensmarke), alles andere `role: "user"`, Antworten anderer Agenten unter ihrem Anzeigenamen. Der Richter einer Debatte liest als Beobachter (`observer=True`): jede Antwort ist die eines anderen.

Spricht der Nutzer einen Agenten an ("Sokrates, was meinst du?"), liefert die Absichtserkennung dessen Agenten-ID als Adressaten; `_route()` wählt ihn, und derselbe Zug läuft mit diesem Agenten als Antwortendem.

### 2. Bilder eines Zuges

Wer die Bilder ansieht, entscheidet `turn_service._look_at_images()` mit `vision_routing.chat_describer()` (Regel A, siehe [Vision-Routing](vision-routing.md)):

- **VL Direct**: Antwortet ein einzelner Agent ohne Debatte und kann sein Modell sehen, bekommt er die Bilder in seiner Anfrage. `_direct_call()` baut dazu die Frage (`turn_images.seeing_question`), den Inhalt (`seeing_content`, Bilder zuerst, dann ein Text) und die Aufgabe im System-Prompt (`seeing_task`). Die Bilder selbst kommen nie in die History; dort steht nur die URL (`image_marker`), damit ein späterer Zug mit `vision_analyze` noch einmal hinsehen kann.
- **Beschreibung**: In jedem anderen Fall (Debatte, Symposion, Modell ohne Sicht) beschreibt `turn_images.describe_images()` die Bilder einmal; die Beschreibung (`description_block`) wird an den Nutzer-Turn gehängt und in der `llm_history` überschrieben (`_rewrite_user_turn`). So arbeiten alle Agenten des Zuges auf derselben Grundlage. Ein Beschreiber eines anderen Rechners lädt dort (`remote_describers`).
- Findet sich kein Beschreiber (`NoVisionModelError`), läuft der Zug ohne Beschreibung weiter, mit Hinweis an den Nutzer.

Die Bilder eines Zuges lassen sich vor dem Senden drehen und zuschneiden (`rotate_upload`, `crop_upload`).

### 3. History-Kompression

Zwei Stellen komprimieren, beide ab `HISTORY_COMPRESSION_TRIGGER` (85 % des Kontexts) bis `HISTORY_COMPRESSION_TARGET` (30 %):

- **Vor dem Zug** (`turn_service._compress` → `compress_history` → `context_manager.summarize_history_if_needed`): gemessen am kleinsten Kontext der Agenten des Zuges und dem größten ihrer System-Prompts. Das Modell des ersten Sprechers fasst zusammen, damit kein zweites Modell geladen werden muss. In den Debattenmodi prüft `turn_debate._compress_before` vor jedem Sprecher erneut, weil die Debatte selbst den Kontext des nächsten füllen kann.
- **In der Werkzeugschleife** (`context_manager.compress_tool_loop`, `LoopCompression`): `stream_call` gibt `run_llm_stream` ein `LoopCompression` mit (Client, Modell, Sprache); erreicht der Kontext der Schleife den Trigger, fasst sie die frühere Unterhaltung und, wenn das nicht reicht, die ältesten abgeschlossenen Werkzeugrunden zusammen. Die Frage, die System-Nachrichten und die letzten Runden bleiben. Die Histories der Sitzung bleiben dabei unberührt; der Pipeline-Event `progress` meldet die Phase `compress`.

Beides meldet die Phase über `debug_bus.report_phase` als Ereignis `turn_phase` an die Tabs.

### 4. Diskussionsmodi (`turn_debate.py`)

Die Modi arbeiten mit **Plätzen** statt mit Namen (`debate_seats.py`): der Verfasser (`SEAT_MAIN`, der Agent, der zuerst antwortet), der Kritiker (`SEAT_CRITIC`) und der Richter (`SEAT_JUDGE`). Kritiker und Richter wählt die Sitzung (Vorgabe aus `settings.json`, `debate_seats`; `session_seats`). Jeder Agent spricht mit seiner eigenen Persona; die Aufgabe des Platzes steht in `prompts/<lang>/debate/<key>.txt` (`seat_prompt`) und nennt die anderen Plätze, nie feste Namen. Die Runden-Regeln wählt der Code, nicht das Modell: `round_rules()` lädt `<key>_first` für Runde 1, `<key>_later` für alle weiteren. Ist ein Agent eines Platzes verschwunden, lehnt `plan_turn` den Zug ab, bevor jemand spricht.

Ablauf: Zuerst die direkte Antwort des Verfassers (`_respond`), dann `run_debate(ctx, plan, answer)`:

```
run_debate()
  |-- mode == "tribunal"  -> _tribunal()
  |-- mode == "recensio"  -> _recensio()
  |-- sonst               -> _critique()   (critical_review und auto_consensus)
```

Jeder Sprecher baut seinen Call mit `_call()` (System-Prompt, History aus seiner Sicht, bei Verfasser-Aufgaben die Frage der Runde) und läuft über `stream_call` → `run_llm_stream()` und `finish_call`. Der System-Prompt ist der Aufgaben-Prompt des Agenten für Mehr-Agenten-Modi (`_base_system`, Rollenbeschreibung `DEBATE_ROLES`) plus die Aufgabe des Platzes (`_seat_system`).

#### Kritische Prüfung (critical_review)

Direkte Antwort, dann genau eine Kritik des Kritikers (`_critique`, eine Runde). Der Nutzer entscheidet danach.

#### Auto-Konsens

```
_critique()   (mode = auto_consensus, höchstens max_debate_rounds Runden)
  |
  Schleife je Runde N:
    |-- KRITIKER: Aufgabe critic + round_rules("critic", N == 1) + critic_voting
    |-- RICHTER (Beobachter): Aufgabe mediator + round_rules("mediator", N == 1)
    |
    |-- ABSTIMMUNG: _agrees() je Platz, _consensus(votes, consensus_type)
    |     [LGTM] = Zustimmung; [WEITER] sticht [LGTM]
    |     Runde 1: nur Kritiker und Richter stimmen (die direkte Antwort des Verfassers
    |     wurde nie um eine Stimme gebeten); ab Runde 2 stimmt auch der Verfasser
    |     "majority": mehr als die Hälfte stimmt zu; "unanimous": alle
    |
    |-- Konsens oder letzte Runde -> Ende
    |
    |-- VERFASSER: Überarbeitung (Aufgabe refinement als Frage der nächsten Runde)
```

Die Abstimmungsregel (`critic_voting`) steht nur im Prompt des Kritikers im Auto-Konsens, nicht in `critical_review`. `consensus_type` und `max_debate_rounds` kommen aus den Einstellungen.

#### Tribunal

```
_tribunal()
  |
  Feste Rundenzahl max_debate_rounds (kein vorzeitiger Ausstieg):
    |-- KRITIKER (Ankläger): Aufgabe tribunal + round_rules("tribunal", N == 1)
    |-- VERFASSER (Verteidigung): Aufgabe defense als Frage (darf bleiben oder ändern)
  |
  Nach allen Runden:
    |-- RICHTER (Beobachter): Aufgabe judge, Urteil
```

#### Recensio (Code-Prüfung)

Der Prüfer (Censor, Platz des Kritikers) liest und testet die Dateien, die der Verfasser (Auctor) in diesem Zug geändert hat (`_author_files`, aus `ToolKit.changed_documents`; Listen neu/geändert/gelöscht über `_file_lists`).

```
_recensio()
  |-- Censor-Toolkit: toolkit.restricted(RECENSIO_CENSOR_TOOLS)  (nur lesen und testen)
  |-- _review(Runde 0): Prüfung der Antwort, gehört zu keiner Runde
  |     System-Prompt: get_seat_system_prompt(..., REVIEW_ROLES, tools=RECENSIO_CENSOR_TOOLS):
  |     die Prüfung ersetzt die Alltags-Aufgabe des Agenten, Identität und Persönlichkeit bleiben
  |
  Schleife je Runde N (höchstens max_debate_rounds):
    |-- [LGTM] der Prüfung (und kein [WEITER]) -> bestanden, Ende
    |-- VERFASSER: Korrektur (Aufgabe review_fix), ändert die Dateien selbst
    |-- _review(Runde N): neue Prüfung
```

#### Symposion

```
run_symposion()
  |
  Je Runde bis max_debate_rounds, je Agent der Tischrunde (jede Runde neu aus der Sitzung gelesen):
    |-- System-Prompt (direct) + Regeln shared/symposion (+ shared/symposion_reflection ab Runde 2)
    |-- Messages: die ganze bisherige Unterhaltung, eigene Beiträge als assistant
    |-- Erinnerungen nur in Runde 1
    |-- stream_call() -> run_llm_stream()
```

Kein Sieger, keine Abstimmung. Ein abgeschalteter Agent verlässt die Runde; bleibt nur einer, antwortet er direkt (`plan_turn`).

### 5. Message Hub (eingehende E-Mail, Scheduler, Alarme)

Vollständig zustandslos. Die Nachricht läuft als Zug des Zug-Dienstes (`lib/turn_service.py`), derselbe, den die Vue-Oberfläche für den Chat nutzt: Absicht, Moduswechsel, Kompression, Diskussionsmodi und dieselbe Blase wie im Browser.

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
  |       |-- Ablauf wie in Abschnitt 1; je Agent agent_toolkit() ->
  |       |     prepare_agent_toolkit mit Stufe, Vertrauen und Metadaten der Herkunft
  |       |-- run_llm_stream()  <-- PIPELINE (Token-Ereignisse an offene Tabs;
  |       |     vorgelesen wird im Browser nicht, der Kanal spricht selbst)
  |       |-- Antworten als Blasen gespeichert, Titel im Hintergrund
  |       v
  |       TurnOutcome: Status, Agent, Antworten (TurnReply: text_clean, silent)
  |
  |-- 5. outbound_text(): alle Antworten des Zugs, bereinigt; mit Namenszeile,
  |       wenn ein anderer Agent als AIfred oder mehrere sprachen
  |-- 6. Auto-Reply über plugin.send_reply() (wenn der Channel always_reply
  |       hat oder sein Auto-Reply-Schalter an ist — E-Mail: SMTP)
```

### 6. Sitzungstitel

Verwendet **nicht** `run_llm_stream()`: einfaches, nicht streamendes `client.chat()` mit hartem Timeout (`SESSION_TITLE_TIMEOUT_SECONDS`, 300 s). Nach dem ersten Austausch startet `turn_service._start_title()` einen Hintergrund-Task (`_title`), wenn die Sitzung noch keinen Titel hat; der fertige Titel kommt als Ereignis `session_title`.

```
session_title.py: first_exchange() + generate_session_title()
  |-- Eingabe: erste Nutzer-Nachricht + erste Antwort (je max. 500 Zeichen; HTML-Tags des
  |     Nutzertexts und Thinking-Blöcke der Antwort entfernt; die Sprechermarke "[AIFRED]: " fällt weg)
  |-- Prompt: load_prompt("utility/chat_title")
  |-- Modell: das Modell des letzten Calls des Zuges samt dessen num_ctx
  |     (es ist das geladene, nichts wird für den Titel neu geladen);
  |     ohne Überschreibung das Automatik-Modell aus den Einstellungen
  |-- Optionen: temperature=0.3, num_predict=SESSION_TITLE_NUM_PREDICT (2000),
  |     enable_thinking=False, num_ctx
  |-- llm_client.chat() (NICHT streamend, Timeout SESSION_TITLE_TIMEOUT_SECONDS)
  |-- Bereinigung: Thinking-Blöcke, Anführungszeichen, Satzzeichen am Ende, max. 80 Zeichen
  |-- update_session_title(session_id, title)
```

### 7. Intent-Erkennung

Verwendet ebenfalls **nicht** `run_llm_stream()`: nutzt das kleine Automatik-Modell, aufgerufen von `turn_service._detect_intent()`.

```
intent_detector.py: detect_query_intent_and_addressee()
  |-- Modell: Automatik-Modell (effective_automatik_id), Prompt immer englisch
  |-- Context: AUTOMATIK_LLM_NUM_CTX (12288), wenn sich das Automatik-Modell
  |     von AIfreds unterscheidet; beim selben Modell AIfreds max_context (kein Reload)
  |-- Optionen: temperature=0.2, enable_thinking=False
  |-- Liefert: (addressee, detected_language, mode_switch_updates,
  |            is_pure_command, raw_response, load_time)
  |     addressee: eine Agenten-ID oder None
  |     language aus: de, en
  |     mode_switch_updates: gewünschte Konfigurationsänderung ({} wenn keine)
  |     is_pure_command: Nachricht ist NUR ein Modus-Wechsel-Befehl
```

Übersprungen wird der Aufruf bei einer Nachricht, die nur aus einer URL oder nur aus Bildern besteht, und bei Aufgaben des Systems (Job, Alarm): dann gilt die Sprache der Oberfläche.

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
  |-- Einstellung: agent_tuning[agent].thinking_by_model {Modell: Wahl}
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
build_llm_options(view, agent, model, temperature, num_ctx) -> LLMOptions;
thinking_fields(Wahl) -> enable_thinking, reasoning_effort
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
| Direkte Antwort (Chat, Kanal, Anrede) | `turn_service._respond` | `stream_call` -> `run_llm_stream` | Volles Toolkit, Agent entscheidet |
| Bild, Hauptmodell sieht | `turn_service._look_at_images` + `_direct_call` | `stream_call` -> `run_llm_stream` | Bilder in der Anfrage (VL Direct) |
| Bild, anderer Beschreiber | `turn_images.describe_images` | **Nein** — `llm_client.chat()` | Eine Beschreibung im Nutzer-Turn, danach normaler Zug |
| Kritische Prüfung, Auto-Konsens | `turn_debate._critique` | `stream_call` -> `run_llm_stream` | Kritiker, Richter, Verfasser; Abstimmung nur im Auto-Konsens |
| Tribunal | `turn_debate._tribunal` | `stream_call` -> `run_llm_stream` | Runden Angriff und Verteidigung, Richter zuletzt |
| Recensio | `turn_debate._recensio` | `stream_call` -> `run_llm_stream` | Censor mit eingeschränktem Toolkit, Auctor korrigiert |
| Symposion | `turn_debate.run_symposion` | `stream_call` -> `run_llm_stream` | N Agenten nacheinander |
| Message Hub | `process_inbound` | `turn_service.run_turn` -> `stream_call` -> `run_llm_stream` | Zustandslos, derselbe Zug wie der Vue-Chat, Auto-Reply |
| Kompression | `turn_service.compress_history`, `compress_tool_loop` | **Nein** — Zusammenfassung durch das Modell des nächsten Sprechers | Vor dem Zug, zwischen den Sprechern, in der Werkzeugschleife |
| Titel | `session_title.generate_session_title` | **Nein** — `client.chat()` | Nicht streamend, 300 s Timeout |
| Intent | `detect_query_intent_and_addressee` | **Nein** — `client.chat()` | Automatik-Modell |

---

## Wichtige Dateien

| Datei | Rolle |
|---|---|
| `aifred/lib/turn_service.py` | Zug des Servers (Vue-Chat, Kanäle, Scheduler, Alarme): `run_turn`, `TurnOrigin`, `TurnOutcome`, `stream_call`, `finish_call`, `compress_history` |
| `aifred/lib/turn_debate.py` | Diskussionsmodi: `plan_turn`, `run_debate`, `run_symposion` |
| `aifred/lib/debate_seats.py` | Plätze (Verfasser, Kritiker, Richter): `seat_prompt`, `round_rules`, `session_seats` |
| `aifred/lib/turn_images.py` | Bilder eines Zuges: Anfrage des sehenden Modells, Beschreibung, Drehen und Zuschneiden |
| `aifred/lib/llm_pipeline.py` | Einheitliche Chunk-Processing-Pipeline (`run_llm_stream`, `PipelineResult`) |
| `aifred/lib/context_manager.py` | History-Kompression (`summarize_history_if_needed`, `compress_tool_loop`, `LoopCompression`) |
| `aifred/lib/session_title.py` | Sitzungstitel (`first_exchange`, `generate_session_title`) |
| `aifred/lib/message_processor.py` | Message-Hub-Pipeline (`process_inbound`, `outbound_text`) |
| `aifred/lib/llm_client.py` | `LLMClient`, `build_llm_options()`, Backend-Abstraktion |
| `aifred/lib/message_builder.py` | `build_messages_from_llm_history()` — Perspektiv-Transformation |
| `aifred/lib/prompt_loader.py` | Prompt-Schichten: `get_agent_direct_prompt`, `get_agent_system_prompt`, `get_seat_system_prompt` |
| `aifred/lib/agent_memory.py` | `prepare_agent_toolkit()` — Memory- + Plugin-Tools |
| `aifred/lib/agent_config.py` | Agenten-Konfiguration (Anzeigenamen, Emojis, Modelle) |
| `aifred/lib/intent_detector.py` | Erkennung von Sprache, Adressat und Moduswechsel |
