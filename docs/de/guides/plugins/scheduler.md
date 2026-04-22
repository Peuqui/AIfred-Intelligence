# Scheduler Plugin

**Datei:** `aifred/plugins/tools/scheduler_tool/`

Geplante Aufgaben und Cron-Jobs, die AIfred automatisch zu definierten Zeitpunkten ausführt.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `scheduler_create` | Neuen geplanten Job anlegen | WRITE_DATA |
| `scheduler_list` | Alle geplanten Jobs auflisten | READONLY |
| `scheduler_delete` | Geplanten Job löschen | WRITE_DATA |

## Features

- **Drei Schedule-Typen:** `cron` (Cron-Ausdruck, z.B. `0 8 * * *`), `interval` (Sekunden, z.B. `3600`), `once` (ISO-Timestamp)
- **Delivery-Modi:** `log` (Standard), `announce` (an Kanal senden), `review` (in UI anzeigen), `webhook` (HTTP POST)
- **Isolierte Sessions:** Jeder Job läuft in eigener Session
- **Auto-Restart:** Jobs überleben Neustarts des Services
