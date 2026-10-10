# Google Suite Plugin

> **Deutsche Version:** [google-suite.md](../../../de/guides/plugins/google-suite.md)

**Files:** `aifred/plugins/tools/google_suite/`

Google Calendar, Contacts, Tasks, and Drive via OAuth 2.0. Orchestrator plugin with four toggleable sub-services. Requires a one-time OAuth flow in Google Cloud Console. The OAuth mechanism (token storage, auto-refresh, API endpoints) is described in [oauth.md](oauth.md).

## Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → New project → **APIs & Services** → **Credentials** → **Create Credentials** → OAuth 2.0 Client ID (type: **Web application**)
2. Enable the following APIs: **Google Calendar API**, **People API**, **Tasks API v1**, **Google Drive API**
3. Add to **Authorized redirect URIs**:
   ```
   https://example.com:8443/api/oauth/google/callback
   ```
4. Add credentials to `.env`:
   ```
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
5. Enable/disable sub-services in `aifred/plugins/tools/google_suite/settings.json` (default: all four enabled)
6. Start the OAuth flow — generate an auth URL:
   ```bash
   curl "http://localhost:8002/api/oauth/google/auth-url?redirect_uri=https://example.com:8443/api/oauth/google/callback&scopes=https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/contacts"
   ```
   Open the returned URL in a browser → Google login → redirected to callback URL → done.
7. Check connection status:
   ```bash
   curl http://localhost:8002/api/oauth/google/status
   ```

## Sub-Services & Settings

`aifred/plugins/tools/google_suite/settings.json`:
```json
{
  "GOOGLE_CALENDAR_ENABLED": "true",
  "GOOGLE_CONTACTS_ENABLED": "true",
  "GOOGLE_TASKS_ENABLED": "true",
  "GOOGLE_DRIVE_ENABLED": "true"
}
```

To disable a sub-service: set the value to `"false"` and restart AIfred. Which scopes the OAuth flow requests depends on the enabled sub-services (`aggregated_scopes()`).

## Calendar Tools

**File:** `aifred/plugins/tools/google_suite/calendar/tools.py`

| Tool | Description | Tier |
|------|-------------|------|
| `google_calendar_list_events` | List events within a time range | READONLY |
| `google_calendar_create_event` | Create a new event | WRITE_DATA |
| `google_calendar_update_event` | Update an existing event (only provided fields) | WRITE_DATA |
| `google_calendar_delete_event` | Delete an event | WRITE_DATA |
| `google_calendar_list_calendars` | List all calendars of the user | READONLY |

**Timestamps:** RFC 3339 format, e.g. `2026-04-22T10:00:00+02:00` or `2026-04-22T08:00:00Z`

### Parameters `google_calendar_list_events`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `start` | Yes | Start time (RFC 3339) |
| `end` | Yes | End time (RFC 3339) |
| `calendar_id` | No | Calendar ID (default: `primary`) |
| `max_results` | No | Maximum number of results (default: 20) |

### Parameters `google_calendar_create_event`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `title` | Yes | Event title |
| `start` | Yes | Start time (RFC 3339) |
| `end` | Yes | End time (RFC 3339) |
| `calendar_id` | No | Calendar ID (default: `primary`) |
| `description` | No | Description |
| `location` | No | Location |
| `attendees` | No | Comma-separated email addresses |

### Parameters `google_calendar_update_event`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `event_id` | Yes | Event ID (from `list_events`) |
| `calendar_id` | No | Calendar ID (default: `primary`) |
| `title` | No | New title |
| `start` | No | New start time (RFC 3339) |
| `end` | No | New end time (RFC 3339) |
| `description` | No | New description |
| `location` | No | New location |

## Contacts Tools

**File:** `aifred/plugins/tools/google_suite/contacts/tools.py`

| Tool | Description | Tier |
|------|-------------|------|
| `google_contacts_list_all` | Retrieve all contacts (paginated) | READONLY |
| `google_contacts_list_groups` | List all contact groups/labels | READONLY |
| `google_contacts_list_by_group` | Retrieve all contacts in a group | READONLY |
| `google_contacts_search` | Search contacts by name or email | READONLY |
| `google_contacts_create` | Create a new contact | WRITE_DATA |
| `google_contacts_update` | Update an existing contact (only provided fields) | WRITE_DATA |
| `google_contacts_delete` | Delete a contact | WRITE_DATA |

**Resource names:** Format `people/c123456789` — returned by `google_contacts_search`, required for update/delete.

### Parameters `google_contacts_search`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Search term (name or email) |
| `max_results` | No | Maximum results (default: 10) |

### Parameters `google_contacts_create`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `display_name` | Yes | Full name |
| `email` | No | Email address |
| `phone` | No | Phone number |
| `organization` | No | Company / organisation |
| `notes` | No | Notes |
| `group` | No | Group name (e.g. `Family`) |

### Parameters `google_contacts_update`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `resource_name` | Yes | Resource name from `google_contacts_search` |
| `display_name` | No | New name |
| `email` | No | New email |
| `phone` | No | New phone number |
| `organization` | No | New organisation |
| `notes` | No | New notes |
| `group` | No | Assign to group |

## Example Usage

> "What do I have on my calendar tomorrow?"

AIfred calls `google_calendar_list_events(start="2026-04-23T00:00:00+02:00", end="2026-04-23T23:59:59+02:00")`.

---

> "Create an appointment Friday 3pm dentist"

AIfred calls `google_calendar_create_event(title="Dentist", start="2026-04-25T15:00:00+02:00", end="2026-04-25T16:00:00+02:00")`.

---

> "What is the email address of John Doe?"

AIfred calls `google_contacts_search(query="John Doe")`.

---

> "Show all contacts in the group Family"

AIfred calls `google_contacts_list_by_group(group_name="Family")`.

---

> "Search my Drive for the project plan"

AIfred calls `google_drive_search(query="project plan")`.

## Tasks Tools

**File:** `aifred/plugins/tools/google_suite/tasks/tools.py`

| Tool | Description | Tier |
|------|-------------|------|
| `google_tasks_list_tasklists` | List all task lists | READONLY |
| `google_tasks_list` | Retrieve tasks from a list | READONLY |
| `google_tasks_create` | Create a new task | WRITE_DATA |
| `google_tasks_update` | Update a task | WRITE_DATA |
| `google_tasks_complete` | Mark a task as done | WRITE_DATA |
| `google_tasks_delete` | Delete a task | WRITE_DATA |

## Drive Tools

**File:** `aifred/plugins/tools/google_suite/drive/tools.py`

| Tool | Description | Tier |
|------|-------------|------|
| `google_drive_list_files` | List the contents of a folder inside the agent folder (without `folder_id`: the agent folder itself) | WRITE_DATA |
| `google_drive_search` | Full-text search inside the agent folder | WRITE_DATA |
| `google_drive_get_file` | Read file content (Google Docs → plain text, Sheets → CSV; capped at 5 MB) | WRITE_DATA |
| `google_drive_create_file` | Create a new text file with content | WRITE_DATA |
| `google_drive_update_file` | Overwrite file content | WRITE_DATA |
| `google_drive_delete_file` | Permanently delete a file (no trash) | WRITE_SYSTEM |
| `google_drive_create_folder` | Create a new folder | WRITE_DATA |
| `google_drive_move_file` | Move a file to a different folder | WRITE_DATA |

The read tools are deliberately `WRITE_DATA`: they expose private Drive content and
therefore stay blocked for external channels (email, Discord, Telegram). The cap for
`google_drive_get_file` is `DRIVE_MAX_DOWNLOAD_BYTES` (environment variable, default
5 MiB).

## Agent Folder

**File:** `aifred/plugins/tools/google_suite/drive/agent_folder.py`

The agents work in **one** Drive folder only. Its name is the setting
`GOOGLE_DRIVE_AGENT_FOLDER` (plugin settings, default `AIfred-Intelligence`); the
folder is looked up directly under "My Drive" and created on first access if missing.

- **Free inside:** read, write, move, delete and create subfolders — in the agent folder and all its subfolders.
- **Never the folder itself:** the tools refuse to change, move or delete the agent folder.
- **Nothing outside:** any `file_id` or `folder_id` outside the agent folder is rejected with an error. Without `folder_id` the agent folder applies. Search returns only hits from inside.
- The boundary is resolved on every toolkit build; a renamed setting or a folder created meanwhile counts from the next turn.
- If "My Drive" holds several folders of that name the boundary is ambiguous and the tools report an error; so do they for an empty setting value.
- The user's **document manager** does not go through this boundary: it sees the whole Drive (see below).

## Drive in the Document Manager

**Files:** `aifred/lib/document_sources.py`, `aifred/plugins/tools/google_suite/drive/source.py`, `aifred/state/_document_mixin.py`, `aifred/ui/modals/documents.py`, `aifred/lib/api/documents.py`

With the Drive sub-service enabled (`GOOGLE_DRIVE_ENABLED`) and Google connected, the
document manager shows a second source, "Google Drive", next to the local folder
(switch at the top). It is a live view, not a mirror of the Drive.

| Function | Behaviour |
|----------|-----------|
| Browsing | Open folders, path bar, up/root; refresh button. The view starts at "My Drive" |
| Search | Input field in the toolbar, searches the whole Drive by file name and full text (at most 100 hits, by relevance) |
| Download | Arrow icon per file; the file is streamed, never routed through Reflex state (`GET /api/documents/source/{key}/file/{id}`, login cookie). 404 for an unknown source, 502 if the source fails |
| Export | Google's own formats have no file and are exported: Docs → `.docx`, Sheets → `.xlsx`, Slides → `.pptx`, Drawings → `.pdf`. Other Google formats and folders cannot be downloaded |
| Upload | The document manager's upload goes into the currently open Drive folder (root: "My Drive"); the same size limit as locally applies (`DOCUMENT_MAX_FILE_SIZE_MB`) |

Not available for Drive rows: indexing, renaming, deleting and preview — those stay with
the local folder.

### Architecture: Document Sources

The document manager knows no plugins. A tool plugin offers a source through the optional
attribute `document_source` (an object satisfying the `DocumentSource` protocol in
`aifred/lib/document_sources.py`, or `None` while it offers none). `document_sources()`
collects them from the enabled, available plugins; a disabled plugin simply offers no
source (plugin atomicity, the core imports no plugin).

| Part | Purpose |
|------|---------|
| `key`, `label(lang)` | stable id (`google_drive`) and name in the switch |
| `list_folder(folder_id)` | folder contents; folders are addressed by id, `""` = root |
| `search(query)` | search across the whole source |
| `download(file_id)` | streamed download (`SourceDownload`) |
| `upload(folder_id, name, data, mime)` | upload a file |

Sources raise only `DocumentSourceError` (a message for the user, shown as a toast).
The state (active source, folder trail, search term) lives in `_document_mixin.py`.
A further source (e.g. Nextcloud) therefore needs only a plugin with `document_source`.

### Parameters `google_drive_search`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Search term or Drive query syntax (e.g. `name contains 'Report'`) |
| `page_size` | No | Maximum results (default: 20) |

### Parameters `google_drive_create_file`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `name` | Yes | Filename with extension (e.g. `note.txt`) |
| `content` | Yes | File content |
| `folder_id` | No | Target folder ID (optional) |
| `mime_type` | No | MIME type (default: `text/plain`) |
