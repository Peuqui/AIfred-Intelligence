# Scheduler & Proaktive Features

> **English version:** [scheduler.md](../../en/architecture/scheduler.md)

**Stand:** 2026-11-24

Das Plugin heißt überall **Horarium** (`plugins/tools/horarium`, Werkzeuge `horarium_*`); die Engine dahinter liegt in `lib/scheduler.py`.

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
| `cron` | `0 8 * * *` | Klassisches 5-Feld-Cron nach Vixie-Regeln (`lib/cron.py`), Ortszeit |
| `interval` | `300` | Feste Intervalle in Sekunden |
| `once` | `2026-04-01T10:00:00` | Einmalige Ausführung zu ISO-Zeitpunkt |

### Cron-Beispiele

| Expression | Bedeutung |
|-----------|-----------|
| `0 8 * * *` | Täglich um 8:00 |
| `*/30 * * * *` | Alle 30 Minuten |
| `0 9 * * 1-5` | Werktags um 9:00 |
| `0 0 1 * *` | Monatlich am 1. um Mitternacht |
| `0 8,14,20 * * *` | Dreimal täglich (8, 14, 20 Uhr): EIN Job mit einer Liste von Stunden |
| `0 */4 * * *` | Alle 4 Stunden |

### Ende eines Zeitplans

`cron`- und `interval`-Jobs können ein Ende haben (`ends_at`, optional): ein Tag `JJJJ-MM-TT` (der Tag läuft noch bis zum
Abend mit) oder ein Zeitpunkt `JJJJ-MM-TTTHH:MM` (danach kein Lauf mehr). Liegt der nächste Lauf nach dem Ende, wird der Job
nach seinem letzten Lauf ausgeschaltet (`enabled = 0`, wie ein einmaliger Job; Historie und Einstellungen bleiben). Ein
Ende vor dem ersten Lauf lehnt der Scheduler beim Anlegen und Ändern ab, ein abgelaufener Job lässt sich erst nach einem
späteren Ende wieder einschalten. Für `once` gibt es kein Ende. Das Format prüft `parse_ends_at` in `lib/scheduler.py`.

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
    retry_count INTEGER,
    ends_at TEXT                     -- NULL = kein Ende; JJJJ-MM-TT oder JJJJ-MM-TTTHH:MM
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

Optional dazu die Plan-Felder `frame`, `parts`, `columns` und `trend` (siehe „Jobs mit Teilaufträgen“).

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
| `announce` | An einen Channel senden (Discord, Telegram, E-Mail) oder auf den FreeEcho-Pucks vorlesen |
| `webhook` | HTTP POST an externe URL (z.B. Home Assistant) |

Die Notification kommt bei jedem Modus, `announce` und `webhook` liefern
zusätzlich aus.

### Ein Job-Lauf

- **Job-Prompt:** `build_job_prompt()` baut die Nachricht an den Agenten aus
  `prompts/<lang>/scheduler/`: Job-Name, wohin die Antwort geht
  (`delivery_<mode>.txt`), die bisherigen Läufe und der Auftrag. Bei einem Job mit Teilaufträgen
  kommen die Ergebnisse der Teile dazu (siehe „Jobs mit Teilaufträgen“); ein Lauf besteht dann aus den Teilen und dem
  Abschluss, bei jedem anderen Job aus genau diesem einen Prompt.
- **Historie pro Job:** Tabelle `job_runs` hält, was jeder Lauf zugestellt hat,
  unabhängig von den Sessions und nur für die neuesten `SCHEDULER_HISTORY_RUNS`
  Läufe pro Job. Genau diese gehen als Auszug in den Prompt, damit sich der
  Job nicht wiederholt: ganze Sätze bis etwa
  `SCHEDULER_HISTORY_EXCERPT_CHARS` Zeichen, nie mitten im Satz, mit „…“, wenn
  gekürzt wurde (`history_excerpt`). Was ein Job exakt behalten muss, soll er
  laut Job-Prompt ins Gedächtnis schreiben. Jeder Lauf hat trotzdem eine
  eigene Session.
- **Die ganze Antwort:** Zugestellt und protokolliert wird die Antwort, wie
  sie im Browser erscheint, ohne Denkblöcke — derselbe `outbound.text`, den
  jede Kanal-Antwort bekommt. Bis 04.10.2026 ging nur der Text nach dem
  letzten Tool-Call raus; schrieb der Agent die Nachricht zuerst und
  aktualisierte danach sein Gedächtnis, kam nur der Schlusssatz an. Gegen
  Arbeitsnotizen in der Zustellung steht der Job-Prompt, und auf dem eigenen
  Zustellkanal fehlt die Sende-Aktion (die Notizen vom 15.09. stammten aus Versandversuchen
  des Agenten). Liefert der Lauf keinen Text, schlägt der Job fehl.
- **Kein doppelter Versand:** Im Scheduler-Lauf fehlt nur das Sende-Werkzeug des
  eigenen Zustellkanals (`Tool.outbound_channel`, `security.may_send_outbound`,
  die Zustellung steht in den Metadaten unter `DELIVERY_CHANNEL_KEY`), beim
  `email`-Werkzeug die Aktion `send`, wenn per Mail zugestellt wird. Das gilt
  auch für Sub-Agenten des Laufs. Die anderen Kanäle stehen dem Agenten offen,
  laut Job-Prompt aber nur, wenn der Auftrag es ausdrücklich verlangt.
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

Auf `freeecho2` wird die Antwort in den Räumen vorgelesen, über denselben Weg wie
das Werkzeug `freeecho2_announce` und die Ansage-API (`resolve_announce_targets`,
`announce_into_rooms`). `recipient` ist ein Raum, `@gruppe` oder `*`; ohne Angabe
gehen alle verbundenen Pucks. `audio_type` wählt den Ton davor (`notification`,
`alarm`), gesprochen wird als der Agent des Jobs. Der Job-Prompt sagt dem Agenten,
dass er zum Hören schreiben soll.

**Fester Text:** Mit `"fixed_text": true` startet kein Modell, `message` geht
wörtlich und pünktlich raus (`_deliver_fixed_text`), auf jedem Kanal, ohne Karte
und ohne Sitzung. Für Wecker und einfache Erinnerungen. Kommt der Text nicht an,
schlägt der Job fehl. Ein Job mit Modell braucht erst den Modellstart (kalt rund
5 Minuten), er muss für eine feste Uhrzeit also früher angesetzt werden.

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

## Jobs mit Teilaufträgen

Ein umfangreicher Auftrag (viele Positionen, mehrere unabhängige Gruppen) passt nicht in einen einzigen Prompt: Das
Modell lässt Positionen still fallen oder kopiert Werte aus früheren Läufen. Deshalb kann ein Job einen Plan tragen. Die
Regie führt dann der Code, nicht das Modell; die Ablaufsteuerung selbst ist die Plan-Engine, die auch das
Chat-Werkzeug `run_task_plan` benutzt und in [Aufgabenpläne](task-plan.md) beschrieben ist. Ein Job ohne `parts`
bleibt ein Lauf mit genau einem Prompt, ohne Sonderweg.

### Payload-Felder

Zusätzlich zu `message` (bei einem Plan der Abschlussauftrag) kennt die Payload vier optionale Felder, alle in
`lib/task_plan.py` (`TaskPlan`, `PLAN_KEYS`):

- `frame`: Text, der für alle Teile gilt (worum es geht, was ein guter Fund ist, was zu meiden ist). Genannte Quellen
  sind Startpunkte, keine Grenze.
- `parts`: Liste von Teilaufträgen mit `id`, `title`, `task` (vollständig, der Teil sieht das Gespräch nicht) und
  `positions`, einer Liste aus `id` und `name`. Jede Position bekommt im Lauf mindestens einen Eintrag.
- `columns`: Archivspalten dieses Jobs, je `name` und `type` (`text`, `zahl` oder `link`), für Werte, die über die
  Läufe verglichen werden (z. B. Shop, Preis, Verfügbarkeit).
- `trend`: `{"value": "<Name einer Spalte vom Typ zahl>"}` für die Verlaufstabelle.

`job_problem` prüft die Payload vor dem Speichern und ruft bei jedem dieser Felder `plan_problem` auf, die eine Prüfung
für Horarium und Chat-Werkzeug: Form der Felder, eindeutige Teil-, Positions- und Spaltennamen, kein Teil ohne
Positionen, die Positions-ID `zusatz` ist reserviert (Funde außerhalb der Liste), Spaltennamen dürfen nicht
`position_id`, `status`, `befund`, `quelle` oder `neue_quellen` heißen, bekannte Spaltentypen, `trend.value` muss eine
`zahl`-Spalte sein, und Teile schließen sich mit `fixed_text` aus. Gelaufen wird der Plan nur, wenn `parts` nicht leer ist.

### Ablauf eines Laufs

`_execute_job` startet bei einem Job mit Teilen zuerst `_run_plan_parts`, dann den Abschluss.

1. **Sitzung vorab.** Der Lauf bekommt wie jeder andere eine frische `channel_id`; `_run_plan_parts` legt die Sitzung
   sofort über `message_processor.session_for_channel("scheduler", channel_id)` an, damit die Teile in derselben
   Sitzung arbeiten, die der Abschluss später über seine Route findet.
2. **Teile nacheinander.** Die Plan-Engine startet je Teil einen Sub-Agenten mit frischem Kontext (Rahmen und Teil, dazu
   die Vorgeschichte aus dem Archiv und, wenn der Job Notizen führt, der Hinweis auf die Notizdatei). Er läuft als
   Agent des Jobs, ohne Persona, mit den Stufen des Jobs, aber ohne Senden: erlaubt sind alle Stufen bis `max_tier` des
   Jobs außer `TIER_COMMUNICATE`. Die Plugin-Einstellung „Erlaubte Tiers“ des Sub-Agent-Plugins gilt hier nicht.
   Zugestellt wird allein vom Horarium.
3. **Eintragen und Abrufprüfung.** Der Teil meldet seine Ergebnisse über das Werkzeug `plan_record`. Ein Eintrag mit
   Status `gefunden` wird nur angenommen, wenn die Quelle in diesem Lauf wirklich abgerufen wurde.
4. **Genau eine Nachforderung.** Bleiben Positionen ohne Eintrag, bekommt derselbe Sub-Agent einmal die Liste der noch
   offenen. Was danach fehlt, gilt als „nicht geprüft“.
5. **Teilfehler.** Wirft ein Teil eine Ausnahme, steht er mit seinem Grund im Ergebnis, und die nächsten Teile laufen
   weiter. Eine Ausnahme außerhalb der Teile (Archiv, Notizen) lässt den ganzen Lauf scheitern; endet die Engine ohne
   Ergebnis, ist das ebenfalls ein Fehler.
6. **Transkripte.** Die Transkripte aller Teile erscheinen als eine Bubble nur im Chatverlauf der Sitzung
   (`record_chat_only_bubble`, Titel aus `scheduler/plan_transcripts_title`); das Modell sieht sie nie, `llm_history`
   bleibt unberührt.
7. **Archiv.** Der Code hängt alle Einträge des Laufs an `plan_archiv.csv` im Job-Ordner (siehe unten). Das Modell
   fasst die Datei nie an.
8. **Notizen.** Neue Quellen, die die Teile gemeldet haben (`neue_quellen`), hängt der Code als Abschnitt mit Datum an die
   Job-Notizen an (nur wenn der Job Notizen führt, `_keeps_notes`).
9. **Abschluss.** Der Abschluss ist der gewohnte Lauf über `process_inbound`. Sein Prompt ist der Job-Prompt
   (`build_job_prompt`) plus der Block aus `scheduler/plan_results.txt`: die Ergebnisse aller Teile
   (`render_results`, einschließlich gescheiterter Teile, nicht geprüfter Positionen, Zusatzfunden und Berichten) und die
   Verlaufstabelle. Das Modell soll gescheiterte Teile und nicht geprüfte Positionen nennen und nichts dazuerfinden.
10. **Zählzeile.** Zugestellt und im Protokoll abgelegt wird die Antwort des Modells, darunter die feste Zeile aus
    `coverage_line` („N von M Positionen geprüft“, bei Lücken mit Liste, bei gescheiterten Teilen mit Grund). Die Zeile
    hängt der Code an, nicht das Modell. Zusatzfunde zählen nicht zur Vollständigkeit.

Ein Teil kann nur weiter delegieren, wo die Rekursionstiefe des Sub-Agent-Plugins es erlaubt (siehe
[Sub-Agenten](subagent-plugin.md)).

### Archiv und Vorgeschichte

`plan_archiv.csv` liegt im Ordner des Jobs (`job_notes.sync`) und wird nur vom Code geschrieben und nur angehängt
(`lib/plan_archive.py`). Pro Eintrag eine Zeile mit den festen Spalten `zeitpunkt`, `teil`, `position`, `status`,
`befund`, `quelle` und danach den Spalten des Jobs in der Reihenfolge des Plans; für jede Position ohne Eintrag eine Zeile
mit Status `nicht_geprueft` (als Befund der Grund des Teilfehlers, falls es einen gab). Das `csv`-Modul schreibt und
liest, Kommas, Anführungszeichen und Zeilenumbrüche im Befund bleiben erhalten. Bekommt der Plan eine neue Spalte,
wird die Datei einmal mit erweitertem Kopf neu geschrieben, alte Zeilen bleiben dort leer; eine entfernte Spalte bleibt
im Kopf stehen.

Aus dem Archiv kommt die Vorgeschichte: `last_entries` liefert je Position die Einträge des letzten Laufs, in dem sie
welche hatte (`nicht_geprueft` zählt nicht). Jeder Teil bekommt sie als Wegweiser, nicht als Werte zum Übernehmen.
Teile sehen einander innerhalb eines Laufs nicht.

### Verlaufstabelle statt Diagramme

Hat der Job `trend`, rechnet der Code nach dem Anhängen des Laufs aus dem Archiv eine Markdown-Tabelle
(`trend_table`) und gibt sie dem Abschluss mit. Je Position: der beste Wert des letzten Laufs (kleinster Wert der
Trend-Spalte unter den `gefunden`-Einträgen) mit Quelle, der beste Wert des Laufs davor, der beste Wert aller Läufe mit
Zeitpunkt und Quelle, und ein Pfeil (↓ kleiner, → gleich, ↑ größer als im vorigen Lauf). Es gibt keine Diagramme und
keine Bilder. Ohne `trend` oder ohne Archivzeilen ist die Tabelle leer und fehlt im Prompt.

### Sperre gegen Selbständerung

Ein laufender Job ändert seinen eigenen Job nicht: Aus einem Lauf (`source == "scheduler"`, `job_id` in den Metadaten)
lehnen `horarium_update` und `horarium_delete` den eigenen Job mit der Fehlermeldung `own_job_locked` ab. Änderungsvorschläge
schreibt der Lauf in seinen Bericht, der Benutzer gibt sie im Chat frei. Andere Jobs sind nicht gesperrt.

### Bearbeiten im Chat

`horarium_create` nimmt `frame`, `parts`, `columns` und `trend`, Felder aus demselben Schema wie das
Chat-Werkzeug (`plan_parameters_schema`). `horarium_update` ersetzt `frame` und `columns` ganz, setzt `trend` (ein
leerer `value` entfernt ihn) und ändert Teile teilgenau: `part_add` fügt einen Teil an, `part_update` ersetzt den Teil
mit derselben `id` (das Modell schickt ihn mit nur der gewünschten Änderung zurück), `part_remove` entfernt einen Teil
nach `id`. Die Operationen laufen in fester Reihenfolge (Rahmen, Spalten, Trend, entfernen, ändern, anfügen); die
fertige Payload wird erst danach als Ganzes geprüft, bei einer ungültigen Payload oder einer unbekannten bzw. doppelten
Teil-ID bleibt der Job unverändert. `horarium_list` zeigt den Plan eines Jobs vollständig (Rahmen, Teile mit Aufgabe und
Positionen, Spalten, Trend).

---

## Jetzt ausführen (Sofort-Lauf)

Die Horarium-Seite kann einen Auftrag sofort starten, auch außerhalb seiner Zeit: `POST /api/jobs/{job_id}/run`
(`api/jobs.py`, Knopf „Jetzt ausführen“). Der Lauf nimmt denselben Weg wie die Schleife (`_execute_job`), aber
`run_job_now` ruft `mark_executed`/`mark_failed` NICHT auf: Die Termine sind starr — `next_run`, `last_run` und der
Ein-/Aus-Zustand des Plans bleiben, wie sie sind (der nächste reguläre Lauf kommt zu seinem eingetragenen Termin, ein
einmaliger Auftrag wird vom Probelauf nicht verbraucht). Der Lauf erscheint im Protokoll (`add_run`) und im Log; Erfolg
und Fehler werden nur geloggt. Antwort sofort (202 mit `job_id` und `name`), der Lauf geht als FastAPI-BackgroundTask
im Server-Loop weiter — nicht in den Hub-Loop (dasselbe Muster wie `/api/agent/trigger`, dessen Ausführung auch
dort läuft). Abgelehnt (409), wenn das Plugin aus ist (`require_plugin_enabled`) oder der Auftrag gerade läuft; 404 für
einen unbekannten Auftrag; ausgeschaltete Aufträge laufen, weil der Knopf die explizite Absicht ist.

Die Doppelstart-Sperre über Thread-Grenzen (Hub-Loop und Server-Loop): `_running_job_ids` ist ein dict
(job_id → Marker des Halters). `claim_job` prüft und belegt in EINEM Schritt über `dict.setdefault` (unter dem GIL
atomar: True nur, wenn der eigene Marker zurückkommt), `release_job` gibt frei. Die Schleife belegt BEVOR sie den Task
plant (skip bei Fehlschlag), der Endpunkt belegt im Endpunkt-Turn (kein await dazwischen); `_run_due_job` und
`run_job_now` geben im finally frei.

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
    "sender": "Home Assistant",
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
- `sender` ist Pflicht (422 ohne) und reine Anzeige: `🪝 Webhook · <sender>` in der Bubble. Webhook und Scheduler gelten immer als Besitzer-Auslöser, die Rechte begrenzt `max_tier`
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
| `aifred/lib/scheduler.py` | Job Store, Scheduler Loop, Delivery Modes, Ablauf eines Jobs mit Teilen (`_run_plan_parts`) |
| `aifred/lib/task_plan.py` | Plan-Datenmodell und die eine Prüfung (`plan_problem`) |
| `aifred/lib/plan_engine.py`, `plan_record.py`, `plan_archive.py` | Plan-Engine, Eintragswerkzeug, Archiv und Verlaufstabelle: [Aufgabenpläne](task-plan.md) |
| `prompts/<lang>/scheduler/plan_*.txt` | Texte des Plan-Ablaufs (Ergebnisblock, Verlauf, Notizen-Hinweis, neue Quellen) |
| `aifred/lib/message_hub.py` | Worker-Management mit Auto-Restart |
| `aifred/lib/api/agents.py` | Webhook-API Endpoint (`/api/agent/trigger`) |
| `aifred/lib/credential_broker.py` | WEBHOOK_API_TOKEN Mapping |
| `aifred/aifred.py` | Scheduler-Registrierung beim App-Start |
| `data/scheduler/jobs.db` | SQLite Job-Datenbank |
