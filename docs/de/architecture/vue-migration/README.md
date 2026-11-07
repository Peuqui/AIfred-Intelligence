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
  Umsetzung in zwei Teilen, ohne denselben Code zweimal anzufassen: In Schritt 2 kennt die
  Server-Schnittstelle der Züge nur Rollen (Hauptagent, Kritiker, Richter) und eine Zuordnung
  Rolle → Agent, standardmäßig wie heute belegt; das Verhalten bleibt gleich. Abschalten,
  Löschen und die Zuordnung in der Oberfläche kommen mit der Agenten-Seite in Schritt 5.
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
  Sampling und Denken lesen (`agent_tuning`, `backend_type`, `backend_url`, `temperature_mode`,
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
- **`lib/turn_debate.py`**: Kritische Prüfung, Auto-Konsens, Tribunal, Symposion über die Rollen
  main, critic, judge aus `agents.json` (`agent_config.agent_for_role`, `role_prompt`); fehlt eine
  Rolle oder ihr Prompt, lehnt der Zug mit Grund ab.
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
- Danach übernehmen Kanäle und Scheduler denselben Dienst.

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
- **Einstellungen, Teil 2 (offen):** Backend-Wechsel, Kalibrierung, Neustarts von Backend und AIfred,
  Grundeinstellungen laden, Temperaturmodus. Neustarts treffen die gemeinsamen Dienste (llama-swap,
  Produktionsdienst): nur mit Peuqui abgestimmt testen.
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
  Die Rollen-Prompts (`shared/multi_agent_roles`, `critic`, `tribunal`, `refinement`, `defense`,
  `mediator`, `judge`) nennen keinen Agenten mehr: `{main_agent}`, `{critic_agent}`,
  `{judge_agent}` aus `agent_config.role_names`, für Server- und Reflex-Zug.
- **Agenten-Editor (erledigt 07.11.2026):** Ein neuer Agent bekommt seine Grundprompts aus
  `prompts/<lang>/agent-template/` (Name eingesetzt, auch `memory_context.txt`: ohne sie sah ein neuer
  Agent seine Erinnerungen nie) und jeden Prompt seiner Debattenrolle (`turn_debate.role_prompt_keys`
  aus `_DEBATE_ROLES`), kopiert vom heutigen Inhaber der Rolle. Die Texte standen vorher im Code
  (`_get_role_templates`, mit Ersatz-Umlauten); ein Kritiker bekam kein `tribunal.txt`. Die Kennung
  ist der Name des Prompt-Ordners: nur `[a-z0-9_]` (vorher legte `../x` Dateien außerhalb an).
- **Bleibt so:** Wer den Hauptagenten direkt anspricht, bekommt nur dessen Antwort (wie bisher).

## Schritte

0. Reflex 0.10.1 (Zweig `reflex-0.10`), damit der Bestand bis zum Wechsel gepflegt läuft.
1. Bestandsaufnahme der 29 State-Mixins (`inventar-*.md`).
2. Zug-Ablauf aus dem State in einen Server-Dienst lösen; der Reflex-State wird dünner Client.
3. Ereignisstrom und Chat-API.
4. Erste Vue-Seite (Chat) neben Reflex unter `/api/app/`.
5. Seite für Seite: Einstellungen, Dokumente, Agenten, Vision, Audio.
6. Reflex entfernen: Mixins, `aifred/ui/`, `scripts/patch-reflex.py`, Reflex-Patches in `CLAUDE.md`;
   Server startet direkt (Granian/Uvicorn), nginx `/aifred/` auf den Server, Einzeilen-Installer.
