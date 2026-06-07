# Projekt-spezifische Regeln für AIfred-Intelligence

(Allgemeine Regeln siehe ~/.claude/CLAUDE.md)

---

## ⚠️ KRITISCH: Git-Workflow

- **NIEMALS** automatisch commit oder push ausführen
- Nur auf explizite Ansage des Users ("commit", "push")
- Bei Änderungen: Zeigen, erklären, warten auf Freigabe

---

## ⚠️ KRITISCH: Keine Fallbacks oder Backward-Compatibility

- **NIEMALS** automatisch Fallback-Logik einbauen
- **NIEMALS** Backward-Compatibility-Aliase erstellen (z.B. `new_func = old_func`)
- Fallbacks (für alte Datenformate, fehlende Felder, Migration etc.) nur nach **expliziter** Absprache mit User
- Backward-Compatibility nur wenn User es **ausdrücklich** wünscht
- Im Zweifel: Alte Daten/Code löschen und sauber neu starten
- GRUND: Fallbacks und Aliase verkomplizieren den Code und verstecken Probleme
- **IMMER mit User abklären - GRUNDSÄTZLICH - WICHTIG!**

---

## ⚠️ Keep It Simple - Auch bei Race Conditions und Edge Cases

- **Bei Race Conditions:** Erst strukturell vermeiden, dann Locks/Mutexe wenn nötig
- **Bei Edge Cases:** Prüfen ob der Edge Case überhaupt eintreten kann
- **Vor jedem Fallback fragen:** "Ist das wirklich nötig oder kann ich das Problem anders lösen?"
- Oft ist die einfachste Lösung: Code so strukturieren, dass das Problem gar nicht erst entsteht
- Locks/Mutexe sind OK wenn wirklich unvermeidbar - aber **IMMER mit User besprechen**
- GRUND: Jeder Fallback und jeder Lock ist potentiell versteckte Komplexität

---

## Bereits integrierte Features (NICHT vergessen!)

- **ChromaDB Vector Cache** - Semantischer Cache für Web-Research (Docker)
- **RAG-System** - Retrieval-Augmented Generation mit Relevanz-Check
- **History Compression** - Automatische Kompression (siehe unten)
- **Multi-Backend Support** - llama.cpp (via llama-swap), Ollama, vLLM, TabbyAPI
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
  - `vector_cache.py` - ChromaDB Integration
  - `conversation_handler.py` - Automatik-Modus, RAG-Kontext
- `prompts/de/` und `prompts/en/` - Alle Prompts (NICHT hardcodiert im Code!)
- `aifred/backends/` - LLM-Backend Adapter

### Architektur
- Python/Reflex Anwendung (nicht Gradio!)
- Backends: llama.cpp (via llama-swap), Ollama, vLLM, TabbyAPI
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

---

## ⚠️ Code-Qualitätsprüfungen (PFLICHT!)

**IMMER nach Code-Änderungen ausführen - KEINE Ausnahmen!**

```bash
# 1. Syntax-Check (schnell, immer zuerst)
python3 -m py_compile aifred/DATEI.py

# 2. Linting mit Ruff
source venv/bin/activate && ruff check aifred/DATEI.py

# 3. Type-Checking mit mypy
source venv/bin/activate && mypy aifred/DATEI.py --ignore-missing-imports
```

**Mehrere Dateien auf einmal:**
```bash
source venv/bin/activate && ruff check aifred/state.py aifred/aifred.py aifred/lib/context_manager.py
source venv/bin/activate && mypy aifred/state.py aifred/aifred.py --ignore-missing-imports
```

**Bekannte Ignores (NICHT beheben):**
- `E402` in config.py/state.py: Import-Position (notwendig wegen zirkulärer Imports)
- `F541` unnötige f-strings: Können mit `--fix` automatisch behoben werden
- mypy-Warnungen in Backends: OpenAI SDK Type-Mismatches (OpenAI-Library Issue)
- mypy `no_implicit_optional`: Bestehender Code, wird nicht refactored

---

## AIfred API - Message Injection

Um Nachrichten direkt in eine Browser-Session zu injizieren (z.B. für Tests):

```bash
# API Endpoint: POST http://localhost:8002/api/chat/inject
# Parameter: session_id, message, token
# Auth: token MUSS gesetzt sein (INJECT_API_TOKEN aus .env) — sonst 403/503

curl -s "http://localhost:8002/api/chat/inject" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID_HIER", "message": "Deine Nachricht hier", "token": "<INJECT_API_TOKEN>"}'

# Erfolgreiche Antwort:
# {"success":true,"message":"Message queued for browser processing","session_id":"...","queued":true}
```

**Aktive Session-ID finden:**
- Debug-Log: `tail data/logs/aifred_debug.log` → "Session loaded: XXXXX..."
- Session-Dateien: `ls data/sessions/` → neueste `.json` Datei (ohne `.pending`/`.update`)

**Wichtig:**
- Port ist `8002` (Backend-API), NICHT `3000` (Frontend)
- Parameter heißt `session_id` (UUID der Browser-Session, Dateiname unter
  `data/sessions/<session_id>.json`) — der Browser pollt `pending_message`
  über genau diese `session_id`
- **`token` ist Pflicht** — Wert aus `INJECT_API_TOKEN` (`.env`). Ohne gültiges
  Token antwortet der Endpoint mit `403` (falsch) bzw. `503` (nicht konfiguriert).
  Schützt den Remote-Control-Zugang zum Agenten.
- Browser muss aktiv sein und pollen, damit die Nachricht aufgenommen wird

---

## ⚠️ Reflex Patch: frontend_path Route-Matching Bug

**Reflex v0.8.24 hat einen Bug** im Route-Matching bei `frontend_path`:

- **Datei:** `venv/lib/python3.12/site-packages/reflex/route.py`, Zeile 217
- **Bug:** `path.removeprefix(config.frontend_path)` matcht nicht, weil der Browser-Pfad
  mit `/` beginnt (`/aifred/`) aber `frontend_path` kein `/` hat (`aifred`)
- **Fix:** `path.removeprefix("/" + config.frontend_path)`
- **Auswirkung ohne Fix:** `on_load` feuert nie → App bleibt bei "wird initialisiert..." hängen

**Bei Reflex-Update:** Prüfen ob der Bug upstream gefixt wurde. Falls nicht, Patch erneut anwenden:
```python
# route.py, Funktion get_route(), ca. Zeile 217
# VORHER (Bug):
path = path.removeprefix(config.frontend_path)
# NACHHER (Fix):
path = path.removeprefix("/" + config.frontend_path)
```

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
- Scannt `~/.cache/huggingface/hub/models--*` für vLLM/TabbyAPI kompatible Modelle
- Download-Anleitung siehe `~/.claude/CLAUDE.md` (global für alle Projekte)