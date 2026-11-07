# Inventar: LLM-Backends, Modellwahl, Kalibrierung, Einstellungen

Stand: 07.11.2026, Zweig `vue-frontend`. Bestandsaufnahme für Schritt 1 des Umbaus (siehe
`README.md` daneben). Gelesen: `aifred/state/_calibration_mixin.py`, `_backend_mixin.py`,
`_settings_mixin.py`, `aifred/ui/settings_accordion/*` und die lib-Module, die sie benutzen.

Kürzel in den Belegen:

| Kürzel | Datei |
|---|---|
| CM | `aifred/state/_calibration_mixin.py` |
| BM | `aifred/state/_backend_mixin.py` |
| SM | `aifred/state/_settings_mixin.py` |
| ACM | `aifred/state/_agent_config_mixin.py` |
| UI/… | `aifred/ui/settings_accordion/…` |

Verwandte Doku: `calibration-strategy.md`, `calibration-vllm.md`, `service-status.md`,
`browser-push-bus.md`.

---

## 1. State-Felder

### 1.1 CalibrationMixin

Öffentliche Felder (CM:194-241):

| Feld | Typ | Zweck | liest die Oberfläche |
|---|---|---|---|
| `is_calibrating` | bool | Spinner, Knöpfe sperren; nur diese Sitzung (CM:194) | ja (UI/calibration_picker.py:159, 176, 191) |
| `calibration_cancel` | bool | Abbruchwunsch, vom Hintergrundlauf alle 2 s gelesen (CM:201, 835) | nein |
| `llamaswap_revision` | int | Zähler, damit berechnete Vars nach Kalibrierung die YAML neu lesen (CM:207, 3235) | indirekt (`_tts_config_mixin.py:90`) |
| `calibration_mode` | str | `legacy` oder `ai` (CM:212, 269-283) | ja (UI/backend_section.py:291) |
| `calibration_allow_hybrid` | bool | CPU-Offload in der Kalibrierung erlauben (CM:217, 257-266) | ja (UI/backend_section.py:308) |
| `calibration_matrix` | dict[str, bool] | Häkchen der Matrix, Schlüssel `"<vlm>|<tts>"` (CM:233) | über die Zeilen-Vars |
| `calibration_vllm_resweep` | bool | vLLM: nur Topologie des Betriebspunkts neu vermessen (CM:240) | ja (UI/calibration_picker.py:240) |
| `calibration_vllm_dry_run` | bool | vLLM: messen, nichts persistieren (CM:241) | ja (UI/calibration_picker.py:258) |

Berechnete Vars:

| Var | Liefert | Quelle | Beleg |
|---|---|---|---|
| `has_dashscope_key` | bool, sperrt KI-Modus | `credential_broker` oder `DASHSCOPE_API_KEY` | CM:285-293 |
| `calibration_ai_label` | `"🤖 KI: <modell>"` | `agents.json` → Agent `calibration` | CM:671-686 |
| `calibration_matrix_header` | Spaltenköpfe (TTS-Engines) | `tts_service.installed_gpu_engine_keys` | CM:434-452 |
| `calibration_matrix_rows` | Zeilen `CalibrationRow` mit Zellen (`checked`, `already_calibrated`, `calibration_failed`) | `model_vram_cache`, `vision_routing.vlm_calibration_choices` | CM:454-560 |
| `vllm_burnin_matrix_rows` | dieselbe Matrix, Zelle = Burn-in-Paar | `sidechannel_vram_cache.fits`, `operating_points.is_vllm_calibrated` | CM:299-354 |
| `vllm_model_calibrated` | grüner Punkt am vLLM-Knopf | `operating_points.is_vllm_calibrated` | CM:412-432 |
| `vllm_sidechannel_summary` | Zeilen für ein Popover | `vlm_vram_cache`, `tts_service` | CM:356-410 (tot, siehe 7.2) |

Privat und modulweit:

| Name | Bedeutung | Beleg |
|---|---|---|
| `_agents_json_revision` | Zähler, damit `calibration_ai_label` `agents.json` neu liest; erhöht vom Agenten-Editor | CM:247; `_agent_editor_mixin.py:1375` |
| `_cal_debug_buffer` (Modul) | Zeilenpuffer des Hintergrundlaufs, alle 2 s in `debug_messages` geleert | CM:34, 692-708, 858-867 |
| `_vllm_cal_target` (Modul) | `(checkpoint, eintragsname)` des vLLM-Laufs, vom Guard gesetzt, vom Dispatch gelesen | CM:39, 777-792, 912 |
| `CalibrationCell`, `CalibrationRow` | TypedDicts für `rx.foreach` | CM:64-94 |

### 1.2 BackendMixin

Öffentliche Felder (BM:89-158):

| Feld | Typ | Zweck | liest die Oberfläche |
|---|---|---|---|
| `backend_type`, `backend_id` | str | gewähltes Backend, beide immer gleich gesetzt | ja (UI/backend_section.py:116, 215ff) |
| `current_backend_label` | str | Anzeige-Label (Mobil-Select) | ja (UI/backend_section.py:93) |
| `backend_url` | str | URL des Backends, aus `config.BACKEND_URLS` | nein (State, Plugins) |
| `cloud_api_provider`, `cloud_api_provider_label`, `cloud_api_key_configured` | str/str/bool | Cloud-Anbieter und Schlüsselstatus | ja (UI/backend_section.py:366, 372) |
| `available_backends_dict` | dict | id → Label, fest im Code | indirekt |
| `available_backends`, `available_backends_list` | list | GPU-verträgliche Backends (ids/Labels) | ja (UI/backend_section.py:96, 103-112) |
| `available_models_dict` | dict[id, label] | Modellkatalog des Backends | über `available_models_rich` |
| `available_models` | list[label] | dieselben Labels als Liste | nein (nur Zählung, siehe 7.2) |
| `vision_models_cache` | list[id] | Vision-fähige Modelle | nein (`_image_mixin.py`) |
| `available_vision_models_list` | list[label] | Labels dazu | nein (tot) |
| `available_vision_models_rich` | list[dict] | Vision-Dropdown mit Badge (Chat-LLM / No Swap / Swap) | ja (UI/agent_model_rows.py:200-209) |
| `automatik_model`, `automatik_model_id` | str | Automatik-LLM (leer = wie AIfred) | über `automatik_model_select_id` |
| `backend_healthy`, `backend_info`, `model_count` | bool/str/int | Status nach Init | nein (geschrieben, nie gelesen) |
| `backend_switching` | bool | Wechsel/Neustart läuft, sperrt Knöpfe | ja (viele Stellen) |
| `backend_initializing` | bool | „wird initialisiert…“ | ja (`ui/chat_display.py:444, 559`) |
| `gpu_detected`, `gpu_name`, `gpu_compute_cap`, `gpu_count`, `gpu_vram_gb`, `gpu_all_names` | div. | GPU-Info | ja (UI/backend_section.py:139-183) |
| `gpu_warnings` | list | Warnungen der Erkennung | nein (tot) |
| `enable_yarn`, `yarn_factor`, `yarn_factor_input`, `yarn_max_factor`, `yarn_max_tested`, `vllm_max_tokens`, `vllm_native_context` | div. | YaRN des alten Direkt-vLLM-Pfads | nein (tot, siehe 7.2) |

Berechnete Vars: `gpu_display_text` (BM:168-187), `gpu_compatible_text` (BM:189-198),
`backend_supports_dynamic_models` (immer `True`, BM:200-207), `backend_label` (BM:209-212, tot),
`automatik_model_select_id` (Sentinel `__same_as_aifred__`, BM:214-217),
`automatik_available_models_rich` (BM:219-228), `available_models_rich` (BM:1179-1185).
Property `_effective_automatik_id` (BM:230-265), von `lib/research_tools.py:112` gelesen.

Privat: `_backend_initialized` (Sitzung fertig initialisiert, BM:132), `_on_load_running`
(Doppelaufruf-Sperre, BM:134), `_model_preloaded` und `_automatik_model_context_limit` (tot,
BM:133, 161), `_min_agent_context_limit` (kleinster Kontext der Agenten für die History-Anzeige,
BM:162, 1058; gelesen in `_session_mixin.py:385`).

Prozessweit in `aifred/state/_base.py:69-81`: `_global_backend_initialized`,
`_global_backend_state` (backend_type, url, Modellkatalog, Vision-Listen, `gpu_info`,
`_init_complete`), `_backend_init_lock`.

### 1.3 SettingsMixin

Öffentliche Felder (SM:97-130):

| Feld | Typ | Zweck |
|---|---|---|
| `ui_language`, `user_name`, `user_gender` | str | Sprache, Name, Anrede |
| `channel_toggles`, `channel_security_tiers` | dict | Message-Hub: Schalter und Stufe je Kanal |
| `channel_credentials_modal_open`, `channel_credentials_editing`, `channel_credentials_display_name`, `channel_credential_values`, `channel_credential_fields`, `channel_cred_show_password` | div. | Formular der Zugangsdaten (Seite `/credentials`) |
| `oauth_connect_status`, `oauth_rights_missing`, `oauth_connect_provider`, `oauth_auth_url` | div. | OAuth-Verbinden im Formular |
| `tool_plugin_toggles`, `channel_allowlists` | dict | Plugin-Manager im Agenten-Editor |
| `audit_log_entries` | list | Audit-Log-Reiter |

Privat: `_last_settings_mtime` (letzte gesehene mtime von `settings.json`, SM:133),
`_last_session_mtime`, `_last_pushed_debug_len` (SM:134-135, für den 500-ms-Tick),
`_credentials_return_to` (Rücksprungpfad, SM:534).

---

## 2. Handler und interne Methoden

### 2.1 CalibrationMixin

| Methode | Art | Tut | lib | Speichert |
|---|---|---|---|---|
| `toggle_calibration_vllm_resweep`, `toggle_calibration_vllm_dry_run` | Handler | Schalter umlegen (CM:249-255) | – | – |
| `toggle_calibration_allow_hybrid` | Handler | Schalter, sofort in Datei (CM:257-266) | `settings` | `settings.json` |
| `set_calibration_mode` | Handler | `legacy`/`ai` (CM:268-283) | `settings` | `settings.json` |
| `open_calibration_picker` | Handler | Matrix-Schlüssel für alle Zellen anlegen (CM:562-576) | `vision_routing`, `tts_service` | – |
| `set_calibration_matrix_cell` | Handler | Häkchen setzen; beim Anhaken Fehlervermerk löschen (CM:616-669) | `model_vram_cache` | `model_vram_cache.json` |
| `reset_vlm_vram_cache` | Handler | VLM-Spitzenwerte verwerfen (CM:578-589) | `vlm_vram_cache.clear` | `vlm_vram_cache.json` |
| `cancel_calibration` | Handler | Flag + prozessweites Cancel (CM:710-724) | `calibration_gate.request_cancel` | – |
| `calibrate_context` | Handler, `background=True` | Guards, Gate setzen, Lauf treiben, Puffer leeren, Cancel prüfen (CM:726-856) | `calibration_gate`, `llamaswap_io` | – |
| `_calibrate_context_impl` | intern, async gen | Dispatch vLLM / llama.cpp / Ollama; Ollama-RoPE-Lauf inline (CM:1065-1236) | `BackendFactory`, `model_vram_cache.add_ollama_calibration` | `model_vram_cache.json` |
| `_calibrate_vllm_impl` | intern | llama-swap stoppen, GPU-Orc räumen, Side-Channel-Burn-in, `calibrate_vllm_checkpoint` im Thread, llama-swap starten (CM:891-1063) | `vllm_flow`, `sidechannel_burnin`, `process_utils`, `gpu_orc_client` | `data/operating_points/*.yaml`, llama-swap-Config, `data/logs/vllm_calibration/<eintrag>/`, `sidechannel_vram_cache.json` |
| `_calibrate_llamacpp` | intern | gesamte llama.cpp-Orchestrierung, Schritte unten (CM:1453-3240) | `calibration.*`, `vlm_stress_prewarm`, `vision_gpu_select`, `tts_service`, `process_utils`, `gguf_utils` | llama-swap-Config, `model_vram_cache.json`, `vlm_vram_cache.json`, Sitzungsdatei |
| `_calibrate_vlm_speed_flavour` | intern | Speed-Variante einer VLM-/Kombi-Variante (CM:1280-1451) | `calibrate_tts_variant_from_base`, `add_llamaswap_vlm_variant` | llama-swap-Config, Cache |
| `_test_and_save_thinking` | intern | Denkfähigkeit testen (Ollama) (CM:1242-1274) | `backend.test_thinking_capability`, `set_thinking_support_for_model` | `model_vram_cache.json` |
| `_cal_debug`, `_flush_cal_console` | intern | Zeile ins Log + Puffer; Puffer in die Konsole (CM:692-708, 858-867) | `logging_utils` | Debug-Log |
| `_warn_if_calibration_incomplete` | intern | nach Abbruch warnen (CM:869-889) | `model_vram_cache` | – |
| `_persist_calibration_progress` | intern | Sitzungsdatei mit Konsole schreiben (CM:591-614) | `_save_current_session` (SessionMixin) | `data/sessions/<id>.json` |
| `_clear_failure_for_matrix_key` | intern | Fehlervermerk der Variante löschen (CM:639-669) | `model_vram_cache` | `model_vram_cache.json` |
| `_show_model_calibration_info`, `_show_tensor_split_info`, `_show_speed_split_info` | intern | Kontext, Split, Speed-Split in die Konsole (CM:3246-3379) | `llamaswap_io`, `model_vram_cache` | – |
| `restart_backend` | Handler (nicht background) | Ollama oder llama-swap neu starten, warten bis bereit; Ollama-Katalog neu (CM:3385-3506) | `process_utils.restart_service/restart_llama_swap` | `_global_backend_state` |
| `restart_aifred` | Handler | Dienst nach 0,5 s im Thread neu starten (CM:3513-3532) | `process_utils.restart_service` | – |
| `_write_variant_cache_entry` (Modul) | Helfer | Cache-Eintrag einer Variante mit Metadaten der Basis (CM:42-61) | `model_vram_cache.add_llamacpp_calibration` | `model_vram_cache.json` |

Ablauf `_calibrate_llamacpp` (alle Schritte im State):

| Schritt | Inhalt | Beleg |
|---|---|---|
| 0 | GPU-Orc räumt STT/TTS-Worker, verwaiste Kalibrier-Server auf Port 9999 beenden, Ollama-Modelle entladen (sync `httpx.Client`) | CM:1485-1537 |
| 1 | llama-swap stoppen, warten bis kein Rechenprozess mehr auf den GPUs | CM:1544-1602 |
| 1c | VLM-Burn-in für VLMs der angehakten Zeilen ohne Cache-Wert; bei Fehler Abbruch | CM:1609-1740 |
| 2 | Basis kalibrieren (`backend.calibrate_max_context_generator`) oder bei leerer Basiszelle Werte aus YAML + Cache übernehmen; Protokoll `__RESULT__`/`__SPEED__` | CM:1773-1871 |
| 3-4 | Ergebnis, `--reasoning-format`, `-c`/`-ngl`/Speed-Variante in die YAML | CM:1873-1988 |
| 5 | TTS-Varianten je Engine: isoliert (Karten disjunkt) → schneller Weg (Projektion) → volle Kalibrierung; Fehler: Profil löschen, Fehlervermerk | CM:2001-2543 |
| 5b | VLM-Varianten (isoliert oder Projektion, plus Speed-Variante) | CM:2557-2866 |
| 5c | TTS×VLM-Kombinationen inkl. Kapazitätsprüfung der Seitenkanal-Karte | CM:2879-3178 |
| 6 | llama-swap starten, Denkfähigkeit und Reasoning-Stufen speichern | CM:3183-3213 |
| finally | llama-swap starten, falls gestoppt; `llamaswap_revision += 1`; `is_calibrating = False`; Sitzung sichern | CM:3221-3240 |

Fortschritt zur Oberfläche:

- Jede Zeile geht über `_cal_debug` sofort ins Debug-Log und in `_cal_debug_buffer` (CM:707-708).
- Der Wrapper `calibrate_context` iteriert den Generator; alle 2 s nimmt er `async with self`,
  schiebt den Puffer in `debug_messages` der startenden Sitzung und liest `calibration_cancel`
  (CM:826-839). Reflex schickt das Delta per Websocket.
- vLLM: der Thread schreibt in eine `queue.Queue`, der Generator leert sie im Sekundentakt
  (CM:995-1003).
- Zeilen der lib-Generatoren landen zusätzlich in `data/logs/calibration.log`
  (`lib/calibration/flow_log.py:10-44`).
- Andere Sitzungen sehen nichts: `is_calibrating` ist pro Sitzung, nur das Gate weist einen
  zweiten Start ab (CM:798-811).

Timer/Polling: kein eigener Timer. Der Lauf pollt selbst (2 s Drain, 3 s GPU-Leerlauf CM:1580-1587,
0,3 s VLM-Burn-in CM:1681-1683, 1 s vLLM-Thread CM:997-1001). `restart_backend` pollt 0,5 s bis
5 s (Ollama) bzw. 20 s (llama-swap) (CM:3420-3500).

### 2.2 BackendMixin

| Methode | Art | Tut | lib | Speichert |
|---|---|---|---|---|
| `on_load` | Handler (Seite `/`) | einmal je Prozess: Server-Init (Aufräum-Tasks, Vision-Prewarm, GPU-Erkennung, Ollama entladen, Message Hub); je Sitzung: Modelle aus Datei nachziehen, Einstellungen laden, Defaults, GPU-Info, `initialize_backend`, Cookie per `rx.call_script` (BM:357-770) | `settings`, `gpu_detection`, `message_hub`, `scheduler`, viele `*_cleanup_task` | `_global_backend_state` |
| `initialize_backend` | öffentlicher Handler, gibt bool zurück | schneller Weg aus `_global_backend_state` oder voller Weg: Katalog holen, gespeicherte Modelle prüfen und ggf. leeren, Fähigkeiten, Parameter, Sampling zurücksetzen, Vision erkennen (BM:776-1114) | `model_discovery.discover_models`, `CloudAPIBackend.list_models`, `model_vram_cache`, `operating_points`, `tts_llm_coordination` | `_global_backend_state`, ggf. `settings.json` |
| `switch_backend`, `switch_backend_by_label` | Handler (nicht background) | speichern, altes Backend aufräumen, Modelle des Ziels aus `backend_models` oder `BACKEND_DEFAULT_MODELS`, Init (BM:1358-1446) | `settings`, `process_utils.stop_llama_swap` | `settings.json` |
| `set_cloud_api_provider(_by_label)` | Handler | Schlüssel prüfen, Modelle holen, erstes Modell für AIfred/Automatik/Vision setzen (BM:1452-1528) | `CloudAPIBackend`, `vision_utils.is_vision_model_sync` | `settings.json` |
| `set_aifred_model` | Handler | Modell setzen, Vision-Badges neu, Parameter laden, Speed aus, Kalibrier-Info, Bildwarnung, Sampling zurücksetzen (BM:1534-1578) | `vision_utils.is_vision_model`, `model_vram_cache` | `settings.json` |
| `set_automatik_model` | Handler | Sentinel → leer, speichern, Info (BM:1580-1595) | – | `settings.json` |
| `set_vision_model` | Handler | setzen, Parameter, Speed-Variante prüfen (BM:1597-1614) | `calibration.model_has_speed_variant` | `settings.json` |
| `_load_agent_model_params`, `_load_all_agent_model_params` | intern | `rope_factor`, `max_context`, `is_hybrid`, `supports_thinking`, `has_speed_variant` je Agent aus Cache/YAML/Betriebspunkt (BM:271-342) | `model_vram_cache`, `operating_points.get_vllm_entry_context`, `calibration.model_has_speed_variant`, `agent_settings` | – |
| `_detect_vision_models`, `_build_vision_rich`, `_vision_candidate_catalog` | intern | Vision-Kandidaten und Swap-Badges; unter vLLM zusätzlich GGUF-Sicht von llama-swap (BM:1187-1316) | seit 07.11.2026 `agent_choices.vision_candidates`/`vision_options`/`vision_catalog` | `settings.json` (Auto-Wahl) |
| `_catalog_for`, `_label_for`, `_sync_agent_display_names` | intern | Label zur Modell-ID (BM:1120-1177) | – | – |
| `_cleanup_old_backend` | intern | Ollama entladen bzw. llama-swap stoppen beim Verlassen der Familie (BM:1322-1352) | `LLMClient`, `process_utils` | – |
| `_clear_models_state` | intern | Katalog leeren (BM:344-351) | – | – |
| `_ensure_backend_initialized` | intern | `on_load` nachholen, von `send_message` (BM:1620-1627; `_chat_mixin.py:776`) | – | – |

Hintergrund: keine `background=True`-Handler. Lange Vorgänge (`switch_backend`, Init mit
Katalog-HTTP) laufen als normale Handler und halten den Sitzungs-Lock.

### 2.3 SettingsMixin

| Methode | Art | Tut | Speichert |
|---|---|---|---|
| `_save_settings` | intern | baut `settings.json` aus dem Sitzungs-State komplett neu (feste Schlüsselliste), übernimmt nur einige Schlüssel aus der Datei (SM:141-233) | `settings.json` |
| `_write_settings_file` | intern | schreiben + eigene mtime merken (SM:235-242) | `settings.json` |
| `_reload_settings_from_file` | intern | Teilmenge aus der Datei in die Sitzung (Sampling, TTS, Sprache, Kalibrier-Modus …), **nicht** Backend und Modelle (SM:263-328) | – |
| `_load_channel_settings`, `_save_channel_setting` | intern | Kanal-Schalter/Stufen lesen bzw. in die Datei schreiben (SM:244-261) | `settings.json` |
| `set_ui_language`, `set_user_name`, `save_user_name`, `set_user_gender` | Handler | Profil (SM:334-374) | `settings.json` |
| `load_default_settings` | Handler | `reset_to_defaults`, Teil zurücklesen, `initialize_backend` (SM:380-432) | `settings.json` |
| `set_channel_security_tier`, `toggle_channel_monitor`, `toggle_channel_listener`, `toggle_channel_auto_reply` | Handler | Kanal-Schalter; Listener im Message Hub an/ab (SM:438-524) | `settings.json` |
| `open_channel_credentials`, `_open_channel_credentials` | Handler/intern | Felder (i18n aus Plugin), Werte aus `.env`/Plugin-`settings.json`, OAuth-Status, Redirect `/credentials` (SM:536-658) | – |
| `close_channel_credentials`, `update_channel_credential`, `toggle_channel_cred_show_password` | Handler | Formular (SM:660-682) | – |
| `save_channel_credentials` | Handler | Geheimnisse → `.env` + `os.environ`, Rest → Plugin-`settings.json`, Kanal anwenden und Listener starten (SM:823-940) | `.env`, Plugin-`settings.json`, `settings.json` |
| `verify_oauth_connection`, `start_oauth_connection`, `disconnect_oauth` | Handler | OAuth prüfen, 5 min pollen, trennen (SM:724-821) | OAuth-Token-Datei |
| `toggle_tool_plugin` | Handler | Plugin an/aus (Marker `.disabled`), Vision zuvor entschärfen (SM:946-1002) | Marker im Plugin-Ordner |
| `get_text` | Handler | Übersetzung (SM:1008-1011, tot) | – |

Polling: `refresh_debug_console` in `aifred/state/_base.py:214-263` (500-ms-Tick der Seite)
vergleicht die mtime von `settings.json` und ruft `_reload_settings_from_file`; das ist auch der
einzige Weg, auf dem `calibration_mode` und `calibration_allow_hybrid` beim Sitzungsstart in den
State kommen (`_last_settings_mtime` startet bei 0). `start_oauth_connection` pollt 60×5 s im
Handler (SM:784-795).

### 2.4 Speicherorte

| Datei | Schreibt | Liest |
|---|---|---|
| `data/settings.json` | `_save_settings` (ganze Datei), Einzel-Handler (Hybrid, Modus, Kanäle), `PATCH /api/settings`, `reset_to_defaults` | Sitzungen (Init, 500-ms-Tick), Hub/Scheduler (`persisted_settings`, `config.get_effective_model_from_settings`), Kalibrier-lib (`reporting.py:184-194`, `flow.py:216, 1307`) |
| llama-swap-Config (`~/.config/llama-swap/config.yaml`) | Kalibrierung (Mixin und `flow.py:549-556`), `operating_points.apply_operating_point`, vLLM-Flow | Katalog, Variantenwahl, Anzeige |
| `data/model_vram_cache.json` | Kalibrierung, Thinking-Test, RoPE-Setter, Fehlervermerke | Matrix, Modell-Parameter |
| `data/vlm_vram_cache.json`, `data/sidechannel_vram_cache.json` | VLM-Burn-in, Side-Channel-Burn-in | Matrix, Reserven |
| `data/operating_points/<eintrag>.yaml` | vLLM-Kalibrierung | `is_vllm_calibrated`, `apply_operating_point` |
| `data/vllm_runtime.yaml` | niemand aus diesem Bereich | vLLM-Sonde (`vllm_probe.py:43, 186`) |
| `data/logs/calibration.log`, `data/logs/vllm_calibration/<eintrag>/` | lib-Kalibrierung | Diagnose |
| `data/agents.json` | Agenten-Editor | `calibration_ai_label`, `ai_agent.py` |
| `data/sessions/<id>.json` | `_persist_calibration_progress` (Konsole) | Sitzungs-Wiederherstellung |
| `.env`, Plugin-`settings.json` | `save_channel_credentials` | Plugins |

---

## 3. Modellwahl und Backend-Wechsel

### 3.1 Wo der Zustand lebt

| Ebene | Inhalt | Gilt für |
|---|---|---|
| Sitzungs-State (Reflex, pro Tab) | `backend_type`, Katalog, `agent_tuning[*].model_id/model`, `automatik_model_id`, Vision-Listen, Kalibrier-Häkchen | diesen Tab |
| `_global_backend_state` (`state/_base.py:70-77`) | **ein** Backend mit Katalog, Vision-Cache, `gpu_info`, `_init_complete` | ganzer Prozess, obwohl Tabs verschiedene Backends haben können |
| `settings.json` | `backend_type`, `backend_models[<backend>][<agent>|automatik]`, `agent_tuning` (u. a. `speed_mode`), `cloud_api_provider`, `calibration_*` | Hub, Scheduler, Status-Leiste, API — die eigentliche Wahrheit |
| `model_vram_cache.json` / llama-swap-YAML / Betriebspunkte | Kontext, Speed-Variante, Denkfähigkeit, RoPE je Modell | alle |

Folge: Die Wahl ist global gemeint (der Hub nimmt `settings.json`), wird aber pro Tab gehalten und
von jedem Tab mit seiner ganzen Sicht zurückgeschrieben. `_save_settings` schreibt
`backend_type` und `backend_models[self.backend_id]` aus dem State (SM:154-169), und
`_reload_settings_from_file` holt Backend und Modelle nicht zurück (SM:288-289). Nur `on_load`
zieht die Modell-IDs nach (BM:513-532). Ein zweiter offener Tab oder ein `PATCH /api/settings`
wird deshalb beim nächsten Speichern eines alten Tabs (irgendein Schalter) überschrieben.

### 3.2 Modellwahl (`set_aifred_model`, BM:1534-1578)

1. Select liefert die Modell-ID; `model` (Label) wird aus dem Katalog nachgezogen.
2. Vision-Badges neu, weil sie vom Chat-Modell abhängen (BM:1547-1554).
3. `_load_agent_model_params`: Parameter aus Cache/YAML/Betriebspunkt; `max_context` mit der
   aufgelösten Varianten-ID (BM:321-331).
4. Ohne Speed-Variante `speed_mode = False`; Kalibrier-Info in die Konsole.
5. Sampling der erbenden Agenten zurücksetzen (ACM:937-955), `_save_settings`.
6. Kein Laden: llama-swap tauscht beim nächsten Request (BM:1576-1577). Welche Variante geladen
   wird (`-speed`, `-tts-<engine>`, `-vlm-<key>`), entscheidet `_effective_model_id`
   (ACM:402-447) über `calibration.resolve_effective_suffix`.

### 3.3 Backend-Wechsel (`switch_backend`, BM:1366-1446)

1. `backend_switching` sperrt die Oberfläche; `_save_settings` sichert das alte Backend.
2. `_cleanup_old_backend`: Ollama entladen; llama-swap nur stoppen, wenn das Ziel nicht zur
   Familie `LLAMASWAP_BACKENDS` gehört (BM:1322-1352).
3. Modelle des Ziels aus `backend_models[ziel]`, sonst `config.BACKEND_DEFAULT_MODELS`
   (BM:1389-1428).
4. `_global_backend_state["_init_complete"] = False`, `initialize_backend` voller Weg: Katalog
   (`discover_models`; llama-swap ggf. starten), unbekannte Modelle leeren (nur wenn der Katalog
   nicht leer ist), globalen Zustand schreiben, Parameter, Sampling, Vision (BM:875-1106).
5. `_save_settings`.

### 3.4 Sitzungsstart (`on_load`, BM:357-770)

Einmal je Prozess (unter `_backend_init_lock`): Aufräum-Tasks, Vision-Prewarm, GPU-Erkennung
(`gpu_detection.detect_gpu`), Ollama entladen, Message Hub + Scheduler. Je Sitzung: Einstellungen
aus Datei in den State, GPU-Info aus `_global_backend_state`, `initialize_backend` (schneller Weg,
wenn ein anderer Tab das Backend schon geladen hat).

### 3.5 Schon browserunabhängig / nur im State

| Schon in lib (ohne Browser nutzbar) | Nur im State |
|---|---|
| Katalog: `model_discovery.discover_models`, `discover_llamaswap_models`, `profile_label` | Validierung und Leeren veralteter Modelle, Defaults je Backend (BM:810-862, 968-1007) |
| Variante ohne State: `config.get_effective_model_from_settings` (config.py:1189-1235), `calibration.resolve_effective_suffix` | Parameter je Agent laden (BM:271-342), Vision-Kandidaten und Badges (BM:1187-1316) |
| Modell laden/entladen: `service_control.main_model_to_load`, `load_llamaswap_model`, `unload_llamaswap_model` | Backend-Wechsel inkl. Aufräumen (BM:1322-1446), Cloud-Modelle holen (BM:926-958, 1460-1528) |
| Caches: `model_vram_cache`, `vlm_vram_cache`, `sidechannel_vram_cache`, `operating_points` | Zusammenbau von `settings.json` (SM:141-233) |
| llama.cpp-Bausteine: `calibrate_llamacpp_model`, `calibrate_tts_variant_from_base`, YAML-Schreiber (`lib/calibration/__init__.py`) | **die gesamte llama.cpp-Varianten-Orchestrierung** (CM:1453-3240, rund 1.800 Zeilen), Ollama-RoPE-Ablauf (CM:1096-1236) |
| vLLM komplett: `vllm_flow.calibrate_vllm_checkpoint` mit `progress`- und `cancel_check`-Rückruf | vLLM-Vor- und Nacharbeit: llama-swap stoppen/starten, Burn-in (CM:921-963, 1049-1063) |
| Gate: `calibration_gate` (prozessweit, auch vom Hub geprüft, `message_processor.py:296-300`) | Start-Guards, Cancel-Polling, Fortschrittsweg (CM:746-856) |
| GPU-Orc: `gpu_orc_client.get_gpu_orc_client().free_all_but_models` | Matrix-Zeilen bauen (CM:299-560), Kalibrier-Info als Text (CM:3246-3379) |
| Dienste: `process_utils.start/stop/restart_llama_swap`, `restart_service` | Backend-Neustart mit Bereitschafts-Polling (CM:3385-3506) |
| Einstellungen: `settings.load/save/persisted_settings/reset_to_defaults` | Server-Init-Tasks, die nur `on_load` startet (BM:398-484) |
| `agent_settings.*` — aber mit `state: Any` als Parameter (duck-typed, `agent_settings.py:72-127`) | |

---

## 4. Einteilung

| Funktionalität | UI-Zustand (Vue) | Domäne (Server-Dienst in lib) | Speicherung |
|---|---|---|---|
| Backend wählen | Dropdown, Spinner | `BackendService.switch(backend)`: aufräumen, Katalog, Modelle prüfen | `settings.json` (`backend_type`, `backend_models`) |
| Cloud-Anbieter | Dropdown, Schlüssel-Badge | Schlüssel prüfen, `list_models` | `settings.json` (`cloud_api_provider`); Schlüssel `.env` |
| GPU-Info | Akkordeon | Erkennung beim Serverstart | Prozess |
| Modellkatalog | Optionen der Selects | Katalog + Labels + Sortierung, Vision-Kandidaten + Badges, zwischengespeichert | Prozess |
| Modell je Agent / Automatik / Vision | Auswahl | prüfen gegen Katalog, Parameter, Sampling-Reset, Variante | `settings.json` |
| Modell-Parameter (Kontext, Thinking, Speed-Variante, RoPE) | Anzeige, Schalter sichtbar/aus | aus Caches/YAML/Betriebspunkt berechnen | Caches |
| Kalibrier-Matrix: Häkchen | **nur Vue** | – | – (pro Lauf übergeben) |
| Kalibrier-Matrix: Status (grün/rot/–) | Anzeige | aus Caches berechnen | Caches |
| Kalibrierlauf, Abbruch | Knopf, Spinner, Log-Ansicht | `CalibrationService` (ein Lauf je Prozess, Gate, Cancel, Fortschritt als Ereignisse) | llama-swap-Config, Caches, Betriebspunkte, Logs |
| Kalibrier-Optionen Modus/Hybrid | Schalter | lib liest sie bereits aus der Datei | `settings.json` |
| vLLM Nachmessen/Trockenlauf | **nur Vue**, Parameter des Starts | `calibrate_vllm_checkpoint(resweep, dry_run)` | – |
| VLM-Cache zurücksetzen | Knopf | `vlm_vram_cache.clear` | Cache |
| Kalibrier-Info je Modell | Anzeige | strukturierte Abfrage statt Konsolentext | – |
| Backend-/AIfred-Neustart | Knopf, Fortschritt | Dienststeuerung + Bereitschaft | – |
| Grundeinstellungen | Knopf | `reset_to_defaults` + Ereignis „Einstellungen geändert“ | `settings.json` |
| Sprache, Name, Anrede | Formular | Prompts lesen `persisted_settings` | `settings.json` |
| Kanal-Schalter, Stufen | Schalter | Message Hub an/ab | `settings.json` |
| Zugangsdaten-Formular | Werte, Passwort-Auge, Seite offen | Feldbeschreibung (i18n), Speichern, Kanal anwenden | `.env`, Plugin-`settings.json` |
| OAuth | Status, Link | `oauth_broker` (verbinden, prüfen, trennen) | Token-Datei |
| Tool-Plugins an/aus | Schalter | Marker setzen, Vision entschärfen | `.disabled` |
| Mobil/Desktop-Select | **nur Vue** (CSS) | – | – |
| YaRN | entfällt | entfällt | Schlüssel entfernen |

---

## 5. API-Vorschlag

### 5.1 Vorhandene Endpunkte, die passen

| Endpunkt | Passt für | Mangel heute |
|---|---|---|
| `GET /api/settings`, `PATCH /api/settings` (`lib/api/core.py:133-250`) | Profil, Modelle, Temperatur | kennt weder `agent_tuning` außer Temperatur/RoPE noch `calibration_*`; veraltete Felder `enable_thinking`, `multi_agent_mode` (core.py:46-52, 82-88); `backend_url` aus der Datei, wo sie nie steht (core.py:152) |
| `GET /api/models` (core.py:253-308) | Katalog | liest `_global_backend_state`, den nur ein Browser füllt; sonst nur Ollama-Rückgriff |
| `GET /api/health` (core.py:111-127) | Status | `backend_healthy` hängt am Browser-Init |
| `POST /api/system/restart-ollama`, `/restart-aifred`, `/reset-defaults` (`lib/api/system.py:15-121`) | Neustarts, Grundeinstellungen | kein llama-swap-Neustart; doppelt zum State (7.3) |
| `GET /api/service-status`, `POST /service-status/load|unload|hold` (`lib/api/service_status.py`) | Modell laden/entladen | passt |
| `GET /api/oauth/{provider}/status`, `/auth-url`, `DELETE /api/oauth/{provider}` (`lib/api/agents.py:163-196`) | OAuth im Formular | passt |
| `GET /api/browser/stream/{session_id}` + `lib/session_events.py` (Arbeitsstand) | Ereignisstrom je Sitzung | Ereignisse dieses Bereichs fehlen |

### 5.2 Neue REST-Endpunkte

| Methode, Pfad | Nutzlast (grob) | Antwort |
|---|---|---|
| `GET /api/backends` | – | `{current, available:[{id,label}], cloud:{provider, providers:[{id,label,key_configured}]}, gpu:{…}}` (Labels aus `config.BACKEND_LABELS`, `CLOUD_API_PROVIDERS`) |
| `PUT /api/backend` | `{backend_type}` | 202; Ereignisse `backend.switching` → `backend.changed` |
| `PUT /api/backend/cloud-provider` | `{provider}` | Katalog |
| `GET /api/models` (neu gefasst) | `?backend=` | `{models:[{id,label,size_gb,badges}], vision:[{id,label,badge,color}]}` aus einem Server-Katalog |
| `PUT /api/agents/{agent}/model` | `{model_id}` (leer = wie AIfred) | Modell-Parameter |
| `PUT /api/automatik/model`, `PUT /api/agents/vision/model` | `{model_id}` | dito |
| `GET /api/models/{model_id}/info` | `?backend=` | `{context, yaml_context, cache_context, tensor_split, speed:{split,context}, supports_thinking, reasoning_levels, has_speed_variant}` (ersetzt CM:3246-3379) |
| `POST /api/backend/restart` | – | 202; Ereignis `backend.restart` |
| `GET /api/calibration` | `?model_id=` | `{active, model_id, backend, started_at, options:{mode, allow_hybrid}, vllm:{calibrated}}` |
| `GET /api/calibration/matrix` | `?model_id=` | `{columns:[…], rows:[{label, cells:[{key, status: done|failed|none|na}]}]}` (gleich für beide Backends, Bedeutung je Backend) |
| `POST /api/calibration` | `{model_id, cells:[key…], vllm:{resweep, dry_run}}` | 202 oder 409 (läuft schon / keine Zelle) |
| `POST /api/calibration/cancel` | – | 202 |
| `PATCH /api/calibration/options` | `{mode?, allow_hybrid?}` | Optionen |
| `DELETE /api/calibration/vlm-cache` | – | `{cleared}` |
| `GET /api/calibration/log` | `?after=` | Zeilen seit Id (Nachladen beim Öffnen) |
| `PATCH /api/profile` | `{ui_language?, user_name?, user_gender?}` | Profil |
| `PUT /api/channels/{name}` | `{monitor?, listener?, auto_reply?, security_tier?}` | Kanalzustand |
| `GET /api/plugins/{name}/credentials` | – | Felder (übersetzt), Werte ohne Passwörter, OAuth-Status |
| `PUT /api/plugins/{name}/credentials` | `{values:{env_key: wert}}` | `{saved}` |
| `PUT /api/plugins/tools/{name}/enabled` | `{enabled}` | Plugin-Zustand |

### 5.3 Ereignisse (SSE)

Kalibrierung, Backend, Katalog und Einstellungen sind Tatsachen des Prozesses, nicht einer
Sitzung. Sie gehen wie `service_status` an alle Sitzungen mit Zuhörern
(`session_events.sessions_with_subscribers()`, `lib/api/service_status.py:40-44`), Zustände mit
„nur das Neueste zählt“ über `replaces`.

| Art | Daten | Wann |
|---|---|---|
| `calibration.state` | `{active, model_id, backend, phase}` (ersetzt Vorgänger) | Start, Phasenwechsel, Ende |
| `calibration.log` | `{id, ts, line, kind: probe|projection|info}` (gebündelt) | laufend |
| `calibration.finished` | `{model_id, outcome: ok|failed|cancelled, warnings}` | Ende |
| `calibration.matrix_changed` | `{model_id}` | Cache/YAML geschrieben, Fehlervermerk gelöscht |
| `backend.changed` | `{backend_type, cloud_provider}` (ersetzt Vorgänger) | nach Wechsel |
| `backend.restart` | `{phase: restarting|waiting|ready|timeout, seconds}` | Neustart |
| `models.changed` | `{backend_type}` | Katalog neu (Neustart, Autoscan, Kalibrierung) |
| `settings.changed` | `{keys:[…]}` | jede Änderung an `settings.json` (ersetzt den mtime-Tick) |
| `oauth.status` | `{provider, status, rights_missing}` | Verbinden fertig/abgelaufen (ersetzt 5-min-Polling im Handler) |
| `debug` | `{line}` | Konsolenzeilen dieser Sitzung (Modellwahl-Infos usw.) |

---

## 6. Abhängigkeiten zu anderen Mixins

| Mixin | nutzt | aus | Beleg |
|---|---|---|---|
| Calibration | `agent_tuning` | AgentConfig | CM:177 |
| Calibration | `available_models(_dict)`, `backend_id/type/url`, `backend_switching` | Backend | CM:178-182 |
| Calibration | `debug_messages`, `add_debug` | Chat | CM:184, 188-189; `_chat_mixin.py:129` |
| Calibration | `ui_language` | Settings | CM:185 |
| Calibration | `_save_current_session`, `session_id` | Session | CM:187, 1641 |
| Backend | `agent_tuning`, `multi_agent_mode`, `max_debate_rounds`, `consensus_type`, `_effective_model_id`, `_reset_sampling_for_all_agents` | AgentConfig | BM:50-80 |
| Backend | `enable_tts`, `tts_*`, `narrator_voices` | TTSConfig | BM:54-67 |
| Backend | `temperature_mode`, `enter_sends_message`, `show_transcription` | UIConfig | BM:55, 62-63 |
| Backend | `image_upload_warning`, `pending_images` | Image | BM:56, 60 |
| Backend | `session_id`, `_session_initialized`, `refresh_session_list` | Session | BM:49, 61, 85-86 |
| Backend | `_refresh_vision_settings` | VisionSettings | BM:74 |
| Backend | `_load_channel_settings`, `_save_settings`, `ui_language`, `user_*` | Settings | BM:68-81 |
| Backend | `_show_model_calibration_info` | Calibration | BM:82 |
| Backend | `handle_username_loaded` | Auth | BM:763-767 |
| Settings | `_backend_initialized`, `initialize_backend`, Backend-Felder | Backend | SM:56, 63-66, 93-94 |
| Settings | `calibration_allow_hybrid`, `calibration_mode` | Calibration | SM:67-68 |
| Settings | `active_agent`, `symposion_agents`, `multi_agent_mode`, `automatik_rope_factor` … | AgentConfig | SM:57-79 |
| Settings | `tts_list_revision` u. a. | TTSConfig | SM:81-85 |
| Settings | `_refresh_editor_tools` | AgentEditor | SM:88 |
| Settings | `force_disarm_vigilantia` | VisionSettings | SM:91-92 |
| UI dieses Bereichs | `ctx_rows`, `sampling_rows`, `agent_model_rows`, Setter `*_for` | AgentConfig | ACM:925-1042 |
| UI dieses Bereichs | `set_agent_num_ctx_manual`, `calculate_manual_context`, `set_temperature_mode` | UIConfig | `_ui_config_mixin.py:66-217` |
| UI dieses Bereichs | `open_reasoning_thinking_help`, `open_model_lifecycle_help`, `is_mobile` | Image | `_image_mixin.py:63, 264-278` |
| UI dieses Bereichs | `open_tts_settings`, `set_enable_tts`, `toggle_tts_autoplay` | AgentEditor, TTSConfig | `_agent_editor_mixin.py:285`, `_tts_config_mixin.py:142-148` |

Umgekehrt (lib hängt am State): `lib/research_tools.py:112` liest `state._effective_automatik_id`;
`vision_utils.is_vision_model(state, …)` (BM:1271, `vision_utils.py:363`);
`plugins/tools/subagent/__init__.py:203` liest `state.backend_url`; `agent_settings.*` nehmen
`state`. Für den Server-Dienst braucht es ein Sitzungs-/Einstellungsobjekt mit denselben
Feldern statt des Reflex-States.

---

## 7. Auffälligkeiten

### 7.1 Workarounds wegen Reflex (entfallen mit Vue)

- Kalibrierung als `background=True` mit Modul-Puffer und 2-s-Drain unter Lock, weil ein
  Hintergrund-Task den State nur unter `async with self` ändern darf und Sitzungsdatei-Schreiben
  einen Sync-Sturm auslöste (CM:27-34, 692-708, 826-839).
- `_persist_calibration_progress`: Konsole in die Sitzungsdatei, weil Reflex gedrosselte Tabs
  verwirft und `debug_messages` verloren gingen (CM:591-614).
- Revisionszähler `_agents_json_revision`, `llamaswap_revision` und `cache=True,
  auto_deps=False` mit festen `deps`, weil Reflex Datei-Abhängigkeiten nicht sieht (CM:203-207,
  243-247, 299-300, 434, 454).
- TypedDicts `CalibrationCell/Row` nur für `rx.foreach` (CM:64-94); Payload `[key, checked]`
  (CM:617-631).
- Absolute Imports in `rx.var` wegen Reflex' Abhängigkeits-Introspektion (CM:288-291).
- Sentinel `__same_as_aifred__`, weil Radix keinen leeren Options-Wert erlaubt (BM:36-39,
  1585-1586).
- Doppelte Felder `model` (Label) neben `model_id` mit Nachzieh-Logik `_sync_agent_display_names`
  (BM:1120-1177).
- `rx.call_script` für Cookie und TTS-Rate (BM:650-652, 758-767); `rx.redirect` und
  `_credentials_return_to` für das Formular (SM:530-534, 652-668); OAuth-URL vorab erzeugen
  gegen den Pop-up-Blocker (SM:642-647, 694-722); `set_user_gender` nimmt auch Listen
  (SM:369-371); Mobil/Desktop-Doppel-Selects (UI/backend_section.py:19-45, 89-121).
- `settings.json`-mtime-Polling im 500-ms-Tick als Ersatz für Push (`state/_base.py:245-263`).

### 7.2 Toter Code

- YaRN komplett: `_yarn_section` wird nicht gerendert (UI/backend_section.py:383-489,
  UI/main.py:60-62), Handler `toggle_yarn`, `set_yarn_factor_input`, `apply_yarn_factor`
  (`state/_base.py:892-945`), sieben Felder (BM:151-158), Schlüssel `enable_yarn`/`yarn_factor`
  (SM:193, 412-413; BM:610-612; `settings.py:111-112`).
- `vllm_sidechannel_summary` (CM:356-410) liest niemand.
- `not_applicable` ist in beiden Matrix-Bauern immer `False` (CM:347, 517, 526, 545, 556); der
  Strich-Zweig in UI/calibration_picker.py:82-89 kommt nie zum Zug.
- `backend_label` (BM:209-212), `backend_supports_dynamic_models` (immer `True`, BM:200-207, die
  `rx.cond` in UI/agent_model_rows.py:147, 179, 192, 223, 236 sind wirkungslos),
  `available_vision_models_list`, `backend_info`, `model_count`, `backend_healthy` (State),
  `gpu_warnings`, `_model_preloaded`, `_automatik_model_context_limit`; `available_models`
  (Label-Liste) nur für Zählungen (BM:109, 868, 1009, 1033).
- `get_text` als Handler (SM:1008-1011), nirgends benutzt.
- `config.BACKEND_LABELS` (config.py:301-306) unbenutzt, während der State die Labels viermal
  selbst hat (7.3).
- `except (subprocess.CalledProcessError, subprocess.TimeoutExpired)` um `stop_llama_swap` /
  `start_llama_swap` (CM:1551, BM:913): `_systemctl_llama_swap` wirft beides nie
  (`process_utils.py:154-166`).
- `load_default_settings` liest `model`/`automatik_model` (SM:417-418), die `get_default_settings`
  nicht mehr liefert (`settings.py:100-102`).
- `if True:` als Blockrest (CM:2001); Kommentar zu `rx.call_script` in `restart_aifred`, das keins
  zurückgibt (CM:3520-3521); Kommentare `ai-<qwen-model>`, obwohl nur `ai` gilt (config.py:120-122,
  CM:209-211, SM:284 gegen CM:277).
- `session_scope` um den VLM-Prewarm (CM:1640-1641, 1669): laut Kommentar meldet
  `stress_prewarm_vlm` nicht über den Bus — wirkungslos oder, wenn doch, genau der Sync-Sturm,
  den CM:27-33 vermeidet.

### 7.3 Doppelte Logik

- GGUF-Parser doppelt: `gpu_utils.read_gguf_field` (eigener `struct`-Parser, gpu_utils.py:254-337,
  nur für Experten-Zahlen gpu_utils.py:381-383) neben `gguf_utils._scan_gguf_numeric_field`
  (gguf-py, gguf_utils.py:213-275).
- Basis- und Speed-Profil werden zweimal geschrieben: `flow.calibrate_llamacpp_model` schreibt
  `-c`, `-ngl`, Split, `CUDA_VISIBLE_DEVICES`, Cache und Speed-Variante selbst (flow.py:549-556,
  persist.py:15-106), danach der Mixin noch einmal `-c`, `-ngl`, Speed-Variante und
  `update_llamacpp_speed_split` (CM:1910-1987).
- Drei Auflöser der wirksamen Modell-ID: State `_effective_model_id` (ACM:402-447, Speed-Variante
  aus dem Bucket-Feld), Automatik `_effective_automatik_id` (BM:230-265, `has_speed_variant=True`),
  ohne State `config.get_effective_model_from_settings` (config.py:1189-1235, Speed-Variante aus
  der YAML). Gemeinsam ist nur `resolve_effective_suffix`.
- Ollama-Katalog mit Label dreimal: `model_discovery.py:54-57`, CM:3431-3436, `api/core.py:288-296`.
- Neustarts und Grundeinstellungen doppelt: `restart_backend` (Ollama-Teil) ↔
  `/system/restart-ollama`, `restart_aifred` ↔ `/system/restart-aifred`, `load_default_settings`
  ↔ `/system/reset-defaults` (system.py:15-121).
- Backend-Labels viermal (BM:100-105, 147-149, 192-196; UI/backend_section.py:103-114) statt
  `config.BACKEND_LABELS`; Cloud-Labels fest im UI (UI/backend_section.py:365) statt
  `CLOUD_API_PROVIDERS` (config.py:210-238).
- Cloud-Modelle holen zweimal leicht verschieden (BM:926-958 sortiert, BM:1477-1515 unsortiert).
- RoPE zwei Wahrheiten: `model_vram_cache` per Setter (ACM:581-599) und
  `settings.json` `agent_tuning.*.rope_factor`/`automatik_rope_factor` per API
  (`api/core.py:209-216`), die `_reload_settings_from_file` liest (SM:293-305), `_save_settings`
  aber nicht schreibt.
- Standard-Backend als Rückfall uneinheitlich: `"ollama"` (config.py:119, 1051; api/core.py:124,
  148) gegen `"llamacpp"` (config.py:1200; api/core.py:222; `message_processor.py:236`).

### 7.4 Fallen und Fehler

- **Server ohne Browser unvollständig:** Aufräum-Tasks (Audio, Lookup-Cache, Vision, Audit-Log,
  vLLM-Compile-Cache, Audio-Index), Vision-Prewarm, GPU-Erkennung und Ollama-Entladen startet nur
  `on_load` (BM:398-484); der Message Hub startet zusätzlich im Lifespan (`aifred/aifred.py:1309-1327`),
  also doppelt. `/api/models` und `/api/health` sind ohne geöffneten Tab leer bzw. „ungesund“.
- **Letzter Schreiber gewinnt:** `_save_settings` schreibt die ganze Datei aus dem Tab-State
  (3.1). Ein alter Tab macht Modellwechsel anderer Tabs und `PATCH /api/settings` rückgängig.
- **`calibration_allow_hybrid` geht verloren:** `_save_settings` schreibt ihn nicht (SM:171-218;
  `calibration_mode` dagegen ausdrücklich, SM:174-177). Jede spätere Speicherung (Modellwahl,
  Schalter) löscht den Schlüssel; die lib liest dann `False` (`reporting.py:184-194`). Dasselbe
  Muster trifft jeden Schlüssel außerhalb der Liste (`voice`, `automatik_rope_factor`, `enable_gpu`).
- **vLLM-Burn-in-Matrix stimmt nicht:** Der Lauf ignoriert, welche Zellen angehakt sind
  (Burn-in mit der laufenden Engine und dem aktiven VLM, CM:944-963; die Zellen zählen nur als
  „irgendeine außer Basis“, CM:978-981). Gespeichert wird unter der Modell-ID des VLM
  (`sidechannel_burnin.py:133, 158`, z. B. `qwen3-vl:4b-instruct-q8_0` laut
  `vision_prewarm.py:61`), gelesen unter dem Kurz-Key (`qwen3vl4b`, CM:336 über
  `vlm_calibration_choices`): die VLM-Zeilen werden so nie grün oder rot.
- **Cloud-Anbieterwechsel bricht das Vision-Dropdown:** Zeilen mit `name` statt `id`/`label`
  (BM:1498-1500; gelesen wird `row["id"]`, `row["label"]` in `ui/helpers.py:308-345`); außerdem
  wird der Katalog nicht sortiert, nicht in `_global_backend_state` geschrieben und die Automatik
  auf das erste Modell gesetzt statt „wie AIfred“ (BM:1489-1508).
- **Blockierende Aufrufe im Event-Loop:** sync `httpx.Client` im Kalibrierlauf (CM:1500-1537);
  `stop_llama_swap`/`start_llama_swap` ohne Thread (CM:931, 1051, 1548, 1738, 3186, 3225; BM:905,
  1349), ein Start dauert mit Autoscan über eine Minute (`process_utils.py:157-159`); sync
  `discover_models` (10 s Timeout) im Init (BM:896-910, 962) und in `_vision_candidate_catalog`
  bei jedem `_label_for("vision")` (BM:1195-1200).
- **Rückgabewert ignoriert:** `stop_llama_swap()` liefert bool, der Lauf setzt trotzdem
  `llama_swap_stopped = True` (CM:1548-1549). Nach gescheitertem Burn-in startet llama-swap zweimal
  (CM:1737-1740 und finally CM:3223-3225).
- **Modul-Global vor dem Guard:** `_vllm_cal_target` wird zurückgesetzt bzw. gesetzt, bevor
  geprüft ist, ob schon ein Lauf läuft (CM:777-792 vor CM:794-811).
- **Häkchen mit Nebenwirkung:** Anhaken einer roten Zelle löscht den Fehlervermerk sofort, auch
  ohne Lauf (CM:636-669).
- **Lange Handler halten den Sitzungs-Lock:** `restart_backend` (CM:3385-3506),
  `switch_backend` (BM:1366-1446) und `start_oauth_connection` mit bis zu 5 min Polling
  (SM:761-800).
- **`initialize_backend` ist ein öffentlicher Handler mit Rückgabewert**, nur aus State-Code
  gerufen (BM:741, 776-777, 1434; SM:424) — gegen die Regel „interne Methoden privat“; jeder Client
  kann eine volle Backend-Initialisierung auslösen.
- **Grundeinstellungen wirken nicht auf die Modelle:** `load_default_settings` ruft
  `initialize_backend`, das bei gleichem Backend den schnellen Weg nimmt und die Modelle der
  Sitzung behält (BM:788-873); die nächste Speicherung schreibt sie zurück.
- **Feste Agenten:** Thinking-Ergebnis nur für AIfred/Sokrates/Salomo (CM:1260-1266);
  Backend-Wechsel, Init und Defaults nennen Sokrates/Salomo einzeln (BM:681-694, 1396-1428,
  1039-1047). Betrifft die Rollen-Umstellung aus dem Plan.
- **`restart_backend` lädt bei llama-swap den Katalog nicht neu** (nur Bereitschaft, CM:3463-3500),
  obwohl der Autoscan neue Modelle bringt; Docstring sagt „reload model list“ (CM:3387).
- **Hart kodierte Texte und Werte:** „Grundeinstellungen laden“, „… Neustart“
  (UI/audio_system_section.py:62, 105); Rückfallmodell `qwen-plus` (CM:685).
- **Verdeckter Rückfall:** fehlt `gguf-py`, liefern die GGUF-Leser still `0` oder `None`
  (`gguf_utils.py:118, 196, 270, 338`).
- **Kalibrierung kennt nur AIfreds Modell** (`agent_tuning["aifred"].model_id`, CM:751, 1762);
  wer ein anderes Modell kalibrieren will, muss das Hauptmodell umstellen.
- llama.cpp-Kalibrierung holt `wait_for_vram_stable` aus dem Ollama-Backend (CM:1567, 1717) —
  GPU-Helfer am falschen Ort.
