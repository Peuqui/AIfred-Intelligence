# Nextcloud Plugin

> **Deutsche Version:** [nextcloud.md](../../../de/guides/plugins/nextcloud.md)

**File:** `aifred/plugins/tools/nextcloud/`

Appointments, tasks and contacts from Nextcloud via CalDAV and CardDAV. The plugin stands on its own — it knows no other plugin, and no other plugin knows it.

## Tools

| Tool | Description | Tier | Offered |
|------|-------------|------|---------|
| `nextcloud_search` | Appointments in a date window (default: the next 30 days), tasks, contacts; lists of calendars, task lists, address books | READONLY | for readable modules |
| `nextcloud_get` | One appointment, task or contact with all details | READONLY | for readable modules |
| `nextcloud_create` | Create an appointment, task or contact | WRITE_DATA | for writable modules |
| `nextcloud_update` | Change an appointment, task or contact | WRITE_DATA | for writable modules |
| `nextcloud_delete` | Delete an appointment, task or contact | WRITE_SYSTEM | for writable modules |

An entry's ID is `<collection>/<file>` and comes from `nextcloud_search`.

## Modules and rights

Each module — **Appointments**, **Tasks**, **Contacts** — is **Off**, **Read** (default) or **Read and write** in the plugin settings. Tools, entity types and instructions contain only the enabled modules; the service layer checks again on every call.

Writing happens only where Nextcloud **also** grants the configured user the write right on the collection. A Nextcloud user of AIfred's own is recommended, with your calendars and address books shared to it — a read-only share is enforced by Nextcloud itself, independent of AIfred.

## Behaviour

- **Times:** read in local time; written in UTC, all-day events as dates. For all-day events the end is the last day (inclusive), on input and output.
- **Series:** Nextcloud returns the occurrences within the window (`recurring=true`). Changing and deleting series is left to the calendar app.
- **Moving:** sending only `start` moves an appointment and keeps its duration.
- **Conditional writes:** create with `If-None-Match`, change and delete with `If-Match` on the ETag. If the entry was changed elsewhere meanwhile, the tool says so instead of overwriting.
- **Everything unknown survives a change:** an appointment's alarms and attendees, a contact's unknown vCard properties.
- **Contacts:** phone numbers by kind (mobile, home, work), e-mail (home, work), postal addresses (home, work), plus name, organization, title, birthday, note. When first or last name change, the display name follows.
- **Arguments:** dates only as `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`; unknown fields produce an error.

## Configuration

| Field | Stored in | Meaning |
|-------|-----------|---------|
| `NEXTCLOUD_URL` | plugin `settings.json` | Address, e.g. `https://cloud.example.org` or `https://example.org/nextcloud` |
| `NEXTCLOUD_USER` | plugin `settings.json` | User (recommended: a user of AIfred's own) |
| `NEXTCLOUD_APP_PASSWORD` | `.env` (secret, via the credential broker) | App password from Nextcloud → Settings → Security |
| `NEXTCLOUD_MODULE_CALENDAR`, `_TASKS`, `_CONTACTS` | plugin `settings.json` | `off`, `read` (default) or `write` |

Required packages: `icalendar`, `vobject` (see `requirements.txt`).

## Tests

`tests/test_nextcloud_ical_vcard.py` needs no server. `tests/test_nextcloud_dav.py` and `tests/test_nextcloud_tools.py` work live in the `aifred-test` collections of a test user of their own, separate from the user AIfred runs as, which only sees what is shared with it. The test user's credentials live locally in `~/.config/aifred/nextcloud-test.env` (without the file the tests are skipped). Every test removes what it creates; Nextcloud keeps deleted items in its trash bin for 30 days.
