# REST API

> **Deutsche Version:** [rest-api.md](../../de/guides/rest-api.md)

AIfred's backend exposes a REST API under `/api` (FastAPI, port `8002`). It is a
**remote control for the browser**: it does not run inference itself, but
queues messages into a browser session, which then runs the full pipeline —
intent detection, multi-agent modes, research, vision, history compression.
One code path, all features, and you can watch the answer stream in the
browser while your script waits.

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

For jobs that should run **without** a browser (cron, webhooks) use the
[scheduler and the webhook endpoint](../architecture/scheduler.md) — they run in
an isolated session with their own security tier.

Interactive OpenAPI docs: `/api/docs` (login required, see below).

---

## Authentication

| Route | Auth |
|---|---|
| `/api/chat/inject` | `token` field = `INJECT_API_TOKEN` from `.env` |
| `/api/agent/trigger` | `token` field = `WEBHOOK_API_TOKEN` from `.env` |
| `/api/audio/announce` · `/api/audio/announce/rooms` | header `Authorization: Bearer <ANNOUNCE_API_TOKEN>` from `.env` |
| `/api/oauth/{provider}/callback` | none (arrives as provider redirect) |
| `/api/auth/login` · `/api/app/*` | none: login of the Vue UI and its shell (code, colours, texts, language) |
| **everything else** (incl. `/api/docs`) | login cookie `aifred_username`, as set by the web login |

- Token endpoints answer **503** if the token is not configured and **403** if it
  is wrong. They are the intended entry points for scripts and other machines.
- **Mandatory caller name:** every call to the three token endpoints that push something into
  AIfred (`inject`, `trigger`, `announce`) names itself — `sender` (`announce`: `speaker`),
  1–64 characters, **422** without it. The name is **display only** (bubble header, message to
  the model); authentication is the token alone, never the name. One token covers all callers of an
  endpoint — whoever holds it can claim any name.
- Cookie routes answer **403 "Login cookie required"** without a valid, signed
  login cookie. From a script, copy the cookie from a logged-in browser and send
  it with `-b 'aifred_username=…'`. The cookie is signed with
  `AIFRED_SESSION_SECRET` (or a persisted random secret if unset).

The backend listens on all interfaces, so this gate also protects against other
machines in the LAN. Put a reverse proxy with TLS in front of it before you
expose AIfred to the internet (see [Deployment → Access the web UI](deployment.md#access-the-web-ui)).

---

## Endpoints

### Chat & sessions

| Endpoint | Method | Description |
|---|---|---|
| `/api/chat/inject` | POST | Send a message into a session (`sender`, `session_id`, `message`, `token`): the server runs the turn itself, with or without an open tab; with a turn running it waits behind it (`queued: true`). It acts as your own input (right-hand bubble); the header names the sender: `[📥 Inject] <sender>`. Unknown session: 404 |
| `/api/chat/status` | GET | `?session_id=` — `is_generating` (a turn is running), `message_count` |
| `/api/chat/history` | GET | `?session_id=` — `chat_history` + `llm_history` (latest session if omitted) |
| `/api/chat/clear` | POST | Clear a session's history (`session_id`) |
| `/api/sessions` | GET / POST | Your own sessions (`session_id`, `title`, `last_message_at`, `message_count`, `channel`) / create one |
| `/api/sessions/{id}` | GET / DELETE | Open one of your sessions (`chat_history`, `config`, `debug_messages`, `running`, `speech_regenerating`, `last_event_id`) / delete it. A foreign or invalid id answers 404 |
| `/api/sessions/{id}/messages` | POST | `{text, images}` — the server runs the turn itself, also without an open tab; while one runs, the message waits with its images (`queued: true`). `images` = URLs from the image upload, only the session's own uploads |
| `/api/sessions/{id}/images` · `/images/{name}` | POST / DELETE | Upload images for the next message (multipart `files`, checked and made smaller, → `[{name, url, size_kb}]`) / take an uploaded one off again |
| `/api/sessions/{id}/images/{name}/rotate` · `/crop` | POST / POST | Turn an uploaded image by 90° (`{clockwise}`) / cut it (`{x, y, width, height}` in percent of the image, from the top left) → `{name, url, size_kb}` under a new name, the old file is gone; a box over the whole image cuts nothing (same name). 404 for another session's image, 422 for a box outside the image |
| `/api/sessions/{id}/dictation` · `/dictation/{job}/answer` | POST / POST | A recording (`source=mic`) or an audio file (`source=file`) as multipart `file` with `job_id` (a UUID from the client) → 202; the server transcribes it itself, questions and the result come over the event stream (`dictation_question`, `dictation_text`, `dictation_finished`) / `{yes}` answers a question |
| `/api/sessions/{id}/dictation/text` | POST | `{text}` — what the browser's speech recognition heard; the same rule as a dictation of the server → `{sent}` (to the AI) or into the input field |
| `/api/sessions/{id}/messages/speech` | POST | `{timestamp}` — read a finished reply aloud again: what plays now stops; the reading comes over the event stream, and what was spoken then replaces the bubble's audio (`message_updated`; if the service breaks off, the old audio stays) → 204. 409 while speech output is off, 404 without a reply with this timestamp |
| `/api/sessions/{id}/speech/regenerate` | POST | Read every reply of the session into new audio, without playing it: what plays now stops; each bubble comes with its new audio as `message_updated`, the run as `speech_regenerating` (`running`), a failure of the TTS service as `notice` → 204. 409 while speech output is off or while one runs already |
| `/api/sessions/{id}/stop` | POST | Cancel the running turn (the streamed partial answer stays) and end the reading aloud, in every tab |
| `/api/sessions/{id}/config` | PATCH | `active_agent`, `multi_agent_mode`, `symposion_agents` (known agents and modes only), `incognito` (no memory), `debate_seats` (`{critic, judge}` → agent, changes only the seats named; the answer gives the seats in effect, default from `settings.json`) |
| `/api/debate/modes` | GET | The multi-agent modes with the seats a session chooses for them (`[{mode, seats}]`, e.g. tribunal: critic and judge) |
| `/api/sessions/{id}/events` | GET | Event stream (SSE) of the session: `turn_started`, `turn_phase` (what the turn does without text), `agent_started`, `token`, `tool_status`, `message_added`, `message_updated` (bubble with what was spoken), `turn_finished`, `debug`, `notice`, playlist `tts` / `tts_pause` / `tts_speak` / `stop`, `speech_regenerating`, … — `?after=<id>` or `Last-Event-ID` |
| `/api/documents/local` · `/local/folders` · `/local/rename` · `/local/delete` · `/local/preview` | GET / POST / POST / POST / GET | The local documents folder: `?folder=` → `{folder, items, download_prefix}` (rows with `indexed`, `chunks`, `indexable`); new folder `{parent, name}`, rename `{folder, old, new}` (the index follows), delete `{folder, files, folders, from_disk, from_index}` → `{deleted, chunks_removed, failed, listing}`, preview `?path=` → `{name, text}`. 422 for a path outside the folder and an invalid name |
| `/api/documents/local/upload` · `/local/index` · `/api/documents/index` | POST / POST / GET | Upload (multipart: `folder`, `files`; a file of the same name is replaced) → `{saved, rejected, listing, indexing}`, every saved file joins the index queue. Index `{folder, files, folders}` (folders with everything below them the index can read). The server indexes one file after the other (`lib/document_indexing.py`), one that fails does not stop the rest; the progress `{running, current, done, total, indexed, chunks, failed}` is the whole run's, whichever tab started it |
| `/api/documents/sources` · `/api/documents/source/{key}` · `…/upload` · `…/index` · `…/deindex` | GET / GET / POST / POST / POST | Further sources of the enabled plugins (e.g. Nextcloud): list `[{key, label, writable}]`, the local folder first (`local`). Folder `?folder_id=` ("" = root) or search `?query=` → `{source, folder_id, query, writable, items}` (rows with `id`, `indexable`, `indexed`, `chunks`, `download_url`). Upload only into a writable source (else 422) and **without** indexing, index `{files: [{id, name}], folders: [id]}` into the same queue as locally, deindex `{file_ids}` → `{chunks_removed}`. 404 for an unknown source, 502 when the source fails. The download `…/file/{file_id}` takes ids with slashes (Nextcloud's id is the path) |
| `/api/documents/index/overview` · `/index/orphans/remove` · `/index/clear` | GET / POST / POST | Care of the search index: overview `{documents, chunks, orphans}` (orphaned entries of the local folder: `{filename, chunks, indexed}`), take orphaned entries out `{filenames}` → overview (only names that are orphans right now; a file still there keeps its entry), clear the whole index → `{chunks_removed}` (the files stay). 503 when the document store is not available |
| `/api/agents/choices` · `/agents/{id}/choices` | GET / PATCH | The agents' quick choices (a setting of AIfred, not of the session): per agent `model_id` (empty = AIfred's model), `personality`, `reasoning`, `thinking` with `thinking_options` (`value`, `label`), `speed_available`/`speed`; plus `automatik_model_id`, `models` and `vision_models` (with `badge`, `fits`). PATCH changes what it carries (`model_id`, `personality`, `reasoning`, `thinking`, `speed`; `automatik` only `model_id`), the model first, and answers with every row. 404 for an unknown agent, 422 for a value the row does not offer |
| `/api/agents/summary` | GET | The agents a chat can address: no system agent, none switched off (`id`, `display_name`, `emoji`, `role`) |
| `/api/agents` · `/agents/{id}` | GET / PATCH / DELETE | The agents page: every chat agent, the ones switched off too (`id`, `display_name`, `emoji`, `description`, `enabled`, `base`, `delete_refusal`); on/off `{enabled}` (422 for AIfred and system agents), delete 204 or 422 with the reason (AIfred never, system agents never, Sokrates and Salomo once Reflex is removed). Switched off, an agent vanishes from every choice; a turn, job or channel naming it is refused out loud |
| `POST /api/agents` · `/agents/{id}/persona` · `/agents/{id}/prompts/{lang}/{key}` · `/agents/{id}/tools` | POST / GET+PATCH / GET+PUT / GET+PUT | New agent `{id, display_name, emoji, description}` with the base prompts of `prompts/<lang>/agent-template/` (201, 422 for an id taken or invalid). Persona `{display_name, emoji, description, aliases, prompt_keys, languages}`, PATCH changes the fields sent (aliases lowercase, no empty ones). Prompt text per language and layer `{text, exists}`; nothing outside `prompts/<lang>/` (422). Rights `{all, groups}` by plugin as on offer now; PUT `{allowed: [...] | null}`, `null` = every tool, a list keeps what it held for tools of plugins switched off. 404 for system agents |
| `/api/memory` · `/memory/{agent}` · `/memory/{agent}/{id}` | GET / GET+DELETE / PATCH+DELETE | The agents' long-term memory: every agent with its number of memories (also none, and collections of agents deleted since), its entries (newest first; `id`, `date`, `type`, `summary`, `content`, `session_id`), one edited `{summary, content}` through `AgentMemory.update` (summary one line, else 422; type and session stay), one or all deleted. 503 without ChromaDB |
| `/api/jobs` · `/jobs/options` · `/jobs/{id}` · `/jobs/{id}/runs` · `/jobs/preview` | GET+POST / GET / PUT+PATCH+DELETE / GET / POST | Scheduled jobs (`lib/scheduler.py`): list with next and last run; create and change `{name, schedule_type, schedule_expr, message, agent, delivery, channel, recipient, webhook_url, fixed_text, audio_type, max_tier}` under the rules of `scheduler.job_problem` (the same as the agents' tool; only the fields of their delivery, an agent switched off and an expression without a next run give 422); on/off `{enabled}`; the recent runs; `preview {schedule_type, schedule_expr}` → `{next_run}`. `options` lists types, deliveries, audio types, tiers, cron templates, selectable agents and the channel plugins (no fixed list) |
| `/api/speech/list` · `/list/refresh` · `/list/move` · `/list/switch` · `/speech/units/{engine}` | GET / POST / POST / POST / PUT | AIfred's list of speech engines (`lib/tts_settings.py`): rows in speaking order `{index, engine, label, enabled, unit, unavailable, reserved, note, warn, status}` and `service_error` (the TTS-Suite does not answer: the list from the settings); `refresh` reads the catalog fresh; move `{index, delta: -1|1}`, switch `{index, enabled}`, unit `{unit}` — each change saves the whole matched list, 503 without the service, 422 for a position or unit that does not exist |
| `/api/speech/choices` · `/speech/agents?engine=` · `/speech/agents/{id}?engine=` · `/speech/agents/{id}/voices/{engine}` · `/speech/narrator/{engine}` · `/speech/recognition` | GET / GET / PATCH / PUT / GET+PUT / GET | Choices (engines, units, languages, tempo and pitch steps from `config.py`); every chat agent with its switch, language and voice on the engine (`voices`: the service's and the saved ones, ★ first); switch/language `{enabled, language}` (for every engine), voice `{voice, speed, pitch}`; the narrator's voice (only one the engine offers); whether the STT-Suite answers. 404 for no chat agent, 422 for values outside the choices |
| `/api/audio/sources` · `/audio/sources/folders?path=` · `POST /audio/sources` · `DELETE /audio/sources/{label}` · `…/{label}/index` · `PUT /audio/settings` | GET / GET / POST / DELETE / POST+DELETE / PUT | The audio player's sources (`lib/audio_library.py`): `{sources: [{label, type, target, is_symlink, indexed}], picker_root, tts_list_max, indexing, message}`; the folders of one place in the sandbox (422 outside); a folder as a source `{path}` (201, 422 for no folder or one resolving outside); remove (404 for an unknown name, the symlink's target stays); read `{force}` (202, 409 while one runs) or empty the index; the sandbox `{picker_root}` (an absolute folder) and `{tts_list_max}` (≥ 1), else 422 |
| `/api/vision/live` · `/vision/cameras/rescan` · `/vision/cameras/{id}/resolutions` · `/vision/cameras/{id}` | GET+PATCH / POST / GET / PATCH | The vision page's live view (`lib/vision_cameras.py`): one camera list for every interface `{id, label, alias, hardware_name, kind, available, ptz, auto_start, resolution, prompt_context, motion_min_area_ratio, alerts_enabled, alert_types, quiet_enabled, quiet_start, quiet_end, schedule_enabled, schedule_start, schedule_end, min_event_interval_sec}` (the name always `VisionStore.source_label`), the cameras shown and the view's settings (`fps`, `teleprompter_mode`, `vlm_cooldown_sec`, `face_throttle_sec`, `visible_sources`; 422 outside their range), the frame rate and PTZ speed choices, `ptz_heartbeat_ms`; look for cameras again; a camera's resolutions (a probe of the device, the driver's default first); a camera's name, resolution (only an offered one, else 422), briefing, background switch, alerts with their types, quiet hours (hours), active window (`HH:MM`) and event interval; the background switch and the window take effect at once (`vision_autostart.apply_background_watch`). Pictures via `/vision/stream` and `/vision/snapshot` |
| `/api/vision/ptz/{id}/press` · `…/keepalive` · `…/release` · `…/home` · `/api/vision/photo/{id}` | POST | The head of a PTZ camera: move `{direction: left\|right\|up\|down\|zoom_in\|zoom_out, speed}` while the key is held (a sign of life every `ptz_heartbeat_ms`, without one the server stops after `VISION_PTZ_DEADMAN_SEC`), release, guard position; 409 for a camera without PTZ. A photo in full resolution → `{url}`, 502 when it fails |
| `/api/vision/settings` · `/vision/armed` · `/vision/model` | GET+PATCH / PUT / GET+PUT | The vision page's settings (`lib/vigilantia.py`): the eye `{armed}` (armed starts the background watchers of every camera with its background switch on inside its window, disarmed stops them; 409 with the vision plugin off), the mode (`live` loads the image model at once), the image model (a new one unloads the old Ollama model), face and person detection, retention (days), with their choices (`modes`, `models`, `alert_types`, `event_intervals`); 422 for a mode or retention the eye does not take. Image model loaded? `{loaded}` as the backend reports it; PUT loads (also in on-demand mode) or unloads, 409 without a model chosen |
| `/api/vision/alert-rules` · `/vision/alert-rules/{category}` | GET / PATCH | The alert rules per category (`alert_bus.VISION_RULE_CATEGORIES`: person, vehicle, animal, face_known, face_unsure, face_unknown): `{channels, rules: [{category, label, sinks, vlm}]}`. PATCH `{sink, sink_on}` switches a channel, `{vlm}` the description by the image model; the dispatcher reads the rules again at once, other producers' rules stay. 422 for an unknown category or channel and for `sink` without `sink_on` |
| `/api/vision/rtsp-cameras` · `/vision/rtsp-cameras/{name}` | GET+POST / PUT+DELETE | RTSP cameras (`lib/rtsp_cameras.py`, in `rtsp_cameras` of the vision settings): each with its `source_id` and `user_set`/`password_set`; user and password never leave the server (they are `RTSP_<CRED>_USER/_PASSWORD` in `.env`). POST adds one (its name must be new), PUT changes the connection: only the fields sent, every other field of the entry stays (`ptz`, `onvif_port`, `snap_channel` …), a field sent as `""` leaves it, empty credentials keep the stored ones; the name stays (the base of the source id). DELETE takes the camera out, its credentials only when no other camera uses them; its events stay. 404 / 422 |
| `/api/vision/describers` · `/vision/describers/move` · `…/switch` · `…/burn-in` | GET / POST / POST / POST | The describer list (`lib/describer_list.py`): who describes images, in which order, `{rows: [{index, source, label, enabled, note, warn, remote}], measuring}` (`source` = `<machine>/<model>`, `local` or `main`; every configured machine is asked). Move `{index, delta: -1|1}`, switch `{index, enabled}`, 422 for a position the list no longer has. `burn-in {source}` starts the burn-in of a describer of another machine (it unloads every model there and loads this one with the stress picture) and answers at once with 202; GET follows it (`measuring`, then the row's note). 409 while one runs (one at a time), 422 for a source of no other machine |
| `/api/vision/casus/events` · `/vision/casus/choices` · `/vision/casus/events/{id}/film` · `/vision/casus/stream` · `PUT …/events/{id}/face` · `DELETE …/events/{id}` · `POST …/events/delete` | GET / GET / GET / GET (SSE) / PUT / DELETE / POST | The vision's events (`lib/casus.py`): a page newest first `?source&type&face&grouped&offset&limit` → `{events, total, offset, page_size}` (`type` from `TYPE_FILTERS`, `face` = `all`, `unknown` = confirmed by no person, or the id of a person; `grouped` = one row per occurrence with `cluster_member_count`; `limit=1` = the picture at one position, for the slideshow over every page; 422 for an unknown filter). The filters' choices (sources with events, types, persons, `taggable_types`). The film of an occurrence, oldest first `[{id, has_zoom}]`. Stream: `stored` with `{latest}` at the start and after every stored event. Assign a person `{face_id}` (null takes it back; 422 without a face or for an unknown person), delete one event (with its pictures; 404), every event of a filter `{source, type, face}` → `{deleted}`. Pictures via `/vision/frame?id=&w=&zoom=` |
| `/api/vision/casus/runs` · `/vision/casus/events/{id}/analyze` · `/vision/casus/describe-all` | GET / POST / POST+DELETE | The image model on events (`lib/casus.py`), runs of the server, one at a time each: `{analysis: {running, last, error}, describe_all: {running, cancelling, events, clusters, processed, failed, outcome, message}}` (`outcome` after a run: `done`, `cancelled`, `nothing`, `busy`, `no_room`, `error`; `message` = the state in words). Analyse one event → 202 (409 while one runs, 404, 422 without a picture); describe every event without a description → 202 (409), DELETE stops after the occurrence it describes now |
| `/api/vision/casus/recent` | GET | The vision's badge in the chat (`casus.recent`): `{plugin_enabled, armed, window_minutes, recent, events, refresh_seconds}` — the eye, the events of the last minutes counted, the newest events, how often to ask again |
| `/api/vision/face/list` · `/{id}/rename` · `/{id}` · `/{id}/embeddings` · `/embedding/{id}` · `/open` · `/open/dismiss` · `/open/{card}/assign` · `/rematch` | GET / POST / DELETE / GET / DELETE / GET / POST / POST / GET+POST | The vision's persons (`lib/personarium.py`): every person `{id, name, embedding_count, last_seen, last_seen_display, crop_url}`; rename (404, 409 for a name taken; the recognition reloads), delete with its embeddings (`message` in the page's language), embeddings newest first `{id, quality, created_at, crop_url}`, delete one. The open faces, one card per crop file `{id, member_ids, crop_url, event_type, matched_name, …}`, without a cap; dismiss `{cards}` (404 for a card no longer open), assign and learn `{face_id}` or `{name}` (422 for both/neither, an unknown person, no face in the picture). Recognise again: POST starts it (202, 409 while it runs), GET `{running, message}` |
| `POST /api/vision/enroll` · `GET /vision/enroll/preview/{source}` · `POST …/enroll/{token}/capture` · `…/skip` · `DELETE …/captures/last` · `GET …/captures/{i}` · `POST …/finish` · `DELETE /vision/enroll/{token}` | POST / GET / POST / POST / DELETE / GET / POST / DELETE | Learn a person from several poses (`lib/enrollment.py`), one session in the server at a time: start `{face_id}` (0 = a new person) → `{token, face_id, name, poses, step, captures: [{pose, url, score}], preview_interval_ms}`; the camera's freshest picture (JPEG, tele lens where it has one); capture `{source_id}` (422 without a face, 404 for an unknown camera), skip, take the last back, a capture's crop (JPEG); save `{name}` → `{face_id, message}` (422 without a capture or a name); cancel. 409 for a session no longer current. The embedding never leaves the server |
| `/api/auth/login` · `/me` · `/logout` | POST / GET / POST | Login of the Vue UI (sets the same login cookie as the Reflex login) |
| `/api/session/config` | POST | Per-session config: `active_agent`, `multi_agent_mode`, `symposion_agents` |

### Settings, models, system

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check with backend type/status |
| `/api/settings` | GET / PATCH | General settings: `ui_language`, `user_name`, `user_gender`, `enable_tts`, `tts_autoplay`, `dictation_engine`, `show_transcription`, `max_debate_rounds`, `consensus_type`; GET also `backend_type` and the range of debate rounds (`debate_rounds_min`/`_max`). PATCH only changes the fields you send, an unknown field or value answers 422. The agents' models and switches: `/api/agents/choices` |
| `/api/models` | GET | Available models of the active backend + vision-capable ones |
| `/api/backend` | GET / PUT | Backend (`lib/backend_overview.py`, `lib/backend_switch.py`): `{backend_type, backends: [{id, label}], cloud_provider, cloud_providers: [{id, label, key_configured}], gpu: {cards: [{name, vram_mb, compute_capability}], total_vram_gb, unsupported_backends}}` (`backends` = the ones the cards can run, and the cloud; `gpu` null without a card). PUT `{backend_type}` switches: the old one frees its models (llama-swap stops when the new one is none of its family), the new one's saved models are checked against its catalog; answers when done. 422 for a backend the cards cannot run, 409 during a switch or a calibration |
| `/api/backend/cloud-provider` | PUT | `{provider}`: the cloud's provider; while the cloud is the backend its models follow (AIfred gets the provider's first unless it offers his). 422 for an unknown one |
| `/api/calibration` | GET | Calibration (`lib/calibration/`): `{backend_type, model_id, matrix, models}` — `models` per model of the agents `{model_id, agents, calibration: {context, cache_context, split, speed, rope_contexts}}`, `matrix` (llama.cpp, vLLM; else null) the variants of AIfred's model `{columns: [{tts_key, label}], rows: [{vlm_key, label, cells: [{key, status: done/failed/open}]}], tts_problem}` |
| `/api/calibration/run` · `/calibration/run/cancel` | GET+POST / POST | Calibration run of AIfred's model (`lib/calibration/service.py`), one per process: POST `{cells: ["<vlm>|<tts>"], vllm_resweep, vllm_dry_run}` → 202, runs in the background for minutes to hours, stops llama-swap and takes every card; a ticked cell with a failure record is tried again. 409 during a run, 422 without or with an unknown cell, 404 without the vLLM checkpoint. GET `?after=` → `{active, model_id, backend_type, started_at, running, outcome: done/cancelled/failed, lines, next}` (the console from line `after` on). Cancel: the run ends at its next step |
| `/api/calibration/options` · `/calibration/vlm-cache` | GET+PATCH / DELETE | Options of every run: `{mode: legacy/ai, allow_hybrid, ai_model, ai_available}` (PATCH `{mode?, allow_hybrid?}`, 422 for another mode); DELETE drops the measured VLM peaks → `{cleared}` |
| `/api/system/restart-aifred` | POST | Restart the AIfred service |
| `/api/system/restart-backend` | POST | Restart the backend's service (Ollama or llama-swap) |
| `/api/system/reset-defaults` | POST | Reset settings to defaults |

### Agents, webhook, OAuth

| Endpoint | Method | Description |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: run an agent headless (`sender`, `message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`). The job shows on the left as `🪝 Webhook · <sender>`; webhook and scheduler are always owner-level triggers, `max_tier` limits the rights |
| `/api/agents/export` | GET | Agents as a ZIP bundle (`?ids=a,b`: configuration, prompts, sampling, voices) |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Preview `{format_version, exported_at (Unix time), agents: [{agent_id, display_name, description, exists_locally}]}` / import (multipart: `file`, `ids`, `conflict` = rename\|overwrite\|abort) → `{success, agent_ids, warnings}`; voices go into the TTS-Suite's voices folder, an existing one is never overwritten |
| `/api/oauth/{provider}/auth-url` · `/status` · `/callback` | GET | OAuth flow (Google) — see [OAuth](plugins/oauth.md) |
| `/api/oauth/{provider}` | DELETE | Revoke a provider's tokens |

### Announcements on the Echo puck (no LLM)

| Endpoint | Method | Description |
|---|---|---|
| `/api/audio/announce` | POST | Read a ready-made text aloud in a room. Body: `room` (room name, `@group` or `*`), `text` OR `texts` (several paragraphs as ONE announcement, optional `pause_ms`), `speaker` (required: the speaker's name). 404 for an unknown room, 502 if it cannot be delivered. No length limit |
| `/api/audio/announce/rooms` | GET | The rooms connected right now (valid values of `room`) |

The announcement appears in the room's session as `🎤 FreeEcho.2 · <speaker>`; the model sees it as
"message from <speaker>".

### Calls made by AIfred

Besides answering the endpoints above, AIfred itself calls one kind of address only: the result of a
scheduler or webhook job with `delivery: webhook`. It arrives as a `POST` to `webhook_url` (30 s
timeout, no redirects, private/local addresses are refused):

```json
{"job_name": "Daily prayer", "job_id": 7, "result": "…", "timestamp": "2026-10-07T06:45:00"}
```

### Media (used by the UI)

| Prefix | Content |
|---|---|
| `/api/vision/*` | snapshots, MJPEG streams, event frames, face enrollment/list/rename, zone masks, zone editor |
| `/api/audio/*` | audio files, test tone, playback position |
| `/api/browser/stream/{session_id}` | server→browser push bus (see [Browser Push Bus](../../de/architecture/browser-push-bus.md), German) |

---

## Global vs. per-session

`/api/settings` and `/api/agents/choices` hold what is truly global (language, name,
speech output, the agents' models and switches). Everything that belongs to one conversation — agent,
discussion mode, symposion participants — goes through
`/api/session/config` and is stored in the session file
(`data/sessions/<session_id>.json`) as the single source of truth:

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

Every new session starts clean with `aifred + standard + automatik` — it never
inherits from the previous one.

**Sync:** all writers (browser tabs, API, e-mail channel, voice terminal) change
the session file; every tab showing that session notices the new mtime within
about a second and reloads. Injected messages start their inference in the
browser within about two seconds. Global settings changes (models, voices,
temperature) show up live in the UI.

---

## Examples

```bash
API=http://localhost:8002/api
COOKIE='aifred_username=<value from your browser>'

# Find a session id (or take it from data/sessions/)
curl -s -b "$COOKIE" $API/sessions

# Send a message — the server runs the full pipeline, with or without an open tab
curl -s -X POST $API/chat/inject \
  -H "Content-Type: application/json" \
  -d '{"sender": "my-script", "session_id": "<session_id>", "message": "What is Python?", "token": "<INJECT_API_TOKEN>"}'

# Wait until the answer is done, then read it
curl -s -b "$COOKIE" "$API/chat/status?session_id=<session_id>"
curl -s -b "$COOKIE" "$API/chat/history?session_id=<session_id>"

# Read a text aloud on the Echo puck (Bearer token, speaker is mandatory)
curl -s -X POST $API/audio/announce \
  -H "Authorization: Bearer <ANNOUNCE_API_TOKEN>" -H "Content-Type: application/json" \
  -d '{"room": "Office", "text": "The commit is done.", "speaker": "Codine"}'

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

## Use cases

- Operate AIfred from another device or over HTTPS
- Let other systems (Home Assistant, Node-RED, scripts) send questions via the
  inject or webhook endpoint
- Batch queries, custom apps, remote tests on headless systems
