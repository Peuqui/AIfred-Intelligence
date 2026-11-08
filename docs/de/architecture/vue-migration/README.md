# Umbau: Reflex → Vue 3 + Server

Stand: 07.11.2026, beschlossen mit Peuqui. Dieses Dokument ist der Plan; die Bestandsaufnahme der
State-Mixins liegt daneben (`inventar-*.md`).

## Ziel

AIfred wird eine reine Server-Anwendung, die ohne Browser vollständig läuft. Die Oberfläche ist ein
Client wie Telegram oder das Echo.

| Schicht | Inhalt | Ort |
|---|---|---|
| Server | Dienste ohne Oberflächen-Zustand: Sitzungen, Züge, Agenten, Einstellungen, Dokumente, TTS/STT, Vision, Kanäle, Scheduler | `aifred/lib/` |
| API | FastAPI: REST für Befehle und Abfragen, ein Ereignisstrom (SSE) je Sitzung | `aifred/lib/api/` |
| Oberfläche | Vue 3, Vite, TypeScript, Pinia, Vue Router (Hash-Modus) | `frontend/` |

Ein Zug (eine Nachricht und alles bis zur fertigen Antwort) läuft immer im Server zu Ende, egal ob
ein Tab offen ist. Mehrere Tabs oder Geräte sehen dieselbe Sitzung live.

## Entscheidungen

- **Ein Port.** Der Server liefert das gebaute Vue (`frontend/dist`), die API und den Ereignisstrom
  aus einem Prozess. Im Heimnetz oder per VPN braucht es keinen Reverse-Proxy; einen Proxy (Caddy
  mit Auto-TLS) nur für Zugriff aus dem Internet. nginx bleibt Peuquis eigener Aufbau.
- **Pfad.** Während Reflex und Vue nebeneinander laufen, liefert das Backend (8002) Vue unter
  `/api/app/` aus; nginx leitet `/api/` dort schon hin, hinter Authelia. Der Build nutzt relative
  Pfade (`base: './'`) und Hash-Routing, damit derselbe Build später unter `/aifred/` läuft. Beim
  endgültigen Wechsel zeigt nginx `/aifred/` auf den Server statt auf das Reflex-Frontend (3002).
- **Farben** bleiben in `aifred/theme.py` (`COLORS`); Vue lädt die CSS-Variablen von
  `GET /api/app/theme.css` (`css_variables()`).
- **Übersetzungen** bleiben in `aifred/lib/i18n/{de,en}.json`; Vue lädt sie von
  `GET /api/app/i18n/{lang}`.
- **Typen der API** entstehen aus den Pydantic-Schemas: OpenAPI der FastAPI-App →
  `openapi-typescript` → `frontend/src/api/schema.d.ts`. Keine doppelt gepflegten Typen.
- **Mikrofon** (Diktat) geben Browser nur über HTTPS oder `localhost` frei. In Produktion liefert
  nginx HTTPS; für andere Aufbauten gehört das in die Installationsanleitung (Tailscale serve,
  Caddy mit lokaler CA oder TLS im Server).
- **Tests:** pytest für Dienste und API, Vitest für Komponenten mit Logik, Playwright für
  Ende-zu-Ende-Abläufe.

## Altlasten, die der Umbau mit aufräumt

- **Feste Agenten** (Peuqui 07.11.2026): AIfred, Sokrates und Salomo werden normale Agenten wie alle
  später eingerichteten: konfigurierbar, abschaltbar, löschbar. Die Diskussionsmodi (Kritiker,
  Tribunal mit Richter) und der Standard-Agent einer Sitzung verweisen auf Rollen, denen man in der
  Konfiguration beliebige Agenten zuordnet, nicht auf feste Namen.
  **Plätze statt Rollen (Peuqui 07.11.2026, nachmittags, umgesetzt):** Die Etiketten `critic`, `judge`,
  `custom` und `main` in `agents.json` waren historisch gewachsen und sind weg; `role` kennt nur noch
  `agent` und `system` (Vision, Kalibrierung), AIfred ist der Grundagent über seine ID. Die
  Diskussionsmodi haben Plätze (`lib/debate_seats.py`): den Verfasser (wer zuerst antwortet; er
  überarbeitet und verteidigt, vorher fest AIfred, was Code-Review mit Refaktorierung brach), Kritiker
  und Richter, **je Sitzung wählbar** (`config.debate_seats`), Vorgabe Sokrates und Salomo aus
  `settings.json`. Jeder Agent kann einen Platz einnehmen und spricht dort mit seiner Persona; die
  Aufgabe gehört dem Platz (`prompts/<lang>/debate/`, je Datei einmal, vorher Kopien in den Ordnern der
  Inhaber). Fehlt der Agent eines Platzes, lehnt der Zug laut ab (`seat`). Jeder Agent spricht mit seiner
  eigenen Temperatur, eine automatische gibt es nicht mehr. Der Reflex-Weg (`lib/multi_agent.py`) spricht
  bis zum Wechsel weiter fest mit AIfred, Sokrates und Salomo (`debate_seats.REFLEX_SEATS`).
  In der Vue-Oberfläche stellt die Kopfleiste des Chats Modus, Plätze und die Teilnehmer des Symposions
  ein (`DebateControls.vue`, `GET /api/debate/modes`). Abschalten und Löschen kommen mit der
  Agenten-Seite in Schritt 5.
- **Agenten-Übersicht** (Peuqui 07.11.2026): je Agent eine Karte mit Persona, Rechten und
  Einstellungen, oben rechts ein Schalter wie bei den Plugins. Jeder Agent ist zur Laufzeit
  ein- und ausschaltbar und löschbar, auch Sokrates und Salomo.
- **AIfred bleibt der Grundagent** (Peuqui 07.11.2026, vormittags): Er ist Namensgeber und der
  eine Agent, den man weder löscht noch abschaltet; umbenennen geht (Anzeigename), die ID bleibt
  `aifred`. Jeder Agent hat ein eigenes Modell oder nimmt AIfreds; AIfred hat immer eins (die
  Standardmodelle aus `config.py`), die Prüfung „kein Modell“ bleibt wie im Reflex-Zug.
- **Denkwahl je Agent** (Peuqui 07.11.2026): 🧠-Wahl und 💭-Reasoning gelten für jeden Agenten
  einzeln, auch im Direktchat (bis dahin dachte dort jeder Agent nach AIfreds Zeile).
- **Titel** vom Modell des letzten Aufrufs im Zug, mit dessen Kontext: Das ist das gerade geladene,
  für den Titel wird nichts nachgeladen (vorher immer AIfreds Modell).
- **Agenten-Schnellwahl als Modal** (Peuqui 07.11.2026): Die Liste vorne im Hauptschirm (je Agent
  Modell, 🎩 Persona, 💭 Reasoning, 🧠 Denken, ⚡ Speed; dazu Automatik und Vision) wandert in ein
  Modal hinter einem Knopf. Wirkt sofort wie heute, schreibt dieselben Felder wie die Agentenkarte
  (eine Komponente für beide). Keine Einstellungen nur für eine Sitzung.

## Schritt 2 im Einzelnen: Zug-Dienst ohne State

Befund der Bestandsaufnahme: Der Browser-Zug steckt in `send_message` (`state/_chat_mixin.py`,
rund 800 Zeilen), `lib/multi_agent.py` schreibt direkt in den State; der Weg ohne Browser
(`message_processor.process_inbound` → `llm_engine.call_llm`) ist ärmer (keine Kompression, kein
Multi-Agent, keine Bilder) und weicht ab. Reflex bleibt im Zweig unverändert, bis Vue alles kann;
der neue Dienst entsteht daneben und ersetzt beim Wechsel beide alten Pfade.

- **`lib/runtime_settings.py`**: Sicht auf `settings.json` mit den Feldern, die Modellwahl, Kontext,
  Sampling und Denken lesen (`agent_tuning`, `backend_type`, `backend_url`,
  `ui_language`, `enable_tts`, `tts_autoplay`, `tts_agents`, `agent_memory_enabled`). Die vorhandenen Funktionen
  (`get_agent_setting`, `get_agent_num_ctx`, `build_llm_options`, `thinking_mode`, …) brauchen nur
  ein Objekt mit diesen Feldern. Der Aufbau von `agent_tuning` aus `settings.json` (heute im
  Backend-Mixin) und die wirksame Modell-ID (heute zweimal: `_effective_model_id` im State,
  `get_effective_model_from_settings` in `config.py`) werden je eine Funktion für alle.
- **`lib/turn_service.py`**: ein Zug als Task der Server-Schleife. Prüfungen (Kalibrier-Sperre,
  Modell), Nutzer-Eintrag, Zeitstempel, Intent und Moduswechsel, Kompression, Routing, Antwort
  des Agenten (zustandslose Fassung von `_run_agent_direct_response` und
  `_stream_agent_to_history`), Speichern unter `session_rmw_lock`, Titel, Abbruch über
  `pipeline_registry`. Ereignisse über `session_events`: `turn_started`, `agent_started`, `token`,
  `tool_status`, `debug`, `message_added`, `turn_finished` (Liste in `inventar-chat-sitzungen.md` 5.2).
- **`lib/reply_speech.py`**: die gesprochene Antwort. Ob vorgelesen wird (`enable_tts` ∧
  `tts_autoplay` ∧ Stimme des Agenten an) und in welcher Sprache (eigene des Agenten, sonst die der
  Frage, sonst die der Oberfläche), gilt hier einmal, auch für den Reflex-State. `stream_call` gibt
  den Text an eine `BrowserReading`; die Wiedergabeliste geht als `tts`, `tts_pause`, `tts_speak`
  über den Ereignisstrom der Sitzung. Ist die Lesung vorbei, hängt das Gesprochene an der Blase
  (`session_storage.keep_reply_audio`, über den Zeitstempel des Eintrags) und die Clients bekommen
  sie neu (`message_updated`). Eine Lesung je Sitzung zur Zeit, auch über Züge hinweg; Stopp beendet
  Zug und Lesung in allen Tabs (`stop`).
- **`lib/turn_debate.py`**: Kritische Prüfung, Auto-Konsens, Tribunal, Symposion über die Plätze der
  Sitzung (`lib/debate_seats.py`: Verfasser, Kritiker, Richter); fehlt der Agent eines Platzes, lehnt
  der Zug mit Grund ab.
- **Bilder** (`lib/turn_images.py`, im Zug `_look_at_images`): Die Oberfläche lädt sie vorab hoch
  (`POST /api/sessions/{id}/images`, geprüft und verkleinert wie jeder Upload, im Upload-Ordner der
  Sitzung), die Nachricht nennt sie per URL; eine Nachricht, die hinter einem laufenden Zug wartet,
  behält ihre Bilder (`session_storage.PendingMessage`). Wer hinsieht, entscheidet weiter
  `vision_routing.chat_describer`: Antwortet EIN Agent ohne Debatte und sieht sein Modell selbst,
  bekommt er die Bilder in seine Anfrage (die Frage läuft durch denselben Aufbau wie jede andere,
  mit der Stil-Erinnerung der Persona). Jeder andere Zug (Debatte, Symposion, Modell ohne Augen)
  bekommt EINE Beschreibung im Nutzer-Turn, alle Agenten arbeiten von derselben Grundlage. Die
  Historie behält nur die URL (`image_marker`). Anders als im Reflex-Zug laufen Absichtserkennung
  und Adressat auch bei Text mit Bild (vorher übersprungen); im Reflex-Zug endete eine Antwort mit
  Bild ohne Debatte, jetzt debattieren die Agenten über die Beschreibung.
- **Diktat und Audio-Dateien** (`lib/dictation.py`): Die Aufnahme geht an
  `POST /api/sessions/{id}/dictation` und wird dort ein Auftrag des Servers, unabhängig von der
  Anfrage (ein gesperrtes Handy bricht nichts ab). Die Auftragsnummer vergibt der Tab vor dem
  Hochladen: Frage oder Text können vor der Antwort auf das Hochladen ankommen. Die Rückfrage bei langer
  Dauer geht als `dictation_question` an diesen Tab, die Antwort wartet ohne Frist.
  Das Ergebnis verteilt sich wie im Browser: langes Transkript → Datei in den Dokumenten mit Link,
  Audio-Datei → Blase mit Transkript, Diktat → direkt als Zug (ohne Warteschlange und Abholer) oder
  ins Eingabefeld (`show_transcription`, Mail/URL im Text).
- **Inkognito je Sitzung** (`incognito` in der Sitzungs-Konfiguration, `PATCH …/config`): Die
  Agenten arbeiten in dieser Sitzung ohne ihr Langzeitgedächtnis (kein Abruf, keine
  Gedächtnis-Werkzeuge, keine Gedächtnis-Schicht im Prompt), in jedem Modus. Im Reflex-Zug war es
  ein Schalter je Tab, der beim Neuladen verloren ging.
- **Kanäle, Scheduler und Alarme auf dem Zug-Dienst (erledigt 07.11.2026):** `process_inbound` behält
  den Vorbau (Kalibrier-Absage über den Kanal, Stufe, Bereinigung, Nutzer-Zuordnung, Sitzung über die
  Routing-Tabelle, Hub-Hinweis) und die Zustellung (`outbound_text`, `send_reply`, Rückgabe an Scheduler
  und Alarme); dazwischen läuft `turn_service.run_turn` im Task des Kanals mit einer `TurnOrigin`: die
  Blase des Kanals, der eingepackte Text (der Zaun `<external_message>` bleibt in der Historie), Stufe,
  Vertrauen und Metadaten des Absenders für jedes Werkzeug des Zugs (`agent_toolkit`, eine Stelle für
  Direktantwort und Debatte), Weckwort oder Auftrag als fester Agent. Damit haben Kanäle jetzt
  Kompression, Diskussionsmodi, Moduswechsel samt Bestätigung, Inkognito je Sitzung (vorher der globale
  `agent_memory_enabled`, in `settings.json` nicht gesetzt), dieselbe Blase wie der Browser und den
  Live-Strom für einen offenen Tab. In einem Diskussionsmodus gehen alle Beiträge hinaus, jeder mit
  „— Name —“ (Peuqui 07.11.2026: wer eine Diskussion will, will sie hören); der Echo liest jeden Beitrag mit
  der Stimme seines Agenten und legt das Gesprochene an dessen Blase (`OutboundMessage.parts`; vorher sprach
  er alles mit der Stimme des angesprochenen und hängte es an die neueste Blase). Im Browser vorgelesen wird
  nur ein Browser-Zug; der Kanal spricht selbst. Einen Text, den AIfred selbst als Aufgabe baut (Auftrag, Alarm:
  `InboundMessage.task`), liest die Absichtserkennung nicht; der feste Agent antwortet in der Sprache der
  Oberfläche. Gefunden im Live-Test (07.11.2026, Qwen3VL-30B-A3B): die Vorlage eines Auftrags galt als
  reiner Wechsel in den Standard-Modus, der Auftrag lieferte nur die Bestätigung; danach rechnete derselbe
  Auftrag über `calculate` richtig (22:06). `run_turn` liefert ein `TurnOutcome` (Status, Agent, Antworten). Anders als
  vorher bleibt eine Frage ohne Antwort in der `llm_history` (wie im Browser), eingepackt. Weg:
  `_call_engine`, `detect_target_agent_via_llm`, `_append_response`, `user_saved` (FreeEchos frühes
  Speichern: der Zug speichert die Frage sofort), aus `call_llm` alles nur für den Hub
  (manuelles num_ctx, Quelle, Ergebnisfelder) und nie Übergebene, `inject_vision_json_context`. Weil
  Kanäle auf der Schleife des Message Hub laufen (eigener Thread): `clear_replay` und das Starten einer
  im Browser wartenden Nachricht gehen über `session_events.on_home_loop`, und das Pipeline-Register
  bricht einen Zug auf dessen eigener Schleife ab (`task.cancel` ist nicht thread-sicher; der Fehler war
  älter, der Stopp-Knopf traf so auch vorher schon Kanal-Züge).

### Stand und offene Punkte (07.11.2026, nachmittags)

Erledigt: Ereignis-Hub, App-Hülle, Einstellungssicht, Zug-Dienst mit allen Modi, Sitzungs-API mit
Besitzerprüfung, Anmeldung, Agentenliste, Vue-Oberfläche mit Chat (Live-Strom, Warteschlange,
Stopp, Agentenwahl, Debug-Konsole), gesprochene Antwort im Browser (Wiedergabeliste, Knopf zum
erneuten Abspielen an der Blase, Sprecher-Vermerk, Blase neu vorlesen, alle Antworten neu
vertonen), Bilder (hochladen, einfügen, ziehen, entfernen, drehen, zuschneiden; gesehen oder einmal
beschrieben), Diktat und Audio-Dateien, Inkognito je Sitzung. Offen:

- **Dateien unter `/_upload/`** (Ton, Bilder, Dokumente): die Tabelle der Ordner und wer sie ohne
  Anmeldung sehen darf, steht in `lib/upload_files.py` (`mount_upload_folders`); die Reflex-App ruft
  sie auf, der Server ohne Reflex muss es beim Wechsel ebenso tun.
- **Agenten-Schnellwahl (erledigt 07.11.2026):** 🎛️ in der Kopfleiste des Chats öffnet
  `AgentChoicesModal.vue`: jeder Agent mit Modell („(wie AIfred-LLM)“ außer bei AIfred), 🎩 (sein
  Emoji), 💭, 🧠 mit den Optionen seines Modells und ⚡, wo es eine `-speed`-Variante gibt; dazu das
  Automatik- und das Vision-Modell (mit Swap-Plakette). Eine Zeile `AgentChoicesRow.vue` dient später
  auch der Agentenkarte. Gezeigt werden immer alle Agenten (Reflex blendete Sokrates und Salomo je nach
  Modus der Sitzung aus; das Modal ist eine Einstellung von AIfred, nicht der Sitzung). Server:
  `GET /api/agents/choices`, `PATCH /api/agents/{id}/choices` (`automatik` nur Modell). Die Regeln stehen
  in `lib/agent_choices.py` und gelten auch für den Reflex-State: Denk-Optionen als Wert und
  Beschriftung, Sampling eines Modells (llama-swap-Flags bzw. `generation_config.json`), Modellwahl mit
  ihren Folgen (Eigenschaften aus den Caches, Speed aus ohne Variante, Sampling des Agenten und bei
  AIfred aller Agenten auf seinem Modell; Vision behält sein Sampling), Vision-Liste mit Plakette.
  Geschrieben wird schlüsselweise (`agent_tuning` der geänderten Agenten, `backend_models` des Backends).
  Nicht übernommen: RoPE-Wahl (nur Ollama, nicht gespeichert) und Modelllisten der Cloud-Backends.
  **Nebeneinander mit Reflex:** Reflex schreibt `agent_tuning` und `backend_models` bei jedem Speichern
  ganz aus seinem Speicher. Darum übernimmt ein Reflex-Tab beim Neuladen der Datei (alle 500 ms, nach
  Änderungszeit) jetzt alles, was die Schnellwahl schreibt (`_reload_agent_choices`: Tuning und Modelle
  nach den Regeln des Servers, vorher nur Sampling und Persona). Offen bleibt nur ein Speichern beider
  Oberflächen innerhalb dieser 500 ms.
- **Einstellungen, Teil 1 (erledigt 07.11.2026):** Kopfleiste mit Seiten (Chat, Einstellungen) in
  `App.vue`, Anmeldung dort; die Chat-Seite bleibt beim Seitenwechsel am Leben (`KeepAlive`). Seite
  `SettingsView.vue`: Sprache, Name, Anrede, Sprachausgabe (an, automatisch vorlesen), Debattenrunden,
  Konsens. `GET/PATCH /api/settings` bereinigt: nur echte Schlüssel, Zusatzfelder geben 422 (vorher
  Modelle fester Agenten, RoPE-Faktoren, `enable_thinking`, `multi_agent_mode` und `backend_type` ohne
  Umschaltlogik); die eigenen Endpunkte für Sprachausgabe und Diktat sind darin aufgegangen (Store
  `settings` als eine Quelle). Grenzen der Runden in `config.py` (`DEBATE_ROUNDS_MIN/_MAX`), auch für
  Reflex. Ein Reflex-Tab übernimmt beim Neuladen nun auch die Anrede.
- **Dokumente, Teil 1 (erledigt 07.11.2026):** Seite `DocumentsView.vue` auf dem lokalen Ordner
  (`data/documents/`): Ordner mit Pfadleiste, anlegen, umbenennen (der Index zieht mit), löschen von der
  Platte und/oder aus dem Index (einzeln, Ordner rekursiv, Auswahl; ein scheiternder Name hält den Rest
  nicht auf), Vorschau über den Parser des Index, Herunterladen, Pfad für AIfred kopieren. Dünne Endpunkte
  über `lib/file_manager.py`. Die Testinstanz teilt ChromaDB mit der Produktion, indexiert aber in
  eigene Sammlungen (`serve_vue_test.py`, `vuetest_documents`).
- **Dokumente, Teil 2 (erledigt 07.11.2026):** Hochladen per Knopf oder auf die Seite gezogen
  (`file_manager.save_upload`, auch für Reflex: Name ohne Verzeichnisteil, nur Typen des Index), jede
  gespeicherte Datei wird indexiert. Indexieren je Datei, Auswahl oder ganzem Ordner (Unterordner mit),
  Deindexieren je Datei. Der Server indexiert in einer Warteschlange eine Datei nach der anderen
  (`lib/document_indexing.py`); die Seite fragt den Fortschritt ab, solange der Lauf läuft, auch wenn
  ihn ein anderer Tab gestartet hat, und zeigt danach das Ergebnis mit den Gründen der Fehlschläge. Der
  Indexier-Knopf erscheint nur an Dateien, die der Index lesen kann (`indexable`). Der Reflex-State
  indexiert bis zum Wechsel weiter selbst (über `file_manager.index_file`).
- **Dokumente, Teil 3 (erledigt 07.11.2026):** Weitere Quellen der eingeschalteten Plugins (Nextcloud,
  Google Drive) in der Quellwahl der Seite (`SourceBrowser.vue`): Ordner über IDs, Suche über die ganze
  Quelle, Download, Hochladen in beschreibbare Quellen **ohne** Indexieren (ihre Dateien liegen hinter
  einer höheren Stufe als die Suche, Indexieren bleibt die Entscheidung je Datei), Indexieren je Datei,
  Auswahl oder Ordner in dieselbe Warteschlange wie lokal (`document_indexing.IndexItem`), Deindexieren.
  Der Download einer Datei mit Schrägstrich in der ID (Nextcloud: jede Datei in einem Unterordner) gab
  404 (`file/{file_id}`, auch auf `main`); jetzt `{file_id:path}`. Die Testinstanz kann einen Ordner als
  Prüfquelle anbieten (`AIFRED_TEST_SOURCE_DIR`). Offen: Fehlertexte nennen die Datei doppelt, weil der
  Indexer sie für die Reflex-Hinweise selbst voranstellt — mit Reflex bereinigen.
- **Index-Pflege (erledigt 07.11.2026):** 🧹 auf der Dokumente-Seite öffnet `IndexCare.vue`: wie viele
  Dokumente und Abschnitte der Suchindex hält, die verwaisten Einträge (Datei im lokalen Ordner nicht
  mehr da) mit Abschnitten und Indexier-Zeitpunkt, einzeln oder alle aus dem Index zu nehmen, und den
  ganzen Index leeren nach einer Rückfrage (die Dateien bleiben). Der Server nimmt nur Einträge heraus,
  die gerade verwaist sind; eine Datei, die noch da ist, verlässt den Index über das Löschen in der
  Ordnerliste. Dünn über `file_manager.list_indexed/list_orphaned/deindex_file` und
  `document_store.clear`, wie der Reflex-Tab.
- **Mehrzahl in Texten (erledigt 07.11.2026):** `tCount(key, count)` in `frontend/src/i18n.ts` nimmt für
  eins den Schlüssel, sonst `<key>_plural`, wie `t(key, count=…)` des Servers („1 Abschnitt“, „2 Dateien“).
  Die Fertig-Meldung des Indexierens setzt sich in beiden Oberflächen aus gezählten Teilen zusammen.
  `tests/test_frontend_i18n_keys.py` verlangt jeden Schlüssel, den das Frontend wörtlich nennt, in
  beiden Sprachdateien, bei `tCount` auch den Plural. Die Reflex-Schlüssel `doc_files_suffix` und
  `doc_file_suffix_one` gehen mit Reflex.
- **API-Meldungen in der UI-Sprache (erledigt 08.11.2026, Peuqui: „deutsch wenn deutsch, englisch wenn
  englisch“):** Eine Ablehnung ist ein `UserError` (`aifred/lib/user_error.py`) mit i18n-Schlüssel und
  Werten; die Art bestimmt den Status (`Invalid` 422, `BadRequest` 400, `Unauthorized` 401, `Refused` 403,
  `Missing` 404, `Conflict` 409, `NotDone` 500, `UpstreamFailed` 502, `Unavailable` 503; Sonderfälle wie
  415 und 416 des Audio-Stroms als eigene Unterklasse am Ort) und ist zugleich die eingebaute Ausnahme,
  die Aufrufer bisher fingen (`Missing` ein `KeyError` …). Geworfen wird dort, wo die Regel lebt (meist
  lib, auch in Plugins wie den Dokumentquellen); der Handler in `api/app.py` antwortet mit `error_<key>`
  in der UI-Sprache, ebenso für unlesbare Anfragen (`error_unreadable_request`) und die Login-Pflicht.
  Die Endpunkte fangen nichts mehr um, die Reflex-Kopie formuliert über `said(error, lang)`. Logs und
  `str()` bleiben englisch, und damit alles, was die Werkzeuge dem Modell geben. Ein abgelehnter Zug
  (`turn_rejected`) trägt seinen Grund als `UserError`; die Dateiverwaltung legt ihn neben das englische
  `detail` in `FileOpResult.problem` (`result.said(lang)`). `ServiceControlRefused` der Statuszeile ist
  jetzt ein `Conflict`, ihre Anfragen tragen kein `lang` mehr. `tests/test_user_error_texts.py` verlangt
  für jeden Aufruf einer `UserError`-Art den Text in beiden Sprachen mit genau den Platzhaltern, die der
  Aufruf füllt (Werte namens `lang` oder `count` gehören `t()` selbst), und lehnt jede `HTTPException`
  im Paket ab. Die Erfolgsmeldungen alter Endpunkte (`SystemActionResponse.message`: Neustart, Grundeinstellungen,
  Zonen-Maske, Chat leeren, Umbenennen) bleiben englisch: Keine Oberfläche zeigt sie (Vue und der Reflex-Zoneneditor
  nehmen eigene übersetzte Texte, „Person gelöscht“ ist schon in der UI-Sprache), sie erreichen nur API-Aufrufer
  wie Skripte und gehören damit zu den Logs (geprüft 08.11.2026). Die Gründe des Audio-Auflösers (`audio_sources`)
  sind fürs Modell geschrieben und stehen englisch in der 404 des Audio-Stroms.
- **Plugins und Kanäle (erledigt 08.11.2026):** Auf der Einstellungsseite `PluginSettings.vue`: die Kanäle mit
  Schalter, Mithören, Auto-Reply und Sicherheitsstufe, die Werkzeug-Plugins mit Schalter; ⚙️ öffnet
  `PluginForm.vue` (Felder in den Worten des Plugins, in Zeilen gruppiert, Auswahlfelder, OAuth mit Anmeldeseite im
  neuen Tab und Warten auf die Verbindung). Die Logik stand nur im Reflex-State, jetzt in `lib/plugin_admin.py` für
  beide Seiten (Helfer für übersetzte Felder dorthin, der Mixin ruft sie): `GET /plugins`, `PUT /plugins/tools/{name}`,
  `PATCH /plugins/channels/{name}`, `GET/PUT /plugins/{name}/form`, `GET /plugins/{name}/oauth-link?origin=`,
  `DELETE /plugins/{name}/oauth`. Behoben: Die Reflex-Seite legte jedes gespeicherte Passwort in den State des
  Browsers; jetzt verlässt ein Passwort den Server nie, das Formular sagt „gespeichert“, ein leeres Feld behält es.
  Ein Kanal ohne Einrichtung lässt sich nicht einschalten (409, die Seite öffnet sein Formular). Abweichung von
  Reflex: Ein OAuth-Plugin ohne Verbindung lässt sich einschalten (die Sperre griff dort nur bei geladenem Plugin);
  es bietet ohne Verbindung keine Werkzeuge, die Liste zeigt den Hinweis. Geprüft gegen die Testinstanz: alle
  Formulare ohne ein einziges Passwort in der Antwort, Rechner aus und an, Formular geöffnet und verworfen; Kanäle
  dort bewusst nicht geschaltet (ein Listener der Testinstanz antwortete neben der Produktion), das Schalten und
  Speichern ist mit Stellvertretern getestet.
- **Speicher und Audit-Log (erledigt 08.11.2026):** Zwei aufklappbare Abschnitte der Einstellungsseite
  (`HousekeepingSettings.vue`, laden beim Aufklappen): die Dateien, die nichts von selbst löscht (Chat-Exporte,
  Sandbox-Ausgaben) mit Öffnen, Übernehmen in den Showcase, Löschen einzeln oder als Auswahl nach Rückfrage, und die
  letzten Werkzeugläufe aus dem Audit-Log. Dünn über `storage_manager` und `security.load_audit_entries`
  (`/api/storage/files…`, `/api/storage/showcase`, `/api/audit`, Modul `api/housekeeping.py`). Geprüft gegen die
  Testinstanz mit einem Prüfexport: gelistet, über den Link geöffnet, über die Seite gelöscht; Audit-Zeile gezeigt.
- **Einstellungen, Teil 2, Paket 1: lesend (erledigt 08.11.2026):** `BackendSettings.vue` auf der Einstellungsseite:
  das Backend mit den Backends, die die Karten können, der Cloud-Anbieter mit dem Stand seines Schlüssels, die
  GPU-Details (eine Zeile je Kartentyp), aufgeklappt die Kalibrierung: je Modell, das die Agenten nutzen, wer darauf
  läuft, Kontext, Schichten-Split und Speed-Split (Ollama: Kontext je RoPE-Faktor), dazu die Matrix von AIfreds Modell
  als Punkte. Umgezogen aus den Mixins nach lib, Reflex ruft sie: `lib/calibration/matrix.py` (Zellstatus für
  llama.cpp und vLLM, Zelle → Profil-ID, Fehlervermerk löschen; vorher drei Kopien der Namensbildung),
  `lib/calibration/model_info.py` (strukturiert statt Konsolentext, Reflex formuliert daraus dieselben Zeilen wie
  bisher, ein Test hält das fest), `lib/backend_overview.py` (verträgliche Backends, GPU-Karten,
  `gpu_detection.server_gpu_info` einmal je Prozess; die Labels aus `config.BACKEND_LABELS` statt vier Kopien),
  `agent_choices.models_in_use`. Endpunkte `GET /backend`, `GET /calibration` (Modul `api/backend.py`). Entfernt:
  `vllm_sidechannel_summary` (las niemand) und `not_applicable` der Matrixzellen (immer falsch, der Strich-Zweig der
  Reflex-Matrix kam nie zum Zug; Rendering vorher/nachher verglichen, nur dieser Zweig fehlt). Der Cloud-Anbieter hat
  einen Standard in `DEFAULT_SETTINGS` (`DEFAULT_CLOUD_API_PROVIDER`), vorher nur als Feld-Default im State.
  Abweichung: Den Hinweis „vLLM braucht Compute 7.0+“ zeigt Vue, wenn vLLM wirklich nicht geht; Reflex zeigte ihn,
  sobald die schwächste Karte unter 7.0 lag, auch wenn eine andere vLLM konnte. Der hier gefundene Fehler der
  vLLM-Matrix (Paare blieben leer) ist mit Paket 3 behoben, siehe dort.
  Geprüft gegen die Testinstanz (llama.cpp: Basis grün, 32.768 Token, Vision 262.144) und lesend mit dem
  Produktionsmodell unter vLLM.
- **Einstellungen, Teil 2, Paket 2: Wechsel und Neustarts (erledigt 08.11.2026):** In `BackendSettings.vue` wählt
  man das Backend (nur die, die die Karten können) und, wenn die Cloud das Backend ist, ihren Anbieter; Knöpfe für
  den Neustart des Backend-Dienstes, AIfreds Neustart und die Grundeinstellungen (beide mit Rückfrage). Der Dienst
  `lib/backend_switch.py` hält, was vorher dreifach im State stand: der Katalog je Backend (`model_catalog`, auch die
  Cloud; llama-swap startet er nur auf Wunsch, beim Wechsel, nie beim bloßen Ansehen, weil eine Kalibrierung es
  absichtlich stoppt), `drop_unavailable_models` (gespeicherte Modelle, die das Backend nicht hat, vergessen: AIfreds
  leer, die anderen auf AIfreds), `choose_vision_model`, `choose_cloud_main_model`, `leave_backend` (Ollama entlädt,
  llama-swap stoppt beim Verlassen der Familie), `switch_backend`, `set_cloud_provider`, `restart_backend` (Ollama oder
  llama-swap, wartet bis er antwortet). Reflex ruft dieselben Regeln (`initialize_backend`, `switch_backend`,
  `set_cloud_api_provider`, `restart_backend`); die Modelle des Ziel-Backends lädt Reflex jetzt über
  `runtime_settings.apply_saved_models`/`apply_default_models` statt einer eigenen Kopie, die nur Sokrates und Salomo
  kannte. AIfreds Neustart: `process_utils.restart_aifred_soon` für beide. Endpunkte `PUT /backend`,
  `PUT /backend/cloud-provider`, `POST /system/restart-backend` (ersetzt `/system/restart-ollama`, kann auch
  llama-swap); die Schnellwahl nimmt denselben Katalog (kennt damit die Cloud). Behoben: Ein Anbieterwechsel in Reflex
  baute Vision-Zeilen mit `name` statt `id`/`label` (Auswahlfeld leer), sortierte nicht, setzte die Automatik auf das
  erste Modell statt „wie AIfred“ und ließ den übrigen Agenten Modelle des alten Anbieters; der Neustart las bei
  llama-swap den Katalog nicht neu; die Kalibrier-Info las das Bildmodell unter vLLM als vLLM-Eintrag (jetzt nach
  `model_discovery.is_vllm_entry`). Neu: Ein Wechsel ist während eines anderen Wechsels und während einer
  Kalibrierung gesperrt (409). Geprüft an der Testinstanz: llama.cpp → vLLM → llama.cpp (llama-swap lief weiter, die
  Modelle des jeweiligen Backends kamen aus `backend_models`), Anbieterwahl, unbekanntes Backend (422 in der
  UI-Sprache), Grundeinstellungen (danach die Datei der Testinstanz zurückgespielt); Reflex-Rendering unverändert.
  Live geprüft 08.11.2026 an der Testinstanz (`~/.cache/aifred-e2e/vue/restart_switch_live.py`): Neustart von
  llama-swap (antwortete 0,5 s danach, Katalog vollständig), AIfreds Neustart (Dienst neu gestartet), Wechsel zu
  Ollama und zur Cloud (llama-swap gestoppt, leerer Katalog ließ die Modelle stehen) und jeweils zurück zu llama.cpp
  (llama-swap gestartet, Katalog und AIfreds Modell wie vorher).
- **Einstellungen, Teil 2, Paket 3: Kalibrierlauf (gebaut 08.11.2026, echter Lauf offen):** Der ganze Lauf (vLLM-
  Betriebspunkt, llama.cpp-Variantenfamilie mit Burn-ins, TTS-, VLM- und Kombi-Profilen und Speed-Varianten, Ollama-
  RoPE) zog mechanisch aus dem Mixin nach `lib/calibration/service.py`: `CalibrationRun` bekommt einen
  `CalibrationRequest` (Backend, AIfreds Modell, angehakte Zellen, vLLM-Nachmessen/Trockenlauf), meldet jede Zeile
  über einen Rückruf und ist ein Async-Generator, an dessen `yield`-Stellen der Aufrufer Zeilen abholt und einen
  Abbruch prüft; `aclose()` lässt die inneren `finally` llama-swap wieder starten. Was der Lauf früher in den State
  schrieb (Denkfähigkeit, Speed-Variante, Revisionszähler), liest die Sitzung danach aus Caches und Config; der
  Server setzt Speed aus, wo die Speed-Variante fehlt (`settle_speed_after_run`). `check_request` lehnt ab, was nicht
  laufen kann (Cloud, kein Modell, keine Zelle, fehlender vLLM-Checkpoint). Der Server-Lauf (`start_server_run`) ist
  ein Task, einer je Prozess über das Tor; Abbruch über `request_cancel`, den auch der Reflex-Lauf jetzt beachtet.
  Optionen (Modus, Hybrid) in `calibration_options`/`set_calibration_options`, Reflex ruft sie. Endpunkte
  `GET/POST /calibration/run` (Konsole ab Zeile `after`), `POST /calibration/run/cancel`, `GET/PATCH
  /calibration/options`, `DELETE /calibration/vlm-cache`. Vue: `CalibrationPanel.vue` (Häkchen in der Matrix,
  Optionen, Start mit Rückfrage, Konsole alle 2 s nachgeladen, Abbruch). Anders als Reflex löscht Vue den
  Fehlervermerk einer Zelle erst beim Start, nicht schon beim Anhaken. Behoben: Reflex verlor
  `calibration_allow_hybrid` bei jedem Speichern (`_save_settings` kannte den Schlüssel nicht); der KI-Modus zeigte
  `qwen-plus`, wenn der Kalibrier-Agent kein Modell hatte (der Lauf bricht dann ab), und prüfte nur den
  DashScope-Schlüssel statt den des eingestellten Anbieters. Entfernt: das wirkungslose `session_scope` um den
  VLM-Burn-in. Geprüft: Tests mit Stellvertreter-Schritten (fertig, Abbruch, Fehler, doppelter Start), Oberfläche
  an der Testinstanz ohne Start, Reflex-Rendering unverändert. Live 08.11.2026 über die API der Testinstanz
  (`~/.cache/aifred-e2e/vue/calibration_live.py`, Qwen3VL-4B, Basiszelle): ein voller Lauf in 63 s (llama-swap
  gestoppt, ein Prüfstart mit echtem Laden bei 262.144 Token auf einer RTX 8000, Profil geschrieben, llama-swap
  wieder gestartet, Denktest; inhaltlich änderte sich in der Config nur dieses Profil, der YAML-Schreiber formatiert
  die Datei dabei neu) und ein Lauf mit Abbruch (hielt am nächsten Schritt, Warnung, llama-swap wieder an).
  **Dabei gefunden und behoben:** Das Aufräumen vor jedem Prüfstart (`verifier.kill_orphan_on_port`) beendete per
  `fuser -k` jeden Prozess auf dem Port des Testservers. Die Testinstanz hatte `LLAMACPP_CALIBRATION_PORT` nicht aus
  der `.env` und nahm den Standard 9999 — den Port der AI-Connect-Bridge (auch Standard von AIfreds AI-Connect-
  Kanal): Die Bridge wurde zweimal beendet, ihr Neustart scheiterte am belegten Port, und der Prozess hing ohne
  Socket weiter (neu gestartet über PolKit). Jetzt beendet das Aufräumen nur einen `llama-server` und nennt jeden
  anderen Prozess im Log; der Standard-Port ist 19999 wie in der `.env` der Produktion. Ein dritter Lauf mit TTS-,
  VLM- und Kombi-Zelle (`calibration_cells_live.py`, 17 s): Basis übernommen, alle drei Varianten im getrennten Modus
  (Sprachmodell auf einer RTX 8000, TTS und Bildmodell auf der V100-Karte 4), Profile mit der Verteilung der Basis
  geschrieben. Ungeprüft bleiben die Varianten-Wege mit eigenen Prüfstarts (Sprachmodell teilt die Karte mit TTS oder
  Bildmodell) und der Burn-in eines Bildmodells ohne Cache-Wert: Dafür bräuchte es ein GGUF, das über die
  Side-Channel-Karte reicht, und auf dem Mini liegen nur das 4B und das 30B (beide passen auf eine Karte).
  **Paare der vLLM-Matrix neu (Peuqui 08.11.2026: Regel wie bei llama.cpp):** Die gemeinsame Messung
  (`sidechannel_burnin.py`, entfernt) hat nie funktioniert: Sie lud das Bildmodell nicht (`prewarm_vlm` ist im Modus
  „bei Bedarf“ ein No-op, und llama-swap war gestoppt), maß die gerade laufende TTS-Engine statt der angehakten Paare,
  speicherte unter der Modell-ID statt unter dem Schlüssel der Matrix und hätte ein „passt nicht“ mit `_sc["fits"]`
  auf einer Dataclass gar nicht auswerten können. Jetzt beurteilt `_judge_side_channel_pairs` jedes angehakte Paar mit
  der Regel der llama.cpp-Kombis (`side_channel_pair_fits`): Reserve des Bildmodells (gemessene Spitze aus dem
  Stress-Burn-in plus `VLM_HEADROOM_MIB`; fehlt sie, misst der Burn-in, der das Bildmodell selbst startet) plus
  `needed_mib` der TTS-Engine gegen die Karte(n); ein Paar, das sich nicht beurteilen lässt, bleibt offen und sagt
  warum. Eine gemeinsame Messung unter Last ginge nur, wenn man der TTS-Suite die Karte vorgeben könnte. Dabei
  behoben: Ein vLLM-Lauf nur mit Paaren kehrte vor dem Neustart zurück und ließ llama-swap gestoppt; jetzt startet
  jeder Ausgang llama-swap wieder. Live: 4B allein 7.896, mit Qwen3-TTS 14.432, mit XTTS 11.008, Qwen3-TTS allein
  6.536 MiB von 32.768 auf Karte 4, das 30B-Bildmodell auf der V100 nicht messbar (offen); llama-swap kam nach dem
  Lauf von selbst wieder.
- **Server-Start in lib (Vorarbeit für Schritt 6, 08.11.2026):** `lib/server_startup.py` hält, was AIfred einmal je
  Prozess startet (`start_process_tasks`: Debug-Log, Plattform, die Aufräum-Tasks, Audio-Index, Vision-Prewarm im
  Live-Modus, GPU-Erkennung, Ollama leeren), und die Lifespans `session_events_lifespan`, `message_hub_lifespan` (Hub,
  Scheduler, Vigilantia-Autostart) und `service_status_lifespan`, vorher in `aifred.py`. Reiner Umzug: Reflex ruft
  `start_process_tasks` weiter beim ersten Seitenaufruf, registriert dieselben Lifespans; Rendering unverändert. Die
  GPU-Info liest die Sitzung aus `gpu_detection.server_gpu_info` statt aus `_global_backend_state`. Für Schritt 6
  offen: Der Server ohne Reflex muss `start_process_tasks` im Lifespan rufen (heute laufen diese Tasks erst, wenn
  jemand die Reflex-Seite öffnet); die Testinstanz darf das nicht, weil etwa das Aufräumen des vLLM-Compile-Caches
  mit den leeren Betriebspunkten des Worktrees die Caches der Produktion als verworfen ansähe.
- **Schritt 6, Paket 1: Server ohne Reflex (08.11.2026):** `aifred/server.py` (`venv/bin/python -m aifred.server`)
  baut mit `create_app(lifespans)` eine App: die API unter `/api` (Vue unter `/api/app/`), die Ordner unter
  `/_upload/`, `/` leitet relativ auf `api/app/` weiter (hält auch hinter nginx unter `/aifred/`). Der Server betritt
  `SERVER_LIFESPANS` der Reihe nach (Sitzungsereignisse, `process_tasks_lifespan` mit `start_process_tasks` beim
  Start statt beim ersten Seitenaufruf, Message Hub, Dienststatus); Host und Port `AIFRED_SERVER_HOST/PORT`
  (Vorgabe 0.0.0.0:8002, Maschinenspezifisches in `.env`). Ein Prozess unter uvicorn; stirbt er, startet systemd ihn
  neu (`Restart=always`), der Granian-Respawn-Patch entfällt mit Reflex. Die Testinstanz nimmt `create_app` mit nur
  dem Ereignis-Lifespan. `aifred/__init__.py` lädt die Reflex-App nicht mehr (Reflex lädt `aifred.aifred` selbst).
  Was bisher der Reflex-Browser erledigte: `POST /api/chat/inject` gab die Nachricht nur in die Warteschlange der
  Sitzung, die ein offener Reflex-Tab alle 500 ms abholte; ohne Tab blieb sie liegen. Jetzt geht sie an
  `turn_service.submit_turn` wie eine Chat-Nachricht (sofort oder hinter dem laufenden Zug), eine unbekannte Sitzung
  gibt 404 statt einer neu angelegten leeren Datei (`sessions.stored_session`, auch für `owned_session`).
  `GET /api/chat/status` las `is_generating` aus der Sitzungsdatei, das nur der Reflex-State schrieb, und meldete ohne
  ihn immer „fertig“; jetzt `pipeline_registry.is_pipeline_running`. Live geprüft auf der Testinstanz ohne offenen Tab:
  zwei Nachrichten per Inject, die erste startete, die zweite wartete (`queued: true`) und lief danach, beide
  beantwortet. Suite 239 Dateien grün.
- **Temperatur nur noch von Hand (erledigt 07.11.2026, Peuqui):** Jeder Agent antwortet mit seiner
  eigenen Temperatur (`agent_settings.resolve_agent_temperature`); der Modus auto, die Temperaturen je
  Absicht (`INTENT_TEMPERATURE_*`) und der Aufschlag für Sokrates und Salomo (`temperature_offset`) sind
  weg, ebenso der Schalter in der Reflex-Sampling-Tabelle und `temperature_mode` in `/api/settings`.
  Die Absichtserkennung bleibt ein Aufruf des Automatik-Modells, aber ohne Kategorie: Sprache,
  Anrede, Moduswechsel, „nur Befehl“. Die Antwort ist jetzt ein JSON-Objekt mit benannten Feldern,
  feldweise gelesen (`intent_detector.parse_intent_response`); am echten Automatik-Modell (Qwen3VL-30B-A3B,
  je Satz fünf Durchläufe) 55/60 statt 28/60 richtig, bei sechs frischen Sätzen 26/30 statt 15/30
  (Skript `~/.cache/aifred-e2e/vue/intent_live_check.py`). Ein Pipe-Format ohne Namen lieferte 40/60
  und ordnete beim Tribunal mit Thema stets Sokrates als Adressaten zu. Der Message Hub antwortete im Modus auto bisher
  immer mit 0,2, weil er keine Absicht kannte. Alte Schlüssel in `settings.json` liest niemand mehr;
  Reflex schreibt sie beim nächsten Speichern nicht mehr mit.
- **Bilder drehen und zuschneiden (erledigt 07.11.2026):** ✂️ am Vorschaubild öffnet `ImageEditor.vue`
  (Rahmen mit Ecken und Kanten, Mindestseite 10 %, Rechnung in `lib/cropBox.ts`). Der Server
  (`turn_images.rotate_upload`, `crop_upload`) legt das Bild als neue Datei unter neuem Namen ab und
  löscht die alte, damit kein Browser die alte Fassung aus dem Cache zeigt; der Name wächst nicht
  (`vision_utils.upload_stem`). Ein Rahmen über das ganze Bild (0,5 % Toleranz) schneidet nichts. Der
  Reflex-State nutzt dieselben Funktionen (vorher eigene Kopie: Drehen überschrieb die Datei mit
  `?t=` an der URL, ein zugeschnittenes PNG behielt seine Endung über JPEG-Inhalt).
- **Browser-Erkennung** (Peuqui 07.11.2026): Im Diktat-Menü neben dem Mikrofon wählt man die
  Spracherkennung, STT-Suite (Standard) oder Browser (Web Speech API; Chrome und Edge schicken die
  Sprache an Google bzw. Microsoft, Firefox kann es nicht), dazu „erst ins Eingabefeld“.
  `dictation_engine` und `show_transcription` in settings.json (`GET/PATCH /api/settings`,
  schlüsselweise über `settings.update_settings`). Kein Ausweichen von selbst, nur für Diktate. Was
  der Browser hört, geht an `POST /api/sessions/{id}/dictation/text`; dieselbe Regel wie beim Diktat
  des Servers (`dictation.hand_on`) schickt es an die KI oder ins Eingabefeld.
- **Diktat, Rest:** Die Schätzung der Dauer nimmt immer die GPU an, auch wenn die Aufnahme auf die CPU
  geht (Bestandsaufnahme). Die Gerätewahl in AIfred und die Rückfrage „LLM entladen?“ sind entfallen
  (Peuqui 07.11.2026, Archiv-Tag `archive-vue-frontend-2026-11-07`): AIfred fragt immer mit `auto`.
- **Sprachausgabe, Rest:** Musik oder Hörbuch vor einer Lesung anhalten (kommt mit der Audio-Seite).
- **Blase neu vorlesen (erledigt 07.11.2026):** 🔄 an jeder Antwort, solange die Sprachausgabe an ist
  (`enable_tts` aus `GET /api/settings`). `reply_speech.read_bubble` beendet, was gerade spielt, liest den Text
  der Blase durch den Filter der Lesung (`tts_text_filter.strip_ui_only_content`, ohne Metrikzeile)
  über die Wiedergabeliste der Sitzung und ersetzt danach den Ton der Blase (`message_updated`). Bricht
  der Dienst nach dem ersten Ton ab, behält die Blase ihren alten Ton (`keep_partial`); eine neue
  Antwort behält auch eine abgebrochene Lesung, weil sie sonst keinen hätte. Welcher Text einer Blase
  gelesen wird, sagt `reply_speech.bubble_reading_text` (auch für den Reflex-State).
- **Alle neu vertonen (erledigt 07.11.2026):** „Alles neu generieren“ in der Kopfleiste des Chats
  (bei Reflex im Audio-Player-Kasten, der mit der Audio-Seite kommt). `reply_speech.regenerate_all`
  liest jede Antwort ohne Wiedergabe in Dateien (`speak_to_files`), jede Blase bekommt ihren Ton,
  sobald er fertig ist (`message_updated`), was der Browser vorher gesprochen hat, entfällt. Einer je
  Sitzung (`speech_regenerating`, auch in den Sitzungsdetails für neu geöffnete Tabs); ein Fehler des
  Dienstes beendet den Lauf mit Hinweis, Fertiges bleibt; Löschen der Sitzung bricht ihn ab.

- **Rollentausch vollständig (erledigt 07.11.2026):** Die Perspektive in
  `message_builder.build_messages_from_llm_history` gilt für jeden Agenten: eigene Antworten sind
  `assistant` ohne Marke, alle anderen `user` unter dem aktuellen Anzeigenamen; der Richter
  (`observer=True`) sieht alles als fremd und behält seine eigene Stil-Erinnerung. Alle Wege
  schreiben die Kennung als Marke (`[AIFRED]:`, `formatting.assistant_history_entry`, auch die
  Kanäle), gelesen klein als Kennung (liest auch die Anzeigenamen-Marken alter Kanal-Einträge).
  Die Prompts der Plätze (`shared/multi_agent_roles`, `debate/critic`, `tribunal`, `refinement`,
  `defense`, `mediator`, `judge`) nennen keinen Agenten: `{main_agent}`, `{critic_agent}`,
  `{judge_agent}` aus `debate_seats.seat_names`, für Server- und Reflex-Zug.
- **Agenten-Editor (erledigt 07.11.2026):** Ein neuer Agent bekommt seine Grundprompts aus
  `prompts/<lang>/agent-template/` (Name eingesetzt, auch `memory_context.txt`: ohne sie sah ein neuer
  Agent seine Erinnerungen nie). Die Aufgaben der Plätze braucht er nicht als Kopie, sie gehören dem
  Platz. Die Texte standen vorher im Code (`_get_role_templates`, mit Ersatz-Umlauten). Die Kennung
  ist der Name des Prompt-Ordners: nur `[a-z0-9_]` (vorher legte `../x` Dateien außerhalb an).
- **Agenten-Seite, Teil 1 (erledigt 07.11.2026):** `AgentsView.vue`, je Agent eine Karte mit Schalter
  oben rechts, der Zeile der Schnellwahl (`AgentChoicesRow.vue`, nur für eingeschaltete) und Löschen.
  Eine Liste für jede Wahl: `agent_config.selectable_agents()` (keine Systemagenten, keine
  abgeschalteten) für Schnellwahl, Agentenliste des Chats, Plätze, Symposion, Namensauflösung,
  Absichtserkennung (die bisher auch den Kalibrierungsagenten als ansprechbar anbot) und Subagenten.
  Abgeschaltet lehnt der Zug laut ab, wenn der Agent antworten oder einen Platz einnehmen soll
  (`turn_debate._speaker`, nie ein anderer an seiner Stelle), aus dem Symposion fällt er heraus (auch
  mitten im Gespräch); Message Hub (Weckwort, Auslöser, Sitzungsagent) und Scheduler (auch fester Text,
  er spricht im Namen des Agenten) melden einen Fehler. AIfred (`agent_config.BASE_AGENT`) und die
  Systemagenten bleiben an; gelöscht wird nach `deletion_refusal`: AIfred nie, Systemagenten nie,
  **Sokrates und Salomo erst mit Schritt 6**, weil die Reflex-Kopie an rund 100 Stellen fest auf ihre
  Tuning-Eimer greift. Vision stand in den eingebauten Standards (frische Installation) als Chat-Agent
  statt als Systemagent; korrigiert.
- **Agenten-Seite, Teil 2 (erledigt 07.11.2026):** ✏️ an jeder Karte öffnet `AgentEditor.vue`: 🎭 Persona
  (Name, Emoji, Beschreibung, weitere Namen für die Spracherkennung), 📝 Prompt-Ebenen je Sprache (mit
  Hinweis auf ungespeicherte Änderungen und auf eine Sprache ohne Datei), 🔐 Rechte nach Plugin-Gruppen
  (`plugin_registry.tool_groups_on_offer`). „Alle Werkzeuge“ speichert keine Liste (auch später
  eingeschaltete Plugins); eine Liste behält, was sie für Werkzeuge abgeschalteter Plugins hielt (Reflex
  verlor sie beim Speichern). ＋ legt einen Agenten aus der Vorlage an, der Editor öffnet sich. Der
  Pfadschutz für Prompt-Dateien liegt jetzt an einer Stelle (`prompt_loader.prompt_path`, auch für die
  Reflex-Kopie). Bleibt für andere Seiten: geplante Aufträge je Agent
  (Aufträge), Gedächtnis ansehen und löschen, Automatik-Prompt, Cloud-Modell der Systemagenten.
- **Gedächtnis-Seite (erledigt 07.11.2026):** `MemoryView.vue` (🧠 in der Kopfleiste): links jeder Agent mit
  der Zahl seiner Erinnerungen (auch Sammlungen gelöschter Agenten), rechts seine Einträge mit Typ-Filter,
  bearbeiten (über `AgentMemory.update` wie das Werkzeug der Agenten), einzeln oder alle löschen.
  `AgentMemory.counts()` und `entries()` sind jetzt öffentlich; ein Agent ohne Sammlung wird nicht gelesen
  (das legte eine an). Die Index-Pflege (verwaiste Einträge) liegt auf der Dokumente-Seite (siehe dort).
- **Agenten-Bündel (erledigt 07.11.2026):** 📦 und 📥 in der Kopfzeile der Agenten-Seite (`AgentBundles.vue`):
  Export der gewählten Agenten als ZIP, Import mit Vorschau des Bündels, Auswahl und Konfliktregel
  (umbenennen, überschreiben, abbrechen), danach die Warnungen. Die Antworten von Vorschau und Import sind
  jetzt typisiert; `exported_at` ist eine Unix-Zeit (als Text typisiert gab die Vorschau 500).
- **Aufträge-Seite (erledigt 07.11.2026):** `JobsView.vue` (⏰ in der Kopfleiste) mit `JobForm.vue`: Liste mit
  Schalter, Agent, Zeitplan, nächstem und letztem Lauf; anlegen, ändern, letzte Läufe, löschen. Der Zeitplan
  entsteht aus Feldern (Cron-Vorlage oder fünf Felder, Intervall mit Einheit, Datum und Uhrzeit;
  `lib/schedule.ts` mit Tests), der Server nennt vor dem Speichern den nächsten Lauf. Die Regeln (Typen,
  Zustellwege, fester Text nur mit „Senden“ und Kanal, Ton-Art) stehen jetzt an einer Stelle,
  `scheduler.job_problem`, für das Werkzeug der Agenten, die Seite und die Cron-Vorlagen der Reflex-Kopie.
  `JobStore.add` lehnt einen Ausdruck ohne nächsten Lauf ab wie `update` (das Werkzeug legte an und löschte
  wieder), ein einmaliger Auftrag braucht einen gültigen Zeitpunkt (vorher wurde jeder Text gespeichert).
  Kanäle kommen aus dem Plugin-Register statt einer festen Liste; stille Ersatzwerte des Reflex-Formulars
  (Intervall 60 s, Datum 2026-01-01) gibt es nicht mehr.
- **Sprach-Seite (erledigt 07.11.2026):** `SpeechView.vue` (seit 08.11. `AudioView.vue`, 🔊 in der Kopfleiste): Sprachausgabe und
  Autoplay (vorher auf der Einstellungsseite), AIfreds Liste der Engines (verschieben, schalten, Einheit je
  Engine, Status frisch lesen; ohne TTS-Suite aus den Einstellungen mit Grund), die Stimmen aller
  Chat-Agenten auf einer gewählten Engine (Schalter, Sprache, Stimme, Tempo, Tonhöhe) mit dem Vorleser in
  derselben Tabelle, ob die Spracherkennung antwortet (Gerät, Engine und Modell stellt die STT-Suite selbst
  ein). Die Regeln stehen jetzt in `lib/tts_settings.py`, auch für den Reflex-State (Zeilen, Änderung der
  Liste als eine Transaktion, Stimmenauswahl, Stimme laden und speichern, Vorleser-Stimme); geschrieben wird
  schlüsselweise (`update_settings`), nicht mehr die ganze Datei aus dem Speicher. Tempo- und
  Tonhöhenstufen kommen aus `config.py` (vorher fest in der Reflex-Oberfläche); Rendering der Reflex-Seiten
  vorher und nachher byteweise gleich. Probehören fehlt noch (braucht die TTS-Suite auf einer Karte).
- **Vision, Paket 1: Live (erledigt 07.11.2026):** Plan mit Peuqui: eine Seite 👁️ mit Reitern (Live, Ereignisse,
  Personen, Einstellungen; jeder mit eigener Adresse `#/vision/<reiter>`, die Live-Ansicht öffnet so auch in einem eigenen
  Fenster), die Kamera als zentrale Karte, Querverweise Ereignis↔Person, gleiche Bausteine für gleiche Dinge, ein
  Abzeichen im Chat statt der Feed-Karte; dazu die Fehler aus `inventar-vision-medien.md` 7.x. Gebaut: `lib/vision_cameras.py`
  (EINE Kamera-Liste statt zwei im State, Name über `VisionStore.source_label`; Auflösungen auf Anfrage; Einstellungen der
  Ansicht mit Bereichen aus `config.py`, ein falscher Wert in der Datei ist ein Fehler statt eines stillen Standards),
  `api/cameras.py`, `VisionView.vue` + `VisionLive.vue` (Kameras ein/aus, Bildrate oder Einzelbild, Vergrößern per Klick,
  Auflösung, Briefing, PTZ gehalten mit Lebenszeichen aus `config.py` und Wächterposition, Foto) und `ImageLightbox.vue`
  (eine Bildanzeige für Fotos, später Ereignisse und Gesichter). Gegen die echten Kameras geprüft (Reolink mit PTZ, Reolink
  Zoom): Bilder, Einzelbild, Briefing, Foto 3840 × 2160; der Kopf fährt (mittlere Bildabweichung 36,7 nach 2 s links,
  6,5 zurück in der Wächterposition). Die Testinstanz bekommt die Kameras über eine Kopie der Vision-Einstellungen mit
  unscharfem Auge und die Zugangsdaten über `AIFRED_TEST_ENV_KEYS`. Die Live-Analyse folgte als eigenes Paket (unten).
- **Vision, Live-Analyse (erledigt 08.11.2026):** Je Kamera in der Live-Ansicht 🧠 (das Bildmodell
  beschreibt das Bild) und 👤 (Gesichter, mit der Einstellung des Auges auch Personen); die Beschreibungen über oder unter
  dem Bild, erkannte Gesichter mit Ausschnitt, Name und Ähnlichkeit, „+ taggen“ führt zu den Personen, „Verwerfen“
  nimmt die Zeile weg; Vision-Takt, ID-Takt und Teleprompter in der Leiste, solange eine Analyse läuft (Listen vom
  Server: `VISION_PREVIEW_VLM_COOLDOWN_CHOICES`, `_FACE_THROTTLE_CHOICES`). Die Analyse ist ein Zustand des Servers
  (`lib/vision_live_analysis.py`, `PUT /vision/live/analysis/{id}`, Zustand in `GET /vision/live`), jede Seite sieht
  denselben; sie läuft, solange eine Seite ihren Strom verfolgt (`/vision/events/{id}`, 5 s nach dem letzten endet sie,
  ebenso eine, der nie jemand folgt: `vision_event_bus.expect_viewer`). Die App hält Seiten am Leben (`KeepAlive`): Auf
  einer anderen Seite ruht die Live-Ansicht, ohne Kamerabilder und Ströme (vorher liefen die Bilder weiter).
  Die Config (Takt der Live-Ansicht, Mindest-Bildrate `VISION_LIVE_ANALYSIS_MIN_FPS`, Gesichts- und Personen-Einstellung
  des Auges, Schwelle der Kamera über `vision_autostart.motion_threshold`, Profil) steht einmal, auch die Reflex-Vorschau
  nutzt sie. Behoben: Beide Schalter aus stoppte den Wächter ganz, auch eine scharfe Kamera verlor ihre
  Hintergrundüberwachung; nach dem letzten Zuschauer entschied eine zweite Regel (`restore_or_stop_after_preview`,
  fragte `vision_mode` nicht). Jetzt gibt das Ende die Kamera an `apply_background_watch` zurück, der einen Stelle;
  solange eine Analyse läuft, lassen Auge (scharf/unscharf) und Zeitfenster-Wächter die Kamera in Ruhe. Neu: Ein
  geänderter Takt wirkt sofort auf laufende Analysen (Reflex: erst beim nächsten Start). Logik des Teleprompters in
  `frontend/src/lib/liveFeed.ts` (mit Tests). Geprüft mit der Zoom-Kamera der Testinstanz: Gesichter an (der Wächter lud
  das Gesichtsmodell auf der CPU), Takt geändert, aus, Seitenwechsel und geschlossener Tab beenden die Analyse, die
  Ansicht danach wie vorher. Mit vllm-research abgestimmt (06:27) auch mit echtem Modell: Qwen3VL-4B-visiond auf
  Karte 4 (GPU-Orc), erste Beschreibung nach 13 s, über und unter dem Bild. Dabei gefunden (bestehend, Reflex und Vue):
  Der Power-Knopf des Bildmodells (`vision_prewarm.is_vlm_loaded`/`unload_vlm_model`) fragte nur Ollama; lief der
  Beschreiber über ein llama-swap-Profil (`-visiond`), meldete er „nicht geladen“ und entlud nicht. Behoben 08.11.2026:
  Ein Modell mit `-visiond`-Profil gilt als geladen, wenn eine seiner Platzierungen (Heimat oder `-visiond-gpu<N>`) bei
  llama-swap geladen ist (`vision_routing.loaded_describer_placements`), Entladen stoppt jede davon; eine Ablehnung
  von llama-swap kommt als 409 mit Grund. Live an der Testinstanz: Laden 8,5 s (Karte 4, 7.202 MiB), Status
  „geladen“, Entladen 0,5 s, Karte leer.
- **Vision, Paket 3: Einstellungen (erledigt 07.11.2026):** `VisionSettings.vue` (Reiter ⚙️): das Auge (`lib/vigilantia.py`;
  scharf/unscharf startet und stoppt die Hintergrund-Wächter), Modus und Bildmodell samt Laden/Entladen, Gesichts- und
  Personenerkennung, Aufbewahrung; die Benachrichtigungs-Regeln als Tabelle Kategorie × Kanal mit Bild-Text
  (`alert_bus.vision_rules`/`change_vision_rule`, auch für den Reflex-State); je Kamera eine Karte mit Name, Hintergrund,
  Benachrichtigungen samt Typen, Ruhezeit, Aktiv-Zeitfenster, Ereignisabstand, Zonen-Editor und 🔌 Verbindung
  (`RtspForm.vue`, ＋ Kamera legt an). Ob der Hintergrund-Wächter einer Kamera jetzt laufen soll, entscheidet EINE Stelle
  (`vision_autostart.apply_background_watch`, auch für den Zeitfenster-Wächter). RTSP-Kameras über `lib/rtsp_cameras.py`
  (auch Reflex): Eine Änderung führt die Felder in den bestehenden Eintrag, statt ihn neu zu bauen — das alte Formular verlor
  dabei `ptz`, `onvif_port` und `snap_channel` der echten Reolink; Zugangsdaten über `credential_broker.persist/forget`, beim
  Entfernen fallen sie mit weg, wenn keine andere Kamera sie nutzt. Damit erledigt aus `inventar-vision-medien.md`: 7.3
  Kategorien und Regel-Datei doppelt; 7.4 Anlegen überschreibt eine Kamera gleichen Namens (jetzt 422), `cred` ungeprüft
  im `.env`-Schlüssel (jetzt nur `[a-z0-9_]`), verwaiste Zugangsdaten; der Datensatz in vision.db bleibt bewusst (die
  Ereignisse der Kamera). Reflex-Seiten vorher und nachher byteweise gleich. Geprüft gegen die Testinstanz mit Kopie der echten
  Kameras: Regel an/aus, Verbindungsformular der Reolink (Profil, PTZ, Passwortfeld leer), Prüfkamera angelegt und
  entfernt, Regeln und Kameras danach wie vorher. Die Beschreiber-Liste (`DescriberList.vue`, unter dem Bildmodell;
  gleiches Muster wie die Engines der Sprach-Seite): Zeilen, Verschieben und Schalten in `lib/describer_list.py` (auch
  für Reflex, vorher im State); das Vermessen eines Beschreibers auf einem anderen Rechner ist ein Lauf des Servers,
  einer zur Zeit für alle Oberflächen (vorher je Browser-Sitzung, zwei Tabs konnten zugleich auf demselben Rechner
  entladen und laden), die Seite folgt ihm wie der Indexierung. Geprüft: Zeilen, Verschieben und Schalten im Browser
  (Aragon war aus, seine Zeile mit Hinweis und Knopf über einen Eintrag der Test-Kopie), Vermessen nur im Test mit
  Stellvertreter (es entlädt auf dem Rechner alle Modelle).
- **Vision, Ereignisse (Casus) ohne Bildmodell (erledigt 08.11.2026):** `VisionEvents.vue` (Reiter 🗂️,
  `#/vision/events`): Liste neueste zuerst mit Filtern Quelle, Typ (jetzt mit Person, Fahrzeug, Tier), Person (alle,
  unzugeordnet, eine bestimmte), „Gruppiert“ (eine Zeile je Vorkommnis mit „+N“), Seiten; je Zeile Gesichts-Ausschnitt
  oder Weitwinkel und daneben das Tele, Typ, Quelle, zugeordnete oder vermutete Person, Beschreibung (aufklappbar),
  Person zuordnen oder lösen (nur Gesichts-Ereignisse), Film des Vorkommnisses, Löschen; „Alle löschen“ nach Filter
  mit Rückfrage und Anzahl. Die Diaschau blättert über alle Seiten, ohne die ID-Liste zum Browser zu schicken: Die
  Bildanzeige (`ImageLightbox.vue`, auch für die Fotos der Live-Ansicht) fragt je Position nach ihren Bildern, Weitwinkel
  und Tele nebeneinander; → ist neuer, sie hält an beiden Enden. Neue Ereignisse kommen über einen Strom
  (`VisionStore.add_event` gibt ein Signal, das auch aus den Threads der Wächter in die Ereignisschleife reicht); die Liste
  lädt neu, außer während ein Bild oder eine Zuordnung offen ist. `lib/casus.py` hält Typfilter, Filter, Seite, Auswahl,
  Film, Zuordnen und Löschen, auch für den Reflex-Casus. In `VisionStore` steht der Filter an EINER Stelle
  (`_event_filter`): Vorher zählte „Nur unzugeordnete“ die unsicheren Gesichter in der Liste mit, in Anzahl, Diaschau
  und Löschen nicht. Gruppiert wird in SQL (Fensterfunktion, gezählt per `COUNT(DISTINCT …)`), vorher in Python über
  höchstens 5000 Ereignisse, der Rest fiel still weg; die ID-Liste schnitt bei 10.000 ab. Gleiche Zeitstempel sortiert
  die ID, damit Seiten sich weder überschneiden noch etwas auslassen. Geprüft gegen die Testinstanz mit einer Kopie der
  2.325 echten Ereignisse und 4 Prüf-Ereignissen: Filter (Person 1.339), gruppiert 99 Vorkommnisse, Film 107 Bilder
  wie „+106“ angibt, Diaschau über alle Seiten mit Tele, Zuordnen und Lösen, eine Zeile und „Alle löschen“ nach Filter
  (nur die Prüf-Ereignisse; Bilder außerhalb des Test-Datenordners rührt das Löschen nicht an), danach 2.325 wie vorher.
  **Mit Bildmodell (08.11.2026):** ✨ je Zeile (ein Ereignis) und „Alle analysieren“ (jedes Ereignis ohne Beschreibung,
  einmal je Vorkommnis, nach der Platzprüfung) mit Fortschritt und Abbrechen. Beide sind Läufe des Servers in
  `lib/casus.py`, je einer zur Zeit für alle Oberflächen (vorher je Browser-Sitzung), die Seite folgt ihnen über
  `GET /vision/casus/runs`; auch der Reflex-Casus nutzt sie. `vision_bulk` meldet seinen Fortschritt als Zahlen
  (Ereignisse, Cluster, erledigt), den Text baut `DescribeAll.message` über i18n (vorher fest deutsche Sätze im Code).
  Live geprüft mit Qwen3VL-4B (Karte nach Absprache mit vllm-research): Einzelanalyse in 22 s mit Laden (16 s beim
  zweiten Mal), neue Beschreibung in der Zeile, die anderen ✨ währenddessen gesperrt; „Alle analysieren“ über eine
  Gruppe von 11 Ereignissen ohne Beschreibung: Platzprüfung, „Analysiere 0 / 1 Cluster (aus 11 Ereignissen)“,
  „Fertig — 1 Cluster analysiert“, alle 11 wieder beschrieben. Die Platzprüfung lehnt ein nicht vermessenes Modell ab;
  die Testinstanz brauchte dafür eine Kopie von `data/vlm_vram_cache.json`.
- **Vision, Personen (Personarium) (erledigt 08.11.2026):** `VisionPersons.vue` (Reiter 👤, `#/vision/persons`): jede
  Person mit Bild, Zahl der Embeddings und letzter Sichtung; umbenennen, löschen (mit Rückfrage), ihre Embeddings mit
  Ausschnitt und Qualität, einzeln löschen, 🗂️ öffnet ihre Ereignisse (`#/vision/events?face=<id>`). Darunter die
  offenen Gesichter, eine Karte je Sichtung (gleiche Crop-Datei), getrennt in unsicher Erkannte (mit Vermutung) und
  Unbekannte: einer bestehenden oder neuen Person zuordnen (das Gesichtsmodell lernt sie aus dem Bild, auf der CPU),
  verwerfen (einzeln, ausgewählt, alle mit Rückfrage), alle neu erkennen (Lauf des Servers, einer zur Zeit). Die Regeln
  stehen in `lib/personarium.py`, auch für den Reflex-Personarium und `/api/vision/face/*` (die Endpunkte gab es,
  benutzt hat sie niemand): Umbenennen über die API lud die Erkennung bisher nicht neu, die offenen Gesichter hatten
  eine Obergrenze von 2000 trotz „ohne Deckel“, die Meldungen standen fest auf Deutsch im State. „Neu erkennen“ prüfte
  eine andere Menge als die Karten (nur Gesichter ohne Person, also nie die unsicher erkannten; auch welche ohne
  Ausschnitt; höchstens 2000), seine Bilanz passte nicht zur Seite; jetzt prüft es genau die Ereignisse hinter den
  offenen Karten (`face_enroll.rematch_untagged_faces` über `personarium.open_faces`). Geprüft gegen die Testinstanz mit
  einer Kopie der Personen und Embeddings: Umbenennen hin und zurück, 8 von 8 Embeddings, 🗂️ führt zu den 24
  Ereignissen der Person, eine Prüfperson in 6 s gelernt (CPU) und gelöscht, eine Karte verworfen, Neu erkennen über
  27 Karten mit 50 Ereignissen („0 von 50“, die Produktion hatte sie schon geprüft).
  **Posen hinzufügen (08.11.2026):** `EnrollPoses.vue` (📸 je Person, „Neue Person mit Posen“): Live-Bild der Kamera
  (Tele, wo vorhanden; Takt `VISION_ENROLL_PREVIEW_MS`), Anweisung je Pose, aufnehmen, zurücknehmen, überspringen,
  speichern. Die Sitzung lebt im Server (`lib/enrollment.py`, eine zur Zeit; auch die Reflex-Kopie): Ausschnitt und
  Embedding bleiben dort, die Seite bekommt Pose, Ausschnitt-URL und Score (vorher lagen Embeddings und Bilder als
  Base64 im öffentlichen State). Die Ausschnitte rahmt `face_crop_store.crop_face_from_jpeg` wie alle anderen (vorher
  eigener Rand von 30 %). Entfernt: das tote `POST /vision/face/enroll` und `embedding_b64` im Live-Ereignis (rund
  2 KB je Gesicht, nirgends gelesen). Geprüft mit der echten Reolink (4 Live-Bilder in 5 s; nachts kein Gesicht, die
  Meldung kommt; Abbrechen beendet die Sitzung, nichts gespeichert), Aufnehmen und Speichern im Test mit Stellvertretern.
- **Vision im Chat: Abzeichen statt Feed-Karte (erledigt 08.11.2026):** `VisionBadge.vue` in der Werkzeugleiste des
  Chats: Auge (scharf/unscharf) und Zahl der Ereignisse der letzten `VISION_BADGE_WINDOW_MIN` Minuten, ein Klick zeigt die
  neuesten `VISION_BADGE_EVENTS`, jedes führt in den Reiter Ereignisse gefiltert auf seine Kamera
  (`#/vision/events?source=<id>`). Neue Ereignisse über den Strom der Ereignisse, dazu alle `VISION_BADGE_REFRESH_SEC`
  (ältere fallen aus dem Fenster); ohne Vision-Plugin bleibt es weg. Die Abfrage steht in `casus.recent`, auch für die
  Reflex-Feed-Karte (dort stand die Typenliste zweimal, die Werte fest im Mixin). Geprüft im Browser mit einem frischen
  Prüf-Ereignis.
- **Vision, Zonen-Editor (erledigt 08.11.2026):** `ZoneEditor.vue` öffnet sich als Dialog aus der Kamerakarte (🗺️)
  statt des eigenen HTML-Fensters: Live-Bild mit eingebrannter Bewegung, Malen mit drei Arten (Bewegung ignorieren,
  schwärzen, nur hier beobachten; rechte Maustaste radiert), Pinsel, Raster, „Maske aktiv“ (speichert sofort), der
  gemessene Bewegungsanteil gegen die gemalte Maske neben der Auslöse-Schwelle, Hilfe. Die reine Logik (Zellen ↔
  Ziffern, runder Pinsel, wohin die Überwachung schaut, blaue Pixel zählen) steht in `lib/zoneMask.ts` mit Tests. Neu:
  Schließen mit ungespeicherten Änderungen fragt nach (das alte Fenster verwarf sie still). Die Schwelle ist eine
  Einstellung der Kamera (`change_camera(motion_min_area_ratio=…)`, Bereich `VISION_MOTION_MIN_AREA_RANGE`, ein laufender
  Wächter übernimmt sie sofort; vorher stand die Grenze dreimal von Hand im Code); die Kamerazeile sagt
  `detects_itself` (Profil `ai_camera`, über `vision_profiles.camera_profile`, vorher zweimal von Hand), dann erscheint
  der Hinweis, dass Ignorieren- und Nur-hier-Zonen dort nicht wirken. Zonenfarben in `theme.py`. Das HTML-Fenster
  (`assets/zone_editor.html`, `GET /vision/zone-editor`, `/vision/motion-min`) bleibt für die Reflex-Oberfläche bis
  Schritt 6; seine Schwelle läuft über dieselbe Kamera-Änderung. Dabei behoben: Die erste Einstellung an einer Kamera,
  die vision.db noch nicht kannte, legte sie mit ihrer Kennung als Namen und Art „webcam“ an (`change_camera` gab
  Name und Art nicht mit wie die Reflex-Seite), eine unbekannte Kennung bekam einen leeren Datensatz; jetzt 404 und
  der echte Name. Geprüft im Browser gegen die echten Kameras: Malen, Nachfrage beim Schließen, Speichern, Schalter,
  Schwelle, Leeren, Hinweis bei der Reolink; Einstellungsdatei und Kameras danach wie vorher. Den gemessenen Anteil
  nur mit ruhigem Nachtbild gesehen (0,0 %); die Rechnung ist per Test geprüft und vom alten Editor übernommen.
- **Musik im Browser (erledigt 08.11.2026):** `lib/media.ts` spielt, was der Browser-Kanal des Audio-Players schickt
  (`media`, `pause`, `resume`, `seek`, `speed`, `stop` über den Sitzungsstrom; vorher kamen sie in Vue nicht an): einen
  Titel oder einen Ordner Titel für Titel, die Sprachausgabe hat Vortritt (der Titel wartet und geht danach
  `pre_roll_sec` früher weiter), die Position geht an `/api/audio/position` (alle `position_save_sec`, bei Pause/Stopp,
  „fertig“ am Ende). Die weiteren Titel eines Ordners lagen bisher nur im Reflex-State; über Vue spielte ein Ordner
  nur seinen ersten Titel, jetzt reisen sie mit dem Ereignis. Geprüft mit Tests (Python: Ereignis mit Warteschlange;
  vitest: Ordner, Vortritt der Sprachausgabe, Pause/Fortsetzen/Stopp). Offen: ein Durchlauf mit dem Werkzeug eines
  Agenten (braucht ein Sprachmodell) und die Seite der Audio-Quellen.
- **Audio-Seite mit Quellen (erledigt 08.11.2026):** Aus der Sprach-Seite wird „🔊 Audio“ (`AudioView.vue`, `#/audio`),
  darunter `AudioSources.vue`: jede Quelle mit Indexzahl, ein Ordner der Sandbox als neue Quelle (schlanker
  Ordner-Auswähler über `file_browser`), einlesen (Lauf des Servers, einer zur Zeit, die Seite folgt ihm), Index
  leeren, entfernen (mit Rückfrage; der Ordner, auf den sie zeigt, bleibt), Sandbox und Schwelle der Listen-Kürzung.
  Die Regeln stehen in `lib/audio_library.py`, auch für die Reflex-Seite `/audio-settings`: Sie las und schrieb die
  Einstellungsdatei an drei eigenen Stellen (jetzt `audio_player_settings.save_audio_player_settings`), ihre Meldungen
  standen fest deutsch und mit Ersatzumlauten im State, der Standard 5 der Listen-Kürzung stand zweimal fest im Code
  (jetzt `AUDIO_TTS_LIST_MAX_DEFAULT`, ein kaputter Wert ist ein Fehler statt eines stillen Ersatzes). Entfernen und
  Einlesen nur für Namen, die die Quellenliste kennt; eine neue Quelle muss auch aufgelöst in der Sandbox liegen.
  Geprüft im Browser mit einer Prüf-Sandbox aus drei WAV-Dateien (anlegen, 3 eingelesen, leeren, entfernen, Ordner
  bleibt). Offen: im Ordner-Auswähler Ordner und Symlinks anlegen oder löschen (konnte der Reflex-Picker; gehört zum
  geplanten gemeinsamen Dateimanager).
- **Bleibt so:** Wer AIfred in einem Diskussionsmodus beim Namen anspricht, bekommt nur seine Antwort.

## Schritte

0. Reflex 0.10.1 (Zweig `reflex-0.10`), damit der Bestand bis zum Wechsel gepflegt läuft.
1. Bestandsaufnahme der 29 State-Mixins (`inventar-*.md`).
2. Zug-Ablauf aus dem State in einen Server-Dienst lösen; der Reflex-State wird dünner Client.
3. Ereignisstrom und Chat-API.
4. Erste Vue-Seite (Chat) neben Reflex unter `/api/app/`.
5. Seite für Seite: Einstellungen, Dokumente, Agenten, Vision, Audio.
6. Reflex entfernen: Mixins, `aifred/ui/`, `scripts/patch-reflex.py`, Reflex-Patches in `CLAUDE.md`;
   Server startet direkt (Granian/Uvicorn), nginx `/aifred/` auf den Server, Einzeilen-Installer.
   Daten der Produktion dabei: `role` in `data/agents.json` von `main`/`critic`/`judge`/`custom` auf
   `agent` (Stand 07.11.2026 noch die alten Etiketten; alles außer `system` gilt schon als Chat-Agent),
   Sokrates und Salomo löschbar machen (`deletion_refusal`, `CANONICAL_AGENTS`).
