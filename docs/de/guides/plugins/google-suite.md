# Google Suite Plugin

**Dateien:** `aifred/plugins/tools/google_suite/`

Google Calendar, Contacts, Tasks und Drive ueber OAuth 2.0. Orchestrator-Plugin mit vier aktivierbaren Sub-Services. Benoetigt einmaligen OAuth-Flow in der Google Cloud Console.

## Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → Neues Projekt → **APIs & Services** → **Credentials** → **Create Credentials** → OAuth 2.0 Client ID (Typ: **Web application**)
2. Folgende APIs aktivieren: **Google Calendar API**, **People API**, **Tasks API v1**, **Google Drive API**
3. Unter **Authorized redirect URIs** eintragen:
   ```
   https://narnia.spdns.de:8443/api/oauth/google/callback
   ```
4. Credentials in `.env` eintragen:
   ```
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
5. Sub-Services in `aifred/plugins/tools/google_suite/settings.json` aktivieren/deaktivieren (Standard: beide an)
6. OAuth-Flow starten — Auth-URL generieren:
   ```bash
   curl "http://localhost:8002/api/oauth/google/auth-url?redirect_uri=https://narnia.spdns.de:8443/api/oauth/google/callback&scopes=https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/contacts"
   ```
   Zurueckgegebene URL im Browser oeffnen → Google-Login → Weiterleitung auf Callback-URL → fertig.
7. Verbindungsstatus pruefen:
   ```bash
   curl http://localhost:8002/api/oauth/google/status
   ```

## Sub-Services & Settings

`aifred/plugins/tools/google_suite/settings.json`:
```json
{
  "GOOGLE_CALENDAR_ENABLED": "true",
  "GOOGLE_CONTACTS_ENABLED": "true",
  "GOOGLE_TASKS_ENABLED": "false",
  "GOOGLE_DRIVE_ENABLED": "false"
}
```

Einen Sub-Service deaktivieren: Wert auf `"false"` setzen, AIfred neu starten.

## Calendar Tools

**Datei:** `aifred/plugins/tools/google_suite/calendar/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_calendar_list_events` | Termine in einem Zeitraum abrufen | READONLY |
| `google_calendar_create_event` | Neuen Termin erstellen | WRITE_DATA |
| `google_calendar_update_event` | Bestehenden Termin aendern (nur gesetzte Felder) | WRITE_DATA |
| `google_calendar_delete_event` | Termin loeschen | WRITE_DATA |
| `google_calendar_list_calendars` | Alle Kalender des Nutzers auflisten | READONLY |

**Zeitangaben:** RFC 3339 Format, z.B. `2026-04-22T10:00:00+02:00` oder `2026-04-22T08:00:00Z`

### Parameter `google_calendar_list_events`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `start` | Ja | Startzeitpunkt (RFC 3339) |
| `end` | Ja | Endzeitpunkt (RFC 3339) |
| `calendar_id` | Nein | Kalender-ID (Standard: `primary`) |
| `max_results` | Nein | Max. Anzahl Ergebnisse (Standard: 20) |

### Parameter `google_calendar_create_event`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `title` | Ja | Titel des Termins |
| `start` | Ja | Startzeit (RFC 3339) |
| `end` | Ja | Endzeit (RFC 3339) |
| `calendar_id` | Nein | Kalender-ID (Standard: `primary`) |
| `description` | Nein | Beschreibung |
| `location` | Nein | Ort |
| `attendees` | Nein | Kommagetrennte E-Mail-Adressen |

### Parameter `google_calendar_update_event`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `event_id` | Ja | ID des Termins (aus `list_events`) |
| `calendar_id` | Nein | Kalender-ID (Standard: `primary`) |
| `title` | Nein | Neuer Titel |
| `start` | Nein | Neue Startzeit (RFC 3339) |
| `end` | Nein | Neue Endzeit (RFC 3339) |
| `description` | Nein | Neue Beschreibung |
| `location` | Nein | Neuer Ort |

## Contacts Tools

**Datei:** `aifred/plugins/tools/google_suite/contacts/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_contacts_list_all` | Alle Kontakte abrufen (paginiert) | READONLY |
| `google_contacts_list_groups` | Alle Kontaktgruppen/Labels auflisten | READONLY |
| `google_contacts_list_by_group` | Alle Kontakte einer Gruppe abrufen | READONLY |
| `google_contacts_search` | Kontakte nach Name oder E-Mail suchen | READONLY |
| `google_contacts_create` | Neuen Kontakt anlegen | WRITE_DATA |
| `google_contacts_update` | Bestehenden Kontakt aktualisieren (nur gesetzte Felder) | WRITE_DATA |
| `google_contacts_delete` | Kontakt loeschen | WRITE_DATA |

**Ressourcennamen:** Format `people/c123456789` — kommt aus den Suchergebnissen von `google_contacts_search` und wird fuer Update/Delete benoetigt.

### Parameter `google_contacts_search`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `query` | Ja | Suchbegriff (Name oder E-Mail) |
| `max_results` | Nein | Max. Treffer (Standard: 10) |

### Parameter `google_contacts_create`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `display_name` | Ja | Vollstaendiger Name |
| `email` | Nein | E-Mail-Adresse |
| `phone` | Nein | Telefonnummer |
| `organization` | Nein | Firma / Organisation |
| `notes` | Nein | Notizen |
| `group` | Nein | Gruppenname (z.B. `Familie`) |

### Parameter `google_contacts_update`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `resource_name` | Ja | Ressourcenname aus `google_contacts_search` |
| `display_name` | Nein | Neuer Name |
| `email` | Nein | Neue E-Mail |
| `phone` | Nein | Neue Telefonnummer |
| `organization` | Nein | Neue Organisation |
| `notes` | Nein | Neue Notizen |
| `group` | Nein | Gruppe zuweisen |

## Beispiel-Nutzung

> "Was habe ich morgen im Kalender?"

AIfred ruft `google_calendar_list_events(start="2026-04-23T00:00:00+02:00", end="2026-04-23T23:59:59+02:00")` auf.

---

> "Erstelle einen Termin Freitag 15 Uhr Zahnarzt"

AIfred ruft `google_calendar_create_event(title="Zahnarzt", start="2026-04-25T15:00:00+02:00", end="2026-04-25T16:00:00+02:00")` auf.

---

> "Wie lautet die E-Mail von Max Muster?"

AIfred ruft `google_contacts_search(query="Max Muster")` auf.

---

> "Zeig alle Kontakte aus der Gruppe Familie"

AIfred ruft `google_contacts_list_by_group(group_name="Familie")` auf.

---

> "Suche in meinem Drive nach dem Projektplan"

AIfred ruft `google_drive_search(query="Projektplan")` auf.

## Tasks Tools

**Datei:** `aifred/plugins/tools/google_suite/tasks/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_tasks_list_tasklists` | Alle Task-Listen auflisten | READONLY |
| `google_tasks_list` | Aufgaben einer Liste abrufen | READONLY |
| `google_tasks_create` | Neue Aufgabe erstellen | WRITE_DATA |
| `google_tasks_update` | Aufgabe aktualisieren | WRITE_DATA |
| `google_tasks_complete` | Aufgabe als erledigt markieren | WRITE_DATA |
| `google_tasks_delete` | Aufgabe loeschen | WRITE_DATA |

## Drive Tools

**Datei:** `aifred/plugins/tools/google_suite/drive/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_drive_list_files` | Dateien auflisten (optional nach Ordner gefiltert) | READONLY |
| `google_drive_search` | Volltextsuche im Drive | READONLY |
| `google_drive_get_file` | Dateiinhalt lesen (Google Docs → Klartext, Sheets → CSV) | READONLY |
| `google_drive_create_file` | Neue Textdatei erstellen und befuellen | WRITE_DATA |
| `google_drive_update_file` | Dateiinhalt ueberschreiben | WRITE_DATA |
| `google_drive_delete_file` | Datei dauerhaft loeschen | WRITE_DATA |
| `google_drive_create_folder` | Neuen Ordner erstellen | WRITE_DATA |
| `google_drive_move_file` | Datei in anderen Ordner verschieben | WRITE_DATA |


### Parameter `google_drive_search`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `query` | Ja | Suchbegriff oder Drive-Query-Syntax (z.B. `name contains 'Bericht'`) |
| `page_size` | Nein | Max. Ergebnisse (Standard: 20) |

### Parameter `google_drive_create_file`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `name` | Ja | Dateiname mit Endung (z.B. `notiz.txt`) |
| `content` | Ja | Dateiinhalt |
| `folder_id` | Nein | Zielordner-ID (optional) |
| `mime_type` | Nein | MIME-Typ (Standard: `text/plain`) |
