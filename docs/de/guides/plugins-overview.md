# Plugin-Übersicht

AIfred verwendet ein einheitliches Plugin-System. Plugins werden automatisch erkannt — eine `.py`-Datei in `plugins/tools/` oder `plugins/channels/` ablegen, fertig.

> **Entwickler-Guide:** [Plugin Development Guide (EN)](../../en/guides/plugin-development.md)
> **Security:** [Security-Architektur](../architecture/security.md)

---

## Tool Plugins

Tool Plugins stellen dem LLM Werkzeuge zur Verfügung, die es autonom aufrufen kann.

### Workspace (Dateien & Dokumente)

**Datei:** `plugins/tools/workspace/`

Dateizugriff auf das Dokumenten-Verzeichnis (`data/documents/`) und semantische Suche über ChromaDB.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `list_files` | Dateien im Dokumenten-Ordner auflisten | READONLY |
| `read_file` | Datei lesen (PDFs seitenweise mit `pages="1-5"`) | READONLY |
| `write_file` | Textdatei schreiben/bearbeiten (mit Verify) | WRITE_DATA |
| `create_folder` | Unterordner anlegen | WRITE_DATA |
| `delete_file` | Datei von der Platte löschen | WRITE_SYSTEM |
| `delete_folder` | Leeren Ordner löschen | WRITE_SYSTEM |
| `index_document` | Datei in ChromaDB-Vektordatenbank einspeisen | WRITE_DATA |
| `search_documents` | Indexierte Dokumente semantisch durchsuchen | READONLY |
| `list_indexed` | Alle indexierten Dokumente anzeigen | READONLY |
| `delete_document` | Dokument aus Vektordatenbank entfernen | WRITE_SYSTEM |
| `chromadb_stats` | Alle ChromaDB-Collections mit Eintragsanzahl anzeigen | READONLY |
| `chromadb_clear` | Alle Einträge einer Collection löschen | WRITE_SYSTEM |

**Features:**
- PDF-Lesen seitenweise (`pages="3,7,10-12"`)
- Textdateien abschnittweise lesen (`line_start`/`line_end` für große Dateien)
- Path-Traversal-Schutz (nur `data/documents/`)
- Write-Verify: Geschriebene Dateien werden nach Schreiben zurückgelesen und verglichen
- Schreiben nur für Textformate (.txt, .md, .csv, .json, .xml, .html)
- Zentrale ChromaDB-Verwaltung (Research Cache, Documents, Agent Memories)

> **Details:** [Workspace Plugin](plugins/workspace.md)

---

### EPIM (Persönliche Datenbank)

**Datei:** `plugins/tools/epim/`

Vollzugriff auf die [EssentialPIM](https://www.essentialpim.com/) Firebird-Datenbank — Termine, Kontakte, Notizen, Todos, Passwörter.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `epim_search` | Einträge suchen (Kalender, Kontakte, Notizen, Todos, Passwörter) | READONLY |
| `epim_create` | Neuen Eintrag anlegen | WRITE_DATA |
| `epim_update` | Eintrag ändern/verschieben | WRITE_DATA |
| `epim_delete` | Eintrag löschen | WRITE_SYSTEM |

**Features:**
- Automatische Name-zu-ID-Auflösung
- 7-Tage-Datumsreferenz im Prompt
- Anti-Halluzinations-Guardrails
- Field-Mapping (Englisch → Deutsch)

> **Details:** [EPIM Plugin](plugins/epim.md)

---

### Web Research

**Datei:** `plugins/tools/research/`

Automatische Web-Recherche mit mehreren Such-APIs und semantischem Cache.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `web_search` | Web-Suche über Brave, Tavily oder SearXNG | READONLY |
| `web_fetch` | URL-Inhalt abrufen und extrahieren | READONLY |

**Features:**
- Multi-API mit automatischem Fallback
- Scraping und Ranking der Ergebnisse
- Semantischer Vector-Cache via ChromaDB (vermeidet Doppel-Suchen)

> **Details:** [Research Plugin](plugins/research.md)

---

### Sandbox (Code-Ausführung)

**Datei:** `plugins/tools/sandbox/`

Isolierte Python-Code-Ausführung in Subprocess.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `execute_code` | Python-Code ausführen (Dokumente read-only) | WRITE_DATA |
| `execute_code_write` | Python-Code ausführen mit Schreibzugriff auf Dokumente | WRITE_SYSTEM |

**Features:**
- Isolierter Subprocess
- Unterstützt interaktive HTML/JS-Visualisierungen
- Timeout-Schutz

> **Details:** [Sandbox Plugin](plugins/sandbox.md)

---

### Calculator

**Datei:** `plugins/tools/calculator/`

Mathematische Berechnungen.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `calculate` | Mathematische Ausdrücke berechnen | READONLY |

> **Details:** [Calculator Plugin](plugins/calculator.md)

---

### Audio Player

**Datei:** `plugins/tools/audio_player/`

Audio-Wiedergabe auf dem Server.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `audio_play` | Audio-Datei abspielen (WAV, MP3, OGG, FLAC) | WRITE_DATA |
| `audio_stop` | Wiedergabe stoppen | READONLY |
| `audio_status` | Wiedergabe-Status abfragen | READONLY |

> **Details:** [Audio Player Plugin](plugins/audio-player.md)

---

### Scheduler

**Datei:** `plugins/tools/scheduler_tool/`

Geplante Aufgaben für AIfred.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `scheduler_create` | Zeitgesteuerten Job anlegen | WRITE_DATA |
| `scheduler_list` | Alle geplanten Jobs auflisten | READONLY |
| `scheduler_delete` | Job löschen | WRITE_DATA |

**Features:**
- Drei Schedule-Typen: `cron`, `interval` (Sekunden), `once` (ISO-Timestamp)
- Delivery-Modi: `log`, `announce`, `review`, `webhook`
- Isolierte Sessions pro Job

> **Details:** [Scheduler Plugin](plugins/scheduler.md)

---

### System Monitor

**Datei:** `plugins/tools/system_monitor/`

Hardware-Status: CPU, RAM, GPU, Festplatte, Temperatur.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `system_status` | Hardware-Status abfragen (CPU, RAM, GPU, Disk, Temperatur) | READONLY |

> **Details:** [System Monitor Plugin](plugins/system-monitor.md)

---

### Google Suite

**Verzeichnis:** `plugins/tools/google_suite/`

OAuth 2.0 Integration für Google Calendar und Contacts. Orchestrator-Plugin mit aktivierbaren Sub-Services.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `google_calendar_list_events` | Termine in einem Zeitraum abrufen | READONLY |
| `google_calendar_create_event` | Neuen Termin erstellen | WRITE_DATA |
| `google_calendar_update_event` | Bestehenden Termin ändern | WRITE_DATA |
| `google_calendar_delete_event` | Termin löschen | WRITE_DATA |
| `google_calendar_list_calendars` | Alle Kalender auflisten | READONLY |
| `google_contacts_list_all` | Alle Kontakte abrufen (paginiert) | READONLY |
| `google_contacts_list_groups` | Kontaktgruppen/Labels auflisten | READONLY |
| `google_contacts_list_by_group` | Kontakte einer Gruppe abrufen | READONLY |
| `google_contacts_search` | Kontakte nach Name/E-Mail suchen | READONLY |
| `google_contacts_create` | Neuen Kontakt anlegen | WRITE_DATA |
| `google_contacts_update` | Kontakt aktualisieren | WRITE_DATA |
| `google_contacts_delete` | Kontakt löschen | WRITE_DATA |

**Features:**
- Ein OAuth-Login für alle Sub-Services (Scopes werden aggregiert)
- Sub-Services per `settings.json` togglebar (Standard: Calendar + Contacts aktiv)
- Gruppen-Support: Kontakte kategorisieren, nach Gruppe filtern
- Fernet-verschlüsselte Token-Speicherung

**Voraussetzung:** Google Cloud Console Setup + OAuth-Flow. Siehe [OAuth Broker](plugins/oauth.md).

> **Details:** [Google Suite Plugin](plugins/google-suite.md)

---

### Translator (DeepL)

**Datei:** `plugins/tools/translator/`

Textuebersetzung via DeepL API mit automatischer Quellsprach-Erkennung. 30+ Sprachen, 500.000 Zeichen/Monat kostenlos.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `translate` | Text in eine Zielsprache uebersetzen | READONLY |

> **Details:** [Translator Plugin](plugins/translator.md)

---

## Channel Plugins

Channel Plugins verbinden AIfred mit externen Kommunikationskanälen. Eingehende Nachrichten werden automatisch verarbeitet und optional beantwortet.

### Email

**Datei:** `plugins/channels/email_channel/`

IMAP IDLE Push-basierter E-Mail-Monitor mit SMTP Auto-Reply.

**Features:**
- IMAP IDLE (Push, kein Polling)
- Ordner-Management (verschieben, erstellen, auflisten)
- Markieren (gelesen/ungelesen/markiert)
- Auto-Reply pro Kanal konfigurierbar

> **Details:** [Email Plugin](plugins/email.md)

---

### Discord

**Datei:** `plugins/channels/discord_channel/`

Discord Bot mit Channel- und DM-Support.

**Features:**
- WebSocket/Gateway-Verbindung
- `/clear` Slash Command
- Kanal- und DM-Nachrichten

> **Details:** [Discord Plugin](plugins/discord.md)

---

### Telegram

**Datei:** `plugins/channels/telegram_channel/`

Telegram Bot via Long Polling.

**Features:**
- Whitelist-basierter Zugang
- Auto-Reply konfigurierbar
- Setup-Guide: [Telegram Setup](telegram-setup.md)

> **Details:** [Telegram Plugin](plugins/telegram.md)

---

## Plugin-Architektur

```
aifred/plugins/
├── tools/                  # Tool Plugins (LLM-Werkzeuge)
│   ├── workspace/          # Dateien & ChromaDB
│   ├── research/           # Web-Recherche
│   ├── sandbox/            # Code-Ausführung
│   ├── calculator/         # Mathematik
│   ├── audio_player/       # Audio-Wiedergabe
│   ├── scheduler_tool/     # Geplante Aufgaben
│   ├── translator/         # DeepL-Uebersetzung
│   ├── epim/               # EPIM-Datenbank
│   │   ├── tools.py
│   │   └── db.py
│   └── google_suite/       # Google Calendar + Contacts (OAuth)
│       ├── calendar/
│       └── contacts/
└── channels/               # Channel Plugins (Kommunikation)
    ├── email_channel/      # E-Mail (IMAP/SMTP)
    ├── discord_channel/    # Discord Bot
    └── telegram_channel/   # Telegram Bot
```

**Auto-Discovery:** Jede `.py`-Datei mit einem `plugin`-Attribut (Tool) oder einer `BaseChannel`-Subklasse (Channel) wird automatisch erkannt. Kein Registrieren nötig.

**Security Tiers:**

| Tier | Stufe | Beispiele |
|------|-------|-----------|
| 0 | READONLY | Suchen, Lesen, Auflisten |
| 1 | COMMUNICATE | E-Mail senden, Discord-Nachricht |
| 2 | WRITE_DATA | Erstellen, Ändern, Code ausführen |
| 3 | WRITE_SYSTEM | Löschen, System-Operationen |
| 4 | ADMIN | Shell-Zugriff (nicht implementiert) |

**Plugin Manager:** Plugins können zur Laufzeit über das UI-Modal aktiviert/deaktiviert werden (verschiebt Dateien nach `disabled/`).
