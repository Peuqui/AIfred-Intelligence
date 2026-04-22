# Plugin Overview

AIfred uses a unified plugin system. Plugins are auto-discovered — drop a `.py` file into `plugins/tools/` or `plugins/channels/`, done.

> **Developer Guide:** [Plugin Development Guide](plugin-development.md)
> **Security:** [Security Architecture](../architecture/security.md)

---

## Tool Plugins

Tool Plugins provide tools the LLM can call autonomously during conversations.

### Workspace (Files & Documents)

**File:** `plugins/tools/workspace/`

File access to the documents directory (`data/documents/`) and semantic search via ChromaDB.

| Tool | Description | Tier |
|------|------------|------|
| `list_files` | List files in the documents directory | READONLY |
| `read_file` | Read a file (PDFs page-by-page with `pages="1-5"`) | READONLY |
| `write_file` | Write/edit a text file (with verify) | WRITE_DATA |
| `create_folder` | Create a subfolder | WRITE_DATA |
| `delete_file` | Delete a file from disk | WRITE_SYSTEM |
| `delete_folder` | Delete an empty folder | WRITE_SYSTEM |
| `index_document` | Index a file into the ChromaDB vector database | WRITE_DATA |
| `search_documents` | Search indexed documents semantically | READONLY |
| `list_indexed` | List all indexed documents | READONLY |
| `delete_document` | Remove a document from the vector database | WRITE_SYSTEM |
| `chromadb_stats` | Show all ChromaDB collections with entry counts | READONLY |
| `chromadb_clear` | Clear all entries from a collection | WRITE_SYSTEM |

**Features:**
- Page-by-page PDF reading (`pages="3,7,10-12"`)
- Line-range reading for large text files (`line_start`/`line_end`)
- Path traversal protection (confined to `data/documents/`)
- Write verify: files are read back after writing and compared
- Writing restricted to text formats (.txt, .md, .csv, .json, .xml, .html)
- Central ChromaDB management (Research Cache, Documents, Agent Memories)

> **Details:** [Workspace Plugin](plugins/workspace.md)

---

### EPIM (Personal Database)

**File:** `plugins/tools/epim/`

Full CRUD access to the [EssentialPIM](https://www.essentialpim.com/) Firebird database — appointments, contacts, notes, todos, passwords.

| Tool | Description | Tier |
|------|------------|------|
| `epim_search` | Search entries (calendar, contacts, notes, todos, passwords) | READONLY |
| `epim_create` | Create a new entry | WRITE_DATA |
| `epim_update` | Update/move an entry | WRITE_DATA |
| `epim_delete` | Delete an entry | WRITE_SYSTEM |

**Features:**
- Automatic name-to-ID resolution
- 7-day date reference in prompt
- Anti-hallucination guardrails
- Field mapping (English to German)

> **Details:** [EPIM Plugin](plugins/epim.md)

---

### Web Research

**File:** `plugins/tools/research/`

Automatic web research with multiple search APIs and semantic cache.

| Tool | Description | Tier |
|------|------------|------|
| `web_search` | Web search via Brave, Tavily or SearXNG | READONLY |
| `web_fetch` | Fetch and extract URL content | READONLY |

**Features:**
- Multi-API with automatic fallback
- Result scraping and ranking
- Semantic vector cache via ChromaDB

> **Details:** [Research Plugin](plugins/research.md)

---

### Sandbox (Code Execution)

**File:** `plugins/tools/sandbox/`

Isolated Python code execution in subprocess.

| Tool | Description | Tier |
|------|------------|------|
| `execute_code` | Run Python code (documents read-only) | WRITE_DATA |
| `execute_code_write` | Run Python code with write access to documents | WRITE_SYSTEM |

> **Details:** [Sandbox Plugin](plugins/sandbox.md)

---

### Calculator

**File:** `plugins/tools/calculator/`

| Tool | Description | Tier |
|------|------------|------|
| `calculate` | Evaluate mathematical expressions | READONLY |

> **Details:** [Calculator Plugin](plugins/calculator.md)

---

### Audio Player

**File:** `plugins/tools/audio_player/`

| Tool | Description | Tier |
|------|------------|------|
| `audio_play` | Play audio file (WAV, MP3, OGG, FLAC) | WRITE_DATA |
| `audio_stop` | Stop playback | READONLY |
| `audio_status` | Query playback status | READONLY |

> **Details:** [Audio Player Plugin](plugins/audio-player.md)

---

### Scheduler

**File:** `plugins/tools/scheduler_tool/`

Scheduled tasks for AIfred.

| Tool | Description | Tier |
|------|------------|------|
| `scheduler_create` | Create a scheduled job | WRITE_DATA |
| `scheduler_list` | List all scheduled jobs | READONLY |
| `scheduler_delete` | Delete a job | WRITE_DATA |

**Features:**
- Three schedule types: `cron`, `interval` (seconds), `once` (ISO timestamp)
- Delivery modes: `log`, `announce`, `review`, `webhook`
- Isolated sessions per job

> **Details:** [Scheduler Plugin](plugins/scheduler.md)

---

### System Monitor

**File:** `plugins/tools/system_monitor/`

Hardware status: CPU, RAM, GPU, disk, temperature.

| Tool | Description | Tier |
|------|------------|------|
| `system_status` | Query hardware status (CPU, RAM, GPU, disk, temperature) | READONLY |

> **Details:** [System Monitor Plugin](plugins/system-monitor.md)

---

### Google Suite

**Directory:** `plugins/tools/google_suite/`

OAuth 2.0 integration for Google Calendar and Contacts. Orchestrator plugin with toggleable sub-services.

| Tool | Description | Tier |
|------|------------|------|
| `google_calendar_list_events` | List events in a time range | READONLY |
| `google_calendar_create_event` | Create a new event | WRITE_DATA |
| `google_calendar_update_event` | Update an existing event | WRITE_DATA |
| `google_calendar_delete_event` | Delete an event | WRITE_DATA |
| `google_calendar_list_calendars` | List all calendars | READONLY |
| `google_contacts_list_all` | Retrieve all contacts (paginated) | READONLY |
| `google_contacts_list_groups` | List contact groups/labels | READONLY |
| `google_contacts_list_by_group` | Retrieve contacts in a group | READONLY |
| `google_contacts_search` | Search contacts by name/email | READONLY |
| `google_contacts_create` | Create a new contact | WRITE_DATA |
| `google_contacts_update` | Update a contact | WRITE_DATA |
| `google_contacts_delete` | Delete a contact | WRITE_DATA |

**Features:**
- Single OAuth login for all sub-services (scopes aggregated)
- Sub-services toggleable via `settings.json` (default: Calendar + Contacts on)
- Group support: categorize contacts, filter by group
- Fernet-encrypted token storage

**Prerequisite:** Google Cloud Console setup + OAuth flow. See [OAuth Broker](plugins/oauth.md).

> **Details:** [Google Suite Plugin](plugins/google-suite.md)

---

### Translator (DeepL)

**File:** `plugins/tools/translator/`

Text translation via DeepL API with automatic source language detection. 30+ languages, 500,000 characters/month free.

| Tool | Description | Tier |
|------|------------|------|
| `translate` | Translate text to a target language | READONLY |

> **Details:** [Translator Plugin](plugins/translator.md)

---

## Channel Plugins

Channel Plugins connect AIfred to external communication channels. Incoming messages are processed automatically with optional auto-reply.

### Email

**File:** `plugins/channels/email_channel/`

IMAP IDLE push-based email monitor with SMTP auto-reply.

**Features:**
- IMAP IDLE (push, no polling)
- Folder management (move, create, list)
- Flag management (read/unread/flagged)
- Auto-reply configurable per channel

> **Details:** [Email Plugin](plugins/email.md)

---

### Discord

**File:** `plugins/channels/discord_channel/`

Discord bot with channel and DM support.

**Features:**
- WebSocket/Gateway connection
- `/clear` slash command
- Channel and DM messages

> **Details:** [Discord Plugin](plugins/discord.md)

---

### Telegram

**File:** `plugins/channels/telegram_channel/`

Telegram bot via long polling.

**Features:**
- Whitelist-based access
- Auto-reply configurable
- Setup guide: [Telegram Setup](telegram-setup.md)

> **Details:** [Telegram Plugin](plugins/telegram.md)

---

## Plugin Architecture

```
aifred/plugins/
├── tools/                  # Tool Plugins (LLM tools)
│   ├── workspace/          # Files & ChromaDB
│   ├── research/           # Web research
│   ├── sandbox/            # Code execution
│   ├── calculator/         # Math
│   ├── audio_player/       # Audio playback
│   ├── scheduler_tool/     # Scheduled tasks
│   ├── translator/         # DeepL translation
│   ├── epim/               # EPIM database
│   │   ├── tools.py
│   │   └── db.py
│   └── google_suite/       # Google Calendar + Contacts (OAuth)
│       ├── calendar/
│       └── contacts/
└── channels/               # Channel Plugins (communication)
    ├── email_channel/      # Email (IMAP/SMTP)
    ├── discord_channel/    # Discord bot
    └── telegram_channel/   # Telegram bot
```

**Auto-Discovery:** Any `.py` file with a `plugin` attribute (Tool) or `BaseChannel` subclass (Channel) is auto-discovered. No registration needed.

**Security Tiers:**

| Tier | Level | Examples |
|------|-------|---------|
| 0 | READONLY | Search, read, list |
| 1 | COMMUNICATE | Send email, Discord message |
| 2 | WRITE_DATA | Create, update, execute code |
| 3 | WRITE_SYSTEM | Delete, system operations |
| 4 | ADMIN | Shell access (not implemented) |

**Plugin Manager:** Plugins can be enabled/disabled at runtime via the UI modal (moves files to `disabled/`).
