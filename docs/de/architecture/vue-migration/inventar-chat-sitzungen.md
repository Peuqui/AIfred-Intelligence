# Bestandsaufnahme: Chat und Sitzungen

Stand: 07.11.2026, Zweig `vue-frontend` (Commit `20878bce`, Reflex 0.10.1). Nur gelesen, nichts
geändert. Belege als `Datei:Zeile`; Pfade relativ zu `aifred/`. Wo etwas aus dem Code abgeleitet
und nicht im Browser nachgeprüft ist, steht „abgeleitet“.

Gelesen: `state/_chat_mixin.py`, `_session_mixin.py`, `_base.py`, `_streaming_state.py`,
`_chat_history_state.py`, `_tts_streaming_mixin.py`, `_image_mixin.py`, `_storage_mixin.py`,
`state/__init__.py`, `aifred.py`, `ui/chat_display.py`, `ui/input_sections.py`,
`ui/message_renderer.py`, `ui/streaming_text.py`, `ui/helpers.py`, `ui/lightbox.py`,
`ui/service_status_bar.py`, `lib/message_processor.py`, `lib/multi_agent.py` (Einstiege),
`lib/llm_engine.py`, `lib/api/chat.py`, `lib/api/browser_bus.py`, `lib/session_storage.py`,
`lib/scheduler.py`, dazu zur Klärung `lib/pipeline_registry.py`, `lib/debug_bus.py`,
`lib/message_hub.py`, `lib/api/app.py`, `lib/api/agents.py`, `state/_backend_mixin.py` (`on_load`),
`state/_auth_mixin.py` (Anmeldung) und der neue, noch unversionierte `lib/session_events.py`.

## Kurzfazit

- **Ein Zug im Browser ist eine einzige Reflex-Ereignisfunktion**, `ChatMixin.send_message`
  (`state/_chat_mixin.py:738-1531`). Sie hält Intent-Erkennung, Moduswechsel, Routing,
  Kompression, Bildbeschreibung, Multi-Agent, TTS-Steuerung, Speichern und Titel. `lib/multi_agent.py`
  bekommt den ganzen `AIState` als Parameter und schreibt direkt in ihn.
- **Ohne Browser gibt es einen zweiten, ärmeren Zug**: `message_processor.process_inbound`
  (`lib/message_processor.py:276-573`) für Kanäle, Scheduler und `/agent/trigger`. Er kennt weder
  History-Kompression noch Multi-Agent, Symposion oder Bilder.
- **Die Inject-API läuft nicht ohne Browser**: `/api/chat/inject` legt die Nachricht nur in eine
  Warteschlange der Sitzungsdatei, die der 500-ms-Abholer eines offenen Tabs leert
  (`lib/api/chat.py:76`, `state/_base.py:268-278`).
- **Der 500-ms-Abholer `refresh_debug_console` ist die Drehscheibe** für Diktat, Inject,
  Hub-Toasts, Sitzungsabgleich, Einstellungsabgleich und Debug-Zeilen aus Hintergrund-Tasks
  (`state/_base.py:215-398`). Sein Zeitgeber steckt im Inhalt des Debug-Akkordeons
  (`ui/input_sections.py:832-836, 884`); Radix hängt geschlossene Akkordeon-Inhalte aus, also stoppt
  das Zuklappen der Debug-Konsole alles davon (abgeleitet, siehe 7.3).
- **Server-weite Starts hängen am ersten Seitenaufruf**: Aufräum-Tasks, GPU-Erkennung, Ollama-Entladen
  und ein zweiter Message-Hub-Start laufen in `on_load` (`state/_backend_mixin.py:376-498`).

---

## 1. State-Felder je Mixin

Spalte „UI“: wird von `aifred/ui/` oder `aifred.py` gelesen. Private Felder (`_x`) gehen nie zum
Browser.

### 1.1 ChatMixin (`state/_chat_mixin.py`)

| Feld | Typ | UI | Zweck |
|---|---|---|---|
| `current_user_input` | str | nein (nur Server) | Text für `send_message` ohne Argument: Inject, Diktat, STT (`:89`, `:754`) |
| `current_user_message` | str | nein | Nachricht des laufenden Zugs (`:90`), wird nur gesetzt/geleert |
| `current_agent`, `current_agent_display_name`, `current_agent_emoji` | str | ja | Kopf der Streaming-Blase (`:92-94`, `ui/chat_display.py:494-508`) |
| `is_generating` | bool | ja | Sperrt Eingaben, zeigt Streaming-Blase (`:95`, `ui/input_sections.py:23`) |
| `is_generating_hub` | bool | nein | Zug kommt aus dem Hub, damit der mtime-Abgleich weiterläuft (`:102`, `state/_base.py:340`) |
| `is_compressing` | bool | ja | Sperre während Kompression (`:103`); wird nur im Debatten-Pfad gesetzt (7.4) |
| `debug_messages` | list[str] | ja | Debug-Konsole, gekappt auf `DEBUG_MESSAGES_MAX` (`:106`, `:149`) |
| `auto_refresh_enabled` | bool | ja | Auto-Scroll-Schalter (`:107`, `ui/input_sections.py:868`), liegt in settings.json |
| `progress_active/phase/current/total/failed` | bool/str/int | ja | Fortschrittsbanner (`:110-114`, `ui/chat_display.py:33-183`) |
| `tool_status` | str | ja | Werkzeuganzeige im Banner (`:117`) |

| Privat | Bedeutung |
|---|---|
| `_pending_load_time` | Ladezeit des Kaltstarts aus der Intent-Erkennung, hängt an der nächsten Antwort (`:82`, `:354-356`) |
| `_last_toolkit_tokens` | Werkzeug-Token des letzten Zugs für die Kompressionsprüfung (`:85`, `lib/multi_agent.py:753`) |
| `_research_context`, `_research_sources_html`, `_research_note` | Ergebnis von `web_search`, gelesen vom Research-Plugin (`:122-124`, `plugins/tools/research/__init__.py:60-66`) |

### 1.2 SessionMixin (`state/_session_mixin.py`)

| Feld | Typ | UI | Zweck |
|---|---|---|---|
| `session_id` | str | ja | Offene Sitzung (`:72`); markiert den Eintrag im Picker |
| `session_restored` | bool | nein | wird nur gesetzt (`:73`, `:207`, `:373`), nirgends gelesen |
| `available_sessions` | list[dict] | ja | Picker (`:76`, `ui/chat_display.py:359`), aus `list_sessions(owner)` |
| `current_session_title` | str | indirekt | Titel; der Picker zeigt `available_sessions[].title` |
| `selected_session_ids` | list[str] | ja | Häkchen für Sammellöschen (`:78`) |
| `session_confirm_delete_all` | bool | ja | zweistufiges „alle löschen“ (`:79`) |

| Privat | Bedeutung |
|---|---|
| `_session_initialized` | Sperre gegen doppelten Cookie-Rückruf (`:74`, `state/_auth_mixin.py:208-211`) |

### 1.3 AIState-Rest (`state/_base.py`)

| Feld | UI | Zweck |
|---|---|---|
| `all_sources`, `used_sources`, `failed_sources` | nein | werden nur auf `[]` gesetzt (`:173-175`, `state/_chat_mixin.py:848-850`, `state/_session_mixin.py:658-660`) |
| `_pending_used_sources`, `_pending_failed_sources` | nein | nur deklariert (`:176-177`) |
| `_last_detected_language` | nein | Sprache des laufenden Zugs, quer gelesen von Blase, TTS und Titel (`:180`) |
| `_stt_pending_path/_source/_filename/_size` | nein | geparkter Upload während einer `window.confirm`-Frage (`:408-411`) |

### 1.4 Teil-States

| Klasse | Feld | Zweck |
|---|---|---|
| `StreamingState(AIState)` (`state/_streaming_state.py:18-21`) | `current_ai_response: str` | wachsender Antworttext; eigener React-Kontext, damit nur `StreamingText` neu rendert |
| `ChatHistoryState(AIState)` (`state/_chat_history_state.py:20-24`) | `chat_history: list[dict]`, `llm_history: list[dict]` | Anzeige-Verlauf und Modell-Verlauf; eigener React-Kontext |

Zugriff aus den Mixins über `_chat_sub()` (`state/_base.py:184-187`) und `_streaming_sub()`
(`state/_tts_streaming_mixin.py:156-159`), beide `_get_state_from_cache`.

### 1.5 TTSStreamingMixin (`state/_tts_streaming_mixin.py`)

| Feld | UI | Zweck |
|---|---|---|
| `tts_regenerating` | ja | Spinner/Sperre der Neu-Vertonung (`:111`, `ui/message_renderer.py:78-107`) |
| `tts_streaming_in_flight` (`@rx.var`) | ja | `data-tts-active`, hält Medien-Fortsetzung zurück (`:119-129`, `aifred.py:1016-1018`) |
| `_tts_streaming_active` | – | Lesung läuft bis zum Finalisieren (`:105`) |
| `_tts_finalize_spawned` | – | Finalisieren genau einmal je Zug (`:106`) |
| `_tts_streaming_agent` | – | Stimme der laufenden Lesung (`:107`) |
| `_pending_audio_urls` | – | Audio für die nächste Blase (`:108`, `state/_chat_mixin.py:379-382`) |
| `_ui_yield_ts`, `_js_chunk_buffer`, `_UI_YIELD_INTERVAL=0.05` | – | 50-ms-Bündelung der Token (`:154-166`) |

Außerhalb des States, je Sitzung im Prozess: `TTSBackendState` mit `reading`, `finalize_task`,
`notices` (`:26-43`), dazu `abort_tts_reading`, `discard_tts_backend_state`, `take_tts_notices`
(`:55-77`).

### 1.6 ImageMixin (`state/_image_mixin.py`)

| Feld | UI | Zweck |
|---|---|---|
| `is_uploading_image` | ja | Spinner (`:40`) |
| `pending_images` | ja | Bilder für die nächste Nachricht: `name, path, url, size_kb` (`:41`, `:207-215`) |
| `image_upload_warning` | ja | Hinweistext (`:42`), fest auf Englisch (`:163-181`) |
| `max_images_per_message` | ja | aus `config.MAX_IMAGES_PER_MESSAGE` (`:43`) |
| `camera_available`, `is_mobile` | ja | Geräteerkennung per JS (`:44`, `:63`) |
| `crop_modal_open`, `crop_image_index`, `crop_preview_url`, `crop_box_*`, `crop_rotation` | ja | Zuschneide-Dialog (`:48-55`) |
| `multi_agent_help_open`, `reasoning_thinking_help_open`, `model_lifecycle_help_open` | ja | Hilfe-Dialoge, sachfremd hier (`:58-60`) |
| `_camera_detection_done`, `_mobile_detection_done` | – | Einmal-Sperren (`:45`, `:64`) |

### 1.7 StorageMixin (`state/_storage_mixin.py`)

| Feld | UI | Zweck |
|---|---|---|
| `storage_files` | ja | Exporte und Sandbox-Ausgaben aus `lib/storage_manager.list_managed_files` (`:26`) |
| `storage_confirm_delete_id`, `storage_confirm_clear` | ja | zweistufiges Löschen (`:28`, `:33`) |
| `storage_last_showcase` | ja | Hinweis nach Kopie in den Showcase (`:30`) |
| `storage_selected` | ja | Mehrfachauswahl (`:32`) |

---

## 2. Handler und interne Methoden

Abkürzungen: **E** öffentlicher `@rx.event`, **BG** `@rx.event(background=True)`, **i** intern,
**T** fire-and-forget `asyncio.create_task`. „Speichert“: **S** Sitzungsdatei
(`data/sessions/<id>.json`), **J** settings.json, **C** ChromaDB, **F** Dateien unter `data/`.

### 2.1 ChatMixin

| Methode | Art | Was | lib | Speichert |
|---|---|---|---|---|
| `add_debug` (`:128-152`) | E | Zeile mit Uhrzeit an `debug_messages`, dazu `debug_bus.debug` | `debug_bus` | – (Log) |
| `_set_progress`, `clear_progress`, `set_tool_status`, `clear_tool_status` (`:154-179`) | i/E | Banner-Felder | – | – |
| `_llamaswap_running_models` (`:187-190`) | i | Kaltstart-Prüfung | `service_status.running_models` | – |
| `_get_mode_label`, `_build_marker`, `_format_panel_metadata` (`:194-276`) | i | Modus-Marke als `<span>` mit fester Farbe, Fußzeile | `formatting.format_performance_footer` | – |
| `_set_current_agent` (`:223-233`) | i | Kopf der Streaming-Blase | `agent_config` | – |
| `_sync_to_llm_history` (`:280-309`) | i | `[AGENT]: …` an `llm_history` | `message_builder.with_history_notes` | – |
| `_add_agent_panel` (`:313-415`) | i | Blase bauen (`[LGTM]`→Text, Denkblöcke raus, Audio-URLs), anhängen, speichern | `formatting.build_assistant_chat_entry` | S |
| `_dispatch_multi_agent` (`:419-457`) | i | Sokrates-Analyse oder Tribunal nach der ersten Antwort | `multi_agent.run_tribunal/run_sokrates_analysis` | S (dort) |
| `_describe_images` (`:461-533`) | i | Bild einmal beschreiben lassen (auch entfernter Beschreiber) | `LLMClient`, `remote_describers`, `prompt_loader` | – |
| `_process_vision_request` (`:535-667`) | i | VL-Direkt-Zug über `call_llm` mit vollem Werkzeugkasten, danach Titel als T | `llm_engine.call_llm`, `agent_memory.prepare_agent_toolkit` | S |
| `_phase_pre_message_compression` (`:671-735`) | i | Kompression vor dem Zug (kleinster Kontext der Agenten) | `context_manager.summarize_history_if_needed` | – (später S) |
| `send_message` (`:738-1531`) | E (async gen) | der ganze Zug, siehe Abschnitt 3 | viele | S, F (Bilder) |
| `clear_chat` (`:1535-1543`) | E | Lesung abbrechen, `_clear_chat_internal` | – | S, F |
| `save_session_memory` (`:1547-1675`) | E (async gen) | Zusammenfassung des Chats per LLM, je Agent in das Agentengedächtnis | `LLMClient.chat_stream`, `agent_memory` | C |

### 2.2 SessionMixin

| Methode | Art | Was | lib | Speichert |
|---|---|---|---|---|
| `new_session` (`:83-120`) | E | neue Sitzung, Lesung abbrechen, State leeren, Cookie per JS | `session_storage.create_empty_session` | S, F |
| `switch_session` (`:122-226`) | E | Besitzer prüfen, `touch_session`, mtime merken, `_restore_session`, Cookie per JS | `load_session`, `touch_session` | S (last_seen) |
| `toggle_session_selection`, `select_all_sessions`, `clear_session_selection`, `request/cancel_delete_all_sessions` (`:228-250`) | E | Picker-Auswahl | – | – |
| `delete_all_sessions`, `delete_session`, `delete_selected_sessions` → `_delete_sessions` (`:252-323`) | E/i | nur Besitzer; offene Sitzung folgt auf Nachbarn (`_successor_session`, `:16-28`) | `session_storage.delete_session`, `discard_*_state` | S, F |
| `_load_latest_session` (`:327-346`) | i | nach Anmeldung die zuletzt gesehene Sitzung | `most_recently_seen_session_id` | – |
| `_load_session_by_id` (`:348-395`) | i | laden, Kontextauslastung melden | `load_session`, `context_manager` | – |
| `_restore_session` (`:397-461`) | i | Verläufe, Debug, Titel, Konfiguration, Audio-Zustand in den State | `DEFAULT_SESSION_CONFIG` | – |
| `_save_current_session` (`:468-503`) | i | ganzer In-Memory-Stand in die Datei, mtime merken | `update_chat_data` | S |
| `_persist_session_config` (`:505-533`) | i | Agent/Modus/Symposion in die Datei | `update_session_config` | S |
| `_refresh_available_sessions` (`:537-551`) | i | nur Pickerliste | `list_sessions(owner)` | – |
| `refresh_session_list` (`:553-592`) | E | Liste, Titel, Abgleich über Nachrichtenzahl, SSE neu starten | `get_session_title`, `load_session` | – |
| `_clear_chat_internal` (`:596-690`) | i | State leeren, Medien stoppen, Dateien löschen, Titel leeren, speichern | `cleanup_old_tts_audio(0)`, `cleanup_session_images/audio/sandbox`, `update_session_title` | S, F |
| `_generate_session_title` (`:694-826`) | T | erstes Frage-Antwort-Paar → Titel; Ergebnis per Push-Bus | `llm_engine.generate_session_title`, `browser_push` | S (in lib) |

### 2.3 AIState-Rest (`state/_base.py`)

| Methode | Art | Was | Speichert |
|---|---|---|---|
| `refresh_debug_console` (`:215-398`) | E, alle 500 ms | siehe 2.8 | – (liest S, J) |
| `handle_audio_upload`, `handle_audio_recording` → `_handle_audio` (`:413-557`) | E | STT-Upload prüfen, Dauer schätzen, ggf. `window.confirm` per `call_script` | F (tmp) |
| `stt_confirm_result`, `stt_unload_confirm_result` (`:571-622`) | E | Rückruf der Bestätigung; ggf. LLM entladen (`_unload_llm_for_stt`, `:624-651`) | – |
| `_run_transcription` (`:653-890`) | i | STT; Datei-Upload → Blase, langes Transkript → Arbeitsbereich-Datei, Diktat → Warteschlange oder Eingabefeld | S, F |
| `toggle_yarn`, `set_yarn_factor_input`, `apply_yarn_factor` (`:892-945`) | E | YaRN, sachfremd hier | J |

### 2.4 TTSStreamingMixin

| Methode | Art | Was | Speichert |
|---|---|---|---|
| `stream_text_to_ui` (`:168-199`) | E (als Funktion aufgerufen) | Token puffern, alle 50 ms an `current_ai_response`; Token an die Lesung | – |
| `flush_stream_to_ui` (`:201-208`) | E | Rest des Puffers | – |
| `_tts_streaming_wanted` (`:210-219`) | i | TTS an, Auto-Play an, Agentenstimme an | – |
| `_pause_media_for_tts` (`:221-233`) | i | setzt `media_paused_for_tts` aus dem Lese-Task, außerhalb jedes Ereignisses | – |
| `_init_streaming_tts` (`:235-260`) | i | `BrowserReading` starten (Push-Bus, Bericht, Hinweise) | – |
| `_abort_streaming_tts` (`:262-267`) | i | Lesung abbrechen | – |
| `_spawn_tts_finalize` (`:269-287`) | i → T | `reading.finish()`, Finalisieren als Task | – |
| `_wait_for_tts_finalize` (`:289-295`) | i | vorherige Lesung zu Ende abwarten | – |
| `_keep_reading_audio` (`:297-312`) | i | Abschnitte zu einer Datei der Sitzung | F |
| `_finalize_streaming_tts_in_background` (`:314-420`) | T | Audio an die jüngste Blase des Agenten ohne Audio patchen, Push `bubble_browser_speech/bubble_audio/bubble_tts_note`, speichern | S, F |
| `_extract_bubble_tts_request`, `_synthesize_bubble_audio`, `_read_bubble_aloud`, `_keep_bubble_reading`, `_apply_bubble_audio` (`:433-579`) | i | drei Phasen der Neu-Vertonung (lesen unter Sperre, sprechen ohne, schreiben unter Sperre) | S, F |
| `resynthesize_bubble_tts` (`:581-660`) | BG | eine Blase vorlesen und behalten; `stopTts()` per `call_script` | S, F |
| `resynthesize_all_tts` (`:662-742`) | BG | alle Blasen neu, ohne Live-Wiedergabe (`speak_to_files`) | S, F |

### 2.5 ImageMixin

| Methode | Art | Was | Speichert |
|---|---|---|---|
| `_normalize_upload_urls` (`:70-107`) | i | absolute `/_upload/`-URLs relativ machen, beim Laden einer Sitzung | – (mutiert in place) |
| `on_camera_click`, `on_file_picker_click` (`:113-123`) | E | nur Debug-Zeile | – |
| `handle_image_upload`, `handle_camera_upload` → `_process_image_upload` (`:125-223`) | E | prüfen, verkleinern, ablegen unter `data/upload/images/<sid>/` | F |
| `remove_pending_image`, `clear_pending_images` (`:225-243`) | E | Bild aus der Liste | – |
| `open/close_*_help` (`:249-285`) | E | Hilfe-Dialoge | – |
| `open_crop_modal`, `cancel_crop`, `rotate_crop_image_left/right`, `_rotate_crop_image`, `apply_crop_with_coords`, `_do_apply_crop` (`:291-491`) | E/i | drehen/zuschneiden mit PIL, neue Datei, alte weg | F |
| `set_camera_available`, `set_is_mobile` (`:497-526`) | E | Rückruf der JS-Erkennung (`aifred.py:827-881`) | – |

### 2.6 StorageMixin

Alle `@rx.event`, alle dünn über `lib/storage_manager` (`:35-129`): `load_storage_files`,
`refresh_storage_files`, `storage_open_file` (`window.open` per `call_script`, `:52-54`),
`storage_request/cancel/confirm_delete`, `storage_toggle_select`, `storage_select_all`,
`storage_clear_selection`, `storage_delete_selected`, `storage_request/cancel_clear`,
`storage_clear_all`, `storage_copy_to_showcase`. Speichert: F (`data/exports`, `data/sandbox_output`,
`docs/examples`).

### 2.7 Wie Daten zur Oberfläche gelangen

| Was | Weg | Beleg |
|---|---|---|
| Antwort-Token | `stream_text_to_ui` → `StreamingState.current_ai_response`, Delta per `yield` alle ≥ 50 ms; `StreamingText` setzt `textContent` per `useEffect` | `state/_tts_streaming_mixin.py:185-199`, `ui/streaming_text.py:35-52` |
| Denkphase | live nicht: `_stream_agent_to_history` behandelt den Chunk-Typ `thinking` nicht; erst die fertige Blase zeigt sie als Aufklapper (`render_bubble`) | `lib/multi_agent.py:258-338`, `lib/llm_pipeline.py:446-450` |
| fertige Blase | `chat_history` neu zugewiesen → Delta des `ChatHistoryState`; `rx.markdown` je Eintrag | `state/_chat_mixin.py:405-406`, `ui/message_renderer.py:247-271` |
| Debug-Konsole | `add_debug` im laufenden Ereignis → Delta beim nächsten `yield`; aus Tasks nur über den 500-ms-Abholer | `state/_base.py:382-398` |
| Fortschritt, Werkzeugstatus | State-Felder, Delta beim `yield` | `lib/multi_agent.py:267-326` |
| TTS-Audio | Push-Bus `tts`, `tts_speak`, `tts_pause` aus `BrowserReading` | `lib/browser_reading.py:282`, `lib/api/browser_bus.py:65-177` |
| Audio an der Blase | Push-Bus `bubble_browser_speech`, `bubble_audio`, `bubble_tts_note`; `custom.js` patcht das DOM | `state/_tts_streaming_mixin.py:390-412`, `assets/custom.js:984-1041` |
| Sitzungstitel | Push-Bus `session_title` (Titel im Feld `url`) und `available_sessions` im State | `state/_session_mixin.py:811-822` |
| Toasts | `rx.toast.*` per `yield`; aus Tasks über `TTSBackendState.notices` → Abholer; Hub über `notification.json` → Abholer | `state/_base.py:280-325, 370-380` |
| Statuszeile | ganz außerhalb von Reflex: Push-Bus `service_status`, `custom.js` füllt leere Rahmen | `ui/service_status_bar.py:1-10` |
| Skripte | `rx.call_script` für Cookie, `startBrowserStream`, `window.confirm`, `stopTts()`, Diktat ins Textfeld | `state/_session_mixin.py:120, 592`, `state/_base.py:548-551, 866-870` |

### 2.8 Zeitgeber und Abfragen

| Zeitgeber | Was er tut | Beleg |
|---|---|---|
| `rx.moment(interval=500)` → `refresh_debug_console` | 1. Vigilantia- und Casus-Abfrage huckepack; 2. settings.json-mtime → `_reload_settings_from_file`; 3. älteste Pending-Nachricht → `send_message`; 4. `notification.json` des Hubs lesen und löschen → Toast, `is_generating(_hub)`; 5. mtime der Sitzungsdatei → ganze Sitzung neu laden; 6. TTS-Hinweise → Toasts; 7. `debug_messages` neu zuweisen, wenn die Länge sich änderte | `state/_base.py:235-398`, `ui/input_sections.py:832-836` |
| `ensureBrowserStream` alle 30 s, bei Fokus/Sichtbarkeit | SSE-Verbindung wieder öffnen | `assets/custom.js:1305-1315` |
| Autoscroll-Intervall 120 ms während Streaming, Lade-Abfrage 300 ms | nur DOM | `aifred.py:194-208, 378-409` |
| Scheduler-Schleife | `_CHECK_INTERVAL_SECONDS`, im Hub-Thread | `lib/scheduler.py:374-403` |

---

## 3. Ablauf eines Zugs

### 3.1 Im Browser

1. **Eingabe.** Der Senden-Knopf führt JS aus: Wert aus dem unkontrollierten Textfeld lesen, Feld
   leeren, Wert als Argument an `send_message` (`ui/input_sections.py:652-664`). Enter löst den
   Knopf aus (`assets/custom.js:2195`). Diktat und Inject kommen über `current_user_input`
   (`state/_base.py:274-278`).
2. **Vorprüfungen** in `send_message`: angemeldet (`:747`), Text oder Bilder (`:756`), nicht schon
   aktiv (`:759`), Kalibrier-Sperre (`:768-774`), `_ensure_backend_initialized` (`:776`), Modell
   gewählt (`:782`), Diagnose unkalibrierter Kombination (`:792-818`).
3. **Sofort sichtbar:** `is_generating=True`, `yield` (`:825-840`).
4. **Nutzer-Eintrag:** Anzeige mit Vorschaubildern (`:852-868`), Eintrag inline gebaut
   (`:870-891`); `llm_history` bekommt Text, Bild-Marke und Zeitstempel (`:898-914`).
5. **Audio-Bus:** bei TTS auf das Finalisieren der vorigen Antwort warten und Push-Warteschlange
   leeren (`:928-931`); `startBrowserStream` per `call_script` (`:933-935`).
6. **Registrieren** in `pipeline_registry` (`:943-945`); ein älterer Zug derselben Sitzung wird
   abgebrochen (`lib/pipeline_registry.py:57-64`).
7. **Bilder** (`:956-1083`): Beschreiber wählen (`_image_describer`). Sieht das Hauptmodell selbst
   und kein Symposion mit ≥ 2 Agenten → **VL-Direkt** über `_process_vision_request` und
   `call_llm`, Ende (`:994-1059`). Sonst beschreibt das Vision-Modell einmal, die Beschreibung kommt
   in den Nutzer-Turn (`:1061-1083`).
8. **LLM-Client** und Automatik-Kontext (`:1086-1117`), Kaltstart-Hinweis über llama-swap
   (`:1124-1151`).
9. **Intent, Adressat, Sprache, Moduswechsel** in einem LLM-Aufruf
   (`lib/intent_detector.detect_query_intent_and_addressee`, `:1178-1191`); Bild-/URL-only ohne
   Aufruf (`:1162-1171`).
10. **Moduswechsel** (`:1216-1283`): in State und Sitzungsdatei; reiner Befehl → Bestätigungsblase
    (inline gebaut), speichern, Ende.
11. **Kompression vor dem Zug** (`:1286-1287`, `_phase_pre_message_compression`): kleinster Kontext
    von AIfred, Sokrates, Salomo (Namen fest, `:690-699`).
12. **Routing** (`:1303-1333`): Adressat aus dem Text > `active_agent` > `aifred`; ein expliziter
    Adressat wird klebrig (`_persist_session_config`). Direkt an AIfred → keine Sokrates-Analyse.
13. **Antwort:** Symposion mit ≥ 2 Agenten → `multi_agent.run_symposion` (`:1345-1348`); sonst
    `run_generic_agent_direct_response` (`:1367-1375`) →
    `_run_agent_direct_response` (`lib/multi_agent.py:641-838`): Modell, Kontext, Werkzeugkasten mit
    Gedächtnis (`prepare_agent_toolkit`), Prompt, Token-Aufstellung, Werkzeug-Budget, dann
    `_stream_agent_to_history` (`:209-380`): `_init_streaming_tts`, `run_llm_stream`, Token an
    `stream_text_to_ui`, Werkzeugstatus, am Ende `_spawn_tts_finalize`, `_sync_to_llm_history`.
    Die Blase entsteht mit `_add_agent_panel` (`:812-818`), danach `_save_current_session`.
14. **Multi-Agent** (`:1380-1386`, `_dispatch_multi_agent`): bei `tribunal` → `run_tribunal`
    (`lib/multi_agent.py:1146`), bei den anderen Nicht-Standard-Modi → `run_sokrates_analysis`
    (`:878`), mit Salomo und Überarbeitungsrunden; zwischen den Runden
    `_check_compression_if_needed` (`:383-446`) mit dem Kontext des nächsten Agenten.
15. **Abschluss** (`:1393-1401`): letzte Assistenz-Blase als `ai_text`, Streaming leeren,
    `is_generating=False`, speichern.
16. **Fehler/Abbruch:** `Exception` → Fehlerblase (`:1415-1437`); `CancelledError` und
    `GeneratorExit` → `finally` rettet den bisher gestreamten Text als unterbrochene Blase
    (`:1474-1493`), Lesung abbrechen statt finalisieren (`:1458-1461`).
17. **`finally`** (`:1439-1531`): Client schließen, abmelden, TTS finalisieren, Titel als
    Hintergrund-Task (`:1508-1518`), speichern, `refresh_session_list` (Picker, Abgleich,
    SSE-Neustart), Streaming-Kopf leeren.
18. **Nach dem Zug, außerhalb des Ereignisses:** `_finalize_streaming_tts_in_background` hängt das
    gesprochene Audio an die Blase und pusht es; `_generate_session_title` pusht den Titel. Ihre
    Debug-Zeilen erscheinen erst über den 500-ms-Abholer.

### 3.2 Ohne Browser

| Weg | Einstieg | Läuft in |
|---|---|---|
| Kanäle (E-Mail, Telegram, Discord, FreeEcho.2) | `dispatch_inbound` → `process_inbound` (`lib/message_processor.py:262-273`) | Hub-Thread mit eigener Event-Loop (`lib/message_hub.py:123-157`) |
| Scheduler | `scheduler_loop` → `_execute_job` → `process_inbound` (`lib/scheduler.py:406-471`), frische Sitzung je Lauf | Hub-Thread |
| Webhook | `POST /api/agent/trigger` → `BackgroundTasks` → `process_inbound` (`lib/api/agents.py:38-125`) | Haupt-Loop (ASGI) |
| Alarme | `lib/alert_bus.py:324` → `process_inbound` | je Aufrufer |
| Inject | `POST /api/chat/inject` → `queue_pending_message` (`lib/api/chat.py:62-76`) | **nur mit offenem Tab**: der Abholer startet `send_message` (`state/_base.py:268-278`) |

`process_inbound` (`lib/message_processor.py:276-573`): Kalibrier-Sperre mit fester englischer
Absage (`:300-326`), Sicherheitsstufe, Bereinigung, Nutzer-Zuordnung, Sitzung über die Routing-Tabelle
(`:344-364`), `pipeline_scope` + `session_scope` + `hub_notification_scope` (`:385-388`),
Intent-Erkennung (`:396-403`), Moduswechsel nur in die Datei (`:413-420`), Routing mit Weck-Agent,
LLM-Adressat, klebrigem Agenten (`:427-458`), Nutzer-Eintrag speichern (`:468-469`), Engine
`_call_engine` → `llm_engine.call_llm` → `run_llm_stream` (`:576-699`, `lib/llm_engine.py:27-323`),
Antwort anhängen (`_append_response`, `:787-846`), Antwort an den Kanal (`:554-558`), Titel inline
(`:563-566`). Debug-Zeilen schreibt `debug_bus.debug` einzeln in die Sitzungsdatei
(`lib/debug_bus.py:55-92`); der Browser sieht sie über den mtime-Abgleich.

### 3.3 Was schon in lib/ liegt, was nur im State

| Baustein | Browserunabhängig in lib/ | Nur im State |
|---|---|---|
| LLM-Strom, Werkzeugschleife, Artefakte, Metadaten | `llm_pipeline.run_llm_stream`, `bubble.render_bubble`, `formatting.build_inference_metadata` | – |
| Intent/Adressat/Moduswechsel | `intent_detector.detect_query_intent_and_addressee` | Anwenden auf State, Bestätigungsblase (`state/_chat_mixin.py:1216-1283`) |
| Kompression | `context_manager.summarize_history_if_needed` | Aufruf und Kontextwahl (`state/_chat_mixin.py:671-735`, `lib/multi_agent.py:383-446`); **im Hub-Pfad fehlt sie** |
| Multi-Agent, Symposion, Tribunal | Code in `lib/multi_agent.py`, aber jede Funktion nimmt `state: AIState` und schreibt hinein | ganze Steuerung |
| Bilder | `vision_routing`, `vision_utils`, `remote_describers` | Ablauf (`state/_chat_mixin.py:956-1083`), `pending_images` |
| Lesung im Browser | `browser_reading.BrowserReading` (Rückrufe statt State) | Start, Ende, Abbruch, Patchen der Blase |
| Sitzungsdatei, Konfiguration, Titel, Warteschlange | `session_storage`, `llm_engine.generate_session_title` | Halten des ganzen Stands im Speicher und Rückschreiben |
| Chat-Einträge | `formatting.build_assistant_chat_entry`, `build_channel_chat_entry` | Nutzer-Eintrag und Befehlsbestätigung inline |
| Abbrechen | `pipeline_registry.cancel_pipeline`, `handle_stop_command` | kein Stopp-Knopf im Browser |
| Agenten-Gedächtnis-Pin | `agent_memory` | Ablauf und Prompt (`state/_chat_mixin.py:1547-1675`) |
| STT | `audio_processing.transcribe_audio_auto`, `stt_client` | Bestätigungsfragen, Zielwahl (`state/_base.py:426-890`) |

---

## 4. Einteilung

| Funktionalität | Vue (UI-Zustand) | Server-Dienst (lib/) | Speicherung |
|---|---|---|---|
| Eingabefeld, Enter-sendet-Verhalten, Autoscroll, Schriftgröße, Lightbox, Zuschneide-Rahmen, Hilfe-Dialoge, Geräteerkennung, Picker-Häkchen, Lösch-Bestätigungen | ✔ | – | Vorlieben `enter_sends_message`, `auto_scroll`, `show_transcription` heute global in J |
| Streaming-Anzeige, Token-Bündelung, Agentenkopf, Fortschrittsbanner, Werkzeugstatus | ✔ (Anzeige) | Ereignisse liefern | – |
| Zug: Prüfungen, Intent, Moduswechsel, Routing, Kompression, Bilder, Antwort, Multi-Agent, Fehler/Teilrettung, Titel | – | ✔ ein `TurnService` für Browser und Kanäle | S |
| Warteschlange (Inject, Diktat) | – | ✔ im Server abarbeiten, nicht im Tab | S (`pending_messages`) |
| Abbrechen | Knopf | ✔ `handle_stop_command` | – |
| Sitzungen: anlegen, öffnen, Besitzer prüfen, löschen, Nachfolger, Liste, Konfiguration, Titel | Auswahl, Anzeige | ✔ `SessionService` | S |
| Chat leeren mit Dateien | Knopf | ✔ eine Funktion für UI und API | S, F |
| Debug-Konsole | Anzeige, Kappung | Zeilen je Sitzung als Ereignis | S (`debug_messages`) |
| Lesung der Antwort, Audio an der Blase, Neu-Vertonung | Wiedergabe, Playlist, Entsperren | ✔ `BrowserReading`, Finalisieren | S, F |
| Bild-Upload, Drehen, Zuschneiden | Rahmen, Vorschau | ✔ prüfen, verkleinern, PIL | F |
| STT-Upload, Dauer-Schätzung, Entladen-Frage | Aufnahme, Bestätigungsdialog | ✔ Transkription, Zielwahl | F, S |
| Gedächtnis-Pin | Knopf | ✔ | C |
| Speicher-Tab | Liste, Auswahl | ✔ `storage_manager` (schon da) | F |
| Hub-Benachrichtigung | Toast | ✔ Ereignis statt Datei | – |
| Einstellungsabgleich zwischen Tabs | – | ✔ Ereignis statt mtime-Abfrage | J |

---

## 5. Vorschlag: REST und Ereignisstrom

Grundsatz: Jeder Endpunkt mit Sitzungsbezug prüft den **Besitzer** gegen das Login-Cookie (heute
prüft die Middleware nur „angemeldet“, `lib/api/app.py:50-71`). Der Ereignisstrom baut auf dem neuen
`lib/session_events.py` auf (mehrere Abonnenten, Wiederholpuffer, `replaces`).

### 5.1 REST

| Methode, Pfad | Nutzlast → Antwort | Ersetzt |
|---|---|---|
| `GET /api/sessions` | → `[{session_id, title, last_message_at, message_count, channel}]` nur eigene | vorhandenes `GET /sessions` zeigt alle (siehe 7.6) |
| `POST /api/sessions` | → `{session_id}` | `new_session` |
| `GET /api/sessions/{id}` | → `{chat_history, config, title, turn: {running, agent}, pending_images, debug_tail}` | `switch_session`, `_restore_session` |
| `POST /api/sessions/{id}/open` | → 204 (last_seen) | `touch_session` |
| `DELETE /api/sessions/{id}`, `POST /api/sessions/delete` `{ids}` | → `{deleted, successor}` | `_delete_sessions` |
| `PATCH /api/sessions/{id}/config` | `{active_agent?, multi_agent_mode?, symposion_agents?}` | vorhanden: `POST /session/config` (`lib/api/chat.py:294-334`) passt inhaltlich |
| `POST /api/sessions/{id}/clear` | → 204 | `clear_chat`; vorhandenes `POST /chat/clear` löscht keine Dateien (7.4) |
| `POST /api/sessions/{id}/messages` | `{text, image_ids?}` → 202 `{queued_position}` | `send_message`; landet in der Server-Warteschlange |
| `POST /api/sessions/{id}/stop` | → `{pipeline_cancelled, audio_stopped}` | neu, nutzt `handle_stop_command` |
| `POST /api/sessions/{id}/images` (multipart) | → `{id, name, url, size_kb}` | `handle_image_upload/_camera_upload` |
| `DELETE /api/sessions/{id}/images/{img}` | | `remove_pending_image`, `clear_pending_images` |
| `POST /api/sessions/{id}/images/{img}/rotate` `{direction}`, `/crop` `{x,y,width,height}` | → neuer Eintrag | Zuschneide-Handler |
| `POST /api/sessions/{id}/audio` (multipart, `source=mic|file`) | → `{upload_id, estimate_s, needs_confirm, question}` | `_handle_audio` |
| `POST /api/sessions/{id}/audio/{upload_id}/transcribe` `{confirmed, unload_llm?}` | → `{target: bubble|workspace|queue|input, text?}` | `_run_transcription`, Bestätigungs-Rückrufe |
| `POST /api/sessions/{id}/memory/pin` | → `{stored, updated}` | `save_session_memory` |
| `POST /api/sessions/{id}/messages/speech` `{timestamp}` | → 204 (gebaut 07.11.2026, Lesung über den Ereignisstrom) | `resynthesize_bubble_tts` |
| `POST /api/sessions/{id}/speech/regenerate` | → 204 (gebaut 07.11.2026, Lauf über `speech_regenerating`) | `resynthesize_all_tts` |
| `GET /api/storage`, `DELETE /api/storage/{id}`, `POST /api/storage/delete` `{ids}`, `POST /api/storage/{id}/showcase` | | StorageMixin |
| `GET /api/sessions/{id}/events` (SSE) | `Last-Event-ID` | `GET /browser/stream/{session_id}` |
| `GET /api/events` (SSE je Konto) | | Hub-Toasts, Picker-Änderungen, Statuszeile, Einstellungen (nicht sitzungsgebunden) |

Schon passend oder nah dran: `POST /chat/inject` (Ziel wird die Server-Warteschlange),
`GET /chat/status` (künftig Laufzeitstatus statt Dateifeld), `POST /session/config`,
`GET /browser/stream/{id}` (Vorläufer), `POST /audio/position`, `GET /audio/file`,
`GET /service-status`, `GET /app/i18n/{lang}`, `GET /app/theme.css`. Nicht passend:
`GET /chat/history` mit gepaartem Altformat (`lib/api/chat.py:229-244`).

### 5.2 Ereignistypen

| Ereignis | Daten | heute |
|---|---|---|
| `turn_started` | `{turn_id, user_entry}` | `is_generating`, Nutzer-Eintrag |
| `turn_rejected` | `{reason: calibration|busy|no_model|not_logged_in}` | Debug-Zeile |
| `agent_started` | `{agent, display_name, emoji, mode, round}` | `_set_current_agent` |
| `token` | `{text}` | `current_ai_response` |
| `thinking` | `{text}` | fehlt live (2.7) |
| `tool_status` | `{text}` oder `{text: ""}` | `tool_status` |
| `progress` | `{phase, current, total, failed}` oder `{clear: true}` | `progress_*` |
| `message_added` | `{entry}` | `_add_agent_panel` |
| `message_updated` | `{timestamp, audio_urls, browser_speech, tts_note}` | `bubble_audio`, `bubble_browser_speech`, `bubble_tts_note` |
| `history_replaced` | `{chat_history}` (nach Kompression, Leeren, Kanal-Zug) | mtime-Abgleich, `refresh_session_list` |
| `turn_finished` | `{turn_id, status: done|error|cancelled, partial}` | `finally` |
| `debug` | `{line}` | `debug_messages` |
| `notice` | `{kind, message}` | `rx.toast`, `take_tts_notices` |
| `session_title` | `{title}` | Push-Bus `session_title` |
| `session_config` | `{active_agent, multi_agent_mode, symposion_agents}` | mtime-Abgleich |
| `tts`, `tts_speak`, `tts_pause`, `media`, `stop`, `pause`, `resume`, `seek`, `speed` | wie heute | Push-Bus |
| je Konto: `session_list_changed`, `hub_notification` `{channel, sender, status, session_id}`, `service_status`, `settings_changed` | | `notification.json`, mtime von settings.json, Push-Bus |

---

## 6. Abhängigkeiten

### 6.1 Was dieser Bereich von anderen braucht

| Quelle | Felder/Methoden | Belege |
|---|---|---|
| AgentConfigMixin | `active_agent`, `multi_agent_mode`, `symposion_agents`, `agent_tuning`, `agent_memory_enabled`, `_effective_model_id`, `_image_describer`, `_image_tuning_bucket`, `sokrates_*`, `debate_*`, `show_sokrates_panel` | `state/_chat_mixin.py:37-59`, `state/_session_mixin.py:41-58` |
| BackendMixin | `backend_type`, `backend_url`, `cloud_api_provider`, `_ensure_backend_initialized`, `_effective_automatik_id`, `_min_agent_context_limit`, `backend_initializing`, `backend_switching`, `on_load` | `state/_chat_mixin.py:41-60`, `ui/chat_display.py:559` |
| SettingsMixin | `_last_settings_mtime`, `_last_session_mtime`, `_last_pushed_debug_len`, `_reload_settings_from_file`, `_save_settings` | `state/_settings_mixin.py:133-135, 141, 263` |
| TTSConfigMixin | `enable_tts`, `tts_autoplay`, `tts_agents`, `tts_playback_rate`, `tts_player_visible`, `_tts_agent_enabled`, `_lang` | `state/_tts_streaming_mixin.py:23, 87-99` |
| AudioPlayerMixin | `media_paused_for_tts`, `media_audio_url`, `_restore_audio_state`, `_stop_media`, `discard_audio_runtime_state` | `state/_session_mixin.py:64-66, 277` |
| AuthMixin | `logged_in_user`; ruft `_load_latest_session`, `new_session`, `refresh_session_list`, `_clear_chat_internal` | `state/_auth_mixin.py:127-243` |
| UIConfigMixin | `ui_language`, `show_transcription`, `enter_sends_message`, `toggle_*`, `toggle_audio_recording` | `state/_ui_config_mixin.py:56-263` |
| VisionSettings, VigilantiaFeed, Casus | `vision_models_cache`; Huckepack-Abfragen im Abholer | `state/_image_mixin.py:168`, `state/_base.py:235-241` |
| DocumentMixin, ExportMixin | `is_uploading_document`, `document_upload_status`, `open_document_manager`, `share_chat`, `download_chat` | `ui/input_sections.py:26, 116, 270, 744-756` |

### 6.2 Was andere von diesem Bereich brauchen

- `add_debug`: praktisch alle Mixins und `lib/multi_agent.py`, `lib/research_tools.py`.
- `_save_current_session`: `state/_calibration_mixin.py:612`, `lib/multi_agent.py` (mehrfach).
- `_chat_sub()`: `state/_image_mixin.py`, `state/_export_mixin.py`, `lib/research_tools.py`,
  `lib/multi_agent.py`.
- `stream_text_to_ui`, `flush_stream_to_ui`, `_sync_to_llm_history`, `_add_agent_panel`,
  `_set_current_agent`, `_init_streaming_tts`, `_spawn_tts_finalize`, `_wait_for_tts_finalize`,
  `is_compressing`, `_last_toolkit_tokens`, `_research_*`: `lib/multi_agent.py:236-369, 438-446,
  753, 805-818`.
- Plugins bekommen den State über `prepare_agent_toolkit(state=self)` und lesen ihn:
  Research (`state._research_context`, `_research_note`, `_set_progress`, `add_debug`), Subagent
  (`state._effective_model_id`, `backend_url`, `backend_type`).
- `session_id`: über 50 Module in lib/ und plugins/ (Pfade, Routing, Push).

---

## 7. Auffälligkeiten

### 7.1 Zwei Zug-Pfade mit abweichender Logik (doppelt)

| Punkt | Browser | Kanäle/Scheduler |
|---|---|---|
| Engine | `multi_agent._run_agent_direct_response` → `run_llm_stream` | `llm_engine.call_llm` → `run_llm_stream` |
| History-Kompression | ja (`state/_chat_mixin.py:1286`) | **nein** (kein Aufruf von `summarize_history_if_needed` außer in `_chat_mixin` und `multi_agent`) |
| Multi-Agent, Symposion | ja | **nein**: Moduswechsel wird gespeichert (`lib/message_processor.py:413-420`), aber nie ausgeführt |
| Bilder | ja | nein |
| Routing/klebriger Agent | `state/_chat_mixin.py:1303-1319` | `lib/message_processor.py:427-458` (andere Reihenfolge, mit Weck-Agent) |
| Reiner Befehl | Bestätigungsblase | keine |
| Kalibrier-Sperre | Debug-Zeile (`:768-774`) | englischer Antworttext (`lib/message_processor.py:308-311`) |
| Titel | Task, Push | inline `await` |
| Nutzer-Eintrag | inline mit allen Feldern (`state/_chat_mixin.py:873-890`) | nur `role`, `content` (`lib/message_processor.py:746`) |

Chat-Einträge entstehen an vier Stellen mit verschiedenen Feldern: `state/_chat_mixin.py:873-890`,
`:1255-1270`, `:391-401` (über `build_assistant_chat_entry` plus Zusatzfelder) und
`lib/message_processor.py:742-749`. Für die API braucht es ein Schema.

### 7.2 Workarounds wegen Reflex

- **Teil-States** `StreamingState`, `ChatHistoryState` nur wegen React-Kontexten
  (`state/_streaming_state.py:1-9`); **50-ms-Bündelung** der Token (`state/_tts_streaming_mixin.py:161-166`).
- **Push-Bus**, weil Tasks keine Deltas pushen (`lib/api/browser_bus.py:1-12`); das Feld `url` trägt
  auch Titel, Notiz und JSON (`:81-82`).
- **Tasks schreiben State ohne Sperre**: `_finalize_streaming_tts_in_background`
  (`state/_tts_streaming_mixin.py:343-379`), `_generate_session_title`
  (`state/_session_mixin.py:803-818`), `_pause_media_for_tts` (bekannt, `:226-231`).
- **Diktat über die Warteschlange**, weil Reflex alles abbricht, was aus der Upload-Anfrage
  ausgelöst wird (`state/_base.py:837-842`, Patch 9).
- **Senden über `call_script`** mit unkontrolliertem Textfeld (`ui/input_sections.py:656-664`),
  Diktat per JS ins Feld (`state/_base.py:866-870`).
- **`window.confirm` per `call_script` + Rückruf** für STT (`state/_base.py:548-551, 727-730`).
- **Selbst-Schreib-Unterdrückung über mtime** an sechs Stellen (`state/_base.py:259, 361`,
  `state/_session_mixin.py:197, 367, 499, 529, 796`).
- **Audio-Knöpfe immer gerendert, JS blendet ein** (`ui/message_renderer.py:65-71`);
  `t()` kompiliert beide Sprachen ins Bundle (`ui/helpers.py:31-48`).
- **`GeneratorExit`-Sonderfall** im `finally` (`state/_chat_mixin.py:1406-1414, 1496, 1526`).
- **`app._api.mount`** über ein privates Attribut (`aifred.py:1209-1210`).

### 7.3 Fallen

- **Debug-Akkordeon zu = Abholer aus** (abgeleitet): `rx.moment` liegt im Inhalt des Akkordeons
  (`ui/input_sections.py:859-887`), Reflex setzt kein `forceMount`
  (`reflex_components_radix/primitives/accordion.py`), Radix rendert geschlossene Inhalte nicht
  (`@radix-ui/react-collapsible`: `children: isOpen && children`). Dann bleiben Diktat „direkt
  senden“, Inject, Hub-Toasts, Sitzungs- und Einstellungsabgleich liegen.
- **Server-Start hängt am Browser:** Aufräum-Tasks (Audio-Zustand, Lookup-Cache, Vision, Audit-Log,
  vLLM-Cache, Audio-Index), Vision-Vorwärmen, GPU-Erkennung und Ollama-Entladen starten nur im
  ersten `on_load` (`state/_backend_mixin.py:376-498`), ohne `track_orphan_task`. Der Message Hub
  startet zweimal: Lifespan (`aifred.py:1308-1327`) und `on_load` (`state/_backend_mixin.py:492`).
- **Log zeigt immer „Global init: False“**: `from . import _global_backend_initialized` bindet den
  Wert beim Import (`state/_backend_mixin.py:364, 369`, `state/__init__.py:6`).
- **Zwei Event-Loops:** Hub-Thread mit eigener Loop (`lib/message_hub.py:125`), Browser im
  Haupt-Loop. `pipeline_registry` ruft `task.cancel()` threadfremd (`lib/pipeline_registry.py:64, 95`);
  `browser_push` legt per `put_nowait` in eine `asyncio.Queue` des anderen Loops
  (`lib/api/browser_bus.py:161`). `session_events.subscribe` verlangt ausdrücklich dieselbe Loop
  (`lib/session_events.py:76-78`). `start_all` blockiert die Loop bis 10 s
  (`lib/message_hub.py:179`).
- **Veralteter Stand überschreibt Kanal-Einträge:** Während eines Browser-Zugs ist der Abgleich aus
  (`state/_base.py:340`); `_save_current_session` schreibt den ganzen Speicherstand
  (`state/_session_mixin.py:486-494`). Bekannt (`lib/session_storage.py:49-53`).
- **Chat leeren löscht alle TTS-Abschnitte aller Sitzungen:** `cleanup_old_tts_audio(max_age_hours=0)`
  (`state/_session_mixin.py:621-625`, `lib/audio_processing.py:1020`) läuft auch bei
  `new_session` und Abmelden; FreeEcho liest diese Dateien von der Platte (`aifred.py:1262-1263`).
- **Nach Sitzungswechsel hängt der SSE-Strom an der alten Sitzung** (abgeleitet):
  `switch_session` setzt nur das Cookie (`state/_session_mixin.py:225-226`), `ensureBrowserStream`
  nutzt die alte Id (`assets/custom.js:1305-1311`); erst der nächste Zug startet neu.
- **Hub-Benachrichtigung ist eine einzige Datei**, gelesen und gelöscht vom ersten Tab
  (`lib/message_processor.py:61-72`); andere Tabs und Konten sehen den Toast nicht.
- **Debug-Bus schreibt je Zeile die ganze Sitzungsdatei** unter der globalen Sperre
  (`lib/debug_bus.py:74-92`); der offene Tab lädt dann alle 500 ms die ganze Sitzung neu.
- **Push-Puffer wächst ohne TTS:** geleert nur bei Zügen mit TTS (`state/_chat_mixin.py:928-931`)
  und für TTS-Arten (`lib/api/browser_bus.py:143-146`); `_browser_event_storage` wird beim
  Sitzungslöschen nicht geräumt. Nur ein SSE-Abonnent je Sitzung (`:251`).

### 7.4 Doppelte oder abweichende Logik im Browser

- **Kompression zweimal:** `_phase_pre_message_compression` verwirft das `progress`-Ereignis
  (`state/_chat_mixin.py:726-735`, `lib/context_manager.py:507`), `_check_compression_if_needed`
  setzt dafür `is_compressing` (`lib/multi_agent.py:437-441`). Beim Hauptpfad bleibt die Sperre aus.
- **Sitzungsabgleich zweimal:** mtime (`state/_base.py:327-368`) und Nachrichtenzahl in
  `refresh_session_list` (`state/_session_mixin.py:576-585`).
- **Leeren zweimal:** Browser löscht Bilder, Audio, Sandbox, Titel (`state/_session_mixin.py:596-690`),
  `POST /chat/clear` nur die Verläufe und ohne Angabe die zuletzt geänderte Sitzung irgendeines
  Kontos (`lib/api/chat.py:142-159`).
- **Leeren gegen Löschen:** Leeren entfernt alle Sandbox-Ausgaben (`cleanup_session_sandbox`,
  `lib/sandbox.py:676-683`), Löschen behält sie absichtlich (`lib/session_storage.py:808-812`).
- **Neu-Vertonung zweimal:** `_read_bubble_aloud` mit Live-Wiedergabe gegen `_synthesize_bubble_audio`
  ohne (`state/_tts_streaming_mixin.py:469-514`).
- **`multi_agent_mode` aus settings.json** wird in `on_load` noch gelesen
  (`state/_backend_mixin.py:618`), obwohl er je Sitzung gilt.

### 7.5 Toter oder halbtoter Code

| Was | Beleg |
|---|---|
| `ChatMessage` (TypedDict), nur re-exportiert | `state/_base.py:42-62`, `state/__init__.py:9` |
| `all_sources`, `used_sources`, `failed_sources` (nur geschrieben), `_pending_used/failed_sources` (nur deklariert) | `state/_base.py:173-177` |
| `used_sources`/`failed_sources` in Chat-Einträgen, vom Renderer nicht gelesen | `state/_chat_mixin.py:396-397` |
| `session_restored`, `current_user_message` (nur geschrieben) | `state/_session_mixin.py:73`, `state/_chat_mixin.py:90` |
| Banner-Phasen `automatik` und `compress`: niemand setzt sie | `ui/chat_display.py:50-56, 77-85` |
| `handle_stop_command` ohne Aufrufer; kein Stopp-Knopf, obwohl Kommentare ihn nennen | `lib/pipeline_registry.py:125`, `state/_chat_mixin.py:941-942` |
| `get_api_app` für `api_transformer`, das bewusst nicht benutzt wird | `lib/api/app.py:91-99`, `aifred.py:1204-1206` |
| Re-Exporte „for backward compatibility“ | `state/__init__.py:2` |
| Altformat-Zweig (Tupel) in `_restore_session` | `state/_session_mixin.py:415-418` |
| `chat_summaries=None  # Aktuell nicht persistiert` | `state/_session_mixin.py:489` |
| Kommentar auf `_queue_tts_for_agent` (gibt es nicht), Überschrift „TTS Audio Broker“ ohne Inhalt | `state/_chat_mixin.py:1503`, `state/_base.py:36-38` |
| `remove_pending_image` prüft auf „Selected model“, der Text lautet „Selected Vision model“ | `state/_image_mixin.py:233` vs. `:170` |
| Docstring „keine Event-Methode“ an einer `@rx.event` | `state/_storage_mixin.py:35-39` |

### 7.6 Sicherheit und Regeln

- **API prüft keinen Besitzer:** `GET /sessions` liefert alle Sitzungen aller Konten, weil
  `list_sessions(None)` nicht filtert, entgegen seinem Docstring (`lib/api/chat.py:197`,
  `lib/session_storage.py:918, 972`); ebenso `GET /chat/history`, `GET /chat/status`,
  `POST /session/config`, `GET /browser/stream/{id}` für jede bekannte Id. Im State gibt es die
  Prüfung (`state/_session_mixin.py:157-166`).
- **Sitzung ohne Besitzer:** `queue_pending_message` legt fehlende Sitzungen ohne `owner` an
  (`lib/session_storage.py:1107-1109`); `switch_session` lässt solche Sitzungen für jeden zu
  (`state/_session_mixin.py:167-171`).
- **Fallback gegen die Regel:** `_ensure_backend_initialized` („Fallback initialization“,
  `state/_backend_mixin.py:1620-1627`).
- **Prompt im Code:** `save_session_memory` mit festem englischem Prompt, Sampling-Werten und
  Kappung auf 4.000 Zeichen (`state/_chat_mixin.py:1595-1634`).
- **Feste Texte statt i18n:** `ui/chat_display.py:446-467`, `ui/input_sections.py:323-328, 579-580`,
  `state/_image_mixin.py:163-181`, Toasts in `state/_base.py:451, 798-806`,
  `"[Bildanalyse]"` (`state/_session_mixin.py:753`), `lib/scheduler.py:618`.
- **Feste Agentennamen**, die laut Plan Rollen werden: `state/_chat_mixin.py:690-699, 711-713`,
  `lib/multi_agent.py:410-414`, `ui/input_sections.py:33`, `ui/message_renderer.py:200-209`.
- **`storage_open_file`** setzt die URL ungeprüft in JS ein (`state/_storage_mixin.py:54`).
