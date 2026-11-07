# Inventar: Agenten, Agenten-Editor, Gedächtnis, Personarium, UI-Konfiguration, Anmeldung

Stand 07.11.2026, Zweig `vue-frontend`, Commit `20878bce`; alle Zeilenangaben beziehen sich auf diesen
Stand. Vollständig gelesen: `aifred/state/_agent_editor_mixin.py`, `_agent_config_mixin.py`,
`_memory_browser_mixin.py`, `_personarium_mixin.py`, `_ui_config_mixin.py`, `_auth_mixin.py`, die
Oberfläche `aifred/ui/agent_editor/*`, `ui/personarium.py`, `ui/modals/{login,credentials,help}.py`,
dazu die benutzten lib-Module. Die Sicherheitsbefunde sind aus dem Code abgeleitet, nicht live
ausprobiert.

Abkürzungen in den Tabellen: `AE` = `_agent_editor_mixin.py`, `AC` = `_agent_config_mixin.py`,
`MB` = `_memory_browser_mixin.py`, `PE` = `_personarium_mixin.py`, `UC` = `_ui_config_mixin.py`,
`AU` = `_auth_mixin.py`, `SM` = `_settings_mixin.py`. Eine Zahl ohne Datei gehört zum Mixin des
Abschnitts.

| Mixin | Zeilen | `@rx.event` | `@rx.var` | öffentliche Felder | private Felder |
|---|---|---|---|---|---|
| AE | 1504 | 55 | 21 | 44 | 10 |
| AC | 1084 | 20 | 18 | 18 | 0 (3 `ClassVar`) |
| MB | 561 | 29 | 2 | 22 | 0 |
| PE | 499 | 23 | 3 | 15 | 0 |
| UC | 263 | 8 | 0 | 4 | 0 |
| AU | 246 | 10 | 0 | 6 | 0 |

Nicht in diesen Mixins, aber von der gelesenen Oberfläche benutzt: Zugangsdaten-Seite und Plugin-Tab
(`SM`, Felder 100–130, Handler 437–1003), Hilfe-Fenster (`_image_mixin.py:58-60, 250-285`), die
Editor-Tabs Speicher, STT und TTS (eigene Mixins, siehe Abschnitt 6).

---

## 1. State-Felder

### AuthMixin

| Feld | Typ | Zweck |
|---|---|---|
| `logged_in_user` | str | angemeldeter Benutzer, `""` = keiner (35) |
| `login_dialog_open` | bool | sperrt die Oberfläche bis zur Anmeldung, beginnt mit True (36) |
| `login_mode` | str | `login` / `register` (37) |
| `login_username`, `login_password` | str | Eingaben; das Passwort liegt im synchronisierten State (38–39) |
| `login_error` | str | Fehlermeldung, deutscher Text im Code (40) |

Privat: keine eigenen; benutzt `_session_initialized` aus `_session_mixin.py:74`.

### UIConfigMixin

| Feld | Typ | Zweck |
|---|---|---|
| `temperature_mode` | str | `auto` (Intent) / `manual`, global in `settings.json` (34) |
| `num_ctx` | int | ungenutzt (39), siehe 7.2 |
| `show_transcription` | bool | Diktat erst ins Eingabefeld (42) |
| `enter_sends_message` | bool | Enter sendet, Umschalt+Enter = Zeilenumbruch (49) |

### AgentConfigMixin

| Feld | Typ | Zweck |
|---|---|---|
| `agent_tuning` | dict[str, AgentTuning] | SSOT je Agent (91): Modell, Speed-Variante, Persönlichkeit, Reasoning, Denkwahl je Modell, Temperatur (+Offset), top_k/top_p/min_p/repeat_penalty, RoPE, max_context, num_ctx manuell (`lib/agent_tuning.py:38-81`) |
| `sampling_reset_key` | int | Zähler, erzwingt Neuaufbau der Eingabefelder nach Reset (93) |
| `automatik_rope_factor` | float | RoPE der Automatik (kein Agent) (96) |
| `active_agent`, `multi_agent_mode`, `symposion_agents` | str, str, list[str] | je Sitzung, in der Sitzungsdatei (102, 106, 108) |
| `agent_memory_enabled` | bool | Inkognito-Schalter, nur im Tab (103) |
| `max_debate_rounds`, `consensus_type` | int, str | global in `settings.json` (107, 109) |
| `sokrates_critique`, `show_sokrates_panel`, `salomo_synthesis`, `show_salomo_panel`, `debate_round`, `debate_user_interjection`, `debate_in_progress` | str/bool/int | Laufzeit der Diskussionsmodi (112–120) |

Berechnete Vars: `aifred_/vision_thinking_mode`, `…_thinking_options` (209–279), `rope_factor_display`,
`automatik_rope_display`, `vision_rope_display` (608–621), `is_unanimous_consensus`,
`consensus_toggle_tooltip`, `speed_switch_tooltip`, `multi_agent_mode_options` (696–726),
`selectable_agents` (733–752, liest `agents.json`), `is_fixed_agent_mode` (754), `symposion_agent_positions`
(802), `secondary_available_models_rich` (838), `sampling_rows`, `ctx_rows`, `agent_model_rows`
(998–1084, Zeilen-Dataclasses aus `agent_tuning.py:112-153`). `ClassVar`: `CORE_AGENTS` (729, ungenutzt),
`FIXED_MODES` (731), `_SECONDARY_MODEL_EMOJI` (863).

### AgentEditorMixin

| Gruppe | Felder (Zeile) |
|---|---|
| Navigation | `agent_editor_open` (51, nur geschrieben), `agent_editor_mode` (54: config, memory, database, scheduler, audit, plugins, storage, tts, stt) |
| Agent | `editor_agent_id` (88; `""` = neu, `automatik` = Pseudo-Agent), `editor_display_name` (89), `editor_emoji` (90), `editor_role` (92), `editor_model` (93), `editor_cloud_provider` (97), `editor_cloud_models` (99), `editor_system_reasoning` (104) |
| Prompts | `editor_prompt_tab` (107), `editor_prompt_lang` (109), `editor_prompt_keys` (111) |
| Tools | `editor_tools` dict[str, bool] (127), `editor_tool_groups` list[dict] (128, Format `plugin_registry.tool_groups_on_offer`) |
| Dialoge | `editor_delete_confirm` (117), `editor_memory_confirm` (119), `editor_emoji_picker_open` (122), `editor_dirty` (331), `editor_dirty_confirm` (332), `editor_reset_confirm` (1463) |
| Scheduler | `scheduler_job_list` (57), `scheduler_edit_id` (58; `""`, `new` oder ID), `scheduler_edit_name/_type/_expr/_message/_agent/_delivery/_channel/_tier/_webhook_url/_recipient/_fixed_text/_audio_type` (59–70), `scheduler_cron_min/_hour/_dom/_month/_dow`, `scheduler_interval_value/_unit`, `scheduler_once_date/_time` (72–80) |

Berechnete Vars: `agent_dropdown_options`, `editor_agent_dropdown_value` (131–146), `editor_is_system_agent`
(152), `editor_cloud_provider_options/_label`, `editor_cloud_model_options` (167–187), `sched_*_options`
und `sched_*_display` (737–925), `scheduler_agent_options` (986, liest `agents.json`),
`scheduler_edit_agent_display` (996).

Privat: `_agent_dropdown_items` (83), `_agent_id_by_label` (85), `_editor_description` (91),
`_editor_prompt_content` (108), `_editor_new_agent_id` (114, nur geschrieben), `_agent_editor_needs_init`
(269), `_agent_editor_start_tab` (271), `_pending_agent_label` (333), `_pending_close` (334),
`_editor_typed` (341).

### MemoryBrowserMixin

| Gruppe | Felder (Zeile) |
|---|---|
| Datenbank-Tab | `db_documents` (59, ein Eintrag je Dokument), `db_clear_confirm` (60), `db_orphans` (63), `db_orphans_visible` (64) |
| Gedächtnis | `memory_browser_agent` (67), `memory_browser_agent_display` (68), `memory_browser_entries` (69: id, date, type, summary, content, session_id), `memory_browser_collections` (70: name, agent_id, display_name, count), `memory_browser_filter` (71: all, session, agent), `memory_edit_id/_summary/_content` (73–75), `memory_confirm_clear` (76) |
| Bundle | `bundle_export_open`, `bundle_export_selected` (79–80), `bundle_all_agents` (82), `bundle_import_open` (84), `bundle_import_uploaded_b64` (85, ganze ZIP als Base64 im synchronisierten State), `bundle_import_agents` (86), `bundle_import_selected` (87), `bundle_import_conflict` (88), `bundle_import_error` (89) |

Berechnete Vars: `memory_agent_dropdown_options` (121), `filtered_memory_entries` (129).

### PersonariumMixin

`personarium_open` (26), `personarium_faces` (30), `personarium_status` (32), `personarium_edit_face_id`,
`personarium_edit_name` (35–36), `personarium_manage_face_id` (40), `personarium_embeddings` (41),
`personarium_untagged` (45, eine Karte je Crop-Datei mit `member_ids`), `personarium_untagged_selected`
(49), `personarium_confirm_dismiss_all` (50), `personarium_tag_event_id/_value/_new_name` (53–55),
`personarium_tag_busy` (57), `personarium_rematch_busy` (59). Berechnet: `personarium_untagged_unknown`,
`…_unsure` (90–107), `personarium_tag_options` (109–118).

---

## 2. Handler und interne Methoden

Kein Handler dieser sechs Mixins ist `background=True`. Eigene Timer gibt es keine; Abgleich mit
anderen Tabs läuft über den 500-ms-Tick des Browsers (`_base.py:242-262`, `assets/custom.js:2147`).

### AuthMixin

| Handler (Zeile) | Was | lib | Speichert |
|---|---|---|---|
| `set_login_username/_password` (44–52), `handle_login_key_down` (56), `handle_login_submit` (65), `open_/close_login_dialog` (73–88) | Eingaben, Enter, Formular, Dialog | – | – |
| `_post_auth_script` (92) | JS: Cookie `aifred_username` und `aifred_session_id` setzen, `startBrowserStream` | `browser_storage.set_username_script` → `auth.sign_username` | Cookie im Browser |
| `do_login` (106) | Passwort prüfen, `logged_in_user` setzen, letzte Sitzung laden | `session_storage.account_exists`, `verify_account`; `_load_latest_session` | `accounts.json` (Hash-Upgrade) |
| `do_register` (139) | Whitelist, Konto, neue Sitzung | `is_username_allowed`, `create_account`; `new_session` | `accounts.json`, Sitzungsdatei |
| `do_logout` (170) | Audio-/TTS-Laufzeitreste der Sitzung verwerfen, State leeren, Cookie löschen | `discard_audio_runtime_state`, `discard_tts_backend_state` | – |
| `handle_username_loaded` (199) | Rückruf mit dem Cookie-Wert aus JS, Signatur prüfen, Auto-Login | `auth.verify_signed_username`, `account_exists` | – |

Auslöser des Auto-Logins: `on_load` (`_backend_mixin.py:758-767`) schickt `get_username_script()` mit
Rückruf `handle_username_loaded`.

### UIConfigMixin

| Handler (Zeile) | Was | lib | Speichert |
|---|---|---|---|
| `toggle_auto_refresh` (55), `set_temperature_mode` (65), `toggle_show_transcription` (244), `toggle_enter_sends_message` (252) | Schalter | – | `settings.json` (global) |
| `set_agent_num_ctx_manual` (108) → `_set_num_ctx_manual` (81) | Zahl säubern, klammern (Vision ≥ 1024, ≤ `NUM_CTX_MANUAL_MAX`) | `agent_settings.set_agent_setting` | nur Vision in `settings.json` |
| `toggle_agent_num_ctx_manual` (127) → `_toggle_num_ctx_manual` (119) | an/aus | dito | nur Vision |
| `_auto_ctx_for_agent` (134) | kalibrierter Kontext je Backend | `calibration.parse_llamaswap_config`, `model_vram_cache`, `operating_points.get_vllm_entry_context` | – |
| `calculate_manual_context` (167) | Kontext und Sampling aller Agenten ins Debug-Log, setzt `_min_agent_context_limit` | `multi_agent.resolve_agent_temperature` | – |
| `toggle_audio_recording` (260) | `toggleRecording()` per JS | – | – |

### AgentConfigMixin

| Bereich | Handler / Methode (Zeile) | Was | lib | Speichert |
|---|---|---|---|---|
| Schalter | `toggle_agent_personality_for`, `toggle_agent_reasoning_for` (961–967) → `_toggle_agent_feature` (126) | bool umdrehen | `agent_settings` | `settings.json` |
| Denken | `set_agent_thinking_mode_for` (969) → `_set_agent_thinking_mode` (160); `_agent_thinking_mode`, `_agent_thinking_options` (seit 07.11.2026 über `agent_choices.thinking_choices`) | Menü aus der Chat-Vorlage des Modells, Label ↔ Wert | `agent_choices.thinking_choices`/`choose_thinking` | `settings.json` (`thinking_by_model`) |
| Sampling | `set_agent_sampling_for` (929) → `_set_agent_sampling` (285); `set_agent_temperature_for` (933) → `_set_temperature_input` (627); `reset_agent_sampling_for` (956) → `_reset_agent_sampling` (307); `_reset_sampling_for_all_agents` (937) | klammern; Reset aus llama-swap-cmd bzw. `generation_config.json` | `agent_choices.sampling_defaults`/`reset_sampling` (seit 07.11.2026) | `settings.json` |
| Modell | `set_agent_model_for` (924) → `_set_secondary_agent_model` (868); `_model_select_id` (850) | Modell eines Sekundäragenten, Metadaten aus Cache, Sampling-Reset | `agent_choices.choose_model` (seit 07.11.2026, auch `set_aifred_model`/`set_vision_model`) | `settings.json` (`backend_models`) |
| Speed/RoPE | `toggle_agent_speed_mode_for` (973) → `_toggle_speed_mode` (476); `set_aifred_rope_factor` (515), `set_automatik_rope_factor` (580), `set_agent_rope_factor_for` (977) → `_set_agent_rope_factor` (590) | Variante bzw. RoPE je Modell | `model_vram_cache.set_rope_factor_for_model` | Modell-Cache |
| Auflösung | `_effective_model_id` (402), `_image_describer` (449), `_image_tuning_bucket` (467), `_speed_mode_debug_str` (484) | ID mit Varianten-Suffix, Bildbeschreiber | `calibration.resolve_effective_suffix`, `vision_routing.chat_describer` | – |
| Modi | `set_multi_agent_mode` (641), `set_active_agent` (768), `toggle_symposion_agent` (784) | Modusregeln (Symposion ≥ 1 Agent, feste Modi → AIfred) | – | Sitzungsdatei über `_persist_session_config` |
| Debatte | `increase_/decrease_debate_rounds` (672–686), `toggle_consensus_type` (688) | 1–10 Runden, Mehrheit/Einstimmig | – | `settings.json` |
| Sonst | `toggle_agent_memory` (759), `reset_sokrates_state` (817), `reset_salomo_state` (828), `_ui_agent_list` (985) | Inkognito; Laufzeit leeren; Agentenliste | `agent_config.load_agents_raw` | – |

### AgentEditorMixin

| Bereich | Handler / Methode (Zeile) | Was | lib | Speichert |
|---|---|---|---|---|
| Navigation | `open_agent_editor` (273), `open_tts_settings` (284), `on_load_agent_editor` (293), `close_agent_editor` (350), `close_editor_with_dirty_check` (360), `set_agent_editor_tab` (403) | Route `/agent-editor`, Init-Flag, Tab laden (Gedächtnis, Index, Speicher, STT, TTS, Plugins, Audit, Scheduler) | `credential_broker.broker.get` (Erlaubnislisten), `plugin_registry.list_all_plugins/all_channels`, `security.load_audit_entries`, `scheduler.get_job_store` | – |
| Auswahl | `select_editor_agent` (1063), `select_editor_agent_with_dirty_check` (375), `confirm_discard_changes` (384), `mark_editor_dirty` (343), `_refresh_agent_dropdown` (237), `_load_agent_into_state` (1073), `_load_automatik_into_state` (1144) | Agent über Label laden, ungespeicherte Änderungen | `agent_config.get_agent_config/load_agents_raw`, `tts_service.first_enabled_key`, `prompt_loader.PROMPTS_DIR` | – |
| Speichern | `save_agent_editor` (1311), `start_new_agent` (1483), `delete_agent_editor` (1407) | Metadaten, Tool-Whitelist, Prompt; neu anlegen; löschen (zweiter Klick) | `agent_config.update_agent/create_agent/delete_agent` | `agents.json`, `prompts/{de,en}/<id>/` |
| Prompts | `set_editor_prompt_tab` (1221), `set_editor_prompt_lang` (1233), `request_/confirm_reset_editor_prompt` (1465–1481), `_load_editor_prompt` (1188), `_save_editor_prompt_to_disk` (1282), `_push_editor_dom` (1174) | Datei lesen/schreiben, per JS ins DOM | – (direkter Dateizugriff) | `prompts/<lang>/…` |
| Tools | `toggle_editor_tool` (1256), `set_all_editor_tools` (1263), `set_editor_tool_group` (1268), `_offer_editor_tools` (1120), `_refresh_editor_tools` (1132, aufgerufen von `SM:1002`) | Angebot je Plugin, an/aus | `plugin_registry.tool_groups_on_offer` | erst mit Speichern |
| System-Agent | `set_editor_cloud_provider` (189), `refresh_editor_cloud_models` (202, `on_mount` in `config_view.py:328`), `set_editor_model` (159), `toggle_editor_system_reasoning` (231) | Cloud-Modell-Liste live | `backends.cloud_api.CloudAPIBackend.list_models` | erst mit Speichern |
| Kleinkram | `set_editor_emoji` (1245), `set_editor_role` (1251), `toggle_emoji_picker` (1277) | – | – | – |
| Gedächtnis | `clear_agent_memory` (1430) | alle Einträge eines Agenten (zweiter Klick) | `agent_memory.get_agent_memory().clear` | ChromaDB |
| Scheduler | `new_/edit_/save_/delete_/toggle_scheduler_job`, `cancel_scheduler_edit` (625–692, 1012–1061), Setter (747–825), `apply_cron_preset` (842), `set_scheduler_*_from_label` (799–929, 1003), `_compose_schedule_expr` (933), `_decompose_schedule_expr` (954), `_load_scheduler_jobs` (585), Anzeige `_human_cron/_interval/_once`, `_format_*` (459–583) | Job-CRUD, Ausdruck aus Feldern, Anzeige | `scheduler.get_job_store()` (`add/update/delete/enable/get/list_all`) | Scheduler-SQLite |

### MemoryBrowserMixin

| Bereich | Handler / Methode (Zeile) | Was | lib | Speichert |
|---|---|---|---|---|
| Gedächtnis | `open_memory_browser` (91), `select_memory_agent` (106), `set_memory_filter` (115), `_load_memory_collections` (217), `browse_memory_agent` (253) | Übersicht je Agent mit Anzahl, Einträge lesen | `agent_memory` (direkt `memory._client`, `memory._collection`) | – |
| Bearbeiten | `start_/cancel_memory_edit` (351–365), Setter (367–373), `save_memory_edit` (375) | Zusammenfassung prüfen, neu einbetten | `AgentMemory.update`, `summary_fits_index` | ChromaDB |
| Löschen | `delete_memory_entry` (303), `request_/cancel_clear_memory` (324–330), `clear_browsed_memory` (332) | einzeln / alles | `collection.delete`, `AgentMemory.clear` | ChromaDB |
| Index | `db_load_documents` (150), `confirm_clear_db` (155), `clear_db_index` (160), `db_toggle_orphans` (176), `db_deindex_document` (190), `db_delete_all_orphans` (201); `_refresh_db_documents` (139), `_reload_db_orphans` (183), `_deindex` (35) | Index je Dokument, Waisen, aus dem Index nehmen (Datei bleibt) | `file_manager.list_indexed/list_orphaned/delete_file`, `document_store.clear` — per `asyncio.to_thread` | ChromaDB |
| Export | `open_/close_bundle_export` (427–447), `toggle_bundle_export_agent` (449), `confirm_bundle_export` (456) | Download über `window.location.href` auf `/api/agents/export` | – | – |
| Import | `open_bundle_import` (471), `handle_bundle_upload` (481, Reflex-Upload), `close_bundle_import` (511), `toggle_bundle_import_agent` (519), `set_bundle_import_conflict` (526), `confirm_bundle_import` (531) | ZIP prüfen, als Base64 im State halten, schreiben | `agent_bundle.peek_bundle/import_bundle` | `agents.json`, `prompts/`, `settings.json`, Stimmen |

### PersonariumMixin

| Bereich | Handler / Methode (Zeile) | Was | lib | Speichert |
|---|---|---|---|---|
| Modal | `open_personarium` (61), `close_personarium` (70), `_refresh_personarium_faces` (79), `_refresh_personarium_untagged` (243), `_untagged_group_ids` (299) | Listen laden, Karten je Crop-Datei gruppieren | `vision_store.VisionStore.list_faces_with_summary/list_events_with_summary` | – |
| Identitäten | `personarium_start_/cancel_/save_rename` (174–215), `personarium_set_edit_name` (181), `personarium_delete_face` (217) | umbenennen, löschen | `VisionStore.rename_face/delete_face_with_assets`, `face_recognize.bump_enrollment_epoch` | Vision-SQLite |
| Embeddings | `personarium_open_embeddings` (146), `personarium_delete_embedding` (157), `_reload_personarium_embeddings` (120) | aufklappen, einzeln löschen | `VisionStore.list_embeddings/delete_embedding` | Vision-SQLite |
| Nachtaggen | `personarium_start_/cancel_tag` (421–432), Setter (434–440), `personarium_save_tag` (442, async) | Embedding aus dem Frame rechnen, zuordnen, Geschwister mit zuordnen | `face_enroll.enroll_face_from_event/confirm_group_events` | Vision-SQLite |
| Verwerfen | `personarium_dismiss_untagged` (336), `…_toggle_untagged` (350), `…_select_all_untagged` (363), `…_clear_untagged_selection` (370), `…_dismiss_selected` (375), `…_request_/cancel_dismiss_all` (393–401), `…_dismiss_all_untagged` (403) | Mehrfachauswahl, zweistufig | `VisionStore.dismiss_untagged_events` | Vision-SQLite |
| Re-Match | `personarium_rematch_untagged` (308, async) | alle Unzugeordneten neu erkennen | `face_enroll.rematch_untagged_faces` | Vision-SQLite |

### Zugangsdaten- und Plugin-Seite (Handler in `SM`)

`open_channel_credentials` (537) → `_open_channel_credentials` (541) füllt Felder aus `.env` und
Plugin-`settings.json`; `update_channel_credential` (671), `save_channel_credentials` (824, schreibt `.env`
per `dotenv.set_key` und Plugin-Settings, startet Kanal-Worker per `asyncio.create_task`),
`start_oauth_connection` (762, pollt 60 × 5 s), `disconnect_oauth` (803), `toggle_tool_plugin` (947,
`.disabled`-Marker), `toggle_channel_monitor/_listener/_auto_reply` (457–530, `message_hub.register`),
`set_channel_security_tier` (438). Hilfe-Fenster: nur `open_…/close_…`-Schalter (`_image_mixin.py:250-285`).

---

## 3. Anmeldung, Sitzungszugehörigkeit, Berechtigungen

### Anmeldung heute

- **Konten** in `data/accounts.json`, bcrypt; alte SHA-256-Hashes werden beim Login umgeschrieben
  (`session_storage.py:281-315`). Registrieren dürfen nur Namen aus `data/allowed_users.json`, leere
  Liste = niemand (`session_storage.py:60, 110-125`).
- **Cookie** `aifred_username` = `name.<ablauf>.<hmac-sha256>` (`auth.py:103-136`); Geheimnis aus
  `AIFRED_SESSION_SECRET` oder `data/.session_secret` (`auth.py:41-71`); Laufzeit
  `AUTH_COOKIE_MAX_AGE_DAYS` = 30 (`config.py:1578`). Gesetzt per JavaScript (`document.cookie`,
  `browser_storage.py:156`): nicht HttpOnly, kein Secure.
- **Ablauf:** `on_load` → JS liest das Cookie → `handle_username_loaded` prüft die Signatur → das Feld
  `logged_in_user` im State dieses Tabs. Ohne gültiges Cookie bleibt der Dialog offen; `do_login` prüft das
  Passwort und setzt das Cookie per Skript.
- **Abmelden** leert nur State und Browser-Cookie; die Signatur bleibt bis zum Ablauf gültig, es gibt
  keine Sperrliste.

| Weg | Prüfung |
|---|---|
| REST `/api/*` | Middleware verlangt gültige Signatur (`api/app.py:50-71`); ausgenommen Inject, Trigger, Announce, TTS-Einträge (eigenes Service-Token) und OAuth-Callback (`app.py:44-47, 61-64`) |
| Dateien `/_upload/*` | `AuthenticatedStaticFiles` (`lib/authenticated_static.py`), Ausnahme `html_preview` (`aifred.py:1219-1221`) |
| Reflex-Websocket `/_event` und `/_upload`-Handler | **keine**; Anmeldung ist nur das State-Feld. Backend lauscht auf `0.0.0.0:8002` (`rxconfig.py:67-71`). Im ganzen State prüfen 7 Handler `logged_in_user` (z. B. `_base.py:428-431`, `_session_mixin.py:90`), in den sechs Mixins hier keiner außer der Anmeldung selbst |

### Sitzungszugehörigkeit

- Jede Sitzungsdatei trägt `owner` (`session_storage.py:502-530`); Kanal-, Scheduler- und
  Webhook-Sitzungen gehören `MESSAGE_HUB_OWNER` (`config.py:1564`, `api/agents.py:70`).
- Browser: Liste `list_sessions(owner=logged_in_user)` (`_session_mixin.py:548`), Auto-Laden der zuletzt
  gesehenen (`session_storage.py:988-1000`, `_session_mixin.py:327-346`), Wechsel mit Besitzerprüfung in
  `switch_session` (`_session_mixin.py:153-168`); Sitzungen ohne `owner` werden zugelassen (Altlast).
  Löschen nur mit `expected_owner` (`session_storage.py:760-790`).
- **REST prüft keinen Besitzer:** `GET /api/sessions` liefert alle Sitzungen aller Konten
  (`api/chat.py:187-201`; `list_sessions` ohne `owner` filtert nicht, `session_storage.py:937, 972`, der
  Docstring 918 behauptet das Gegenteil). `/api/chat/history`, `/api/session/config`,
  `/api/browser/queue|stream/{session_id}` (`browser_bus.py:195-222`) nehmen jede `session_id`.

### Berechtigungen

- **Keine Benutzerrollen.** Jedes Konto darf alles. Agenten, Prompts, `settings.json`, Plugin-Schalter,
  Zugangsdaten, OAuth-Token, Scheduler, Gedächtnis, Dokumentindex und Personarium sind global; nur
  Sitzungen gehören einem Konto.
- **Tool-Stufen** (`security.py:33-37`): T0 lesen, T1 kommunizieren, T2 Daten schreiben, T3 System
  schreiben, T4 Admin. Höchststufe je Quelle `DEFAULT_TIER_BY_SOURCE` (40–49, Browser = T4), Überschreibung je
  Kanal in `settings.json` `channel_security_tiers` (Plugin-Tab, `SM:438-447`). Besitzer auf Kanälen
  bekommt `max(Kanal, OWNER_TIER = T2)` (63, 66–99); Besitzer ist der erste Eintrag der Erlaubnisliste,
  bei E-Mail nur mit SPF/DKIM/DMARC „pass“ (167–208). Scheduler und Webhook nehmen die Stufe aus Job bzw.
  Aufruf (77–82), Webhook gedeckelt (`api/agents.py:52-54`). Filter `filter_tools_by_tier` (235),
  Rückfrage `needs_confirmation` (469–483), Gedächtnis und Dokumentordner nur im Besitzer-Kontext
  `in_owner_context` (244–254).
- **Tool-Whitelist je Agent:** `agents.json` `tools` (None = alle), durchgesetzt in
  `agent_memory.py:518-519` und `prompt_loader.py:555`.
- **Plugin-Schalter:** Marker `.disabled` im Plugin-Ordner (`plugin_registry.py:41, 51-52, 263-284`); der
  Name wird gegen die vorhandenen Ordner geprüft (247–250). Kanalschalter `monitor/listener/auto_reply`
  in `settings.json` `channel_toggles` (`SM:449-530`).

### Was ist schon browserunabhängig?

| In `lib/`, ohne State nutzbar | Nur im State |
|---|---|
| Signatur und Service-Token (`auth.py`), Konten, Whitelist, `owner`, `list_sessions(owner)`, `delete_session(expected_owner)` (`session_storage.py`), Middleware (`api/app.py`), `AuthenticatedStaticFiles` | wer angemeldet ist (`logged_in_user` je Tab), Login-Ablauf und Meldungen, Besitzerprüfung beim Sitzungswechsel, Auto-Laden der letzten Sitzung |
| Stufen und Besitzerlogik (`security.py`), Plugin-Marker (`plugin_registry.py`), `credential_broker`, `oauth/broker.py` | Regeln der Diskussionsmodi (`AC:641-800`), Inkognito |
| Agenten-CRUD (`agent_config.py`), Bundles (`agent_bundle.py`), `AgentMemory`, `JobStore`, `VisionStore`, `face_enroll`, `file_manager`, `document_store` | `agent_tuning` im Speicher und das Schreiben der ganzen `settings.json` aus dem Tab (`SM:141-235`); `lib/agent_settings`-Helfer verlangen ein State-Objekt (`agent_settings.py:81-100`) |
| | Prompt-Dateien lesen/schreiben (`AE:1188-1309`), Tool-Angebot → Whitelist (`AE:1120-1142, 1335-1340`), Sampling-Defaults je Backend (`AC:307-396`), effektive Modell-ID (`AC:402-447`), Scheduler-Ausdruck und Anzeige (`AE:459-583, 933-984`), Kontext-Übersicht (`UC:167-238`) |

---

## 4. Einteilung

| Funktion | UI-Zustand (Vue) | Domäne (Dienst in `lib/`) | Speicherung |
|---|---|---|---|
| Anmelden, Registrieren, Abmelden | Dialog, Modus, Eingaben, Fehltext | Auth-Dienst: login, register, logout, me; Cookie per `Set-Cookie` | `accounts.json`, `allowed_users.json`, `.session_secret` |
| Sitzung nach Login | – | `most_recently_seen_session_id` | Sitzungsdateien (`last_seen`) |
| Agentenliste, Auswahl | Dropdown (Werte = IDs) | `agent_config` | `agents.json` |
| Agent anlegen, ändern, löschen | Formular, Dirty-Flag, Bestätigungen, Emoji-Auswahl | `agent_config` + ID- und Rollenprüfung | `agents.json`, `prompts/{de,en}/<id>/` |
| Prompt-Ebenen (Agenten, Automatik) | Tab, Sprache, Textfeld, Reset-Bestätigung | neuer Prompt-Dienst: Schlüssel listen, lesen, schreiben, mit Pfadprüfung | `prompts/` |
| Tool-Whitelist | Pills, Gruppenschalter | `tool_groups_on_offer` + Umrechnung „alle = None“, Tools abgeschalteter Plugins erhalten | `agents.json` `tools` |
| Cloud-Modell der System-Agenten | Provider, Modell | `CloudAPIBackend.list_models` | `agents.json` `model`, `cloud_provider`, `toggles` |
| Tuning je Agent (Sampling, Denken, Speed, RoPE, Modell, num_ctx) | Felder | Tuning-Dienst, einmal je Prozess: get/set mit Klammerung, Reset je Backend, Denk-Optionen, effektive Modell-ID | `settings.json` `agent_tuning`, `backend_models`; Modell-Cache (RoPE) |
| Temperatur-Modus, Runden, Konsens | Schalter | Einstellungs-Dienst | `settings.json` |
| Sitzungskonfiguration (Agent, Modus, Symposion) | Schalter | Sitzungs-Dienst mit den Regeln aus `AC:641-800` | Sitzungsdatei `config` |
| Inkognito | Schalter | Parameter des Zuges | heute keine (7.2) |
| Diskussions-Laufzeit (`sokrates_*`, `salomo_*`, `debate_*`) | Panels | Zug-Dienst sendet Ereignisse | Sitzung |
| Kontext-Übersicht | Anzeige | Funktion liefert Daten statt Debug-Zeilen | – |
| Gedächtnis | Agent, Filter, Entwurf, Bestätigung | `AgentMemory`: Übersicht, Einträge, update, delete, clear | ChromaDB `agent_memory_<id>` |
| Dokumentindex | Bestätigung, Waisen sichtbar | `file_manager`, `document_store` | ChromaDB `aifred_documents` |
| Bundle | Auswahl, Konflikt, Fehler | `agent_bundle` | `agents.json`, `prompts/`, `settings.json`, Stimmen |
| Scheduler | Formular, Presets, strukturierte Felder | `JobStore` + Prüfung (Ausdruck, Stufe 0–4, Kanal aus Registry) + Anzeigeformat | Scheduler-SQLite |
| Plugins, Kanäle | Liste, Schalter | `plugin_registry`, `message_hub`, Kanalstufen | `.disabled`, `settings.json` |
| Zugangsdaten | Formular | Zugangsdaten-Dienst: Felder beschreiben, speichern; Geheimnisse nie ausliefern | `.env`, Plugin-`settings.json` |
| OAuth | Status, Link | `oauth_broker`; Status als Ereignis | `data/oauth_tokens.json` |
| Audit | Tabelle | `security.load_audit_entries` | Audit-SQLite |
| Personarium | Modal, Inline-Edit, Auswahl, Tag-Modus, busy | `VisionStore`, `face_enroll`, `bump_enrollment_epoch` | Vision-SQLite, Crops |
| Hilfe-Fenster | ganz Vue (Texte aus i18n) | – | – |
| Enter sendet, Diktat bearbeiten, Auto-Scroll | Vue | – | heute global in `settings.json`; je Konto oder Gerät wäre passender (Frage an Peuqui) |
| Aufnahme starten | Vue (MediaRecorder) | STT-Upload | – |

---

## 5. Schnittstelle für Vue

### Vorhandene Endpunkte, die passen

| Endpunkt | Datei:Zeile | für | Lücke |
|---|---|---|---|
| `GET /agents/export` | `api/agents.py:199` | Export | – |
| `POST /agents/import/peek`, `POST /agents/import` | `api/agents.py:234, 248` | Import | Fehler in `agent_bundle` (7.4) |
| `GET /oauth/{p}/status`, `GET /oauth/{p}/auth-url`, `DELETE /oauth/{p}`, Callback | `api/agents.py:129-191` | OAuth | `redirect_uri` kommt vom Client |
| `POST /session/config` | `api/chat.py:295` | Agent, Modus, Symposion | keine Besitzerprüfung, keine Modusregeln |
| `GET /sessions` | `api/chat.py:187` | – | listet alle Konten |
| `GET`/`PATCH /settings` | `api/core.py:133, 180` | teilweise | liest Schlüssel, die niemand schreibt (7.2) |
| `/vision/face/list`, `/{id}/details`, `/{id}/rename`, `DELETE /{id}`, `DELETE /embedding/{id}`, `POST /enroll` | `api/vision.py:294-484` | Personarium-Grundfunktionen | Nachtaggen, Verwerfen, Re-Match fehlen |
| `GET /browser/stream/{session_id}` | `api/browser_bus.py:222` | Vorbild für SSE | keine Besitzerprüfung |

### Neue Endpunkte (Vorschlag)

| Bereich | Endpunkte |
|---|---|
| Anmeldung | `POST /auth/login`, `POST /auth/register`, `POST /auth/logout`, `GET /auth/me`; Login und Register von der Cookie-Pflicht ausgenommen, Cookie HttpOnly, Secure, SameSite=Lax |
| Agenten | `GET /agents`, `GET /agents/{id}`, `POST /agents`, `PATCH /agents/{id}`, `DELETE /agents/{id}` |
| Prompts | `GET /agents/{id}/prompts`, `GET`/`PUT /agents/{id}/prompts/{key}?lang=de\|en`, dasselbe unter `/automatik/prompts` |
| Tools | `GET /agents/{id}/tools` (Angebot je Gruppe mit Stufe und Freigabe) |
| Cloud | `GET /cloud/{provider}/models` |
| Tuning | `GET`/`PATCH /agents/{id}/tuning`, `POST /agents/{id}/tuning/reset`, `GET /agents/{id}/thinking-options`, `GET /agents/context-overview` |
| Einstellungen | `GET`/`PATCH /settings` bereinigt (nur echte Schlüssel) |
| Gedächtnis | `GET /memory` (Agenten mit Anzahl), `GET /memory/{agent}?type=`, `PATCH`/`DELETE /memory/{agent}/{entry}`, `DELETE /memory/{agent}` |
| Dokumentindex | `GET /documents/index`, `GET /documents/index/orphans`, `DELETE /documents/index/{path}`, `DELETE /documents/index`, `DELETE /documents/index/orphans` |
| Scheduler | `GET`/`POST /scheduler/jobs`, `PUT`/`DELETE /scheduler/jobs/{id}`, `PATCH /scheduler/jobs/{id}/enabled`, `GET /scheduler/options` (Kanäle, Stufen, Presets) |
| Plugins | `GET /plugins`, `POST /plugins/tools/{name}/enable\|disable`, `PATCH /plugins/channels/{name}` (monitor, listener, auto_reply, tier), `GET`/`PUT /plugins/{name}/credentials` (Geheimnisse nur als „gesetzt“) |
| Audit | `GET /audit?limit=` |
| Personarium | `GET /vision/faces/untagged`, `POST /vision/faces/untagged/dismiss`, `POST /vision/faces/untagged/{event_id}/tag`, `POST /vision/faces/untagged/rematch` (als Job) |

### Ereignisse (SSE)

Die meisten Änderungen dieses Bereichs gelten für die ganze Installation, nicht für eine Sitzung. Ein
Strom je Sitzung reicht deshalb nicht; Vorschlag: zusätzlich `GET /api/events` für globale Ereignisse.

| Ereignis | Daten | Ersetzt heute |
|---|---|---|
| `agents.changed` | ids | `_agents_json_revision`, Dropdown-Refresh |
| `settings.changed` | Schlüssel | mtime-Poll im Tick (`_base.py:242-262`) |
| `tuning.changed` | agent | dito |
| `plugins.changed`, `channels.changed` | name, Zustand | Neuladen des Plugin-Tabs |
| `scheduler.job_changed`, `scheduler.job_ran` | job_id, last_run, next_run | – |
| `memory.changed` | agent_id, count | – |
| `documents.index_changed` | – | – |
| `oauth.status` | provider, status | 5-min-Poll (`SM:762-797`) |
| `personarium.changed`, `personarium.job` | rematch/enroll, Fortschritt, Ergebnis | busy-Flags im State |
| `session.config_changed` (Sitzungsstrom) | session_id, config | mtime der Sitzungsdatei |
| `debate.*` (Sitzungsstrom) | Kritik, Runde, Synthese | `sokrates_*`, `salomo_*`, `debate_*` |

---

## 6. Abhängigkeiten zu anderen Mixins

| Mixin | benutzt aus | wird benutzt von |
|---|---|---|
| AU | Session (`_session_initialized`, `available_sessions`, `session_id`, `_clear_chat_internal`, `_load_latest_session`, `new_session`, `refresh_session_list`), Base (`add_debug`, `debug_messages`), `_audio_player_mixin.discard_audio_runtime_state`, `_tts_streaming_mixin.discard_tts_backend_state` | Backend `on_load` (`_backend_mixin.py:758-767`); `logged_in_user` liest Session, Chat, Image, Document, Base |
| UC | Base (`auto_refresh_enabled`), Backend (`backend_type`), AC (`_effective_model_id`, `_ui_agent_list`), Settings (`_save_settings`), Kontext (`_log_history_utilization`, `_min_agent_context_limit`) | Chat, `multi_agent` (`temperature_mode`) |
| AC | Backend (`available_models_dict`, `backend_id`, `backend_type`, `_effective_automatik_id`, `_label_for`, `_show_model_calibration_info`, `SAME_AS_AIFRED`), TTS (`enable_tts`), UC (`temperature_mode`), Settings (`ui_language`, `_save_settings`), Session (`_persist_session_config`), AE (`_agent_dropdown_items` als Abhängigkeit) | Chat, Session, `lib/multi_agent.py`, `lib/llm_engine.py` (State-Felder direkt), Settings (`_save_settings` liest `agent_tuning`) |
| AE | Calibration (`_agents_json_revision`), Settings (`audit_log_entries`, `channel_allowlists`, `channel_security_tiers`, `tool_plugin_toggles`, `ui_language`), MB (`db_clear_confirm`, `db_load_documents`, `open_memory_browser`), TTS-Config (`editor_tts_engine`, `tts_list_revision`, `_load_editor_tts_settings`, `check_tts_status`), Storage (`load_storage_files`), STT-Settings (`load_stt_settings`) | Settings (`toggle_tool_plugin` → `_refresh_editor_tools`, `SM:1002`), MB |
| MB | AE (`agent_editor_mode`, `editor_agent_id`, `_refresh_agent_dropdown`) | AE |
| PE | – (Oberfläche ruft `open_multipose`, `_multipose_mixin.py:99`) | – |

Oberfläche: TTS-Abschnitt des Config-Tabs → TTS-Config-Mixin; Plugin-Tab → Settings (`channel_toggles`,
`toggle_channel_*`, `open_channel_credentials`, `set_channel_security_tier`, `toggle_tool_plugin`) und über
`settings_event_name` einen beliebigen State-Handler (`plugins.py:92-105`); Zugangsdaten-Seite → Settings;
Hilfe-Fenster → Image-Mixin.

---

## 7. Auffälligkeiten

### 7.1 Workarounds wegen Reflex (fallen mit Vue weg)

- **Ungesteuerte DOM-Felder:** Name, Beschreibung und Prompt werden per `call_script` mit `setTimeout(50)`
  befüllt (`AE:1174-1186, 1221-1243, 1470-1481, 1483-1503`) und beim Speichern per JS als JSON
  zurückgelesen (`config_sections.py:37-44, 320-323`). `_editor_typed` (`AE:336-348, 1326-1333`) schützt
  davor, nach einem Reconnect leere Felder zu speichern (Vorfall 10.10.2026).
- **Route und Init-Flag:** `_agent_editor_needs_init`/`_agent_editor_start_tab` (`AE:264-328`) für die
  Rückkehr auf `/agent-editor`; eigene Route wegen der Babel-Grenze von 500 KB (`page.py:27-38`).
- **Explizite Setter**, weil Reflex 0.9 keine `set_<var>` mehr erzeugt (`AU:44-52`, `AE:745-825`,
  `MB:367-373`, `PE:181-183, 434-440`).
- **Auswahl über Anzeige-Labels statt IDs:** Agent (`AE:259-262`), Scheduler-Agent (`AE:1003-1010`),
  Gedächtnis-Agent per `startswith` (`MB:106-113`), Rückwärtssuche in `TranslationManager._translations`
  (`AE:724-731`), Radix-Stringvergleich (`PE:109-118`). Gleiche Emoji+Namen kollidieren.
- `sampling_reset_key` (`AC:93, 396`) erzwingt Neuaufbau der Felder; roher JS-Ausdruck im Gruppenschalter
  (`tool_pills.py:78`).
- **Revisionszähler** statt Ereignissen: `_agent_dropdown_items` als Abhängigkeit berechneter Vars
  (`AC:733, 998-1042`), `_agents_json_revision` (`AE:1373-1375`), `tts_list_revision`; berechnete Vars lesen
  `agents.json` bei jeder Auswertung (`AC:741-742`, `AE:989-990`).
- **Bundle-Import über Reflex-Upload:** ZIP als Base64 im synchronisierten State zwischen zwei Events
  (`MB:85, 504`); dieselbe Funktion gibt es als REST (`api/agents.py:234-279`). Export per
  `window.location.href` (`MB:456-465`).
- **Cookie lesen** per `call_script`-Rückruf mit Guard `_session_initialized`; SSE-Start per Skript
  (`AU:92-104, 207-211, 235-243`).
- **OAuth:** Auth-URL vorab im State wegen Popup-Blocker (`SM:642-647`); `start_oauth_connection` pollt
  5 min in einem normalen Handler (`SM:762-797`) und hält solange die Event-Warteschlange des Tabs.
  Ebenso rechnen `personarium_rematch_untagged` und `personarium_save_tag` InsightFace im Handler
  (`PE:308-334, 442-499`).
- **Plugin-Tab beim Kompilieren gebaut** (`plugins.py:50-123`): ein neuer Plugin-Ordner erscheint erst nach
  Neustart.
- **Abgleich zwischen Tabs** nur über die mtime von `settings.json` im 500-ms-Tick (`_base.py:242-262`).

### 7.2 Toter Code (Verweise ohne Klammer gesucht, auch in `assets/`)

| Was | Stelle |
|---|---|
| `agent_editor_open` nur geschrieben | `AE:51, 308, 353` |
| `_editor_new_agent_id` nur geschrieben | `AE:114, 1492` |
| Schleife mit `pass` in `editor_agent_dropdown_value` | `AE:141-144` |
| `reset_sokrates_state`, `reset_salomo_state` ohne Aufrufer | `AC:817-832` |
| `debate_user_interjection` nur geschrieben | `AC:119, 825`, `_session_mixin.py:668` |
| `CORE_AGENTS`, `_FEATURE_EMOJI["personality"]` ungenutzt | `AC:729, 59` |
| `UC.num_ctx` ungenutzt | `UC:39` |
| Bundle `sampling.json`: exportiert `<id>_*`-Schlüssel (`agent_bundle.py:124`), die es nicht mehr gibt (Tuning liegt unter `agent_tuning`; die Produktions-`settings.json` hat keinen solchen Schlüssel) → immer `{}`, Import-Zweig 303–311 wirkungslos, Tuning geht beim Bundle verloren | `agent_bundle.py:124, 303-311` |
| Hub liest `agent_memory_enabled` aus `settings.json`, niemand schreibt es → Hub immer mit Gedächtnis | `message_processor.py:639`, `SM:141-235` |
| `/api/settings` liest `rope_factor` aus `agent_tuning` (nicht persistiert, `agent_settings.py:40-51`), `enable_thinking`, `multi_agent_mode`, `automatik_rope_factor` — alles von `_save_settings` nie geschrieben | `api/core.py:163-171` |

### 7.3 Doppelte Logik und Festwerte

- **Gedächtnis:** der Browser liest und löscht direkt in der Chroma-Collection über private Member
  (`MB:235, 282, 312-313`) statt `AgentMemory._all_entries`/`delete` (`agent_memory.py:173-182, 259-276`).
  Zwei „alles vergessen“-Wege mit eigener Bestätigung (`AE:1430-1460`, `MB:332-349`).
- **Bundle-Import** zweimal: State (`MB:481-561`) und REST (`api/agents.py:234-279`).
- **Feste Agenten** an mehreren Stellen: `is_default` in der UI (`config_view.py:372-377`),
  `CANONICAL_AGENTS` (`agent_tuning.py:34`), Löschsperre (`agent_config.py:395-396`), Emojis
  `_PERSONALITY_EMOJI` (`AC:50-55`) und `_SECONDARY_MODEL_EMOJI` (`AC:863-866`) neben `agents.json`.
  Betrifft den Beschluss „Feste Agenten“ im README.
- **Modusnamen** an vier Stellen (`AC:663-669, 716-726, 731, 1057-1062`); die Modusregeln gibt es nur im
  State, `POST /session/config` umgeht sie.
- **Kontextgrenze** zweimal: `set_aifred_rope_factor` (`AC:551-565`, nur AIfred/Sokrates/Salomo, llama.cpp
  nur aus dem Cache) gegen `_auto_ctx_for_agent` (`UC:134-165`, YAML zuerst, alle Agenten).
- **Wochentags- und Monatsnamen** fest im Code (`AE:467-482`) neben den i18n-Schlüsseln (`AE:856-897`).
- **Feste UI-Listen:** Rollen (`config_view.py:339` statt `VALID_ROLES`, `agent_config.py:28`),
  Scheduler-Kanäle (`scheduler.py:333` statt Registry), Stufen `0…4` (`scheduler.py:354` statt
  `TIER_I18N_KEYS`), Emoji-Auswahl (`config_view.py:228-241`), Tempo/Tonhöhe (`config_sections.py:103-117`).
- **Texte ohne i18n:** Login-Meldungen (`AU:115-156`) und Beschriftungen (`login.py:36-97`), Toasts
  (`AE:1347, 1405, 1455-1460`), Bundle-Fehler (`MB:493, 500, 551`), Personarium-Status (`PE:201-485`).
- `toggle_tool_plugin` kennt das Plugin `vision` beim Namen (`SM:994`).

### 7.4 Fallen

- **Whitelist verliert Tools abgeschalteter Plugins:** `editor_tools` enthält nur das gerade Angebotene
  (`AE:1120-1130`), gespeichert werden nur diese Namen (`AE:1335-1340`). Wer einen Agenten speichert,
  während ein Plugin aus ist, entzieht ihm dessen Tools dauerhaft. Sind alle angebotenen an, wird `None`
  gespeichert, also auch jedes später eingeschaltete Plugin.
- **Sprach-Fallback im Prompt-Editor:** fehlt die DE-Datei, steht die EN-Datei mit `[EN only]` im Feld
  (`AE:1207-1219`); Speichern entfernt das Präfix und schreibt den englischen Text als DE-Datei
  (`AE:1301-1309`).
- **Bundle-Import „rename“** übernimmt `config.json` unverändert (`agent_bundle.py:279`): die Prompt-Pfade
  zeigen weiter auf `<alte_id>/…`; die Kopie liest und bearbeitet die Prompts des Originals, und
  `delete_agent` der Kopie löscht per `rmtree` den Prompt-Ordner des Originals (`agent_config.py:402-419`).
- **Bundle-Import schreibt jeden Schlüssel aus `sampling.json` in `settings.json`** (`agent_bundle.py:310-311`),
  auch `channel_security_tiers`, das `_save_settings` aus der Datei übernimmt (`SM:217`); `peek` zeigt das
  nicht.
- **Neuer Agent:** `create_agent` wirft bei vorhandener ID (`agent_config.py:348-349`), `save_agent_editor`
  fängt das nicht (`AE:1387-1393`). `update_agent` prüft die Rolle nicht (`agent_config.py:424-444`),
  `set_editor_role` nimmt jeden Wert (`AE:1251-1254`). `agents.json` wird ohne Sperre und nicht atomar
  geschrieben (`agent_config.py:205-209`), anders als `accounts.json` (`session_storage.py:206-231`).
- **Scheduler:** `add()` prüft den Ausdruck nicht und legt Jobs ohne `next_run` an, die nie laufen
  (`scheduler.py:140-160`); `update()` wirft `ValueError` (181–183), den `save_scheduler_job` nicht fängt
  (`AE:1050-1058`); `int()` auf die Stufe ohne Prüfung (`AE:1048, 1057`); stille Ersatzwerte Intervall 60
  und Datum `2026-01-01` (`AE:946, 949-950`) — ein „einmal“-Job ohne Datum läuft sofort.
- **Stille Verwerfung:** ungültige Sampling- und Temperaturwerte (`AC:304-305, 634-635`).
- **Inkognito** gilt nur im Tab, wird nicht gespeichert und ist nach dem Neuladen wieder aus.
- **`settings.json` ist global** und wird von jedem Tab komplett aus seinem State geschrieben
  (`SM:141-235`); Tabs gleichen sich nur über den mtime-Poll ab, der zuletzt Speichernde gewinnt.
- `limit=2000` im Personarium, obwohl der Docstring „ohne Deckel“ sagt (`PE:248-249, 259`).
- Doku-Abweichungen: `list_sessions`-Docstring (`session_storage.py:918`) gegen Code (937, 972);
  `SessionConfigRequest` nennt einen Modus `sokrates`, den es nicht gibt (`api/chat.py:279`).

### 7.5 Sicherheitsrelevant

Jede öffentliche Methode ist ein Event-Handler, den jeder verbundene Client mit eigenen Argumenten
auslösen kann. Der Websocket selbst ist nicht angemeldet (Abschnitt 3).

| Befund | Stelle | Folge |
|---|---|---|
| Kein Login-Check in den Handlern dieses Bereichs | alle sechs Mixins (Gegenbeispiel `_base.py:428-431`) | Wer den Websocket auf 8002 erreicht (LAN, `rxconfig.py:67`), kann ohne Anmeldung Agenten ändern und löschen, Prompts schreiben, Plugins schalten, Jobs anlegen, Gedächtnis und Index leeren, Personarium ändern, Bundles importieren |
| Pfad-Traversal im Prompt-Editor | `set_editor_prompt_lang` (`AE:1233`), `set_editor_prompt_tab` (`AE:1221`) ungeprüft → `_load_editor_prompt` (`AE:1192-1206`) liest, `_save_editor_prompt_to_disk` (`AE:1289-1309`) schreibt `PROMPTS_DIR/<lang>/automatik/<tab>.txt` (bei Agenten `<lang>` frei) | Lesen und Schreiben beliebiger `.txt`-Dateien mit den Rechten des Dienstes; Inhalt kommt über `save_agent_editor` (`AE:1332-1333`) |
| Agent-ID aus dem DOM ungeprüft | `AE:1382` → `create_agent` → `_create_prompt_files` (`agent_config.py:447-465`) | `../` legt Ordner und Vorlagen außerhalb von `prompts/` an; der Bundle-Import prüft mit `_AGENT_ID_RE` (`agent_bundle.py:51`), der Editor nicht |
| Geheimnisse im Browser | `_open_channel_credentials` liest `.env`-Werte (`SM:583`) in `channel_credential_values`; nur optisch maskiert (`credentials.py:21-44`) | jeder Client mit offener Zugangsdaten-Seite hat API-Schlüssel und Passwörter im Klartext |
| Jobstufe frei | `set_scheduler_edit_tier` (`AE:759-761`), `AE:1048, 1057`; der Scheduler nimmt die Stufe aus dem Job (`security.py:77-82`) | unbeaufsichtigter Job mit T4 |
| Kanalstufe ohne Grenzen | `SM:438-447` | jede Zahl für jeden Kanalnamen |
| REST ohne Besitzerprüfung | `api/chat.py:187-201, 203, 295`, `api/browser_bus.py:195-222` | jedes Konto liest Sitzungen anderer Konten und abonniert deren Strom |
| Keine Rollen | – | jedes Konto ist Admin |
| Konten-Aufzählung, keine Bremse | `AU:118-124, 151-153` | Namen und Whitelist erratbar, beliebig viele Versuche |
| Cookie | `browser_storage.py:156, 159-166` | nicht HttpOnly, kein Secure; Abmelden widerruft nicht (30 Tage) |
| Gedächtnis mit beliebiger `agent_id` | `AE:1430-1460` → `_collection` = `get_or_create` (`agent_memory.py:74-82`) | legt Collections für beliebige Namen an |
| Bundle setzt Einstellungen und löscht fremde Prompt-Ordner | 7.4 | – |

**Folgerungen für den Umbau:** jeder Endpunkt hinter der Cookie-Pflicht (Login und Register
ausgenommen), Cookie per `Set-Cookie` HttpOnly/Secure/SameSite=Lax, Widerruf beim Abmelden und Bremse beim
Login; Besitzerprüfung als FastAPI-Dependency für alles mit `session_id`; IDs und Pfade an der lib-Grenze
prüfen (eine Regel für Agent-IDs, `lang ∈ {de, en}`, Prompt-Schlüssel aus der Agenten-Konfiguration bzw.
den Automatik-Dateien, Stufe 0–4); Geheimnisse nie ausliefern, nur „gesetzt“ und Ersetzen. Offen für
Peuqui: braucht es Rollen (Admin und Benutzer), oder bleiben alle Konten gleichberechtigt?
