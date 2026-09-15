# Scheduler & Proaktive Features

**Stand:** 2026-03-29

AIfred kann zeitgesteuert eigenständig handeln — ohne dass ein User eine Nachricht schickt.
Jobs laufen in isolierten Sessions mit Security-Tier-Enforcement.

---

## Architektur

```
                    ┌─────────────────────────────────┐
                    │          Message Hub             │
                    │   (Background Worker Manager)    │
                    └──────────┬──────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
   Channel Workers      Scheduler Worker     Webhook API
   (Email, Discord)     (prüft jede Min)    (/api/agent/trigger)
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Isolierte Session   │
                    │  (sched_ID_random)   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   AIfred Engine     │
                    │   (mit max_tier +   │
                    │  channel="scheduler")│
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Delivery Mode     │
                    │   review/announce/  │
                    │   webhook           │
                    └─────────────────────┘
```

---

## Job-Typen

| Typ | Expression | Beschreibung |
|-----|-----------|-------------|
| `cron` | `0 8 * * *` | Standard 5-Feld Cron (via croniter), mit Timezone |
| `interval` | `300` | Feste Intervalle in Sekunden |
| `once` | `2026-04-01T10:00:00` | Einmalige Ausführung zu ISO-Zeitpunkt |

### Cron-Beispiele

| Expression | Bedeutung |
|-----------|-----------|
| `0 8 * * *` | Täglich um 8:00 |
| `*/30 * * * *` | Alle 30 Minuten |
| `0 9 * * 1-5` | Werktags um 9:00 |
| `0 0 1 * *` | Monatlich am 1. um Mitternacht |

---

## Job Store (SQLite)

Persistiert in `data/scheduler/jobs.db`. Jobs überleben Neustarts.

```sql
CREATE TABLE jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    schedule_type TEXT NOT NULL,     -- 'cron', 'interval', 'once'
    schedule_expr TEXT NOT NULL,     -- Cron-Expr, Sekunden, oder ISO-Timestamp
    payload TEXT NOT NULL,           -- JSON: message, agent, delivery, channel, ...
    max_tier INTEGER NOT NULL,       -- Security-Tier (siehe security-architecture.md)
    enabled INTEGER NOT NULL,
    created_at TEXT,
    last_run TEXT,
    next_run TEXT,
    retry_count INTEGER
);
```

### Job Payload

```json
{
    "message": "Fasse meine ungelesenen E-Mails zusammen",
    "agent": "aifred",
    "delivery": "announce",
    "channel": "discord",
    "recipient": "",
    "webhook_url": "",
    "metadata": {}
}
```

### API

```python
from aifred.lib.scheduler import get_job_store

store = get_job_store()

# Job anlegen
job = store.add(
    name="morning_summary",
    schedule_type="cron",
    schedule_expr="0 8 * * *",
    payload={"message": "Fasse meine E-Mails zusammen", "delivery": "announce", "channel": "discord"},
    max_tier=1,
)

# Jobs auflisten
all_jobs = store.list_all()
active_jobs = store.list_all(enabled_only=True)

# Job löschen
store.delete(job.job_id)

# Job aktivieren/deaktivieren
store.enable(job.job_id, enabled=False)
```

---

## Delivery Modes

Wohin geht das Ergebnis eines Jobs?

| Mode | Beschreibung |
|------|-------------|
| `review` | Notification in der Web-UI, User prüft in Session (Default) |
| `announce` | An einen Channel senden (Discord, Telegram, E-Mail) |
| `webhook` | HTTP POST an externe URL (z.B. Home Assistant) |

Die Notification kommt bei jedem Modus, `announce` und `webhook` liefern
zusätzlich aus.

### Ein Job-Lauf

- **Job-Prompt:** `build_job_prompt()` baut die Nachricht an den Agenten aus
  `prompts/<lang>/scheduler/`: Job-Name, wohin die Antwort geht
  (`delivery_<mode>.txt`), die bisherigen Läufe und der Auftrag.
- **Historie pro Job:** Tabelle `job_runs` hält, was jeder Lauf zugestellt hat,
  unabhängig von den Sessions und nur für die neuesten `SCHEDULER_HISTORY_RUNS`
  Läufe pro Job. Genau diese gehen als Auszug in den Prompt, damit sich der
  Job nicht wiederholt: ganze Sätze bis etwa
  `SCHEDULER_HISTORY_EXCERPT_CHARS` Zeichen, nie mitten im Satz, mit „…“, wenn
  gekürzt wurde (`history_excerpt`). Was ein Job exakt behalten muss, soll er
  laut Job-Prompt ins Gedächtnis schreiben. Jeder Lauf hat trotzdem eine
  eigene Session.
- **Nur die Schlussantwort:** Zugestellt und protokolliert wird der Text nach
  dem letzten Tool-Call (`outbound.metadata["final_text"]`). Arbeitsnotizen
  aus früheren Tool-Runden bleiben in der Session. Endet der Lauf ohne
  Schlussantwort, schlägt der Job fehl.
- **Kein Selbstversand:** Im Scheduler-Lauf fehlen alle Sende-Werkzeuge
  (`Tool.outbound`, `security.may_send_outbound`), beim `email`-Werkzeug die
  Aktion `send`. Zustellen tut ausschließlich der Scheduler.
- **Gedächtnis:** Jobs laufen als Owner und dürfen Erinnerungen schreiben,
  obwohl ihr Tier bei `TIER_COMMUNICATE` liegt (siehe security.md,
  Gedächtnis nur für den Owner).
- **Agent-Einstellungen:** Denken, Reasoning-Stufe und Sampling kommen aus
  den Agent-Einstellungen des Hauptfensters.

### announce

Der Job-Name wird zum Betreff (bei Kanälen mit Betreff, also E-Mail).
Benötigt `channel` und optional `recipient` im Payload:
```json
{"delivery": "announce", "channel": "discord", "recipient": ""}
```

### webhook

Benötigt `webhook_url` im Payload:
```json
{"delivery": "webhook", "webhook_url": "http://homeassistant.local:8123/api/webhook/aifred"}
```

POST-Body:
```json
{
    "job_name": "morning_summary",
    "job_id": 1,
    "result": "Du hast 3 ungelesene E-Mails...",
    "timestamp": "2026-03-30T08:00:05"
}
```

---

## Webhook-API (externe Trigger)

Externe Systeme können AIfred-Aktionen auslösen:

```
POST /api/agent/trigger
```

### Auth

Token-basiert via Credential Broker (`WEBHOOK_API_TOKEN` in `.env`).

### Request

```json
{
    "message": "Was ist das Wetter heute?",
    "agent": "aifred",
    "token": "dein-geheimer-token",
    "max_tier": 0,
    "delivery": "webhook",
    "webhook_url": "http://homeassistant.local:8123/api/webhook/result"
}
```

### Response

```json
{
    "success": true,
    "session_id": "webhook_a1b2c3d4",
    "message": "Agent triggered, running in background"
}
```

### Security

- Token wird gegen `WEBHOOK_API_TOKEN` geprüft (via Broker, nie im LLM-Kontext)
- `max_tier` ist gekappt auf `DEFAULT_TIER_BY_SOURCE["webhook"]` (Default: 0 = read-only)
- Jeder Trigger bekommt eine isolierte Session
- Rate Limiting greift (aus Security-Layer S6)

---

## Worker Auto-Restart

Alle Worker (Channel-Listener, Scheduler) haben automatischen Restart bei Crashes:

- **Exponentieller Backoff:** 5s → 10s → 20s → 40s → 80s → max 300s
- **Max 5 Versuche**, danach permanenter Stopp
- **UI-Notification** bei permanentem Tod (kein Silent Failure)
- **Health-API:** `message_hub.health()` liefert running-Status + restart_count

---

## Dateien

| Datei | Funktion |
|-------|----------|
| `aifred/lib/scheduler.py` | Job Store, Scheduler Loop, Delivery Modes |
| `aifred/lib/message_hub.py` | Worker-Management mit Auto-Restart |
| `aifred/lib/api/agents.py` | Webhook-API Endpoint (`/api/agent/trigger`) |
| `aifred/lib/credential_broker.py` | WEBHOOK_API_TOKEN Mapping |
| `aifred/aifred.py` | Scheduler-Registrierung beim App-Start |
| `data/scheduler/jobs.db` | SQLite Job-Datenbank |
