# Printer Plugin

> **Deutsche Version:** [printer.md](../../../de/guides/plugins/printer.md)

**File:** `aifred/plugins/tools/printer/`

Prints files from the documents area and reads the printer state through **CUPS**.
The plugin talks to CUPS through its command-line tools (`lp`, `lpstat`, `ipptool`) —
no Python package is needed, but the CUPS client tools must be installed. Commands run
with `LC_ALL=C` and a 15 s timeout.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `print_file` | Print a file from the documents area on the default printer | COMMUNICATE |
| `printer_status` | State, warnings, queued jobs and supply levels of the default printer | READONLY |

### Parameters (`print_file`)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `filename` | string | yes | Path inside the documents area, as `list_files` shows it (e.g. `rechnungen/2026-10.pdf`) |
| `copies` | integer | no | Number of copies (default 1; capped by the plugin setting) |
| `pages` | string | no | Page selection, e.g. `1-3,5` (empty = all pages) |
| `duplex` | boolean | no | Print on both sides (long edge); default single-sided |

Returns (JSON): `printer`, `job` (CUPS job id), `file`, `copies`. `printer_status` has
no parameters and returns `printer`, `state` (`idle`, `processing`, `stopped`),
`reasons` (CUPS state reasons such as `media-empty-warning`), `message`,
`queued_jobs`, `supplies` (name, kind, level in %, low/high mark), plus the derived lists
`low_supplies` (consumables at or below their low mark) and `full_waste_containers`
(waste containers at or above their high mark). For a waste container the level is
how full it is.

## Which printer

The printers come from CUPS itself (`lpstat -e`), including network printers that
`cups-browsed` finds — those exist only while the printer is reachable. None is
configured in the plugin. The default printer is chosen like this:

1. the printer chosen in the plugin settings, if CUPS lists it right now;
2. otherwise the only printer, if exactly one is listed;
3. otherwise an error: no printer reachable (CUPS lists none), the chosen printer is not reachable, or several printers and none chosen. There is no automatic fallback to another printer.

## Configuration

Plugin settings (in the plugin tab; stored in
`aifred/plugins/tools/printer/settings.json`, no secrets):

| Key | Default | Description |
|-----|---------|-------------|
| `PRINTER_DEFAULT` | empty | Default printer; the options list what CUPS reaches right now. With a single printer it is used without a choice |
| `PRINTER_MAX_COPIES` | `10` | Maximum copies per job — a guard against the model's typos; `print_file` refuses more |

## Permissions and limits

- `print_file` is tier **COMMUNICATE**: channels at that level (email, Discord, Telegram, FreeEcho.2) may print; sources limited to READONLY (scheduler, AI-Connect, webhook, unless a job raises its `max_tier`) can only read `printer_status`. See [Security](../../architecture/security.md).
- Only files **inside the local documents area** are printed (`safe_resolve`: no path outside it). Files in other document sources (e.g. Google Drive) are not.
- CUPS prints these types directly: `.pdf`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.ps`. Anything else (Office, HTML, …) is refused with a hint to convert it to PDF first. A test page needs only a short `.txt` file.
- The CUPS jobs run as the user AIfred runs as; no extra rights and no `sudo` are involved.

## Flow

1. The model checks the file name (`list_files`) and calls `print_file` — only when the user asked for it, and only what was asked for.
2. The plugin resolves the file in the documents area, checks the type and the copy count, and picks the printer.
3. `lp -d <printer> -n <copies> [-P <pages>] -o sides=… -- <file>` hands the file to CUPS; the CUPS job id comes back.
4. The model says briefly what was printed on which printer. State and supplies are read with `printer_status` (`ipptool` against `ipp://localhost/printers/<printer>`); on a problem the model names the concrete action (load paper, replace toner).

A scheduler job can poll `printer_status` and notify on any channel when supplies run low.
