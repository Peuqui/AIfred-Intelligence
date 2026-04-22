# Email Channel Plugin

**File:** `aifred/plugins/channels/email_channel/`

Channel plugin for email communication via IMAP IDLE and SMTP.

## Tools (for the LLM)

| Tool | Description | Tier |
|------|------------|------|
| `email` | Fetch, read, search, send, move, delete, and mark emails | COMMUNICATE |

The `email` tool uses an `action` parameter: `check`, `read`, `search`, `delete`, `send`, `move`, `list_folders`, `create_folder`, `mark`.

## Features

- **Push-based:** IMAP IDLE for instant notification on new emails
- **Folder management:** Configurable IMAP folders to monitor
- **Flags:** Read/replied emails are correctly flagged
- **Auto-reply:** Automatic replies configurable per channel

## Configuration

- IMAP/SMTP server, port, credentials via `.env` or UI modal
- TLS/SSL configurable
- Monitored folders selectable

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
| `email` | **Incoming:** User sends emails to AIfred from this address | `receive@gmx.net` |
| `email_out` | **Outgoing:** AIfred sends results here (scheduler, tool calls) | `send@mail.de` |

### Outbound Resolution (Scheduler, Announce)

1. **Recipient specified** (e.g. `"Lord Helmchen"`) → user mapping → `email_out` preferred, fallback to `email`
2. **No recipient** → first user in mapping → `email_out` preferred
3. **No mapping** → fallback to `EMAIL_ALLOWED_SENDERS` (allowlist, first entry)

### Allowlist (Incoming)

The allowlist in `EMAIL_ALLOWED_SENDERS` controls only **incoming** emails — who may contact AIfred. Outgoing emails can be sent to any address.
