# REST API

> **Deutsche Version:** [rest-api.md](../../de/guides/rest-api.md)

AIfred's backend exposes a REST API under `/api` (FastAPI, port `8002`). It is a
**remote control for the browser**: it does not run inference itself, but
queues messages into a browser session, which then runs the full pipeline —
intent detection, multi-agent modes, research, vision, history compression.
One code path, all features, and you can watch the answer stream in the
browser while your script waits.

```
API request ──→ set_pending_message() ──→ browser polls (1 s)
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
| `/api/oauth/{provider}/callback` | none (arrives as provider redirect) |
| **everything else** (incl. `/api/docs`) | login cookie `aifred_username`, as set by the web login |

- Token endpoints answer **503** if the token is not configured and **403** if it
  is wrong. They are the intended entry points for scripts and other machines.
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
| `/api/chat/inject` | POST | Queue a message into a browser session (`session_id`, `message`, `token`) |
| `/api/chat/status` | GET | `?session_id=` — `is_generating`, `message_count` |
| `/api/chat/history` | GET | `?session_id=` — `chat_history` + `llm_history` (latest session if omitted) |
| `/api/chat/clear` | POST | Clear a session's history (`session_id`) |
| `/api/sessions` | GET | List sessions (`session_id`, `last_seen`, `message_count`) |
| `/api/session/config` | POST | Per-session config: `active_agent`, `multi_agent_mode`, `symposion_agents`, `research_mode` |

### Settings, models, system

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check with backend type/status |
| `/api/settings` | GET / PATCH | Global settings (backend, models per role, TTS, language, …) — PATCH only changes the fields you send |
| `/api/models` | GET | Available models of the active backend + vision-capable ones |
| `/api/system/restart-aifred` | POST | Restart the AIfred service |
| `/api/system/restart-ollama` | POST | Restart Ollama |
| `/api/system/reset-defaults` | POST | Reset settings to defaults |

### Agents, webhook, OAuth

| Endpoint | Method | Description |
|---|---|---|
| `/api/agent/trigger` | POST | Webhook: run an agent headless (`message`, `agent`, `token`, `max_tier`, `delivery`: `review` / `announce` / `webhook`) |
| `/api/agents/export` | GET | Export agent definitions |
| `/api/agents/import/peek` · `/api/agents/import` | POST | Preview / import an agent export (multipart upload) |
| `/api/oauth/{provider}/auth-url` · `/status` · `/callback` | GET | OAuth flow (Google) — see [OAuth](plugins/oauth.md) |
| `/api/oauth/{provider}` | DELETE | Revoke a provider's tokens |

### Media (used by the UI)

| Prefix | Content |
|---|---|
| `/api/vision/*` | snapshots, MJPEG streams, event frames, face enrollment/list/rename, zone masks, zone editor |
| `/api/audio/*` | audio files, test tone, playback position |
| `/api/browser/queue/{session_id}` · `/api/browser/stream/{session_id}` | server→browser push bus (see [Browser Push Bus](../../de/architecture/browser-push-bus.md), German) |

---

## Global vs. per-session

`/api/settings` holds what is truly global (backend, models, TTS voices,
language, sampling). Everything that belongs to one conversation — agent,
discussion mode, research mode, symposion participants — goes through
`/api/session/config` and is stored in the session file
(`data/sessions/<session_id>.json`) as the single source of truth:

```json
{
  "data": {
    "config": {
      "active_agent": "aifred",
      "multi_agent_mode": "standard",
      "symposion_agents": [],
      "research_mode": "automatik"
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
  -d '{"session_id": "<session_id>", "message": "What is Python?", "token": "<INJECT_API_TOKEN>"}'

# Wait until the answer is done, then read it
curl -s -b "$COOKIE" "$API/chat/status?session_id=<session_id>"
curl -s -b "$COOKIE" "$API/chat/history?session_id=<session_id>"

# Switch this session to Tribunal with deep research
curl -s -b "$COOKIE" -X POST $API/session/config \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "multi_agent_mode": "tribunal", "research_mode": "deep"}'

# Change AIfred's model (global)
curl -s -b "$COOKIE" -X PATCH $API/settings \
  -H "Content-Type: application/json" \
  -d '{"aifred_model": "<model id from /api/models>"}'
```

The browser has to be open and polling for injected messages to be picked up.

---

## Use cases

- Operate AIfred from another device or over HTTPS
- Let other systems (Home Assistant, Node-RED, scripts) send questions via the
  inject or webhook endpoint
- Batch queries, custom apps, remote tests on headless systems
