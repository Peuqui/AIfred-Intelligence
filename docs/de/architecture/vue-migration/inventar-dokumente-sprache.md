# Inventar: Dokumente, Dateiauswahl, Export, Sprachausgabe, Spracherkennung

Stand 07.11.2026, Zweig `vue-frontend` (Reflex 0.10.1, Commit `20878bce`). Nur gelesen, nichts
geändert; Zeilenangaben gelten für diesen Stand. Plan und Ziel: [README.md](README.md).

**Umfang.** Die Mixins `DocumentMixin`, `FilePickerMixin`, `ExportMixin`, `TTSConfigMixin`,
`STTSettingsMixin`, dazu das Diktat (liegt in `state/_base.py`, gehört inhaltlich hierher) und die
Seiten `ui/modals/documents.py`, `ui/file_picker.py`, `ui/audio_settings.py`, `ui/modals/bundles.py`,
`ui/modals/narrator_settings.py`, `ui/modals/crop.py`. Für Audio-Einstellungen, Bundles und Zuschnitt
liegt der State in `AudioPlayerMixin`, `MemoryBrowserMixin` und `ImageMixin`; hier steht davon nur,
was diese Seiten brauchen. `ui/modals/__init__.py` exportiert nur (`crop_modal`,
`document_manager_page`, `bundle_*_modal`, …), keine Logik.

Abkürzungen in Belegen: `doc` = `aifred/state/_document_mixin.py`, `pick` = `_file_picker_mixin.py`,
`exp` = `_export_mixin.py`, `ttsc` = `_tts_config_mixin.py`, `sttm` = `_stt_settings_mixin.py`,
`base` = `_base.py`, `fm` = `aifred/lib/file_manager.py`.

---

## 1. State-Felder

### 1.1 DocumentMixin

| Feld | Typ | Zweck |
|---|---|---|
| `is_uploading_document` | bool | Upload läuft; sperrt auch Knöpfe der Eingabeleiste (`ui/input_sections.py:141,173,208,271,295`) |
| `document_upload_status` | str | Fortschrittszeile („Indexiere i/n“), am Ende als Toast |
| `document_manager_open` | bool | wird gesetzt (doc:127,132), nie gelesen → tot |
| `document_preview_filename`, `document_preview_content` | str | Vorschau rechts (ganzer Text) |
| `doc_current_folder` | str | Ordner relativ zu `data/documents/`, `""` = Wurzel |
| `doc_file_list` | list[dict] | Zeilen. Lokal: `name,type,size,created,modified,indexed,chunks,file_count` (fm:156-179). Quelle: `id,name,type,size,modified,download_url,indexable,indexed,chunks` (doc:244-257) |
| `doc_rename_target`, `doc_rename_value` | str | Umbenennen in der Zeile |
| `doc_delete_target`, `doc_delete_is_folder`, `doc_delete_from_disk`, `doc_delete_from_index`, `doc_delete_batch` | str/bool | Löschdialog (Einzeln, Ordner, Auswahl) |
| `doc_selected_files` | list[str] | Mehrfachauswahl, nur Dateinamen des aktuellen Ordners (doc:72-76) |
| `doc_creating_folder`, `doc_new_folder_name` | bool/str | Eingabe „neuer Ordner“ |
| `doc_source` | str | `"local"` oder Schlüssel einer Quelle (`nextcloud_files`, Google) |
| `doc_source_options` | list[{key,label}] | Umschalter, lokal zuerst |
| `doc_source_writable` | bool | Upload-Zone sichtbar |
| `doc_remote_trail` | list[{id,name}] | geöffnete Ordner einer Quelle |
| `doc_remote_query`, `doc_remote_query_input` | str | aktive Suche / Eingabefeld |

Berechnet: `doc_path_prefix` (Pfad für die Datei-Tools, doc:39-49), `doc_download_prefix` (doc:51-60),
`doc_selection_count`, `doc_has_selection`, `doc_all_selected`, `doc_delete_dialog_open` (doc:79-98),
`doc_is_remote`, `doc_remote_path_text` (doc:198-204). Private Felder: keine.

### 1.2 FilePickerMixin

| Feld | Typ | Zweck |
|---|---|---|
| `picker_open`, `picker_title` | bool/str | Modal |
| `picker_root` | str | absolute Sandbox-Wurzel |
| `picker_current`, `picker_path_input` | str | aktueller Pfad relativ zur Wurzel / Eingabe |
| `picker_entries` | list[dict] | `name,rel_path,abs_path,is_dir,is_symlink,size,mtime,error` (pick:167-179) |
| `picker_caps` | dict[str,bool] | `can_create_folder`, `can_create_symlink` (weitere im Kommentar pick:31 gibt es nicht) |
| `picker_symlink_target_must_be_under_root` | bool | Schutz gegen Sandbox-Ausbruch |
| `picker_file_filter`, `picker_show_files`, `picker_show_hidden`, `picker_sort_by`, `picker_mode` | | Listenoptionen; `pick_folder`/`pick_file` |
| `picker_callback_event`, `picker_callback_args` | str/dict | Rückruf per Name in das aufrufende Mixin |
| `picker_loading`, `picker_error`, `picker_writable`, `picker_filter_text` | | Zustand der Liste |
| `picker_creating_folder`, `picker_new_folder_name`, `picker_creating_symlink`, `picker_symlink_name`, `picker_symlink_target` | | Eingabeformulare |

Berechnet: `picker_breadcrumbs` (pick:61-81), `picker_filtered_entries` (Live-Filter, pick:83-89).

### 1.3 ExportMixin

Keine eigenen Felder. Liest `chat_history` (über `_chat_sub()`), `current_session_title`,
`session_id`, `user_name` (exp:16-26).

### 1.4 TTSConfigMixin

| Feld | Typ | Zweck |
|---|---|---|
| `enable_tts` | bool | Sprachausgabe an/aus (persistiert) |
| `tts_autoplay` | bool | automatisch abspielen (persistiert) |
| `tts_playback_rate` | str | Abspieltempo des Browser-Players, kommt nur aus settings.json, kein Setter (`aifred.py:65`) |
| `tts_pitch` | str | global, nur gespeichert/geladen, nie gelesen → tot |
| `tts_agents` | dict | je Agent `{enabled, language}` (Stummschalten, Sprach-Override) |
| `tts_list_revision` | int | Zähler, der alle aus settings.json abgeleiteten Vars neu rechnen lässt |
| `tts_status_checking` | bool | Statusabfrage läuft |
| `narrator_voices` | dict[engine,str] | Erzählerstimme je Engine |
| `narrator_voice_engine`, `narrator_settings_open` | str/bool | Modal |
| `editor_tts_engine` | str | im Agent-Editor bearbeitete Engine |

Privat: `_editor_tts_settings` (`{voice,speed,pitch}` des bearbeiteten Agenten, ttsc:246).
Berechnet: `tts_service_error`, `tts_escalation_rows` (Zeilen der Liste), `tts_player_visible`,
`tts_editor_engine_options`, `editor_tts_engine_label`, `editor_tts_available_voices`,
`editor_agent_tts_voice|speed|pitch|language`, `tts_language_labels`, `narrator_plugin_enabled` (tot),
`narrator_engine_options`, `narrator_engine_display`, `narrator_voice_options`,
`narrator_voice_display` (ttsc:78-137, 248-318, 388-432).

### 1.5 STTSettingsMixin und Diktat

| Feld | Typ | Zweck |
|---|---|---|
| `stt_available` | bool | STT-Suite antwortet (sttm:34) |
| `stt_device` | str | `auto`/`cpu`/`cuda`, settings.json (sttm:35) |
| `show_transcription` | bool | (UIConfigMixin) Diktat erst ins Eingabefeld statt direkt an die KI |
| `_stt_pending_path/_source/_filename/_size` | str, privat | geparkter Upload während eines Bestätigungsdialogs (base:408-411) |

Berechnet: `stt_device_label`, `stt_device_labels` (sttm:37-45).

### 1.6 Felder fremder Mixins, die diese Seiten lesen

| Seite | Mixin | Felder |
|---|---|---|
| `audio_settings.py` | AudioPlayerMixin | `audio_settings_open`, `audio_sources_view`, `audio_settings_busy`, `audio_settings_busy_source`, `audio_settings_status`, `audio_picker_root_input`, `audio_tts_list_max_input`, `audio_settings_help_open` (`_audio_player_mixin.py:87-94,245`) |
| `bundles.py` | MemoryBrowserMixin | `bundle_export_open`, `bundle_export_selected`, `bundle_all_agents`, `bundle_import_open`, `bundle_import_uploaded_b64`, `bundle_import_agents`, `bundle_import_selected`, `bundle_import_conflict`, `bundle_import_error` (`_memory_browser_mixin.py:79-89`) |
| `crop.py` | ImageMixin | `crop_modal_open`, `crop_image_index`, `crop_preview_url`; `crop_box_*`, `crop_rotation` nur geschrieben (`_image_mixin.py:48-55`) |

---

## 2. Handler, Abläufe, Uploads, Diktat

### 2.1 DocumentMixin

| Handler | tut | lib | speichert |
|---|---|---|---|
| `open_document_manager` / `on_load_document_manager` / `close_document_manager` | Route `/documents`, Zustand zurücksetzen, Quellen laden, Liste lesen (doc:108-136) | `document_sources()` | – |
| `_refresh_file_list` (privat) | lokale Liste, Auswahl bereinigen (doc:142-154), **synchron** | `fm.list_directory` | – |
| `doc_navigate_folder/_up/_root`, `doc_refresh` | Ordnerwechsel (doc:156-182, 319-322) | wie oben | – |
| `set_doc_source`, `doc_open_remote_folder`, `doc_remote_up/_root/_search/_refresh`, `set_doc_remote_query_input` | Quelle wechseln, Ordner/Suche einer Quelle (doc:267-313) | `get_document_source().list_folder/search`, `chunks_per_document` (to_thread), `source_index_name`, `PARSERS` | – |
| `doc_open_create_folder` … `doc_confirm_create_folder` | Ordner anlegen (doc:324-351) | `fm.create_folder` | Platte |
| `doc_start_rename` … `doc_confirm_rename` | Umbenennen, Index zieht mit (doc:365-399), to_thread | `fm.rename(sync_index=True)` | Platte + ChromaDB |
| `handle_document_upload` | Upload in den Ordner bzw. in die Quelle, Endung prüfen, Pfadausbruch abweisen, danach `doc_index_uploaded` verketten (doc:405-507) | `DOCUMENT_ALLOWED_EXTENSIONS`, `source.upload` | Platte bzw. Quelle |
| `doc_index_uploaded` (**background**) | Hochgeladenes nacheinander indexieren, Toast je Datei (doc:513-542) | `fm.index_file` | ChromaDB |
| `doc_index_folder`, `doc_index_remote_folder` | Ordner rekursiv indexieren mit Fortschritt (doc:598-657) über `_index_files` (doc:544-582) | `fm.index_file`, `files_below`, `fm.index_source_file` | ChromaDB |
| `doc_index_file`, `doc_index_remote_file`, `doc_deindex_file`, `doc_deindex_remote_file` | einzeln (de)indexieren (doc:659-732) | `fm.index_file/index_source_file/deindex_file` | ChromaDB |
| `doc_toggle_select(_all)`, `doc_clear_selection`, `doc_open_(batch_)delete_dialog`, `doc_close_delete_dialog`, `doc_toggle_delete_disk/_index` | reiner Dialog-/Auswahlzustand (doc:738-792) | – | – |
| `doc_confirm_delete` + `_doc_delete_selection` | Datei, Ordner (rekursiv) oder Auswahl löschen, Platte und/oder Index (doc:794-903) | `fm.delete_file`, `fm.delete_folder` | Platte + ChromaDB |
| `preview_document`, `close_document_preview` | Text über den Parser lesen (doc:909-945), synchron | `fm.safe_resolve`, `PARSERS` | – |

Kein Timer außer `asyncio.sleep(4)` in `doc_index_file` (doc:677-680, wirkungslos, siehe 7).
Die Suche über den Index (`lib/document_search_index.py`, `fm.search_index`) benutzt die Oberfläche
nicht; sie ist nur Agenten-Werkzeug.

### 2.2 FilePickerMixin

| Handler | tut | lib |
|---|---|---|
| `_picker_open_for` (privat) | Wurzel, Fähigkeiten, Filter, Modus, Rückruf setzen und öffnen (pick:96-135); einziger Aufrufer `open_audio_source_picker` (`_audio_player_mixin.py:418-432`) | – |
| `picker_close` | schließen, Rückruf löschen (pick:137-141) | – |
| `_picker_refresh`, `picker_navigate(_up)`, `picker_jump_to_path`, `picker_set_sort`, `picker_set_path_input`, `picker_set_filter_text` | Liste lesen, navigieren (pick:145-220) | `file_browser.browse` |
| `picker_pick_current`, `picker_entry_clicked`, `_dispatch_callback` | Auswahl → Rückruf per `getattr(self, name)(rel_path, **args)` (pick:224-249) | – |
| `picker_create_folder_*`, `picker_create_symlink_*` | Ordner/Symlink anlegen, Fähigkeit geprüft (pick:253-324) | `file_browser.create_folder/create_symlink` |

### 2.3 ExportMixin

| Handler | tut | lib | speichert |
|---|---|---|---|
| `_build_and_save_export` (privat) | ganze Sitzung als eigenständiges HTML bauen (exp:32-106): Markdown (mistune), Quellen-Kasten, Agenten-Kopf, Bilder/Audio/Sandbox als Base64 bzw. `srcdoc`, KaTeX und CSS eingebettet | `vision_utils.load_image_url_as_base64`, `audio_processing.load_audio_url_as_base64`, `formatting.get_katex_inline_assets`, `formatting._save_html_to_assets`, `theme.css_variables` | `data/html_preview/🎩 AIfred - <Titel>.html` |
| `share_chat` | Export bauen, per `call_script` `window.open` (exp:108-120) | | |
| `download_chat` | Export bauen, per `call_script` fetch → Blob → `<a download>` (exp:122-146) | | |

Alle Bauhelfer (exp:152-1046) sind Methoden bzw. statische Methoden des Mixins, nicht lib.

### 2.4 TTSConfigMixin

| Handler | tut | lib | speichert |
|---|---|---|---|
| `set_enable_tts`, `toggle_tts_autoplay` | Schalter (ttsc:141-151) | – | `_save_settings` (Schnappschuss) |
| `_edit_tts_lists` (privat) + `move_tts_entry`, `set_tts_entry_enabled`, `set_tts_entry_unit` | Liste mit Katalog abgleichen, Änderung auf Kopie, prüfen, ganz schreiben (ttsc:155-208) | `persisted_settings`, `tts_service.catalog/matched_list/check_stored_settings`, `SPEECH_UNITS` | `tts_escalation`, `tts_toggles_per_engine[engine].unit` |
| `check_tts_status` (**background**) | Katalog frisch lesen, Liste neu zeigen (ttsc:210-231) | `catalog(fresh=True)` | – |
| `_tts_agent_enabled`, `_set_tts_agent` (privat) | Stummschalten/Sprache je Agent (ttsc:235-240) | – | `tts_agents` über `_save_settings` |
| `set_editor_tts_engine`, `set_editor_agent_tts_voice/_speed/_pitch`, `set_editor_agent_tts_language` | Stimme je Agent und Engine im Agent-Editor (ttsc:320-384) | `i18n.tts_label_to_key`, `agent_config.get_tts_voice_default` | `tts_agent_voices_per_engine[engine][agent]` |
| `_load_editor_tts_settings`, `_save_editor_tts_settings` (privat) | gespeicherte Werte oder Agenten-Standard (ttsc:325-351) | `persisted_settings`, `load_settings` | s. o. |
| `set_narrator_voice_engine`, `set_narrator_voice`, `open/close_narrator_settings` | Erzählerstimme je Engine (ttsc:434-457) | `tts_label_to_key` | `narrator_voices` über `_save_settings` |

### 2.5 STTSettingsMixin

| Handler | tut | lib | speichert |
|---|---|---|---|
| `load_stt_settings` | Gerät lesen, `health()` prüfen; aufgerufen beim Tab-Wechsel (`_agent_editor_mixin.py:421`) | `stt_client.get_default_client().health` (synchron, HTTP) | – |
| `refresh_stt_settings` | ruft `load_stt_settings`; kein Aufrufer | | |
| `stt_set_device` | Beschriftung → Schlüssel, schreiben (sttm:67-79) | `load_settings` | `stt_device` |

### 2.6 Uploads heute (Reflex)

1. **Datei-Upload.** `rx.upload(id=…, on_drop=Handler)` (Dokumente `ui/modals/documents.py:641-660`,
   Audio-Datei `ui/input_sections.py:286-307`, Bilder 199-228, Kamera 164-195, Bundle
   `ui/modals/bundles.py:139-160`). Der Browser schickt multipart an Reflex' `POST /_upload` mit
   Client-Token und Handlername; Reflex ruft den Handler mit `list[rx.UploadFile]` **innerhalb dieser
   HTTP-Anfrage** auf und streamt die Deltas als deren Antwort.
2. Folgen: Jeder Token reicht, daher prüft jeder Upload-Handler selbst den Login (doc:416-420,
   base:428-432, `_image_mixin.py:145-149`; Test `tests/test_upload_login_guard.py`). Der Handler liest
   die ganze Datei in den Speicher (`await file.read()`, doc:454, base:469). Was er verkettet, endet mit
   der Anfrage (Kommentar base:837-842).
3. **Ausliefern** über Starlette-Mounts in `aifred.py`: `/_upload/documents` (Cookie-Pflicht,
   `aifred.py:1294-1300`), `/_upload/html_preview` **ohne** Cookie für geteilte Exporte
   (`aifred.py:1244-1250`), `/_upload/images`, `/tts_audio`, `/audio`, `/sandbox_output` mit Cookie.
   Dateien einer Quelle streamt `GET /api/documents/source/{key}/file/{id}`
   (`lib/api/documents.py:17-33`).
4. **Patch 9** (eigener Kontext für Uploads) ist seit Reflex 0.10 upstream behoben (PR #7357), aus
   `scripts/patch-reflex.py` entfernt (Commit `20878bce`); `tests/test_reflex_upload_context.py`
   wacht weiter, bis Reflex weg ist.

### 2.7 Diktat: Aufnahme im Browser → STT → Text oder direkt an die KI

1. Mikrofonknopf → `toggle_audio_recording` → `call_script("toggleRecording()")`
   (`_ui_config_mixin.py:260-263`).
2. `assets/custom.js:57-111`: `getUserMedia`, `MediaRecorder` (webm/opus), Piepton; bei Stopp
   `uploadAudioBlob` (custom.js:138-169) legt die Datei per `DataTransfer` in das versteckte
   `<input type=file>` von `#audio-recording-upload` (`input_sections.py:334-343`) und löst `change`
   aus → Reflex-Upload → `handle_audio_recording` (base:419-424). Die Audio-Datei-Taste geht über
   `handle_audio_upload` (base:413-417) denselben Weg mit `source="file"`.
3. `_handle_audio` (base:426-557): Login; STT-Suite bereit (`is_ready`, sonst `ensure_ready`, das den
   Container startet, base:435-446); Endung gegen feste Liste (base:457); Größe gegen
   `AUDIO_UPLOAD_MAX_MB`; Temp-Datei; Dauer schätzen (`get_audio_duration` × `WHISPER_RTF_GPU`); über
   `WHISPER_TRANSCRIBE_TIMEOUT_S` → Abbruch; über `WHISPER_CONFIRM_THRESHOLD_S` → `window.confirm` per
   `call_script` mit Rückruf `stt_confirm_result`, Kontext in den privaten Feldern geparkt.
4. `_run_transcription` (base:653-890): `transcribe_audio_auto` im Executor (Gerät aus `stt_device`,
   `lib/audio_processing.py:1073-1096`; Datei: Sprache `auto` und Sprecher-Erkennung, Mikrofon:
   UI-Sprache). `STTGPUUnavailableError` → `window.confirm` „LLM entladen?“ →
   `stt_unload_confirm_result` → `_unload_llm_for_stt` (llama-swap `POST /unload` bzw. Ollama,
   base:624-651) → ein zweiter Versuch.
5. Ergebnis: länger als `TRANSCRIPT_TO_WORKSPACE_THRESHOLD_CHARS` (4000) → `transcript-….txt` in die
   Dokumente (`fm.write_file`) plus Agenten-Blase mit Link (base:758-810). Audio-Datei → Blase mit
   Transkript (base:815-828). Mikrofon und „direkt senden“ (und keine Mail-/URL-Muster,
   base:833-835) → `queue_pending_message(session_id, text)` (base:843-854); der 500-ms-Abholer
   `refresh_debug_console` (base:268-278, `rx.moment` in `input_sections.py:832-836`) übernimmt die
   Nachricht und verkettet `send_message`. Sonst Text an `current_user_input` anhängen und per JS in
   die unkontrollierte Textarea `#user-text-input` schreiben (base:856-871).

---

## 3. Was ist schon browserunabhängig?

| Funktion | in `lib/` | über `lib/api` | nur im State |
|---|---|---|---|
| Ordner listen, anlegen, löschen, umbenennen, Index folgt | `fm.list_directory/create_folder/delete_*/rename` | – | Zeilen der Quelle bauen (doc:243-257), Quellenliste (doc:206-213) |
| Upload speichern | – | – | Endung, Pfadprüfung, Schreiben (doc:426-489) |
| Indexieren einzeln/Ordner, Fortschritt | `fm.index_file/index_source_file/deindex_file`, `files_below` | – | Schleife mit Fortschritt und Zusammenfassung (doc:544-582), Indexieren nach Upload (doc:513-542) |
| Vorschau | `PARSERS`, `fm.safe_resolve` | – | Aufruf (doc:909-939) |
| Download | – | Quelle: `/api/documents/source/{key}/file/{id}`; lokal: Mount `/_upload/documents` | – |
| Quellen (Nextcloud, Google) | `lib/document_sources.py`, Plugins `nextcloud/source.py` | Download | – |
| Dateiauswahl | `lib/file_browser.browse/create_folder/create_symlink` | – | Bereich (Wurzel, Fähigkeiten), Rückruf |
| Chat-Export | nur Hilfen (`_save_html_to_assets`, KaTeX, Base64-Lader) | – | ganzer HTML-Bau (exp:32-1046) |
| TTS-Katalog, Liste, Labels, Stimmen, Einheit | `lib/tts_service.py` (SSOT), `tts_voices.voices_for` | `GET /api/tts/entries` (nur lesen, Dienst-Token, ohne Einheit/Hinweis) | Zeilen für die Oberfläche (ttsc:89-132), Änderungs-Transaktion (ttsc:155-178), Stimme je Agent laden/speichern (ttsc:325-351) |
| Sprachausgabe an/aus | settings.json | `GET/PATCH /api/settings` (nur `enable_tts`, `lib/api/core.py:93`) | `tts_autoplay`, `tts_agents`, `narrator_voices` |
| STT-Client, Transkription, Dauer | `stt_client`, `audio_processing.transcribe_audio_auto/get_audio_duration` | – (Status nur in `/api/service-status`) | Prüfen, Schätzen, Rückfragen, LLM entladen, Ergebnis verteilen (base:426-890) |
| Diktat „direkt an die KI“ | `session_storage.queue_pending_message` | `POST /api/chat/inject` (gleiche Warteschlange, Dienst-Token) | Start des Zugs nur über den 500-ms-Abholer im Browser (base:268-278) |
| Agent-Bundles | `lib/agent_bundle` | `GET /api/agents/export`, `POST /api/agents/import/peek`, `POST /api/agents/import` (vollständig) | zweiter Weg im State (`_memory_browser_mixin.py:428-551`) |
| Audio-Quellen (Seite Audio-Einstellungen) | `audio_player_settings.build_configured_source_map`, `audio_index` | `GET /api/audio/file`, `POST /api/audio/position` | Quellenliste, Index starten/löschen, Quelle anlegen/entfernen, Sandbox-Wurzel |

**Kern:** Dokumente und TTS haben ihre Domänenlogik schon weitgehend in lib; es fehlen Upload,
Index-Läufe mit Fortschritt und die Zeilen-/Transaktionsschicht. Export und Diktat liegen fast
vollständig im State; sie brauchen je einen neuen lib-Dienst. Bundles sind bereits fertig über die
API erreichbar.

---

## 4. Einteilung

| Funktion | UI-Zustand (Vue) | Domänenlogik (lib-Dienst) | Speicherung |
|---|---|---|---|
| Dokumentliste, Navigation | aktueller Ordner, Pfadleiste, Auswahl, Inline-Eingaben, Löschdialog-Optionen, Vorschau offen | Liste je Quelle (lokal + Quelle in einer Funktion), Ordner/Umbenennen/Löschen | Platte, ChromaDB |
| Upload | Drop-Zone, Fortschritt | `documents.save_upload(source, folder, name, stream)` mit Endung und Pfadprüfung | Platte / Quelle |
| Indexieren | Fortschrittsanzeige | Index-Job (eine Datei, Ordner, Hochgeladenes) mit Ereignissen | ChromaDB |
| Vorschau | Fenster, Scrollen | Text über Parser (in Abschnitten liefern) | – |
| Dateiauswahl | Pfad, Filter, Sortierung, Formulare | Bereiche serverseitig (Wurzel + Fähigkeiten je Zweck), browse, Ordner/Symlink | Platte |
| Chat-Export | Knopf, Download/neuer Tab | `chat_export.build_html(session_id)` aus der gespeicherten Sitzung | `data/html_preview/` |
| TTS-Liste | Reihenfolge-Knöpfe, Schalter, Einheit-Auswahl | Zeilen bauen, Liste ändern (Transaktion), Status frisch | settings.json |
| TTS je Agent, Erzähler | Auswahl im Agent-Editor/Modal | Stimmenlisten, laden/speichern je Agent und Engine | settings.json |
| STT-Gerät | Auswahl | Gerät lesen/schreiben, Erreichbarkeit | settings.json |
| Diktat | Aufnahme (`MediaRecorder`), Knopfzustand, Bestätigungsdialog, Eingabefeld | Transkriptions-Job: prüfen, schätzen, Rückfragen, ausführen, Ergebnis verteilen, bei „direkt“ den Zug starten | Temp-Datei, Dokumente (langes Transkript), Sitzung |
| Bundles | Auswahl, Konfliktwahl, Datei-Drop | vorhanden (`agent_bundle` + API) | `data/agents`, settings.json |
| Zuschnitt | Rahmen ziehen (Koordinaten in Prozent), Drehen-Knopf | drehen, zuschneiden einer hochgeladenen Bilddatei | `data/upload/images/<session>/` |
| Audio-Quellen | Liste, Busy-Anzeige, Hilfe | Quellen, Index-Job, Symlink anlegen (gehört dem Plugin) | `data/media/audio/`, Plugin-settings.json |

---

## 5. Vorschlag: REST-Endpunkte und Ereignisse

Alle unter `/api/`, Cookie-Pflicht über die vorhandene Middleware (`lib/api/app.py:48-70`); damit
entfallen die Login-Prüfungen in den Upload-Handlern. Lange Arbeiten antworten `202` mit `job_id`,
Fortschritt kommt über den Ereignisstrom der Sitzung (Erweiterung von
`GET /api/browser/stream/{session_id}`, `lib/api/browser_bus.py`).

### 5.1 REST

| Methode, Pfad | Zweck | vorhanden? |
|---|---|---|
| `GET /documents/sources` | `[{key,label,writable}]`, lokal zuerst | neu |
| `GET /documents/{source}/list?folder=&q=` | Zeilen (ein Schema für lokal und Quelle) | neu |
| `POST /documents/local/folders` `{parent,name}` | Ordner anlegen | neu |
| `POST /documents/local/rename` `{folder,old,new}` | Umbenennen, Index folgt | neu |
| `POST /documents/local/delete` `{folder,names[],folders[],from_disk,from_index}` | Einzeln, Ordner, Auswahl in einem | neu |
| `POST /documents/{source}/upload?folder=` (multipart, gestreamt) | Upload; lokal startet danach einen Index-Job | neu |
| `POST /documents/{source}/index` `{paths[]\|ids[], recursive}` → `202 {job_id}` | Indexieren | neu |
| `POST /documents/{source}/deindex` `{paths[]\|ids[]}` | aus dem Index | neu |
| `GET /documents/local/preview?path=` | Text, als Strom | neu |
| `GET /documents/source/{key}/file/{id}` | Datei einer Quelle | **vorhanden, passt** |
| `GET /_upload/documents/<pfad>` | lokale Datei | **vorhanden (Mount)**, später in den Server |
| `GET /browse/{scope}?path=&sort=` / `POST /browse/{scope}/folders` / `POST /browse/{scope}/symlinks` | Dateiauswahl; `scope` legt der Server fest (z. B. `audio_sources` → Wurzel und Fähigkeiten aus der Plugin-Einstellung) | neu |
| `GET /sessions/{id}/export.html?download=1` | Export als Datei (`Content-Disposition`) | neu |
| `POST /sessions/{id}/share` → `{url}` | Export nach `html_preview` (ohne Cookie) | neu |
| `GET /tts/list?lang=` | Zeilen wie `tts_escalation_rows` | neu; `GET /tts/entries` (Dienst-Token) aus derselben lib-Funktion bauen |
| `POST /tts/list/move` `{index,delta}`, `PATCH /tts/list/{index}` `{enabled}`, `PUT /tts/units/{engine}` `{unit}` | Liste ändern | neu |
| `POST /tts/status/refresh` | Katalog frisch | neu |
| `GET /tts/engines?lang=`, `GET /tts/engines/{engine}/voices` | Auswahlen (Schlüssel + Beschriftung) | neu |
| `GET/PUT /agents/{id}/tts/{engine}` `{voice,speed,pitch}`, `PUT /agents/{id}/tts` `{enabled,language}` | Stimme, Stummschalten, Sprache je Agent | neu |
| `GET/PUT /narrator/voices/{engine}` | Erzählerstimme | neu |
| `PATCH /settings` `{enable_tts, tts_autoplay, show_transcription}` | Schalter | **vorhanden** (nur `enable_tts`), erweitern |
| `GET /stt/status`, `PUT /stt/device` `{device}` | STT-Tab | neu |
| `POST /stt/transcribe?session_id=&source=mic\|file&mode=edit\|send` (multipart) → `202 {job_id}` | Diktat und Audio-Datei; bei `mode=send` startet der Server den Zug selbst, ohne Warteschlange und Abholer | neu |
| `POST /stt/jobs/{id}/confirm` `{confirmed}` | Antwort auf lange Datei / „LLM entladen?“ | neu |
| `GET /agents/export?ids=`, `POST /agents/import/peek`, `POST /agents/import` | Bundles | **vorhanden, passt** |
| `POST /sessions/{id}/images/{image}/rotate` `{dir}`, `…/crop` `{x,y,width,height}` | Zuschnitt | neu (Inventar ImageMixin) |
| `GET /audio/sources`, `POST /audio/sources` `{path}`, `DELETE /audio/sources/{label}`, `POST /audio/sources/{label}/index` `{force}`, `DELETE /audio/sources/{label}/index`, `PUT /audio/settings` | Audio-Einstellungen; besser als Router des Plugins | neu |

### 5.2 Ereignisse (SSE je Sitzung)

| Art | Inhalt | Ersetzt |
|---|---|---|
| `documents_changed` | `{source, folder}` | `doc_refresh` von Hand nach Schreiben durch Agenten, andere Tabs |
| `job_progress` | `{job_id, kind: index\|audio_index\|stt, current, total, name}` | `document_upload_status`, `audio_settings_busy` |
| `job_done` | `{job_id, kind, ok, summary, failed[]}` | Abschluss-Toasts |
| `toast` | `{kind, message}` | `rx.toast` aus Handlern, `take_tts_notices` im Abholer (base:370-374) |
| `stt_confirm` | `{job_id, reason: long\|gpu_busy, minutes, filename}` | `window.confirm` per `call_script` |
| `stt_result` | `{job_id, target: input\|bubble\|workspace\|sent, text?}` | JS in die Textarea, Warteschlange |
| `settings_changed` | `{keys[]}` | 500-ms-mtime-Prüfung von settings.json, `tts_list_revision` |
| `service_status` | vorhanden (STT/TTS-Zustand) | – |
| `bubble_audio`, `tts*`, `media` | vorhanden (Wiedergabe) | – |

Neue Chat-Blasen (Transkript, gespeichertes Transkript) kommen über den Chat-Strom der Sitzung
(Inventar Chat).

---

## 6. Abhängigkeiten zu anderen Mixins

| Mixin | braucht | wird gebraucht von |
|---|---|---|
| DocumentMixin | `logged_in_user` (Auth), `ui_language`, `add_debug` (doc:22-26) | Eingabeleiste liest `is_uploading_document`; Diktat schreibt Transkripte in denselben Ordner (base:768) |
| FilePickerMixin | – | AudioPlayerMixin (`_picker_open_for`, Rückruf `audio_on_source_picked`) |
| ExportMixin | `ChatHistoryState` (`_chat_sub`), `current_session_title`, `session_id`, `user_name`, `add_debug` (exp:20-26) | Eingabeleiste (`input_sections.py:744,755`) |
| TTSConfigMixin | `agent_tuning`, `editor_agent_id`, `_save_settings`, `_write_settings_file`, `add_debug`; Vars hängen an `backend_type`, `llamaswap_revision` (ttsc:51-57, 90) | TTSStreamingMixin (`_tts_agent_enabled`, `tts_agents` → `_tts_streaming_mixin.py:145`), CalibrationMixin (`tts_escalation_rows`), AgentEditor (setzt `editor_tts_engine`, ruft `_load_editor_tts_settings`, `check_tts_status`, zählt `tts_list_revision`: `_agent_editor_mixin.py:290,425-426,1115-1116`), BackendMixin und SettingsMixin laden die Felder (`_backend_mixin.py:600-605`, `_settings_mixin.py:311-315,411`), `aifred.py:65,89` |
| STTSettingsMixin | `ui_language`, `_write_settings_file` | AgentEditor (`load_stt_settings`, `_agent_editor_mixin.py:421`) |
| Diktat (`_base.py`) | `show_transcription` (UIConfig), `current_user_input`, `_add_agent_panel`, `send_message` (Chat), `backend_type` (Backend), `session_id` | 500-ms-Abholer |
| lib → State | `lib/audio_channels/browser.py:73,275` und `lib/research/context_utils.py:176-177` lesen `state.enable_tts` per `getattr` | muss im Server aus settings.json kommen |

---

## 7. Auffälligkeiten

### 7.1 Umwege wegen Reflex (fallen mit Vue weg)

- **Login im Upload-Handler** (doc:416-420, base:428-432): nötig, weil `/_upload` jeden Token annimmt.
- **Diktat über Warteschlange und Abholer** (base:837-854, 268-278): Reflex bricht ab, was die
  Upload-Anfrage verkettet. Dasselbe gilt dem Kommentar nach für `doc_index_uploaded`, das aus der
  Upload-Anfrage verkettet wird (doc:506-507); ob Reflex 0.10 ein Hintergrund-Ereignis dabei mit
  abbricht, ist nicht geprüft.
- **Patch 9** ist upstream erledigt (Commit `20878bce`), nur der Wächtertest bleibt.
- **JS-Brücken:** Mikrofon → verstecktes Upload-Input per `DataTransfer` (custom.js:138-169); Text
  per `call_script` in die Textarea (base:866-870); `window.confirm` mit Rückruf (base:548-551,
  727-730); Crop-Koordinaten aus dem DOM (crop.py:103-118; der Ziehcode steht in `aifred.py:476ff.`,
  nicht in `custom.js`, wie `crop.py:1` sagt); Download über fetch → Blob (exp:136-146).
- **Rohes `<a>`** statt `rx.link` für Downloads (documents.py:12-16, 194-220, 324-339), weil der
  React-Router-Link den Pfad umschreibt.
- **`tts_list_revision`** als Zähler, damit abgeleitete Vars neu rechnen (ttsc:67-68; erhöht in
  ttsc:177,225,230, `_settings_mixin.py:315`, `_agent_editor_mixin.py:290,425`).
- **Beschriftung statt Schlüssel** in Auswahlfeldern, zurückübersetzt (ttsc:357-363, 436-441, 323;
  sttm:72-75). Vue sendet Schlüssel.
- **Netzwerk in berechneten Vars:** Katalog und Stimmen der TTS-Suite (ttsc:78-132, 248-291,
  394-423), synchron in der Ereignisschleife, nur durch den TTL-Cache entschärft;
  `editor_tts_engine_label` fängt den Ausfall, weil Reflex die Var beim Start kompiliert
  (ttsc:261-263).
- `set_doc_source(key: str | list[str])` wegen des Typs des Segmented Controls (doc:267-272); Dicts
  neu zuweisen, damit Reflex Änderungen sieht (ttsc:239, 446-447).

### 7.2 Toter Code (belegt mit Suche ohne Klammer über `aifred/`, `assets/`, `tests/`)

- `document_manager_open` (doc:31,127,132): nur geschrieben.
- `tts_pitch` (ttsc:63): nur gespeichert und geladen (`_settings_mixin.py:203`, `_backend_mixin.py:605`);
  die Tonhöhe gilt je Agent über `_editor_tts_settings`.
- `narrator_plugin_enabled` (ttsc:388-392): kein Leser.
- `refresh_stt_settings` (sttm:62-65): kein Aufrufer. `load_stt_settings` ruft nur State-Code auf
  (`_agent_editor_mixin.py:421`), müsste nach Projektregel privat sein; ihr Docstring („kein
  Event-Decorator“, sttm:49-50) widerspricht dem Dekorator in sttm:47.
- `doc_index_file`: `asyncio.sleep(4)` und erneutes Leeren des Status (doc:677-680), der seit doc:673
  schon leer ist.
- `crop_box_x/y/width/height`, `crop_rotation` (`_image_mixin.py:51-55`): nur geschrieben.
- `file_browser.delete_entry` (`lib/file_browser.py:219`): kein Aufrufer; `can_delete` wird gesetzt
  (`_audio_player_mixin.py:426`), der Picker hat keinen Löschknopf; `picker_show_hidden` ohne Setter.
- `lib/api/audio.py:14` `_audio_active`: kein Leser; `/api/audio/test` (audio.py:177-217): Testseite
  ohne Aufrufer.
- `lib/api/core.py:218-219` `{"tts_voice": "voice"}`: `SettingsUpdate` hat kein `tts_voice`
  (core.py:92-93); der Standardwert `"voice"` in `lib/config.py:129` hat keinen Leser.

### 7.3 Doppelte Logik

- **Audio-Quellen dreifach:** `lib/audio_player_settings.build_configured_source_map` (44-57) ist die
  SSOT; `lib/api/audio.py:17-49` und `_audio_player_mixin.py:200-224` bauen sie mit eigenem festen
  Pfad zur Plugin-Datei nach; `_audio_picker_root` (389-406) und `audio_save_picker_root` (464-494)
  lesen/schreiben die Plugin-Datei an `load_audio_player_settings` vorbei.
- **TTS-Zeilen zweimal:** `tts_escalation_rows` (ttsc:89-132) und `GET /api/tts/entries` (tts.py:47-58).
- **TTS-Felder laden an drei Stellen:** `_backend_mixin.py:600-605` (alle sechs),
  `_settings_mixin.py:311-315` (ohne `tts_playback_rate`, `tts_pitch`), `_settings_mixin.py:411`
  (nur `enable_tts`).
- **Bundles zweimal:** State (`_memory_browser_mixin.py:428-551`) und API (`lib/api/agents.py:199-275`);
  die API-Wege zum Import ruft niemand auf. Der State hält das ZIP als Base64 im öffentlichen Feld
  `bundle_import_uploaded_b64` (`_memory_browser_mixin.py:85`), das damit an den Browser geht.
- **Sprache „auto“ → „de“** zweimal: ttsc:27-28 und exp:64-68.
- **Toasts:** `lib/toast.py` ist die SSOT für Dauer und Position; DocumentMixin und Diktat setzen beides
  selbst (z. B. doc:239-242, 505, 594; base:445, 461-465).
- **Audio-Endungen** fest an zwei Stellen: base:457 und `input_sections.py:301`.
- **Export-Dateiname** zweimal bereinigt: exp:100-101 und `lib/formatting.py:636-641`.

### 7.4 Fallen und Fehler

- **„Ordner indexieren“ bricht ab (Befund aus dem Code, nicht im Lauf geprüft).** `doc_index_folder`
  gibt jede Datei unter dem Ordner an `fm.index_file` (doc:616-622, kein Filter nach `PARSERS`);
  `index_document` wirft `ValueError` für Endungen ohne Parser und für leere Dateien
  (`lib/document_store.py:587-594`); `_index_files` fängt nicht (doc:565). Im Dokumentenordner der
  Produktion liegen `.json`, `.html`, `.png` (Zählung der Endungen bis Tiefe 3: 43, 10, 2) → der Lauf
  endet beim ersten solchen, ohne Abschluss-Toast, die Fortschrittszeile bleibt stehen. Die
  Quellen-Variante filtert (doc:646-649), `_index_temp_copy` fängt (fm:554-563). Gleiches gilt für
  `doc_index_file` einer solchen Datei; die lokale Zeile zeigt den Index-Knopf für jede Datei
  (documents.py:175), die Quellen-Zeile nur bei `indexable` (documents.py:316-321).
- **Blockierte Ereignisschleife:** `_refresh_file_list` → `list_directory` fragt ChromaDB und zählt per
  `rglob` je Unterordner, synchron (fm:131,152), aus fast jedem Handler, auch direkt nach dem
  ausgelagerten Umbenennen (doc:399); `preview_document` parst synchron (doc:934); der Export liest alle
  Bilder und Audios und schreibt synchron (exp:173,248; `formatting.py:647-648`). Die Quelle macht es
  richtig (doc:243).
- **Ganze Datei im Speicher:** `await file.read()` bei `AUDIO_UPLOAD_MAX_MB = 2048`
  (`lib/config.py:1328`); geprüft wird erst danach (base:469-480).
- **Verlorene Änderungen in settings.json:** `_save_settings` schreibt den Schnappschuss des Tabs
  (`enable_tts`, `tts_autoplay`, `tts_agents`, `narrator_voices`, `tts_playback_rate`, `tts_pitch`,
  `_settings_mixin.py:198-204`); ein Tab mit älterem Stand überschreibt, was ein anderer vor dem
  nächsten Abgleich geändert hat. Liste, Einheit, Stimmen dagegen per Lesen-Ändern-Schreiben
  (ttsc:168-176, 345-351). Im Server: eine Stelle, die je Schlüssel schreibt.
- **Wirkung nur im Browser:** `tts_agents` (Stummschalten, Sprache je Agent) liest nur der Browser-Strom
  (`_tts_streaming_mixin.py:145`); lib und die Kanäle lesen es nirgends.
- **STT-Meldungen überholt:** Schätzung und Texte nehmen immer GPU an (`WHISPER_RTF_GPU`,
  `engine="GPU"`, base:507-547, 665, 675), obwohl `transcribe_audio_auto` dem `stt_device` folgt und
  bei `auto` Dateien über 10 MB auf die CPU schickt (`audio_processing.py:1093-1095`); die Kommentare
  base:489-491, 693-695 beschreiben den alten Ablauf. `_unload_llm_for_stt` (base:624-651) entlädt
  llama-swap bzw. Ollama direkt statt über GPU-Orc (`lib/gpu_orc_client.py`).
- `stt_set_device`: `next(...)` ohne Standard (sttm:72-75) → `StopIteration` bei unbekannter
  Beschriftung; `set_editor_agent_tts_language` fällt still auf `"auto"` (ttsc:323).
- **Export:** `html_preview` ohne Cookie-Pflicht (bewusst zum Teilen, `aifred.py:1244-1250`); der
  Dateiname kommt aus dem Titel (`formatting.py:636-641`), gleiche Titel überschreiben sich, die URL
  ist erratbar. Die LRU-Liste lebt nur im Speicher (`formatting.py:25,650-661`): nach einem Neustart
  werden alte Dateien nie entfernt; Docstring sagt 50 (formatting.py:623), Konfiguration 200
  (`config.py:1725`). Beschriftungen DE/EN fest im Code (exp:325-336, 588-589), Quellen-Erkennung
  über den Text „Web-Quellen“ (exp:212-214).
- **Picker-Rückruf per Name** (pick:239-249): der einzige Rückruf `audio_on_source_picked` ist ein
  öffentliches Ereignis (`_audio_player_mixin.py:500-501`), jeder Client kann es mit eigenem Pfad
  auslösen; geschützt nur durch die eigene Sandbox-Prüfung (`_audio_player_mixin.py:525-534`). Im
  Server-Entwurf legt der Server den Bereich fest, und die Auswahl geht an den Endpunkt des Aufrufers.
- **Feste deutsche Texte ohne i18n:** `ui/file_picker.py` (z. B. 99, 109, 169, 318, 346),
  `ui/audio_settings.py` (z. B. 48, 85, 340, 508), `ui/modals/crop.py:87,95`, Diktat-Toasts
  (base:451, 852, 878), `custom.js:107`.
- **Vorschau ohne Abschnitte:** der ganze Text geht in ein State-Feld (doc:936); in Vue als Strom
  laden, ohne künstliche Grenze.
