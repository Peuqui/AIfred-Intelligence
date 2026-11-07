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
| `/api/chat/inject` | POST | Queue a message into a browser session (`sender`, `session_id`, `message`, `token`). It acts as your own input (right-hand bubble); the header names the sender: `[📥 Inject] <sender>` |
| `/api/chat/status` | GET | `?session_id=` — `is_generating`, `message_count` |
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
| `/api/sessions/{id}/events` | GET | Event stream (SSE) of the session: `turn_started`, `agent_started`, `token`, `tool_status`, `message_added`, `message_updated` (bubble with what was spoken), `turn_finished`, `debug`, `notice`, playlist `tts` / `tts_pause` / `tts_speak` / `stop`, `speech_regenerating`, … — `?after=<id>` or `Last-Event-ID` |
| `/api/documents/local` · `/local/folders` · `/local/rename` · `/local/delete` · `/local/preview` | GET / POST / POST / POST / GET | The local documents folder: `?folder=` → `{folder, items, download_prefix}` (rows with `indexed`, `chunks`); new folder `{parent, name}`, rename `{folder, old, new}` (the index follows), delete `{folder, files, folders, from_disk, from_index}` → `{deleted, chunks_removed, failed, listing}`, preview `?path=` → `{name, text}`. 422 for a path outside the folder and an invalid name |
| `/api/agents/choices` · `/agents/{id}/choices` | GET / PATCH | The agents' quick choices (a setting of AIfred, not of the session): per agent `model_id` (empty = AIfred's model), `personality`, `reasoning`, `thinking` with `thinking_options` (`value`, `label`), `speed_available`/`speed`; plus `automatik_model_id`, `models` and `vision_models` (with `badge`, `fits`). PATCH changes what it carries (`model_id`, `personality`, `reasoning`, `thinking`, `speed`; `automatik` only `model_id`), the model first, and answers with every row. 404 for an unknown agent, 422 for a value the row does not offer |
| `/api/agents/summary` | GET | The agents a chat can address (`id`, `display_name`, `emoji`, `role`) |
| `/api/auth/login` · `/me` · `/logout` | POST / GET / POST | Login of the Vue UI (sets the same login cookie as the Reflex login) |
| `/api/session/config` | POST | Per-session config: `active_agent`, `multi_agent_mode`, `symposion_agents` |

### Settings, models, system

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check with backend type/status |
| `/api/settings` | GET / PATCH | General settings: `ui_language`, `user_name`, `user_gender`, `enable_tts`, `tts_autoplay`, `dictation_engine`, `show_transcription`, `temperature_mode`, `max_debate_rounds`, `consensus_type`; GET also `backend_type` and the range of debate rounds (`debate_rounds_min`/`_max`). PATCH only changes the fields you send, an unknown field or value answers 422. The agents' models and switches: `/api/agents/choices` |
| `/api/models` | GET | Available models of the active backend + vision-capable ones |
| `/api/system/restart-aifred` | POST | Restart the AIfred service |
| `/api/system/restart-ollama` | POST | Restart Ollama |
| `/api/system/reset-defaults` | POST | Reset settings to defaults |

### Agents, webhook, OAuth

| Endpoint | Method | Description |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: run an agent headless (`sender`, `message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`). The job shows on the left as `🪝 Webhook · <sender>`; webhook and scheduler are always owner-level triggers, `max_tier` limits the rights |
| `/api/agents/export` | GET | Export agent definitions |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Preview / import an agent export (multipart upload) |
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

# Queue a message — the browser session runs the full pipeline
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

The browser has to be open and polling for injected messages to be picked up.

---

## Use cases

- Operate AIfred from another device or over HTTPS
- Let other systems (Home Assistant, Node-RED, scripts) send questions via the
  inject or webhook endpoint
- Batch queries, custom apps, remote tests on headless systems
