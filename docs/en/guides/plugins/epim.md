# EPIM Plugin

> **Deutsche Version:** [epim.md](../../../de/guides/plugins/epim.md)

**File:** `aifred/plugins/tools/epim/`

Access to an EssentialPIM database (Firebird 2.5, embedded): appointments, contacts, notes, todos and the titles of password entries. **Read-only** by default; the "Write access" switch in the plugin settings enables writing.

## Tools

| Tool | Description | Tier | Offered |
|------|-------------|------|---------|
| `epim_search` | Appointments, contacts, notes, todos, password titles; lists of categories, calendars, todo lists, note trees | READONLY | always |
| `epim_get` | One appointment, contact, or note with all tabs | READONLY | always |
| `epim_create` | Create an appointment, contact, note, todo | WRITE_DATA | write access only |
| `epim_update` | Change an appointment, contact, note, note tab, todo | WRITE_DATA | write access only |
| `epim_delete` | Move to the EPIM trash | WRITE_SYSTEM | write access only |

With write access off, the model never sees the write tools or their instructions.

**Not enabled yet**, until EPIM's own write format is established by a reference recording: creating notes and todos, changing note text, marking todos done, creating or changing password entries. The tool says so explicitly.

## Protecting the database

With write access off, four independent locks apply:

1. The write tools are not offered.
2. The database layer refuses every write call (`EpimWriteDisabled`).
3. Every read runs in a read-only transaction that ends right away.
4. The file itself is in Firebird's `read_only` mode — the engine rejects any change. Saving the switch in the settings applies this mode.

Further rules, also with writing on:

- Every transaction ends with its call (commit or rollback as one unit), with a 10 s lock timeout instead of waiting forever. Database calls run in a worker thread and never block the event loop.
- AIfred connects without EPIM's database triggers (`DB_CONNECT0` would register every session in `CONNECTIONS`). Table triggers run as usual.
- When the file is replaced (a new mirror), the next call reconnects.
- Arguments are parsed strictly: dates only as `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`; unknown fields produce an error instead of vanishing; an end before the start is refused.
- Recurring appointments (EPIM materializes every occurrence as its own row) and entries with sub-entries are not changed or deleted — that is left to EPIM.
- Contact fields: the codec writes only when it understands the stored fields completely and can re-encode them byte-identically; otherwise it refuses. Reads report incompletely stored fields (`field_data_error`).
- Deleting moves the entry to the EPIM trash (`STATUS=1`, `DELETED`), the way EPIM does it.

## EPIM specifics

- EPIM stores "no date" as 1899-12-30 (Delphi's zero date); AIfred returns `null` for it.
- All-day appointments: EPIM's end is the midnight after the last day; AIfred takes and returns the inclusive last day.
- EPIM shows note text from `FORMATEDTEXT` (zlib-compressed RTF); `TEXT` is the plain copy.
- Standard contact fields carry no name in the database; their names live in `contact_fields.py` (established fields only). Date fields hold Delphi day numbers.

## Mirror from Aragon

The working database lives on Aragon (Windows). The script `aragon/push-epim-to-mini.ps1` copies it to the Mini once EssentialPIM is closed (the exclusive open fails otherwise). On the Mini, `install-mirror` rebuilds the copy with a Firebird backup and restore, compares the record count of every table, makes it read-only and atomically replaces the configured file; the previous one stays as `.previous`. With write access on, `install-mirror` refuses, so AIfred's changes are not overwritten.

## Configuration

In the plugin settings (stored in `aifred/plugins/tools/epim/settings.json`):

| Field | Meaning |
|-------|---------|
| `EPIM_DB_PATH` | Path to the `.epim` file |
| `EPIM_WRITE_ENABLED` | `false` (default) or `true` |

The embedded Firebird library lives in `lib/firebird25/` (not versioned).

## Maintenance

```
python -m aifred.plugins.tools.epim.maintenance validate FILE
python -m aifred.plugins.tools.epim.maintenance rebuild SOURCE DEST
python -m aifred.plugins.tools.epim.maintenance set-mode FILE read-only|read-write
python -m aifred.plugins.tools.epim.maintenance snapshot FILE OUT.json
python -m aifred.plugins.tools.epim.maintenance diff BEFORE.json AFTER.json
python -m aifred.plugins.tools.epim.maintenance install-mirror INCOMING
python -m aifred.plugins.tools.epim.maintenance make-fixture SOURCE DEST
```

`make-fixture` creates the database for the integration tests (`data/epim/test-fixture.epim`): EPIM's complete schema with triggers and views, but only the lookup tables, no entries.
