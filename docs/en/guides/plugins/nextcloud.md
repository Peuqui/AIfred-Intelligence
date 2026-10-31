# Nextcloud Plugin

> **Deutsche Version:** [nextcloud.md](../../../de/guides/plugins/nextcloud.md)

**File:** `aifred/plugins/tools/nextcloud/`

Appointments, tasks, contacts, notes and files from Nextcloud via CalDAV, CardDAV and WebDAV. The plugin stands on its own — it knows no other plugin, and no other plugin knows it.

## Tools

| Tool | Description | Tier | Offered |
|------|-------------|------|---------|
| `nextcloud_search` | Appointments in a date window (default: the next 30 days), tasks, contacts; lists of calendars, task lists, address books | WRITE_DATA | for readable modules |
| `nextcloud_get` | One appointment, task, contact or note with all details | WRITE_DATA | for readable modules |
| `nextcloud_create` | Create an appointment, task, contact or note | WRITE_DATA | for writable modules |
| `nextcloud_update` | Change an appointment, task, contact or note | WRITE_DATA | for writable modules |
| `nextcloud_delete` | Delete an appointment, task, contact or note | WRITE_SYSTEM | for writable modules |

| `nextcloud_file_list` | List the agent folder or a subfolder, or search it by name | WRITE_DATA | files module readable |
| `nextcloud_file_read` | Read a text file, also in portions by line | WRITE_DATA | files module readable |
| `nextcloud_file_write` | Create a text file, or overwrite it with its ETag | WRITE_DATA | files module writable |
| `nextcloud_file_move` | Move or rename, overwriting nothing | WRITE_DATA | files module writable |
| `nextcloud_file_delete` | Put into the trash bin | WRITE_SYSTEM | files module writable |

An entry's ID is `<collection>/<file>` and comes from `nextcloud_search`.

Reading carries `WRITE_DATA` too: appointments, contacts and files are private and must not leak through external channels. On a channel below this tier and in scheduled jobs with `max_tier` 1, reading is therefore not available either.

The file tools stand on their own on purpose: an agent can be given files separately from calendars and contacts.

## Modules and rights

Each module — **Appointments**, **Tasks**, **Contacts**, **Notes**, **Files** — is **Off**, **Read** (default) or **Read and write** in the plugin settings. Tools, entity types and instructions contain only the enabled modules; the service layer checks again on every call.

Writing happens only where Nextcloud **also** grants the configured user the write right on the collection. A Nextcloud user of AIfred's own is recommended, with your calendars and address books shared to it — a read-only share is enforced by Nextcloud itself, independent of AIfred.

## Behaviour

- **Times:** read in local time; written in UTC, all-day events as dates. For all-day events the end is the last day (inclusive), on input and output.
- **Series:** Nextcloud returns the occurrences within the window (`recurring=true`). Changing and deleting series is left to the calendar app.
- **Moving:** sending only `start` moves an appointment and keeps its duration.
- **Conditional writes:** create with `If-None-Match`, change and delete with `If-Match` on the ETag. If the entry was changed elsewhere meanwhile, the tool says so instead of overwriting.
- **Everything unknown survives a change:** an appointment's alarms and attendees, a contact's unknown vCard properties.
- **Contacts:** phone numbers by kind (mobile, home, work), e-mail (home, work), postal addresses (home, work), plus name, organization, title, birthday, note. When first or last name change, the display name follows.
- **Arguments:** dates only as `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`; unknown fields produce an error.

## Files

- **Agent folder:** Agents work only in `NEXTCLOUD_AGENT_FOLDER`. They name paths relative to it; `..` and similar ways out are refused before anything reaches the server. They can fill the folder itself but neither move nor delete it. If it is missing, the first write creates it.
- **Document window:** Nextcloud shows up as a further source while the files module is not off, live and without a mirror. There the user sees the whole account of the configured user with its shares, not only the agent folder. Uploading needs the write level. Files can be indexed there for the document search, one by one or as a whole folder, see [Workspace](workspace.md).
- **Overwriting:** Creating a file fails if it exists. Overwriting needs the ETag from reading it; if someone changed the file meanwhile, it fails. The one gap lies with the server: a change in the same second as the one before leaves the ETag as it is.
- **Reading:** UTF-8 text only, without a size limit: the file passes through as a stream, only the chosen range of lines stays in memory (`lib/line_window.py`), and a binary file drops out at its first invalid byte. The content goes into the model's context and comes back marked as data; what does not fit is cut from the end by the general limit on tool output. An agent therefore reads long files in portions with `line_start` and `line_end`; the answer names `total_lines`. The ETag always belongs to the whole file. Other files are available through the document window.
- **Searching:** by file name, not by content.
- **Deleting:** Nextcloud puts the file into the user's trash bin.

## Notes

The Notes app stores every note as a text file: the title is the file name, the category the subfolder. Agents work through `nextcloud_search`, `nextcloud_get`, `nextcloud_create`, `nextcloud_update` and `nextcloud_delete` with the type `note`, only inside `NEXTCLOUD_NOTES_FOLDER`; app and agents see the same state.

- **Sharing:** The user's notes lie in that user's account. The `Notes` folder has to be shared with AIfred's Nextcloud user, with write access for writing. If it is missing, creating a note is refused instead of producing an invisible folder of its own.
- **Searching:** by title, not in the text.
- **Changing:** `content` replaces the whole text. A new title or category moves the file; the result names the new ID.
- **Deleting:** The note goes into the trash bin.

## Deletions from EssentialPIM

EPIM's option to delete entries on the other side stays off when syncing with Nextcloud: in about every fourth run EPIM loses entries while reading, takes them for deleted on the server and, with the option on, deletes them locally. The server delivers everything.

Without the option a deletion in EPIM does not reach Nextcloud. `scripts/epim_deletions_to_nextcloud.py` does that part: it reads the newest backup EPIM wrote itself, takes what entered the recycle bin since the last run and deletes it in Nextcloud when exactly one non-recurring event with the same title and start exists and EPIM holds no active entry like it. Series and ambiguous matches are only reported. Contacts and tasks are not covered, nor are deletions made in Nextcloud, which stay in EPIM.

Settings live in `data/epim/deletion_sync.json` (`ssh_host`, `ssh_port`, `ssh_user`, `backup_dir`, `backup_glob`, `work_dir`, `max_deletions`). Without `--apply` the script only shows the plan; above `max_deletions` it deletes nothing and exits with an error code. The user timer `systemd/aifred-epim-deletions.timer` starts it hourly: with the EPIM machine off the run ends at once without an error, and a backup already processed changes nothing. A deletion reaches Nextcloud with the next backup EPIM writes.

## Configuration

| Field | Stored in | Meaning |
|-------|-----------|---------|
| `NEXTCLOUD_URL` | plugin `settings.json` | Address, e.g. `https://cloud.example.org` or `https://example.org/nextcloud` |
| `NEXTCLOUD_USER` | plugin `settings.json` | User (recommended: a user of AIfred's own) |
| `NEXTCLOUD_NOTES_FOLDER` | Plugin `settings.json` | Folder of the Notes app (default `Notes`) |
| `NEXTCLOUD_APP_PASSWORD` | `.env` (secret, via the credential broker) | App password from Nextcloud → Settings → Security |
| `NEXTCLOUD_MODULE_CALENDAR`, `_TASKS`, `_CONTACTS`, `_FILES` | plugin `settings.json` | `off`, `read` (default) or `write` |
| `NEXTCLOUD_AGENT_FOLDER` | plugin `settings.json` | The folder agents work in (default `AIfred-Intelligence`) |

Required packages: `icalendar`, `vobject` (see `requirements.txt`).

## Tests

`tests/test_nextcloud_ical_vcard.py` needs no server. `tests/test_nextcloud_dav.py`, `tests/test_nextcloud_tools.py` and the live parts of `tests/test_nextcloud_files.py` work live in the `aifred-test` collections of a test user of their own, separate from the user AIfred runs as, which only sees what is shared with it. The test user's credentials live locally in `~/.config/aifred/nextcloud-test.env` (without the file the tests are skipped). Every test removes what it creates; Nextcloud keeps deleted items in its trash bin for 30 days.
