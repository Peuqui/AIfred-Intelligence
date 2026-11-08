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
| `/api/chat/inject` | POST | Nachricht in eine Session geben (`sender`, `session_id`, `message`, `token`): Der Server führt den Zug selbst, auch ohne offenen Tab; läuft schon einer, wartet sie dahinter (`queued: true`). Sie wirkt wie deine eigene Eingabe (rechte Bubble), die Kopfzeile nennt den Absender: `[📥 Inject] <sender>`. Unbekannte Session: 404 |
| `/api/chat/status` | GET | `?session_id=` — `is_generating` (läuft gerade ein Zug), `message_count` |
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
| `/api/documents/index/overview` · `/index/orphans/remove` · `/index/clear` | GET / POST / POST | Pflege des Suchindex: Übersicht `{documents, chunks, orphans}` (verwaiste Einträge des lokalen Ordners: `{filename, chunks, indexed}`), verwaiste Einträge herausnehmen `{filenames}` → Übersicht (nur Namen, die gerade verwaist sind; eine vorhandene Datei bleibt im Index), ganzen Index leeren → `{chunks_removed}` (die Dateien bleiben). 503, wenn der Dokumentenspeicher nicht erreichbar ist |
| `/api/agents/choices` · `/agents/{id}/choices` | GET / PATCH | Schnellwahl der Agenten (Einstellung von AIfred, nicht der Session): je Agent `model_id` (leer = AIfreds Modell), `personality`, `reasoning`, `thinking` mit `thinking_options` (`value`, `label`), `speed_available`/`speed`; dazu `automatik_model_id`, `models` und `vision_models` (mit `badge`, `fits`). PATCH ändert, was mitkommt (`model_id`, `personality`, `reasoning`, `thinking`, `speed`; `automatik` nur `model_id`), zuerst das Modell, und antwortet mit allen Zeilen. 404 für unbekannte Agenten, 422 für Werte, die die Zeile nicht anbietet |
| `/api/agents/summary` | GET | Die Agenten, die ein Chat ansprechen kann: keine Systemagenten, keine abgeschalteten (`id`, `display_name`, `emoji`, `role`) |
| `/api/agents` · `/agents/{id}` | GET / PATCH / DELETE | Agenten-Seite: jeder Chat-Agent, auch abgeschaltete (`id`, `display_name`, `emoji`, `description`, `enabled`, `base`, `delete_refusal`); an/aus `{enabled}` (422 für AIfred und Systemagenten), Löschen 204 oder 422 mit Grund (AIfred nie, Systemagenten nie, Sokrates und Salomo erst nach dem Ausbau von Reflex). Abgeschaltet verschwindet ein Agent aus jeder Wahl, ein Zug, Auftrag oder Kanal, der ihn nennt, wird laut abgelehnt |
| `POST /api/agents` · `/agents/{id}/persona` · `/agents/{id}/prompts/{lang}/{key}` · `/agents/{id}/tools` | POST / GET+PATCH / GET+PUT / GET+PUT | Neuer Agent `{id, display_name, emoji, description}` mit den Grundprompts aus `prompts/<lang>/agent-template/` (201, 422 für vergebene oder ungültige ID). Persona `{display_name, emoji, description, aliases, prompt_keys, languages}`, PATCH ändert die mitgeschickten Felder (Aliase klein, ohne leere). Prompt-Text je Sprache und Ebene `{text, exists}`; nichts außerhalb von `prompts/<lang>/` (422). Rechte `{all, groups}` nach Plugin wie gerade angeboten; PUT `{allowed: [...] | null}`, `null` = alle Werkzeuge, eine Liste behält, was sie für Werkzeuge abgeschalteter Plugins hielt. 404 für Systemagenten |
| `/api/memory` · `/memory/{agent}` · `/memory/{agent}/{id}` | GET / GET+DELETE / PATCH+DELETE | Langzeitgedächtnis der Agenten: jeder Agent mit der Zahl seiner Erinnerungen (auch ohne und Sammlungen gelöschter Agenten), seine Einträge (neueste zuerst; `id`, `date`, `type`, `summary`, `content`, `session_id`), einen bearbeiten `{summary, content}` über `AgentMemory.update` (Zusammenfassung eine Zeile, sonst 422; Typ und Sitzung bleiben), einen oder alle löschen. 503 ohne ChromaDB |
| `/api/jobs` · `/jobs/options` · `/jobs/{id}` · `/jobs/{id}/runs` · `/jobs/preview` | GET+POST / GET / PUT+PATCH+DELETE / GET / POST | Geplante Aufträge (`lib/scheduler.py`): Liste mit nächstem und letztem Lauf; Anlegen und Ändern `{name, schedule_type, schedule_expr, message, agent, delivery, channel, recipient, webhook_url, fixed_text, audio_type, max_tier}` mit den Regeln von `scheduler.job_problem` (dieselben wie das Werkzeug der Agenten; Felder nur ihrer Zustellung, ein abgeschalteter Agent und ein Ausdruck ohne nächsten Lauf geben 422); an/aus `{enabled}`; die letzten Läufe; `preview {schedule_type, schedule_expr}` → `{next_run}`. `options` nennt Typen, Zustellwege, Ton-Arten, Stufen, Cron-Vorlagen, wählbare Agenten und die Kanal-Plugins (keine feste Liste) |
| `/api/speech/list` · `/list/refresh` · `/list/move` · `/list/switch` · `/speech/units/{engine}` | GET / POST / POST / POST / PUT | AIfreds Liste der Sprach-Engines (`lib/tts_settings.py`): Zeilen in Sprechreihenfolge `{index, engine, label, enabled, unit, unavailable, reserved, note, warn, status}` und `service_error` (die TTS-Suite antwortet nicht: Liste aus den Einstellungen); `refresh` liest den Katalog frisch; verschieben `{index, delta: -1|1}`, schalten `{index, enabled}`, Einheit `{unit}` — jede Änderung speichert die ganze abgeglichene Liste, 503 ohne Dienst, 422 für eine Stelle oder Einheit, die es nicht gibt |
| `/api/speech/choices` · `/speech/agents?engine=` · `/speech/agents/{id}?engine=` · `/speech/agents/{id}/voices/{engine}` · `/speech/narrator/{engine}` · `/speech/recognition` | GET / GET / PATCH / PUT / GET+PUT / GET | Auswahlen (Engines, Einheiten, Sprachen, Tempo- und Tonhöhenstufen aus `config.py`); jeder Chat-Agent mit Schalter, Sprache und Stimme auf der Engine (`voices`: die des Dienstes und die gespeicherten, ★ zuerst); Schalter/Sprache `{enabled, language}` (für jede Engine), Stimme `{voice, speed, pitch}`; die Stimme des Vorlesers (nur eine, die die Engine anbietet); ob die STT-Suite antwortet. 404 für keinen Chat-Agenten, 422 für Werte außerhalb der Auswahl |
| `/api/audio/sources` · `/audio/sources/folders?path=` · `POST /audio/sources` · `DELETE /audio/sources/{label}` · `…/{label}/index` · `PUT /audio/settings` | GET / GET / POST / DELETE / POST+DELETE / PUT | Quellen des Audio-Players (`lib/audio_library.py`): `{sources: [{label, type, target, is_symlink, indexed}], picker_root, tts_list_max, indexing, message}`; Ordner einer Stelle der Sandbox (422 außerhalb); Ordner als Quelle `{path}` (201, 422 kein Ordner oder aufgelöst außerhalb); entfernen (404 unbekannter Name, Ziel des Symlinks bleibt); einlesen `{force}` (202, 409 solange einer läuft) oder Index leeren; Sandbox `{picker_root}` (absoluter Ordner) und `{tts_list_max}` (≥ 1), sonst 422 |
| `/api/vision/live` · `/vision/cameras/rescan` · `/vision/cameras/{id}/resolutions` · `/vision/cameras/{id}` | GET+PATCH / POST / GET / PATCH | Live-Ansicht der Vision-Seite (`lib/vision_cameras.py`): eine Kamera-Liste für alle Oberflächen `{id, label, alias, hardware_name, kind, available, ptz, auto_start, resolution, prompt_context, motion_min_area_ratio, alerts_enabled, alert_types, quiet_enabled, quiet_start, quiet_end, schedule_enabled, schedule_start, schedule_end, min_event_interval_sec}` (Name immer `VisionStore.source_label`), die gezeigten Kameras und die Einstellungen der Ansicht (`fps`, `teleprompter_mode`, `vlm_cooldown_sec`, `face_throttle_sec`, `visible_sources`; außerhalb des Bereichs 422), Auswahl der Bildrate und PTZ-Stufen, `ptz_heartbeat_ms`; Kameras neu suchen; Auflösungen der Kamera (eine Abfrage des Geräts, Treiber-Standard zuerst); Name, Auflösung (nur angebotene, sonst 422), Briefing, Hintergrund-Schalter, Benachrichtigungen mit Typen, Ruhezeit (Stunden), Aktiv-Zeitfenster (`HH:MM`) und Ereignisabstand einer Kamera; Hintergrund-Schalter und Zeitfenster wirken sofort (`vision_autostart.apply_background_watch`). Bilder über `/vision/stream` und `/vision/snapshot` |
| `/api/vision/ptz/{id}/press` · `…/keepalive` · `…/release` · `…/home` · `/api/vision/photo/{id}` | POST | Kopf einer PTZ-Kamera: fahren `{direction: left\|right\|up\|down\|zoom_in\|zoom_out, speed}`, solange die Taste gehalten wird (Lebenszeichen alle `ptz_heartbeat_ms`, ohne hält der Server nach `VISION_PTZ_DEADMAN_SEC` an), loslassen, Wächterposition; 409 für eine Kamera ohne PTZ. Foto in voller Auflösung → `{url}`, 502 wenn es scheitert |
| `/api/vision/settings` · `/vision/armed` · `/vision/model` | GET+PATCH / PUT / GET+PUT | Einstellungen der Vision-Seite (`lib/vigilantia.py`): Auge `{armed}` (scharf startet die Hintergrund-Wächter aller Kameras mit Hintergrund-Schalter im Zeitfenster, unscharf stoppt sie; 409 bei abgeschaltetem Vision-Plugin), Modus (`live` lädt das Bildmodell sofort), Bildmodell (ein neues entlädt das alte Ollama-Modell), Gesichts- und Personenerkennung, Aufbewahrung (Tage), mit den Auswahllisten (`modes`, `models`, `alert_types`, `event_intervals`); 422 für einen Modus oder eine Aufbewahrung, die das Auge nicht nimmt. Bildmodell geladen? `{loaded}` laut Backend; PUT lädt (auch im Modus „Bei Bedarf“) oder entlädt, 409 ohne gewähltes Modell |
| `/api/vision/alert-rules` · `/vision/alert-rules/{category}` | GET / PATCH | Benachrichtigungs-Regeln je Kategorie (`alert_bus.VISION_RULE_CATEGORIES`: person, vehicle, animal, face_known, face_unsure, face_unknown): `{channels, rules: [{category, label, sinks, vlm}]}`. PATCH `{sink, sink_on}` schaltet einen Kanal, `{vlm}` die Beschreibung durch das Bildmodell; der Dispatcher liest die Regeln sofort neu, Regeln anderer Erzeuger bleiben. 422 für unbekannte Kategorie oder Kanal und für `sink` ohne `sink_on` |
| `/api/vision/rtsp-cameras` · `/vision/rtsp-cameras/{name}` | GET+POST / PUT+DELETE | RTSP-Kameras (`lib/rtsp_cameras.py`, in `rtsp_cameras` der Vision-Einstellungen): jede mit `source_id` und `user_set`/`password_set`, Benutzer und Passwort verlassen den Server nie (stehen als `RTSP_<CRED>_USER/_PASSWORD` in der `.env`). POST legt an (Name muss neu sein), PUT ändert die Verbindung: nur die gesendeten Felder, alle anderen des Eintrags bleiben (`ptz`, `onvif_port`, `snap_channel` …), ein Feld als `""` fällt weg, leere Zugangsdaten lassen die gespeicherten stehen; der Name bleibt (Basis der Quellen-ID). DELETE nimmt die Kamera heraus, ihre Zugangsdaten nur, wenn keine andere Kamera sie nutzt; Ereignisse bleiben. 404 / 422 |
| `/api/vision/describers` · `/vision/describers/move` · `…/switch` · `…/burn-in` | GET / POST / POST / POST | Beschreiber-Liste (`lib/describer_list.py`): wer Bilder beschreibt, in welcher Reihenfolge, `{rows: [{index, source, label, enabled, note, warn, remote}], measuring}` (`source` = `<Rechner>/<Modell>`, `local` oder `main`; jeder konfigurierte Rechner wird gefragt). Verschieben `{index, delta: -1|1}`, Schalten `{index, enabled}`, 422 für eine Position, die die Liste nicht mehr hat. `burn-in {source}` startet das Vermessen eines Beschreibers auf einem anderen Rechner (entlädt dort alle Modelle, lädt dieses mit dem Stressbild) und antwortet sofort mit 202; GET folgt ihm (`measuring`, danach der Hinweis der Zeile). 409, solange eines läuft (eines zur Zeit), 422 für eine Quelle ohne anderen Rechner |
| `/api/vision/casus/events` · `/vision/casus/choices` · `/vision/casus/events/{id}/film` · `/vision/casus/stream` · `PUT …/events/{id}/face` · `DELETE …/events/{id}` · `POST …/events/delete` | GET / GET / GET / GET (SSE) / PUT / DELETE / POST | Ereignisse der Vision (`lib/casus.py`): Seite neueste zuerst `?source&type&face&grouped&offset&limit` → `{events, total, offset, page_size}` (`type` aus `TYPE_FILTERS`, `face` = `all`, `unknown` = von keinem Menschen bestätigt, oder die ID einer Person; `grouped` = eine Zeile je Vorkommnis mit `cluster_member_count`; `limit=1` = das Bild an einer Position, für die Diaschau über alle Seiten; 422 für einen unbekannten Filter). Auswahl der Filter (Quellen mit Ereignissen, Typen, Personen, `taggable_types`). Film eines Vorkommnisses, ältestes zuerst `[{id, has_zoom}]`. Strom: `stored` mit `{latest}` zu Beginn und nach jedem gespeicherten Ereignis. Person zuordnen `{face_id}` (null löst; 422 ohne Gesicht oder unbekannte Person), ein Ereignis löschen (mit Bildern; 404), alle nach Filter `{source, type, face}` → `{deleted}`. Bilder über `/vision/frame?id=&w=&zoom=` |
| `/api/vision/casus/runs` · `/vision/casus/events/{id}/analyze` · `/vision/casus/describe-all` | GET / POST / POST+DELETE | Das Bildmodell an Ereignissen (`lib/casus.py`), Läufe des Servers, je einer zur Zeit: `{analysis: {running, last, error}, describe_all: {running, cancelling, events, clusters, processed, failed, outcome, message}}` (`outcome` nach einem Lauf: `done`, `cancelled`, `nothing`, `busy`, `no_room`, `error`; `message` = der Zustand in Worten). Ein Ereignis analysieren → 202 (409 solange eines läuft, 404, 422 ohne Bild); alle ohne Beschreibung beschreiben → 202 (409), DELETE hält nach dem laufenden Vorkommnis an |
| `/api/vision/casus/recent` | GET | Abzeichen der Vision im Chat (`casus.recent`): `{plugin_enabled, armed, window_minutes, recent, events, refresh_seconds}` — Auge, Zahl der Ereignisse der letzten Minuten, die neuesten Ereignisse, wie oft neu fragen |
| `/api/vision/face/list` · `/{id}/rename` · `/{id}` · `/{id}/embeddings` · `/embedding/{id}` · `/open` · `/open/dismiss` · `/open/{card}/assign` · `/rematch` | GET / POST / DELETE / GET / DELETE / GET / POST / POST / GET+POST | Personen der Vision (`lib/personarium.py`): jede Person `{id, name, embedding_count, last_seen, last_seen_display, crop_url}`; umbenennen (404, 409 für einen vergebenen Namen; die Erkennung lädt neu), löschen mit Embeddings (`message` in der Sprache der Seite), Embeddings neueste zuerst `{id, quality, created_at, crop_url}`, eins löschen. Offene Gesichter, eine Karte je Crop-Datei `{id, member_ids, crop_url, event_type, matched_name, …}`, ohne Obergrenze; verwerfen `{cards}` (404 für eine nicht mehr offene Karte), zuordnen und lernen `{face_id}` oder `{name}` (422 für beides/keins, unbekannte Person, kein Gesicht im Bild). Neu erkennen: POST startet (202, 409 solange es läuft), GET `{running, message}` |
| `POST /api/vision/enroll` · `GET /vision/enroll/preview/{source}` · `POST …/enroll/{token}/capture` · `…/skip` · `DELETE …/captures/last` · `GET …/captures/{i}` · `POST …/finish` · `DELETE /vision/enroll/{token}` | POST / GET / POST / POST / DELETE / GET / POST / DELETE | Person aus mehreren Posen lernen (`lib/enrollment.py`), eine Sitzung im Server zur Zeit: Start `{face_id}` (0 = neue Person) → `{token, face_id, name, poses, step, captures: [{pose, url, score}], preview_interval_ms}`; frischestes Kamerabild (JPEG, Tele wo vorhanden); Aufnahme `{source_id}` (422 ohne Gesicht, 404 unbekannte Kamera), überspringen, letzte zurücknehmen, Ausschnitt einer Aufnahme (JPEG); speichern `{name}` → `{face_id, message}` (422 ohne Aufnahme oder Namen); abbrechen. 409 für eine Sitzung, die nicht mehr die aktuelle ist. Das Embedding verlässt den Server nie |
| `/api/auth/login` · `/me` · `/logout` | POST / GET / POST | Anmeldung der Vue-Oberfläche (setzt dasselbe Login-Cookie wie der Reflex-Login) |
| `/api/session/config` | POST | Konfiguration pro Session: `active_agent`, `multi_agent_mode`, `symposion_agents` |

### Einstellungen, Modelle, System

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/health` | GET | Health-Check mit Backend-Typ/-Status |
| `/api/settings` | GET / PATCH | Allgemeine Einstellungen: `ui_language`, `user_name`, `user_gender`, `enable_tts`, `tts_autoplay`, `dictation_engine`, `show_transcription`, `max_debate_rounds`, `consensus_type`; GET dazu `backend_type` und den Bereich der Debattenrunden (`debate_rounds_min`/`_max`). PATCH ändert nur die mitgeschickten Felder, ein unbekanntes Feld oder ein unbekannter Wert gibt 422. Modelle und Schalter der Agenten: `/api/agents/choices` |
| `/api/models` | GET | Verfügbare Modelle des aktiven Backends + die Vision-fähigen |
| `/api/backend` | GET / PUT | Backend (`lib/backend_overview.py`, `lib/backend_switch.py`): `{backend_type, backends: [{id, label}], cloud_provider, cloud_providers: [{id, label, key_configured}], gpu: {cards: [{name, vram_mb, compute_capability}], total_vram_gb, unsupported_backends}}` (`backends` = die, die die Karten können, und die Cloud; `gpu` null ohne Karte). PUT `{backend_type}` wechselt: das alte gibt seine Modelle frei (llama-swap stoppt, wenn das neue nicht zur Familie gehört), die gespeicherten Modelle des neuen werden gegen seinen Katalog geprüft; antwortet, wenn fertig. 422 für ein Backend, das die Karten nicht können, 409 während eines Wechsels oder einer Kalibrierung |
| `/api/backend/cloud-provider` | PUT | `{provider}`: Anbieter der Cloud; ist die Cloud das Backend, ziehen die Modelle mit (AIfred das erste des Anbieters, wenn er seins nicht hat). 422 für einen unbekannten |
| `/api/calibration` | GET | Kalibrierung (`lib/calibration/`): `{backend_type, model_id, matrix, models}` — `models` je Modell der Agenten `{model_id, agents, calibration: {context, cache_context, split, speed, rope_contexts}}`, `matrix` (llama.cpp, vLLM; sonst null) die Varianten von AIfreds Modell `{columns: [{tts_key, label}], rows: [{vlm_key, label, cells: [{key, status: done/failed/open}]}], tts_problem}` |
| `/api/calibration/run` · `/calibration/run/cancel` | GET+POST / POST | Kalibrierlauf von AIfreds Modell (`lib/calibration/service.py`), einer je Prozess: POST `{cells: ["<vlm>|<tts>"], vllm_resweep, vllm_dry_run}` → 202, läuft im Hintergrund Minuten bis Stunden, stoppt llama-swap und belegt alle Karten; eine angehakte Zelle mit Fehlervermerk wird neu versucht. 409 während eines Laufs, 422 ohne oder mit unbekannter Zelle, 404 ohne vLLM-Checkpoint. GET `?after=` → `{active, model_id, backend_type, started_at, running, outcome: done/cancelled/failed, lines, next}` (die Konsole ab Zeile `after`). Abbruch: der Lauf endet am nächsten Schritt |
| `/api/calibration/options` · `/calibration/vlm-cache` | GET+PATCH / DELETE | Optionen jedes Laufs: `{mode: legacy/ai, allow_hybrid, ai_model, ai_available}` (PATCH `{mode?, allow_hybrid?}`, 422 für einen anderen Modus); DELETE verwirft die gemessenen VLM-Spitzen → `{cleared}` |
| `/api/system/restart-aifred` | POST | AIfred-Dienst neu starten |
| `/api/system/restart-backend` | POST | Dienst des Backends neu starten (Ollama oder llama-swap) |
| `/api/system/reset-defaults` | POST | Einstellungen auf Standardwerte zurücksetzen |

### Agenten, Webhook, OAuth

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: einen Agenten headless ausführen (`sender`, `message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`). Der Auftrag steht links als `🪝 Webhook · <sender>`; Webhook und Scheduler gelten immer als Besitzer-Auslöser, die Rechte begrenzt `max_tier` |
| `/api/agents/export` | GET | Agenten als ZIP-Bündel (`?ids=a,b`: Konfiguration, Prompts, Sampling, Stimmen) |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Vorschau `{format_version, exported_at (Unix-Zeit), agents: [{agent_id, display_name, description, exists_locally}]}` / Import (Multipart: `file`, `ids`, `conflict` = rename\|overwrite\|abort) → `{success, agent_ids, warnings}`; Stimmen kommen in den Stimmenordner der TTS-Suite, vorhandene werden nie überschrieben |
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

# Send a message — the server runs the full pipeline, with or without an open tab
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

---

## Anwendungsfälle

- AIfred von einem anderen Gerät oder über HTTPS bedienen
- Andere Systeme (Home Assistant, Node-RED, Skripte) Fragen über den
  Inject- oder Webhook-Endpoint schicken lassen
- Batch-Abfragen, eigene Apps, Remote-Tests auf Headless-Systemen
