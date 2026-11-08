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
- `aifred/server.py` - der Server (`venv/bin/python -m aifred.server`): API, Vue-Oberfläche, `/_upload/`, `/vendor/`
- `aifred/lib/api/` - die HTTP-API (FastAPI), unter `/api`
- `frontend/` - die Oberfläche (Vue 3, Vite, TypeScript); Build nach `frontend/dist`, ausgeliefert unter `/api/app/`
- `aifred/lib/` - Kern-Bibliotheken (IMMER hier zuerst suchen!)
  - `turn_service.py` - ein Zug: Nachricht bis zur fertigen Antwort, im Server, mit oder ohne Tab
  - `turn_debate.py` - die Diskussionsmodi (Plätze: Verfasser, Kritiker, Richter; `debate_seats.py`)
  - `session_events.py` - Ereignisstrom je Sitzung (den die Tabs lesen)
  - `prompt_loader.py` - Prompt-Handling
  - `message_builder.py` - Message-Formatting
  - `message_hub.py` - Message Hub Worker-Management
  - `envelope.py` - InboundMessage/OutboundMessage Dataclasses
  - `routing_table.py` - SQLite Routing Table (Kanal → Session)
  - `imap_listener.py` - IMAP IDLE Listener (Background Worker)
  - `message_processor.py` - Processing Pipeline (Message → Engine → Reply)
  - `embeddings.py` - bge-m3-Embedding-Function für die ChromaDB-Collections
  - `document_search_index.py` - Suche über jeden Abschnitt: Vektorabstand + Wortindex, zusammengeführt nach Rang
  - `text_search.py` - Wörter für FTS5 mit Trigrammen, gemeinsam für Dokumentensuche und Mail-Index
  - `research_tools.py` - Recherche-Pipeline (`run_web_search`)
- `prompts/de/` und `prompts/en/` - Alle Prompts (NICHT hardcodiert im Code!)
- `aifred/backends/` - LLM-Backend Adapter

### Architektur
- Python-Server (FastAPI unter uvicorn, ein Prozess, ein Port) mit Vue-3-Oberfläche; Plan und Stand des
  Umbaus von Reflex: `docs/de/architecture/vue-migration/README.md`
- Backends: llama.cpp und vLLM (beide via llama-swap), Ollama, Cloud-APIs (Claude, Qwen, DeepSeek, Kimi)
- Multi-Agent System: AIfred ist der Grundagent; in den Diskussionsmodi nimmt jeder Agent einen Platz ein
  (Verfasser, Kritiker, Richter), Vorgabe Sokrates und Salomo aus `settings.json`

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

### Stellvertreter in Tests
Eine kleine Klasse statt des echten Objekts erbt für mypy von der echten Klasse und zur Laufzeit von
`object`, wenn eine echte Unterklasse nicht geht (sie hätte Methoden, die der Stellvertreter verweigern
muss, oder eine Dataclass verlangt im Konstruktor alle Felder):
```python
if TYPE_CHECKING:
    _ReadingBase = BrowserReading
else:
    _ReadingBase = object

class FakeReading(_ReadingBase):
    ...
```
mypy prüft dann Felder, geliehene Methoden und Überschreibungen gegen die echten; `Any` oder `cast`
würden nichts prüfen. Kein `# type: ignore[attr-defined]` für solche Zugriffe: `warn_unused_ignores`
meldet jede Ausnahme, die nichts mehr unterdrückt.

---

## ⚠️ Code-Qualitätsprüfungen (PFLICHT!)

**IMMER nach Code-Änderungen ausführen - KEINE Ausnahmen!**

ruff und mypy kommen aus `~/.venv/devtools/bin/`, nicht aus dem Projekt-venv. Die Typen des Projekts
und die Stubs (`types-*` in `requirements.txt`) bekommt mypy über `--python-executable`.

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
~/.venv/devtools/bin/ruff check aifred/ tests/ scripts/
```

**Oberfläche** (in `frontend/`): `npm run build` (erzeugt vorher die API-Typen aus dem OpenAPI-Schema
des Servers, `src/api/schema.d.ts`, und prüft mit `vue-tsc`), `npx vitest run`.

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
- `E402` in config.py: Import-Position (notwendig wegen zirkulärer Imports)
- `F541` unnötige f-strings: Können mit `--fix` automatisch behoben werden
- mypy-Warnungen in Backends: OpenAI SDK Type-Mismatches (OpenAI-Library Issue)
- mypy `no_implicit_optional`: Bestehender Code, wird nicht refactored

---

## AIfred API - Message Injection

Eine Nachricht als eigene Eingabe des Users in eine Sitzung geben (z. B. für Tests); der Server
führt den Zug aus wie eine im Chat geschriebene Nachricht, auch ohne offenen Tab:

```bash
# API Endpoint: POST http://localhost:8002/api/chat/inject
# Parameter: sender (Pflicht, nur Anzeige), session_id, message, token
# Auth: token MUSS gesetzt sein (INJECT_API_TOKEN aus .env) — sonst 403/503

curl -s "http://localhost:8002/api/chat/inject" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"sender": "Claude", "session_id": "SESSION_ID_HIER", "message": "Deine Nachricht hier", "token": "<INJECT_API_TOKEN>"}'

# Erfolgreiche Antwort (queued=true: wartet hinter dem laufenden Zug der Sitzung):
# {"success":true,"message":"Turn started","session_id":"...","queued":false}
```

**Session-ID finden:** `ls -t data/sessions/` (neueste `.json`), oder `GET /api/sessions`
mit Login-Cookie.

**Wichtig:**
- Port ist `8002` (der Server, `aifred/server.py`)
- `session_id` ist der Dateiname unter `data/sessions/<session_id>.json`; eine unbekannte
  Sitzung gibt `404`. Läuft in der Sitzung schon ein Zug, wartet die Nachricht in der
  Warteschlange `pending_messages` und startet, wenn er endet (derselbe Weg wie eine
  Chat-Nachricht oder ein Diktat, `turn_service.submit_turn`)
- Ob der Zug fertig ist: `GET /api/chat/status?session_id=…` (`is_generating`, Login-Cookie)
- **`token` ist Pflicht** — Wert aus `INJECT_API_TOKEN` (`.env`). Ohne gültiges
  Token antwortet der Endpoint mit `403` (falsch) bzw. `503` (nicht konfiguriert).
  Schützt den Remote-Control-Zugang zum Agenten.

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
  der Katalogzeile der TTS-Suite; gelesen in `lib.tts_service.entries()`, die Einheit je Eintrag): `sentence`,
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