# Email Channel Plugin

**File:** `aifred/plugins/channels/email_channel/`

Channel plugin for email communication via IMAP IDLE and SMTP.

## Tools (for the LLM)

| Tool | Description | Tier |
|------|------------|------|
| `email` | Fetch, read, search, send, move, delete, and mark emails | COMMUNICATE |

The `email` tool dispatches by an `action` parameter:
`check`, `read`, `search`, `delete`, `send`, `move`, `list_folders`, `create_folder`, `mark`.

| Action | Required parameters | Notes |
|--------|--------------------|-------|
| `check` | – | `n` (default 10, capped at 20), `folder` (default INBOX) |
| `read` | `msg_id` | `folder` (default INBOX) |
| `search` | `query` | `folder` (default INBOX) |
| `send` | `to`, `subject`, `body` | registers the session route |
| `move` | `msg_id`, `target_folder` | `folder` = source (default INBOX) |
| `delete` | `msg_id` | `folder` (default INBOX) |
| `mark` | `msg_id`, `flag` | `flag` ∈ `read` / `unread` / `flagged` / `unflagged` |
| `list_folders` | – | |
| `create_folder` | `folder_name` | |

All IMAP/SMTP operations run in `asyncio.to_thread()` (blocking I/O).

## Architecture Overview

```
External sender                        AIfred (mail account)
      |                                      |
      |--- email -->   INBOX  <-- IMAP IDLE listener (background worker)
      |                                      |
      |                              _process_uid()
      |                                      |
      |                              Message Processor
      |                              (session + routing)
      |                                      |
      |                                LLM generates reply
      |                                      |
      |<-- auto-reply ---  SMTP  <-- send_reply()
```

## Features

- **Push-based:** IMAP IDLE for instant notification on new emails
- **Auto-reply:** Incoming mails are answered automatically
- **Startup recovery:** Mails arriving during a restart are caught up on startup (checkpoint-based)
- **Session routing:** Replies are mapped back to the original session via the `In-Reply-To` header
- **HTML + plain text:** Replies are sent as `multipart/alternative` (agent Markdown is rendered to HTML with a plain-text fallback)
- **Logging:** All lifecycle events in journalctl (`journalctl -u aifred-intelligence | grep "Email Plugin"`)

## Reply Behaviour

The LLM automatically distinguishes between two scenarios:

| Incoming mail | AIfred's behaviour |
|---------------|--------------------|
| Normal conversation ("Hi", questions, info) | Replies directly via auto-reply |
| Irreversible action ("Send a mail to Bob", "Create an event") | Shows a draft, waits for confirmation via reply |

For irreversible actions a multi-turn flow over email emerges:

```
External → "Send a mail to bob@example.com with content XYZ"
AIfred   → Auto-reply: "Here is what I would do: ... Please confirm."
External → Reply: "Yes"          (lands in the same session via In-Reply-To)
AIfred   → Executes the action, auto-reply: "Done."
```

## Startup Recovery (Checkpoint)

After each processed mail the IMAP listener stores the UID in
`data/message_hub/imap_checkpoint.json`:

```json
{"last_uid": 146, "uidvalidity": 1278976979}
```

On (re)start:
- All UIDs > `last_uid` are detected as missed and caught up
- On UIDVALIDITY change (the IMAP server reassigned UIDs): recovery is skipped
- First start ever (no checkpoint): all existing mails are treated as "known"

## Configuration

Credentials are entered via `.env` or the UI modal (managed by the credential broker):

| Field | Default | Purpose |
|-------|---------|---------|
| `EMAIL_IMAP_HOST` | – | IMAP server |
| `EMAIL_IMAP_PORT` | `993` | IMAP port (SSL) |
| `EMAIL_SMTP_HOST` | – | SMTP server |
| `EMAIL_SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `EMAIL_USER` | – | Account login |
| `EMAIL_PASSWORD` | – | Account password (secret) |
| `EMAIL_FROM` | falls back to `EMAIL_USER` | Display name |
| `EMAIL_ALLOWED_SENDERS` | – | Allowlist for incoming senders |

The plugin counts as configured when `enabled = true` and IMAP host, user, and
password are set.

### Allowlist Semantics (`EMAIL_ALLOWED_SENDERS`)

The allowlist controls only **incoming** emails — who may contact AIfred.
Outgoing emails can be sent to any address.

- **Empty** → nobody allowed (safe default)
- **`*`** → everyone allowed
- **Comma-separated** addresses/domains: `user@mail.de, @family.de`
  - `@domain.de` matches any address at that domain
  - a bare address matches exactly

## User Mapping and Email Routing

AIfred distinguishes between **incoming** and **outgoing** email addresses per user.
Configuration is in `data/user_mapping.json`:

```json
{
  "Lord Helmchen": {
    "telegram": ["8669153916"],
    "discord": [],
    "email": ["receive@gmx.net"],
    "email_out": ["send@mail.de"]
  }
}
```

### Routing Logic

| Field | Purpose | Example |
|-------|---------|---------|
| `email` | **Incoming:** user sends emails to AIfred from this address | `receive@gmx.net` |
| `email_out` | **Outgoing:** AIfred sends results here (scheduler, tool calls) | `send@mail.de` |

### Outbound Resolution (Scheduler, Announce)

1. **Recipient specified** (e.g. `"Lord Helmchen"`) → user mapping → `email_out` preferred, fallback to `email`
2. **No recipient** → first user in mapping → `email_out` preferred
3. **No mapping** → fallback to `EMAIL_ALLOWED_SENDERS` (allowlist, first entry)

## Delta Chat as a Messenger Alternative

[Delta Chat](https://delta.chat) is a messenger that uses email as transport.
Because AIfred communicates over an email account, Delta Chat works as a
chat-style front end for talking to AIfred — similar to Telegram or Discord,
but without a separate bot account.

### Setup

1. **Install Delta Chat** (desktop or mobile)
2. **Add your own email account** (e.g. `you@mail.de`)
3. **Enable multi-device mode** (Advanced → Multi-device)
   - This makes Delta Chat watch the Sent folder
   - AIfred's replies then also appear as chat bubbles
4. **Start a new chat** with AIfred's email address (e.g. `aifred@gmx.net`)
5. **Add the sender address to the allowlist** (`EMAIL_ALLOWED_SENDERS`)

### Notes

- Delta Chat generates `@localhost` Message-IDs — session routing still works
  via the `In-Reply-To` header
- Messages from Delta Chat appear in AIfred as ordinary incoming emails
- AIfred's replies appear in Delta Chat thanks to the copy in the Sent folder
- Multiple profiles are possible: one for the normal mail account, another for a
  different account — independent of each other
- Delta Chat shows messages as chat bubbles with timestamps, which makes talking
  to AIfred feel more natural
