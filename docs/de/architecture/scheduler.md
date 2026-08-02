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
                    │   log/announce/     │
                    │   review/webhook    │
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
| `log` | Nur im Audit-Log und Session (Default) |
| `announce` | An einen Channel senden (Discord, Telegram, E-Mail) |
| `review` | Notification in der Web-UI, User prüft in Session |
| `webhook` | HTTP POST an externe URL (z.B. Home Assistant) |

### announce

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
