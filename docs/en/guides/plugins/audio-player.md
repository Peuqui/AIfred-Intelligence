# Audio Player Plugin

**File:** `aifred/plugins/tools/audio_player/`

Playback control for local audio files (folders mounted via NAS or local disk)
and HTTP streams (internet radio), with pause/resume and position saving. The
LLM never sees raw paths or URLs — only labels defined in `settings.json`; this
is a deliberate SSRF / path-traversal guard (see
`docs/de/architecture/audio-pipeline.md`).

Phase 1.0 ships local playback; browser and FreeEcho.2 output adapters are
routed through an `AudioOutputChannel` registry.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `audio_play` | Play an item (`label/file.mp3` or a stream `label`) from the beginning; `restart=false` resumes from saved position | READONLY |
| `audio_play_folder` | Queue and play all audio files in a folder sequentially (natural sort), optionally shuffled | READONLY |
| `audio_pause` | Pause a target (auto / specific id / `all`); position is saved | READONLY |
| `audio_resume` | Resume: unpause, resume a specific `item`, or pick up the most recent unfinished audio | READONLY |
| `audio_stop` | Stop a target (auto / specific id / `all`); position is saved | READONLY |
| `audio_seek` | Seek to an absolute position in seconds | READONLY |
| `audio_skip` | Skip forward/backward by N seconds relative to current position | READONLY |
| `audio_speed` | Set playback speed multiplier (0.25–4.0) | READONLY |
| `audio_status` | Query playback state per target (all targets, or one) | READONLY |
| `audio_list` | List configured sources, or items inside a source folder | READONLY |
| `audio_list_unfinished` | List items with a saved position that are not yet completed | READONLY |
| `audio_targets` | List available output targets (local, browser tab, FreeEcho.2 rooms) | READONLY |
| `audio_search` | BM25 full-text search over the audio index (artist/album/title/filename/path) | READONLY |
| `audio_index_rebuild` | (Re)build the SQLite/FTS5 index for one or all local-folder sources | WRITE_DATA |

Playback is treated as operational, not destructive, so the play/pause/stop
tools are `READONLY` — this lets the FreeEcho.2 voice channel (which runs at the
`COMMUNICATE` tier) invoke them. Only `audio_index_rebuild`, which writes the
index, is `WRITE_DATA`.

## Targets and routing

Every tool takes an optional `target` parameter:

- **Omitted** → auto-route to where the request came from (FreeEcho.2 wake → that
  room, browser input → that tab, text channels → server-side `local`). This is
  the common case.
- A specific id like `freeecho2:wohnzimmer`, `browser:<id>` or `local`.
- `all` (for `audio_pause` / `audio_stop`) → every active stream across all
  channels.

Call `audio_targets()` to list valid ids. The default routing behaviour can be
pinned in `settings.json` under `targets.default` (default `"auto"`).

## Discovery and search

- `audio_list()` (no args) lists configured sources with per-source item counts.
- `audio_list(source='music', subdir='Klassik/Mozart')` lists items, preferring
  the SQLite index and falling back to a filesystem walk if the source is not
  indexed yet.
- `audio_search(query='mozart sonate')` does sub-millisecond BM25 search across
  ID3/FLAC/Vorbis tags, filenames and paths even on 100k+ file NAS mounts.
  Tokens are AND-combined as prefix matches. It returns `state_key` values you
  pass straight to `audio_play`.

Source labels and paths are **case-sensitive** for `audio_list` / `audio_play`;
`audio_search` is case-insensitive.

## Resume

Position is stored in `audio_state.json`, so long audiobooks survive pauses,
restarts and other media in between. `audio_resume` auto-selects between three
behaviours: simple unpause, resume a specific `item`, or pick up the most recent
unfinished entry — with a short pre-roll for audiobooks.

## Configuration

`settings.json` (in the plugin directory):

- `sources` — labelled audio sources. Local folders are discovered under
  `MEDIA_AUDIO_DIR`; entries with `type: "http_stream"` define radio streams.
- `targets.default` — `"auto"` or a fixed target id.
- `resume` — `pre_roll_sec`, `pre_roll_for_streams`,
  `min_audio_duration_for_pre_roll_sec`, `position_save_interval_sec`.
- `list` / `tts_list` — listing limits.
- `limits` — `max_duration_min`, `max_buffer_mb`, `connect_timeout_sec`,
  `read_timeout_sec`.

The Plugin tab's gear icon opens a custom settings modal
(`settings_event_name = "open_audio_settings"`).
