# Audio Pipeline Architecture

> **Deutsche Version:** [audio-pipeline.md](../../de/architecture/audio-pipeline.md)

As of: 2026-09-25. Living document — grows along with the implementation.

## Current Implementation Status

| Component | Status | Where |
|---|---|---|
| `audio_player` plugin with 14 tools | ✅ | [aifred/plugins/tools/audio_player/](../../../aifred/plugins/tools/audio_player/) |
| `audio_manager.py` (mpv via JSON-IPC) | ✅ | [aifred/lib/audio_manager.py](../../../aifred/lib/audio_manager.py) |
| `audio_state.py` (JSON position SSOT) | ✅ | [aifred/lib/audio_state.py](../../../aifred/lib/audio_state.py) |
| `audio_sources.py` (folder + HTTP stream) | ✅ | [aifred/lib/audio_sources.py](../../../aifred/lib/audio_sources.py) |
| `audio_index.py` (SQLite/FTS5 for 100k+ files) | ✅ | [aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) |
| Output: local (mpv default → ALSA/Pulse) | ✅ | see `_route_play()` |
| Output: browser (HTML5 `<audio>` + REST) | ✅ | [audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py), [audio.py](../../../aifred/lib/api/audio.py) |
| Output: FreeEcho.2 bridge | ✅ Phase 3.0b | mpv→FIFO→WS pipeline per room, one FreeEcho2Stream instance per target |
| `AudioOutputChannel` protocol (refactor) | ✅ Phase 3.0a | Registry with Local/Browser/FreeEcho.2, all tools channel-based |
| Wake tokens `_pause`/`_resume`/`_standby`/`_activate` | ✅ Phase 3.0c | Server: [freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py); FreeEcho.2 firmware sends `_pause`/`_resume` as WA_PAUSE/WA_RESUME |
| Browser text parser (`_pause`, `_resume`, …) | ❌ Phase 3.0d | currently only via LLM tool call |
| Browser keyboard shortcuts | ❌ Phase 3.0d | UI buttons in the player already exist |
| Room following | ⚠️ Phase 3.0e | `_resume` at a FreeEcho.2 loads the last unfinished item there; stopping it at the previous target is missing |
| YouTube plugin | ❌ Phase 2.0 (after 3.0) | not implemented |
| Internet radio (HTTP streams) | ⚠️ | Infrastructure yes, streams in `settings.json` currently empty |
| Audiobook auto-pause (wake word → pause) | ⚠️ | Browser via `media_paused_for_tts` yes; FreeEcho.2: the TTS reply replaces the music (position via `consumed_ms`), no automatic resume (Phase 4.0) |
| Audio bus refactor (FreeEcho.2 = dumb sink) | ✅ Phase 5.0 | `AudioOrchestrator` per room ([_audio_orchestrator.py](../../../aifred/lib/audio_channels/_audio_orchestrator.py)), `audio_flag` frame; firmware handles `audio_flag` |

## Motivation

The Mini is headless — the primary use case is a user working via
browser, VS Code Server or a FreeEcho.2 in the living room. Local
ALSA output on the Mini does not help then (there is no speaker
attached directly). Audio has to arrive where the request came
from — and the user must be able to redirect it by voice
("play that in the bedroom").

Further requirements:
- **Pause / resume** with position save for long audiobooks (11 h+).
- **Multiple audio sources in parallel:** pause audiobook A, play
  music B, later resume audiobook A exactly where it left off.
- **Multiple outputs in parallel:** FreeEcho.2 living room plays an audiobook, FreeEcho.2
  bedroom plays radio, a browser tab plays YouTube — `Stop` at the
  living-room FreeEcho.2 stops **only** the living-room stream, not the
  others.
- **YouTube / internet streaming** as sources with shared
  position save.
- **Security** — the LLM must not pass arbitrary URLs/paths to the
  audio backend (SSRF + path traversal).

## Guiding Principles

1. **Source/sink separation.** Sources deliver URIs/paths (local files,
   HTTP streams, later YouTube-resolved URLs). `OutputChannel`
   implementations are the sinks — each knows which format it
   needs and how to output to its hardware/UI.
2. **Label-only LLM API.** The LLM knows no raw URLs. It picks from
   source labels the user maintains in the plugin config — which
   rules out SSRF by design.
3. **Central position save.** [data/audio_state.json](../../../data/audio_state.json) is the SSOT for
   all resumes — regardless of which plugin or which output played
   the item.
4. **mpv as engine.** Instead of aplay/ffplay we use mpv with JSON-IPC
   over a Unix socket. That gives us pause/resume/seek/position/
   volume natively, plus HTTP streaming, plus PCM output for streaming
   adapters (FreeEcho.2).
5. **Auto-target from PluginContext.** The default output is the channel
   the request came from. The LLM recognizes a user override by voice
   on its own and passes it on as the `target=` parameter.
6. **Per-channel stop.** A `Stop` wake at a FreeEcho.2 stops only its
   stream. Multiple output streams can run simultaneously and be
   controlled independently.

## Architecture Diagram

```
   ┌─────────────────────┐    ┌──────────────────┐    ┌─────────────────┐
   │ Audio Player Plugin │    │ YouTube Plugin   │    │ Radio/HTTP      │
   │ (local files,       │    │ (yt-dlp →        │    │ (in audio config│
   │  NAS, HTTP streams) │    │  resolved URL)   │    │  as source)     │
   └──────────┬──────────┘    └────────┬─────────┘    └────────┬────────┘
              │                        │                        │
              └────────────────────────┴────────────────────────┘
                                       │
                                       ▼
                            ┌──────────────────────┐
                            │  AudioOutputChannel  │
                            │  Registry            │
                            │                      │
                            │  pick channel by     │
                            │  target_id prefix    │
                            └──────────┬───────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
       ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
       │ LocalChannel    │   │ BrowserChannel  │   │ FreeEcho2Channel     │
       │                 │   │                 │   │                 │
       │ mpv → Pulse/    │   │ HTML5 <audio>   │   │ mpv → 48kHz     │
       │ ALSA            │   │ + /api/audio/   │   │ int16 PCM → FIFO│
       │ (default        │   │ file?key=...    │   │ → FreeEcho2-WS  │
       │  output)        │   │ via Reflex      │   │ (chunked)       │
       │                 │   │ state push      │   │                 │
       └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                      │                      │
              ▼                      ▼                      ▼
        Mini speakers         User's browser tab      FreeEcho.2 in the
        (3.5mm headphones,    (office, living         living room/
         if present)          room, etc.)             bedroom
```

**Important:** The browser channel does **not** do PCM streaming over a
FIFO. The HTML5 `<audio>` component loads the file directly via HTTP
range requests from the REST endpoint `/api/audio/file?key=<state_key>`.
The server only pushes `media_audio_url`/`media_state_key`/
`media_pause_pos_sec` via the Reflex state; the browser takes care of
loading, decoding and playback itself.

## Output Channel Protocol

The Phase 3.0 refactor introduces a generic interface that plugs in
current and future output channels — analogous to the tool/channel
plugin structure.

```python
# aifred/lib/audio_channels/base.py

from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class AudioFormat:
    """Format requirement of the output channel.

    None for the fields the channel does not care about (e.g. mpv default
    accepts everything, hence an empty AudioFormat)."""
    sample_rate: int | None = None      # 48000 for FreeEcho.2
    channels: int | None = None         # 1 for FreeEcho.2
    sample_format: str | None = None    # "s16le" for FreeEcho.2

@dataclass
class TargetInfo:
    id: str               # "local", "browser:abc123", "freeecho2:wohnzimmer"
    label: str            # "Lokale Lautsprecher", "FreeEcho.2 Wohnzimmer"
    ready: bool           # True = hardware/connection available


@runtime_checkable
class AudioOutputChannel(Protocol):
    name: str                          # "local", "browser", "freeecho2"
    required_format: AudioFormat       # what has to be fed in

    def can_handle(self, target_id: str) -> bool: ...
    def list_targets(self, ctx: PluginContext) -> list[TargetInfo]: ...

    async def play(
        self,
        src: ResolvedSource,
        target_id: str,
        start_pos_sec: float | None,
        ctx: PluginContext,
    ) -> dict: ...

    # ctx is only needed by the BrowserChannel (Reflex state push)
    async def pause(self, target_id: str, ctx: PluginContext | None = None) -> bool: ...
    async def resume(self, target_id: str, ctx: PluginContext | None = None) -> bool: ...
    async def stop(self, target_id: str, ctx: PluginContext | None = None) -> bool: ...
    async def seek(self, target_id: str, position_sec: float,
                   relative: bool = False, ctx: PluginContext | None = None) -> bool: ...
    async def set_speed(self, target_id: str, factor: float,
                        ctx: PluginContext | None = None) -> bool: ...
    async def play_queue(self, items: list[dict[str, str]], target_id: str,
                         ctx: PluginContext, audio_type: str = "music",
                         shuffle: bool = False) -> dict: ...
    async def status(self, target_id: str, ctx: PluginContext | None = None) -> dict: ...

    # Optional, for push sinks (FreeEcho.2): app-level flow control
    def supports_flow_control(self) -> bool: ...        # default False
    def notify_flow(self, target_id: str, state: str) -> None: ...   # "pause" | "resume"
    def get_stream_start_offset(self, target_id: str) -> float | None: ...
```

`play_queue()` (sequential folder playback) is implemented by the Browser and
FreeEcho.2 channels; the `LocalChannel` returns "not implemented".

**Registry** (`aifred/lib/audio_channels/__init__.py`):

```python
_REGISTRY: list[AudioOutputChannel] = []

def register(channel: AudioOutputChannel) -> None: ...
def resolve(target_id: str) -> AudioOutputChannel | None:
    for ch in _REGISTRY:
        if ch.can_handle(target_id):
            return ch
    return None

def all_targets(ctx: PluginContext) -> list[TargetInfo]: ...
def all_channels() -> list[AudioOutputChannel]: ...

# Built-in channels register themselves on import:
register(LocalChannel()); register(BrowserChannel()); register(FreeEcho2Channel())
```

**Format requirements per channel:**

| Channel | `required_format` | Conversion by |
|---|---|---|
| `LocalChannel` | empty (mpv eats everything) | mpv internally |
| `BrowserChannel` | empty (HTML5 loads the file directly) | browser internally |
| `FreeEcho2Channel` | `(48000, 1, "s16le")` | mpv itself (`--audio-samplerate`/`--audio-channels`/`--audio-format`), no separate ffmpeg stage |

**Discovery of the concrete targets** is always live — `list_targets()`
queries at call time:
- `LocalChannel` → always `[{"id":"local","ready":True}]`
- `BrowserChannel` → `browser:<ctx.session_id>` of the current request
- `FreeEcho2Channel` → iterates the connected speakers in `freeecho2_channel._devices`
  (defined in `_shared.py`)

`audio_targets()` hides the browser target when the request does not come from
the browser.

This way FreeEcho.2 speakers show up in `audio_targets()` automatically as soon as they
connect via WebSocket — no user configuration needed.

## Plugin Configuration

**Local folder sources are discovered automatically**: every direct subfolder
or symlink under `data/media/audio/` (`MEDIA_AUDIO_DIR`) becomes a
`local_folder` source whose label is the folder name. Symlinks make NAS mounts
transparent. They are maintained in the UI (Plugin Settings → Audio Player: add a
source via file picker = create a symlink, remove it, rebuild/clear the index).
Source map SSOT: `build_configured_source_map()` in
[aifred/lib/audio_player_settings.py](../../../aifred/lib/audio_player_settings.py).

The plugin's [`settings.json`](../../../aifred/plugins/tools/audio_player/settings.json)
only holds the rest (shipped state):

```jsonc
{
  // Only http_stream entries count here — items are referenced via labels,
  // never raw paths/URLs. Example entry:
  //   "swr3": { "type": "http_stream", "url": "https://liveradio.swr.de/sw282p3/swr3/play.mp3" }
  "sources": {},

  // Area the file picker in the settings UI may reach
  "picker": { "root": "/mnt" },

  // Threshold of the TTS list filter (audio_processing.py)
  "tts_list": { "full_max_items": 5 },

  // Resume behavior
  "resume": {
    "pre_roll_sec": 7,
    "pre_roll_for_streams": false,
    "min_audio_duration_for_pre_roll_sec": 60,
    "position_save_interval_sec": 60
  },

  // Output default
  "targets": {
    "default": "auto"   // = derive from PluginContext.source
                        // or "local", "browser:<id>", "freeecho2:wohnzimmer", ...
  }
}
```

A source entry may set `audio_type` explicitly; otherwise it is derived from genre
tag > filename > folder name (`resolve_audio_type()` in `audio_sources.py`).

Live discovery (browser sessions, active FreeEcho.2 speakers) is **not** mirrored into
`settings.json` — it follows at runtime from the channel
registry.

## Tool Inventory (Audio Player Plugin)

14 tools, registered in `get_tools()`. The control tools (play … index_rebuild) are
only listed for requests from `browser` and `freeecho2` (`_CONTROL_SOURCES`);
e-mail, Telegram, Discord and unattended triggers only get the five read tools
(status, list, list_unfinished, targets, search). The control tools are
deliberately `TIER_READONLY` so the FreeEcho.2 channel may call them — the gate is
the source, not the tier.

For `target`, the control tools accept: omitted/`"auto"` = request origin, `"all"` =
every active target, or a concrete id (`"local"`, `"browser:<id>"`,
`"freeecho2:<room>"`).

| Tool | Tier | Purpose |
|---|---|---|
| `audio_play(item, target=None, restart=True)` | READONLY | Start an item. `target=None` → auto from PluginContext. Default `restart=True` starts from the beginning; `restart=False` continues from the saved position. |
| `audio_play_folder(folder, target=None, shuffle=False)` | READONLY | Play a whole folder sequentially in natural order (`CD 1` < `CD 2` < `CD 10`), optionally shuffled. Browser and FreeEcho.2 targets (the local channel has no queue). |
| `audio_pause(target=None)` | READONLY | Pause playback on the target. |
| `audio_resume(item=None, target=None)` | READONLY | Smart resume: three cases automatically — let a paused stream continue, or load a different stream from the position state via `item`, or continue the last-played unfinished item. |
| `audio_stop(target=None)` | READONLY | Stop playback on the target. The position is kept. |
| `audio_seek(position_sec, target=None)` | READONLY | Jump to an absolute position. |
| `audio_skip(delta_sec, target=None)` | READONLY | Relative forward/back. |
| `audio_speed(factor, target=None)` | READONLY | 0.25–4.0× playback speed (mpv resamples correctly). |
| `audio_status(target=None)` | READONLY | State of one target, or of all targets without `target`: running/playing/paused, item, position, duration. |
| `audio_list(source=None, subdir=None, limit=None)` | READONLY | List sources or items within a source. Prefers the SQLite index, falls back to a filesystem walk. |
| `audio_list_unfinished()` | READONLY | Items with a saved position (≠ completed), sorted by date. |
| `audio_targets()` | READONLY | Available output targets with status (live from the channel registry). |
| `audio_search(query, source=None, limit=None)` | READONLY | FTS5 full-text search over artist/album/title/filename, BM25 ranking. Sub-ms even with 100k+ files. |
| `audio_index_rebuild(source=None, force=False)` | WRITE_DATA | Rebuild the index for one or all local_folder sources. Incremental by default (mtime-based), `force=True` re-tags every file. |

## State Schema

[data/audio_state.json](../../../data/audio_state.json) — the SSOT for resume:

```json
{
  "hoerbuecher/Tolkien_HdR_Buch1.mp3": {
    "uri": "/mnt/family-nas/Hoerbuecher/Tolkien_HdR_Buch1.mp3",
    "pos_sec": 15825.3,
    "duration_sec": 39600.0,
    "last_played": "2026-05-04T22:13:00",
    "completed": false
  },
  "youtube:dQw4w9WgXcQ": {
    "uri": "…",
    "pos_sec": 213.0,
    "duration_sec": 213.0,
    "last_played": "2026-05-04T20:00:00",
    "completed": true
  }
}
```

(`youtube:` keys are the planned convention of the YouTube plugin, Phase 2.0.)

Write rules:
- **Only at save points** — the position is read from mpv and written at the save
  points below; every `audio_state.update()` writes the file atomically (tmp + rename).
- **Periodically** as crash insurance against power loss/OOM: position save loop in
  the mpv wrapper. Interval from `settings.json` → `resume.position_save_interval_sec`
  (shipped: 60); the plugin applies it to the local channel before each play
  (`audio_manager.configure_save_interval()`, code default
  `POSITION_SAVE_INTERVAL_SEC = 30` in `audio_manager.py`). FreeEcho.2 streams use
  `DEFAULT_SAVE_INTERVAL_SEC = 60` in `_freeecho2_stream.py`. Values ≤ 0 are ignored.
- **Immediately on pause and stop** — the most important save point, because this
  is where the user's intent to resume is stored.
- On FreeEcho.2 wakes, `consumed_ms` from the puck overwrites the mpv position
  (see "consumed_ms Position Sync").
- On mpv `eof-reached` → `completed: true`, immediately.
- `completed: true` entries older than `AUDIO_STATE_CLEANUP_AGE_DAYS` (config.py, 7)
  are deleted at service start and daily at `GARBAGE_COLLECTION_HOUR`
  (`cleanup_audio_state_task()`).

## Audio Index (SQLite/FTS5)

[aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) maintains a local index over all
items in `local_folder` sources, persisted in `data/audio_index.db` (runtime file):

- **Schema:** `(source, rel_path)` as composite PK, plus
  tag columns (`artist`, `album`, `title`, `year`, `genre`,
  `duration`), `mtime`, `size`.
- **FTS5 virtual table** over the tag columns + `filename` + `rel_path`.
- **Incremental rebuild:** compares mtime, tags only changed
  files (mutagen). Full re-tag with `force=True`.
- **BM25 ranking** in `audio_search()`. Tokens are AND-combined
  as prefixes.

Performance goal: sub-ms search even on NAS mounts with 100k+ files.
Falls back to a filesystem walk if the index for a source is still empty
(with a note in the tool output).

## Auto-Target from PluginContext

`_resolve_target(ctx, requested)` in
[audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py)
(simplified):

```python
def _resolve_target(ctx: PluginContext, requested: str | None) -> str:
    default = load_audio_player_settings().get("targets", {}).get("default", "auto")
    if requested:
        return requested
    if default != "auto":
        return default
    if ctx.source == "browser":
        return f"browser:{ctx.session_id}"
    if ctx.source == "freeecho2":
        room = ctx.metadata.get("room", "")
        if room:
            return f"freeecho2:{room}"
        return "local"   # should not happen — logged as a warning
    return "local"       # text channels (discord/email/telegram), CLI, cron
```

User voice override: the LLM infers from the tool description and
conversation cues which `target` parameter to pass
("play that in the bedroom" → `target="freeecho2:schlafzimmer"`). No
additional code needed.

## Wake-Word Control on the FreeEcho.2

The FreeEcho.2 (firmware) recognizes several wake words onboard. The
wake phrases are **dynamically configurable** via the FreeEcho.2 web UI
(`vosk_mapping` field) — the user freely enters "phrase = token" pairs
without model retraining (Vosk engine).

**Default agent wakes:** `Alfred`, `Sokrates`, `Salomo`, `Pater`,
`Pater_Tack`, `Rabbi`, `Codi`, `HAL9000` and others — address an agent
directly, the server LLM answers the request.

### Command Tokens (with `_` prefix)

Two classes:

**1. Audio output tokens** — control the `AudioOutputChannel` targets:

| Token | Server action | Wake phrase (example) |
|---|---|---|
| `_stop` | cancel the pipeline + `channel.stop(target)` for the sending target | `Bitte Stopp`, `Ruhe bitte` |
| `_pause` | same server reaction as `_stop`: cancel the pipeline + stop the stream; the position is saved before mpv terminates, `_resume` continues from there | `Bitte Pause`, `Halt` |
| `_resume` | cancel the pipeline + smart resume: load the last unfinished item from `audio_state.json` on this FreeEcho.2 (pre-roll 3 s) | `Weiter`, `Fortsetzen` |

**2. FreeEcho.2 lifecycle tokens** — microphone/hardware of the FreeEcho.2 itself, **not**
audio output. They stay internal to `freeecho2_channel` and are not part of the
`AudioOutputChannel` protocol:

| Token | Action | Wake phrase (default) |
|---|---|---|
| `_standby` | FreeEcho.2 microphone soft mute, Vosk only listens for `_activate` | `Entry passiv` |
| `_activate` | Soft mute off | `Entry aktiv` |
| `_done` | Puck acknowledgement: proactive playback (chime + TTS) finished — wakes the alert queue for the next item | — (sent by the firmware) |

`_standby` additionally calls `channel.stop(target)` for its own target
— when the FreeEcho.2 sleeps, its running audio stream should end as
well. But that is an implemented consequence, not a
semantic part of the token.

### Per-Channel Target — No Global Stop

`_stop`/`_pause`/`_resume` at a FreeEcho.2 control **only** the stream
**of that one FreeEcho.2 speaker**. Other output streams keep running. The server
knows the source target (`freeecho2:wohnzimmer`) and specifically calls
`channel.stop("freeecho2:wohnzimmer")`, not a global stop.

Example: the living-room FreeEcho.2 plays an audiobook, the bedroom FreeEcho.2 plays
radio, a browser tab plays music. Whoever says "Bitte Stopp" in the living room
stops only the audiobook — the other two streams keep running.

### Server Implementation Status

All tokens are handled in `_handle_command_token(token, room)` in
[freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py):

| Token | Server handling |
|---|---|
| `_stop` | ✅ `_cancel_pipeline_and_stop_stream()` — cancels the running pipeline task + LLM pipeline, stops only this FreeEcho.2's stream |
| `_pause` | ✅ same as `_stop` (position save in `_cleanup_unlocked` before mpv terminates) |
| `_resume` | ✅ `_cancel_pipeline_for_room()` + `_smart_resume_on_freeecho2()` |
| `_standby` | ✅ same as `_stop` (the soft mute itself is FreeEcho.2-local) |
| `_activate` | ✅ deliberately a no-op on the server (soft mute off is FreeEcho.2-local, no auto-resume) |
| `_done` | ✅ `signal_playback_done(room)` for the alert queue |

## Security Layer

| Threat | Mitigation |
|---|---|
| Path traversal (`audio_play("/etc/passwd.wav")`) | Whitelist of configured paths, `..` in the item string is rejected. Items are resolved against the `sources` map. |
| SSRF via HTTP stream | The LLM knows no raw URLs. URLs only in the plugin config (user-maintained). `audio_play(item="swr3")` → plugin resolves to the configured URL. |
| Internal network probing | Not implemented: configured stream URLs are not checked against private address ranges (planned: block `127.*`, `10.*`, `192.168.*`, `172.16-31.*` with an explicit allowlist). Protection so far: URLs come only from the user-maintained config. |
| Decoder vulnerabilities (manipulated MP3) | mpv runs as a subprocess with user privileges. `apt update` regularly. Optional: `firejail`/`bwrap` sandboxing. |
| DoS via stream | mpv arguments `--demuxer-max-bytes=512MiB`, `--network-timeout=30` (`audio_manager.py`, `_freeecho2_stream.py`). A duration limit does not exist. |
| Prompt injection from external channels | Control tools are only listed for `browser`/`freeecho2` (`_CONTROL_SOURCES`). |
| Credential leak | There are no authenticated audio sources yet; if they come: auth via `credential_broker`, never cookies/headers in plugin code. |

## Phase Plan

### ✅ Phase 1.0 — Core Audio Player (done)

mpv subprocess with JSON-IPC, `audio_state.py` (position save with
periodic loop), `audio_sources.py` (LocalFolder + HttpStream),
`audio_player` plugin with all tools, `settings.json` schema.

### ✅ Phase 1.1 — Browser Output (done, differently than originally planned)

Instead of mpv→FIFO→SSE, a direct REST download was implemented:
- `media_audio_url`, `media_state_key`, `media_pause_pos_sec`,
  `media_paused_for_tts`, `media_queue` in the Reflex state.
- The HTML5 `<audio>` element consumes `/api/audio/file?key=...` via
  HTTP range. A JS hook persists the position on `pause`/`ended`
  events.
- Advantage: no separate streaming path, mpv does not run for the browser.
  The browser decodes on its own.
- Disadvantage: no server-side speed/volume for browser tabs (the
  browser handles that).

### ✅ Phase 1.2 — Auto-Target Routing (done)

`_resolve_target(ctx, requested)` in [audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py).
Originally only `local` + the browser session; since Phase 3.0b `audio_targets()`
also lists the connected FreeEcho.2 speakers, and FreeEcho.2 requests are routed to
`freeecho2:<room>`.

### ✅ Phase 1.3 — Audiobook Workflow (done)

Smart `audio_resume()` (three cases merged), pre-roll for
audiobooks, `audio_list_unfinished()` with date sorting,
`completed` cleanup (service start + daily task). The plugin prompt contains
detailed instructions (see `get_prompt_instructions`).

### ✅ Phase 1.4 — Audio Index (done, originally not in the plan)

SQLite/FTS5 index in `audio_index.py`, tools `audio_search` and
`audio_index_rebuild`. Became necessary to scale to NAS mounts with
tens of thousands of files.

### ❌ Phase 2.0 — YouTube Plugin (open, after Phase 3.0)

**Separate plugin** in `aifred/plugins/tools/youtube/`. Uses the same
`audio_state.json` store as the audio player.

**Code:**
- `youtube_search(query)` — yt-dlp search mode (`yt-dlp ytsearch5:...`)
- `youtube_play(video_id_or_url, target=None)` — yt-dlp resolves the
  audio URL → mpv streams it directly (no full download)
- `youtube_play_search(query, target=None)` — play the top hit directly
- Items in `audio_state.json` with key `youtube:<video_id>`

**Effort:** ~3 h.

### ⚠️ Phase 2.1 — Internet Radio (infrastructure done, empty)

The `http_stream` source type has been fully implemented since Phase 1.0. But:
the shipped `settings.json` currently contains **no** streams.
As soon as the user adds some (or the UI offers a
default list for it), it works out of the box.

**TODO:** Offer example streams in the plugin settings UI as "quick add"
buttons (SWR3, DLF, BBC, …).

### Phase 3.0 — FreeEcho.2 Output + Channel Refactor (before Phase 2)

Five stages — tightly coupled, ~6 h total. Status: **a, b, c done; d open; e partly**.

**✅ 3.0a — Channel protocol refactor** (done):
- New folder `aifred/lib/audio_channels/` with `base.py` (protocol +
  AudioFormat + TargetInfo), `__init__.py` (registry), `local.py`, `browser.py`,
  `freeecho2.py` (fully implemented since 3.0b).
- Replace the existing if-else cascade in `_route_play()` with a registry
  lookup.
- `audio_targets()` iterates the registry, no longer hardcoded.

**✅ 3.0b — FreeEcho2Channel implementation** (done):
- `aifred/lib/audio_channels/_freeecho2_stream.py` new — `FreeEcho2Stream`
  class per target (mpv + FIFO + IPC + reader pump + save loop).
- mpv alone as decoder + resampler:
  ```
  mpv --audio-samplerate=48000 --audio-channels=1 \
      --audio-format=s16 --ao=pcm --ao-pcm-file=<fifo> \
      --ao-pcm-waveheader=no --input-ipc-server=<sock> <source>
  ```
- Bridge: public send methods in the FreeEcho2 channel
  ([ws_bridge.py](../../../aifred/plugins/channels/freeecho2_channel/ws_bridge.py)) —
  `send_audio_flag(room, audio_type, **params)`,
  `send_audio_start(room, total_size=None, channels=1)`,
  `send_audio_chunk(room, data)`, `send_audio_end(room)`, plus
  `send_heartbeat(room)` and `send_done(room, reason=None)`. Shared by TTS
  (`send_reply` via the AudioOrchestrator) and `FreeEcho2Stream`.
- One mpv instance per active FreeEcho.2 target — cleanly isolated,
  multi-room in parallel possible.
- `audio_targets()` shows connected FreeEcho.2 speakers live from
  `freeecho2_channel._devices`.
- Cleanup order: terminate mpv before cancelling the pump (otherwise the read
  hangs blocking in `os.read(fifo)`).

**✅ 3.0c — Wake token server integration** (server done):
- `_handle_command_token(token, room)` in
  [freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py).
- Per target: `_stop` resolves `freeecho2:{room}` via the channel registry and calls
  `channel.stop(target_id)` plus `cancel_pipeline(session_id)` (abort LLM
  inference, other streams remain).
- `_pause`/`_resume` fully wired. `_standby`/`_activate` as well
  (soft mute on standby = local on the FreeEcho.2; the server only stops the stream).
- Firmware: `freeecho2_client.c` maps `_pause`/`_resume` to `WA_PAUSE`/`WA_RESUME`
  and sends them to the server.

**❌ 3.0d — Browser text parser + keyboard shortcuts** (~1 h, open):
- Server-side parser `parse_audio_command(text)` as a new lib helper (proposed:
  `aifred/lib/audio_commands.py` — does not exist yet). Called before handing over to
  the LLM. Not to be confused with the FreeEcho.2 wake tokens, which arrive as
  `wake` frames and are handled in `freeecho2_channel/commands.py`.
  Works the same for **all** channels (browser, Telegram, Discord,
  e-mail).
- Control character convention: `_` (consistent with the FreeEcho.2 tokens).
  Recognized inputs:
  ```
  _pause, _resume, _stop
  _skip 10, _skip -10
  _seek 120
  _vol 50
  ```
- Match only if the entire trimmed, lowercased message matches the pattern
  — no partial matches within sentences.
- Browser keyboard shortcuts in the audio player Reflex component:
  `Space` (toggle pause/resume), `Esc` (stop), `←`/`→` (skip ±10 s),
  `↑`/`↓` (vol ±10). Only active while audio is playing + focus is not in
  the chat input.

**⚠️ 3.0e — Room following** (~1 h, partly):
- Already there: `_resume` at FreeEcho.2 Y loads the last unfinished item from
  `audio_state.json` on Y with 3 s pre-roll (`_smart_resume_on_freeecho2()`). If the
  item was stopped/paused on the previous target, that is effectively room following.
- Missing: a stream still running on the previous target is not stopped.

Original plan:
- `_resume` at FreeEcho.2 Y fetches the last active item to Y, stops it
  at the previous target (position is saved immediately), starts it
  at the new target with a short pre-roll (3 s, because the user acts deliberately).
- Prerequisite: per-target tracking of which item is playing where —
  follows automatically from the channel refactor (each channel
  holds its own active stream state per target).

**Tests:**
- Play an item on the living-room FreeEcho.2
- Auto-target works (FreeEcho.2 request → FreeEcho.2 output)
- Multiple FreeEcho.2 speakers in parallel with different items
- "Bitte Stopp" in the living room stops only the living room
- "Bitte Pause" / "Weiter" work on the FreeEcho.2 and in the browser
- Typed browser input `_pause` pauses without an LLM call
- Audiobook in the living room → walk into the bedroom → "Weiter" → audio
  moves to the bedroom with pre-roll

### ❌ Phase 4.0 — Audiobook Mode with Auto-Pause (open, optional)

On the FreeEcho.2 wake word or when a user request comes in:
audio is paused automatically. After the TTS answer: resumed
automatically. State runs through the existing position save.

**Code:**
- Hook in `_chat_mixin.py` or `freeecho2_channel`:
  - before the LLM call: `audio_channels.pause_for_inference(target)`
  - after TTS ends: `audio_channels.resume_after_inference(target)`
- Shorter pre-roll (3 s is enough, because the user paused deliberately).

Already partly implemented on the browser side: the `media_paused_for_tts`
flag in the state pauses the HTML5 player while TTS is speaking.

**Effort:** ~2 h.

### ❌ Phase 4.1 (optional) — Multiple Output Targets Simultaneously

Example: "Play that in the living room and bedroom in parallel."
Requires mpv multi-output or two mpv instances with
synchronized position save. In the backlog.

## Audio Bus Refactor (FreeEcho.2 = dumb sink) — Phase 5.0

**Status: ✅ implemented (as of 2026-09-25).** Consensus between the AIfred server
instance and the FreeEcho.2 firmware instance (negotiated 2026-05-10 via AI-Connect
using the Salomo principle). Both sides were rebuilt in parallel,
no capability gate, no legacy fallback. Server: `AudioOrchestrator` in
[_audio_orchestrator.py](../../../aifred/lib/audio_channels/_audio_orchestrator.py),
frame senders in `freeecho2_channel/ws_bridge.py`; firmware: `audio_flag` handling
in `freeecho2_client.c`.

### Motivation

Before the refactor, TTS and music on the FreeEcho.2 ran over two separate paths:
- TTS: `send_reply` → `audio_start(speech)` + chunks + `audio_end`
- Music: mpv → FIFO → fifo_pump → continuous `audio_chunk` frames

With TTS during music, the two PCM sources collide at the speaker —
the TTS `audio_start` frame is discarded in the firmware's SPEAKING loop
(it only matches on `"audio_end"`), and the TTS bytes end up in the music source.
Plus: source pre-emption via a new `audio_start` would trigger a DAC
reinit at the speaker → crackle. No clean mechanism for a mid-stream
type switch.

### Architecture

**FreeEcho.2 = dumb sink.** One audio pipeline per session, the server
orchestrates what flows in. New frame type `audio_flag` for
type changes without a stream reset.

### Tuple Whitelist (strict, FATAL on violation)

```
(music                    )
(speech                   )
(tts                      )
(alarm,        with_tts=B )
(notification, with_tts=B )
```

`speech` (audiobook/podcast/reading, voice VU on the puck) is allowed as well; server-side
the whitelist lives in `_AUDIO_TYPE_SCHEMA` in `ws_bridge.py` and is validated
before sending (`ValueError` instead of a firmware FATAL).

Anything outside the whitelist → protocol error, connection close,
FATAL log. Strict: no defaults, no hidden assumptions.

`alarm` and `notification` are structurally identical — the only
difference is which WAV the puck plays locally and which LED
layer becomes active. Both play **once**. Longer or more
intrusive use cases (e.g. an annoying alarm clock) are server-orchestrated:
the caller loops `play_alarm()` several times; when the firmware
acknowledges with `_stop`, the server loop aborts. Full control stays with the server,
the puck stays dumb.

### Behavior per Type

| Type | PCM source | Where stored |
|---|---|---|
| `music` | Server mpv FIFO → WS stream | Server pumps |
| `tts` | Server TTS render → WS stream | Server pumps |
| `alarm` | FreeEcho.2-local WAV (UI-configurable) | Puck-local — no server PCM, play once |
| `alarm` + `with_tts=true` | Local WAV + TTS tail stream | Puck plays 1×, then server TTS |
| `notification` | FreeEcho.2-local WAV (UI-configurable) | Puck-local — no server PCM, play once |
| `notification` + `with_tts=true` | Local WAV + TTS tail stream | Puck plays 1×, then server TTS |

### Frame Sequences (finalized)

| Use case | Frame sequence |
|---|---|
| Music | `audio_flag(music)` → `audio_start` → chunks → `audio_end` |
| TTS standalone | `audio_flag(tts)` → `audio_start` → chunks → `audio_end` |
| alarm without tail | only `audio_flag(alarm, with_tts=false)` — no PCM, no audio_end |
| alarm with tail | `audio_flag(alarm, with_tts=true)` → `audio_flag(tts)` → `audio_start` → chunks → `audio_end` |
| notification without/with tail | analogous |

**No more TTS takeover** (consensus spec 2026-05-10): TTS while music is playing
replaces the music source completely. Music is stopped cleanly (mpv terminate,
audio_end, position via consumed_ms in audio_state.json), TTS starts as a
new standalone source. The user brings the music back via `audio_resume` (the pre-roll
applies at the real listening position). Simplifies the server code massively and
eliminates race conditions between mpv pause/resume and the TTS pump.

**Important**: `audio_flag` and `audio_start` are **separate** frames
with different semantics:
- `audio_flag` = type setting (LED + VU + source behavior)
- `audio_start` = PCM stream setup header (optional `total_size`)

`rate` is not sent — the puck hardware is fixed at 48 kHz int16. `audio_start`
does carry `channels` (1 or 2); currently all streams are mono
(`_CHANNELS_PER_TYPE` in `_freeecho2_stream.py`, stereo for music/speech reverted
on 2026-05-31 because of puck problems).

### Four Operations — Semantically Clear per Type

| Operation | music | tts (standalone) | alarm (also +tail) | notification (also +tail) |
|---|---|---|---|---|
| **Play** | mpv start, audio_flag, audio_start, pump | TTS render, audio_flag, audio_start, pump (replaces any running music) | audio_flag with params (no PCM) | audio_flag with params (no PCM) |
| **Pause** | real pause (mpv IPC, position is kept) | real pause (remember TTS cursor) | = stop (everything gone) | = stop (everything gone) |
| **Resume** | mpv IPC unpause, keep pumping | keep pumping from cursor, **no** re-render | (n/a, since stop) | (n/a, since stop) |
| **Stop** | everything gone, mpv terminate | everything gone, drop TTS buffer | everything gone, audio_end to puck | everything gone, audio_end to puck |

**Logic**: music and standalone TTS are classic audio content
(audiobook / longer answer) — pause/resume makes sense. alarm and
notification are event-driven transient audio — pausing an
alarm clock makes no sense, → stop.

### Persistence

Only `music` persists in `audio_state.json` (for long-form resume
after a server restart, audiobooks 11 h+). TTS, alarm, notification live
in memory only — aborted on server restart (an alarm clock that still rings after
a restart would be a bug, not a feature).

### Server-Side SSOT API

```python
async def play_music(stream)                        # register music stream
async def play_tts(pcm_data)                        # TTS standalone (replaces music)
async def play_alarm(with_tts, tts_pcm=None)        # puck-local + opt. TTS tail
async def play_notification(with_tts, tts_pcm=None) # analogous
async def pause()                                   # type-aware
async def resume()
async def stop()                                    # discard everything
```

Methods of `AudioOrchestrator`; they act on the "currently active audio source" per room. The AudioOrchestrator
has exactly one active source (no source stack). A type change (e.g.
music → TTS) means: end the old source cleanly, start the new one.

### silent_reply — Smart Speaker UX

Audio tools (`audio_play`, `audio_play_folder`, `audio_resume`) set
`silent_reply: true` in their tool result. The channel's `send_reply`
reads it from `outbound.metadata` and skips TTS render+send completely.
Music starts immediately, without the butler explaining what he is doing. The reply text
stays visible in the chat log for the browser UI.

Path: tool result JSON → `llm_pipeline.run_llm_stream` parses silent_reply →
`PipelineResult.silent_reply` → `llm_engine` sets `metadata_dict["silent_reply"]` →
`message_processor` sets `outbound.metadata["silent_reply"]` →
`freeecho2_channel.send_reply` → early return.

### consumed_ms Position Sync

With a large puck ring buffer, `mpv-time-pos` (decode position) is well ahead of
the user's real listening position. The puck tracks
`consumed_frames_since_stream_start` and sends in the wake frame:

```json
{"type":"wake","room":"...","agent":"...","consumed_ms":12345}
```

The server uses it to overwrite `audio_state.json[key].pos_sec` **after** the
stream stop (the mpv-time-pos save comes first from `_cleanup_unlocked`,
consumed_ms then overwrites it). The existing pre-roll in
`audio_resume` (default 7s) thus applies at the real listening position.

### Firmware-Side SSOT API (FreeEcho.2)

- `apply_audio_type(t)` — atomically VU mode, LED pattern, AEC reset,
  Vosk permissive, BT tap (all 5 sub-points)
- `audio_source_stream_flush_with_fade(src, 30 ms)` — clean
  type switch without DAC crackle
- `audio_pause_current()` / `audio_resume_current()` — type-aware
  (alarm/notification → stop instead of pause)
- `audio_stop_all()` — total abort incl. TTS tail drain
- audio_flag frame handler with strict whitelist validation
- alarm/notification sequencer: play the local WAV once, optionally
  a TTS tail. The loop for the "intrusive alarm clock" use case is handled by the
  server (play_alarm() several times), not the firmware.

### LED Pattern Extension (Firmware)

New audio layer patterns in `freeecho2_ledd.c`:
- `audio:alarm` → orange, fast pulsing (default `solid:FF8000+pulse:300`)
- `audio:notification` → soft cyan, slow (default `solid:00C8FF+pulse:1500`)

Configurable via UI: local WAV files for alarm/notification
plus LED patterns.

### Implementation Phases (Server Side)

1. ✅ **Phase 1**: Frame sender (`send_audio_flag`, slim
   `send_audio_start`) in the plugin layer (`ws_bridge.py`)
2. ✅ **Phase 2**: Stateful AudioOrchestrator per room with music stream
   and TTS buffer as two pump sources
3. ✅ **Phase 3**: `send_reply` (`freeecho2_channel/tts_reply.py`) plays TTS via
   `orc.play_tts()` — the old `pause_for_tts`/`resume_after_tts` pattern is gone
4. ✅ **Phase 4** (solved differently): no `audio_alarm`/`audio_notification` tools;
   instead the `freeecho2_announce` tool of the FreeEcho.2 channel (`audio_type`
   `notification`/`alarm`) and proactive pushes (e.g. vision alerts) go through
   the alert queue (`alert_queue.py`) → `orc.play_alarm()`/`orc.play_notification()`

## Open Questions

1. **Auto-discovery of active FreeEcho.2 speakers for `audio_targets()`.** ✅ Solved:
   `FreeEcho2Channel.list_targets()` iterates `freeecho2_channel._devices` live.

2. **Audiobook mode during TTS.** ✅ Decided (Phase 5.0, "no TTS takeover"): TTS on
   the FreeEcho.2 replaces the music source — `play_tts()` ends the music cleanly,
   the position comes from `consumed_ms`, the user continues via `audio_resume`.
   An automatic resume after the TTS (Phase 4.0) does not exist.

3. **Browser tab closed / inactive.** If the user closes the browser
   while an audiobook is playing, AIfred should:
   a) Pause and save the position? (user comes back later)
   b) Keep playing? (no listener, pointless)
   c) Migrate to the FreeEcho.2? (smart, but complex)
   For now: pause on a "browser disconnected" event.

4. **Multi-stream state per target.** ✅ Decided in Phase 3.0b: one mpv instance per
   FreeEcho.2 target (`FreeEcho2Stream`, cleanly isolated). `audio_manager.py`
   remains the singleton for the local output only.

5. **Re-activate `_tool_index_clear`?** ✅ Settled: the tool no longer exists.
   Clearing the index per source is done in the settings UI
   (`audio_index_clear_source`).
