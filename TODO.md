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

## Backlog

- [ ] Structured Output / Data Extraction
- [ ] READMEs weiter refaktorieren
