# Bestandsaufnahme: Vision, Casus, Vigilantia, Audio-Player

Stand 07.11.2026, Zweig `vue-frontend`. Nur gelesen, kein Code geändert. Teil von Schritt 1 des
Umbaus (siehe `README.md` daneben).

Kürzel für die Belege (Datei:Zeile):

| Kürzel | Datei |
|---|---|
| VS | `aifred/state/_vision_settings_mixin.py` (1127 Zeilen) |
| VP | `aifred/state/_vision_preview_mixin.py` (1050) |
| CA | `aifred/state/_casus_mixin.py` (769) |
| MP | `aifred/state/_multipose_mixin.py` (388) |
| VF | `aifred/state/_vigilantia_feed_mixin.py` (144) |
| AP | `aifred/state/_audio_player_mixin.py` (553) |
| uVP, uVS, uCA, uVF, uMP | `aifred/ui/vision_preview.py`, `vision_settings.py`, `casus.py`, `vigilantia_feed.py`, `multipose.py` |
| aV, aA, aB | `aifred/lib/api/vision.py`, `audio.py`, `browser_bus.py` |
| BC | `aifred/lib/audio_channels/browser.py` |
| SSE.js | `assets/vlm_sse_manager.js` |
| cj | `assets/custom.js` |

---

## 1. State-Felder je Mixin

### 1.1 VisionSettingsMixin (Vigilantia-Einstellungen, Auge, Kameras, Alarmregeln, Beschreiber)

| Feld | Typ | Zweck |
|---|---|---|
| `vision_settings_open` | bool | Modal offen (VS:103) |
| `vision_event_interval_options` | list[dict] | feste Auswahl 1–10 s „Min. Event-Abstand“ (VS:109) |
| `vision_mode_value` | str | `on-demand` / `live` (Verweildauer des VLM) (VS:116) |
| `vision_model_value` | str | VLM-Name; auch Dropdown im Vorschau-Kopf (VS:117) |
| `vision_available_models` | list[str] | llama-swap-Modelle mit `-visiond` + Ollama-VLMs (VS:118, 312–360) |
| `vision_describer_rows` | list[dict] | Beschreiber-Liste: index, source, label, enabled, note, warn, remote; Texte schon übersetzt (VS:121, 210–255) |
| `vision_describer_measuring` | str | Quelle, die gerade vermessen wird (VS:123) |
| `face_recognition_enabled`, `face_recognition_continuous` | bool | Gesichtserkennung an / unabhängig von Bewegung (VS:127, 133) |
| `person_detect_enabled` | bool | YOLO-Personen bei Bewegung (VS:138) |
| `face_retention_days` | int | Aufbewahrung Crops/Frames/Events (VS:141) |
| `vigilantia_armed` | bool | Hauptschalter „Auge“ (VS:146) |
| `vigilantia_sources` | list[dict] | Kamerakarten: id, label, alias, hardware_name, is_rtsp, available, auto_start, alerts_enabled, alert_types, quiet_*, schedule_*, resolution, event_interval (VS:151, 493–517) |
| `vigilantia_has_armed_source` | bool | mindestens eine Quelle mit auto_start (VS:157) |
| `vlm_model_loaded`, `vlm_model_busy` | bool | Power-Knopf (VS:162–164) |
| `rtsp_form`, `rtsp_form_open`, `rtsp_form_editing`, `rtsp_form_error` | dict/bool/str/str | RTSP-Formular (VS:171–175) |
| `alert_rules_ui` | list[dict] | eine Zeile je Kategorie: category, label_key, sinks, vlm (VS:180) |
| `alert_channels` | list[dict] | Kanal-Plugins name/display_name (VS:183) |
| `vision_plugin_enabled` (berechnet) | bool | Plugin an; Abhängigkeit über `tool_plugin_toggles` als Stellvertreter (VS:465–471) |

Private Felder: keine. Modulkonstanten `_DEFAULT_ALERT_TYPES`, `_ALERT_RULE_CATEGORIES` (VS:53–65).

### 1.2 VisionPreviewMixin (Live-Ansicht im eigenen Fenster)

| Feld | Typ | Zweck |
|---|---|---|
| `vision_preview_sources` | list[dict] | Quellen: id, label (mit „✗“), alias, hardware_name, available, resolution, resolution_options, ptz, prompt_context, auto_start, motion_min_area_ratio (VP:115, 881–895) |
| `ptz_held` | str | Quelle mit gehaltener Pfeiltaste; schaltet den Herzschlag (VP:119) |
| `vision_preview_focus` | str | Quelle im Vollbild (VP:122) |
| `vision_preview_photos` | dict[str, list[str]] | Foto-URLs dieser Sitzung (VP:127) |
| `ptz_speed` | str | fine/medium/fast (VP:130) |
| `vision_preview_visible_sources` | list[str] | sichtbare Quellen (VP:133) |
| `vision_preview_resolutions` | dict[str, str] | Auflösung je Quelle; liest nur State-Code, doppelt zu `sources[].resolution` (VP:136, 807–809) |
| `vision_preview_fps` | float | Bildrate, 0 = Einzelbild (VP:139) |
| `vision_preview_teleprompter_mode` | str | overlay / below (VP:144) |
| `vision_preview_cache_buster` | int | Zähler in den Bild-URLs (VP:147) |
| `vision_preview_watching`, `vision_preview_face_active` | list[str] | Bild-Analyse bzw. Gesichtserkennung je Quelle an (VP:152, 158) |
| `vision_preview_vlm_cooldown_sec`, `vision_preview_face_throttle_sec` | float | Takt der VLM-Aufrufe / Gesichts-Events (VP:163, 168) |
| `vision_preview_status` | str | Meldezeile (VP:210) |
| `vision_preview_cooldown_options`, `_face_throttle_options`, `_fps_options` | list[dict] | feste Auswahllisten, teils deutsche Texte (VP:175, 188, 216) |
| berechnet | | `*_vlm_cooldown_value`, `*_face_throttle_value`, `*_fps_value`, `*_is_manual_mode`, `*_visible_entries` (baut `image_url`), `*_has_visible` (VP:197–301) |

Private Felder: keine.

### 1.3 CasusMixin (Ereignisverwaltung)

| Feld | Typ | Zweck |
|---|---|---|
| `casus_open`, `casus_help_open` | bool | Modal / Hilfe offen (CA:48, 103) |
| `casus_events` | list[dict] | aktuelle Seite (50): id, event_type, date_display, time_display, source_name, crop_url, frame_path, has_zoom, face_name, matched_name, description, cluster_id, cluster_member_count (CA:50) |
| `casus_total_count`, `casus_page` | int | Gesamtzahl, Seite; `casus_page` liest nur State-Code (CA:51–52) |
| `casus_all_ids` | list[int] | alle IDs der Filtermenge für die Diaschau; öffentlich, wird also komplett zum Client geschickt (CA:56) |
| `casus_filter_source`, `_type`, `_face` | str | Filter (CA:58–60) |
| `casus_source_options`, `casus_face_options` | list[dict] | Dropdowns, deutsche Texte (CA:62–63, 651–669) |
| `casus_status` | str | Meldezeile (CA:65) |
| `casus_tag_event_id`, `casus_tag_face_id` | int/str | Zuordnen einer Zeile (CA:68–69) |
| `casus_confirm_delete_all` | bool | zweistufiges Löschen (CA:78) |
| `casus_analyzing_event_id`, `casus_expanded_event_id` | int | Einzelanalyse läuft / Text aufgeklappt (CA:81, 84) |
| `casus_image_index`, `casus_image_scale_wide`, `casus_image_scale_zoom` | int | Bild-Modal, Zoomstufe (CA:89, 477–478) |
| `casus_bulk_running`, `_total`, `_progress`, `_message`, `_cancel` | bool/int/int/str/bool | Massenanalyse (CA:92–96) |
| `casus_cluster_mode` | bool | Gruppiert (CA:100) |
| `casus_type_values` | list[str] | Typfilter-Werte (CA:766) |
| berechnet | | `casus_page_label`, `_has_prev`, `_has_next`, `_image_open`, `_image_src`, `_image_zoom_src` (DB-Abfrage im berechneten Feld), `_image_counter`, `_image_at_newest`, `_image_at_oldest` (CA:115–131, 427–504) |

Private Felder: `_casus_last_seen_event_id`, `_casus_last_poll_ts` (CA:72–73).

### 1.4 MultiposeMixin (Einlernen mit mehreren Posen)

| Feld | Typ | Zweck |
|---|---|---|
| `multipose_open` | bool | Modal offen (MP:42) |
| `multipose_target_face_id` | int | 0 = neue Person (MP:44); nur über berechnete Felder gelesen |
| `multipose_name`, `multipose_source_id` | str | Name, Kamera (MP:45–46) |
| `multipose_step` | int | Pose 0–5 (MP:49) |
| `multipose_captures` | list[dict] | je Aufnahme key, preview_data_url (Base64-JPEG), embedding_b64, detection_score; alles öffentlich (MP:52) |
| `multipose_preview_data_url` | str | letzte Aufnahme als Data-URL; von keiner Oberfläche gelesen (MP:56) |
| `multipose_live_preview_url` | str | Live-Bild als Base64-Data-URL (MP:59) |
| `multipose_status`, `multipose_busy` | str/bool | Meldung, Aufnahme läuft (MP:60–61) |
| `multipose_source_options` | list[dict] | Kameras (MP:63) |
| berechnet | | `multipose_is_existing`, `_progress_label`, `_current_pose_key`, `_finished`, `_can_save` (MP:67–94) |

Modulkonstante `POSE_STEPS` (MP:31–37).

### 1.5 VigilantiaFeedMixin (Live-Karte in der Modus-Zeile)

| Feld | Typ | Zweck |
|---|---|---|
| `vigilantia_feed_visible` | bool | Karte einblenden; wird nirgends gespeichert (VF:46) |
| `vigilantia_feed_open` | bool | ungenutzt (VF:48) |
| `vigilantia_feed_popover_open` | bool | Popover offen, gesteuert (VF:52) |
| `vigilantia_feed_events` | list[dict] | letzte 10 Ereignisse (VF:55) |
| `vigilantia_feed_recent_count` | int | Ereignisse der letzten 10 min (VF:57) |
| `vigilantia_help_open` | bool | Hilfe (VF:60) |

Modulkonstanten `_BADGE_WINDOW_MIN = 10`, `_FEED_LIMIT = 10` (VF:26–28).

### 1.6 AudioPlayerMixin (Medien im Browser, Audio-Einstellungsseite)

| Feld | Typ | Zweck |
|---|---|---|
| `media_audio_url`, `media_state_key`, `media_is_stream` | str/str/bool | aktuelles Medium; landen als `data-media-*` am `<audio>` (AP:70–72, aifred.py:69–79) |
| `media_paused_for_tts`, `media_pause_pos_sec` | bool/float | Pause wegen Sprachausgabe, Position (AP:73–74) |
| `media_paused_by_user` | bool | vom Benutzer pausiert (AP:78); liest nur der tote Handler `resume_media_after_tts` |
| `media_queue` | list[dict] | Ordner-Wiedergabe {audio_url, state_key} (AP:84) |
| `media_queue_json` (berechnet) | str | JSON für `data-media-queue` (AP:172–176) |
| `audio_settings_open` | bool | wird gesetzt, nie gelesen (AP:87, 267, 303) |
| `audio_sources_view` | list[dict] | Quellen: label, type, target, is_symlink, indexed, is_stream (AP:88, 229–237) |
| `audio_settings_busy`, `_busy_source`, `_status` | str | Index-Lauf, Meldung (AP:89–91) |
| `audio_picker_root_input`, `audio_tts_list_max_input` | str | Eingabefelder (AP:92–93) |
| `audio_settings_help_open` | bool | Hilfe (AP:241) |

Modulglobal: `_audio_runtime_state` (AP:33), Schnappschuss der `media_*`-Felder je `session_id`; überlebt Tab-Reload, nicht Neustart. Konstante `_AUDIO_SOURCE_PICKER_ROOT_DEFAULT = "/mnt"` (AP:387).

---

## 2. Event-Handler und interne Methoden

### 2.1 VisionSettingsMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `open_vision_settings` / `close_…` | lädt alles neu, öffnet (VS:362–372) | über `_refresh_vision_settings`, `_reload_alert_rules` | – |
| `_refresh_vision_settings` | Settings, Quellen, Modelle, Beschreiber laden (VS:185–208); auch beim Login (`_backend_mixin.py:597`) und beim Öffnen der Vorschau (VP:362) | `vision_settings.load_vision_settings`, siehe unten | – |
| `_refresh_describer_list` | Zeilen inkl. übersetzter Hinweise bauen (VS:210–255) | `describer_list.matched/stored`, `remote_describers.overview/split` (HTTP), `vlm_vram_cache.get_any/measured_at`, `i18n.t`, `formatting.format_number/format_stamp` | – |
| `move_describer`, `set_describer_enabled` | Reihenfolge/Schalter (VS:266–281) | `describer_list.save` (über `_edit_describer_list`, VS:257–264) | settings.json `describers` |
| `measure_remote_describer` (background) | Einbrennen auf fernem Rechner (VS:283–310) | `remote_describers.remote_model`, `vlm_stress_prewarm.stress_prewarm_remote` | VRAM-Cache (lib) |
| `_discover_vision_models`, `rescan_vision_models` | Modell-Liste (VS:312–360, 1124–1127) | `calibration.llamaswap_io.parse_llamaswap_config`, `ollama_models.list_ollama_vlm_models` (HTTP), `vision_routing._normalize` (privat!) | – |
| `set_vision_mode_value` | Modus; bei `live` sofort laden (VS:374–390) | `vision_prewarm.prewarm_vlm` | settings.json `vision_mode` |
| `set_vision_model_value` | Modell wechseln, altes Ollama-Modell entladen (VS:392–412) | `vision_routing.visiond_profile_for`, `vision_prewarm.unload_vlm_model` | settings.json `vlm.model` |
| `set_face_recognition_enabled/_continuous`, `set_face_retention_days` | Schalter (VS:414–451) | – | settings.json `face_recognition.*` |
| `_reload_vigilantia_sources` | Kamerakarten bauen (VS:473–529); auch aus dem 500-ms-Takt, solange die Liste leer ist (VF:107–108) | `frame_sources.list_all` + `src.info()` (Kamera-Probe), `VisionStore.get_source` | – |
| `set_vigilantia_source_auto_start` | Hintergrund je Kamera; wenn scharf sofort starten/stoppen (VS:572–599) | `VisionStore.update_source_fields`, `vision_autostart.start_background_watcher`, `vision_watcher.get_default_watcher().stop` | vision.db `sources.auto_start` |
| `set_vigilantia_source_alias` | Kameraname, zieht beide Listen nach (VS:601–631) | `_persist_source_alias` (VP) | vision.db `settings.alias` |
| `set_vigilantia_source_alerts`, `set_vigilantia_alert_type`, `set_vigilantia_quiet`, `set_vigilantia_event_interval` | Alarm-Feinfilter je Kamera (VS:651–740) | `_persist_source_setting` (VS:633–649) | vision.db `settings.*` |
| `set_vigilantia_schedule` | Zeitfenster, sofort anwenden (VS:742–799) | `vision_autostart.schedule_minutes`, `_schedule_active_now` (privat!), `start_background_watcher`, `watcher.is_running/stop` | vision.db `settings.schedule_*` |
| `open_zone_editor` | öffnet `/api/vision/zone-editor` per `rx.call_script` und `window.open` (VS:545–570) | – | localStorage Fenstergröße |
| `_reload_alert_rules`, `set_alert_rule`, `toggle_alert_sink` | Regeln je Kategorie (VS:803–882) | `plugin_registry.all_channels`, `plugin_base.plugin_display_name`, `alert_bus.reload_rules` | `data/alert_rules.json` |
| `open_rtsp_camera_new/_edit_by_source`, `close_rtsp_camera_form`, `set_rtsp_form_field` | Formular (VS:898–939) | `frame_sources.rtsp_source.find_camera_config` | – |
| `save_rtsp_camera`, `delete_rtsp_camera` | Kamera anlegen/ändern/löschen (VS:956–1041) | `dotenv.set_key`, `credential_broker.broker.set_runtime/get_env_key`, `frame_sources.rescan` | settings.json `rtsp_cameras`, `.env` `RTSP_<CRED>_USER/PASSWORD` |
| `toggle_vigilantia_armed` | Auge scharf/unscharf (VS:1043–1069) | `plugin_registry.is_plugin_enabled`, `vision_autostart.start_all/stop_all_background_watchers` | settings.json `vigilantia_armed` |
| `force_disarm_vigilantia` | beim Abschalten des Plugins (VS:1071–1084), gerufen aus `_settings_mixin.py:995` | `stop_all_background_watchers` | settings.json |
| `_refresh_vlm_loaded`, `toggle_vlm_model_loaded` | Power-Knopf (VS:1086–1122) | `vision_prewarm.is_vlm_loaded/prewarm_vlm/unload_vlm_model` | – |

### 2.2 VisionPreviewMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `open_vision_preview` | `window.open('/vision-preview-popup', 'aifred-cam')` per `rx.call_script` (VP:305–328) | – | – |
| `on_load_vision_preview` | Seite laden: Fotos/Fokus leeren, Einstellungen, VLM-Status, Quellen; spritzt `vlm_sse_manager.js` und die Briefing-Texte per JS ins DOM (VP:330–423) | `i18n.TranslationManager._translations` (privat) | – |
| `toggle_vision_preview_source` | Quelle sichtbar (VP:425–443) | – | `data/vision_preview.json` `visible_sources` |
| `set_vision_preview_prompt_context` | Briefing je Kamera, beim Verlassen des Feldes (VP:446–461) | `VisionStore.update_source_fields` | vision.db `sources.prompt_context` |
| `set_vision_preview_resolution` | Auflösung (VP:469–488) | `_persist_source_setting` | vision.db `settings.resolution` |
| `toggle_vision_preview_watch`, `toggle_vision_preview_face_recognition` → `_apply_watch_mode` | baut `WatchConfig` aus den Schaltern, Profil-Overrides, stoppt und startet den Watcher (VP:490–618) | `vision_watch_types.WatchConfig`, `vision_autostart.profile_watch_overrides`, `VisionStore.get_source`, `watcher.stop/start` | – (nur Session-State) |
| `set_vision_preview_vlm_cooldown`, `_face_throttle`, `_teleprompter_mode`, `_fps` | globale Vorschau-Einstellungen (VP:620–710) | – | `data/vision_preview.json` |
| `clear_vlm_teleprompter` | ruft `window.clearVlmTeleprompter` (VP:651–666) | – | – |
| `toggle_vision_preview_focus` | Vollbild an/aus (VP:712–716) | – | – |
| `take_vision_photo` | Foto in voller Auflösung, öffnet danach die Lightbox per Script (VP:718–736) | `vision_photo.take_photo` | `data/vigilantia/photos/<Quelle>/<Datum>/` |
| `ptz_press`, `ptz_release`, `ptz_heartbeat`, `ptz_home`, `set_ptz_speed` | Kopfsteuerung (VP:737–787) | `ptz_control.ptz_driver.press/release/keep_alive/home` | – |
| `refresh_vision_preview`, `rescan_vision_preview_sources` | Einzelbild neu, Quellen neu suchen (VP:789–799) | `frame_sources.rescan` | – |
| `_refresh_sources` | Quellenliste, Auflösungen, Alias, Briefing, sichtbare Quellen (VP:803–924) | `frame_sources.list_all`, `src.info()`, `src.detect_resolutions()`, `VisionStore.get_source`, `ptz_control.get_controller` | – |
| `_load_preview_fps`, `_persist_preview_setting`, `_load_persisted_visible_sources` | JSON lesen/schreiben (VP:930–1000) | – | `data/vision_preview.json` |
| `_persist_source_prompt_context`, `_persist_source_alias`, `_persist_source_resolution` | Store schreiben (VP:1002–1050) | `VisionStore` | vision.db |

### 2.3 CasusMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `open_casus`, `close_casus`, `open/close_casus_help` | Modal, VLM-Status (CA:105–155) | `vision_prewarm.is_vlm_loaded` | – |
| `casus_set_filter_*`, `casus_toggle_cluster_mode`, `casus_clear_filters`, `casus_prev/next_page`, `casus_refresh` | Liste neu laden (CA:159–196, 390–400, 581–587) | über `_refresh_events` | – |
| `_refresh_events` | Seite holen; gruppiert: bis 5000 Ereignisse in Python nach `cluster_id` bündeln (CA:671–760) | `VisionStore.list_events_with_summary/count_events/list_event_ids` | – |
| `_resolve_casus_filters` | Filter → Abfrageparameter (CA:346–363) | – | – |
| `casus_analyze_event` | VLM-Beschreibung eines Ereignisses (CA:200–221) | `vision_event_analysis.analyze_event_with_vlm` | vision.db `classification.description` (lib) |
| `casus_bulk_start` (background), `casus_bulk_cancel_run` | alle ohne Beschreibung, Fortschritt und Abbruch über Rückrufe (CA:225–304) | `vision_bulk.run_bulk_describe(progress_cb, cancel_cb, check_vram=True)` | vision.db (lib) |
| `casus_request/cancel_delete_all`, `casus_confirm_delete_all_now` | Löschen nach Filter (CA:308–386) | `VisionStore.delete_events_filtered` | vision.db + Dateien |
| `casus_delete_event` | ein Ereignis (CA:404–419) | `VisionStore.delete_event` | vision.db + Dateien |
| `casus_start/cancel_tag`, `casus_set_tag_face`, `casus_save_tag` | Person zuordnen/lösen (CA:589–636) | `VisionStore.set_event_face_id` | vision.db `events.face_id` |
| `casus_show_image_at`, `casus_open_film`/`_open_film`, `casus_image_newer/older`, `casus_close_image`, `casus_image_cycle_scale`, `casus_toggle_expand` | Bild-Modal, Diaschau, „Film“ eines Clusters (CA:421–579) | `VisionStore.get_event/list_cluster_event_ids` | – |
| `_casus_poll_new_events` | aus dem 500-ms-Takt, gedrosselt auf 2 s: `max(id)` prüfen, bei Neuem neu laden (CA:319–344; `_base.py:240–241`) | `VisionStore.latest_event_id` | – |
| `_refresh_filter_options` | Dropdowns (CA:640–669) | `VisionStore.list_event_source_ids/list_faces` | – |

### 2.4 MultiposeMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `open_multipose(face_id, name)`, `close_multipose` | Modal (MP:98–124) | über `_refresh_multipose_sources` | – |
| `multipose_live_tick` | alle 800 ms neues Live-Bild als Data-URL (MP:126–133; Takt uMP:271–279) | über `_fresh_frame` | – |
| `multipose_set_name`, `multipose_set_source` | Eingaben (MP:135–144) | – | – |
| `multipose_capture` | Bild holen, größtes Gesicht erkennen, Ausschnitt + Embedding in die Liste (MP:148–198) | `vision_snap.snap_frames(prefer_face=True)`, sonst `frame_hub.snapshot` (MP:314–329); `face_detect.get_default_detector().detect` | – (State) |
| `multipose_retry_current`, `multipose_skip_current` | Schritt zurück/weiter (MP:200–214) | – | – |
| `multipose_finish` | Person anlegen, Embeddings + Ausschnitte speichern, Erkenner neu laden (MP:218–268) | `VisionStore.get_or_create_face/add_embedding`, `face_crop_store.get_default_store().save_raw`, `face_recognize.bump_enrollment_epoch` | vision.db `faces`/`embeddings`, Crop-Dateien |
| `_refresh_multipose_sources` | Kameras (MP:272–301) | `frame_sources.list_all`, `VisionStore.source_labels` | – |
| `_make_preview_data_url` | Ausschnitt mit 30 % Rand per OpenCV (MP:360–388) | `cv2` | – |

### 2.5 VigilantiaFeedMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `open/close_vigilantia_help`, `set_vigilantia_feed_visible`, `refresh_vigilantia_feed`, `open/close_vigilantia_feed_popover`, `handle_vigilantia_feed_popover_change` | Karte und Popover; Schließen nur über X/ESC (VF:62–100) | über `_refresh_vigilantia_feed` | – |
| `_refresh_vigilantia_feed` | letzte 10 + Zählung 10 min; läuft im 500-ms-Takt, solange die Karte sichtbar ist (VF:102–144; `_base.py:235–236`) | `VisionStore.list_events_with_summary/count_events` | – |

### 2.6 AudioPlayerMixin

| Handler / Methode | Wirkung | lib-Aufrufe | Speichert |
|---|---|---|---|
| `_persist_audio_state`, `_restore_audio_state`, `discard_audio_runtime_state` | Schnappschuss je Sitzung (AP:36–43, 99–138); gerufen aus BC und `_session_mixin.py:299, 461`, `_auth_mixin.py:182` | – | Prozessspeicher |
| `play_media`, `resume_media_after_tts` | ohne Aufrufer (siehe 7.2) (AP:140–196) | – | – |
| `_stop_media` | Medium leeren (AP:162–170); gerufen aus `_session_mixin.py:319, 618` | – | – |
| `open_audio_settings`, `on_load_audio_settings`, `close_audio_settings` | Seite `/audio-settings`, zurück zum Agenten-Editor (AP:243–305) | `audio_player_settings.load_audio_player_settings` | – |
| `_refresh_audio_sources_view` | Quellen + Indexzahlen (AP:200–239) | `audio_sources.build_source_map`, `audio_index.stats` | – |
| `audio_index_rebuild_source` (background) | Tags einlesen (AP:307–344) | `audio_index.scan_source` (Thread) | `data/audio_index.sqlite` |
| `audio_index_clear_source`, `audio_remove_source` | Index leeren, Quelle löschen (AP:346–380) | `audio_index.remove_source`, `shutil.rmtree`/`unlink` | Index, `data/media/audio/<label>` |
| `open_audio_source_picker`, `audio_on_source_picked` | Ordner wählen (Sandbox), Symlink anlegen (AP:408–433, 500–553) | `_picker_open_for` (FilePicker-Mixin) | Symlink unter `data/media/audio/` |
| `audio_save_picker_root`, `audio_save_tts_list_max` (+ Setter) | Plugin-Einstellungen (AP:435–498) | – | `plugins/tools/audio_player/settings.json` |

Die eigentliche Steuerung (spielen, pausieren, springen, Tempo, Ordner) liegt im Plugin `audio_player` und den Kanälen in `lib/audio_channels/`; der Browser-Kanal BC schreibt dafür direkt in den Reflex-State (`ctx.state.media_* = …`, BC:78–87, 126–129, 146–149, 170–177, 277–294) und schickt zusätzlich Ereignisse auf den Browser-Bus.

### 2.7 Wie Bilder, Texte und Takt in den Browser kommen

| Was | Weg | Beleg |
|---|---|---|
| Live-Bild | `<img src="/api/vision/stream/<id>?fps=…&cb=…&width=…">`, MJPEG (`multipart/x-mixed-replace`), gedrosselt im Generator | VP:284–287; aV:132–228 |
| Einzelbild-Modus | `<img src="/api/vision/snapshot/<id>?cb=<Zähler>">`, Neu-Laden über `vision_preview_cache_buster` | VP:284–285, 789–793; aV:22–52 |
| Zonen-Editor | eigenes HTML (`assets/zone_editor.html`), spricht nur REST: snapshot, stream mit `overlay=motion`, zone-mask, motion-min | aV:534–707 |
| Casus- und Feed-Vorschaubilder | `/api/vision/frame?id=…&w=80` bzw. `w=64`, Zoom `&zoom=1` | uCA:51–98; uVF:41; aV:486–522 |
| Gesichtsausschnitte, Fotos | statisch unter `/_upload/vigilantia/…` (`AuthenticatedStaticFiles`) | aifred.py:1236–1241; `lib/vision_photo.py:46` |
| Multipose-Live-Bild | Base64-JPEG im State, alle 800 ms über `rx.moment` | MP:303–312; uMP:271–279 |
| VLM-Text und Gesichter live | SSE `/api/vision/events/<id>` je Quelle; `vlm_sse_manager.js` sucht alle 2 s im DOM nach Zielen und öffnet je Quelle einen `EventSource` | aV:231–270; SSE.js:221–284 |
| Lebensdauer des Vorschau-Watchers | an die SSE-Abonnenten gekoppelt: 5 s nach dem letzten Abmelden `restore_or_stop_after_preview` | `lib/vision_event_bus.py:38, 91–124`; `lib/vision_autostart.py:250–278` |
| PTZ-Herzschlag | `rx.moment(interval=400 ms)` nur solange `ptz_held` = Quelle; Totmann-Stopp nach 1 s im Server | uVP:419–427; `lib/ptz_control.py:287–312`; `config.py:860–861` |
| Feed und Casus | kein eigener Takt; Huckepack auf `refresh_debug_console` (500 ms, `rx.moment` der Hauptseite) | `_base.py:216–241` |
| Medien | Browser-Bus SSE `/api/browser/stream/<session>` (Arten media/stop/pause/resume/seek/speed), dazu `data-media-*` am `<audio>`; Datei per Range von `/api/audio/file`; Position alle 30 s per POST `/api/audio/position` | aB:65–316; BC:89–102; cj:1740–2133; aA:52–240 |

### 2.8 Hintergrund-Tasks und Timer

- Reflex-Hintergrundereignisse: `measure_remote_describer` (VS:283), `casus_bulk_start` (CA:225), `audio_index_rebuild_source` (AP:307).
- Server-seitig, unabhängig vom State: Teardown-Task je Quelle im Event-Bus (`vision_event_bus.py:121`), Totmann-Timer der PTZ (`ptz_control.py:309`), `schedule_supervisor` (`vision_autostart.py:347–405`), Positions-Speicherer von mpv (`audio_manager.py:161`), `sync_audio_index_task` (`audio_index.py:557`).
- Client-Timer: 500-ms-Takt der Hauptseite, 800 ms Multipose, 400 ms PTZ, 2 s DOM-Scan in SSE.js, 30 s Positionsspeichern in cj:1761.

---

## 3. Was ist schon browserunabhängig, was steckt nur im State?

**Schon in lib bzw. über die API erreichbar (wiederverwenden):**

| Bereich | lib | API |
|---|---|---|
| Kamerazugriff | `frame_hub` (ein Leser je Quelle), `frame_sources` (Registry, rescan), `vision_snap` | snapshot, stream, frame |
| Überwachung | `vision_watcher` (start/stop/is_running/list_active/reload_zone_mask/reload_motion_min), `vision_autostart` (Hintergrund, Zeitplan, Profil-Overrides, Vorschau-Ende) | zone-mask, motion-min, zone-editor |
| Live-Ereignisse | `vision_event_bus` | SSE `/vision/events/<id>` |
| PTZ, Foto | `ptz_control.ptz_driver` (mit Totmann), `vision_photo.take_photo` | – |
| VLM | `vision_prewarm` (prewarm/unload/is_loaded), `vision_event_analysis`, `vision_bulk.run_bulk_describe` (mit Rückrufen, kopflos), `describer_list`, `remote_describers`, `vlm_stress_prewarm` | – |
| Gesichter | `VisionStore`, `face_crop_store`, `face_enroll`, `face_recognize.bump_enrollment_epoch` | face/list, details, rename, delete, embedding delete, enroll — alle ohne Aufrufer im Repo |
| Alarme | `alert_bus.load_rules/reload_rules` | – |
| Einstellungen | `vision_settings.load/save_vision_settings`, `audio_player_settings.load_audio_player_settings/build_configured_source_map` | – |
| Audio | `audio_manager` (mpv), `audio_index`, `audio_state`, `audio_sources` | file, position, Browser-Bus |

**Nur im State (muss für Vue in lib-Dienste):**

- Alle Setter der Vigilantia-Einstellungen mit ihren Folgen: Modus `live` lädt das VLM (VS:388–390), Modellwechsel entlädt das alte (VS:405–412), Auge startet/stoppt alle Watcher (VS:1064–1069), `auto_start` und Zeitfenster starten/stoppen sofort (VS:591–599, 774–799).
- Die Kamerakarte als Ganzes (VS:473–529) und die zweite Liste der Vorschau (VP:803–924): zwei Zusammenstellungen derselben Quelle.
- RTSP-Verwaltung inkl. `.env`-Schreiben (VS:941–1041).
- Alarmregeln lesen/schreiben (VS:68–88, 803–882); `alert_bus` kann nur lesen.
- Zusammenbau der Vorschau-`WatchConfig` (`_apply_watch_mode`, VP:490–588) — Domänenlogik.
- Casus: Filterauflösung, Gruppieren nach Cluster in Python (CA:686–727), Seitenbildung.
- Multipose komplett: Aufnahme, Erkennung, Ausschnitt, Embedding, Speichern (MP:148–268, 314–388).
- Audio-Quellen anlegen/löschen (Symlink, Sandbox-Prüfung, `rmtree`), Plugin-Einstellungen schreiben (AP:353–553).
- Der Player-Zustand des Browsers (`media_*`, `_audio_runtime_state`): lebt im Reflex-State, und der lib-Kanal BC schreibt hinein — lib hängt hier am State.
- `lib/toast.py` importiert Reflex (Zeile 12) und wird von VP:36 benutzt; einziges Reflex-Import in lib.

---

## 4. Einteilung

| Funktionalität | UI-Zustand (Vue) | Domänenlogik (lib-Dienst) | Speicherung |
|---|---|---|---|
| Vigilantia-Modal | offen/zu, Formularfelder, Ladeanzeige | `VigilantiaSettings`: lesen/ändern mit Folgen (prewarm, unload, start/stop) | `plugins/tools/vision/settings.json` |
| Auge | Tooltip, Farbe | scharf/unscharf → `start_all/stop_all_background_watchers` | settings.json `vigilantia_armed` |
| Modelle, Power-Knopf | Dropdown, Spinner | Discovery (llama-swap + Ollama), load/unload/is_loaded | settings.json `vlm.*` |
| Beschreiber-Liste | Pfeile, Schalter, Texte übersetzen | `describer_list` + strukturierter Zustand je Eintrag (erreichbar, nie geladen, vermessen …) | settings.json `describers`, VRAM-Cache |
| Kameraquellen | Karten, Eingaben | eine Quellenliste (Label über `VisionStore.source_label`), Patch je Quelle mit Live-Folgen | vision.db `sources` |
| RTSP-Kameras | Formular | anlegen/ändern/löschen, Zugangsdaten, rescan | settings.json `rtsp_cameras`, `.env` |
| Alarmregeln | Tabelle | Regel ändern + `reload_rules` (in `alert_bus`) | `data/alert_rules.json` |
| Zonen-Editor | Canvas, Fenstergröße (localStorage) | vorhanden (API) | settings.json `zone_masks`, vision.db `motion_min_area_ratio` |
| Live-Ansicht | sichtbare Quellen, Fokus, Teleprompter-Zeilen, Gesichtsliste, Fotos dieser Sitzung, Lightbox | Vorschau-Watch (VLM/Gesicht) je Quelle, Foto, PTZ | `data/vision_preview.json` (fps, Takt, Layout, sichtbare Quellen), vision.db (Auflösung, Briefing) |
| Casus | Filter, Seite, aufgeklappte Zeile, Bild-Modal, Zoomstufe, Bestätigen | Ereignisliste mit Filter/Gruppierung/Seite, Löschen, Zuordnen, Einzel- und Massenanalyse mit Fortschritt | vision.db `events`, Frames auf Platte |
| Multipose | Schritt-Anzeige, Live-Bild | Einlern-Sitzung: Aufnahme, Erkennung, Ausschnitt, Embedding (bleiben im Server), Abschluss | vision.db `faces`/`embeddings`, Crops |
| Feed-Karte | sichtbar, Popover | letzte Ereignisse + Zählung (gleiche Abfrage wie Casus) | vision.db; Sichtbarkeit derzeit nicht gespeichert |
| Medien im Browser | `<audio>`, Vorrollen 3 s, Warteschlangen-Cursor | Player-Zustand je Sitzung (statt State-Feldern), Kanal BC ohne State | `data/audio_state.json` (Positionen), Prozessspeicher |
| Audio-Einstellungen | Seite, Eingaben | Quellen anlegen/löschen (Sandbox), Index-Lauf mit Ergebnis, Plugin-Einstellungen | `data/media/audio/`, `data/audio_index.sqlite`, `plugins/tools/audio_player/settings.json` |

---

## 5. Vorschlag: REST und Ereignisse

Kennzeichnung: **vorhanden** = Endpunkt gibt es und passt; alle anderen sind neu.

### 5.1 REST

| Methode, Pfad | Inhalt | ersetzt |
|---|---|---|
| GET `/vision/settings` | Modus, Modell, Gesichtserkennung (an, kontinuierlich, Tage), Personen, scharf | `_refresh_vision_settings` |
| PATCH `/vision/settings` | Teiländerung; der Dienst wendet die Folgen an (live → laden, Modellwechsel → entladen) | `set_vision_mode_value`, `set_vision_model_value`, `set_face_*` |
| PUT `/vision/armed` `{armed}` | Auge, startet/stoppt alle Watcher | `toggle_vigilantia_armed`, `force_disarm_vigilantia` |
| GET `/vision/models` | Discovery frisch | `_discover_vision_models`, `rescan_vision_models` |
| GET `/vision/vlm`, POST `/vision/vlm/load`, POST `/vision/vlm/unload` | Status / laden / entladen | `toggle_vlm_model_loaded`, `_refresh_vlm_loaded` |
| GET `/vision/describers`, PUT `/vision/describers`, POST `/vision/describers/{source}/measure` (202) | Liste strukturiert (ohne übersetzte Texte), speichern, Einbrennen | `_refresh_describer_list`, `move_describer`, `set_describer_enabled`, `measure_remote_describer` |
| GET `/vision/sources` | eine Liste für Einstellungen und Vorschau: id, label, hardware_name, kind, available, ptz, auto_start, settings, resolution, resolution_options, prompt_context, Watch-Status | `_reload_vigilantia_sources`, `_refresh_sources` |
| PATCH `/vision/sources/{id}` | alias, auto_start, alerts_enabled, alert_types, quiet_*, schedule_*, min_event_interval_sec, resolution, prompt_context; Live-Folgen im Dienst | alle `set_vigilantia_source_*`, `set_vision_preview_resolution`, `set_vision_preview_prompt_context` |
| POST `/vision/sources/rescan` | Geräte neu suchen | `rescan_vision_preview_sources` |
| GET/POST/PUT/DELETE `/vision/rtsp-cameras[/{name}]` | Kamera ohne Geheimnisse lesen, anlegen, ändern, löschen | RTSP-Handler |
| GET `/vision/alert-rules`, PATCH `/vision/alert-rules/{category}` `{sinks?, vlm?}` | Regeln und Kanäle | `_reload_alert_rules`, `set_alert_rule`, `toggle_alert_sink` |
| GET `/vision/snapshot/{id}` **vorhanden** | Einzelbild; für Multipose ein Parameter `prefer_face=1` (Snap-API, Tele) ergänzen | `multipose_live_tick` |
| GET `/vision/stream/{id}` **vorhanden** | MJPEG | – |
| GET `/vision/frame` **vorhanden** | Ereignisbild, Vorschau, Zoom | – |
| GET/POST `/vision/zone-mask`, GET/POST `/vision/motion-min`, GET `/vision/zone-editor` **vorhanden** | Zonen-Editor | – |
| GET `/vision/preview/settings`, PATCH `/vision/preview/settings` | fps, Teleprompter-Layout, VLM-Takt, Gesichts-Takt, sichtbare Quellen | `_load_preview_fps`, `set_vision_preview_*`, `toggle_vision_preview_source` |
| GET `/vision/watch`, PUT `/vision/watch/{id}` `{vlm, face}` | laufende Watcher mit Herkunft (Hintergrund/Vorschau); Vorschau-Modus setzen, der Dienst baut die `WatchConfig` | `_apply_watch_mode`, `toggle_vision_preview_watch/_face_recognition` |
| POST `/vision/ptz/{id}/press` `{direction, speed}`, `/keepalive`, `/release`, `/home` | Kopfsteuerung; Herzschlag als `setInterval` in Vue | `ptz_*` |
| POST `/vision/photo/{id}` | Foto, Antwort `{url}` | `take_vision_photo` |
| GET `/vision/casus/events?source&type&face&grouped&offset&limit&since` | Seite + `total`; mit `limit=10` auch die Feed-Karte, mit `since` die Zählung | `_refresh_events`, `_refresh_vigilantia_feed` |
| GET `/vision/casus/event-ids?…`, GET `/vision/casus/clusters/{cluster_id}/event-ids` | IDs für Diaschau und Film | `casus_all_ids`, `_open_film` |
| GET `/vision/casus/filter-options` | Quellen, Personen | `_refresh_filter_options` |
| DELETE `/vision/casus/events/{id}`, POST `/vision/casus/events/delete` `{filter}` | einzeln / nach Filter | `casus_delete_event`, `casus_confirm_delete_all_now` |
| PUT `/vision/casus/events/{id}/face` `{face_id \| null}` | zuordnen/lösen | `casus_save_tag` |
| POST `/vision/casus/events/{id}/analyze` (202) | Einzelanalyse | `casus_analyze_event` |
| POST `/vision/casus/describe-all` (202), DELETE `/vision/casus/describe-all` | Massenanalyse starten / abbrechen | `casus_bulk_start`, `casus_bulk_cancel_run` |
| POST `/vision/enroll`, POST `/vision/enroll/{token}/capture`, DELETE `/vision/enroll/{token}/captures/last`, POST `/vision/enroll/{token}/finish`, DELETE `/vision/enroll/{token}` | Einlern-Sitzung im Server; Antwort je Aufnahme nur Pose, Ausschnitt-URL, Score | `MultiposeMixin`; ersetzt auch das tote POST `/vision/face/enroll` |
| GET `/vision/face/list`, `/vision/face/{id}/details`, POST `…/rename`, DELETE `/vision/face/{id}`, DELETE `/vision/face/embedding/{id}` **vorhanden** | Personarium (eigenes Inventar) | – |
| GET `/audio/sources`, POST `/audio/sources` `{rel_path}`, DELETE `/audio/sources/{label}` | Quellenliste, Symlink anlegen (Sandbox), löschen nur bekannter Labels | `_refresh_audio_sources_view`, `audio_on_source_picked`, `audio_remove_source` |
| POST `/audio/sources/{label}/index` `{force}` (202), DELETE `/audio/sources/{label}/index` | Index-Lauf, Index leeren | `audio_index_rebuild_source`, `audio_index_clear_source` |
| GET/PATCH `/audio/settings` | `picker.root`, `tts_list.full_max_items` | `audio_save_picker_root`, `audio_save_tts_list_max` |
| GET `/audio/player/{session}` | Player-Zustand der Sitzung (Medium, Position, Pause, Warteschlange) | `media_*`, `_audio_runtime_state` |
| GET `/audio/file`, POST `/audio/position` **vorhanden** | Datei mit Range, Position | – |

Namenskonflikt: `/vision/events/{source_id:path}` ist schon der Live-SSE je Quelle (aV:231). Eine Ereignisliste darf deshalb nicht unter `/vision/events?…` liegen; oben steht sie unter `/vision/casus/…`. Alternativ den Live-Strom in `/vision/live/{id}` umbenennen.

### 5.2 Ereignisse (SSE)

Der Plan sieht einen Strom je Sitzung vor; der vorhandene Browser-Bus (aB) ist schon so gebaut (Versionen, Wiederholung nach Reconnect, „neuester gewinnt“ über `LATEST_WINS_KINDS`). Vision-Ereignisse sind aber global, nicht sitzungsbezogen: Sie müssen an alle Sitzungsströme verteilt werden (offen: ein `broadcast` neben `browser_push`, oder ein globales Thema im selben Strom).

| Art | Inhalt | Auslöser | ersetzt |
|---|---|---|---|
| `vision.settings` | Einstellungen nach Änderung (Auge, Modus, Modell, Gesicht) | PATCH, PUT armed, Plugin-Abschaltung | Neuladen beim Öffnen |
| `vision.sources` | Quellenliste geändert | rescan, RTSP, PATCH Quelle | – |
| `vision.watch` | je Quelle: läuft, VLM, Gesicht, Herkunft | `vision_watcher.start/stop`, Vorschau-Ende | Session-Listen `vision_preview_watching/_face_active` |
| `vision.vlm` | Modell, geladen, lädt gerade | load/unload/prewarm | `vlm_model_loaded/_busy` |
| `vision.describer` | Einbrennen: läuft / fertig (MiB) / Fehler | `measure` | `vision_describer_measuring` |
| `vision.event` | Kurzfassung eines neuen Ereignisses (wie eine Listenzeile) | eine Stelle hinter `VisionStore.add_event` (`vision_store.py:615`, gerufen aus `vision_recorder.py:94, 154, 220, 248, 273`, `vision_edge_ai.py:273`) | 500-ms-Abfragen von Feed und Casus |
| `vision.events_deleted` | gelöschte IDs oder „nach Filter“ | Löschen | – |
| `vision.analysis` | Einzelanalyse: Ereignis, läuft/fertig/Fehler, Text | analyze | `casus_analyzing_event_id`, `casus_status` |
| `vision.describe_all` | läuft, verarbeitet, gesamt, Meldungsschlüssel | `run_bulk_describe`-Rückruf | `casus_bulk_*` |
| `audio.index` | Quelle, läuft/fertig, Ergebnis (scanned, inserted, updated, deleted, Dauer) | Index-Lauf | `audio_settings_busy/_status` |
| `media`, `stop`, `pause`, `resume`, `seek`, `speed` **vorhanden** | Player-Steuerung | Kanal BC | – |
| `media.state` (neu, „neuester gewinnt“) | Player-Zustand der Sitzung | BC statt State-Schreiben | `data-media-*`, `_audio_runtime_state` |

Live je Quelle bleibt ein eigener Strom: `/vision/events/{id}` **vorhanden** mit `vlm_analysis`, `face_known`, `face_unsure`, `face_unknown`. Er ist hochfrequent, quellenbezogen und hält über seine Abonnenten den Vorschau-Watcher am Leben (`vision_event_bus.py:91–124`). Das Feld `embedding_b64` (`vision_recorder.py:191`) braucht dort niemand mehr (siehe 7.2).

---

## 6. Abhängigkeiten zu anderen Mixins

| Mixin | benutzt von anderen | wird benutzt von |
|---|---|---|
| VisionSettings | `ui_language`, `tool_plugin_toggles` (`_settings_mixin.py:97, 126`); `vision_preview_sources`, `_persist_source_alias` (VP) (VS:94–101, 625–631) | Login `_backend_mixin.py:597` (`_refresh_vision_settings`); Plugin aus `_settings_mixin.py:995` (`force_disarm_vigilantia`); VP (`_refresh_vision_settings`, `_refresh_vlm_loaded`, `_persist_source_setting`, `vision_model_value` im Kopf); CA (`vlm_model_loaded`, `toggle_vlm_model_loaded` in uCA:848); VF (`vigilantia_sources`, `_reload_vigilantia_sources`, `vigilantia_armed`, `vigilantia_has_armed_source`, `toggle_vigilantia_armed`, `open_vision_settings`, `vision_plugin_enabled`) |
| VisionPreview | über `getattr` ohne Deklaration: `face_recognition_continuous`, `person_detect_enabled`, `ui_language` (VP:381, 526–528, 547–549); `open_personarium` (Personarium) in uVP:751 | VS (`vision_preview_sources`, `_persist_source_alias`) |
| Casus | `vlm_model_loaded` (deklariert), `vision_model_value` per `getattr` (CA:149); `open_personarium` in uCA:419 | `_base.py:240–241` (Takt); VF/uVF:452–455, uVS:856 (`open_casus`) |
| Multipose | keine State-Felder anderer Mixins | Personarium-UI `personarium.py:108` (`open_multipose(fid, name)`), uVS:865 |
| VigilantiaFeed | VS (siehe oben); `open_casus` (CA); `open_model_lifecycle_help` (`_image_mixin.py:278`) in uVF:397 | `_base.py:235–236` (Takt) |
| AudioPlayer | `agent_editor_mode` (`_agent_editor_mixin.py:54`), `_picker_open_for` (`_file_picker_mixin.py:96`), `session_id` per `getattr` (`_session_mixin.py:72`) | `_session_mixin.py:277–319, 461, 618`; `_auth_mixin.py:174–182`; `_tts_streaming_mixin.py:232–233` (`media_paused_for_tts`); BC (schreibt `media_*`, ruft `_persist_audio_state`); aifred.py:69–79 (`data-media-*`) |

Vorschau-Fenster und Hauptfenster teilen denselben State (gleicher Client-Token, aifred.py:1133–1144); Personarium und Multipose sind deshalb im Popup mitgemountet.

---

## 7. Auffälligkeiten

### 7.1 Umwege wegen Reflex (fallen mit Vue weg)

- SSE-Manager wird per JS in `<head>` gehängt, weil `rx.script(src=…)` in Lazy-Bundles nicht ausgeführt wird (VP:340–344, 395–403).
- Briefing-Textfelder bekommen ihren Startwert per Polling-JS ins DOM, weil `value`/`default_value` aus `rx.foreach` nicht ankommt (VP:404–422; uVP:714–720).
- `set_vision_preview_fps` baut alle Quellen-Dicts neu und fasst `vision_preview_resolutions` an, nur damit `rx.foreach` die `<img src>` aktualisiert (VP:677–710); dazu der Cache-Buster.
- `resolution` doppelt im Dict jeder Quelle, weil Dict-Vars nicht mit Schleifenvariablen indiziert werden können (VP:807–809).
- `vigilantia_has_armed_source` als einfaches Feld statt berechnet (MutableProxy-Dirty-Kreislauf, VS:152–157); Zuweisen nur bei echter Änderung in VS:518–524 und VF:121–129, damit der 500-ms-Takt nicht jedes Mal ein Delta schickt und Textauswahlen löscht.
- Gesteuerter Popover, der Schließen-Ereignisse ignoriert (VF:91–100; uVF:603–638).
- Zwei Knöpfe per `rx.cond` statt `rx.cond` an Eigenschaften (uVP:194–239); `rx.select.root`-Breite über einen Wrapper (uVS:46–68).
- `vision_plugin_enabled` mit `tool_plugin_toggles` als Stellvertreter-Abhängigkeit (VS:462–471); `casus_image_zoom_src` mit expliziten deps (CA:441–447).
- JS-Aufrufe über `rx.call_script`: `open_zone_editor`, `open_vision_preview`, `clear_vlm_teleprompter`, Lightbox nach Foto (VS:559–570, VP:324–328, 664–666, 732–736).
- Feed und Casus hängen am 500-ms-Takt des Debug-Fensters (`_base.py:228–241`), statt Ereignisse zu bekommen.
- Picker-Rückruf über Ereignisnamen als String `callback_event="audio_on_source_picked"` und `# type: ignore[arg-type]` (AP:430, 500).
- **Fortschrittsanzeigen ohne Wirkung:** `toggle_vlm_model_loaded` setzt `vlm_model_busy` (VS:1103–1122), `casus_analyze_event` setzt `casus_analyzing_event_id` (CA:208–220), `multipose_capture` setzt `multipose_busy` (MP:160–198) — alles normale Handler ohne `yield`, also kommt der Zwischenstand nie beim Client an; der Handler blockiert zugleich alle weiteren Ereignisse dieses Clients für die Dauer des VLM-Ladens bzw. der Analyse.

### 7.2 Toter Code (Aufrufer gesucht in `aifred/`, `assets/`, `tests/`, `scripts/`, auch ohne Klammer)

- `play_media` (AP:140–160): kein Aufrufer; BC setzt die Felder selbst.
- `resume_media_after_tts` (AP:178–196): kein Aufrufer, cj setzt das Fortsetzen selbst um (cj:1994–2026). Damit ist auch `media_paused_by_user` wirkungslos (gesetzt in BC:84, 126, 146, 283).
- `audio_settings_open` (AP:87): gesetzt (AP:267, 303), nie gelesen.
- `vigilantia_feed_open` (VF:48): nirgends benutzt.
- `multipose_preview_data_url` (MP:56): gesetzt (MP:108, 118, 195, 207, 268), von keiner Oberfläche gelesen — schickt bei jeder Aufnahme ein Base64-JPEG zum Client.
- `_persist_preview_fps` (VP:977–979): „Backwards-compat wrapper“, einziger Aufrufer VP:709.
- `_audio_active` (aA:14): nie benutzt.
- GET `/audio/test` (aA:177–217): Testseite mit fest eingetragenem Titel `music/05-Ausgefressen.mp3`.
- POST `/vision/face/enroll` (aV:278–349): kein Aufrufer, seit der Knopf „+ taggen“ das Personarium öffnet (SSE.js:337–346). Damit auch `embedding_b64` im Live-Ereignis (`vision_recorder.py:191`, SSE.js:198), rund 2 KB je Gesichtsereignis.
- `window.clearVlmFaces` (SSE.js:355–360): nie aufgerufen.
- Alter SSE-Manager in cj:2226–2325: Doppel von SSE.js. Im Popup wird cj nicht geladen (Lader nur im Hauptlayout, aifred.py:717–746), im Hauptfenster gibt es keine Ziele — aber sein `MutationObserver` auf `document.body` mit `subtree` (cj:2313–2314) ruft bei jeder DOM-Änderung `scan()` mit `querySelectorAll` auf, also laufend während des Streamens.
- GET/DELETE `/browser/queue/{session}` (aB:195–219): „Polling-Fallback“ ohne Aufrufer im Repo.
- `variant=rx.cond(…, "soft", "soft")` (uCA:233): Bedingung ohne Wirkung.

### 7.3 Doppelte Logik

- Kategorien der Alarmregeln zweimal: VS:58–65 und uVS:587–594 („muss zur Reihenfolge passen“).
- Pfad und Lesen von `alert_rules.json` zweimal: VS:68–82 und `alert_bus.py:518–553`.
- Merge eines `settings`-Schlüssels: VS:633–649 und VP:1020–1042 bauen nach, was `VisionStore.patch_source_settings` (`vision_store.py:232–244`, „Gemeinsamer Persist-Pfad für State und API“) schon kann.
- Kameraname: `VisionStore.source_label` ist laut Docstring die SSOT („ALLE Stellen … gehen hierüber“, `vision_store.py:257–262`), VS:491–492, VP:876–879 und `_label_from` (VP:59–68) rechnen selbst.
- Zwei Quellenlisten für dieselben Kameras (VS:473–529 und VP:803–924), jede mit eigenen Store-Abfragen.
- Standard-Bewegungsschwelle `0.02` fest in VP:893 und aV:613, obwohl `VISION_MOTION_MIN_AREA_RATIO` (`config.py:828`) existiert.
- Gültigkeitsbereiche mehrfach: fps 0–30 (VP:697–702, 945; aV:211), VLM-Takt 0,5–300 (VP:629, 951), Gesichts-Takt 0,1–60 (VP:642, 954), Aufbewahrung 1–3650 (VS:203, 446), Bewegungsschwelle 0,001–0,5 (VP:861; aV:612, 625).
- Audio-Einstellungen: `AUDIO_PLAYER_SETTINGS_PATH` und `build_configured_source_map` (`audio_player_settings.py:18–57`) sind die SSOT, trotzdem eigene Pfade und eigenes Parsen in AP:209–224, 392–406, 483–494 und aA:31–49 (dort „Mirrors the plugin's _make_resolver()“); zwei Schreibwege (AP:274–283 und AP:487–494).
- Freshest-Frame-Logik: MP:314–329 (Snap mit `prefer_face`, sonst Hub) neben `vision_photo.take_photo` (`vision_photo.py:31–39`).
- Gesichtsausschnitt: MP:360–388 mit 30 % Rand neben `face_crop_store._crop_from_jpeg` mit `BBOX_PAD_*` (`face_crop_store.py:83–85, 427–448`) — Multipose-Crops sind anders gerahmt als alle anderen.
- Ereignistyp-Liste der Feed-Karte zweimal in derselben Methode (VF:115–117, 132–134); Badge-`rx.match` zweimal (uCA:24–39, uVF:62–75).

### 7.4 Fallen und Fehler

- **Pfad-Traversal beim Löschen von Audio-Quellen:** `audio_remove_source(source)` baut `MEDIA_AUDIO_DIR / source` ohne Prüfung und ruft bei einem Ordner `shutil.rmtree` (AP:364–372). Jede öffentliche Methode ist ein Event-Handler, den jeder angemeldete Client mit eigenen Argumenten auslösen kann; `source="../../…"` löscht einen beliebigen Ordner. Im REST-Endpunkt nur Labels aus `build_source_map` zulassen.
- **Vorschau stoppt die Hintergrundüberwachung:** Ein Watcher je Quelle wird von Hintergrund und Vorschau geteilt. `_apply_watch_mode` stoppt und startet mit der Vorschau-Konfiguration (VP:583–585) und stoppt ganz, sobald beide Schalter aus sind (VP:513–515) — eine scharfe Kamera ist dann bis zum Schließen des Fensters unbewacht; erst der Teardown stellt sie wieder her (`vision_autostart.py:250–278`).
- **Schalterzustand veraltet:** `vision_preview_watching/_face_active` sind Session-Listen, der Watcher läuft global und endet 5 s nach dem letzten SSE-Abonnenten. `on_load_vision_preview` setzt die Listen nicht zurück (VP:354–357): Nach erneutem Öffnen zeigen die Knöpfe „an“, obwohl nichts läuft. Zwei Clients sehen unterschiedliche Zustände. Der Watch-Status gehört aus `vision_watcher.list_active()` in die Oberfläche.
- **Teure, synchrone Abfragen im Handler:** `_refresh_vision_settings` fragt bei Login, Modal und Popup Kameras ab (V4L2-Probe mit `cv2.VideoCapture`, RTSP-TCP-Verbindung), Ollama per HTTP (5 s Grenze) und jeden fernen Beschreiber (`/health` 0,5 s, `/v1/models` bis 15 s, `remote_describers.py:113–138`, `config.py:1288, 1297`) — alles ohne `to_thread`. Ist ein Beschreiber-Rechner aus, verzögert das den Login.
- 500-ms-Takt: Bei sichtbarer Feed-Karte zwei DB-Abfragen je Takt und Client (VF:114–136); bei leerer Quellenliste zusätzlich Kamera-Probes (VF:107–108).
- `vigilantia_feed_visible` wird nirgends gespeichert: Ausblenden gilt nur bis zum nächsten Laden (VF:46, 70–74).
- Casus-Gruppierung lädt höchstens 5000 Ereignisse und schneidet still ab (CA:693); `casus_all_ids` schickt die ganze ID-Liste zum Client (CA:56, 727, 749).
- Typfilter kennt `person`, `animal`, `vehicle` nicht (CA:28–36, 766–769), obwohl diese Ereignisse in Liste und Feed vorkommen (uCA:31–33; VF:115).
- Docstring von `casus_refresh` sagt „Bewusst manuell statt Auto-Refresh“ (CA:583–586), `_casus_poll_new_events` aktualisiert automatisch (CA:319–344). Docstring von `save_zone_mask` sagt „greift erst nach Re-Arm“ (aV:557–558), der Code lädt live nach (aV:585–591).
- Doppeltes Abfangen: `set_vigilantia_source_alias` fängt `sqlite3.Error` (VS:613–617), `_persist_source_alias` fängt ihn selbst schon und schreibt die Meldung in `vision_preview_status` (VP:1040–1042) — im Einstellungs-Modal unsichtbar; `hasattr(self, "vision_preview_sources")` auf ein deklariertes Feld (VS:625).
- `multipose_finish` schreibt die Erfolgsmeldung und schließt im selben Zug das Modal (MP:265–266): Die Meldung sieht niemand.
- Embeddings und Base64-Bilder der Multipose-Aufnahmen liegen in öffentlichen Feldern (MP:52, 187–194) und gehen damit an den Browser und zurück.
- `save_rtsp_camera` überschreibt beim Anlegen eine bestehende Kamera gleichen Namens ohne Rückfrage (VS:1000–1010); `delete_rtsp_camera` lässt Zugangsdaten in `.env` und den Datensatz in vision.db stehen (VS:1029–1041); `cred` geht ungeprüft in den Namen der `.env`-Schlüssel (VS:950–954).
- `settings.json` der Vision wird von vielen Stellen gelesen-geändert-geschrieben (VS, `describer_list.save`, aV:559–584) ohne Sperre; gleichzeitige Änderungen (Zonen-Editor und Modal) können einander überschreiben.
- Zugriffe auf Privates aus anderen Modulen: `vision_routing._normalize` (VS:326), `vision_autostart._schedule_active_now` (VS:775–776), `TranslationManager._translations` (VP:382; aV:666).
- Legacy-Reste gegen die No-Legacy-Regel: Umdeuten von `vision_mode="off"` (VS:191–194), Prüfungen auf `"off"` in `vision_autostart.py:300, 359`, Stunden-Ganzzahlen in Zeitfenstern (VS:33–38, 758–764). Ruhezeit speichert ganze Stunden (VS:703–708), das Aktiv-Fenster `"HH:MM"` — zwei Formate für dasselbe Konzept.
- `asyncio.get_event_loop()` in einer Coroutine (AP:332), statt `asyncio.to_thread`.

### 7.5 Fest eingetragene Texte und Formatierung

- Deutsche Texte im State statt `prompts`/`i18n`: „Treiber-Default“ (VP:84), „Manuell (Einzelbild)“ und weitere Labels (VP:216–226), „⚠️ Persistierung fehlgeschlagen“ (VP:1018), alle `casus_status`/`casus_bulk_message` (CA:213–304, 380–414, 627–632), „Alle Quellen/Personen“, „Nur unzugeordnete“ (CA:652–669), Multipose-Meldungen (MP:156, 173, 234, 265), Audio-Meldungen (AP:320–378, 449–552) und „Neue Audio-Source hinzufügen“ (AP:419), „Host ist Pflicht.“ (VS:969–971), Platzhalter „z. B. Garage“ u. a. (uVS:485–519); in SSE.js die Ersatztexte „Unbekannt“, „+ taggen“, „Verwerfen“ (SSE.js:118, 125, 168, 200–201).
- Ersatzumlaute in Benutzertexten: „Eintraege geloescht“ (AP:350, 378).
- Zahl mit f-String statt `format_number`: `{result.elapsed_sec:.1f}s` (AP:342).
- `COLORS.get("primary", COLORS["primary"])` (uVS:603): Ersatzwert, der denselben Schlüssel liest.
