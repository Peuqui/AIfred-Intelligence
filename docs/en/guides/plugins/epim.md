# EPIM Plugin

**File:** `aifred/plugins/tools/epim/`

CRUD operations on an EssentialPIM Firebird database. Allows the LLM to manage contacts, appointments, notes, tasks (todos), passwords and other entities.

The plugin only becomes available once a database path is configured — without it, `is_available()` returns `False` and the tools are not registered (see [Configuration](#configuration)).

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `epim_search` | Search/read entries (tasks, contacts, notes, todos, passwords, categories, calendar_list, todolists, notetrees) | READONLY |
| `epim_create` | Create a new entry (task, contact, note, todo, password) | WRITE_DATA |
| `epim_update` | Update an existing entry (task, contact, note, note_tab, todo, password) | WRITE_DATA |
| `epim_delete` | Soft-delete an entry (task, contact, note, todo, password) | WRITE_SYSTEM |

### `epim_search` parameters

| Parameter | Type | Notes |
|-----------|------|-------|
| `entity_type` | string | **Required.** One of the entity types above |
| `query` | string | Search text (title, name, subject) |
| `date_from` | string | Start date filter `YYYY-MM-DD`, tasks only |
| `date_to` | string | End date filter `YYYY-MM-DD`, tasks only |
| `completed` | boolean | Filter by completion status, todos only |
| `limit` | integer | Max results (default: 20) |

`epim_create` and `epim_update` take an `entity_type` plus a `data` object with the
entity's fields. `epim_update` and `epim_delete` additionally require the `entity_id`
returned by `epim_search`.

## Features

- **Name-to-ID resolution:** Natural references like "meeting with Max" are resolved to the correct DB ID; category, calendar, todo-list and note-tree can be given by name instead of ID.
- **Entity aliases:** Both English and German entity names (e.g. `tasks`/`termine`, `contacts`/`kontakte`) resolve to the same canonical type.
- **7-day date reference:** The system prompt injects the upcoming week so relative expressions ("tomorrow", "next Monday") are interpreted correctly.
- **Anti-hallucination:** Strict prompt rules forbid inventing entries; an empty result is reported honestly instead.
- **Field mapping:** LLM-friendly and English field names are mapped to the internal German EPIM column names.
- **Large-ID safety:** Large integer IDs are returned as strings so the LLM does not truncate or round them.

## Configuration

Set via the credential field `EPIM_DB_PATH` (e.g. in `.env` or the plugin settings UI):

```
EPIM_DB_PATH=/path/to/database.epim
```

- `EPIM_ENABLED` is derived automatically — it is true exactly when `EPIM_DB_PATH` is set.
- Without a valid, readable database the plugin reports unavailable and contributes no tools.
- Passwords are never shown unsolicited (enforced by the prompt instructions).
