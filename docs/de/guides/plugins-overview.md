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
| `audio_play` | Audio-Datei abspielen (WAV, MP3, OGG, FLAC) | READONLY |
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

Textübersetzung via DeepL API mit automatischer Quellsprach-Erkennung. 30+ Sprachen, 500.000 Zeichen/Monat kostenlos.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `translate` | Text in eine Zielsprache übersetzen | READONLY |

> **Details:** [Translator Plugin](plugins/translator.md)

---

### Narrator (Dokument → Audio)

**Datei:** `plugins/tools/narrator/`

Vertont ganze Textdokumente aus dem Workspace zu einer MP3-Datei — serverseitig gechunkt und synthetisiert, der Text passiert nie den LLM-Kontext. Engine, GPU-freier Fallback und Stimme (pro Engine) über das Zahnrad im Plugin-Tab einstellbar.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `narrate_file` | Textdatei zu einer MP3-Audiodatei vertonen | WRITE_DATA |

> **Details:** [Narrator Plugin](plugins/narrator.md)

---

### Vision (Kamera + VLM)

**Datei:** `plugins/tools/vision/`

Tools für das Vision-Subsystem — Bild-Snapshots, VLM-Analyse, Gesichtserkennung und Watcher-Steuerung. Aufbauend auf der FrameHub-Pipeline und der Personarium-Identitäten-Datenbank.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `vision_list_sources` | Verfügbare Kameraquellen mit Status auflisten | READONLY |
| `vision_rescan_sources` | Source-Discovery neu starten (z.B. nach Hardware-Wechsel) | READONLY |
| `vision_snapshot` | Einzelbild von einer Quelle holen | READONLY |
| `vision_analyze` | VLM-Analyse eines Snapshots oder Watcher-Events | READONLY |
| `vision_enroll_face` | Gesicht zur Personarium-Datenbank hinzufügen (Identitäts-Enrollment) | WRITE_DATA |
| `vision_start_watch` | Background-Watcher (Motion + Face + optional VLM) auf einer Quelle starten | WRITE_DATA |
| `vision_stop_watch` | Background-Watcher auf einer Quelle stoppen | WRITE_DATA |
| `vision_list_active_watches` | Aktive Watcher auflisten | READONLY |
| `vision_query_events` | Vergangene Vision-Events durchsuchen (Filter: Typ, Quelle, Face-ID, Zeitraum) | READONLY |

> **Details:** [Vision Plugin](plugins/vision.md)

---

### Bibel

**Datei:** `plugins/tools/bible/`

Bibel-Zugriff über ein Tool: exakter Stellen-Lookup für benannte Referenzen und thematische Vektorsuche, Modus automatisch aus der Anfrage gewählt.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `search_bible` | Bibelstelle nachschlagen oder thematisch suchen (Modus automatisch) | READONLY |

> **Details:** [Bible Plugin](plugins/bible.md)

---

### Judaica

**Datei:** `plugins/tools/judaica/`

Zugriff auf den jüdischen Quellkorpus (Tanach, Talmud, Mischna, Midrasch, Halacha, klassische Tora-Kommentare) per exaktem Stellen-Lookup oder thematischer Vektorsuche.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `search_judaica` | Quelle nachschlagen oder thematisch suchen (Modus automatisch) | READONLY |

> **Details:** [Judaica Plugin](plugins/judaica.md)

---

## Channel Plugins

Channel Plugins verbinden AIfred mit externen Kommunikationskanälen. Eingehende Nachrichten werden automatisch verarbeitet und optional beantwortet.

> **Sprachsteuerung:** Die Sprachbedienung läuft über „FreeEcho.2" — Custom-Firmware für den Amazon Echo Dot 2, die das Gerät vom Cloud-Zwang befreit und als lokales Sprach-Interface nutzbar macht. Die Geräte-Firmware lebt in einem separaten Projekt, aber die AIfred-seitige Integration ist ein vollwertiges Channel-Plugin in diesem Repository (`plugins/channels/freeecho2_channel/`, siehe unten).

**Outbound Markdown:** Agenten antworten in Markdown. Jeder Channel wandelt das via `BaseChannel.format_outbound()` in ein Format, das der Empfänger darstellen kann: Email bekommt HTML mit Plaintext-Fallback (multipart/alternative), Telegram bekommt gestripptes Plain, Discord bleibt Markdown (rendert es nativ). Gemeinsame Konverter liegen in `aifred/lib/markdown_render.py` (`md_to_html`, `md_to_plain`). Vollständiges Pattern für neue Channels siehe [Plugin Development → Outbound Markdown Conversion](../../en/guides/plugin-development.md#outbound-markdown-conversion) (englisch).

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
- Absender-Allowlist (Pflicht — siehe unten)
- `/clear` Slash Command (setzt die Konversation zurück UND löscht Channel-Nachrichten, soweit die Berechtigungen es erlauben)
- Kanal- und DM-Nachrichten

**Allowlist (Pflicht):** Der Bot antwortet nur User-IDs, die in den Plugin-Settings gelistet sind (Zahnrad → *Allowed user IDs*, kommagetrennt). Leere Liste heißt **niemand**; das `*`-Wildcard wird **nicht** unterstützt — ein weltoffener Bot ließe jeden dein LLM steuern. **Onboarding eines neuen Users:** einmal den Bot anschreiben lassen — der Versuch wird abgelehnt, aber die numerische User-ID erscheint im Log (`blocked message from user <ID>`); diese ID ins Allowlist-Feld eintragen.

> **Details:** [Discord Plugin](plugins/discord.md)

---

### Telegram

**Datei:** `plugins/channels/telegram_channel/`

Telegram Bot via Long Polling.

**Features:**
- Absender-Allowlist (Pflicht — siehe unten)
- `/clear` löscht die Konversation: Kontext-Reset + Bulk-Delete aller getrackten Chat-Nachrichten (Telegram-Limits gelten: nur Nachrichten, die der Bot gesehen/gesendet hat, jünger als 48 h)
- Nachrichten, die während AIfred-Downtime eingingen, werden beim Start nachgeholt (Telegram puffert bis zu 24 h)
- Auto-Reply konfigurierbar
- Setup-Guide: [Telegram Setup](telegram-setup.md)

**Allowlist (Pflicht):** Gleiches Modell wie Discord — der Bot antwortet nur User-IDs aus den Plugin-Settings (Zahnrad → *Allowed user IDs*). Leer = niemand, `*` wird **nicht** unterstützt. **Onboarding eines neuen Users:** einmal den Bot anschreiben lassen; die numerische User-ID erscheint im Log (`blocked message from <ID>`) — ins Allowlist-Feld eintragen.

> **Details:** [Telegram Plugin](plugins/telegram.md)

---

### FreeEcho.2 (Voice)

**Datei:** `plugins/channels/freeecho2_channel/`

Voice-Channel für das FreeEcho.2-Gerät (siehe Sprachsteuerungs-Hinweis oben). Eingehende Transkripte werden wie bei jedem anderen Channel beantwortet; Antworten werden über die plugin-eigene TTS-Engine als Audio synthetisiert.

**Features:**
- Eigene TTS-Engine, pro Plugin konfiguriert (unabhängig vom Browser)
- Always-Reply-Channel (Sprach-Terminal)
- i18n + agentenspezifische Prompts

**Auth-Token (empfohlen):** Das Plugin betreibt einen WebSocket-Server, mit dem sich das Gerät verbindet. Setze ein Shared Secret in den Channel-Settings (Zahnrad → *Auth token*) **und** denselben Wert in der Web-UI des Pucks (Server → *Auth token*): Registrierungen ohne passendes Token werden abgelehnt, bevor sie einen Device-Slot belegen oder die STT→LLM→TTS-Pipeline steuern können. Ohne Token akzeptiert der Server jeden Host, der den Port erreicht (beim Start wird eine Warnung geloggt). Der explizite *Authentication*-Schalter neben dem Token-Feld kann die Prüfung deaktivieren; nur das wörtliche „Off" tut das — fail-safe Richtung an.

---

### Vigilantia (Kamera-Überwachung)

**Code:** `aifred/lib/` (`frame_hub.py`, `vision_watcher.py`, `frame_sources/`, `vision_*.py`) — **kein Plugin**, sondern fest integriertes Subsystem

Kontinuierliche Kamera-Überwachung — Master Eye, Background-Watcher, Casus Event-Browser, Personarium-Identitäten-Datenbank. Baut auf der FrameHub-Pipeline auf (Frame-Quellen: RTSP + V4L2 in `aifred/lib/frame_sources/`); die LLM-seitigen Tools liegen im [Vision-Tool-Plugin](#vision-kamera--vlm).

**Features:**
- **Master Eye + Per-Source Watcher** im Message-Hub-Worker-Prozess (überlebt Browser-Disconnects)
- **Motion-Detection** via OpenCV MOG2 mit konfigurierbarer Min-Area-Ratio, Warmup-Frames, Event-Throttling
- **Gesichtserkennung** mit insightface (`buffalo_l`), wählbarem Execution-Provider (CUDA/CPU/CoreML), Continuous-Detection-Modus
- **Personarium**: Identitäten-Datenbank für Enrollment (Name + ID + Gruppe), Multi-Pose-Wizard, Known/Unsure/Unknown-Klassifikation via Cosine-Similarity
- **Casus Event-Browser**: Filter (Typ, Quelle, Face-ID), Pagination, Single-Event VLM-Analyse, Bulk-Mode mit Progress + Cancel
- **pHash-Dedup + Cluster-Modus**: Perceptual-Hash auf jedem Event-Frame, Near-Duplicates werden zu Clustern zusammengefasst
- **VRAM-Vorab-Check** vor Bulk-VLM-Analyse — bricht sauber ab statt mittendrin OOM zu produzieren
- **Vigilantia-Feed Live-Card** auf der Hauptseite zeigt die letzten N Events aller Quellen

Die LLM-seitigen Tools für Vision (snapshot, analyze, enroll_face, start_watch etc.) liegen unter [Vision](#vision-kamera--vlm) — Vigilantia ist der dauerhafte Überwachungs-Layer darüber.

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
│   ├── translator/         # DeepL-Übersetzung
│   ├── narrator/           # Dokument → MP3 (narrate_file)
│   ├── vision/             # Kamera-Snapshots, VLM, Gesichtserkennung
│   ├── bible/              # Bibel-Lookup + thematische Suche
│   ├── judaica/            # Jüdischer Quellkorpus
│   ├── epim/               # EPIM-Datenbank
│   │   ├── tools.py
│   │   └── db.py
│   └── google_suite/       # Google Calendar + Contacts (OAuth)
│       ├── calendar/
│       └── contacts/
└── channels/               # Channel Plugins (Kommunikation)
    ├── email_channel/      # E-Mail (IMAP/SMTP)
    ├── discord_channel/    # Discord Bot
    ├── telegram_channel/   # Telegram Bot
    └── freeecho2_channel/  # FreeEcho.2 Sprach-Terminal
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
