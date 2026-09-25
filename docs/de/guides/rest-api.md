# REST API

> **English version:** [rest-api.md](../../en/guides/rest-api.md)

AIfreds Backend stellt unter `/api` eine REST API bereit (FastAPI, Port `8002`). Sie ist
eine **Fernbedienung für den Browser**: Sie führt selbst keine Inferenz aus, sondern
reiht Nachrichten in eine Browser-Session ein, die dann die komplette Pipeline durchläuft –
Intent-Erkennung, Multi-Agent-Modi, Recherche, Vision, History-Kompression.
Ein Code-Pfad, alle Features, und du kannst im Browser zusehen, wie die Antwort
gestreamt wird, während dein Skript wartet.

```
API request ──→ set_pending_message() ──→ browser polls (1 s)
                                              │
                                              ▼
                                    send_message() pipeline
                                              │
                                              ▼
                            intent detection, multi-agent, research …
                                              │
                                              ▼
                             response visible in browser + via API
```

Für Jobs, die **ohne** Browser laufen sollen (Cron, Webhooks), nutze den
[Scheduler und den Webhook-Endpoint](../architecture/scheduler.md) – sie laufen in
einer isolierten Session mit eigener Sicherheitsstufe.

Interaktive OpenAPI-Doku: `/api/docs` (Login erforderlich, siehe unten).

---

## Authentifizierung

| Route | Auth |
|---|---|
| `/api/chat/inject` | Feld `token` = `INJECT_API_TOKEN` aus `.env` |
| `/api/agent/trigger` | Feld `token` = `WEBHOOK_API_TOKEN` aus `.env` |
| `/api/oauth/{provider}/callback` | keine (kommt als Redirect des Providers) |
| **alles andere** (inkl. `/api/docs`) | Login-Cookie `aifred_username`, wie ihn der Web-Login setzt |

- Token-Endpoints antworten mit **503**, wenn das Token nicht konfiguriert ist, und mit **403**,
  wenn es falsch ist. Sie sind die vorgesehenen Einstiegspunkte für Skripte und andere Rechner.
- Cookie-Routen antworten ohne gültigen, signierten Login-Cookie mit
  **403 "Login cookie required"**. Aus einem Skript heraus kopierst du den Cookie aus einem
  eingeloggten Browser und schickst ihn mit `-b 'aifred_username=…'` mit. Der Cookie wird mit
  `AIFRED_SESSION_SECRET` signiert (bzw. mit einem persistierten Zufalls-Secret, falls nicht gesetzt).

Das Backend lauscht auf allen Interfaces, daher schützt diese Sperre auch gegen andere
Rechner im LAN. Setz einen Reverse-Proxy mit TLS davor, bevor du
AIfred ins Internet stellst (siehe [Einrichtungsanleitung → Auf die Web-UI zugreifen](deployment.md#auf-die-web-ui-zugreifen)).

---

## Endpoints

### Chat & Sessions

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/chat/inject` | POST | Nachricht in eine Browser-Session einreihen (`session_id`, `message`, `token`) |
| `/api/chat/status` | GET | `?session_id=` — `is_generating`, `message_count` |
| `/api/chat/history` | GET | `?session_id=` — `chat_history` + `llm_history` (ohne Angabe die neueste Session) |
| `/api/chat/clear` | POST | History einer Session leeren (`session_id`) |
| `/api/sessions` | GET | Sessions auflisten (`session_id`, `last_seen`, `message_count`) |
| `/api/session/config` | POST | Konfiguration pro Session: `active_agent`, `multi_agent_mode`, `symposion_agents`, `research_mode` |

### Einstellungen, Modelle, System

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/health` | GET | Health-Check mit Backend-Typ/-Status |
| `/api/settings` | GET / PATCH | Globale Einstellungen (Backend, Modelle pro Rolle, TTS, Sprache, …) — PATCH ändert nur die mitgeschickten Felder |
| `/api/models` | GET | Verfügbare Modelle des aktiven Backends + die Vision-fähigen |
| `/api/system/restart-aifred` | POST | AIfred-Dienst neu starten |
| `/api/system/restart-ollama` | POST | Ollama neu starten |
| `/api/system/reset-defaults` | POST | Einstellungen auf Standardwerte zurücksetzen |

### Agenten, Webhook, OAuth

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: einen Agenten headless ausführen (`message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`) |
| `/api/agents/export` | GET | Agenten-Definitionen exportieren |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Vorschau / Import eines Agenten-Exports (Multipart-Upload) |
| `/api/oauth/{provider}/auth-url` · `/status` · `/callback` | GET | OAuth-Flow (Google) — siehe [OAuth](plugins/oauth.md) |
| `/api/oauth/{provider}` | DELETE | Tokens eines Providers widerrufen |

### Medien (von der UI genutzt)

| Präfix | Inhalt |
|---|---|
| `/api/vision/*` | Snapshots, MJPEG-Streams, Event-Frames, Gesichter registrieren/auflisten/umbenennen, Zonen-Masken, Zonen-Editor |
| `/api/audio/*` | Audiodateien, Testton, Wiedergabeposition |
| `/api/browser/queue/{session_id}` · `/api/browser/stream/{session_id}` | Push-Bus Server→Browser (siehe [Browser Push Bus](../architecture/browser-push-bus.md)) |

---

## Global vs. pro Session

`/api/settings` enthält, was wirklich global ist (Backend, Modelle, TTS-Stimmen,
Sprache, Sampling). Alles, was zu einer Unterhaltung gehört – Agent,
Diskussionsmodus, Recherchemodus, Symposion-Teilnehmer – läuft über
`/api/session/config` und wird in der Session-Datei
(`data/sessions/<session_id>.json`) als Single Source of Truth gespeichert:

```json
{
  "data": {
    "config": {
      "active_agent": "aifred",
      "multi_agent_mode": "standard",
      "symposion_agents": [],
      "research_mode": "automatik"
    }
  }
}
```

Jede neue Session startet sauber mit `aifred + standard + automatik` – sie erbt
nie etwas von der vorherigen.

**Sync:** Alle Schreiber (Browser-Tabs, API, E-Mail-Kanal, Voice-Terminal) ändern
die Session-Datei; jeder Tab, der diese Session anzeigt, bemerkt die neue mtime innerhalb
von etwa einer Sekunde und lädt neu. Injizierte Nachrichten starten ihre Inferenz im
Browser innerhalb von etwa zwei Sekunden. Änderungen an globalen Einstellungen (Modelle, Stimmen,
Temperatur) erscheinen live in der UI.

---

## Beispiele

```bash
API=http://localhost:8002/api
COOKIE='aifred_username=<value from your browser>'

# Find a session id (or take it from data/sessions/)
curl -s -b "$COOKIE" $API/sessions

# Queue a message — the browser session runs the full pipeline
curl -s -X POST $API/chat/inject \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "message": "What is Python?", "token": "<INJECT_API_TOKEN>"}'

# Wait until the answer is done, then read it
curl -s -b "$COOKIE" "$API/chat/status?session_id=<session_id>"
curl -s -b "$COOKIE" "$API/chat/history?session_id=<session_id>"

# Switch this session to Tribunal with deep research
curl -s -b "$COOKIE" -X POST $API/session/config \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "multi_agent_mode": "tribunal", "research_mode": "deep"}'

# Change AIfred's model (global)
curl -s -b "$COOKIE" -X PATCH $API/settings \
  -H "Content-Type: application/json" \
  -d '{"aifred_model": "<model id from /api/models>"}'
```

Der Browser muss geöffnet sein und pollen, damit injizierte Nachrichten aufgenommen werden.

---

## Anwendungsfälle

- AIfred von einem anderen Gerät oder über HTTPS bedienen
- Andere Systeme (Home Assistant, Node-RED, Skripte) Fragen über den
  Inject- oder Webhook-Endpoint schicken lassen
- Batch-Abfragen, eigene Apps, Remote-Tests auf Headless-Systemen
