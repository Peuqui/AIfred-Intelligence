# Audio-Pipeline-Architektur

Stand: 2026-05-05. Lebendes Dokument — wird mit der Implementierung
weiter ausgebaut.

## Aktueller Implementierungs-Stand

| Komponente | Status | Wo |
|---|---|---|
| `audio_player`-Plugin mit 15 Tools | ✅ | [aifred/plugins/tools/audio_player/](../../../aifred/plugins/tools/audio_player/) |
| `audio_manager.py` (mpv via JSON-IPC) | ✅ | [aifred/lib/audio_manager.py](../../../aifred/lib/audio_manager.py) |
| `audio_state.py` (JSON-Position-SSOT) | ✅ | [aifred/lib/audio_state.py](../../../aifred/lib/audio_state.py) |
| `audio_sources.py` (Folder + HTTP-Stream) | ✅ | [aifred/lib/audio_sources.py](../../../aifred/lib/audio_sources.py) |
| `audio_index.py` (SQLite/FTS5 für 100k+ Files) | ✅ | [aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) |
| Output: lokal (mpv default → ALSA/Pulse) | ✅ | siehe `_route_play()` |
| Output: Browser (HTML5 `<audio>` + REST) | ✅ | [audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py), [api.py](../../../aifred/lib/api.py) |
| Output: Puck (FreeEcho.2-Bridge) | ✅ Phase 3.0b | mpv→FIFO→WS-Pipeline pro Raum, eine PuckStream-Instanz pro Target |
| `AudioOutputChannel`-Protokoll (Refactor) | ✅ Phase 3.0a | Registry mit Local/Browser/Puck, alle Tools kanalbasiert |
| Wake-Tokens `_pause`/`_resume`/`_standby`/`_activate` | ⚠️ Phase 3.0c (Server done) | Server fertig; Puck-Code muss `_pause`/`_resume` noch als WA_PAUSE/WA_RESUME aufnehmen |
| Browser-Text-Parser (`_pause`, `_resume`, …) | ❌ Phase 3.0d | aktuell nur via LLM-Tool-Call |
| Browser-Keyboard-Shortcuts | ❌ Phase 3.0d | UI-Buttons im Player gibt es schon |
| Room-Following | ❌ Phase 3.0e | hängt an Channel-Refactor |
| YouTube-Plugin | ❌ Phase 2.0 (nach 3.0) | nicht implementiert |
| Internet-Radio (HTTP-Streams) | ⚠️ | Infrastruktur ja, Streams in `settings.json` aktuell leer |
| Hörbuch-Auto-Pause (Wake-Word → Pause) | ⚠️ | Browser via `media_paused_for_tts` ja, Puck nein (Phase 4.0) |

## Motivation

Der Mini ist headless — der primäre Use-Case ist ein User der per
Browser, VS-Code-Server oder per Puck im Wohnzimmer arbeitet. Lokaler
ALSA-Output am Mini hilft dann nicht (es ist gar kein Lautsprecher
direkt angeschlossen). Audio muss dort ankommen, wo die Anfrage
herkam — und vom User per Sprache umgelenkt werden können
(„spiel das im Schlafzimmer").

Weiteres Pflichtenheft:
- **Pause / Resume** mit Position-Save für lange Hörbücher (11 h+).
- **Mehrere Audio-Quellen parallel:** Hörbuch A pausieren, Musik B
  spielen, später Hörbuch A genau dort fortsetzen.
- **Mehrere Outputs parallel:** Puck Wohnzimmer spielt Hörbuch, Puck
  Schlafzimmer spielt Radio, Browser-Tab spielt YouTube — `Stopp` am
  Wohnzimmer-Puck stoppt **nur** den Wohnzimmer-Stream, nicht die
  anderen.
- **YouTube / Internet-Streaming** als Sources mit gemeinsamem
  Position-Save.
- **Sicherheit** — die LLM darf keine willkürlichen URLs/Pfade ans
  Audio-Backend übergeben (SSRF + Path-Traversal).

## Leitprinzipien

1. **Source/Sink-Trennung.** Sources liefern URIs/Pfade (lokale Files,
   HTTP-Streams, später YouTube-resolved URLs). `OutputChannel`-
   Implementierungen sind die Sinks — jede weiß welches Format sie
   braucht und wie sie an ihre Hardware/UI ausgibt.
2. **Label-only LLM-API.** Die LLM kennt keine raw URLs. Sie wählt aus
   Source-Labels die der User in der Plugin-Config gepflegt hat — damit
   ist SSRF systembedingt ausgeschlossen.
3. **Position-Save zentral.** [data/audio_state.json](../../../data/audio_state.json) ist die SSOT für
   alle Resumes — egal welches Plugin oder welcher Output das Item
   gespielt hat.
4. **mpv als Engine.** Statt aplay/ffplay nutzen wir mpv mit JSON-IPC
   über einen Unix-Socket. Das gibt uns Pause/Resume/Seek/Position/
   Volume nativ, plus HTTP-Streaming, plus PCM-Output für Streaming-
   Adapter (Puck).
5. **Auto-Target aus PluginContext.** Default-Output ist der Channel
   woher die Anfrage kam. User-Override per Sprache erkennt die LLM
   selbständig und gibt es als `target=`-Parameter weiter.
6. **Per-Channel-Stop.** `Stopp`-Wake an einem Puck stoppt nur dessen
   Stream. Mehrere Output-Streams können gleichzeitig laufen und
   unabhängig gesteuert werden.

## Architektur-Diagramm

```
   ┌─────────────────────┐    ┌──────────────────┐    ┌─────────────────┐
   │ Audio-Player Plugin │    │ YouTube Plugin   │    │ Radio/HTTP      │
   │ (lokale Files,      │    │ (yt-dlp →        │    │ (in audio-config│
   │  NAS, HTTP-Streams) │    │  resolved URL)   │    │  als Source)    │
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
       │ LocalChannel    │   │ BrowserChannel  │   │ PuckChannel     │
       │                 │   │                 │   │                 │
       │ mpv → Pulse/    │   │ HTML5 <audio>   │   │ ffmpeg → 48kHz  │
       │ ALSA            │   │ + /api/audio/   │   │ mono int16 PCM  │
       │ (default        │   │ file?key=...    │   │ → FreeEcho2-WS  │
       │  output)        │   │ via Reflex-     │   │ (chunked)       │
       │                 │   │ State-Push      │   │                 │
       └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                      │                      │
              ▼                      ▼                      ▼
        Mini-Lautsprecher     Browser-Tab des         Puck im
        (3.5mm Kopfhörer,     Users (Office,           Wohnzimmer/
         falls vorhanden)     Wohnzimmer, etc.)        Schlafzimmer
```

**Wichtig:** Der Browser-Channel macht **kein** PCM-Streaming über
FIFO. Die HTML5 `<audio>`-Komponente lädt die Datei direkt per HTTP-
Range-Request vom REST-Endpoint `/api/audio/file?key=<state_key>`.
Der Server pusht nur `media_audio_url`/`media_state_key`/
`media_pause_pos_sec` über den Reflex-State; der Browser kümmert sich
selbst ums Laden, Decodieren und Abspielen.

## Output-Channel-Protokoll

Der Refactor in Phase 3.0 führt eine generische Schnittstelle ein, die
heutige und künftige Ausgabekanäle einbindet — analog zur Tool/Channel-
Plugin-Struktur.

```python
# aifred/lib/audio_channels/base.py

from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class AudioFormat:
    """Format-Anforderung des Output-Channels.

    None für die Felder die der Channel egal sind (z.B. mpv-default
    akzeptiert alles, also leeres AudioFormat)."""
    sample_rate: int | None = None      # 48000 für Puck
    channels: int | None = None         # 1 für Puck
    sample_format: str | None = None    # "s16le" für Puck

@dataclass
class TargetInfo:
    id: str               # "local", "browser:abc123", "freeecho2:wohnzimmer"
    label: str            # "Lokale Lautsprecher", "Puck Wohnzimmer"
    ready: bool           # True = Hardware/Connection verfügbar


@runtime_checkable
class AudioOutputChannel(Protocol):
    name: str                          # "local", "browser", "puck"
    required_format: AudioFormat       # was muss reingegeben werden

    def can_handle(self, target_id: str) -> bool: ...
    def list_targets(self, ctx: PluginContext) -> list[TargetInfo]: ...

    async def play(
        self,
        src: ResolvedSource,
        target_id: str,
        start_pos_sec: float | None,
        ctx: PluginContext,
    ) -> dict: ...

    async def pause(self, target_id: str) -> None: ...
    async def resume(self, target_id: str) -> None: ...
    async def stop(self, target_id: str) -> None: ...
    async def seek(self, target_id: str, position_sec: float) -> None: ...
    async def set_speed(self, target_id: str, factor: float) -> None: ...
```

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
```

**Format-Anforderungen pro Channel:**

| Channel | `required_format` | Konversion durch |
|---|---|---|
| `LocalChannel` | leer (mpv frisst alles) | mpv intern |
| `BrowserChannel` | leer (HTML5 lädt Datei direkt) | Browser intern |
| `PuckChannel` | `(48000, 1, "s16le")` | ffmpeg-Subprozess |

**Discovery der konkreten Targets** ist immer live — `list_targets()`
fragt zur Aufrufzeit:
- `LocalChannel` → immer `[{"id":"local","ready":True}]`
- `BrowserChannel` → liest die aktive Reflex-Session aus `ctx`
- `PuckChannel` → iteriert `freeecho2_channel._devices.keys()`

Damit erscheinen Pucks automatisch in `audio_targets()`, sobald sie
sich per WebSocket verbinden — keine User-Konfiguration nötig.

## Plugin-Konfiguration

UI-pflegbar (Plugin-Settings → Audio Player). Beispiel-`settings.json`:

```jsonc
{
  // Quellen — Items werden über Labels referenziert, nie raw Pfade/URLs.
  "sources": {
    "alarms":      { "type": "local_folder", "path": "/home/mp/Audio/wecker" },
    "music":       { "type": "local_folder", "path": "/home/mp/Audio/musik" },
    "hoerbuecher": { "type": "local_folder", "path": "/mnt/family-nas/Hoerbuecher" },
    "sandbox":     { "type": "local_folder", "path": "./data/sandbox_output" },
    "swr3":        { "type": "http_stream", "url": "https://liveradio.swr.de/sw282p3/swr3/play.mp3" },
    "dlf":         { "type": "http_stream", "url": "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3" }
  },

  // Resume-Verhalten
  "resume": {
    "pre_roll_sec": 7,
    "pre_roll_for_streams": false,
    "min_audio_duration_for_pre_roll_sec": 60,
    "position_save_interval_sec": 30
  },

  // Output-Default
  "targets": {
    "default": "auto"   // = aus PluginContext.source ableiten
                        // oder "local", "browser", "freeecho2:wohnzimmer", ...
  },

  // Stream-Limits (DOS-Schutz für HTTP-Sources)
  "limits": {
    "max_duration_min": 240,
    "max_buffer_mb": 512,
    "connect_timeout_sec": 10,
    "read_timeout_sec": 30
  }
}
```

Live-Discovery (Browser-Sessions, aktive Pucks) wird **nicht** in
`settings.json` gespiegelt — das ergibt sich zur Laufzeit aus dem
Channel-Registry.

## Tool-Inventar (Audio-Player Plugin)

15 Tools, registriert in `get_tools()`:

| Tool | Tier | Zweck |
|---|---|---|
| `audio_play(item, target=None, restart=False)` | WRITE_DATA | Item starten. `target=None` → Auto aus PluginContext. `restart=True` ignoriert Position-State. |
| `audio_play_folder(folder, target=None, restart=False)` | WRITE_DATA | Ganzen Ordner sequenziell in natürlicher Reihenfolge (`CD 1` < `CD 2` < `CD 10`) abspielen. Aktuell nur `target='browser'`. |
| `audio_pause()` | READONLY | Aktuelle Wiedergabe pausieren. Position wird gespeichert. |
| `audio_resume(item=None, target=None)` | WRITE_DATA | Smart-Resume: drei Cases automatisch — gepausten Stream weiterlaufen lassen, oder per `item` einen anderen Stream aus Position-State laden, oder zuletzt-gespieltes Unfinished-Item fortsetzen. |
| `audio_stop()` | READONLY | Wiedergabe stoppen (browser + lokal). Position bleibt erhalten. |
| `audio_seek(position_sec)` | READONLY | Absolute Position springen. |
| `audio_skip(delta_sec)` | READONLY | Relativ vor/zurück. |
| `audio_speed(factor)` | READONLY | 0.25–4.0× Playback-Speed (mpv resampled korrekt). |
| `audio_status()` | READONLY | Aktueller Zustand: running/playing/paused, Item, Position, Duration, Speed. |
| `audio_list(source=None, subdir=None, limit=None)` | READONLY | Sources auflisten oder Items in einer Source. Bevorzugt SQLite-Index, fällt zurück auf Filesystem-Walk. |
| `audio_list_unfinished()` | READONLY | Items mit gespeicherter Position (≠ completed), nach Datum sortiert. |
| `audio_targets()` | READONLY | Verfügbare Output-Targets mit Status (live aus Channel-Registry). |
| `audio_search(query, source=None, limit=None)` | READONLY | FTS5-Volltextsuche über Artist/Album/Title/Filename, BM25-Ranking. Sub-ms auch bei 100k+ Files. |
| `audio_index_rebuild(source=None, force=False)` | WRITE_DATA | Index für eine oder alle local_folder-Sources neu aufbauen. Default incremental (mtime-basiert), `force=True` re-tagged jede Datei. |

## State-Schema

[data/audio_state.json](../../../data/audio_state.json) — die SSOT für Resume:

```json
{
  "hoerbuecher/Tolkien_HdR_Buch1.mp3": {
    "pos_sec": 15825.3,
    "duration_sec": 39600,
    "last_played": "2026-05-04T22:13:00",
    "source_label": "hoerbuecher",
    "completed": false
  },
  "youtube:dQw4w9WgXcQ": {
    "pos_sec": 213,
    "duration_sec": 213,
    "last_played": "2026-05-04T20:00:00",
    "source_label": "youtube",
    "completed": true
  }
}
```

Schreibregeln:
- **RAM-only zwischen den Save-Points** — laufende Position lebt im
  AudioState-Singleton, kein Disk-Hit pro Update.
- **Periodisch alle 60 s** als Crash-Insurance gegen Power-Loss/OOM
  (konfigurierbar via `AUDIO_POSITION_SAVE_INTERVAL_SEC` in `config.py`,
  Default 60). Setzt man auf 0 → kein periodisches Save.
- **Bei `audio_pause()` und `audio_stop()` sofort** — der wichtigste
  Save-Point, weil hier der User-Intent zum Resume gespeichert wird.
- Bei mpv `eof-reached` → `completed: true`, sofort.
- Beim nächsten Plugin-Start: `completed: true`-Einträge älter als
  `AUDIO_STATE_CLEANUP_AGE_DAYS` werden gelöscht.

## Audio-Index (SQLite/FTS5)

[aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) führt einen lokalen Index über alle
Items in `local_folder`-Sources, persistiert in [data/audio_index.db](../../../data/audio_index.db):

- **Schema:** `(source, rel_path)` als zusammengesetzter PK, plus
  Tag-Spalten (`artist`, `album`, `title`, `year`, `genre`,
  `duration`), `mtime`, `size`.
- **FTS5-Virtual-Table** über Tag-Spalten + `filename` + `rel_path`.
- **Incremental-Rebuild:** vergleicht mtime, taggt nur geänderte
  Dateien (mutagen). Kompletter Re-Tag mit `force=True`.
- **BM25-Ranking** in `audio_search()`. Tokens werden AND-kombiniert
  als Prefix.

Performance-Ziel: sub-ms-Suche auch bei NAS-Mounts mit 100k+ Files.
Fallback auf Filesystem-Walk wenn der Index für eine Source noch leer
ist (mit Hinweis im Tool-Output).

## Auto-Target aus PluginContext

```python
def _resolve_target(ctx: PluginContext, requested: str | None) -> str:
    if requested:
        return requested
    cfg_default = settings["targets"]["default"]
    if cfg_default != "auto":
        return cfg_default
    if ctx.source == "browser":
        return f"browser:{ctx.session_id}"
    if ctx.source == "freeecho2":
        return f"freeecho2:{ctx.metadata['room']}"   # ← Phase 3.0
    if ctx.source in ("discord", "email", "telegram"):
        return "local"   # Text-Channels — Audio dort nicht möglich
    return "local"
```

User-Sprach-Override: LLM erkennt aus Tool-Description und
Konversations-Hinweisen, welchen `target`-Parameter sie übergeben muss
(„spiel das im Schlafzimmer" → `target="freeecho2:schlafzimmer"`). Kein
zusätzlicher Code nötig.

## Wake-Word-Steuerung am Puck

Der Puck (FreeEcho.2-Firmware) erkennt onboard mehrere Wake-Words. Die
Wake-Phrasen sind **dynamisch konfigurierbar** über das Puck-Web-UI
(`vosk_mapping`-Feld) — der User trägt frei „Phrase = Token" Paare ein,
ohne Modell-Retraining (Vosk-Engine).

**Default-Agenten-Wakes:** `Alfred`, `Sokrates`, `Salomo`, `Pater`,
`Pater_Tack`, `Rabbi`, `Codi`, `HAL9000` u.a. — sprechen einen Agent
direkt an, der Server-LLM beantwortet die Anfrage.

### Command-Tokens (mit `_`-Prefix)

Zwei Klassen:

**1. Audio-Output-Tokens** — steuern die `AudioOutputChannel`-Targets:

| Token | Server-Action | Wake-Phrase (Beispiel) |
|---|---|---|
| `_stop` | `channel.stop(target)` für sendendes Target | `Bitte Stopp`, `Ruhe bitte` |
| `_pause` *(Phase 3.0c)* | `channel.pause(target)` | `Bitte Pause`, `Halt` |
| `_resume` *(Phase 3.0c)* | `channel.resume(target)` (Smart: unpause oder last-unfinished) | `Weiter`, `Fortsetzen` |

**2. Puck-Lifecycle-Tokens** — Mikrofon/Hardware des Pucks selbst, **kein**
Audio-Output. Bleiben in `freeecho2_channel` intern, sind nicht im
`AudioOutputChannel`-Protocol:

| Token | Action | Wake-Phrase (Default) |
|---|---|---|
| `_standby` *(Phase 3.0c)* | Puck-Mikrofon Soft-Mute, Vosk lauscht nur auf `_activate` | `Entry passiv` |
| `_activate` *(Phase 3.0c)* | Soft-Mute aus | `Entry aktiv` |

`_standby` ruft zusätzlich `channel.stop(target)` für das eigene Target
auf — wenn der Puck schläft, soll auch sein laufender Audio-Stream
beendet sein. Aber das ist eine implementierte Konsequenz, nicht
semantischer Teil des Tokens.

### Per-Channel-Target — kein globaler Stop

`_stop`/`_pause`/`_resume` an einem Puck steuern **nur** den Stream
**dieses einen Pucks**. Andere Output-Streams laufen weiter. Der Server
kennt das Quell-Target (`freeecho2:wohnzimmer`) und ruft gezielt
`channel.stop("freeecho2:wohnzimmer")` auf, nicht `stop_all()`.

Beispiel: Wohnzimmer-Puck spielt Hörbuch, Schlafzimmer-Puck spielt
Radio, Browser-Tab läuft Musik. Wer im Wohnzimmer „Bitte Stopp" sagt,
stoppt nur das Hörbuch — die anderen zwei Streams laufen weiter.

### Server-Implementierungs-Status

| Token | Server-Verarbeitung |
|---|---|
| `_stop` | ✅ implementiert ([freeecho2_channel:243](../../../aifred/plugins/channels/freeecho2_channel/__init__.py#L243)) — cancelt LLM-Pipeline + Browser-Audio. Phase 3.0c: zusätzlich per-Target Stop. |
| `_pause` | ❌ Phase 3.0c |
| `_resume` | ❌ Phase 3.0c |
| `_standby` | ❌ Phase 3.0c (Code: „reserved for future use; ignored for now") |
| `_activate` | ❌ Phase 3.0c |

## Sicherheits-Layer

| Bedrohung | Mitigation |
|---|---|
| Path-Traversal (`audio_play("/etc/passwd.wav")`) | Whitelist konfigurierter Pfade, `..` in Item-String wird abgelehnt. Items werden gegen die `sources`-Map resolved. |
| SSRF via HTTP-Stream | LLM kennt keine raw URLs. URLs nur in Plugin-Config (User-pflegbar). `audio_play(item="swr3")` → Plugin resolved zu der konfigurierten URL. |
| Internal-Network-Probing | Wenn doch URLs in der Config: blocke `127.*`, `10.*`, `192.168.*`, `172.16-31.*` außer explizit in `internal_allowed_hosts` whitelisted. |
| Decoder-Lücken (manipulierte MP3) | mpv läuft als Subprocess mit User-Privilegien. `apt update` regelmäßig. Optional: `firejail`/`bwrap`-Sandboxing. |
| DOS via Stream | `--demuxer-max-bytes=512MiB`, `--network-timeout=30`, `max_duration_min` Limit. |
| Credential-Leak | Auth über `credential_broker` — nie Cookies/Headers im Plugin-Code. |

## Phasen-Plan

### ✅ Phase 1.0 — Core Audio-Player (done)

mpv-Subprocess mit JSON-IPC, `audio_state.py` (Position-Save mit
periodischem Loop), `audio_sources.py` (LocalFolder + HttpStream),
`audio_player`-Plugin mit allen Tools, `settings.json`-Schema.

### ✅ Phase 1.1 — Browser-Output (done, anders als ursprünglich geplant)

Statt mpv→FIFO→SSE wurde direkter REST-Download umgesetzt:
- `media_audio_url`, `media_state_key`, `media_pause_pos_sec`,
  `media_paused_for_tts`, `media_queue` im Reflex-State.
- HTML5 `<audio>`-Element konsumiert `/api/audio/file?key=...` per
  HTTP-Range. JS-Hook persistiert die Position bei `pause`/`ended`-
  Events.
- Vorteil: kein separater Streaming-Pfad, mpv läuft nicht für Browser.
  Der Browser dekodiert selbst.
- Nachteil: kein Server-seitiges Speed/Volume für Browser-Tabs (das
  macht der Browser).

### ✅ Phase 1.2 — Auto-Target-Routing (done)

`_resolve_target(ctx, requested)` in [audio_player/__init__.py:55-82](../../../aifred/plugins/tools/audio_player/__init__.py#L55).
`audio_targets()`-Tool listet aktuell nur `local` + Browser-Session.
Puck-Auto-Routing ist als TODO markiert (fällt auf `local` zurück).

### ✅ Phase 1.3 — Hörbuch-Workflow (done)

Smart-`audio_resume()` (drei Cases zusammengelegt), Pre-Roll für
Audiobücher, `audio_list_unfinished()` mit Datum-Sortierung,
`completed`-Cleanup beim Plugin-Start. Plugin-Prompt enthält
ausführliche Anweisungen (siehe `get_prompt_instructions`).

### ✅ Phase 1.4 — Audio-Index (done, war ursprünglich nicht im Plan)

SQLite/FTS5-Index in `audio_index.py`, Tools `audio_search` und
`audio_index_rebuild`. Wurde wegen Skalierung auf NAS-Mounts mit
zigtausenden Files notwendig.

### ❌ Phase 2.0 — YouTube-Plugin (offen, nach Phase 3.0)

**Eigenes Plugin** in `aifred/plugins/tools/youtube/`. Nutzt denselben
`audio_state.json` Store wie Audio-Player.

**Code:**
- `youtube_search(query)` — yt-dlp Search-Mode (`yt-dlp ytsearch5:...`)
- `youtube_play(video_id_or_url, target=None)` — yt-dlp resolved
  Audio-URL → mpv direkt streamen (kein vollständiger Download)
- `youtube_play_search(query, target=None)` — top Treffer direkt spielen
- Items in `audio_state.json` mit Key `youtube:<video_id>`

**Aufwand:** ~3 h.

### ⚠️ Phase 2.1 — Internet-Radio (Infrastruktur done, leer)

`http_stream`-Source-Type ist seit Phase 1.0 voll implementiert. Aber:
in der ausgelieferten `settings.json` sind aktuell **keine** Streams
eingetragen. Sobald der User welche reinträgt (oder die UI dafür eine
Default-Liste anbietet), funktioniert es out-of-the-box.

**TODO:** Beispiel-Streams in der Plugin-Settings-UI als „Quick-Add"-
Buttons anbieten (SWR3, DLF, BBC, …).

### Phase 3.0 — Puck-Output + Channel-Refactor (vor Phase 2)

Fünfstufig — eng zusammenhängend, ~6 h total. Status: **a, b, c done; d, e offen**.

**✅ 3.0a — Channel-Protocol-Refactor** (done):
- Neuer Ordner `aifred/lib/audio_channels/` mit `base.py` (Protocol +
  AudioFormat + TargetInfo + Registry), `local.py`, `browser.py`,
  `puck.py` (Stub).
- Bestehende If-Else-Kaskade in `_route_play()` durch Registry-Lookup
  ersetzen.
- `audio_targets()` iteriert das Registry, nicht mehr hardcoded.

**✅ 3.0b — PuckChannel-Implementierung** (done):
- `aifred/lib/audio_channels/_puck_stream.py` neu — `PuckStream`-
  Klasse pro Target (mpv + FIFO + IPC + Reader-Pump + Save-Loop).
- mpv allein als Decoder + Resampler:
  ```
  mpv --audio-samplerate=48000 --audio-channels=1 \
      --audio-format=s16 --ao=pcm --ao-pcm-file=<fifo> \
      --ao-pcm-waveheader=no --input-ipc-server=<sock> <source>
  ```
- Bridge: drei neue Public-Methoden im FreeEcho2-Channel —
  `send_audio_start(room, channels, rate, audio_type, total_size?)`,
  `send_audio_chunk(room, bytes)`, `send_audio_end(room)`. Werden von
  TTS (`send_reply`) und PuckChannel gemeinsam genutzt.
- Eine mpv-Instanz pro aktivem Puck-Target — sauber isoliert,
  Multi-Room parallel möglich.
- `audio_targets()` zeigt aktive Pucks live aus
  `freeecho2_channel._devices`.
- Cleanup-Order: mpv terminate vor Pump-Cancel (sonst hängt der Read
  blockend in `os.read(fifo)`).

**✅ 3.0c — Wake-Token-Server-Integration** (Server done):
- `_handle_command_token(token, room)` neu in `freeecho2_channel`.
- Per-Target: `_stop` ruft `puck_channel.stop(f"freeecho2:{room}")` plus
  `cancel_pipeline(session_id)` (LLM-Inferenz brechen, andere Streams
  bleiben).
- `_pause`/`_resume` voll verdrahtet. `_standby`/`_activate` ebenfalls
  (Soft-Mute beim Standby = lokal am Puck; Server stoppt nur Stream).
- **Offen am Puck-Repo:** `_pause`/`_resume` müssen in `freeecho2_client.c`
  als `WA_PAUSE`/`WA_RESUME` aufgenommen werden, sonst filtert der Puck
  sie als `WA_UNKNOWN` und sendet sie nicht an den Server. Der Server
  ist bereit, sobald die Tokens ankommen.

**❌ 3.0d — Browser-Text-Parser + Keyboard-Shortcuts** (~1 h, offen):
- Server-seitiger Parser `parse_audio_command(text)` in neuer Datei
  `aifred/lib/audio_commands.py`. Wird vor LLM-Übergabe aufgerufen.
  Funktioniert für **alle** Channels (Browser, Telegram, Discord,
  E-Mail) gleich.
- Steuerzeichen-Konvention: `_` (konsistent mit Puck-Tokens).
  Erkannte Eingaben:
  ```
  _pause, _resume, _stop
  _skip 10, _skip -10
  _seek 120
  _vol 50
  ```
- Match nur wenn die ganze Trim-Lower-Nachricht dem Pattern entspricht
  — keine Partial-Matches in Sätzen.
- Browser-Keyboard-Shortcuts in der Audio-Player-Reflex-Component:
  `Space` (toggle pause/resume), `Esc` (stop), `←`/`→` (skip ±10 s),
  `↑`/`↓` (vol ±10). Nur aktiv wenn Audio läuft + Fokus nicht im
  Chat-Input.

**❌ 3.0e — Room-Following** (~1 h, offen):
- `_resume` an Puck Y holt das letzte aktive Item nach Y, stoppt es
  am bisherigen Target (Position wird sofort gespeichert), startet
  am neuen Target mit kurzem Pre-Roll (3 s, weil User aktiv handelt).
- Voraussetzung: Per-Target-Tracking welches Item wo läuft —
  ergibt sich automatisch aus dem Channel-Refactor (jeder Channel
  hält seinen eigenen aktiven Stream-State pro Target).

**Tests:**
- Item am Wohnzimmer-Puck abspielen
- Auto-Target funktioniert (Puck-Anfrage → Puck-Output)
- Mehrere Pucks parallel mit unterschiedlichen Items
- „Bitte Stopp" am Wohnzimmer stoppt nur Wohnzimmer
- „Bitte Pause" / „Weiter" funktionieren am Puck und im Browser
- Browser-Tippeingabe `_pause` pausiert ohne LLM-Call
- Hörbuch in Wohnzimmer → ins Schlafzimmer gehen → „Weiter" → Audio
  wandert nach Schlafzimmer mit Pre-Roll

### ❌ Phase 4.0 — Hörbuch-Modus mit Auto-Pause (offen, optional)

Beim Wake-Word des Pucks oder beim Eingehen einer User-Anfrage:
Audio wird automatisch pausiert. Nach der TTS-Antwort: automatisch
fortgesetzt. State läuft über bestehenden Position-Save.

**Code:**
- Hook in `_chat_mixin.py` oder `freeecho2_channel`:
  - vor LLM-Call: `audio_channels.pause_for_inference(target)`
  - nach TTS-Ende: `audio_channels.resume_after_inference(target)`
- Pre-Roll kürzer (3 s reichen, weil User bewusst pausiert hat).

Browser-seitig schon teilweise umgesetzt: das `media_paused_for_tts`-
Flag im State pausiert den HTML5-Player während TTS spricht.

**Aufwand:** ~2 h.

### ❌ Phase 4.1 (optional) — Mehrere Output-Targets gleichzeitig

Beispiel: „Spiel das in Wohnzimmer und Schlafzimmer parallel."
Erfordert mpv-Multi-Output oder zwei mpv-Instanzen mit
synchronisiertem Position-Save. Im Backlog.

## Offene Fragen

1. **Auto-Discovery aktiver Pucks für `audio_targets()`.** ✅ Lösung
   klar: `PuckChannel.list_targets()` greift live auf
   `freeecho2_channel._devices.keys()` zu.

2. **Hörbuch-Modus während TTS.** Auto-Pause + Resume klingt simpel,
   aber während TTS spricht muss der Music-Stream auch pausiert sein —
   sonst Audio-Konflikt am Puck. mpv macht das nicht automatisch — wir
   müssen explizit `pause` rufen vor TTS-Stream.

3. **Browser-Tab geschlossen / inactive.** Wenn der User den Browser
   schließt während Hörbuch läuft, soll AIfred:
   a) Pausieren und Position speichern? (User kommt später wieder)
   b) Weiterlaufen lassen? (Kein Hörer, sinnlos)
   c) Auf den Puck migrieren? (smart, aber komplex)
   Vorerst: pausieren auf „browser disconnected"-Event.

4. **Multi-Stream-State pro Target.** Der heutige `audio_manager.py`
   ist Singleton — eine mpv-Instanz, ein State. Sobald mehrere
   PuckChannel-Targets parallel laufen, brauchen wir entweder
   - eine mpv-Instanz pro Target (mehr Speicher, aber sauber isoliert)
   oder
   - einen einzigen mpv mit mehreren Output-Pipelines (komplexer, aber
     leichter).
   Entscheidung steht in Phase 3.0b.

5. **`_tool_index_clear` re-aktivieren?** Aktuell tot. Nach Phase 3.0
   prüfen ob ein User-facing „Index komplett löschen"-Tool sinnvoll
   ist; sonst entfernen.
