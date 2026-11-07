# REST API

> **English version:** [rest-api.md](../../en/guides/rest-api.md)

AIfreds Backend stellt unter `/api` eine REST API bereit (FastAPI, Port `8002`). Sie ist
eine **Fernbedienung für den Browser**: Sie führt selbst keine Inferenz aus, sondern
reiht Nachrichten in eine Browser-Session ein, die dann die komplette Pipeline durchläuft –
Intent-Erkennung, Multi-Agent-Modi, Recherche, Vision, History-Kompression.
Ein Code-Pfad, alle Features, und du kannst im Browser zusehen, wie die Antwort
gestreamt wird, während dein Skript wartet.

```
API request ──→ queue_pending_message() ──→ browser polls (500 ms)
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
| `/api/audio/announce` · `/api/audio/announce/rooms` | Header `Authorization: Bearer <ANNOUNCE_API_TOKEN>` aus `.env` |
| `/api/oauth/{provider}/callback` | keine (kommt als Redirect des Providers) |
| `/api/auth/login` · `/api/app/*` | keine: Login der Vue-Oberfläche und ihre Hülle (Code, Farben, Texte, Sprache) |
| **alles andere** (inkl. `/api/docs`) | Login-Cookie `aifred_username`, wie ihn der Web-Login setzt |

- Token-Endpoints antworten mit **503**, wenn das Token nicht konfiguriert ist, und mit **403**,
  wenn es falsch ist. Sie sind die vorgesehenen Einstiegspunkte für Skripte und andere Rechner.
- **Pflichtfeld Aufrufername:** Jeder Aufruf der drei Token-Endpoints, die etwas in AIfred
  hineinschicken (`inject`, `trigger`, `announce`), nennt sich beim Namen — `sender`
  (bei `announce`: `speaker`), 1–64 Zeichen, ohne Angabe **422**. Der Name ist **reine Anzeige**
  (Bubble-Kopfzeile, Meldung an das Modell); die Echtheit sichert allein das Token, nie der Name.
  Ein Token gilt für alle Aufrufer des Endpoints — wer es hat, kann jeden Namen angeben.
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
| `/api/chat/inject` | POST | Nachricht in eine Browser-Session einreihen (`sender`, `session_id`, `message`, `token`). Sie wirkt wie deine eigene Eingabe (rechte Bubble), die Kopfzeile nennt den Absender: `[📥 Inject] <sender>` |
| `/api/chat/status` | GET | `?session_id=` — `is_generating`, `message_count` |
| `/api/chat/history` | GET | `?session_id=` — `chat_history` + `llm_history` (ohne Angabe die neueste Session) |
| `/api/chat/clear` | POST | History einer Session leeren (`session_id`) |
| `/api/sessions` | GET / POST | Die eigenen Sessions (`session_id`, `title`, `last_message_at`, `message_count`, `channel`) / neue anlegen |
| `/api/sessions/{id}` | GET / DELETE | Eine eigene Session öffnen (`chat_history`, `config`, `debug_messages`, `running`, `speech_regenerating`, `last_event_id`) / löschen. Fremde oder ungültige IDs antworten 404 |
| `/api/sessions/{id}/messages` | POST | `{text, images}` — der Server führt den Zug selbst, auch ohne offenen Tab; läuft schon einer, wartet die Nachricht samt Bildern (`queued: true`). `images` = URLs aus dem Bild-Upload, nur eigene Uploads der Session |
| `/api/sessions/{id}/images` · `/images/{name}` | POST / DELETE | Bilder für die nächste Nachricht hochladen (multipart `files`, geprüft und verkleinert, → `[{name, url, size_kb}]`) / ein hochgeladenes wieder entfernen |
| `/api/sessions/{id}/images/{name}/rotate` · `/crop` | POST / POST | Ein hochgeladenes Bild um 90° drehen (`{clockwise}`) / zuschneiden (`{x, y, width, height}` in Prozent des Bildes, von links oben) → `{name, url, size_kb}` unter neuem Namen, die alte Datei ist weg; ein Rahmen über das ganze Bild schneidet nichts (derselbe Name). 404 für fremde Bilder, 422 für einen Rahmen außerhalb des Bildes |
| `/api/sessions/{id}/dictation` · `/dictation/{job}/answer` | POST / POST | Aufnahme (`source=mic`) oder Audio-Datei (`source=file`) als multipart `file` mit `job_id` (UUID vom Client) → 202; der Server transkribiert selbst, Rückfragen und Ergebnis kommen über den Ereignisstrom (`dictation_question`, `dictation_text`, `dictation_finished`) / `{yes}` beantwortet eine Rückfrage |
| `/api/sessions/{id}/dictation/text` | POST | `{text}` — was die Spracherkennung des Browsers gehört hat; dieselbe Regel wie beim Diktat des Servers → `{sent}` (an die KI) oder ins Eingabefeld |
| `/api/sessions/{id}/messages/speech` | POST | `{timestamp}` — eine fertige Antwort neu vorlesen: was gerade spielt, endet; die Lesung kommt über den Ereignisstrom, das Gesprochene ersetzt danach den Ton der Blase (`message_updated`; bricht der Dienst ab, bleibt der alte) → 204. 409 bei abgeschalteter Sprachausgabe, 404 ohne Antwort mit diesem Zeitstempel |
| `/api/sessions/{id}/speech/regenerate` | POST | Alle Antworten der Session neu vertonen, ohne Wiedergabe: was gerade spielt, endet; jede Blase kommt mit ihrem neuen Ton als `message_updated`, der Lauf als `speech_regenerating` (`running`), ein Fehler des TTS-Dienstes als `notice` → 204. 409 bei abgeschalteter Sprachausgabe oder wenn schon einer läuft |
| `/api/sessions/{id}/stop` | POST | Laufenden Zug abbrechen (die gestreamte Teilantwort bleibt) und das Vorlesen beenden, in allen Tabs |
| `/api/sessions/{id}/config` | PATCH | `active_agent`, `multi_agent_mode`, `symposion_agents` (nur bekannte Agenten und Modi), `incognito` (ohne Gedächtnis), `debate_seats` (`{critic, judge}` → Agent, ändert nur die genannten Plätze; die Antwort nennt die wirksame Besetzung, Vorgabe aus `settings.json`) |
| `/api/debate/modes` | GET | Die Diskussionsmodi mit den Plätzen, die eine Session für sie wählt (`[{mode, seats}]`, etwa Tribunal: Kritiker und Richter) |
| `/api/sessions/{id}/events` | GET | Ereignisstrom (SSE) der Session: `turn_started`, `agent_started`, `token`, `tool_status`, `message_added`, `message_updated` (Blase mit dem Gesprochenen), `turn_finished`, `debug`, `notice`, Wiedergabeliste `tts` / `tts_pause` / `tts_speak` / `stop`, `speech_regenerating`, … — `?after=<id>` bzw. `Last-Event-ID` |
| `/api/documents/local` · `/local/folders` · `/local/rename` · `/local/delete` · `/local/preview` | GET / POST / POST / POST / GET | Der lokale Dokumentenordner: `?folder=` → `{folder, items, download_prefix}` (Zeilen mit `indexed`, `chunks`, `indexable`); neuer Ordner `{parent, name}`, Umbenennen `{folder, old, new}` (der Index zieht mit), Löschen `{folder, files, folders, from_disk, from_index}` → `{deleted, chunks_removed, failed, listing}`, Vorschau `?path=` → `{name, text}`. 422 für Pfade außerhalb des Ordners und ungültige Namen |
| `/api/documents/local/upload` · `/local/index` · `/api/documents/index` | POST / POST / GET | Hochladen (multipart: `folder`, `files`; gleichnamige Datei wird ersetzt) → `{saved, rejected, listing, indexing}`, jede gespeicherte Datei kommt in die Warteschlange des Index. Indexieren `{folder, files, folders}` (Ordner mit allem, was der Index lesen kann, darunter). Der Server indexiert eine Datei nach der anderen (`lib/document_indexing.py`), eine scheiternde hält den Rest nicht auf; der Fortschritt `{running, current, done, total, indexed, chunks, failed}` gilt für den ganzen Lauf, auch wenn ihn ein anderer Tab gestartet hat |
| `/api/documents/sources` · `/api/documents/source/{key}` · `…/upload` · `…/index` · `…/deindex` | GET / GET / POST / POST / POST | Weitere Quellen der eingeschalteten Plugins (z. B. Nextcloud): Liste `[{key, label, writable}]`, der lokale Ordner zuerst (`local`). Ordner `?folder_id=` ("" = Wurzel) oder Suche `?query=` → `{source, folder_id, query, writable, items}` (Zeilen mit `id`, `indexable`, `indexed`, `chunks`, `download_url`). Hochladen nur in beschreibbare Quellen (sonst 422) und **ohne** Indexieren, Indexieren `{files: [{id, name}], folders: [id]}` in dieselbe Warteschlange wie lokal, Deindexieren `{file_ids}` → `{chunks_removed}`. 404 für eine unbekannte Quelle, 502, wenn die Quelle scheitert. Der Download `…/file/{file_id}` nimmt IDs mit Schrägstrich (Nextclouds ID ist der Pfad) |
| `/api/agents/choices` · `/agents/{id}/choices` | GET / PATCH | Schnellwahl der Agenten (Einstellung von AIfred, nicht der Session): je Agent `model_id` (leer = AIfreds Modell), `personality`, `reasoning`, `thinking` mit `thinking_options` (`value`, `label`), `speed_available`/`speed`; dazu `automatik_model_id`, `models` und `vision_models` (mit `badge`, `fits`). PATCH ändert, was mitkommt (`model_id`, `personality`, `reasoning`, `thinking`, `speed`; `automatik` nur `model_id`), zuerst das Modell, und antwortet mit allen Zeilen. 404 für unbekannte Agenten, 422 für Werte, die die Zeile nicht anbietet |
| `/api/agents/summary` | GET | Die Agenten, die ein Chat ansprechen kann (`id`, `display_name`, `emoji`, `role`) |
| `/api/auth/login` · `/me` · `/logout` | POST / GET / POST | Anmeldung der Vue-Oberfläche (setzt dasselbe Login-Cookie wie der Reflex-Login) |
| `/api/session/config` | POST | Konfiguration pro Session: `active_agent`, `multi_agent_mode`, `symposion_agents` |

### Einstellungen, Modelle, System

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/health` | GET | Health-Check mit Backend-Typ/-Status |
| `/api/settings` | GET / PATCH | Allgemeine Einstellungen: `ui_language`, `user_name`, `user_gender`, `enable_tts`, `tts_autoplay`, `dictation_engine`, `show_transcription`, `max_debate_rounds`, `consensus_type`; GET dazu `backend_type` und den Bereich der Debattenrunden (`debate_rounds_min`/`_max`). PATCH ändert nur die mitgeschickten Felder, ein unbekanntes Feld oder ein unbekannter Wert gibt 422. Modelle und Schalter der Agenten: `/api/agents/choices` |
| `/api/models` | GET | Verfügbare Modelle des aktiven Backends + die Vision-fähigen |
| `/api/system/restart-aifred` | POST | AIfred-Dienst neu starten |
| `/api/system/restart-ollama` | POST | Ollama neu starten |
| `/api/system/reset-defaults` | POST | Einstellungen auf Standardwerte zurücksetzen |

### Agenten, Webhook, OAuth

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: einen Agenten headless ausführen (`sender`, `message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`). Der Auftrag steht links als `🪝 Webhook · <sender>`; Webhook und Scheduler gelten immer als Besitzer-Auslöser, die Rechte begrenzt `max_tier` |
| `/api/agents/export` | GET | Agenten-Definitionen exportieren |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Vorschau / Import eines Agenten-Exports (Multipart-Upload) |
| `/api/oauth/{provider}/auth-url` · `/status` · `/callback` | GET | OAuth-Flow (Google) — siehe [OAuth](plugins/oauth.md) |
| `/api/oauth/{provider}` | DELETE | Tokens eines Providers widerrufen |

### Ansagen auf den Echo-Puck (ohne LLM)

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/audio/announce` | POST | Fertigen Text im Raum vorlesen. Body: `room` (Raumname, `@gruppe` oder `*`), `text` ODER `texts` (mehrere Absätze als EINE Ansage, optional `pause_ms`), `speaker` (Pflicht: Name des Sprechers). 404 bei unbekanntem Raum, 502 wenn nicht zustellbar. Keine Längengrenze |
| `/api/audio/announce/rooms` | GET | Die gerade verbundenen Räume (gültige Werte von `room`) |

Die Ansage steht in der Sitzung des Raums als `🎤 FreeEcho.2 · <speaker>`; das Modell sieht sie als
„Nachricht von <speaker>“.

### Von AIfred ausgehende Aufrufe

Außer den Antworten auf die Endpoints oben ruft AIfred von sich aus nur eine Adresse auf: das
Ergebnis eines Scheduler- oder Webhook-Auftrags mit `delivery: webhook`. Es kommt als `POST` an
`webhook_url` (30 s Timeout, keine Weiterleitungen, private/lokale Adressen werden abgelehnt):

```json
{"job_name": "Tägliches Gebet", "job_id": 7, "result": "…", "timestamp": "2026-10-07T06:45:00"}
```

### Medien (von der UI genutzt)

| Präfix | Inhalt |
|---|---|
| `/api/vision/*` | Snapshots, MJPEG-Streams, Event-Frames, Gesichter registrieren/auflisten/umbenennen, Zonen-Masken, Zonen-Editor |
| `/api/audio/*` | Audiodateien, Testton, Wiedergabeposition |
| `/api/browser/stream/{session_id}` | Push-Bus Server→Browser (siehe [Browser Push Bus](../architecture/browser-push-bus.md)) |

---

## Global vs. pro Session

`/api/settings` und `/api/agents/choices` enthalten, was wirklich global ist (Sprache, Name,
Sprachausgabe, Modelle und Schalter der Agenten). Alles, was zu einer Unterhaltung gehört – Agent,
Diskussionsmodus, Symposion-Teilnehmer – läuft über
`/api/session/config` und wird in der Session-Datei
(`data/sessions/<session_id>.json`) als Single Source of Truth gespeichert:

```json
{
  "data": {
    "config": {
      "active_agent": "aifred",
      "multi_agent_mode": "standard",
      "symposion_agents": []
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
  -d '{"sender": "mein-skript", "session_id": "<session_id>", "message": "What is Python?", "token": "<INJECT_API_TOKEN>"}'

# Wait until the answer is done, then read it
curl -s -b "$COOKIE" "$API/chat/status?session_id=<session_id>"
curl -s -b "$COOKIE" "$API/chat/history?session_id=<session_id>"

# Read a text aloud on the Echo puck (Bearer token, speaker is mandatory)
curl -s -X POST $API/audio/announce \
  -H "Authorization: Bearer <ANNOUNCE_API_TOKEN>" -H "Content-Type: application/json" \
  -d '{"room": "Büro", "text": "Der Commit ist durch.", "speaker": "Codine"}'

# Switch this session to Tribunal
curl -s -b "$COOKIE" -X POST $API/session/config \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "multi_agent_mode": "tribunal"}'

# Change AIfred's model (global)
curl -s -b "$COOKIE" -X PATCH $API/agents/aifred/choices \
  -H "Content-Type: application/json" \
  -d '{"model_id": "<model id from /api/agents/choices>"}'
```

Der Browser muss geöffnet sein und pollen, damit injizierte Nachrichten aufgenommen werden.

---

## Anwendungsfälle

- AIfred von einem anderen Gerät oder über HTTPS bedienen
- Andere Systeme (Home Assistant, Node-RED, Skripte) Fragen über den
  Inject- oder Webhook-Endpoint schicken lassen
- Batch-Abfragen, eigene Apps, Remote-Tests auf Headless-Systemen
