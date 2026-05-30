# Scheduler Plugin

**Datei:** `aifred/plugins/tools/scheduler_tool/`

Geplante Aufgaben und Cron-Jobs, die AIfred automatisch zu definierten Zeitpunkten ausführt.
Zum geplanten Zeitpunkt wird die `message` des Jobs wie ein normaler Prompt verarbeitet und
das Ergebnis an den konfigurierten Delivery-Modus übergeben.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `scheduler_create` | Neuen geplanten Job anlegen | WRITE_DATA |
| `scheduler_list` | Alle geplanten Jobs mit Status, nächstem Lauf und Delivery-Modus auflisten | READONLY |
| `scheduler_delete` | Geplanten Job anhand seiner ID löschen | WRITE_DATA |

## `scheduler_create`-Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|--------------|
| `name` | ja | Kurzer, aussagekräftiger Name für den Job |
| `schedule_type` | ja | Einer von `cron`, `interval`, `once` |
| `schedule_expr` | ja | Cron-Ausdruck, Intervall in Sekunden oder ISO-Timestamp |
| `message` | ja | Der Prompt, den AIfred zum geplanten Zeitpunkt verarbeitet |
| `agent` | nein | Zu verwendender Agent (Standard: `aifred`) |
| `delivery` | nein | `log`, `announce`, `review`, `webhook` (Standard: `log`) |
| `channel` | nein | Zielkanal für `announce` (z.B. `telegram`, `discord`, `email`) |
| `recipient` | nein | Empfänger für `announce` (E-Mail-Adresse usw.) |
| `webhook_url` | nein | URL für `webhook`-Delivery |

## Features

- **Drei Schedule-Typen:** `cron` (Cron-Ausdruck, z.B. `0 8 * * *` = täglich 8 Uhr), `interval` (Sekunden, z.B. `3600` = stündlich), `once` (ISO-Timestamp, z.B. `2026-03-30T10:00:00`)
- **Delivery-Modi:** `log` (Standard), `announce` (an einen Kanal senden), `review` (in UI anzeigen), `webhook` (HTTP POST)
- **Tier-Begrenzung:** Jobs laufen als Cron und werden auf das `cron`-Standard-Tier begrenzt, nicht auf das Tier des erstellenden Users
- **Isolierte Ausführung:** Jeder Job läuft aus seinem eigenen gespeicherten Payload
- **Persistent:** Jobs werden im Job-Store abgelegt und überleben Neustarts des Services

## Anwendungsbeispiele

Gesprochene bzw. Chat-Anfragen, die AIfred auf die Tools abbildet:

- „Fasse jeden Morgen um 7 meine E-Mails zusammen und schick es an Telegram"
  → `scheduler_create(name="Morgen-Mail-Digest", schedule_type="cron", schedule_expr="0 7 * * *", message="Fasse meine neuen E-Mails zusammen", delivery="announce", channel="telegram")`
- „Erinnere mich morgen um 10 an den Arzttermin"
  → `scheduler_create(name="Arzt-Reminder", schedule_type="once", schedule_expr="2026-03-31T10:00:00", message="Erinnere mich an den Arzttermin", delivery="review")`
- „Zeig mir meine geplanten Jobs" → `scheduler_list()`
- „Lösche Job 3" → `scheduler_delete(job_id=3)`
