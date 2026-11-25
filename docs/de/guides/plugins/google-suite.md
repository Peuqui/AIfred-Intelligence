# Google Suite Plugin

> **English version:** [google-suite.md](../../../en/guides/plugins/google-suite.md)

**Dateien:** `aifred/plugins/tools/google_suite/`

Google Calendar, Contacts, Tasks und Drive über OAuth 2.0. Orchestrator-Plugin mit vier Diensten, jeder einzeln aus, nur lesend oder lesend und schreibend. Benötigt einmaligen OAuth-Flow in der Google Cloud Console. Den OAuth-Mechanismus (Token-Storage, Auto-Refresh, API-Endpoints) beschreibt [oauth.md](oauth.md).

## Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → Neues Projekt → **APIs & Services** → **Credentials** → **Create Credentials** → OAuth 2.0 Client ID (Typ: **Web application**)
2. Folgende APIs aktivieren: **Google Calendar API**, **People API**, **Tasks API v1**, **Google Drive API**
3. Unter **Authorized redirect URIs** eintragen:
   ```
   https://example.com:8443/api/oauth/google/callback
   ```
4. Credentials in `.env` eintragen:
   ```
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
5. Stufe je Dienst in den Plugin-Einstellungen wählen (Plugin-Manager → Zahnrad; Standard: alle vier „Lesen“), speichern
6. Im selben Fenster **Mit Google verbinden** → Google-Login → Zustimmung → fertig. AIfred fragt dabei die Berechtigungen der eingeschalteten Dienste an.

Der API-Weg (`GET /api/oauth/google/auth-url?redirect_uri=…&scopes=…`) bleibt für Skripte; er muss dann die Berechtigungen aller eingeschalteten Dienste anfordern (siehe Tabelle unten), sonst bleibt das Plugin aus. `GET /api/oauth/google/status` meldet nur, ob ein Token gespeichert ist, nicht ob ihm Rechte fehlen.

## Dienste und Stufen

`aifred/plugins/tools/google_suite/settings.json` (geschrieben vom Einstellungsfenster):
```json
{
  "GOOGLE_MODULE_CALENDAR": "read",
  "GOOGLE_MODULE_CONTACTS": "read",
  "GOOGLE_MODULE_TASKS": "read",
  "GOOGLE_MODULE_DRIVE": "read"
}
```

Jeder Dienst steht auf `off`, `read` oder `write` (gemeinsamer Baustein `aifred/lib/plugin_modules.py`, wie bei EPIM und Nextcloud). Ein unbekannter Wert lässt das Speichern scheitern, statt still als irgendeine Stufe zu gelten.

Die Stufe setzt AIfred selbst durch, nicht Google:

- **Aus:** keine Werkzeuge, keine Berechtigung bei Google; Drive aus nimmt Drive auch von der Dokumentenseite.
- **Lesen:** nur die Lesewerkzeuge (`READ_TOOL_NAMES` in der `tools.py` des Dienstes). Die schreibenden stehen nicht im Werkzeugkasten; ruft das Modell trotzdem eines auf, lehnt die Ausführung es als unbekanntes Werkzeug ab. Drive im Dokumentenfenster: ansehen und herunterladen, kein Upload-Feld, und die Quelle lehnt `upload()` ab.
- **Lesen und Schreiben:** alle Werkzeuge des Dienstes, Upload ins Drive.

Bei Google fragt AIfred für jeden eingeschalteten Dienst das volle Recht an, gleich welche Stufe:

| Dienst | Berechtigung bei Google |
|--------|-------------------------|
| Kalender | `calendar` |
| Kontakte | `contacts` |
| Aufgaben | `tasks` |
| Drive | `drive` |

Dazu kommen immer `userinfo.email` und `userinfo.profile`.

**Stufe ändern:** jederzeit, ohne neu zu verbinden — zwischen „Lesen“ und „Lesen und Schreiben“ ändert sich bei Google nichts. Neu verbinden muss man nur, wenn ein Dienst eingeschaltet wird, dem Google noch nicht zugestimmt hat (`oauth_grant_current()`: jedem eingeschalteten Dienst muss sein Recht erteilt sein, weitere Rechte schaden nicht). Bis dahin ist das Plugin aus, und das Einstellungsfenster zeigt den Hinweis „Für die eingeschalteten Dienste fehlen Berechtigungen“ und wieder **Mit Google verbinden**. Wer Google ein Recht ganz entziehen will, entfernt den Zugriff in der Google-Kontoübersicht (Sicherheit → Drittanbieter-Zugriff) und verbindet danach mit den gewünschten Diensten neu.

## Calendar Tools

**Datei:** `aifred/plugins/tools/google_suite/calendar/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_calendar_list_events` | Termine in einem Zeitraum abrufen | READONLY |
| `google_calendar_create_event` | Neuen Termin erstellen | WRITE_DATA |
| `google_calendar_update_event` | Bestehenden Termin ändern (nur gesetzte Felder) | WRITE_DATA |
| `google_calendar_delete_event` | Termin löschen | WRITE_DATA |
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
| `google_contacts_delete` | Kontakt löschen | WRITE_DATA |

**Ressourcennamen:** Format `people/c123456789` — kommt aus den Suchergebnissen von `google_contacts_search` und wird für Update/Delete benötigt.

### Parameter `google_contacts_search`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `query` | Ja | Suchbegriff (Name oder E-Mail) |
| `max_results` | Nein | Max. Treffer (Standard: 10) |

### Parameter `google_contacts_create`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `display_name` | Ja | Vollständiger Name |
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
| `google_tasks_delete` | Aufgabe löschen | WRITE_DATA |

## Drive Tools

**Datei:** `aifred/plugins/tools/google_suite/drive/tools.py`

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_drive_list_files` | Inhalt eines Ordners im Agenten-Ordner auflisten (ohne `folder_id`: der Agenten-Ordner selbst) | WRITE_DATA |
| `google_drive_search` | Volltextsuche im Agenten-Ordner | WRITE_DATA |
| `google_drive_get_file` | Textdatei lesen, auch abschnittsweise nach Zeilen (Google Docs → Klartext, Sheets → CSV) | WRITE_DATA |
| `google_drive_create_file` | Neue Textdatei erstellen und befüllen | WRITE_DATA |
| `google_drive_update_file` | Dateiinhalt überschreiben | WRITE_DATA |
| `google_drive_delete_file` | Datei dauerhaft löschen (kein Papierkorb) | WRITE_SYSTEM |
| `google_drive_create_folder` | Neuen Ordner erstellen | WRITE_DATA |
| `google_drive_move_file` | Datei in anderen Ordner verschieben | WRITE_DATA |

Die Lese-Tools sind bewusst `WRITE_DATA`: Sie legen private Drive-Inhalte offen und
bleiben damit für externe Kanäle (E-Mail, Discord, Telegram) gesperrt. Welche Werkzeuge
bei der Stufe „Lesen“ angeboten werden, entscheidet deshalb nicht der Tier, sondern
`READ_TOOL_NAMES`. `google_drive_get_file` hat keine Größengrenze: Die Datei läuft als
Strom durch, im Speicher bleibt nur der gewählte Zeilenbereich (`lib/line_window.py`).
Binärdateien werden abgelehnt.

## Agenten-Ordner

**Datei:** `aifred/plugins/tools/google_suite/drive/agent_folder.py`

Die Agenten arbeiten nur in **einem** Drive-Ordner. Sein Name steht in der Einstellung
`GOOGLE_DRIVE_AGENT_FOLDER` (Plugin-Einstellungen, Standard `AIfred-Intelligence`); der
Ordner wird direkt unter „Meine Ablage“ gesucht und beim ersten Zugriff angelegt, wenn
er fehlt.

- **Innerhalb frei:** lesen, schreiben, verschieben, löschen und Unterordner anlegen — im Agenten-Ordner und in allen Unterordnern.
- **Nie der Ordner selbst:** Ändern, Verschieben und Löschen des Agenten-Ordners lehnen die Tools ab.
- **Nichts außerhalb:** Jede `file_id` oder `folder_id` außerhalb des Agenten-Ordners wird mit einer Fehlermeldung abgewiesen. Ohne `folder_id` gilt der Agenten-Ordner. Die Suche liefert nur Treffer von innen.
- Die Grenze wird bei jedem Aufbau des Toolkits neu bestimmt; eine umbenannte Einstellung oder ein inzwischen angelegter Ordner gilt ab dem nächsten Turn.
- Gibt es mehrere gleichnamige Ordner in „Meine Ablage“, ist die Grenze nicht eindeutig und die Tools melden einen Fehler; ein leerer Einstellungswert ebenso.
- Die **Dokumentenseite** des Anwenders geht nicht durch diese Grenze: Es sieht das ganze Drive (siehe unten).

## Drive auf der Dokumentenseite

**Dateien:** `aifred/lib/document_sources.py`, `aifred/plugins/tools/google_suite/drive/source.py`, `aifred/lib/file_areas.py`, `aifred/lib/source_ops.py`, `aifred/lib/api/files.py`

Steht Drive mindestens auf „Lesen“ und ist Google mit dem Drive-Recht verbunden, erscheint auf der
Dokumentenseite neben dem lokalen Ordner eine zweite Quelle „Google Drive“
(Auswahl oben). Es ist eine Live-Ansicht, kein Abbild des Drives. Die Seite zeigt sie wie jede
Quelle im Datei-Browser (VueFinder) als Bereich `source-google_drive`, die Vorschau der angeklickten Datei steht rechts daneben.

| Funktion | Verhalten |
|----------|-----------|
| Durchsuchen | Ordner öffnen, Pfad-Leiste des Datei-Browsers. Die Ansicht beginnt in „Meine Ablage“ |
| Suche | Suchfeld des Datei-Browsers, durchsucht das ganze Drive nach Dateiname und Volltext (höchstens 100 Treffer, nach Relevanz) |
| Vorschau | Klick auf eine Datei zeigt sie rechts: Text, Markdown, Tabellen, Bild, PDF, HTML-Seite (im Sandkasten), Audio und Video; „In neuem Tab öffnen“ steht dabei. Die Quelle liefert die Datei gestreamt (`/api/files/source-google_drive/preview` und `/text`) |
| Download | Kontextmenü des Datei-Browsers; die Datei wird gestreamt; eine HTML-Seite wird mit den Bibliotheken, die sie lädt, zu einer eigenständigen Datei zusammengesetzt (`GET /api/files/source-google_drive/download`, Login-Cookie). 404 bei unbekannter Quelle, 502 bei Fehler der Quelle |
| Export | Googles eigene Formate haben keine Datei und werden exportiert: Docs → `.docx`, Sheets → `.xlsx`, Slides → `.pptx`, Zeichnungen → `.pdf`. Andere Google-Formate und Ordner sind nicht herunterladbar |
| Upload | Nur bei Drive auf „Lesen und Schreiben“: Der Upload des Datei-Browsers lädt in den gerade geöffneten Drive-Ordner (Wurzel: „Meine Ablage“), jede Dateiart, und indexiert nichts. Bei „Lesen“ fehlt der Upload, und die Quelle lehnt `upload()` ab |
| Indexieren | Kontextmenü „Indexieren“ je Datei oder Ordner, wie bei jeder Quelle; am Symbol steht, wie viele Abschnitte der Datei im Index sind |

Nicht verfügbar für Drive: Ordner anlegen, Umbenennen, Verschieben, Kopieren und Löschen. Drive
erfüllt nur das Protokoll `DocumentSource`, nicht `EditableDocumentSource` (siehe unten); der
Datei-Browser bietet diese Aktionen deshalb gar nicht erst an.

### Architektur: Dokumentenquellen

Die Dokumentenseite kennt keine Plugins. Ein Tool-Plugin bietet eine Quelle über das
optionale Attribut `document_source` an (ein Objekt, das das Protokoll `DocumentSource` aus
`aifred/lib/document_sources.py` erfüllt, oder `None`, solange das Plugin sie nicht anbietet).
`document_sources()` sammelt sie bei den aktivierten, verfügbaren Plugins ein; ein
deaktiviertes Plugin bietet schlicht keine Quelle an (Plugin-Atomarität, der Kern importiert
kein Plugin). Jede Quelle ist ein Bereich der Datei-API (`source-<key>`, `file_areas.file_area`); ihre
Arbeit macht `SourceOps` (`aifred/lib/source_ops.py`), die erlaubten Aktionen leitet `source_actions` aus dem ab, was die
Quelle selbst kann.

| Teil | Aufgabe |
|------|---------|
| `key`, `label(lang)` | stabile Kennung (`google_drive`) und Name in der Quellenauswahl |
| `writable` | `False`: kein Upload (und keine Änderungen), `upload()` lehnt ab |
| `list_folder(folder_id)` | Ordnerinhalt; Ordner werden über IDs angesprochen, `""` = Wurzel |
| `search(query)` | Suche über die ganze Quelle |
| `download(file_id)` | gestreamter Download (`SourceDownload`) |
| `upload(folder_id, name, data, mime)` | Datei hochladen (nur bei `writable`) |

Dazu das Protokoll `EditableDocumentSource` für eine Quelle, die auch ändern kann: `create_folder`,
`rename`, `delete`, `move`, `copy`, jede Methode antwortet mit der ID, die der Eintrag danach hat. Hat die Quelle diese Methoden und ist
`writable`, bietet der Datei-Browser die Aktionen an (Nextcloud: alle), sonst nicht; der Kern führt keine Liste
der Quellen, die das können. Der Suchindex zieht bei Umbenennen und Verschieben mit und verliert beim Löschen die Abschnitte.

Quellen werfen nur `DocumentSourceError` (Meldung für den Anwender, HTTP 502).
Die gewählte Quelle merkt sich der Store der Seite (`frontend/src/stores/documents.ts`); die Listen und Änderungen
laufen über den Datei-Browser. Eine weitere Quelle (z. B. Nextcloud) braucht damit nur ein Plugin mit `document_source`.

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
