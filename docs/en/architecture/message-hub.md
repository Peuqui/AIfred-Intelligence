# Message Hub — Architecture & Implementation Plan

> **Deutsche Version:** [message-hub.md](../../de/architecture/message-hub.md)

**Date:** 2026-03-28
**Status:** Packages 1-5 implemented — e-mail channel ready for testing

---

## Concept

AIfred becomes the central dispatcher for all communication channels.
Each channel (e-mail, Discord, Telegram, Signal) is a plugin with its own identity.
AIfred is an independent participant — it does NOT monitor the user's channels,
but has addresses of its own (its own e-mail account, its own Discord bot, etc.).

---

## Architecture Overview

```
                        ┌──────────────────────────┐
                        │      Message Hub          │
                        │   (Background Workers)    │
                        └────────────┬─────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼────────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
    │  IMAP Listener   │  │  Discord Bot     │  │  Telegram Bot    │
    │  (IMAP IDLE)     │  │  (WebSocket)     │  │  (Bot API)       │
    └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  InboundMessage      │
                          │  (Envelope)          │
                          │  - channel           │
                          │  - channel_id        │
                          │  - sender            │
                          │  - text              │
                          │  - target_agent      │
                          │  - metadata          │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Routing Table     │
                          │   (SQLite)          │
                          │   channel+id → session │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   AIfred Engine     │
                          │   (or Sokrates/     │
                          │    Salomo depending │
                          │    on target_agent) │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Outbound Reply    │
                          │   (back through     │
                          │    the same channel)│
                          └─────────────────────┘
```

---

## Envelope Normalization (InboundMessage)

Every incoming message is normalized into a unified format.
The AIfred engine never sees channel-specific details — it takes text in
and gives text out. The channel plugins take care of the rest.

```python
@dataclass
class InboundMessage:
    channel: str            # "email", "discord", "telegram", "freeecho2", "scheduler"
    channel_id: str         # thread ID, channel ID, conversation ID
    sender: str             # e-mail address, Discord user, Telegram user
    text: str               # the actual message content
    timestamp: datetime
    metadata: dict          # channel-specific (subject, attachments, etc.)
    target_agent: str = "aifred"  # which agent should answer?

@dataclass
class OutboundMessage:
    channel: str            # back through the same channel
    channel_id: str         # to the same thread/channel
    recipient: str          # to the same sender
    text: str               # reply text
    media: str | None = None  # optional image (local path or URL) to attach
    metadata: dict          # channel-specific (subject for e-mail, etc.)
```

---

## Routing Table (SQLite)

A simple mapping: which conversation on which channel belongs to which AIfred session.

```sql
CREATE TABLE routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,           -- "email", "discord", etc.
    channel_id TEXT NOT NULL,        -- thread ID, channel ID
    session_id TEXT NOT NULL,        -- AIfred session ID
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, channel_id)
);
```

- New message → look up the route → session found? Forward it.
- No route? → Create a new session, store the route.
- Session deleted? → Delete the route. On the next message: new session.

---

## Agent Routing

When a message is addressed to a specific agent, that agent answers.
There is no function per agent: `process_inbound()` sets
`message.target_agent`, and `_call_engine(agent=...)` passes it on to
`call_llm(agent=...)` — this works for AIfred, Sokrates, Salomo and every
custom agent from the agent configuration.

Target-agent detection is LLM-based: `detect_target_agent_via_llm()` in
`message_processor.py` wraps `detect_query_intent_and_addressee()` (the same
detection as the browser) and returns `(agent_id, intent, detected_language,
mode_switch_updates)`. Routing priority (highest wins):

1. `metadata["wake_agent"]` — internal triggers and channels hard-pin the
   agent this way (e.g. the scheduler, see `scheduler.py`, or the FreeEcho.2
   wake word). Intent detection still runs, but its addressee is overridden
   (only if the wake agent is configured).
2. The addressee the LLM detected in the current message
3. The session's `active_agent` (sticky from the previous turn)
4. Default `"aifred"`

---

## Auto-Reply Toggle

- Default: **OFF** (`auto_reply` in `settings.json` → `channel_toggles`)
- Toggle per channel in the Plugins tab of the settings page (see
  [Activation](#activation)). It is only shown for channels without
  `always_reply` — currently e-mail. Discord, Telegram and FreeEcho.2 declare
  `always_reply = True` and always reply (no toggle).
- When ON: AIfred replies immediately and automatically via the channel
  (`plugin.send_reply()`)
- When OFF: the reply is generated and stored in the session, but not sent

---

## User Assignment

AIfred is a **single-owner system** as far as the Message Hub is concerned:
- All incoming messages land in sessions belonging to the **operator** (owner)
- AIfred monitors the owner's mailbox, not the mailboxes of other users
- If somebody sends the owner an e-mail, it is a message to the owner
- Multi-user routing (separate sessions per user) is a topic for the future

AIfred does have a user system (accounts.json, whitelist, session-owner binding),
but for the Message Hub only the primary user matters for now.

---

## Allowlist / Security

- Configurable per channel: who is allowed to contact AIfred?
  - E-mail: `EMAIL_ALLOWED_SENDERS` — addresses or `@domain`; empty = nobody,
    `*` = everyone
  - Telegram/Discord: numeric user IDs; empty = nobody, `*` is blocked
- Unknown senders are ignored (only logged, no reply)
- Extensible later: a pairing mechanism like OpenClaw

---

## Implementation Packages

### Package 1: Background worker infrastructure + envelope ✅
- [x] `aifred/lib/message_hub.py` — worker management (register, start, stop)
- [x] `aifred/lib/envelope.py` — InboundMessage / OutboundMessage dataclasses
- [x] Lifecycle: start with the app, stop on shutdown (Reflex lifespan)
- [x] Logging into the existing debug log

### Package 2: Routing table ✅
- [x] `aifred/lib/routing_table.py` — SQLite-based
- [x] CRUD: get_route(), set_route(), delete_route(), delete_routes_for_session()
- [x] Auto-cleanup when a session is deleted (`session_storage.py` calls
  `delete_routes_for_session()`)

### Package 3: IMAP IDLE listener ✅
- [x] `aifred/plugins/channels/email_channel/` — IMAP IDLE for push notifications
- [x] Detect incoming mail (In-Reply-To header for thread matching)
- [x] UID-based detection of new mail
- [x] Auto-reconnect on connection errors
- [x] Registered with the Message Hub as a worker
- [x] Allowlist check (who is allowed to mail?) — `EMAIL_ALLOWED_SENDERS`

### Package 4: Processing pipeline + auto-reply ✅
- [x] `aifred/lib/message_processor.py` — bridge between hub and engine
- [x] Incoming message → routing table → create/find session
- [x] Call the AIfred engine directly (call_llm)
- [x] Send the reply via SMTP (when auto-reply is ON)
- [x] Update the session with the conversation (update_chat_data)
- [x] Agent routing (Sokrates/Salomo when addressed in the text)
- [x] Config: MESSAGE_HUB_OWNER (auto-reply later moved to `channel_toggles`)

### Package 5: Settings & UI ✅
- [x] Toggles persisted in `settings.json` → `channel_toggles` (`monitor`,
  `listener`, `auto_reply` per channel) instead of the originally planned
  config keys MESSAGE_HUB_ENABLED, EMAIL_MONITOR_ENABLED, AUTO_REPLY_*
- [x] "Channels" section in the Plugins tab of the settings page
  (`aifred/ui/agent_editor/plugins.py`)
- [x] Toggle per channel: plugin on/off, monitor on/off, auto-reply on/off
- [x] Allowlist configuration (credentials page via the gear icon)

### Package 6: Additional channels
- [x] Discord bot plugin (`plugins/channels/discord_channel/`)
- [x] Telegram bot plugin (`plugins/channels/telegram_channel/`)
- [x] FreeEcho.2 voice channel (`plugins/channels/freeecho2_channel/`)
- [x] Auto-discovery + registration via `message_hub.py: register_channel_workers()`
- [ ] Cross-channel routing

---

## Design Principles

- Envelope normalization (InboundMessage/OutboundMessage)
- Own identity per channel (no reading along in user accounts)
- Allowlist/security as a first-class feature
- Mention gating in groups (Discord: only react to @AIfred)

---

## Setup & Workflow

### Prerequisites

1. **E-mail credentials** are read from environment variables. Usually
   you enter them on the credentials page (gear icon in the e-mail row, see
   [Activation](#activation)): saving writes the password and
   `EMAIL_ENABLED=true` to `.env`, the other fields to
   `aifred/plugins/channels/email_channel/settings.json` (loaded into the
   environment at boot, takes priority over `.env`). Setting them directly in
   `.env` works as well:
   ```
   EMAIL_ENABLED=true
   EMAIL_IMAP_HOST=imap.example.com
   EMAIL_IMAP_PORT=993
   EMAIL_SMTP_HOST=smtp.example.com
   EMAIL_SMTP_PORT=587
   EMAIL_USER=aifred@example.com
   EMAIL_PASSWORD=secret
   EMAIL_FROM=aifred@example.com
   EMAIL_ALLOWED_SENDERS=you@example.com, @family.example
   EMAIL_SENT_FOLDER=Gesendet   # optional, this is the default
   ```
   Without `EMAIL_ALLOWED_SENDERS` every mail is blocked (empty = nobody).

2. **Message Hub owner** (optional, default: `mp`):
   ```
   MESSAGE_HUB_OWNER=mp
   ```
   Sessions created by the hub belong to this user.

### Activation

1. Start AIfred (or restart it if you edited `.env` by hand)
2. In the web UI: menu icon (☰, top right) → **Settings** → tab **Plugins**
   → section **Channels** → row **Email**: switch ON (if the plugin is not
   configured yet, the credentials page opens)
3. Below it: **Monitor: ON** (starts the IMAP IDLE listener)
4. Optional: **Auto-Reply: ON** (appears only once Monitor is ON; AIfred answers automatically by e-mail)

### Flow: Incoming E-Mail

```
New e-mail in the mailbox
  │
  ▼
IMAP IDLE listener detects the new UID
  │
  ▼
E-mail is fetched → InboundMessage (envelope)
  ├── channel: "email"
  ├── channel_id: Message-ID / In-Reply-To (thread)
  ├── sender: sender address
  ├── text: body (max 10,000 characters)
  └── metadata: subject, Message-ID, references
  │
  ▼
Sender gate: allowlist (EMAIL_ALLOWED_SENDERS), SPF/DKIM/DMARC "fail",
auto-generated mail (RFC 3834) → dropped, only logged
  │
  ▼
Routing table (SQLite): thread known?
  ├── YES → load the existing session
  └── NO  → create a new session (owner = MESSAGE_HUB_OWNER)
  │
  ▼
Agent routing: "Sokrates, ..." → target_agent = "sokrates"
  │
  ▼
AIfred engine: call_llm()
  ├── model + backend from settings.json
  ├── temperature, thinking mode etc. from the settings
  └── agent according to target_agent
  │
  ▼
Update the session
  ├── chat history: "[Email] sender — subject" + body
  │   (channel name in the UI language, build_user_chat_content())
  └── LLM history: user text + AIfred's reply
  │
  ▼
Auto-reply active?
  ├── YES → send via SMTP (Re: subject, In-Reply-To header)
  └── NO  → visible in the session only
```

### Flow: Toggling the Monitor at Runtime

The e-mail monitor can be switched on/off from the UI **without a restart**:

- **Switching on:** the worker is registered + an asyncio task is started
- **Switching off:** the worker is deregistered + the task is cancelled
- **Persistence:** the setting is stored in `settings.json` and
  automatically active again on the next app start

### Database

The routing table lives in `data/message_hub/routing.db` (SQLite).
It is created automatically on first access.

---

## Module Overview

| Module | File | Purpose |
|--------|------|---------|
| Envelope | `aifred/lib/envelope.py` | InboundMessage / OutboundMessage dataclasses |
| Message Hub | `aifred/lib/message_hub.py` | worker lifecycle (register, start, stop) |
| Routing Table | `aifred/lib/routing_table.py` | SQLite: (channel, channel_id) → session_id |
| IMAP Listener | `aifred/plugins/channels/email_channel/__init__.py` | IMAP IDLE, detects new mail (IMAP/SMTP helpers in `client.py`) |
| Processor | `aifred/lib/message_processor.py` | session management, engine call, auto-reply |
| Lifespan | `aifred/aifred.py` | startup/shutdown hook + worker registration |
| Settings | `aifred/state/_settings_mixin.py` | UI toggles + persistence |
| UI | `aifred/ui/agent_editor/plugins.py` | channel toggles (listener, auto-reply) in the Plugins tab |
| Config | `aifred/lib/config.py` | MESSAGE_HUB_OWNER (auto-reply comes from `channel_toggles` in `settings.json`) |
| i18n | `aifred/lib/i18n/` | translations (DE/EN, `de.json` / `en.json`) |

---

## Dependencies

No new system dependencies for packages 1-5.
- `sqlite3` — Python standard library
- `imaplib` — Python standard library
- `smtplib` — Python standard library (already in use)
- `asyncio` — Python standard library

For later packages:
- `discord.py` — Discord bot (pip install)
- `python-telegram-bot` — Telegram bot (pip install)
- `signal-cli-rest-api` — Signal (Docker container)
