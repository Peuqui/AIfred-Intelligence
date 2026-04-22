# Google Suite Plugin

**Files:** `aifred/plugins/tools/google_suite/`

Google Calendar and Google Contacts via OAuth 2.0. Orchestrator plugin with two toggleable sub-services (Calendar, Contacts). Requires a one-time OAuth flow in Google Cloud Console.

## Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → New project → **APIs & Services** → **Credentials** → **Create Credentials** → OAuth 2.0 Client ID (type: **Web application**)
2. Enable the following APIs: **Google Calendar API**, **People API**
3. Add to **Authorized redirect URIs**:
   ```
   https://narnia.spdns.de:8443/api/oauth/google/callback
   ```
4. Add credentials to `.env`:
   ```
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
5. Enable/disable sub-services in `aifred/plugins/tools/google_suite/settings.json` (default: both enabled)
6. Start the OAuth flow — generate an auth URL:
   ```bash
   curl "http://localhost:8002/api/oauth/google/auth-url?redirect_uri=https://narnia.spdns.de:8443/api/oauth/google/callback&scopes=https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/contacts"
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
  "GOOGLE_CONTACTS_ENABLED": "true"
}
```

To disable a sub-service: set the value to `"false"` and restart AIfred.

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
