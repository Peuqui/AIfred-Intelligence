# Plugin-Review 2026-08-11 — Vollständige Befundliste

Review aller 17 aktiven Plugins (~12.500 Zeilen) durch 12 parallele Tiefen-Reviews,
Top-Befunde manuell verifiziert. `ruff` + `mypy` über alle 44 Plugin-Dateien: grün.
Vision-Plugin (`disabled/`) bewusst ausgelassen.

**Architektur-Entscheidung (Peuqui, 2026-08-11, präzisiert):** Plugins sind
eigenständige, atomare, modulare Gebilde.
1. **Plugin → Plugin: VERBOTEN** (Import/Code-Abhängigkeit) — beim Deaktivieren
   eines Plugins darf keine Kette brechen.
2. **Plugin → aifred/lib: ERLAUBT** — lib ist immer da. Gemeinsame Logik zweier
   Plugins wird als lib-Helper konsolidiert (Abschnitt E), nie per
   Plugin-Import geteilt.

**Abarbeitung:** von oben nach unten. `[ ]` offen, `[x]` erledigt.
⚠️ = braucht User-Entscheidung vor dem Fix.

---

## A — HOCH (Funktions-/Daten-/Sicherheitsverlust)

### A1 — email: Retry/Quarantäne ausgehebelt, stiller Mail-Verlust
- [x] `channels/email_channel/__init__.py:232-240` — Checkpoint wird VOR der
  Verarbeitung auf `max_uid` geschrieben. Wirft `_process_uid`, ist die UID beim
  Reconnect schon „verarbeitet": `EMAIL_MAX_PROCESS_ATTEMPTS=5` wird nie erreicht,
  `_quarantine_uid` (`\Flagged`) ist praktisch unerreichbar, Mail geht ohne Marker
  verloren. Scheitert Mail 1 von N in der Recovery, sind auch die übrigen N−1 weg
  (Checkpoint steht schon auf `max_uid`). Widerspricht der eigenen Docstring-Zusage.
  Fix-Richtung: Checkpoint erst nach erfolgreicher Verarbeitung je UID vorrücken
  (bzw. pro-UID-Advance), Attempt-Zählung erhalten.

### A2 — discord: `/clear` ohne Sender-Allowlist
- [x] `channels/discord_channel/__init__.py:173-201` — `slash_clear` prüft
  `_is_discord_user_allowed` nirgends; `routing_table.delete_route` läuft sogar VOR
  jeder Permission-Prüfung. Jedes Server-Mitglied/jeder DM-Absender kann den
  Konversations-Kontext resetten. Telegram-Pendant prüft korrekt
  (`telegram_channel/__init__.py:173`).
- [x] ebd. Z.200 — `purge()` ohne Argument löscht max. 100 Nachrichten
  (discord.py-Default `limit=100`), Kommentar verspricht „delete everything".
  Fix: `purge(limit=None)`.

### A3 — system_monitor: `get_ui_status` kapert fremde Plugins
- [x] `tools/system_monitor/__init__.py:177-178` — gibt bedingungslos
  `"📊 System Status"` zurück statt `tool_name == "system_status"` zu prüfen.
  Dispatcher (`lib/multi_agent.py:222-230`) nimmt den ersten nicht-leeren Treffer →
  translator-/workspace-Tools zeigen im UI „System Status".

### A4 — audio_player: `audio_index_rebuild` funktionslos für lokale Quellen
- [x] `tools/audio_player/__init__.py:1013-1017` — liest nur
  `_load_settings().get("sources")`; lokale Ordner werden aber aus
  `MEDIA_AUDIO_DIR` auto-discovered und stehen NICHT in settings.json → Tool liefert
  immer `{"results": [], "total_sources": 0}`. Der eigene Prompt (Z.927) verweist
  bei leerem Index genau auf dieses Tool (Sackgasse). Korrekte Logik existiert in
  `state/_audio_player_mixin.py:326` (`build_source_map(MEDIA_AUDIO_DIR, {})`).
- [x] `lib/audio_index.py:591` — `sync_audio_index_task` hat denselben Fehler
  (liest ebenfalls nur settings.json-Sources).

### A5 — lib/narrator: abgeschnittene Narration wird Erfolg gemeldet
- [x] `lib/audio_processing.py:238+241` — `concatenate_wav_files` liefert bei
  ffmpeg-Fehler UND bei `TimeoutExpired`/`OSError` still `wav_urls[0]` (ersten
  Chunk) statt `None`. Stiller Fallback in der lib, hebelt die Fail-loud-Prüfung
  des Narrators (`tools/narrator/__init__.py:250-254`) aus — 80-min-Narration kann
  als ~30-s-MP3 mit Erfolgsmeldung enden. Fix: `None` zurückgeben (fail-loud).
- [x] `tools/narrator/__init__.py:269-271` — `src.unlink(missing_ok=True)` läuft
  VOR dem `enc.returncode != 0`-Check: schlägt der MP3-Encode fehl, ist das
  konkatenierte WAV (Stunden Synthese) schon gelöscht; partielle `out_path`-MP3
  bleibt liegen. Unlink hinter den Check / in den Erfolgszweig.

### A6 — scheduler_tool: Delivery-Default 5 Stellen, 2 Werte; tote Jobs melden Erfolg
- [x] ⚠️ ENTSCHIEDEN: `review` als einziger Default, `log` gestrichen (Entscheidung: `log` oder `review`?
  funktional identisch, `_deliver_result` ruft immer `_deliver_review`):
  Plugin `__init__.py:39,157` + `prompts/tools/scheduler_create.txt` = `"log"`;
  `prompts/de|en/_intro.txt` = `"review"`; `lib/scheduler.py:422` Laufzeit-Default
  `"review"` (Docstring Z.417 behauptet `"log"`); UI-Editor
  (`state/_agent_editor_mixin.py:35,621-627`) Default `"review"`, `_DELIVERY_DISPLAY`
  kennt `"log"` gar nicht (roher String im Dropdown, nicht reproduzierbar).
- [x] `tools/scheduler_tool/__init__.py:67-83` + `lib/scheduler.py:301-303` —
  ungültige Cron-Expression: `_next_cron_run` → `None` → Job mit `next_run=NULL`
  gespeichert → läuft nie (`get_due_jobs` filtert `IS NOT NULL`), Tool antwortet
  trotzdem `{"success": true}`. Fix: fail-loud (Fehler ans LLM wie beim
  interval-Pfad).

### A7 — google_suite: stiller Fehlschlag + widersprüchliche Defaults
- [x] `tools/google_suite/contacts/tools.py:212-218` (create) + `:281-287` (update)
  — `members:modify`-POST ohne `raise_for_status`, Response verworfen: schlägt die
  Gruppenzuweisung fehl (403/Quota/ungültige Gruppe), meldet das Tool Erfolg.
  Einzige 2 von ~24 HTTP-Calls ohne Prüfung.
- [x] `tools/google_suite/__init__.py` — `GOOGLE_TASKS_ENABLED`/`GOOGLE_DRIVE_ENABLED`
  Default-Konflikt: `get_tools` Z.129/133 = `"false"`, `credential_fields`
  Z.97/104 = `"true"`, `aggregated_scopes` Z.188 = `"true"`. Frische Installation:
  OAuth fordert Scopes an, UI zeigt „Aktiviert", Tools laden nicht. Aktuell durch
  vorhandene settings.json maskiert. Eine Wahrheit definieren (Konstante).

### A8 — workspace: `delete_file` ≡ `delete_document`
- [x] ⚠️ ENTSCHIEDEN: `delete_document` ersatzlos gestrichen (Tool, Description,
  agents.json-Grants, i18n-Key, Prompt-Erwähnungen). `tools/workspace/__init__.py:349-380` vs. `:803-830` — beide rufen exakt
  `fm.delete_file(parent, leaf, from_disk=True, from_index=True)`; Descriptions
  suggerieren unterschiedliche Semantik. Für „nur aus Index" existiert
  `fm.deindex_file` (file_manager.py:530, nur von UI genutzt).
  Entscheidung: `delete_document` streichen ODER auf `deindex_file` umstellen?

---

## B — MITTEL

### B-Systemisch (UI/Infrastruktur, mehrere Plugins betroffen)

- [x] **Tooltip-Pipeline gebrochen:** `state/_settings_mixin.py:557` legt das
  bereits ÜBERSETZTE Label unter `"label_key"` ab; `ui/modals/credentials.py:64-71`
  baut daraus `"<Label>_tooltip"` und schlägt im zentralen `t()` nach — Plugin-
  i18n.json wird nie gelesen. Alle `*_tooltip`-Keys in discord/telegram/email-
  i18n.json + `bible_cred_translation_tooltip` (lib/i18n/de+en.json:99) sind
  unerreichbar; User sieht z.T. rohe `"..._tooltip"`-Strings.
- [x] **Telegram+Discord-Tooltips inhaltlich falsch (TD8):**
  `telegram_channel/i18n.json:10-13` + `discord_channel/i18n.json:19-20` behaupten
  „`*` = alle erlaubt" — Code blockt `*` hart. Beim Tooltip-Fix mit korrigieren.
- [x] **Discord-Placeholder wird gespeicherter Default:**
  `discord_channel/__init__.py:116` — Placeholder
  `"123456789012345678, * für alle"` wird via `CredentialField.__post_init__`
  (plugin_base.py:231-234) zum Default; ungeändert gespeichert akzeptiert
  `_is_discord_user_allowed` die Beispiel-ID als erlaubten Absender. Zudem
  deutscher Text hartcodiert.
- [x] **Agent-Editor zeigt für Discord falsche Allowlist:**
  `state/_agent_editor_mixin.py:361` zeigt `broker.get("discord", "channel_ids")`;
  die sicherheitsrelevante Sender-Allowlist ist `allowed_users` (Stand von vor TD8).

### B-email

- [x] `client.py:427-437, 440-455, 216` — `delete_email`/`move_email` nutzen
  IMAP-**Sequence-Numbers** über getrennte Verbindungen (jede Tool-Aktion =
  neue Verbindung). Expunge dazwischen → falsche Mail wird gelöscht/verschoben.
  Fix: UID-Kommandos (`uid search`/`uid store`), wie der Listener sie nutzt.
- [x] `__init__.py:597-618` — Listener-`_connect_imap` ohne Socket-Timeout;
  `asyncio.wait_for` deckt nur `_idle_cycle`. `_get_existing_uids` + FETCHes in
  `_process_uid` ungeschützt → toter Socket hängt den Listener dauerhaft.
  (client.py:52-54 setzt timeout=30 und dokumentiert warum.)
- [x] `__init__.py:516-518` — CR/LF-Stripping nur fürs Subject, nicht für
  `message.sender` → `outbound.recipient` (gleicher RFC2047-Vektor) →
  `send_email` wirft `ValueError` → Antwort scheitert (und Mail wegen A1 weg).
- [x] `client.py:167` + `tools.py:41` — `n=0` → `msg_ids[-0:]` = KOMPLETTE
  Mailbox (je Mail ein FETCH); negativ ähnlich. Clamp `n >= 1`.
- [x] `client.py:404-416` — Sent-Ordner „Gesendet"→„Sent" hartcodiert
  (GMX-spezifisch) mit Fallback-Kette. Konfigurierbar machen
  (CredentialField/settings.json).

### B-telegram

- [x] `__init__.py:239-242` — `finally` ruft unconditional
  `updater.stop()/app.stop()/app.shutdown()`; bei Boot-Fehler (ungültiger Token)
  ersetzt der Folge-`RuntimeError` („This Updater is not running!") die echte
  Ursache im Hub-Log + 5× sinnloser Restart. Dazu fehlt ein
  `telegram.error.InvalidToken`-Handling analog Discords `LoginFailure`
  (kein Restart bei Config-Fehler).

### B-freeecho2

- [x] `connection.py:236-238` — Pipeline-Cancel im `finally` nicht
  ownership-geschützt (Device-Teardown Z.241 dagegen schon): Room-Takeover-Race —
  alter Handler cancelt die Pipeline der NEUEN Verbindung, Query stirbt still.
  Cancel an die Task-Referenz der eigenen Verbindung binden (strukturell, kein Lock).
- [x] `pipeline.py:228` — `_devices[room] = ws` mitten in der Pipeline: redundant
  (Register-Frame ist SSOT) und schädlich beim Takeover (re-installiert alten
  Socket; alter `finally` hält sich für Owner und löscht den Room-Slot der
  lebendigen neuen Verbindung). Zeile streichen.
- [x] `tts_reply.py:347` — `channel_language()` wird nicht als `language=` an
  `generate_tts` übergeben → bei `FREEECHO2_LANGUAGE=en` synthetisieren
  xtts/dashscope deutsch. (Browser-Pfad übergibt korrekt,
  `_tts_streaming_mixin.py:261,781`.)
- [ ] **Cross-Plugin-Import (verletzt Atomaritäts-Prinzip):**
  `commands.py:404-411` importiert `_load_settings` PRIVAT aus dem
  audio_player-Plugin — audio_player deaktiviert → Voice-Resume bricht.
  Lösung über lib-Helper (Abschnitt E: Settings-Lese-SSOT + Resolver-Bau in
  lib, beide Plugins nutzen ihn); dabei Hardcodes `pre_roll = 3.0` (Z.423) und
  `duration >= 60` (Z.427) als Config-Werte statt Magic Numbers.
- [ ] (TEILWEISE: float-Parse jetzt fail-loud; Dedup → Abschnitt E) `tts_reply.py:314-343` vs. `state/_tts_streaming_mixin.py:151-202` —
  Voice/Speed/Pitch-Auflösung doppelt (Plugin vs. Browser-„SSOT") mit
  Verhaltens-Divergenz; Plugin-Variante: `float(speed_str.replace("x",""))`
  AUSSERHALB des try → kaputter Settings-Wert killt den Reply-Pfad ungefangen.
  Gemeinsamer lib-Helper (settings-dict statt State als Input) bedient beide.
- [x] `pipeline.py:56-63` — Command-Token-Branch in `_handle_audio` unerreichbar
  (`_pending_wake_agent` wird für Command-Tokens nie gesetzt, commands.py:97-109
  returnt vorher); Kommentar behauptet fälschlich „handlers are TODO/(stub)".
  Branch + Kommentar entfernen/korrigieren.
- [x] `__init__.py:176-178` — `FREEECHO2_TTS_VOICE`: totes Write mit Hardcode
  (`values.get(..., "de_DE-thorsten-high")` liefert immer den Default, kein
  CredentialField existiert, Broker-Wert wird nirgends gelesen). Entfernen,
  inkl. verwaistem i18n-Key `freeecho2_cred_tts_voice` (i18n.json:18-21).

### B-workspace

- [x] `__init__.py:808-810` — ENTFALLEN durch A8 (`_delete_document` komplett
  entfernt).
- [x] `__init__.py:30-32` — `_safe_resolve` = expliziter „Compatibility shim"
  (NO-LEGACY-Regel); 2 Aufrufer über Shim, 3 direkt. Ersatzlos streichen,
  Aufrufer auf `fm.safe_resolve`.
- [x] `__init__.py:51-91` — `_list_files` reimplementiert Listing statt
  `fm.list_directory` (file_manager-Docstring beansprucht Exklusivität):
  kein Hidden-File-Filter, kein Indexed-Status, und fehlender `is_dir()`-Check →
  `NotADirectoryError` leakt absoluten Serverpfad ans Modell.
- [x] `__init__.py:263-270` — `_write_file` schreibt via `write_text` an
  `fm.write_file` vorbei (zwei Write-Semantiken auf demselben Baum);
  Read-back-Längen-Verify ist Defensive Programming ohne Fehlerfall.
- [x] (dateiintern erledigt via _chroma_client; repo-weite Factory → E) `__init__.py:839-845` vs. `894-899` — ChromaDB-Client-Konstruktion doppelt
  im File; repo-weit ~8 weitere Stellen ohne Factory (`api/system.py:113` sogar
  hartcodiert `localhost:8000`). Mindestens dateiintern Helper; lib-Factory ⚠️
  (größeres Paket).
- [x] `prompts/de/_intro.txt` + `prompts/en/_intro.txt` — nennen nur 9 von 16
  Tools (fehlen: create_folder, delete_file, delete_folder, copy_file, move_file,
  rename, list_orphaned); write_file-Extension-Liste falsch (`.xml`/`.html`
  fehlen — `.html` trägt das SANDBOX_HTML_URL-Embedding). Beide Sprachen.

### B-audio_player

- [x] `__init__.py:263,428-432` — `restart`-Parameter von `audio_play_folder`
  ist tot (Signatur + Schema versprechen Verhalten, Körper referenziert ihn nie,
  `play_queue` kennt ihn nicht). Implementieren oder entfernen. ⚠️
- [x] `__init__.py:340-342,388-392,407` — Doppel-Shuffle: Tool shuffled selbst
  UND übergibt `shuffle=True` an `channel.play_queue()` (das erneut mischt) →
  zurückgegebene `files[:10]`-Preview ≠ Abspielreihenfolge. Eine Shuffle-Stelle.
- [x] `__init__.py:754` — `audio_speed`: `float(factor)` ohne Finite-Check
  (json akzeptiert NaN/Infinity; genau dafür existiert `_finite_seconds` Z.30-44,
  das seek/skip nutzen); freeecho2 reicht roh an mpv-IPC durch.
- [x] `__init__.py:49-56` — `_load_settings` verschluckt OSError/JSONDecodeError
  still → `{}` (korrupte settings.json = Streams weg, kein Log). Mindestens loggen.
- [x] `prompts/de/_intro.txt` (68 Z.) vs. `prompts/en/_intro.txt` (31 Z.) —
  EN fehlen: FTS5-Such-Strategie (a-d), „keine Halluzinationen bei 0 Treffern",
  Groß-/Kleinschreibung, Target-Parameter-Abschnitt. Synchronisieren
  (Prompts-bilingual-Regel).
- [x] `__init__.py:1114-1137` — `get_ui_status`: hartcodierte deutsche Texte,
  `lang` ignoriert (Repo-Konvention: `t(...)` aus lib/i18n, siehe research);
  Zahlen ohne `format_number`; 7 von 14 Tools ganz ohne Status
  (u.a. die langlaufenden play_folder/index_rebuild).
- [x] `settings.json:23-28` — kompletter `limits`-Block (max_duration_min,
  max_buffer_mb, connect/read_timeout) hat repo-weit null Konsumenten. ⚠️
  Entfernen oder implementieren.
- [x] `settings.json:7-10` + `ui/audio_settings.py:255` — `list.default_limit`/
  `search_default_limit` werden per UI editiert, aber von keinem Codepfad
  angewendet (Tools defaulten bewusst auf ALLE). ⚠️ UI-Zeile raus oder Tools
  konsumieren den Wert.

### B-epim

- [x] `db.py:1027-1036` vs. `:955` — `update_note_tab` schreibt nur `TEXT`,
  `get_note` bevorzugt `TEXT2` → Update an Tab mit befülltem TEXT2 unsichtbar.
  Fail-loud-Guard analog `_read_fieldsdata_for_update` (db.py:485-489) fehlt
  für TEXT2. ⚠️ Strategie: TEXT2 mitschreiben/leeren oder Guard?
- [x] `db.py:906-911` — `search_notes` durchsucht `TEXT2` nicht → Inhalte, die
  nur dort liegen, per Suche unauffindbar (get_note zeigt sie).
- [x] `db.py:1237-1244` vs. `:1210-1215` — `create_password` schreibt Gruppe in
  `PATH`, setzt `IDPARENT` nicht; `search_passwords` liest Gruppe über
  `IDPARENT` → per Tool erstellte Einträge zeigen nie ihre Gruppe.
  ⚠️ Welche Spalte ist im EPIM-Schema korrekt? (Vermutlich IDPARENT.)
- [x] `tools.py:253,234` + `db.py:199-237` — `work_email` existiert nicht in
  `DEFAULT_CONTACT_FIELDS`; `encode_fieldsdata` (db.py:163-165) überspringt
  unmappbare Namen kommentarlos → Wert geht still verloren, Tool meldet success.
  Fail-loud beim Encode + `work_email` aus `_contact_keys` entfernen (oder Feld
  ergänzen).
- [x] Stille Namens-Auflösungs-Fallbacks (Regel-Verstoß): `db.py:656-659+683`
  (unbekannte Kategorie → Termin ohne Kategorie, success), `:704-707,712-715`
  (update_task: Feld still weggelassen), `:1165-1168` (update_todo),
  `:670-673,978-981,1136-1139` (fehlender Kalender/Tree/Liste → still erste
  Zeile). Nicht-auflösbar → Fehler ans LLM.
- [x] `db.py:613-618, 792-796, 933-936` — `get_task`/`get_contact`/`get_note`
  filtern nicht auf `STATUS = 0` → `epim_get` liefert soft-gelöschte Einträge
  (search + `_row_exists` filtern konsequent).
- [x] ⚠️ ENTSCHIEDEN (keine Ausnahmen): auf load_tool_description migriert. `tools.py:395-400` — EPIM lädt Tool-Descriptions via
  `load_prompt("shared/epim_tool_*")` statt `load_tool_description` +
  `prompts/tools/` (einziges Plugin; Descriptions dadurch zweisprachig statt
  nur EN). Migration = Entscheidung.
- [x] `db.py:199-237` / `tools.py:226-256` / `prompts/de|en/_intro.txt` /
  `tools.py:474` — Kontaktfeld-Namen an 3+ Stellen handgepflegt
  (Drift = Ursache von work_email-Bug). `_contact_keys`/`_en_to_de` aus
  `DEFAULT_CONTACT_FIELDS` ableiten.
- [x] `db.py:720-734, 862-876, 1173-1187, 1275-1289` — Soft-Delete-Muster 4×
  identisch → `_soft_delete(table, id_col, id)`-Helper (delete_note bleibt
  2-Statement-Sonderfall).

### B-google_suite

- [ ] `calendar/tools.py:108-115` — nutzloser GET vor PATCH in `update_event`
  (Response nie gelesen, Copy-Paste vom tasks-PUT-Muster). GET streichen.
- [ ] `prompts/de|en/_intro.txt` — monolithisch, beschreibt immer alle 4
  Services (auch wenn Sub-Service deaktiviert/nicht granted). In per-Service-
  Fragmente splitten (`+`-Konvention, wie Vision es hatte).
- [ ] `prompts/tools/*.txt` (alle 26) — Descriptions DEUTSCH statt Englisch
  (Konvention plugin_base.py:99 „Nur Englisch: Empfänger ist das Modell";
  einziges abweichendes Plugin). Übersetzen. Auch die JSON-Schema-Parameter-
  Descriptions im Code sind deutsch.
- [ ] `prompts/tools/google_drive_get_file.txt` — behauptet „50.000 Zeichen"-Limit;
  real: 5-MB-Byte-Cap (`DRIVE_MAX_DOWNLOAD_BYTES`, drive/tools.py:42,60-73). 
- [ ] `__init__.py:38` — Plugin-Description nennt Gmail (existiert nicht),
  verschweigt Contacts/Tasks. Korrigieren.
- [ ] ⚠️ Delete-Tools auf `TIER_WRITE_DATA` (2) statt `TIER_WRITE_SYSTEM` (3):
  `calendar/tools.py:234`, `contacts/tools.py:402`, `tasks/tools.py:242`,
  `drive/tools.py:393` — Repo-Konvention (lib/security.py:36) stuft Deletes als
  Tier 3; Drive löscht endgültig (kein Papierkorb). Externe Kanäle mit
  max_tier 2 dürfen aktuell endgültig löschen.
- [ ] HTTP-Boilerplate ~24× (`async with httpx.AsyncClient()` + Bearer-Header +
  `timeout=15` + `raise_for_status`) → Helper `_google_request(...)` in
  `_common.py` (intra-Plugin; die 2 raise_for_status-Ausreißer aus A7 sind
  genau dort entstanden).
- [ ] `contacts/tools.py:184-188` vs. `:249-253` — givenName/familyName-Split
  doppelt inkl. Kommentar → Helper neben `_format_person`.

### B-translator

- [ ] `__init__.py:98,127-136` — `_FENCE_RE` matcht bei 4+-Backtick-Fences nur
  ` ``` ` → Closing-Check trifft nie → Rest des Dokuments landet unübersetzt im
  „unbalanced block"-Pass-Through, Tool meldet Erfolg. Auch eingerückte Fences
  (bis 3 Spaces, CommonMark) und Blockquote-Fences nicht erkannt.
- [ ] `__init__.py:246-263` vs. `:303-320` — ~18 Zeilen Validierung verbatim
  doppelt (API-Key, target_lang, formality inkl. Error-JSONs) → gemeinsamer
  Helper; dabei überflüssiges `lang_codes = set(DEEPL_LANGUAGES.keys())` weg.

### B-narrator

- [ ] `__init__.py:343` + `prompts/tools/narrate_file.txt` (Abs. 4) UND
  `translator/__init__.py:464` — Pfad-Beispiel `'documents/meeting-DE.txt'` ist
  falsch (Pfade relativ zu `data/documents/`; das Beispiel zeigt auf
  `data/documents/documents/…` → „File not found"). In beiden Plugins fixen.
- [ ] `__init__.py:26-36` — `_voice_names` = ~6. Kopie des Musters
  „`get_voices()` except → `voices_fallback`" (weitere:
  `_tts_config_mixin.py:203-209, 329/335, 769-784, 871-882`,
  `tts_stress_burnin.py:242-255`). Helper in lib (z.B. tts_engines/registry).
- [ ] `__init__.py:39-69` vs. `_tts_config_mixin.py:183-187,216ff` —
  Engine/Voice-Auflösung („auto → tts_engine/fallback_engine; Voice =
  gespeicherte pro Engine, sonst erste") doppelt (State- vs. settings-basiert).
  Gemeinsamer Helper. (Plugin↔State/lib, kein Cross-Plugin.)

### B-bible

- [ ] `reference.py:86-92` (`_flex`) + `book_aliases/de.json`/`en.json` —
  Zitierformen „Ziffer + Space + Kurzform" werden nicht erkannt:
  `"2 Tim 1,7"`, `"1 Kor 13"`, `"1 Petr 5,7"` → None → stiller Fall in die
  unscharfe thematische Suche. Fix: `_flex` nach führender Ziffer `\.?\s*`
  erlauben (oder Aliasse mit Space pflegen). Denselben Fix ggf. parallel in
  judaica nachziehen (Duplikatpflege ist akzeptierter Preis).
- [ ] `__init__.py:61-77,146-151` — Settings-Boilerplate
  (`_settings_path`/`_load_settings`/`_save_settings` + env-Load) dupliziert
  google_suite und reimplementiert BaseChannel-Mechanik; Save-Pfad hängt an
  `getattr(tool, "_save_settings")`-Duck-Typing (`_settings_mixin.py:800,830`).
  ⚠️ Gemeinsame Basis für Tool-Plugins in plugin_base (lib = erlaubt)?

### B-scheduler_tool

- [ ] `__init__.py:63-65` — `DEFAULT_TIER_BY_SOURCE.get("cron", 1)`: einziger
  Konsument des als „(legacy, kept for compat)" markierten Keys
  (lib/security.py:46); Jobs laufen real als `channel="scheduler"` (Kommentar
  falsch); Fallback `1` = Magic-Number-Duplikat von TIER_COMMUNICATE.
  Explizit machen (gewünschtes Tier direkt benennen), Legacy-Key danach
  entfernen. ⚠️ Gewolltes Job-Tier klären (1 wie jetzt, oder Scheduler-Default 0?).

### B-system_monitor

- [ ] `__init__.py:143-146` — Temperatur-Block: `except Exception: pass`
  verschluckt jeden Fehler (kaputtes `sensors -j`-JSON) komplett still —
  CPU/RAM/Disk schreiben `{"error": ...}` ins Resultat. Angleichen; fehlendes
  sensors-Binary darf optional bleiben (⚠️ kurz absegnen).

### B-calculator

- [ ] `__init__.py:18` — `description = "… (sympy-basiert)."` ist falsch:
  reiner AST-Walker, sympy kommt im Repo nicht vor; verspricht im Plugin-Manager
  Fähigkeiten (sqrt/sin/Symbolik), die fehlen. Text korrigieren.

---

## C — NIEDRIG (Kleinkram, trotzdem alles fixen)

### C-email
- [ ] `client.py:260-262` — SEARCH mit Umlauten („Müller") →
  `UnicodeEncodeError` (imaplib ASCII, kein CHARSET/Literal-Handling); Suche mit
  Umlauten funktioniert nie.
- [ ] `client.py:171,201` — Preview `BODY.PEEK[TEXT]<0.400>` liefert rohe
  base64/quoted-printable-Bodies ans LLM.
- [ ] `__init__.py:609-617` — UIDVALIDITY-Ermittlung `except Exception: pass`
  ohne Log (uv bleibt still 0 → Recovery-Skip-Logik rechnet falsch); dazu
  doppelter `import re` (Z.612, Modul-Import Z.14 existiert).
- [ ] `__init__.py:63-67` — korrupter Checkpoint → still `(0, 0)` → Recovery
  komplett übersprungen, kein Log.
- [ ] `__init__.py:472` — `metadata.get("subject", "Re: AIfred")` hartcodierter
  Default (Key wird von build_reply_metadata immer gesetzt — zweite Wahrheit).
- [ ] `__init__.py:597` vs. `client.py:147` — zwei IMAP-Connect-Implementierungen
  im selben Plugin (Timeout-Asymmetrie = B-Finding oben); nach Timeout-Fix
  konsolidieren.
- [ ] `tools.py:41` — `min(n, 20)`: Magic 20 dupliziert `EMAIL_MAX_FETCH`
  (config.py:14).
- [ ] `tools.py:21-22` vs. `:198,235` — `_SAFE_ACTIONS`/`_MANAGE_ACTIONS` und
  Schema-Enums kodieren dieselben Mengen doppelt → `sorted(...)` aus den Sets.
- [ ] `client.py:376` — `import email.utils as _eu` doppelt (Z.11 importiert).

### C-telegram
- [ ] `__init__.py:385-386` — `_execute_telegram_send` loggt/returnt Original-
  `chat_id` (leer beim Owner-Default) statt aufgelöstem `target`.
- [ ] `__init__.py:263-292` — `_deliver`: nicht existenter lokaler Media-Pfad →
  Attachment still verworfen (Reply-Pfad), kein Log.
- [ ] `__init__.py:52-57` — `_msglog_load` verschluckt korrupte Datei still →
  `{}`; Decode-Fehler mindestens loggen. (Writes Z.69/77 nicht atomar.)
- [ ] `__init__.py:511-527` — `_split_message`: `rfind` == 0 bzw. leerer Text →
  leerer Chunk → Telegram-API-Fehler „message text is empty" (Discord schützt
  sich mit `chunk or None`).
- [ ] `__init__.py:191-194` — `/clear`-Bestätigungstext hartcodiert EN im Code
  (i18n/prompts wären der Ort); Discord identisch (Z.180-199) → zusammen fixen.
- [ ] `__init__.py:183-193` — `deleted += len(batch)` zählt von deleteMessages
  still übersprungene IDs mit (Bestätigung = Obergrenze, kein Ist).
- [ ] `__init__.py:423-424` — `build_reply_metadata`-Override = exakt der
  Base-Default → streichen.
- [ ] `__init__.py:329,433` — doppelte Imports (json lokal trotz Modul-Import;
  `from pathlib import Path` neben `import pathlib` — zwei Stile in einer Datei).
- [ ] `__init__.py:442-451` vs. `lib/security.py:116-130` — Owner-Konvention
  („erster Allowlist-Eintrag") doppelt implementiert (Plugin↔lib; lib ist SSOT).

### C-discord
- [ ] `__init__.py:191-200` — `/clear` prüft User-Rechte statt Bot-Rechte; ohne
  Bot-`manage_messages` wirft `purge()` unbehandelt `Forbidden` (nach bereits
  gesendeter Response).
- [ ] `__init__.py:311-314` — leerer Text ohne Attachment → `send(None)` →
  „Cannot send an empty message", im send_reply-Pfad unbehandelt.
- [ ] `__init__.py:311` — Hard-Split `text[i:i+2000]` zerschneidet Wörter/
  Codeblöcke; 2000 als Magic Number. Lösung: lib-Chunker aus Abschnitt E
  nutzen (bis dahin mindestens Konstante + Umbruch an Zeilengrenzen).
- [ ] `__init__.py:28-37,69` — ungültige Channel-IDs/Allowlist-Einträge still
  verworfen; alle ungültig → Bot lauscht still auf ALLEN Channels. Log-Warnung
  pro verworfenem Eintrag. Intra-Duplikat: Allowlist-Parse = Kopie von
  `_parse_channel_ids` → wiederverwenden.
- [ ] `__init__.py:308` — redundanter lokaler `import discord` (top-level Z.13).
- [ ] `__init__.py:229` — `created_at.replace(tzinfo=utc)` ist No-Op
  (discord.py 2.x liefert aware UTC); samt dann unnötigem timezone-Import.
- [ ] `__init__.py:204-206` — `tree.sync()` in `on_ready` (feuert bei jedem
  Reconnect, rate-limitierter Global-Call). Einmalig/gated syncen.
- [ ] `lib/credential_broker.py:78-80` — `("discord", "allowed_users")` fehlt in
  `_CREDENTIAL_MAP` (Telegram hat den Eintrag; funktioniert nur über generische
  Ableitung) — Konsistenz.

### C-freeecho2
- [ ] `ws_bridge.py:292` — `send_done` als einzige Sende-Methode ohne
  `wait_for(..., _CHUNK_SEND_TIMEOUT_SEC)` (Docstring begründet den Timeout
  explizit mit ~2-min-TCP-Hang).
- [ ] `ws_bridge.py` — 6× identisches Sende-Boilerplate
  (get→None-Check→wait_for→Timeout-Log→Except-Log); nur flag/start prüfen
  `ws.closed` → `_send_frame`-Helper (intra-Plugin).
- [x] `__init__.py:164-169` — SSL-Zweige in `apply_credentials` unerreichbar
  (keine CredentialFields) — streichen (TLS via env/Broker funktioniert).
- [ ] `_shared.py:65` — `_pending_responses` komplett ungenutzt — streichen.
- [ ] `tts_reply.py:133` — Magic `96000` dupliziert
  `alert_queue._PCM_BYTES_PER_SEC` (intra-Plugin) — Konstante importieren.
- [ ] `__init__.py:204-205` vs. `tts_reply.py:138-144` — audio_type-Whitelist
  doppelt, davon einmal STILL koerziert — eine geloggte Prüfstelle.
- [ ] `ws_bridge.py:128-129,195-196,223-224,268-269` + `tts_reply.py:53` —
  deutsche Log-Texte („WS bleibt offen", „TTS-Bestaetigung") → Englisch.
- [ ] `_shared.py:37` / `connection.py:159` — `_reject_log_last` wächst
  unbegrenzt (nie gepruned).
- [ ] `pipeline.py:296`, `tts_reply.py:245,276` — deprecated
  `asyncio.get_event_loop()` → `get_running_loop()` (pipeline.py:156ff macht es
  schon richtig).
- [ ] `connection.py:139-143` + `commands.py:29` — Register-Frame doppelt
  JSON-geparst.
- [ ] `commands.py:151-159,183-188` — `_done`-Token fehlt in Emoji-Map und
  Docstring-Listing (Handler existiert).

### C-workspace
- [x] `__init__.py:285` vs. `:254` — `.htm` im Embed-Check unerreichbar
  (nicht in allowed_extensions) — konsistent machen.
- [x] `__init__.py:254` — Write-Whitelist hartcodiert im Executor, während die
  Index-Whitelist `DOCUMENT_ALLOWED_EXTENSIONS` (config.py:1397) konfigurierbar
  ist — angleichen.
- [ ] `__init__.py:325-326,351-352,384-385,499-500,805-806` — Parent/Leaf-Split
  3-Zeilen-Pattern 5× → Helper.
- [ ] `__init__.py:621` — redundanter Funktions-Import `list_indexed`
  (`fm.list_indexed` Z.756 zeigt den richtigen Weg).
- [x] `__init__.py:27` — `_DOCUMENTS_DIR = DOCUMENTS_DIR` sinnfreier Alias.
- [ ] `__init__.py:354` — `path, _ = fm.safe_resolve(...)` verwirft Fehler
  (folgenlos, aber Muster).
- [ ] `__init__.py:947-974` — `get_ui_status` deckt copy_file/move_file/rename/
  list_orphaned nicht ab.

### C-audio_player
- [ ] `__init__.py:913-921` — FS-Fallback von `audio_list`: Limit bricht Walk
  VOR der Sortierung ab (willkürliche Teilmenge); plain `sort()` statt
  `_natural_key` („CD 10" vor „CD 2").
- [ ] `__init__.py:277-282` vs. `:63-68` — Streams-Extraktion+Source-Map-Bau in
  `_play_folder` dupliziert `_make_resolver` (intra-Plugin; 3. Kopie im Mixin).
- [ ] Settings-Reader 4× (`__init__.py:47-56`,
  `_audio_player_mixin.py:259-273`, `audio_processing.py:92-112`,
  `audio_index.py:575-591`) — Plugin↔lib/state: eine Lese-SSOT (lib) schaffen. ⚠️
- [ ] Path-Traversal-Guard 3× (`audio_sources.py:290-296` = lib-SSOT,
  `__init__.py:304-310`, `:894-906`) — Plugin-Kopien auf lib-Helper umstellen.
- [ ] `__init__.py:887` — `from pathlib import Path as _Path` (top-level Z.19
  existiert); sinnlose `cfg`-Zwischen-Dict-Konstruktion Z.886-888.
- [ ] `__init__.py:91,94-102` — defensive Fallbacks in `_resolve_target`:
  `getattr(ctx, "session_id", ...)` auf Pflichtfeld; freeecho2 ohne Room →
  stiller Fallback auf `local` (Audio am Server-mpv statt Puck, ohne Signal).
- [ ] `__init__.py:476-488,563,574-577` — `_dispatch_action` „all": Falsy-
  Ergebnisse verschwinden, immer `success: True`; `except: continue` im
  All-Loop still; explizit-Zweig fängt gar nicht — vereinheitlichen.
- [ ] `__init__.py:927` — „call audio_index_rebuild first" geht auch an
  read-only-Quellen, die das Tool nicht haben.

### C-epim
- [ ] `tools.py:322-330` — Update-Pfad ohne die Create-Sanitisierung
  (`_as_bool` für allday, „high"/„low" für priority) — roh in die DB.
- [x] `db.py:1164` — `fields.pop("list_name") or fields.pop("list")`:
  Short-circuit-Muster, das update_task (Z.696-699) selbst als Bug dokumentiert
  — angleichen.
- [ ] `db.py:781-783,803-804` + `:89` — FIELDSDATA2-Read-Pfad: bytes würde in
  `raw.encode("utf-8")` crashen (latent, 0/399 betroffen); Update-Pfad ist
  fail-loud, Read-Pfad angleichen.
- [ ] `tools.py:385-386` vs. `:197-198,318-319` — `epim_delete` für `password`
  ohne den Browser-only-Guard von create/update — symmetrisch machen.
- [ ] `tools.py:331-337` — Kontakt-Update ignoriert flache Felder kommentarlos
  (create mappt sie); mindestens Fehler melden.
- [ ] `db.py:839-860` vs. `:1250-1273` — update_contact/update_password fast
  identisch → Helper (intra).
- [ ] `db.py:1233-1234` vs. `:1266-1267` — PASSENTRYFIELDS-Query doppelt.
- [ ] `db.py:824,854` — `name_to_id`-Inversion doppelt.
- [ ] `db.py:780-782,802-804` — FIELDSDATA2-Override-Snippet doppelt.
- [ ] `db.py:700-707,708-715` — Alias-Auflösungs-Block in update_task doppelt.
- [x] `db.py:1210-1215` — search_passwords-Subquery-Verschachtelung →
  simpler LEFT JOIN.
- [ ] `db.py:371-376` — `EpimDatabase.close()` ohne Aufrufer — streichen.
- [ ] `db.py:547-549,744,1074` — tote Suchparameter (location/tags/category/
  list_name), vom Tool-Schema nie angeboten — ⚠️ ins Schema aufnehmen oder
  entfernen.
- [ ] `tools.py:132` — „gefunden" im Debug-Log → Englisch.
- [ ] `tools.py:201,220,267,280,295` + `db.py:967` — deutsche Default-Titel
  („Neuer Termin", „Tab 1") hartcodiert — ⚠️ ok als Datenbestand oder
  sprachabhängig?
- [ ] `db.py:363-364` — „SYSDBA"/„masterkey" hartcodiert (bei Firebird embedded
  funktional egal — Kommentar dazu, oder Konstante).

### C-google_suite
- [ ] `*/tools.py` — `logger` + `import logging` in allen 4 Modulen definiert,
  nie benutzt — streichen.
- [ ] `get_*_tools(lang)` — `lang`-Parameter in allen 4 Factories ungenutzt —
  entfernen (Aufrufer in `__init__.py:123-135` anpassen).
- [ ] `_PLUGIN_DIR`-Block 4× identisch inkl. Kommentar → `_common.py`.
- [ ] `__init__.py:145-172` — status_map = dritte handgepflegte Liste aller
  26 Tool-Namen → i18n-Key aus Tool-Namen ableiten.
- [ ] Hartcodierte deutsche Strings: `calendar/tools.py:55` „(kein Titel)";
  `_common.py:8` RuntimeError; `contacts/tools.py:67` ValueError;
  `drive/tools.py:67-69` RuntimeError; `__init__.py:84-105` „Aktiviert"/
  „Deaktiviert"-Options-Labels (i18n.json existiert).
- [ ] `drive/tools.py:68` — MB-Zahl ohne `format_number` in User-facing Meldung.
- [ ] `contacts/tools.py:124,155` — keine Clamps auf Google-Limits
  (batchGet max 200, searchContacts pageSize max 30) → HTTP 400 statt sauberer
  Begrenzung.
- [ ] Kleinkram: `__init__.py:54-60` `_translate` liest i18n.json bei jedem
  Aufruf neu; `drive/tools.py:162-163` `meta.json()` doppelt geparst;
  `calendar/tools.py:109-135` zwei AsyncClients wo einer reicht.

### C-translator
- [ ] `__init__.py:408-419` vs. `:468-486` + `:429-443` vs. `:487-495` —
  Parameter-Schema-Descriptions (target_lang/source_lang/formality) verbatim
  doppelt → Modul-Konstanten.
- [ ] Kein `prompts/de|en/` (nur tools/) — Anleitung lebt allein in den
  Descriptions. ⚠️ Konvention für Kleinst-Plugins ok so? (calculator, narrator,
  research gleich.)

### C-narrator
- [ ] `__init__.py:145-148,207-212` vs. `:171-173,301-303` — unbekannter
  Engine-Key nur in 2 von 3 Pfaden abgefangen; Single-Voice-Pfad liefert
  irreführendes „TTS failed at chunk 1/N" statt „Unknown TTS engine".
- [ ] `__init__.py:6-8` — Modul-Docstring sagt „single WAV", Default ist MP3;
  `:107-110` description sagt „satzweise", Code chunkt absatzweise.
- [ ] `__init__.py:216-254` — Datei nur aus Sprecher-Markern ohne Text →
  Fehlermeldung „ffmpeg concat failed" statt Ursache (leere Segmentliste vorher
  abfangen).

### C-bible / C-judaica
- [ ] `bible/reference.py:71-78` — `_active_bible_path`: fehlende konfigurierte
  Übersetzung → still erste verfügbare (andere Übersetzung ohne Hinweis) —
  mindestens Log-Zeile.
- [ ] `bible/__init__.py:96` vs. `reference.py:41` — Ordnername `"bibel"` als
  Literal doppelt → Konstante (judaica macht es mit `_JUDAICA_FOLDER` vor).
- [ ] `judaica/__init__.py:34` vs. `reference.py:31` — `_JUDAICA_DIR` doppelt
  definiert.
- [ ] `judaica/reference.py:145` vs. `:167` — `section_type` aus zwei Quellen
  (_index.json vs. Werk-JSON) — eine Quelle wählen.
- [ ] `judaica/reference.py:177` vs. `:184-188` — `sorted(key=int)` (wirft) vs.
  `isdigit()`-Filter (defensiv) — eine Strategie.
- [ ] ⚠️ Verfügbarkeits-Semantik der Geschwister angleichen: judaica degradiert
  bei fehlender `_index.json` still zur thematischen Suche (reference.py:129,
  kein Log), bible schaltet ohne Struktur-JSON komplett ab (`__init__.py:80`).
  Welche Semantik soll gelten? (Jeweils plugin-lokal umsetzen.)

### C-scheduler_tool
- [ ] `__init__.py:209-216` — get_ui_status hartcodierte EN-Strings statt
  `t(...)` (research/sandbox machen es vor).
- [ ] `__init__.py:47-48` — `schedule_type` dreifach validiert (Executor +
  Schema-Enum + SQLite-CHECK); `delivery` dagegen nur Schema — Validierungstiefe
  vereinheitlichen.

### C-system_monitor
- [ ] `__init__.py:129,42` — `import json as _json` / `import os` lokal trotz
  Modul-Import — aufräumen.
- [ ] `__init__.py:38-40` — `subprocess cat /proc/loadavg` →
  `Path("/proc/loadavg").read_text()`.

### C-calculator
- [ ] `__init__.py` — Randnotiz: `bool` ist int-Subklasse (`True + 1` wird
  ausgewertet) — bei Gelegenheit `type(x) in (int, float)` statt isinstance.

### C-research / C-sandbox
- [ ] research `__init__.py:40` — Ellipse auch bei Queries < 60 Zeichen —
  konditional (wie translator:512).
- [ ] sandbox `__init__.py:30` — get_ui_status kennt `render_html` nicht
  (drittes Tool aus `get_sandbox_tools`).
- [ ] sandbox `prompts/de|en/` — kein `_intro`, kein Fragment für
  `execute_code` allein; `execute_code_write` in keinem Fragment-Namen. Agent
  mit nur execute_code bekäme null Anleitung (latent — aktuelle Agenten haben
  alle drei Tools).
- [ ] ⚠️ Architektur-Notiz research/sandbox: Anleitungen leben im globalen
  `tool_instructions.txt` und sind NICHT über granted_tools gegated — Agent
  ohne web_search bekommt web_search-Anweisungen trotzdem
  (prompt_loader.py:760-764). Umbau = Entscheidung.
- [ ] `lib/sandbox_tools.py:53` — Docstring „low-tier contexts only see
  execute_code" falsch (execute_code ist selbst Tier 2; Tier 0/1 sieht kein
  Sandbox-Tool) — Kommentar korrigieren.

### C-Infrastruktur / lib-Randnotizen
- [x] ⚠️ NEU (Fund bei A8), GELÖSCHT nach Freigabe: `prompts/de/shared/workspace_instructions.txt`,
  `prompts/en/shared/workspace_instructions.txt` und
  `prompts/shared/document_tools.txt` werden NIRGENDS geladen (repo-weiter
  Grep, kein `load_prompt`-Aufrufer) — tote Legacy-Dateien, inhaltlich
  Duplikate des workspace-`_intro`. Löschen nach Freigabe.
- [ ] `plugins/__init__.py` — Docstring verweist auf nicht existierende Module
  `aifred.plugins.registry`/`aifred.plugins.base` (real:
  `aifred.lib.plugin_registry`/`plugin_base`).
- [ ] `get_prompt_instructions`-Boilerplate 13× wortgleich in allen
  Tool-Plugins — ⚠️ Default in plugin_base (lib = erlaubt) oder Konvention
  belassen?
- [ ] `lib/scheduler.py:296-300` — croniter-ImportError → stiller
  1h-Intervall-Fallback (derzeit schlafend, croniter installiert) — fail-loud
  machen.
- [ ] `lib/scheduler.py:276,299` — `__import__("datetime").timedelta` trotz
  Top-Level-Import — aufräumen.

---

## E — lib-SSOT-Kandidaten (Cross-Plugin-Duplikate → aifred/lib konsolidieren)

Größere Refactor-Pakete, je eines pro Arbeitspaket. Lösung ist IMMER ein
lib-Helper — nie ein Import zwischen Plugins.

- [ ] **Sender-Allowlist-Helper in lib** (z.B. `lib/security.py`):
  `telegram_channel/__init__.py:454-479` und `discord_channel/__init__.py:50-69`
  sind derselbe ~20-Zeilen-Block (Komma-Parse, leer=niemand, TD8-`*`-Block,
  numerische ID-Prüfung); einziger Unterschied ist der Broker-Key. Zusätzlich
  Owner-Konvention „erster Eintrag" mit `security._is_owner` zusammenführen
  (aktuell auch in `telegram _owner_chat_id` dupliziert).
- [ ] **Message-Chunker in lib** (Limit als Parameter): Telegrams
  `_split_message` (`telegram_channel/__init__.py:511-527`, inkl. Leer-Chunk-Fix
  aus C-telegram) als Basis; Discord (naiver Hard-Split `text[i:i+2000]`) und
  Telegram stellen darauf um.
- [ ] **Lokaler-Media-Pfad-Helper in lib** (neben `resolve_outbound_attachment`
  in vision_utils): `_local_photo_path` (telegram:429-434) ≡ `_local_file_path`
  (discord:40-47).
- [ ] **`_dispatch_inbound` in lib** (message_hub/message_processor): existiert
  3× (discord:427-438 ≡ email:715-726 wortgleich, telegram:530-533 kürzer und
  ohne Ergebnis-Log — vereinheitlichen).
- [ ] ⚠️ **bible/judaica-Referenz-Kern in lib** (z.B. `lib/reference_lookup.py`):
  `_flex`/`_norm`/Pattern-Builder (parametrisiert um Amud-Gruppe)/Range-Logik/
  `resolve`-Gerüst/„reference-or-thematic"-Executor sind per diff nahezu
  identisch; Plugins behielten nur Datenmodelle (Vilna-Konversion,
  Übersetzungs-Setting). ⚠️ Peuqui hatte zunächst „keine SSOT für bible/judaica"
  gesagt (Begründung war die Plugin-Kette, die bei lib nicht greift) — vor
  Umsetzung kurz bestätigen. Bis dahin: `_flex`-Bugfix (B-bible) in BEIDEN
  Plugins parallel.
- [ ] **freeecho2-Resume ohne audio_player-Import** (siehe auch B-freeecho2):
  der private Import `_load_settings` aus dem audio_player-Plugin
  (`commands.py:404-411`) ist der einzige echte Plugin→Plugin-Verstoß im Repo.
  Benötigte Teile (Settings-Lese-SSOT, Resolver-Bau) nach lib, beide Plugins
  nutzen den lib-Helper.
- [ ] **Settings-Handling für Tool-Plugins in plugin_base** (lib): bible und
  google_suite reimplementieren `_load_settings`/`_save_settings`/env-Load;
  Save-Pfad hängt an `getattr`-Duck-Typing (`_settings_mixin.py:800,830`).
  Gemeinsame Basis analog `BaseChannel.load_settings/save_settings/
  load_settings_to_env`.
- [ ] **ChromaDB-Client-Factory in lib**: ~10 Konstruktionsstellen repo-weit
  (2× workspace, document_store, agent_memory, vector_cache, api/system.py —
  dort hartcodiert `localhost:8000` —, _memory_browser_mixin 3×).
- [ ] **audio_player-Settings-Lese-SSOT in lib**: 4 unabhängige Reader
  (Plugin, `_audio_player_mixin`, `audio_processing`, `audio_index`).
- [ ] **Voice-Katalog- und Engine/Voice-Auflösungs-Helper in lib**
  (tts_engines): ~6 Kopien des `get_voices()→voices_fallback`-Musters +
  doppelte Engine/Voice-Auflösung narrator↔`_tts_config_mixin` und
  freeecho2-`_run_tts`↔`_resolve_agent_tts` (settings-dict als Input, damit
  Browser- und Channel-Pfad denselben Helper nutzen).
- [ ] ⚠️ **`get_prompt_instructions`-Default in plugin_base**: 13× wortgleiche
  Boilerplate — Default-Implementierung in lib oder Konvention belassen?

**Offen (User-Entscheidung ausstehend):**
- Atomaritäts-Prinzip (Plugin→Plugin verboten, Plugin→lib erlaubt) in
  Projekt-CLAUDE.md aufnehmen? (Im Claude-Memory bereits gespeichert.)
