# Projekt-spezifische Regeln für AIfred-Intelligence

(Allgemeine Regeln siehe ~/.claude/CLAUDE.md — dort stehen u. a. Git-Workflow, keine
Fallbacks und keine Backward-Compatibility, Keep It Simple bei Race Conditions und Edge Cases)

---

## ⚠️ Plugin-Atomarität (Architektur-Entscheidung 2026-08-11)

Plugins (`aifred/plugins/`) sind eigenständige, atomare, modulare Gebilde:

- **Plugin → Plugin: VERBOTEN** — kein Import und keine Code-Abhängigkeit
  zwischen Plugins. Wird ein Plugin abgeschaltet, darf keine Kette brechen.
- **Abschalten = Markierung, nie Verschieben** (seit 06.11.2026): Der Plugin-Manager
  legt `.disabled` in den Ordner des Plugins (gitignoriert, gilt nur auf diesem
  Rechner); die Registry lädt es dann nicht. Der Code bleibt unter `plugins/tools/`
  bzw. `plugins/channels/`, seine Tests laufen immer. Vorher wurde das Verzeichnis
  nach `plugins/disabled/` verschoben: Git sah die Dateien als gelöscht, und die
  Tests des Plugins fanden es nicht.
- **Plugin → aifred/lib: ERLAUBT und erwünscht** — lib ist Kern-Infrastruktur
  und immer da. Gemeinsame Logik zweier Plugins wird als lib-Helper
  konsolidiert, niemals per Plugin-Import geteilt.
- Duplikate zwischen Plugins sind KEIN Refactoring-Grund, solange die
  gemeinsame Logik (noch) nicht in lib lebt — niemals als Fix ein Plugin
  aus dem anderen importieren lassen.

---

## Bereits integrierte Features (NICHT vergessen!)

- **ChromaDB** (Docker, Daten in `data/chromadb/`) - Dokumente (`aifred_documents`) + Agent-Memory (`agent_memory_*`). KEIN Web-Research-Cache — bewusst entfernt, jede Recherche läuft frisch
- **RAG über Dokumente** - agentengetrieben per `search_documents`-Tool (kein Auto-Inject); Hochgeladenes im lokalen Ordner wird von selbst indexiert, von Agenten Erzeugtes nur über `index_document`, aus einer Quelle wie Nextcloud nur, was der Benutzer dort anklickt (ihre Dateien liegen hinter einer höheren Stufe als die Suche). Löschen nimmt den Index mit, Umbenennen und Verschieben ziehen ihn um (`DocumentStore.rename_document`, die Abschnitts-IDs tragen den Namen). Gesucht wird immer über alle Abschnitte, nach Bedeutung und Wortlaut zugleich (`lib/document_search_index.py`, exakt im Arbeitsspeicher, nie über den Näherungs-Index von ChromaDB); ohne Ordner lässt `search_documents` Bibel und Judaica aus
- **History Compression** - Automatische Kompression (siehe unten)
- **Nextcloud** - Kalender, Aufgaben, Kontakte, Notizen, Dateien (`plugins/tools/nextcloud`); EPIM gleicht selbst ab, Löschungen zieht `scripts/epim_deletions_to_nextcloud.py` nach
- **E-Mail** - `email` (AIfreds Postfach), `email_readonly` (privates Postfach), Anhänge über die Parser des Dokumenten-Index, Scans über den Bildbeschreiber. Die Suche läuft über einen lokalen Index je Postfach (`email_channel/mail_index.py`, unter `~/.cache/aifred/mail_index`, außerhalb der Sicherung), weil der Mailserver nur ganze Wörter findet
- **Multi-Backend Support** - llama.cpp und vLLM (beide via llama-swap), Ollama, Cloud-APIs
- **Thinking Mode** - Chain-of-Thought für Qwen3 Modelle
- **Message Hub** *(WIP)* - Background-Worker für externe Kanäle (E-Mail, Discord, Telegram, Signal). Architektur: [docs/de/architecture/message-hub.md](docs/de/architecture/message-hub.md)

---

## Projekt-Struktur

### Wichtige Verzeichnisse
- `aifred/lib/` - Kern-Bibliotheken (IMMER hier zuerst suchen!)
  - `prompt_loader.py` - Prompt-Handling
  - `message_builder.py` - Message-Formatting
  - `multi_agent.py` - Multi-Agent Logik
  - `message_hub.py` - Message Hub Worker-Management
  - `envelope.py` - InboundMessage/OutboundMessage Dataclasses
  - `routing_table.py` - SQLite Routing Table (Kanal → Session)
  - `imap_listener.py` - IMAP IDLE Listener (Background Worker)
  - `message_processor.py` - Processing Pipeline (Message → Engine → Reply)
  - `embeddings.py` - bge-m3-Embedding-Function für die ChromaDB-Collections
  - `document_search_index.py` - Suche über jeden Abschnitt: Vektorabstand + Wortindex, zusammengeführt nach Rang
  - `text_search.py` - Wörter für FTS5 mit Trigrammen, gemeinsam für Dokumentensuche und Mail-Index
  - `research_tools.py` - Recherche-Pipeline (`execute_research`, `hub_web_search`)
- `prompts/de/` und `prompts/en/` - Alle Prompts (NICHT hardcodiert im Code!)
- `aifred/backends/` - LLM-Backend Adapter

### Architektur
- Python/Reflex Anwendung (nicht Gradio!)
- Backends: llama.cpp und vLLM (beide via llama-swap), Ollama, Cloud-APIs (Claude, Qwen, DeepSeek, Kimi)
- Multi-Agent System:
  - AIfred (Hauptagent)
  - Sokrates (Kritiker)
  - Salomo (Richter im Tribunal-Mode)

---

## Code-Konventionen

### History Compression Algorithmus

Die History-Kompression verwendet folgende Schwellenwerte (konfigurierbar in `config.py`):

| Parameter | Wert | Beschreibung |
|-----------|------|--------------|
| `HISTORY_COMPRESSION_TRIGGER` | 0.7 (70%) | Bei dieser Context-Auslastung wird komprimiert |
| `HISTORY_COMPRESSION_TARGET` | 0.3 (30%) | Ziel nach Kompression (Platz für ~2 Roundtrips) |
| `HISTORY_SUMMARY_RATIO` | 0.25 (4:1) | Summary = 25% des zu komprimierenden Inhalts |
| `HISTORY_SUMMARY_MIN_TOKENS` | 500 | Minimum für sinnvolle Zusammenfassungen |
| `HISTORY_SUMMARY_TOLERANCE` | 0.5 (50%) | Erlaubte Überschreitung, darüber wird gekürzt |

**Ablauf:**
1. Trigger bei 70% Context-Auslastung
2. Sammle älteste Messages (FIFO) bis remaining < 30%
3. Komprimiere gesammelte Messages zu Summary (4:1 Ratio)
4. Neue History = [Summary] + [verbleibende Messages]

**Token-Estimation:** Ignoriert `<details>`, `<span>`, `<think>` Tags (gehen nicht ans LLM)

**Beispiele:**
- 7K Context: Trigger bei 4.900, Ziel 2.100, Summary ~700 tok
- 40K Context: Trigger bei 28.000, Ziel 12.000, Summary ~4.000 tok
- 200K Context: Trigger bei 140.000, Ziel 60.000, Summary ~20.000 tok

---

### Zahlenformatierung (WICHTIG!)
- **IMMER** `format_number()` aus `aifred/lib/formatting.py` verwenden
- Diese Funktion formatiert Zahlen locale-aware (DE: 1.000,5 vs EN: 1,000.5)
- **NIEMALS** f-Strings mit direkten Zahlen wie `f"{value:.1f}"` in User-facing Output
- Beispiel:
  ```python
  from .lib.formatting import format_number
  # RICHTIG:
  yield f"Model: {format_number(size_mb / 1024, 1)} GB"
  # FALSCH:
  yield f"Model: {size_mb / 1024:.1f} GB"
  ```

### State-Mixins und mypy (seit 06.11.2026)
`AIState` setzt sich aus Mixins zusammen; mypy prüft jedes Mixin für sich und
sieht die anderen nicht.
- **Was ein Mixin von einem anderen benutzt** (Feld oder Methode), deklariert es
  am Anfang der Klasse unter `if TYPE_CHECKING:`, wörtlich wie die echte
  Definition (Typ, Signatur, Dekoratoren). Wo `AIState` die Mixins zusammensetzt,
  prüft mypy jede Deklaration gegen die echte Definition.
- **Kein `# type: ignore[attr-defined]`** für solche Zugriffe. Für `aifred/state/`
  gilt keine Ausnahme in `mypy.ini`, und `warn_unused_ignores` meldet jede
  Ausnahme, die nichts mehr unterdrückt.
- `tests/test_state_attribute_references.py` prüft jeden Zugriff `self.X` gegen
  die zusammengesetzte Klasse; er fängt auch eine Deklaration ohne echte Definition.
- **Methoden, die nur State-Code aufruft, sind privat** (`_name`). Jede öffentliche
  Methode ist ein Event-Handler, den jeder verbundene Client mit eigenen
  Argumenten auslösen kann (06.11.: `picker_open_for` setzte so Sandbox-Wurzel,
  Fähigkeiten und Rückruf des Datei-Pickers).
- **Öffentliche State-Felder sind `rx.Field`:** `name: rx.Field[T] = rx.field(wert)`,
  ein veränderlicher Default über `default_factory` (`rx.field(default_factory=list)`).
  Nur so ist `AIState.name` in der Oberfläche für mypy eine typisierte Var
  (`.length()`, `.contains()`, `["key"]`); zur Laufzeit ist es dasselbe wie die
  einfache Annotation (Reflex baut beides gleich).
- **Jede öffentliche Methode trägt `@rx.event`** (berechnete Vars `@rx.var`). Erst dann
  prüft mypy die Aufrufe aus der Oberfläche, mit Argumenttypen. Das nackte
  `@rx.event` tut zur Laufzeit nichts. Aus State-Code aufgerufen prüft mypy nur die
  Positionsargumente, nicht Schlüsselwörter, Defaults oder den Rückgabewert: Eine
  Methode, die Code so aufruft, ist eine interne und gehört privat; ein Handler,
  dessen Ergebnis State-Code braucht, ruft eine private Methode auf, die auch der
  Code nutzt (`open_channel_credentials` → `_open_channel_credentials`).
- `tests/test_state_typing_conventions.py` hält beide Regeln.
- **Oberfläche:** Ein Element aus `rx.foreach` ist `ObjectVar[dict[str, Any]]` (bzw.
  `ObjectVar[EineDataclass]`), nie `rx.Var` oder `dict`; `data-*`-Attribute gehen über
  `custom_attrs={...}`, nicht über `**{...}`. Einen Text aus `rx.cond` gibt `t()` als
  `StringVar` zurück (`.to(str)` ändert weder Laufzeittyp noch JavaScript). Nach
  Änderungen an Typen, Deklarationen oder Dekoratoren das Rendering jeder Seite
  vorher und nachher vergleichen: `scripts/render_pages.py` (06.11. fing das ein
  `.to(list)`, das den Elementtyp verwarf und die Seite beim Kompilieren gebrochen hätte).
- Meldet mypy „Module not callable“ (meist `rx.icon`), **ohne Cache gegenprüfen**
  (`--no-incremental`): Am 06.11. lieferte ein alter Cache 242 solche Fehler, auch beim
  Lauf über `aifred/ tests/`; ohne Cache und mit frischem Cache (auch nach
  Einzeldatei-Läufen) keine. Die Ursache ist nicht gefunden.
- **Stellvertreter in Tests** (eine kleine Klasse statt des echten States oder
  Objekts) erben für mypy von der echten Klasse und zur Laufzeit von `object`:
  ```python
  if TYPE_CHECKING:
      _StateBase = TTSStreamingMixin
  else:
      _StateBase = object

  class _Reply(_StateBase):
      stream_text_to_ui = TTSStreamingMixin.stream_text_to_ui  # echte Methode geliehen
  ```
  mypy prüft dann Felder, geliehene Methoden und Überschreibungen gegen die
  echten. Eine echte Unterklasse geht bei Reflex-States nicht (sie lehnen
  undeklarierte Attribute ab), und `Any` oder `cast` würden nichts prüfen.
  Reflex-States und Dataclasses verlangen im Konstruktor alle Felder: Der
  Stellvertreter braucht ein eigenes `__init__`.

---

## ⚠️ Code-Qualitätsprüfungen (PFLICHT!)

**IMMER nach Code-Änderungen ausführen - KEINE Ausnahmen!**

ruff und mypy kommen aus `~/.venv/devtools/bin/`; im Projekt-venv sind sie seit
06.11.2026 nicht mehr installiert (die alten Versionen dort bewerteten
`@rx.event(background=True)` anders). Die Typen des Projekts und die Stubs
(`types-*` in `requirements.txt`) bekommt mypy über `--python-executable`.

```bash
# 1. Syntax-Check (schnell, immer zuerst)
python3 -m py_compile aifred/DATEI.py

# 2. Linting mit Ruff
~/.venv/devtools/bin/ruff check aifred/DATEI.py

# 3. Type-Checking mit mypy (Code und Tests, rund 50 s)
~/.venv/devtools/bin/mypy aifred/ tests/ --python-executable venv/bin/python --ignore-missing-imports
```

**Mehrere Dateien auf einmal:**
```bash
~/.venv/devtools/bin/ruff check aifred/state/ aifred/aifred.py aifred/lib/context_manager.py
```

**Breite `except`-Fänge (seit 06.11.2026):**
- Gefangen wird, was dort auftreten kann: Datenbank `sqlite3.Error`, Datei `OSError`,
  JSON-Datei `(OSError, ValueError)`, HTTP `httpx.HTTPError`, OpenCV `cv2.error`, Ollama
  `OLLAMA_ERRORS` (`lib/ollama_models.py`), mpv `PLAYER_ERRORS` / `STREAM_ERRORS`.
- Kein Fang, wo der Aufgerufene selbst fängt oder der Aufrufer die Grenze ist (Tool-Fehler
  meldet `function_calling.py` dem Modell, mit Traceback im Log).
- `except Exception` nur an einer echten Grenze (Schleife, Rückruf, je Regel/Kamera/Ziel,
  Aktion mit sichtbarer Fehlermeldung) und immer mit Traceback: mit `logger` als
  `except Exception as e:  # <Grund>` plus `exc_info=True`, mit `log_message` als
  `# noqa: BLE001 — <Grund>` plus `log_exception(...)`. `tests/test_broad_except_reasons.py`
  lehnt jedes `noqa: BLE001` ohne „ — Grund“ ab.

**Bekannte Ignores (NICHT beheben):**
- `E402` in config.py/state/: Import-Position (notwendig wegen zirkulärer Imports)
- `F541` unnötige f-strings: Können mit `--fix` automatisch behoben werden
- mypy-Warnungen in Backends: OpenAI SDK Type-Mismatches (OpenAI-Library Issue)
- mypy `no_implicit_optional`: Bestehender Code, wird nicht refactored

---

## AIfred API - Message Injection

Um Nachrichten direkt in eine Browser-Session zu injizieren (z.B. für Tests):

```bash
# API Endpoint: POST http://localhost:8002/api/chat/inject
# Parameter: sender (Pflicht, nur Anzeige), session_id, message, token
# Auth: token MUSS gesetzt sein (INJECT_API_TOKEN aus .env) — sonst 403/503

curl -s "http://localhost:8002/api/chat/inject" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"sender": "Claude", "session_id": "SESSION_ID_HIER", "message": "Deine Nachricht hier", "token": "<INJECT_API_TOKEN>"}'

# Erfolgreiche Antwort:
# {"success":true,"message":"Message queued for browser processing","session_id":"...","queued":true}
```

**Aktive Session-ID finden:**
- Debug-Log: `tail data/logs/aifred_debug.log` → "Session loaded: XXXXX..."
- Session-Dateien: `ls data/sessions/` → neueste `.json` Datei (ohne `.pending`/`.update`)

**Wichtig:**
- Port ist `8002` (Backend-API), NICHT `3000` (Frontend)
- Parameter heißt `session_id` (UUID der Browser-Session, Dateiname unter
  `data/sessions/<session_id>.json`) — die Nachricht kommt in die Warteschlange
  `pending_messages` dieser Sitzung; der Browser holt alle 500 ms die älteste ab,
  sobald nichts mehr läuft (derselbe Weg wie ein Diktat „direkt an die KI“)
- **`token` ist Pflicht** — Wert aus `INJECT_API_TOKEN` (`.env`). Ohne gültiges
  Token antwortet der Endpoint mit `403` (falsch) bzw. `503` (nicht konfiguriert).
  Schützt den Remote-Control-Zugang zum Agenten.
- Browser muss aktiv sein und pollen, damit die Nachricht aufgenommen wird

---

## AIfred API - Ansagen auf den Echo Dot (ohne LLM)

`POST /api/audio/announce` (`aifred/lib/api/announce.py`) liest einen fertigen Text auf
einem FreeEcho.2-Raum vor — derselbe Zustellweg wie die proaktiven Alarme
(`announce_to_channel` → TTS des Echo-Plugins → Alarm-Warteschlange, Ansagen eines
Raums laufen nacheinander). Aufrufer ist z. B. Agent-Orc.

- Auth: `Authorization: Bearer <ANNOUNCE_API_TOKEN>` (`.env`), fail-closed (503 ohne
  Token, 403 bei falschem). Pfad ist von der Login-Cookie-Pflicht ausgenommen.
- Body `{"room": "...", "text": "..."}` ODER `{"room": "...", "texts": ["Absatz", ...],
  "pause_ms": 1000, "speaker": "Whisper"}`; `room` = Raumname, `@gruppe` oder `*`.
  `texts` = mehrere Absätze als EINE Ansage (`pause_ms` = Stille zwischen den Absätzen); `speaker`
  ist PFLICHT (Name des sprechenden Agenten, keine anonymen Ansagen, sonst 422): er steht in der Bubble-Kopfzeile, das Modell sieht „Nachricht von <speaker>“. Ob ein Ding vor und ein Dong nach der Ansage angefordert wird,
  ist eine Plugin-Einstellung (`FREEECHO2_NOTIFICATION_START_TONE` / `_END_TONE`, an/aus), nicht
  Sache des Aufrufers; welcher Ton das ist, legt der Puck fest (`notification_wav` / `_end_wav`).
  `GET /api/audio/announce/rooms` liefert die gerade verbundenen Räume.
- Einheit der Sprachausgabe: EINE Einstellung pro TTS-Engine, systemweit für alle Agenten und Kanäle
  (Browser: Sprachausgabe-Panel; Speicher `tts_toggles_per_engine[engine]["unit"]`; Standard je Engine = `unit_default`
  der Katalogzeile der TTS-Suite; Lesen über `lib.tts_service.speech_unit_for`): `sentence`,
  `paragraph` oder `whole` (alles am Stück, beste Qualität, die erste Sprache kommt erst nach der
  kompletten Erzeugung). Das Echo-Plugin liest sie nur, es kennt keine eigene Einstellung dafür. Der TTS-Dienst zerlegt den Text nach der Einheit des sprechenden Eintrags (`texts` = EIN Text, Absätze durch Leerzeilen;
  an jeder Absatzgrenze liefert der Dienst einen Pause-Marker `pause_ms`, aus dem AIfred Stille macht);
  der erste Abschnitt läuft los, die übrigen entstehen währenddessen (`lib/speech_synthesis.py`). Braucht ein Segment
  länger als `KEEPALIVE_SEC` des Dienstes, schickt er einen Keep-alive-Marker, aus dem AIfred 20 ms Stille macht
  (das 30-s-Inaktivitäts-Timeout des Pucks löst nie aus, er ist die einzige Erkennung einer toten
  Verbindung; die Pumpe im Orchestrator hat keine zweite Uhr). Jede Ansage wird in der Sitzung des Raums dokumentiert (`record_autonomous_turn`).
- 404 bei unbekanntem/nicht verbundenem Raum, keine Längengrenze. Chime fest `notification`, nie `alarm`; Beginn- und Ende-Ton
  spielt der Puck selbst (Protokoll v2: `audio_flag(…, start_tone)` und `audio_end(end_tone)`, Slots
  `notification_wav` / `notification_end_wav` in der Puck-Oberfläche). Läuft Musik/Hörbuch/TTS, hält der Server
  sie vor der Ansage per `_pause` am Puck an und wartet auf die Bestätigung (sonst wird die Ansage verworfen).
- Port 8002 (Backend), nicht 3002.

---

## ⚠️ Reflex Patch: Worker-Respawn nach Crash

**Problem:** `reflex run` startet granian programmatisch (`exec.py`,
`run_granian_backend`) mit `reload=True` + `reload_ignore_worker_failure=True`.
Stirbt der Backend-Worker durch einen C-Level-Crash (z.B. libc-Segfault,
opencv/ffmpeg am korrupten RTSP-Stream), wird er NICHT respawnt — der
granian-Master lebt weiter, also greift auch `systemd Restart=always` nicht
(Main-PID = Master, der „läuft"). Folge: AIfred ist tot/unerreichbar bis zum
manuellen Neustart (2026-06-23 ~1h43, 2026-09-22 16:09).

**Falle:** `respawn_failed_workers=True` allein wirkt NICHT. Im Reload-Modus
läuft granian (2.6) in `_serve_with_reloader`, das nur Dateiänderungen
beobachtet; der Respawn-Code steht in `_serve_loop`, der nur OHNE Reload
läuft. Zusätzlich meldet der Worker-Wächter bei
`reload_ignore_worker_failure=True` den Absturz gar nicht weiter.

- **Datei:** `venv/lib/python3.12/site-packages/reflex/utils/exec.py`, im
  `Granian(...)`-Konstruktor in `run_granian_backend()` (~Zeile 545)
- **Fix:** `reload=False` (die `reload_*`-Argumente entfallen) +
  `respawn_failed_workers=True` + `respawn_interval=3.5`. Kein Backend-Hot-Reload
  mehr — nach Code-Änderungen ohnehin Service-Restart.
- **Verhalten:** Worker-Absturz → Respawn nach ~3,5 s, Frontend läuft weiter.
  Absturz innerhalb 5,5 s nach einem Respawn (Crash-Schleife, z.B. Startfehler
  im Code) → Master beendet sich → systemd startet den Dienst neu.
- **Abgenommen 2026-09-22:** `kill -SEGV <worker-pid>` → Backend nach 4 s
  wieder da, Master + bun unverändert.

**Bei Reflex-Update:** Patch erneut anwenden:
```python
# exec.py, run_granian_backend(), im Granian(...)-Aufruf:
reload=False,            # statt reload=True + reload_paths/_ignore_*/_tick
workers_kill_timeout=2,
respawn_failed_workers=True,
respawn_interval=3.5,
```

---

## ⚠️ Reflex Patch: vite.config.js (allowedHosts, dedupe)

Reflex 0.9 schreibt `.web/vite.config.js` bei **jedem Compile** neu — nach dem
`ExecStartPre`. Das `sed` aus `scripts/patch-vite-config.sh` wirkt deshalb nicht
mehr (06.10.2026: „Blocked request. This host is not allowed", Domain gesperrt).
`allowedHosts` setzt `vite_allowed_hosts` in `rxconfig.py` (aus
`AIFRED_ALLOWED_HOST`), `dedupe` patcht `scripts/patch-reflex.py` ins Template
`reflex_base/compiler/templates.py`. **Bei Reflex-Update:** `patch-reflex.py` erneut
laufen lassen; danach prüfen: `grep -n "allowedHosts\|dedupe" .web/vite.config.js`.
`patch-vite-config.sh` ist obsolet (Entfernen braucht eine Änderung der Unit).

---

## ⚠️ Reflex Patch: Typen der Komponenten-Pakete (mypy)

Reflex liefert die Komponenten in eigenen Paketen (`reflex_components_*`)
mit `.pyi`-Stubs, aber **ohne `py.typed`** (PEP 561, noch in 0.10.1; upstream
Issue #7708, PR #7710).
mypy ignoriert die Stubs dann, jede Komponente wird `Any` → ~200
`no-any-return`-Fehler. Zweiter Fehler: In
`reflex_components_core/core/cond.py` stehen TypeVars mitten in der
`@overload`-Serie von `cond`; mypy verwirft deshalb die sieben
Wert-Überladungen, und `rx.cond(var, "a", "b")` scheitert mit `call-overload`.

`scripts/patch-reflex.py` setzt die Marker und zieht die TypeVars vor die
Serie (Laufzeit unverändert). **Bei Reflex-Update:** Skript erneut laufen
lassen, `--check` meldet, ob upstream inzwischen gefixt ist.

Dazu kommen drei reine Typ-Patches (5–7), ohne die mypy die Oberfläche nicht
prüfen kann (Begründung je Patch im Docstring des Skripts): `Field.__get__`
(mypy prüft die Grenze einer TypeVar im `self`-Typ nicht, wenn die Lösung eine
Union ist; ohne Patch wird jedes `dict`-Feld eine `ArrayVar[Never]`), `rx.event`
als überladene Funktion statt Klasse mit `__new__` und `ObjectVar.__getitem__`
für Dataclass-Werte. Den Rückgabetyp von `EventCallback.__get__` und den
Upload-Kontext (jedes Ereignis wurde Kind des laufenden Uploads, PR #7357) hat
Reflex 0.10 selbst behoben; `tests/test_reflex_upload_context.py` wacht weiter
darüber. Fehlen sie nach einem Update, meldet mypy in `aifred/ui`
Hunderte Fehler — dann zuerst `patch-reflex.py` laufen lassen.

---

## AIfred Systemdienst

AIfred läuft als PolKit Systemdienst (NICHT als root!):

```bash
# Neustart
systemctl restart aifred-intelligence.service

# Status prüfen
systemctl status aifred-intelligence.service

# Logs ansehen
journalctl --user -u aifred-intelligence -f
```

---

## HuggingFace Model Download

**Model Discovery verwendet HuggingFace Cache:**
- Siehe `aifred/lib/model_discovery.py` → `discover_huggingface_models()`
- Scannt `~/.cache/huggingface/hub/models--*` für vLLM-kompatible Modelle
- Download-Anleitung siehe `~/.claude/CLAUDE.md` (global für alle Projekte)