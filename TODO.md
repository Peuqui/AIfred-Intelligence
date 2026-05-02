# AIfred TODO

## Grundprinzip: Security im Framework, nicht in Plugins

Security wird im System-Code erzwungen (plugin_registry, function_calling,
message_processor). Plugins koennen die Sicherheitsmechanismen NICHT umgehen.

Pipeline: Inbound Sanitization → Tier-Check → Tool-Aufruf → Output-Sanitization → Audit-Log

---

## Audio-Player Plugin

- [ ] Live-Test im Browser (AIfred soll WAV abspielen)
- [ ] Integration mit Scheduler: Weckerton zu bestimmter Uhrzeit
- [ ] Netzwerk-Playback (PulseAudio remote oder eigener Audio-Server fuer Pucks)
- [ ] **Musik-Streaming an Puck** — eigener Streaming-Kanal (nicht ueber TTS-Protokoll),
  Playlisten, Pause/Skip/Resume, setzt Bluetooth-Speaker am Puck voraus

---

## FreeEcho.2 Puck — AIfred Voice Interface

Separates Projekt: github.com/Peuqui/FreeEcho.2
Firmware-TODOs dort in TODO.md, hier nur AIfred-seitige Punkte.

### Wake-Word-basiertes Agent-Routing

**Status:** Firmware sendet bereits das Agent-Label (aus `[wakeword] label_N = agentname`),
aber AIfred ignoriert es und lässt die Automatik-LLM den Agent aus dem Transkript raten.

**Firmware (funktioniert):**
Puck sendet `{"type":"wake","room":"...","agent":"sokrates"}` — siehe
`FreeEchoDot2/firmware/src/freeecho2_client.c:901-916`.

**AIfred (fehlt):**
In `aifred/plugins/channels/freeecho2_channel/__init__.py:222-226` wird das
`wake`-Event empfangen, aber der `agent`-Key nicht ausgelesen. Die InboundMessage
erhält `target_agent="aifred"` als Default (Zeile 331-338), danach überschreibt
`detect_target_agent_via_llm` in `message_processor.py:204` den Agent mit dem
LLM-Rate-Ergebnis aus dem STT-Text.

**Konkret umzusetzen:**
- [ ] `_handle_text`: `agent` aus wake-Event auslesen, pro `room` in dict zwischenspeichern
      (analog zu `_devices` dict, aber mit TTL oder reset beim nächsten `wake`)
- [ ] `_handle_audio`: gecachten Agent in `InboundMessage.target_agent` setzen
      (Feld existiert bereits in `aifred/lib/envelope.py:23`)
- [ ] `message_processor.py`: wenn `target_agent` bereits gesetzt und nicht Default,
      Addressee-Teil der Intent-Detection überspringen. **Aber**: Intent-Detection-Call
      komplett laufen lassen (wegen Mode-Switches per Voice — siehe SSOT-Abschnitt unten).
- [ ] Edge Case: Action-Button (kein Wake-Word) → fällt auf LLM-Detection zurück wie bisher.

**Abhängigkeit:** Sinnvoll erst nach dem SSOT-Refactor (siehe unten), weil wir sonst
zuerst den LLM-Call einsparen und dann doch wieder brauchen für Mode-Switches.

---

## SSOT: Unified Inference Pipeline (Browser + Hub)

**Problem:**
Wir haben aktuell ZWEI parallele Inferenz-Pfade, die historisch getrennt gewachsen sind.
Der Hub-Pfad (Puck, Email, Telegram, Discord, Message Hub allgemein) unterstützt nur
einen Bruchteil dessen, was der Browser-Pfad kann. Das widerspricht unserer
SSOT-Doktrin und zwingt uns zu Duplikation, wenn wir Features wie Mode-Switches
per Voice oder Multi-Agent-Diskussionen in nicht-Browser-Kanälen wollen.

### Status Quo (Stand Refactor-Beginn)

**Browser-Pfad** (`aifred/state/_chat_mixin.py`):
- Reflex-State Objekt mit `self.active_agent`, `self.multi_agent_mode`, `self.research_mode`
- Mode-Switch-Anwendung bei Zeile 1033-1041:
  ```python
  if mode_switch_updates:
      if "active_agent" in mode_switch_updates:
          self.active_agent = mode_switch_updates["active_agent"]
      if "multi_agent_mode" in mode_switch_updates:
          self.multi_agent_mode = mode_switch_updates["multi_agent_mode"]
      ...
  ```
- Multi-Agent-Logik: `run_sokrates_analysis()`, `run_tribunal()`, `run_symposion()`
- Forced Research: `research_mode in ("quick", "deep")` → 3 / 7 Websites
- Automatik Research: `research_mode == "automatik"` → Tool-Use mit `web_search`
- Kein Research: `research_mode == "none"` → nur eigenes Wissen

**Hub-Pfad** (`aifred/lib/message_processor.py`):
- Stateless, lädt Session von Disk via `load_session(session_id)`
- `detect_target_agent_via_llm()` ruft `detect_query_intent_and_addressee()`,
  aber verwirft `_mode_switch` und `_remaining` (Zeile 124, Unterstrich-Prefix)
- `_call_engine()` (Zeile 298-402) ruft direkt `call_llm()` mit Single-Agent —
  KEINE Multi-Agent-Verzweigung, KEINE Research-Mode-Verzweigung
- Hardcoded `detected_intent="ALLGEMEIN"` (Zeile 388)
- Liest NICHT die Session-Config (`get_session_config(session_id)`) — weiß also gar
  nicht, in welchem Modus die Session läuft

**Session-Config** (`aifred/lib/session_storage.py:585-617`):
- Ist bereits der natürliche SSOT-Kandidat
- Enthält: `active_agent`, `multi_agent_mode`, `research_mode`, `symposion_agents`
- `update_session_config(session_id, **updates)` existiert und funktioniert
- Wird im Browser-Pfad gepflegt, im Hub-Pfad ignoriert

### Ziel-Architektur

**Ein gemeinsamer Inferenz-Einstiegspunkt**, z.B.
`aifred/lib/inference_pipeline.py::run_inference(session_id, user_text, source, ...)`:

1. Lädt Session-Config (SSOT)
2. Je nach `multi_agent_mode`:
   - `standard` → Single-Agent (`active_agent` aus Config)
   - `critical_review` / `devils_advocate` → AIfred + Sokrates
   - `auto_consensus` → mehrrundige Diskussion (respektiert `max_debate_rounds`)
   - `tribunal` → AIfred + Sokrates + Salomo
   - `symposion` → alle Agenten aus `symposion_agents`
3. Je nach `research_mode`:
   - `none` → kein Research
   - `automatik` → Research-Tools im Toolkit (LLM entscheidet)
   - `quick` / `deep` → forced Research (3/7 Websites) vorgeschaltet
4. Emittiert Events über den **Debug Bus** (existiert bereits: `aifred/lib/debug_bus.py`).
   KEIN direkter State-Zugriff.

**Browser-Pfad wird dünner Wrapper:**
- Nimmt User-Input aus UI
- Schreibt Mode-Änderungen in Session-Config via `update_session_config`
- Ruft `run_inference(...)`
- Konsumiert Debug-Bus-Events und rendert in die UI (Streaming-Box etc.)

**Hub-Pfad wird dünner Wrapper:**
- Empfängt InboundMessage
- Optional: wendet `mode_switch_updates` auf Session-Config an
- Ruft `run_inference(...)`
- Konsumiert Debug-Bus-Events für Hub-Notifications (toast, ghosting)

### Haupt-Hürde: State-Entkopplung in `multi_agent.py`

Aktuell tief gekoppelt an Reflex-State (`grep 'state\.' aifred/lib/multi_agent.py` zeigt >200 Hits).
Beispiele:
- `state._chat_sub()` — Reflex-Substate für Chat-History
- `state._streaming_sub()` — Reflex-Substate für Streaming-Output
- `state.stream_text_to_ui(text)` — direkte UI-Ausgabe
- `state.add_debug(msg)` — UI-Debug-Panel
- `state._effective_model_id(agent)` — Model-Resolution
- `state._sync_to_llm_history(agent, text)` — History-Update
- `state.set_tool_status(...)` — UI Tool-Status-Anzeige

**Entkopplungs-Strategie:**
Diese Methoden in zwei Kategorien teilen:
- **Pure Logik** (Model-Resolution, History-Update) → nach `aifred/lib/` auslagern,
  funktioniert mit Session-ID statt State
- **UI-Darstellung** (stream_text_to_ui, add_debug, set_tool_status) → durch
  Debug-Bus-Events ersetzen. Browser-Pfad konsumiert und rendert,
  Hub-Pfad konsumiert für Notifications.

### Umsetzungsplan (Schritte)

**Phase 1 — Inventur & Design (1 Session)**
- [ ] Vollständiger `grep 'state\.' aifred/lib/multi_agent.py` → Liste aller Zugriffe
- [ ] Klassifikation: Pure Logik vs. UI-Darstellung
- [ ] Design-Dokument: Event-Schema für Debug-Bus (welche Event-Typen brauchen wir?)
- [ ] Entscheidung: Async-Generator vs. Callback-basiert für Event-Emission

**Phase 2 — Pure Logik extrahieren (2-3 Sessions)**
- [ ] `_effective_model_id` aus `state._base` nach `aifred/lib/agent_config.py`
  (als `get_effective_model_for_agent(agent_id, session_config)`)
- [ ] `_sync_to_llm_history` nach `aifred/lib/session_storage.py`
  (als `append_to_llm_history(session_id, agent, text)`)
- [ ] History-Compression-Aufrufe entkoppeln (aktuell über State)
- [ ] Type-Tests: Alle neuen Funktionen mit mypy sauber

**Phase 3 — UI-Events über Debug-Bus (2-3 Sessions)**
- [ ] Event-Typen definieren: `AgentStreamEvent`, `ToolCallEvent`, `ToolResultEvent`,
      `AgentCompleteEvent`, `DiscussionRoundEvent`, etc.
- [ ] `state.stream_text_to_ui` → `emit(AgentStreamEvent(agent, chunk))`
- [ ] `state.add_debug` → bereits über `debug()` im Bus möglich
- [ ] Browser-Listener in `_chat_mixin.py`: konsumiert Bus-Events, ruft State-Methoden
- [ ] Hub-Listener in `message_processor.py`: konsumiert relevante Events für
      Notifications/Tool-Status

**Phase 4 — Unified `run_inference` bauen (1-2 Sessions)**
- [ ] `aifred/lib/inference_pipeline.py::run_inference(session_id, user_text, source, max_tier)`
- [ ] Lädt Session-Config, verzweigt nach `multi_agent_mode`
- [ ] Nutzt entkoppelte Multi-Agent-Funktionen aus Phase 2+3
- [ ] `_call_engine` in `message_processor.py` durch `run_inference` ersetzen
- [ ] `_chat_mixin.py` Inferenz-Code durch `run_inference` ersetzen
- [ ] Mode-Switch-Anwendung (`update_session_config`) VOR `run_inference` im Hub-Pfad

**Phase 5 — Mode-Switch im Hub aktivieren (kleine Session)**
- [ ] `_mode_switch` in `message_processor.py:124` NICHT mehr mit `_` prefixen
- [ ] `update_session_config(session_id, **mode_switch)` vor `run_inference`
- [ ] Audit-Log-Eintrag für Mode-Switch via Voice/Email/etc.
- [ ] Test: Puck sagt "Sokrates, starte ein Tribunal und recherchiere tief zu X"
      → Session-Config wird aktualisiert, Tribunal läuft

**Phase 6 — Wake-Word-Routing (siehe oben)**
- [ ] Erst nach Phase 5, damit Intent-Call für Mode-Switches bleibt und nur
      der Addressee-Teil via Wake-Word überschrieben wird

### Stolperfallen / offene Fragen

- **Streaming vs. Batch**: Browser braucht Token-Streaming für Live-UI, Hub kann
  Batch nach Fertigstellung verarbeiten. Event-Schema muss beide unterstützen
  (entweder AsyncGenerator mit Stream-Events, oder Bus mit Ack/Buffer).
- **TTS-Deferred-Logik** im Puck (`_ensure_tts_state`, `_force_tts_switch`):
  aktuell im FreeEcho-Channel. Bei Multi-Agent-Antworten im Hub müsste TTS ggf.
  mehrmals laufen — aktuell nicht designt dafür.
- **Multi-Agent in Email/Telegram**: Braucht die Welt wirklich ein Tribunal per Email?
  Ggf. pro Kanal konfigurierbar machen (Channel-Setting: `allow_multi_agent: true/false`).
- **Tool-Streaming-Events**: Research-Tools emittieren aktuell viele Debug-Messages
  über den Bus. Bei Hub muss gefiltert werden (nicht alles soll als Notification raus).
- **Settings vs. Session-Config**: Manche Settings sind global (`temperature_mode`,
  `backend_type`), andere per Session (`active_agent`). SSOT-Regel klar halten:
  - Global in `settings.json` → `load_settings()`
  - Per Session in Session-File → `get_session_config(session_id)`

### Was dieser Refactor obsolet macht

- Duplizierung zwischen `_chat_mixin.py` Inferenz-Code und `_call_engine` in
  `message_processor.py`.
- Kommentar "Mode-switch handling in the hub path is not yet supported"
  (`message_processor.py:130-132`) kann entfernt werden.

Nicht obsolet: `Settings Control Plugin` — das ist Vorläufer der
Tool-Call-first-Migration (siehe nächster Abschnitt) und läuft parallel zur
Automatik-LLM-MODE_SWITCH-Variante.

### Nice-to-Have nach Refactor

- [ ] Kanalübergreifendes Routing (eine Session über mehrere Kanäle) — siehe
      "Offene Verbesserungen" weiter unten, wird trivial mit SSOT-Pipeline
- [ ] Browser kann Hub-Sessions anzeigen/fortsetzen (eine Session = Set von
      Kanälen, Browser ist nur ein weiterer Kanal)

---

## Zukunftsweg: Tool-Call-first-Architektur (Ablösung der Automatik-LLM)

**Langfristiges Ziel:** Die separate Automatik-LLM (Intent-Detection-Pre-Call)
abschaffen. Stattdessen trifft die Haupt-LLM alle Routing-Entscheidungen selbst
via Tool-Calls. Das entspricht dem Industriestandard (ChatGPT, Claude, Qwen3.6-Agent).

### Warum

**Aktuell:** Pro User-Eingabe läuft ein Automatik-LLM-Call VOR der eigentlichen
Haupt-Inferenz und entscheidet: Addressee, Intent, Language, MODE_SWITCH. Das
bedeutet:
- Zwei Modelle im VRAM (oder Swap-Kosten zwischen beiden)
- Immer Extra-Latenz, auch wenn nichts zu entscheiden ist
- String-Parsing-Format (`INTENT|ADDRESSEE|LANG|MODE_SWITCH|REMAINING`) ist
  fragiler als Tool-Use-JSON
- Doppelte Logik-Pfade (Intent-Parser + Tool-Path)

**Ziel-Architektur:**
Haupt-LLM bekommt Tools wie:
- `switch_mode(mode="tribunal")` — Multi-Agent-Modus wechseln
- `switch_research(mode="deep")` — Research-Modus wechseln
- `delegate_to(agent="sokrates")` — Agent wechseln / delegieren
- `set_language(lang="en")` — Sprache umstellen (optional — meist implizit
  durch Antwort-Sprache der LLM)

Die LLM entscheidet selbst ob/wann sie diese Tools ruft. Addressee/Language
werden implizit durch die Antwort der LLM (und User-Sprache des Inputs)
abgehandelt.

### Risiken

- **Tool-Use-Schwäche kleiner Modelle**: 3B / 7B Lokal-Modelle sind oft
  unzuverlässig im strukturierten Tool-Calling. User mit schwächerer
  Hardware könnten das Feature nicht nutzen können.
- **Regression bei Multi-Agent-Routing**: Heute entscheidet die Automatik-LLM
  deterministisch, an wen delegiert wird (via ADDRESSEE-Feld). Eine Haupt-LLM,
  die selbst "delegieren" soll, könnte das vergessen oder falsch machen.
- **Prompt-Template-Auswahl**: Einige Templates werden heute VOR der Haupt-
  Inferenz anhand der erkannten Sprache gewählt (`prompts/de/` vs `prompts/en/`).
  Wenn die Sprache erst aus der Antwort der Haupt-LLM kommt, ist es zu spät.
- **Sampling-Wechsel mid-generation nicht möglich** *(vom User betont)*:
  Sampling-Parameter (temperature, top_p, top_k, repeat_penalty, …) sind
  **pro Request** frei setzbar — kein Model-Reload nötig. Einschränkung ist
  also nicht der Reload, sondern: Innerhalb **einer laufenden Generation**
  kann der Sampler nicht umgeschaltet werden.
  - **Automatik-LLM heute**: Intent ist VOR der Haupt-Inferenz bekannt →
    Haupt-LLM kann direkt mit intent-spezifischem Preset aufgerufen werden
    (z. B. low-temp für `FAKTISCH`, high-temp für `KREATIV`). Das Feature
    ist verfügbar, wird in der Praxis aber kaum genutzt.
  - **Tool-Call-first**: Intent wird erst *während* der Inferenz entdeckt
    (via Tool-Call der Haupt-LLM). Der aktuelle Turn läuft mit dem
    Default-Preset zu Ende. Ein Preset-Wechsel kann erst beim **nächsten**
    User-Turn greifen.
  - **Mögliche Mitigation A**: Mini-Intent-Call bleibt erhalten (nur Temperatur-
    Klasse, keine Addressee/Mode-Logik) — sehr kurzer Prompt, geringe Latenz,
    entkoppelt vom großen Routing-Entscheid.
  - **Mögliche Mitigation B**: Tool `switch_mode(mode="creative")` ändert neben
    der Session-Config auch das aktive Sampling-Preset für den nächsten
    User-Turn.
  - **Mögliche Mitigation C**: Sampling-Preset als User-Voice-Command
    (`"antworte kreativ"` → Tool-Call → Preset-Wechsel beim nächsten Turn).
    Verlagert Kontrolle zum User, aber robust.
  - Entscheidung vor Phase T2: Welcher Ansatz (A/B/C oder keine Mitigation,
    falls Feature bewusst gestrichen wird)?

### Umsetzungsplan (nach SSOT-Refactor)

**Phase T1 — Settings Control Plugin bauen**
- [ ] `plugins/tools/settings_control/tools.py` mit `switch_mode`,
      `switch_research`, `delegate_to`
- [ ] Tools ändern Session-Config via `update_session_config(session_id, ...)`
- [ ] UI-Flag im Plugin-Manager: "Tool-Call Routing aktivieren" (Opt-in)
- [ ] Nur für Agenten aktiv, die in `agent_config` als tool-use-fähig markiert sind

**Phase T2 — Parallelbetrieb testen**
- [ ] Setting `routing_strategy`: `"automatik_llm"` (Default) | `"tool_call"` | `"hybrid"`
- [ ] In `detect_target_agent_via_llm`: wenn `tool_call`-Modus, Automatik-LLM-Call
      überspringen, Default-Agent aus Session-Config nehmen
- [ ] Mit Qwen3.6, Claude, GPT testen — Baseline: läuft Mode-Switch per Voice
      zuverlässig?
- [ ] Metriken sammeln: Fehlentscheidungen pro Tool-Call (Modell vergisst Tool,
      ruft falsches Tool, etc.)

**Phase T3 — Hybrid-Modus für schwache Modelle**
- [ ] Wenn Haupt-Modell nicht tool-use-fähig (Flag `tool_capable: false` in
      `agent_config`): Fallback auf Automatik-LLM
- [ ] Wenn tool_capable: nur Haupt-LLM, keine Automatik-LLM
- [ ] Sprach-Pre-Detection als eigener Mikro-Call behalten (deutlich kleiner
      als aktueller Automatik-Call) — oder als separates Feature via FastText
      / langdetect aus dem User-Text ableiten (kein LLM nötig)

**Phase T4 — Default umdrehen**
- [ ] Wenn Tool-Call-Pfad in der Wildnis stabil läuft: Default auf `tool_call`
- [ ] Automatik-LLM-Prompt-Templates in eigenen Folder verschieben (`prompts/legacy/`)
- [ ] Dokumentation aktualisieren

**Phase T5 — Automatik-LLM entfernen** (optional, irgendwann)
- [ ] `intent_detector.py` auf Pure-Language-Detection reduzieren (falls nötig)
- [ ] `detect_target_agent_via_llm` entfernen
- [ ] Automatik-Model-Settings aus UI entfernen
- [ ] Memory-Einsparung: Automatik-Modell muss nicht mehr im VRAM gehalten werden

### Empfehlung zum Timing

**Nicht überstürzen.** Die Automatik-LLM funktioniert und ist deterministisch.
Erst SSOT-Refactor abschließen (der macht unabhängig vom Routing die Pipeline
sauber), dann Tool-Call als Opt-in parallel fahren und in der Praxis messen.
Erst wenn Tool-Call-Ansatz über mehrere Modelle und reale Nutzung stabil läuft:
Default umdrehen. Automatik-LLM als Fallback für schwache Modelle belassen
oder entfernen — das ist die letzte Entscheidung, nicht die erste.

---

## Mode-Switch- & Routing-Lücken (Voice-Steuerung)

**Stand 2026-04-25:** Browser-Pfad hat funktionierenden Mode-Switch via
Automatik-LLM (siehe `_parse_mode_switch` in
[`intent_detector.py`](aifred/lib/intent_detector.py)). Schon umgesetzt:

- `agent=<id>` → setzt `active_agent` (Sticky)
- `research=none|quick|deep|automatik`
- `multi=standard|tribunal|symposion|critical_review|auto_consensus`

### Lücke 1: Symposion mit ad-hoc Agenten-Liste

**Problem:** *"Starte Symposion mit Codine, HAL und Rabbi"* → wird zu
`multi=symposion` geparsed, aber die teilnehmenden Agenten kommen aus
der UI-Konfig, nicht aus dem Voice-Befehl. Eine ad-hoc Auswahl per
Sprache ist nicht möglich.

**Lösung:**
- [ ] Neuen Schlüssel `symposion_agents=<id1>,<id2>,...` im
      Mode-Switch-Format ergänzen
- [ ] `_parse_mode_switch` erweitern: kommagetrennte Agent-IDs
      validieren gegen `agent_config`, ungültige verwerfen
- [ ] Im Browser-Pfad ([`_chat_mixin.py`](aifred/state/_chat_mixin.py))
      `symposion_agents` aus `mode_switch_updates` in die Session-Config
      übernehmen
- [ ] Prompt-Beispiele in
      [`intent_detection.txt`](prompts/en/automatik/intent_detection.txt)
      ergänzen: *"Start symposion with Codine, HAL and Rabbi"* →
      `multi=symposion,symposion_agents=codi,hal,rabbi`

### Lücke 2: Hub-Pfad (Puck) ignoriert Mode-Switch komplett

**Problem:** [`message_processor.py:124`](aifred/lib/message_processor.py#L124)
verwirft den `mode_switch`-Output mit Underscore-Präfix:

```python
intent, addressee, lang, _mode_switch, _remaining, _raw = await detect_query_intent_and_addressee(...)
# NOTE: Mode-switch handling in the hub path is not yet supported.
```

→ *"Hey AIfred, schalte um auf Tiefrecherche"* via Puck ändert nichts.

**Lösung:** Im Zuge des SSOT-Refactors (siehe oben) den Mode-Switch
auch im Hub-Pfad anwenden — über `update_session_config(session_id,
**mode_switch_updates)`. Browser und Hub teilen dann denselben
Mechanismus.

### Lücke 2b/2c/3: Universal Active-Agent Routing (vereinheitlicht)

*Diese drei Lücken hängen zusammen — eine einheitliche Regel löst alle drei.*

**Leitidee (vom User):** *"Jede explizite Adressierung — Wake-Word,
Voice-Switch, UI-Klick oder Inline-Anrede — schaltet `active_agent`
persistent um. Folge-Turns ohne Anrede laufen automatisch über
`active_agent`."*

Damit entfällt der separate "Sticky Follow-up"-Mechanismus: jede
Adressierung ist sticky, bis sie explizit wieder gewechselt wird.

#### Aktuelle Bugs in dieser Logik

**Bug A — Voice-Mode-Switch persistiert nicht**
[`_chat_mixin.py:1039`](aifred/state/_chat_mixin.py#L1039) setzt
`self.active_agent` im State, ruft aber **kein**
`_persist_session_config()`. UI-Klick (`set_active_agent()`) macht
das richtig — Voice-Switch ist inkonsistent.

**Bug B — Inline-Anrede setzt active_agent nicht um**
*"Hey Sokrates, was ist X?"* setzt `addressed_to=sokrates` für genau
diesen Turn. `active_agent` bleibt unverändert. Folge-Turn ohne
Anrede fällt auf `active_agent` zurück (typischerweise "aifred").

**Bug C — Hub-Pfad ignoriert active_agent**
[`message_processor.py:204`](aifred/lib/message_processor.py#L204):
```python
agent, intent, lang = await detect_target_agent_via_llm(text)
# Default "aifred", Session-Config nicht gelesen.
```
Selbst wenn der Browser `active_agent=pater` korrekt persistiert
hätte, würde der Puck es ignorieren.

**Bug D — Mode-Switch-Parser matcht nur Agent-IDs**
[`intent_detector.py:65-70`](aifred/lib/intent_detector.py#L65):
*"Schalt auf HAL 9000"* → LLM produziert vermutlich `agent=hal 9000`
oder `agent=HAL 9000` → matcht nicht gegen `agents.keys()` (die ID
ist `hal`). Verworfen.

Inkonsistent mit der **Addressee-Erkennung**
([`intent_detector.py:219-221`](aifred/lib/intent_detector.py#L219)),
die sowohl ID als auch `display_name.lower()` matcht.

#### Einheitliche Lösung (SSOT-konform)

- [ ] **Helper-Funktion `set_session_active_agent(session_id, agent_id)`**
      in `session_storage.py` — schreibt `active_agent` per
      `update_session_config(...)` und ist die einzige autorisierte
      Stelle dafür. Wird von allen Routing-Pfaden gerufen.

- [ ] **Routing-Priorität** klar definieren und an *einer* Stelle
      implementieren (idealerweise im SSOT-Refactor von Lücke 2):
      ```
      1. Wake-Override (Plugin-Hint)         → schaltet active_agent
      2. Voice-Mode-Switch (`agent=...`)     → schaltet active_agent
      3. LLM-Addressee aus aktuellem Query   → schaltet active_agent
      4. (kein Match) → use active_agent     → keine Änderung
      5. (kein active_agent) → "aifred"      → keine Änderung
      ```
      Schritte 1-3 schreiben jeweils per `update_session_config`
      durch. Browser-Reflex-State liest dieselbe Quelle.

- [ ] **Voice-Mode-Switch im Browser** ergänzen:
      `_persist_session_config()` muss bei Änderung an `active_agent`,
      `multi_agent_mode`, `research_mode`, `symposion_agents` greifen.

- [ ] **Inline-Anrede schaltet active_agent**: Wenn LLM-Addressee !=
      heute-aktiver Agent → persistieren.

- [ ] **Hub-Pfad nutzt Session-Config**: Wenn weder Wake-Override noch
      Voice-Switch noch LLM-Addressee einen Agent liefern → Default
      ist `active_agent` aus der Session-Config (statt "aifred"
      hardgecodet).

- [ ] **Parser akzeptiert Display-Name** beim `agent=`-Switch
      (Konsistenz mit Addressee-Erkennung). Plus optional:
      `aliases: [...]`-Feld in `agent_config` für Vosk-Phonetik
      ("Hell 9000" → `hal`).

- [ ] **Prompt-Hinweis ergänzen** in
      [`intent_detection.txt`](prompts/en/automatik/intent_detection.txt):
      *"For agent= mode-switch, ALWAYS use the lowercase ID
      (e.g. `hal`), never the display name (e.g. `HAL 9000`)."*

#### Geklärte Design-Entscheidung: Sticky-only

**Diskutiert und entschieden 2026-04-25:**
Jede explizite Adressierung schaltet `active_agent` sticky um.
Es gibt **keinen** Single-Shot-Modus, **keinen** Voice-*"zurück"*-
Befehl und **keinen** State-Stack.

**Recovery ist trivial:** Der User adressiert beim nächsten Turn
einfach den gewünschten Agent direkt (*"Hey AIfred, weiter mit ..."*).
Direkte Adressierung deckt alle Wechsel-Szenarien ab — vorwärts,
zurück, oder Sprung zu jedem beliebigen Agent. Kein zusätzliches
Vokabular nötig.

**Begründung:**
- KISS: eine Regel, keine Sonderfälle.
- In der Praxis dominieren fortlaufende Gespräche, nicht Einmal-Fragen.
- Direkte Adressierung ist genauso schnell wie ein *"zurück"*-Befehl,
  aber unmissverständlich.

#### Geklärte Design-Entscheidung: kein Pattern-Fallback im Code

**Diskutiert und entschieden 2026-04-25** (revert von Commit `c81d071`):
Es gibt **keinen** Code-Fallback der den Query selbst per Regex auf
*"Hey Alfred, ..."*-Muster scannt, wenn die LLM kein Addressee setzt.

**Begründung:**
- CLAUDE.md verbietet explizit *"automatische Fallbacks ohne Diskussion"*
  und *"Defensive Programming ohne Grund"*.
- Browser hat zuverlässige UI-Buttons — User klickt den gewünschten
  Agenten direkt an. Voice-Adressierung im Browser ist Komfort, nicht
  Notwendigkeit.
- Puck adressiert primär über Wake-Word (deterministisch, vom Server-
  side Wake-Override empfangen). Inline-Voice-Anrede *"Hey Alfred"*
  am Puck ist sekundärer Pfad.
- LLM-Versagen ist akzeptabel — bessere User-Experience: "LLM hat sich
  verschluckt, AIfred antwortet" als ein zusätzlicher Code-Pfad mit
  potentiellen False Positives bei Edge Cases.

**Routing-Pipeline final:**
1. Wake-Override (Channel-Hint, deterministisch)
2. Mode-Switch `agent=...` (Voice-Befehl, LLM-erkannt + Parser-validiert)
3. LLM-Addressee aus aktuellem Query (wenn LLM was zurückgibt)
4. Sticky-Fallback: ``active_agent`` aus Session-Config (UI-Klick / vorher gesetzt)
5. Default: ``"aifred"``

Wenn 3. wegen schwacher LLM ausfällt, greift 4. — User sieht den
"falschen" Agent antworten und korrigiert per UI-Klick (oder direkt
*"Hey AIfred, ..."* in einem zweiten Anlauf, das funktioniert weil
der Sticky-Code den klar adressierten Agent dann übernimmt).

---

## Hub-Channel @-Adressierung (E-Mail / Discord / Telegram / Signal)

**Idee (vom User 2026-04-25):** In Multi-User-Channels wie Discord ist
die `@username`-Konvention etabliert. Analog könnten Hub-Channels einen
deterministischen `@<agent_id>`-Parser bekommen — Vorteile:

- Funktioniert ohne LLM-Detection (deterministisch, keine Halluzinationen)
- Integration in Plugins ist trivial (Pre-Parser vor `process_inbound`)
- Konsistent mit Browser-UI (Klick) und Puck (Wake-Word) — alle drei
  Routing-Wege sind explizit, keine Heuristik

**Vorgeschlagene Syntax:**
- `@aifred Wie spät ist es?` → `target_agent="aifred"`
- `@hal Was siehst du?` → `target_agent="hal"`
- `@codi @rabbi Diskutiert mal` → kombiniert mit Symposion-Mode (zukünftig)

**Umsetzung:**
- [ ] Pre-Parser in `message_processor.py` der vor LLM-Detection prüft
      ob der Text mit `@<word>` startet, gegen `resolve_agent_id` matcht,
      bei Match: `message.target_agent` setzen + Token strippen.
- [ ] Channel-spezifische Konventionen respektieren (Discord nutzt
      `<@user_id>`-Mention, E-Mail vermutlich plain `@id`).
- [ ] Sticky-Logik gleich wie überall: `@-Adressierung` schaltet
      `active_agent` persistent um.

**Priorität:** Niedrig — solange E-Mail/Telegram/Signal manuell
funktionieren ohne explizites Routing (Default-AIfred reicht meistens).
Wird interessant wenn echte Multi-Agent-Konversationen über
Text-Channels stattfinden.

---

## Calibration-Optimierungen für Hybrid-Modus

**Stand:** Hybrid-Mode läuft seit 2026-04-25 wieder (commit `c421a82`, fix für
Health-Timeout und Kill-Reaping). Bei MiniMax-M2.7-UD-Q4_K_S + MOSS-TTS
hat die Calibration eine Hybrid-Konfig erzeugt, aber zwei Verbesserungen
sind beim Lauf aufgefallen:

### 1. Greedy first-fit lässt Headroom liegen

**Beobachtung:** Hybrid-Optimizer in
[`flow.py:_calibrate_hybrid`](aifred/lib/calibration/flow.py) probiert
feste Context-Targets in absteigender Reihenfolge `[native, 131k, 65k,
32k, 16k]`, akzeptiert das erste passende Resultat, und stoppt. Beim
MiniMax-MOSS-Lauf:

- 196k / 131k / 65k → RAM insufficient
- 32k mit ngl=53 → ✓ verifiziert mit **7053 / 5133 / 2963 / 4463 MB
  Headroom** auf den vier GPUs

→ Bei minimal 2.9 GB Headroom pro GPU wäre vermutlich 49k oder 65k
Context drin gewesen, oder höheres ngl bei gleichem Context. Wurde
aber nicht probiert, weil first-fit stoppt.

**Lösung:** Nach erstem erfolgreichen Hit eine zweite Suchrunde
starten — Optimum suchen, nicht first-fit:
- Binary-Search Context nach oben (zwischen aktuellem Erfolg und
  vorherigem RAM-Insufficient-Target)
- ngl schrittweise erhöhen, bis Verify scheitert
- Beides kombinieren: maximalen Context bei maximalem ngl finden

**Zeit ist nachrangig** — Calibration läuft pro Modell einmalig,
dafür soll das Ergebnis optimal sein. Auch wenn 3-5 zusätzliche
Verify-Runs ~5-8 Minuten extra Zeit kosten: das amortisiert sich
über die Lebensdauer des Profils.

### 1b. Nicht-TTS-GPUs werden im Hybrid-Mode unnötig mitreduziert

**Beobachtung:** Vergleich Base vs. MOSS-Hybrid für MiniMax:

```
            CUDA0   CUDA1   CUDA2   CUDA3
Base        20      21      10      11      (kein TTS)
MOSS-TTS    19      14      10      10      (MOSS belegt 14 GB auf CUDA1)
                                            
Diff:       -1      -7       0      -1
```

Erwartet wäre eigentlich `20:14:10:11` (nur CUDA1 zurücknehmen, weil
TTS dort sitzt). Stattdessen wurden auch CUDA0 und CUDA3 leicht
reduziert (–1 Layer je), obwohl die GPUs **kein TTS-belastetes
Budget** haben — sie hatten in Base bereits 20 bzw. 11 Layer mit
ausreichend Headroom.

**Warum?** `_seed_tensor_split` in
[`flow.py`](aifred/lib/calibration/flow.py) verteilt ngl proportional
zur "verfügbaren VRAM" pro GPU. Wenn das Gesamt-ngl von 99 auf 53
fällt (CPU-Offload), wird auch der Anteil pro GPU proportional
geschrumpft — nicht nur die TTS-belastete GPU.

**Idee:** Im Hybrid-Mode den Layer-Count pro GPU **nicht unter den
Base-Wert** drücken, solange das VRAM-Budget der GPU es erlaubt.
Konkret: cpu_layers nur von der TTS-belasteten GPU abziehen, nicht
proportional verteilen. Das würde im MiniMax-MOSS-Fall direkt 2 Layer
mehr auf GPU geben (CUDA0+CUDA3) und die Inferenz beschleunigen.

### 2. KV=f16 ist im Hybrid-Mode Overhead

**Beobachtung:** Hybrid-Pfad ruft `verify` mit hardcodiertem KV=f16
([`flow.py:1007`](aifred/lib/calibration/flow.py)). Bei einem Q4-Modell
(wie MiniMax-Q4_K_S) ist KV-Cache in f16 deutlich überdimensioniert —
q8_0 würde den KV-Footprint **halbieren** und entsprechend GPU-VRAM
freigeben, ohne nennenswerte Qualitätseinbußen.

**Konkret bei MiniMax-MOSS:** ctx=32k mit f16 KV-Cache braucht für ein
122-GB-Q4-Modell mehrere GB pro GPU. Mit q8_0 KV würde der gleiche
Context entweder ~50% weniger VRAM brauchen → mehr Layer auf GPU
(höheres ngl) → schnellere Inferenz, ODER mehr Context bei gleichem
ngl drin sein.

**Idee:** Hybrid-Pfad sollte KV-Quant analog zur GPU-only-Logik wählen
(q8_0 als Default für Hybrid, weil dort Tempo zweitrangig ist und VRAM
knapp). Oder direkt `LLAMACPP_CALIBRATION_PRECISION` aus der Config
respektieren statt f16 hartzucodieren.

### Konkretes Calibration-Ergebnis MiniMax-M2.7-UD-Q4_K_S (für Tracking)

| Variante | Mode | Context | ngl | KV | Tensor-Split | Tightest GPU |
|---|---|---|---|---|---|---|
| Base | GPU | 83.712 | 99 | q8_0 | 20:21:10:11 | CUDA3 1.7 GB |
| -tts-xtts | GPU | 81.408 | 99 | q8_0 | 20:21:10:11 | CUDA1 0.5 GB |
| -tts-moss | Hybrid | 32.768 | 53 | f16 | 19:14:10:10 | CUDA2 3.0 GB |

→ MOSS-Variante zeigt deutlichen Headroom-Spielraum, der mit den oben
genannten Optimierungen besser ausgenutzt werden könnte.

---

## Security-Verfeinerung

### Guard-LLM (S8)
- [ ] Kleines Modell als Vorfilter (safe/suspicious/malicious)
- [ ] Bei suspicious: Tier runterstufen
- [ ] Bei malicious: Block + Audit-Log
- [ ] Konfigurierbar pro Channel

### Action Confirmation Verfeinerung
- [ ] Rueckfrage an Sender statt hartem Block (Telegram Inline Keyboards etc.)
- [ ] Review-Queue fuer Cron-Jobs
- [ ] Optional: PIN/zweiter Faktor

### Erweiterte Output-Sanitization
- [ ] Entropy-Detection: Hochentropie-Strings flaggen
- [ ] Pattern-Liste konfigurierbar/erweiterbar

### Information Flow Control (S10)
- [ ] Taint-Tracking: Labels propagieren durch alle Schritte
- [ ] Cross-Channel-Exfiltration verhindern
- [ ] Network Egress Control (Default-Deny)

### RAG/Vector-DB Haertung
- [ ] Untrusted-Channel-Daten bekommen niedrigen Trust-Score
- [ ] Periodisches Auditing/Purging des Vector-Cache
- [ ] Namespace-Isolation pro Channel/Sender

---

## Plugins (spaeter)

### Community & Forum Plugins
- [ ] Reddit Plugin: lesen, posten, kommentieren, Subreddit-Monitoring
- [ ] Hacker News Plugin: lesen, suchen (read-only)
- [ ] Discourse Plugin (optional)

### Neue Kanaele
- [ ] Signal Plugin (signal-cli-rest-api Docker)

### Neue Tool Plugins
- [ ] **Settings Control Plugin** — Vorläufer der Tool-Call-first-Migration
      (siehe "Zukunftsweg: Tool-Call-first-Architektur"). Puck/Haupt-LLM bekommt
      Tools `set_discussion_mode()`, `set_research_mode()`, `set_active_agent()`.
      Erstmal parallel zur Automatik-LLM-MODE_SWITCH-Variante, als Opt-in-Pfad.
- [ ] RSS/News Feed Plugin: Nachrichten-Quellen ueberwachen und zusammenfassen
- [ ] Home Assistant Plugin: Smart Home Geraete steuern (REST API)
- [ ] **Audio-Transkription Plugin**: Watchfolder fuer Audio-Dateien (z.B. Memos,
      Meeting-Recordings). Whisper ist schon installiert → neu hereingelegte
      Files automatisch transkribieren und als Text ablegen / in RAG indexieren.
- [ ] Kalender-Sync Plugin: Google Calendar / CalDAV (unabhaengig von EPIM)

### Google Suite (OAuth-basierte Plugins)

**Architektur-Entscheidung (2026-04-19):**
- **Google Orchestrator** als einzelner ToolPlugin-Eintrag (`plugins/tools/google/`)
- Sub-Services in Unterordnern: `calendar/`, `contacts/`, `drive/`, `tasks/`
- Jeder Sub-Service per Toggle aktivierbar (settings.json im Plugin-Verzeichnis)
- **Ein OAuth-Flow** fuer alle Services — Scopes werden aus aktiven Sub-Services aggregiert
- OAuth-Broker bereits implementiert: `aifred/lib/oauth/` (Fernet-verschluesselt, CSRF-State)
- `ToolPlugin` hat keine Settings-UI → **Option B gewaehlt**:
  - Google-Plugin implementiert `load_settings()`/`save_settings()` selbst
  - Settings-Accordion bekommt neuen "Tool Plugins"-Block fuer Toggles + Credentials
  - Dieses Muster dann auch fuer Home Assistant, RSS, etc. wiederverwendbar

**Naechste Schritte:**
- [ ] `plugins/tools/google/__init__.py` — Orchestrator mit Scope-Aggregation + settings.json
- [ ] `plugins/tools/google/calendar/tools.py` — Calendar CRUD Tools
- [ ] `plugins/tools/google/contacts/tools.py` — Contacts lesen/suchen
- [ ] `plugins/tools/google/drive/tools.py` — Drive readonly (optional)
- [ ] `plugins/tools/google/tasks/tools.py` — Tasks sync (optional)
- [ ] `plugins/tools/google/i18n.json` — Labels fuer Credentials + Toggles
- [ ] `plugins/tools/google/settings.json` — Default: calendar+contacts an, drive+tasks aus
- [ ] Settings-Accordion: "Tool Plugins"-Block fuer ToolPlugins mit settings.json

**Noch offen:**
- [ ] **Google Calendar Plugin**: Termine lesen, erstellen, aendern, loeschen;
      Konfliktpruefung, Recurrence-Support. Wakeup vor Terminen an Puck.
- [ ] **Google Contacts Plugin**: Kontakte durchsuchen, fuer Email/Telegram/
      Discord-Empfaenger-Aufloesung ("schreib Max" → findet Email-Adresse).
      Bidirektional: Neue Kontakte anlegen lassen.
- [ ] **Google Drive Plugin** (optional): Dokumente durchsuchen/lesen in
      Research-Pipeline; Upload von generierten Artefakten (Reports, Code).
- [ ] **Google Tasks Plugin** (optional): Sync mit AIfred-Scheduler / Todo-Listen.

### KI-gestuetzte Kalibrierung
- [ ] LLM-basierte Schaetzung der optimalen Kalibrierungsparameter (Proof of Concept abgeschlossen)
- [ ] Implementierung als Alternative zum Binary-Search-Algorithmus
- [ ] Cloud-API oder CPU-only Modell fuer Schaetzung (kein VRAM-Verbrauch)

---

### UI Verbesserungen
- [ ] Tages-Separatoren bei Datumswechsel in Chat (Messenger-Stil)
- [ ] Clickable Tooltips: Hilfe-Modale fuer alle UI-Bereiche (Agenten-Editor, etc.)
- [ ] **Scheduler UI: Benutzerfreundliche Zeiteingabe** (statt rohem Cron-Textfeld)
  - Cron: 5 separate Inputs (Minute, Stunde, Tag, Monat, Wochentag) mit Presets
    ("Jeden Tag um 8", "Werktags um 9", "Jede Stunde", "Jeden Montag")
  - Interval: Zahleneingabe + Einheit-Dropdown (Minuten/Stunden/Tage)
  - Once: Datum+Uhrzeit-Picker
  - Bug: Schedule-Feld aendert sich nicht beim Typ-Wechsel (cron/interval/once)

## Offene Verbesserungen

- [ ] Multi-Email-Konto Support (mehrere IMAP/SMTP Accounts, Rechte pro Konto)
- [ ] Kanaluebergreifendes Routing (eine Session ueber mehrere Kanaele — Telegram/Discord/Email/Browser)
      — wird trivial nach dem SSOT-Refactor (siehe oben)
- [ ] Research-Pipeline State-Abhaengigkeit weiter reduzieren
- [ ] Inbound-Sanitization Strictness pro Channel konfigurierbar
- [ ] Session Memory Sanitization nach Job-Ende
- [ ] Audit-Log UI Filter (Channel, Tool, Zeitraum)

---

## RAG / Embedding-Pipeline (offen nach 2026-05-02 Migration)

Seit BGE-M3-Migration und Token-Chunker — folgende Punkte fuer die naechste
Iteration:

- [ ] **README + zentrale Docs auf den heutigen Stand bringen.** Viele
  Aenderungen sind unkommunziert, betrifft mindestens:
  - BGE-M3-Embedding (statt nomic-embed-text-v2-moe) — 8192 Token Context,
    1024-dim, multilingual; ChromaDB-Collections sind dimension-spezifisch
    inkompatibel zwischen Modellen
  - Token-genauer Chunker mit Qwen3-Tokenizer (Char-Heuristik nur als
    Fallback); `DOCUMENT_CHUNK_SIZE = 800` Tokens
  - Embedding-Mode-Switch: GPU bei Index (`mode="index"`,
    `keep_alive=60s`), CPU bei Query (`mode="query"`, `keep_alive=1800s`).
    Document-Store haelt zwei separate `OllamaEmbeddingFunction`-Instanzen
  - `delete + upsert` statt nur `upsert` in `index_document` —
    Zombie-Chunk-Fix bei verkleinerten Dateien
  - Document Manager UI: Bulk-Index-Button (gruenes Database-Icon im
    Header), rekursive Datei-Anzahl pro Ordner in der File-Liste
  - `search_documents` mit `folder`-Parameter (exact match, keine
    Wildcards) und Chunk-Nachbar-Retrieval (`neighbor_window=1`,
    `_neighbor=true` markiert Augmentations-Chunks)
  - `DOCUMENT_SEARCH_MAX_RESULTS = 100` als Hard-Cap (Konstante in
    config.py, keine Hardcodierung mehr)
  - Document-Manager Status-Meldungen jetzt via `rx.toast` statt
    Status-Zeile (SSOT zum restlichen Code)
  - Sefaria-Downloader (`scripts/download_judaica.py`) +
    Verifikations-Skript (`scripts/verify_judaica.py`) mit
    Schema-Inflation-Check
  - Personality-Prompts fuer Rabbi Shmuel + Pater Tuck jetzt mit
    Wissensbasis-Hinweis (welche Folder fuer welche Quelle, mit
    Anti-Konfabulations-Klausel)
  - Symposion-Modus: Reflection-Prompt-Layer ab Runde 2 (Variante D —
    Augmentation, kein Replacement); Title-Generation greift jetzt auch
    im Symposion (SSOT-Fix in `_chat_mixin.py:1316-1325`)
  - Tool-Output-Cap: `TOOL_OUTPUT_TOTAL_INPUT_RATIO = 0.75` —
    JSON-aware Truncation der Tool-Results, ContextVar-basiert pro
    Inferenz, gilt fuer alle Tools
  - Multi-Agent: Agent-Prefix `[Sokrates]` in Tool-Call-Debug-Zeilen
    (greift nur in Multi-Agent-Modi)
  - Tool-Result-Token-Count im Debug-Panel (lokalisiert formatiert)
  - System-Agenten (`role=system`) automatisch aus Symposion-Auswahl
    ausgeschlossen — Calibration faellt damit raus, kuenftige interne
    Agenten ohne Code-Aenderung mit
  - Orphan-Cleanup im Settings → Datenbank-Panel
    (`fm.list_orphaned()` + Bulk-Delete-Button, gruppiert pro
    Dokument)
  - File-Manager als Single Source of Truth fuer FS+ChromaDB-Operationen
    (`aifred/lib/file_manager.py` — wird vom Workspace-Plugin und
    Document-UI genutzt)

- [ ] **Embedding-Modell-Strategie verfeinern.** Aktuell: GPU bei Index,
  CPU bei Query — global konfiguriert. Verbesserungen:
  - Dynamische GPU-Auswahl: schnellste verfuegbare Karte beim Index-Modus
    (statt fester GPU-0-Default in Ollama)
  - Vor Embedding-Start: pruefen ob LLM-Modell entladen werden muss oder
    genuegend VRAM-Reserve besteht; ggf. LLM kurz pausieren
  - Bei aktiver LLM-Inferenz: zwingend auf CPU bleiben fuer maximalen
    Kontext-Headroom (heute manuell, sollte automatisch erkannt werden)

- [ ] **Sentence-Window-Indexing pruefen.** Statt 800-Token-Chunks pro
  Vers/Satz indexieren mit gespeichertem Window-Kontext (LlamaIndex-
  Pattern). Schaerfere Embeddings, hoeherer Index-Aufwand.
  Lohnt sich evtl. nur fuer dichte Texte wie Talmud/Kommentare.

- [ ] **`_research_context` von State-Variable auf ContextVar umstellen.**
  Konsistenz mit `doc_rag_tokens_var` und `tool_output_budget_var`.
  Funktional kein Bug (deklariert in `_chat_mixin.py:58`), nur Stil.
  Multi-Agent-Tribunal-Reset-Logik beachten.

---

## Hardware (offen)

- [ ] **V100 32GB testen** sobald die SXM2→PCIe-konvertierte Karte
  geliefert ist. Vermutlich Mai 2026.
  - Stress-Test (24h Volllast, < 85°C)
  - cuBLAS-Stabilitaet (war Hauptgrund fuer OOM-Crashes mit MiniMax)
  - Speed-Vergleich vs. P40 in derselben Pipeline (erwartet: 3-5×
    schneller bei FP16-Quants wegen HBM2-Bandwidth)
  - Tensor-Split-Anpassung in `llama-swap config.yaml` —
    Speed-Klassen-Logik erkennt Volta-Tensor-Cores (Compute 7.0)
    derzeit nicht als "schnell wie RTX 8000"
  - Power-Connector pruefen (Custom-Konvertierung kann EPS-12V mit
    abweichender Pin-Belegung haben)

---

## Backlog

- [ ] Structured Output / Data Extraction
- [ ] READMEs weiter refaktorieren
