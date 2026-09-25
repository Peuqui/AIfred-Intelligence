# Audio-Pipeline-Architektur

> **English version:** [audio-pipeline.md](../../en/architecture/audio-pipeline.md)

Stand: 2026-09-25. Lebendes Dokument — wird mit der Implementierung
weiter ausgebaut.

## Aktueller Implementierungs-Stand

| Komponente | Status | Wo |
|---|---|---|
| `audio_player`-Plugin mit 14 Tools | ✅ | [aifred/plugins/tools/audio_player/](../../../aifred/plugins/tools/audio_player/) |
| `audio_manager.py` (mpv via JSON-IPC) | ✅ | [aifred/lib/audio_manager.py](../../../aifred/lib/audio_manager.py) |
| `audio_state.py` (JSON-Position-SSOT) | ✅ | [aifred/lib/audio_state.py](../../../aifred/lib/audio_state.py) |
| `audio_sources.py` (Folder + HTTP-Stream) | ✅ | [aifred/lib/audio_sources.py](../../../aifred/lib/audio_sources.py) |
| `audio_index.py` (SQLite/FTS5 für 100k+ Files) | ✅ | [aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) |
| Output: lokal (mpv default → ALSA/Pulse) | ✅ | siehe `_route_play()` |
| Output: Browser (HTML5 `<audio>` + REST) | ✅ | [audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py), [audio.py](../../../aifred/lib/api/audio.py) |
| Output: FreeEcho.2-Bridge | ✅ Phase 3.0b | mpv→FIFO→WS-Pipeline pro Raum, eine FreeEcho2Stream-Instanz pro Target |
| `AudioOutputChannel`-Protokoll (Refactor) | ✅ Phase 3.0a | Registry mit Local/Browser/FreeEcho.2, alle Tools kanalbasiert |
| Wake-Tokens `_pause`/`_resume`/`_standby`/`_activate` | ✅ Phase 3.0c | Server: [freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py); FreeEcho.2-Firmware sendet `_pause`/`_resume` als WA_PAUSE/WA_RESUME |
| Browser-Text-Parser (`_pause`, `_resume`, …) | ❌ Phase 3.0d | aktuell nur via LLM-Tool-Call |
| Browser-Keyboard-Shortcuts | ❌ Phase 3.0d | UI-Buttons im Player gibt es schon |
| Room-Following | ⚠️ Phase 3.0e | `_resume` an einem FreeEcho.2 lädt dort das letzte unfertige Item; Stoppen am bisherigen Target fehlt |
| YouTube-Plugin | ❌ Phase 2.0 (nach 3.0) | nicht implementiert |
| Internet-Radio (HTTP-Streams) | ⚠️ | Infrastruktur ja, Streams in `settings.json` aktuell leer |
| Hörbuch-Auto-Pause (Wake-Word → Pause) | ⚠️ | Browser via `media_paused_for_tts` ja; FreeEcho.2: die TTS-Antwort ersetzt die Musik (Position via `consumed_ms`), kein automatisches Fortsetzen (Phase 4.0) |
| Audio-Bus-Refactor (FreeEcho.2 = dumb sink) | ✅ Phase 5.0 | `AudioOrchestrator` pro Raum ([_audio_orchestrator.py](../../../aifred/lib/audio_channels/_audio_orchestrator.py)), `audio_flag`-Frame; Firmware verarbeitet `audio_flag` |

## Motivation

Der Mini ist headless — der primäre Use-Case ist ein User der per
Browser, VS-Code-Server oder per FreeEcho.2 im Wohnzimmer arbeitet. Lokaler
ALSA-Output am Mini hilft dann nicht (es ist gar kein Lautsprecher
direkt angeschlossen). Audio muss dort ankommen, wo die Anfrage
herkam — und vom User per Sprache umgelenkt werden können
(„spiel das im Schlafzimmer").

Weiteres Pflichtenheft:
- **Pause / Resume** mit Position-Save für lange Hörbücher (11 h+).
- **Mehrere Audio-Quellen parallel:** Hörbuch A pausieren, Musik B
  spielen, später Hörbuch A genau dort fortsetzen.
- **Mehrere Outputs parallel:** FreeEcho.2 Wohnzimmer spielt Hörbuch, FreeEcho.2
  Schlafzimmer spielt Radio, Browser-Tab spielt YouTube — `Stopp` am
  Wohnzimmer-FreeEcho.2 stoppt **nur** den Wohnzimmer-Stream, nicht die
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
   Adapter (FreeEcho.2).
5. **Auto-Target aus PluginContext.** Default-Output ist der Channel
   woher die Anfrage kam. User-Override per Sprache erkennt die LLM
   selbständig und gibt es als `target=`-Parameter weiter.
6. **Per-Channel-Stop.** `Stopp`-Wake an einem FreeEcho.2 stoppt nur dessen
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
       │ LocalChannel    │   │ BrowserChannel  │   │ FreeEcho2Channel     │
       │                 │   │                 │   │                 │
       │ mpv → Pulse/    │   │ HTML5 <audio>   │   │ mpv → 48kHz     │
       │ ALSA            │   │ + /api/audio/   │   │ int16 PCM → FIFO│
       │ (default        │   │ file?key=...    │   │ → FreeEcho2-WS  │
       │  output)        │   │ via Reflex-     │   │ (chunked)       │
       │                 │   │ State-Push      │   │                 │
       └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                      │                      │
              ▼                      ▼                      ▼
        Mini-Lautsprecher     Browser-Tab des         FreeEcho.2 im
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
    sample_rate: int | None = None      # 48000 für FreeEcho.2
    channels: int | None = None         # 1 für FreeEcho.2
    sample_format: str | None = None    # "s16le" für FreeEcho.2

@dataclass
class TargetInfo:
    id: str               # "local", "browser:abc123", "freeecho2:wohnzimmer"
    label: str            # "Lokale Lautsprecher", "FreeEcho.2 Wohnzimmer"
    ready: bool           # True = Hardware/Connection verfügbar


@runtime_checkable
class AudioOutputChannel(Protocol):
    name: str                          # "local", "browser", "freeecho2"
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

    # ctx braucht nur der BrowserChannel (Reflex-State-Push)
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

    # Optional, für Push-Senken (FreeEcho.2): App-Level-Flow-Control
    def supports_flow_control(self) -> bool: ...        # Default False
    def notify_flow(self, target_id: str, state: str) -> None: ...   # "pause" | "resume"
    def get_stream_start_offset(self, target_id: str) -> float | None: ...
```

`play_queue()` (sequenzielles Ordner-Playback) implementieren Browser- und
FreeEcho.2-Channel; der `LocalChannel` meldet „not implemented".

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

# Eingebaute Channels melden sich beim Import selbst an:
register(LocalChannel()); register(BrowserChannel()); register(FreeEcho2Channel())
```

**Format-Anforderungen pro Channel:**

| Channel | `required_format` | Konversion durch |
|---|---|---|
| `LocalChannel` | leer (mpv frisst alles) | mpv intern |
| `BrowserChannel` | leer (HTML5 lädt Datei direkt) | Browser intern |
| `FreeEcho2Channel` | `(48000, 1, "s16le")` | mpv selbst (`--audio-samplerate`/`--audio-channels`/`--audio-format`), keine separate ffmpeg-Stufe |

**Discovery der konkreten Targets** ist immer live — `list_targets()`
fragt zur Aufrufzeit:
- `LocalChannel` → immer `[{"id":"local","ready":True}]`
- `BrowserChannel` → `browser:<ctx.session_id>` der aktuellen Anfrage
- `FreeEcho2Channel` → iteriert die verbundenen Speaker in `freeecho2_channel._devices`
  (definiert in `_shared.py`)

`audio_targets()` blendet das Browser-Target aus, wenn die Anfrage nicht aus dem
Browser kommt.

Damit erscheinen FreeEcho.2-Speaker automatisch in `audio_targets()`, sobald sie
sich per WebSocket verbinden — keine User-Konfiguration nötig.

## Plugin-Konfiguration

**Lokale Ordner-Sources werden automatisch gefunden**: jeder direkte Unterordner
oder Symlink unter `data/media/audio/` (`MEDIA_AUDIO_DIR`) wird eine
`local_folder`-Source, deren Label der Ordnername ist. Symlinks machen NAS-Mounts
transparent. Gepflegt wird das in der UI (Plugin-Settings → Audio Player: Source per
Datei-Picker hinzufügen = Symlink anlegen, entfernen, Index neu bauen/leeren).
Source-Map-SSOT: `build_configured_source_map()` in
[aifred/lib/audio_player_settings.py](../../../aifred/lib/audio_player_settings.py).

Die [`settings.json`](../../../aifred/plugins/tools/audio_player/settings.json) des
Plugins hält nur noch den Rest (ausgelieferter Stand):

```jsonc
{
  // Hier zählen nur http_stream-Einträge — Items werden über Labels referenziert,
  // nie raw Pfade/URLs. Beispiel-Eintrag:
  //   "swr3": { "type": "http_stream", "url": "https://liveradio.swr.de/sw282p3/swr3/play.mp3" }
  "sources": {},

  // Bereich, den der Datei-Picker in der Settings-UI erreichen darf
  "picker": { "root": "/mnt" },

  // Schwelle des TTS-Listen-Filters (audio_processing.py)
  "tts_list": { "full_max_items": 5 },

  // Resume-Verhalten
  "resume": {
    "pre_roll_sec": 7,
    "pre_roll_for_streams": false,
    "min_audio_duration_for_pre_roll_sec": 60,
    "position_save_interval_sec": 60
  },

  // Output-Default
  "targets": {
    "default": "auto"   // = aus PluginContext.source ableiten
                        // oder "local", "browser:<id>", "freeecho2:wohnzimmer", ...
  }
}
```

Ein Source-Eintrag kann `audio_type` explizit setzen; sonst wird er aus Genre-Tag >
Dateiname > Ordnername abgeleitet (`resolve_audio_type()` in `audio_sources.py`).

Live-Discovery (Browser-Sessions, aktive FreeEcho.2-Speaker) wird **nicht** in
`settings.json` gespiegelt — das ergibt sich zur Laufzeit aus dem
Channel-Registry.

## Tool-Inventar (Audio-Player Plugin)

14 Tools, registriert in `get_tools()`. Die Steuer-Tools (play … index_rebuild)
werden nur für Anfragen aus `browser` und `freeecho2` gelistet (`_CONTROL_SOURCES`);
E-Mail, Telegram, Discord und unbeaufsichtigte Trigger bekommen nur die fünf
Lese-Tools (status, list, list_unfinished, targets, search). Die Steuer-Tools sind
bewusst `TIER_READONLY`, damit der FreeEcho.2-Channel sie aufrufen darf — das Gate
ist die Quelle, nicht der Tier.

Für `target` akzeptieren die Steuer-Tools: weggelassen/`"auto"` = Herkunft der
Anfrage, `"all"` = alle aktiven Targets, oder eine konkrete ID (`"local"`,
`"browser:<id>"`, `"freeecho2:<room>"`).

| Tool | Tier | Zweck |
|---|---|---|
| `audio_play(item, target=None, restart=True)` | READONLY | Item starten. `target=None` → Auto aus PluginContext. Default `restart=True` startet von vorn; `restart=False` setzt an der gespeicherten Position fort. |
| `audio_play_folder(folder, target=None, shuffle=False)` | READONLY | Ganzen Ordner sequenziell in natürlicher Reihenfolge (`CD 1` < `CD 2` < `CD 10`) abspielen, optional gemischt. Browser- und FreeEcho.2-Targets (der lokale Channel hat keine Queue). |
| `audio_pause(target=None)` | READONLY | Wiedergabe am Target pausieren. |
| `audio_resume(item=None, target=None)` | READONLY | Smart-Resume: drei Cases automatisch — gepausten Stream weiterlaufen lassen, oder per `item` einen anderen Stream aus Position-State laden, oder zuletzt-gespieltes Unfinished-Item fortsetzen. |
| `audio_stop(target=None)` | READONLY | Wiedergabe am Target stoppen. Position bleibt erhalten. |
| `audio_seek(position_sec, target=None)` | READONLY | Absolute Position springen. |
| `audio_skip(delta_sec, target=None)` | READONLY | Relativ vor/zurück. |
| `audio_speed(factor, target=None)` | READONLY | 0.25–4.0× Playback-Speed (mpv resampled korrekt). |
| `audio_status(target=None)` | READONLY | Zustand eines Targets, ohne `target` aller Targets: running/playing/paused, Item, Position, Duration. |
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

(`youtube:`-Keys sind die geplante Konvention des YouTube-Plugins, Phase 2.0.)

Schreibregeln:
- **Nur an Save-Points** — die Position wird aus mpv gelesen und an den unten
  genannten Save-Points geschrieben; jedes `audio_state.update()` schreibt die Datei
  atomar (tmp + rename).
- **Periodisch** als Crash-Insurance gegen Power-Loss/OOM: Position-Save-Loop im
  mpv-Wrapper. Intervall aus `settings.json` → `resume.position_save_interval_sec`
  (ausgeliefert: 60); das Plugin setzt es vor jedem Play beim lokalen Channel
  (`audio_manager.configure_save_interval()`, Code-Default
  `POSITION_SAVE_INTERVAL_SEC = 30` in `audio_manager.py`). FreeEcho.2-Streams nutzen
  `DEFAULT_SAVE_INTERVAL_SEC = 60` in `_freeecho2_stream.py`. Werte ≤ 0 werden ignoriert.
- **Bei Pause und Stop sofort** — der wichtigste Save-Point, weil hier der
  User-Intent zum Resume gespeichert wird.
- Bei FreeEcho.2-Wakes überschreibt `consumed_ms` vom Puck die mpv-Position
  (siehe „consumed_ms-Position-Sync").
- Bei mpv `eof-reached` → `completed: true`, sofort.
- `completed: true`-Einträge älter als `AUDIO_STATE_CLEANUP_AGE_DAYS` (config.py, 7)
  werden beim Dienst-Start und täglich um `GARBAGE_COLLECTION_HOUR` gelöscht
  (`cleanup_audio_state_task()`).

## Audio-Index (SQLite/FTS5)

[aifred/lib/audio_index.py](../../../aifred/lib/audio_index.py) führt einen lokalen Index über alle
Items in `local_folder`-Sources, persistiert in `data/audio_index.db` (Laufzeit-Datei):

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

`_resolve_target(ctx, requested)` in
[audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py)
(vereinfacht):

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
        return "local"   # sollte nicht vorkommen — wird als Warnung geloggt
    return "local"       # Text-Channels (discord/email/telegram), CLI, Cron
```

User-Sprach-Override: LLM erkennt aus Tool-Description und
Konversations-Hinweisen, welchen `target`-Parameter sie übergeben muss
(„spiel das im Schlafzimmer" → `target="freeecho2:schlafzimmer"`). Kein
zusätzlicher Code nötig.

## Wake-Word-Steuerung am FreeEcho.2

Der FreeEcho.2 (Firmware) erkennt onboard mehrere Wake-Words. Die
Wake-Phrasen sind **dynamisch konfigurierbar** über das FreeEcho.2-Web-UI
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
| `_stop` | Pipeline canceln + `channel.stop(target)` für sendendes Target | `Bitte Stopp`, `Ruhe bitte` |
| `_pause` | gleiche Server-Reaktion wie `_stop`: Pipeline canceln + Stream stoppen; die Position wird vor dem mpv-Terminate gespeichert, `_resume` setzt dort fort | `Bitte Pause`, `Halt` |
| `_resume` | Pipeline canceln + Smart-Resume: letztes unfertiges Item aus `audio_state.json` auf diesem FreeEcho.2 laden (Pre-Roll 3 s) | `Weiter`, `Fortsetzen` |

**2. FreeEcho.2-Lifecycle-Tokens** — Mikrofon/Hardware des FreeEcho.2 selbst, **kein**
Audio-Output. Bleiben in `freeecho2_channel` intern, sind nicht im
`AudioOutputChannel`-Protocol:

| Token | Action | Wake-Phrase (Default) |
|---|---|---|
| `_standby` | FreeEcho.2-Mikrofon Soft-Mute, Vosk lauscht nur auf `_activate` | `Entry passiv` |
| `_activate` | Soft-Mute aus | `Entry aktiv` |
| `_done` | Quittung des Pucks: proaktive Wiedergabe (Chime + TTS) fertig — weckt die Alert-Queue fürs nächste Item | — (sendet die Firmware) |

`_standby` ruft zusätzlich `channel.stop(target)` für das eigene Target
auf — wenn der FreeEcho.2 schläft, soll auch sein laufender Audio-Stream
beendet sein. Aber das ist eine implementierte Konsequenz, nicht
semantischer Teil des Tokens.

### Per-Channel-Target — kein globaler Stop

`_stop`/`_pause`/`_resume` an einem FreeEcho.2 steuern **nur** den Stream
**dieses einen FreeEcho.2-Speakers**. Andere Output-Streams laufen weiter. Der Server
kennt das Quell-Target (`freeecho2:wohnzimmer`) und ruft gezielt
`channel.stop("freeecho2:wohnzimmer")` auf, keinen globalen Stop.

Beispiel: Wohnzimmer-FreeEcho.2 spielt Hörbuch, Schlafzimmer-FreeEcho.2 spielt
Radio, Browser-Tab läuft Musik. Wer im Wohnzimmer „Bitte Stopp" sagt,
stoppt nur das Hörbuch — die anderen zwei Streams laufen weiter.

### Server-Implementierungs-Status

Alle Tokens verarbeitet `_handle_command_token(token, room)` in
[freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py):

| Token | Server-Verarbeitung |
|---|---|
| `_stop` | ✅ `_cancel_pipeline_and_stop_stream()` — cancelt den laufenden Pipeline-Task + die LLM-Pipeline, stoppt nur den Stream dieses FreeEcho.2 |
| `_pause` | ✅ wie `_stop` (Position-Save in `_cleanup_unlocked` vor dem mpv-Terminate) |
| `_resume` | ✅ `_cancel_pipeline_for_room()` + `_smart_resume_on_freeecho2()` |
| `_standby` | ✅ wie `_stop` (der Soft-Mute selbst ist FreeEcho.2-lokal) |
| `_activate` | ✅ bewusst No-op am Server (Soft-Mute aus ist FreeEcho.2-lokal, kein Auto-Resume) |
| `_done` | ✅ `signal_playback_done(room)` für die Alert-Queue |

## Sicherheits-Layer

| Bedrohung | Mitigation |
|---|---|
| Path-Traversal (`audio_play("/etc/passwd.wav")`) | Whitelist konfigurierter Pfade, `..` in Item-String wird abgelehnt. Items werden gegen die `sources`-Map resolved. |
| SSRF via HTTP-Stream | LLM kennt keine raw URLs. URLs nur in Plugin-Config (User-pflegbar). `audio_play(item="swr3")` → Plugin resolved zu der konfigurierten URL. |
| Internal-Network-Probing | Nicht implementiert: konfigurierte Stream-URLs werden nicht gegen private Adressbereiche geprüft (geplant: `127.*`, `10.*`, `192.168.*`, `172.16-31.*` blocken, mit expliziter Allowlist). Schutz bisher: URLs kommen nur aus der vom User gepflegten Config. |
| Decoder-Lücken (manipulierte MP3) | mpv läuft als Subprocess mit User-Privilegien. `apt update` regelmäßig. Optional: `firejail`/`bwrap`-Sandboxing. |
| DOS via Stream | mpv-Argumente `--demuxer-max-bytes=512MiB`, `--network-timeout=30` (`audio_manager.py`, `_freeecho2_stream.py`). Ein Dauer-Limit gibt es nicht. |
| Prompt-Injection aus externen Kanälen | Steuer-Tools werden nur für `browser`/`freeecho2` gelistet (`_CONTROL_SOURCES`). |
| Credential-Leak | Authentifizierte Audio-Sources gibt es noch nicht; wenn sie kommen: Auth über `credential_broker`, nie Cookies/Headers im Plugin-Code. |

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

`_resolve_target(ctx, requested)` in [audio_player/__init__.py](../../../aifred/plugins/tools/audio_player/__init__.py).
Anfangs nur `local` + Browser-Session; seit Phase 3.0b listet `audio_targets()` auch
die verbundenen FreeEcho.2-Speaker, und FreeEcho.2-Anfragen werden auf
`freeecho2:<room>` geroutet.

### ✅ Phase 1.3 — Hörbuch-Workflow (done)

Smart-`audio_resume()` (drei Cases zusammengelegt), Pre-Roll für
Audiobücher, `audio_list_unfinished()` mit Datum-Sortierung,
`completed`-Cleanup (Dienst-Start + täglicher Task). Plugin-Prompt enthält
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

### Phase 3.0 — FreeEcho.2-Output + Channel-Refactor (vor Phase 2)

Fünfstufig — eng zusammenhängend, ~6 h total. Status: **a, b, c done; d offen; e teilweise**.

**✅ 3.0a — Channel-Protocol-Refactor** (done):
- Neuer Ordner `aifred/lib/audio_channels/` mit `base.py` (Protocol +
  AudioFormat + TargetInfo), `__init__.py` (Registry), `local.py`, `browser.py`,
  `freeecho2.py` (seit 3.0b voll implementiert).
- Bestehende If-Else-Kaskade in `_route_play()` durch Registry-Lookup
  ersetzen.
- `audio_targets()` iteriert das Registry, nicht mehr hardcoded.

**✅ 3.0b — FreeEcho2Channel-Implementierung** (done):
- `aifred/lib/audio_channels/_freeecho2_stream.py` neu — `FreeEcho2Stream`-
  Klasse pro Target (mpv + FIFO + IPC + Reader-Pump + Save-Loop).
- mpv allein als Decoder + Resampler:
  ```
  mpv --audio-samplerate=48000 --audio-channels=1 \
      --audio-format=s16 --ao=pcm --ao-pcm-file=<fifo> \
      --ao-pcm-waveheader=no --input-ipc-server=<sock> <source>
  ```
- Bridge: öffentliche Sende-Methoden im FreeEcho2-Channel
  ([ws_bridge.py](../../../aifred/plugins/channels/freeecho2_channel/ws_bridge.py)) —
  `send_audio_flag(room, audio_type, **params)`,
  `send_audio_start(room, total_size=None, channels=1)`,
  `send_audio_chunk(room, data)`, `send_audio_end(room)`, dazu
  `send_heartbeat(room)` und `send_done(room, reason=None)`. Werden von TTS
  (`send_reply` über den AudioOrchestrator) und `FreeEcho2Stream` gemeinsam genutzt.
- Eine mpv-Instanz pro aktivem FreeEcho.2-Target — sauber isoliert,
  Multi-Room parallel möglich.
- `audio_targets()` zeigt verbundene FreeEcho.2-Speaker live aus
  `freeecho2_channel._devices`.
- Cleanup-Order: mpv terminate vor Pump-Cancel (sonst hängt der Read
  blockend in `os.read(fifo)`).

**✅ 3.0c — Wake-Token-Server-Integration** (Server done):
- `_handle_command_token(token, room)` in
  [freeecho2_channel/commands.py](../../../aifred/plugins/channels/freeecho2_channel/commands.py).
- Per-Target: `_stop` löst `freeecho2:{room}` über die Channel-Registry auf und ruft
  `channel.stop(target_id)` plus `cancel_pipeline(session_id)` (LLM-Inferenz
  brechen, andere Streams bleiben).
- `_pause`/`_resume` voll verdrahtet. `_standby`/`_activate` ebenfalls
  (Soft-Mute beim Standby = lokal am FreeEcho.2; Server stoppt nur Stream).
- Firmware: `freeecho2_client.c` bildet `_pause`/`_resume` auf `WA_PAUSE`/`WA_RESUME`
  ab und sendet sie an den Server.

**❌ 3.0d — Browser-Text-Parser + Keyboard-Shortcuts** (~1 h, offen):
- Server-seitiger Parser `parse_audio_command(text)` als neuer lib-Helper
  (Vorschlag: `aifred/lib/audio_commands.py` — existiert noch nicht). Wird vor
  LLM-Übergabe aufgerufen. Nicht zu verwechseln mit den FreeEcho.2-Wake-Tokens, die
  als `wake`-Frames ankommen und in `freeecho2_channel/commands.py` verarbeitet werden.
  Funktioniert für **alle** Channels (Browser, Telegram, Discord,
  E-Mail) gleich.
- Steuerzeichen-Konvention: `_` (konsistent mit FreeEcho.2-Tokens).
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

**⚠️ 3.0e — Room-Following** (~1 h, teilweise):
- Schon da: `_resume` an FreeEcho.2 Y lädt das letzte unfertige Item aus
  `audio_state.json` auf Y mit 3 s Pre-Roll (`_smart_resume_on_freeecho2()`). Wurde
  das Item am bisherigen Target gestoppt/pausiert, ist das faktisch Room-Following.
- Fehlt: Ein am bisherigen Target noch laufender Stream wird nicht gestoppt.

Ursprünglicher Plan:
- `_resume` an FreeEcho.2 Y holt das letzte aktive Item nach Y, stoppt es
  am bisherigen Target (Position wird sofort gespeichert), startet
  am neuen Target mit kurzem Pre-Roll (3 s, weil User aktiv handelt).
- Voraussetzung: Per-Target-Tracking welches Item wo läuft —
  ergibt sich automatisch aus dem Channel-Refactor (jeder Channel
  hält seinen eigenen aktiven Stream-State pro Target).

**Tests:**
- Item am Wohnzimmer-FreeEcho.2 abspielen
- Auto-Target funktioniert (FreeEcho.2-Anfrage → FreeEcho.2-Output)
- Mehrere FreeEcho.2-Speaker parallel mit unterschiedlichen Items
- „Bitte Stopp" am Wohnzimmer stoppt nur Wohnzimmer
- „Bitte Pause" / „Weiter" funktionieren am FreeEcho.2 und im Browser
- Browser-Tippeingabe `_pause` pausiert ohne LLM-Call
- Hörbuch in Wohnzimmer → ins Schlafzimmer gehen → „Weiter" → Audio
  wandert nach Schlafzimmer mit Pre-Roll

### ❌ Phase 4.0 — Hörbuch-Modus mit Auto-Pause (offen, optional)

Beim Wake-Word des FreeEcho.2 oder beim Eingehen einer User-Anfrage:
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

## Audio-Bus-Refactor (FreeEcho.2 = dumb sink) — Phase 5.0

**Status: ✅ umgesetzt (Stand 2026-09-25).** Konsens zwischen AIfred-Server-
Instanz und FreeEcho.2-Firmware-Instanz (am 2026-05-10 via AI-Connect
ausverhandelt mit Salomo-Prinzip). Beide Sides wurden parallel
umgebaut, kein Capability-Gate, kein Legacy-Fallback. Server: `AudioOrchestrator` in
[_audio_orchestrator.py](../../../aifred/lib/audio_channels/_audio_orchestrator.py),
Frame-Sender in `freeecho2_channel/ws_bridge.py`; Firmware: `audio_flag`-Verarbeitung
in `freeecho2_client.c`.

### Motivation

Vor dem Refactor liefen TTS und Music am FreeEcho.2 über zwei separate Pfade:
- TTS: `send_reply` → `audio_start(speech)` + chunks + `audio_end`
- Music: mpv → FIFO → fifo_pump → kontinuierliche `audio_chunk`-Frames

Bei TTS während Music kollidieren die zwei PCM-Quellen am Speaker —
TTS-`audio_start`-Frame wird im SPEAKING-Loop der Firmware verworfen
(matcht nur auf `"audio_end"`), TTS-Bytes landen in der Music-Source.
Plus: Source-Pre-emption via neuem `audio_start` würde am Speaker DAC-
Reinit triggern → knackt. Kein sauberer Mechanismus für Mid-Stream-
Type-Switch.

### Architektur

**FreeEcho.2 = dumb sink.** Eine Audio-Pipeline pro Session, Server
orchestriert was reinfließt. Neuer Frame-Typ `audio_flag` für
Type-Wechsel ohne Stream-Reset.

### Tupel-Whitelist (strikt, FATAL bei Verletzung)

```
(music                    )
(speech                   )
(tts                      )
(alarm,        with_tts=B )
(notification, with_tts=B )
```

`speech` (Hörbuch/Podcast/Lesung, Voice-VU am Puck) ist ebenfalls erlaubt;
server-seitig liegt die Whitelist in `_AUDIO_TYPE_SCHEMA` in `ws_bridge.py` und wird
vor dem Senden validiert (`ValueError` statt Firmware-FATAL).

Alles außerhalb der Whitelist → Protocol Error, Connection close,
FATAL log. Strikt: keine Defaults, keine versteckten Annahmen.

`alarm` und `notification` sind strukturell identisch — einziger
Unterschied ist welche WAV der Puck lokal abspielt und welches LED-
Layer aktiv wird. Beide spielen **einmal** ab. Längere oder
aufdringliche Use-Cases (z.B. nerviger Wecker) sind Server-orchestriert:
der Caller loopt `play_alarm()` mehrmals, bei `_stop`-Quittierung der
Firmware bricht der Server-Loop ab. Volle Kontrolle bleibt beim Server,
Puck bleibt dumm.

### Verhalten pro Type

| Type | PCM-Quelle | Wo gespeichert |
|---|---|---|
| `music` | Server-mpv-FIFO → WS-Stream | Server pumpt |
| `tts` | Server-TTS-Render → WS-Stream | Server pumpt |
| `alarm` | FreeEcho.2-lokale WAV (UI-konfigurierbar) | Puck-lokal — kein Server-PCM, einmal abspielen |
| `alarm` + `with_tts=true` | Lokale WAV + TTS-Tail-Stream | Puck spielt 1×, dann Server-TTS |
| `notification` | FreeEcho.2-lokale WAV (UI-konfigurierbar) | Puck-lokal — kein Server-PCM, einmal abspielen |
| `notification` + `with_tts=true` | Lokale WAV + TTS-Tail-Stream | Puck spielt 1×, dann Server-TTS |

### Frame-Sequenzen (final fixiert)

| Use-Case | Frame-Sequenz |
|---|---|
| Music | `audio_flag(music)` → `audio_start` → chunks → `audio_end` |
| TTS standalone | `audio_flag(tts)` → `audio_start` → chunks → `audio_end` |
| alarm ohne Tail | nur `audio_flag(alarm, with_tts=false)` — kein PCM, kein audio_end |
| alarm mit Tail | `audio_flag(alarm, with_tts=true)` → `audio_flag(tts)` → `audio_start` → chunks → `audio_end` |
| notification ohne/mit Tail | analog |

**Kein TTS-Takeover mehr** (Konsens-Spec 2026-05-10): TTS während Music läuft
ersetzt die Music-Source komplett. Music wird sauber gestoppt (mpv terminate,
audio_end, Position via consumed_ms in audio_state.json), TTS startet als
neue Standalone-Source. User holt Music via `audio_resume` zurück (Pre-Roll
greift auf der echten Hörposition). Vereinfacht den Server-Code massiv und
eliminiert Race-Conditions zwischen mpv-pause/resume und TTS-Pump.

**Wichtig**: `audio_flag` und `audio_start` sind **getrennte** Frames
mit unterschiedlicher Semantik:
- `audio_flag` = Type-Setting (LED + VU + Source-Verhalten)
- `audio_start` = PCM-Stream-Setup-Header (optional `total_size`)

`rate` wird nicht gesendet — die Puck-Hardware ist fest auf 48 kHz int16.
`audio_start` trägt aber `channels` (1 oder 2); aktuell laufen alle Streams mono
(`_CHANNELS_PER_TYPE` in `_freeecho2_stream.py`, Stereo für music/speech am
2026-05-31 wegen Puck-Problemen zurückgenommen).

### Vier Operationen — semantisch klar pro Type

| Operation | music | tts (standalone) | alarm (auch +Tail) | notification (auch +Tail) |
|---|---|---|---|---|
| **Play** | mpv start, audio_flag, audio_start, pump | TTS render, audio_flag, audio_start, pump (ersetzt evtl. laufende Music) | audio_flag mit Params (kein PCM) | audio_flag mit Params (kein PCM) |
| **Pause** | echtes Pause (mpv-IPC, Position bleibt) | echtes Pause (TTS-Cursor merken) | = Stop (alles weg) | = Stop (alles weg) |
| **Resume** | mpv-IPC unpause, weiter pumpen | ab Cursor weiter pumpen, **kein** Re-Render | (n/a, da Stop) | (n/a, da Stop) |
| **Stop** | alles weg, mpv terminate | alles weg, TTS-Buffer drop | alles weg, audio_end an Puck | alles weg, audio_end an Puck |

**Logik**: music und standalone-tts sind klassische Audio-Inhalte
(Hörbuch / längere Antwort) — Pause/Resume sinnvoll. alarm und
notification sind ereignisgetriebene transiente Audios — Pause auf
einem Wecker macht keinen Sinn, → Stop.

### Persistenz

Nur `music` persistiert in `audio_state.json` (für long-form Resume
nach Server-Restart, Hörbücher 11 h+). TTS, alarm, notification leben
nur in-memory — bei Server-Restart abgebrochen (ein Wecker der nach
Restart noch klingelt wäre Bug, nicht Feature).

### Server-Side SSOT-API

```python
async def play_music(stream)                        # Music-Stream registrieren
async def play_tts(pcm_data)                        # TTS-Standalone (ersetzt Music)
async def play_alarm(with_tts, tts_pcm=None)        # Puck-lokal + opt. TTS-Tail
async def play_notification(with_tts, tts_pcm=None) # analog
async def pause()                                   # type-aware
async def resume()
async def stop()                                    # alles verwerfen
```

Methoden von `AudioOrchestrator`; sie wirken auf die "aktuell aktive Audio-Source" pro Room. AudioOrchestrator
hat genau eine aktive Source (kein Source-Stack). Type-Wechsel (z.B.
Music → TTS) bedeutet: alte Source sauber beenden, neue starten.

### silent_reply — Smart-Speaker-UX

Audio-Tools (`audio_play`, `audio_play_folder`, `audio_resume`) setzen
`silent_reply: true` in ihrem Tool-Result. Der Channel `send_reply`
liest das aus `outbound.metadata` und skippt TTS-Render+Send komplett.
Music läuft sofort, ohne dass der Butler erklärt was er tut. Reply-Text
bleibt im Chat-Log fürs Browser-UI sichtbar.

Pfad: Tool-Result-JSON → `llm_pipeline.run_llm_stream` parst silent_reply →
`PipelineResult.silent_reply` → `llm_engine` setzt `metadata_dict["silent_reply"]` →
`message_processor` setzt `outbound.metadata["silent_reply"]` →
`freeecho2_channel.send_reply` → early return.

### consumed_ms-Position-Sync

Bei großem Puck-Ring liegt `mpv-time-pos` (Decode-Position) deutlich vor
der echten User-Hörposition. Der Puck trackt
`consumed_frames_since_stream_start` und schickt im Wake-Frame:

```json
{"type":"wake","room":"...","agent":"...","consumed_ms":12345}
```

Server überschreibt damit `audio_state.json[key].pos_sec` **nach** dem
Stream-Stop (mpv-time-pos-Save kommt zuerst aus `_cleanup_unlocked`,
consumed_ms überschreibt dann). Der bestehende Pre-Roll im
`audio_resume` (default 7s) greift damit auf der echten Hörposition.

### Firmware-Side SSOT-API (FreeEcho.2)

- `apply_audio_type(t)` — atomisch VU-Mode, LED-Pattern, AEC-Reset,
  Vosk-permissive, BT-Tap (alle 5 Sub-Punkte)
- `audio_source_stream_flush_with_fade(src, 30 ms)` — sauberer
  Type-Switch ohne DAC-Knack
- `audio_pause_current()` / `audio_resume_current()` — type-aware
  (alarm/notification → stop statt pause)
- `audio_stop_all()` — totaler Abbruch inkl. TTS-Tail-Drain
- audio_flag-Frame-Handler mit strikter Whitelist-Validation
- alarm/notification-Sequenzer: lokale WAV einmal abspielen, optional
  TTS-Tail. Loop bei "aufdringlicher Wecker"-Use-Case übernimmt der
  Server (mehrfach play_alarm()), nicht die Firmware.

### LED-Pattern-Erweiterung (Firmware)

Neue Audio-Layer-Patterns in `freeecho2_ledd.c`:
- `audio:alarm` → orange, schnell pulsierend (Default `solid:FF8000+pulse:300`)
- `audio:notification` → sanftes Cyan, langsam (Default `solid:00C8FF+pulse:1500`)

Per UI konfigurierbar: lokale WAV-Dateien für alarm/notification
plus LED-Patterns.

### Implementierungs-Phasen (Server-Side)

1. ✅ **Phase 1**: Frame-Sender (`send_audio_flag`, schlankes
   `send_audio_start`) im Plugin-Layer (`ws_bridge.py`)
2. ✅ **Phase 2**: Stateful AudioOrchestrator pro Room mit Music-Stream
   und TTS-Buffer als zwei Pump-Quellen
3. ✅ **Phase 3**: `send_reply` (`freeecho2_channel/tts_reply.py`) spielt TTS über
   `orc.play_tts()` — das alte `pause_for_tts`/`resume_after_tts`-Pattern ist weg
4. ✅ **Phase 4** (anders gelöst): keine `audio_alarm`/`audio_notification`-Tools;
   stattdessen laufen das `freeecho2_announce`-Tool des FreeEcho.2-Channels
   (`audio_type` `notification`/`alarm`) und proaktive Pushes (z.B. Vision-Alerts)
   über die Alert-Queue (`alert_queue.py`) → `orc.play_alarm()`/`orc.play_notification()`

## Offene Fragen

1. **Auto-Discovery aktiver FreeEcho.2-Speaker für `audio_targets()`.** ✅ Gelöst:
   `FreeEcho2Channel.list_targets()` iteriert live über `freeecho2_channel._devices`.

2. **Hörbuch-Modus während TTS.** ✅ Entschieden (Phase 5.0, „kein TTS-Takeover"):
   TTS am FreeEcho.2 ersetzt die Music-Source — `play_tts()` beendet die Musik
   sauber, die Position kommt aus `consumed_ms`, der User setzt per `audio_resume`
   fort. Ein automatisches Fortsetzen nach der TTS (Phase 4.0) gibt es nicht.

3. **Browser-Tab geschlossen / inactive.** Wenn der User den Browser
   schließt während Hörbuch läuft, soll AIfred:
   a) Pausieren und Position speichern? (User kommt später wieder)
   b) Weiterlaufen lassen? (Kein Hörer, sinnlos)
   c) Auf den FreeEcho.2 migrieren? (smart, aber komplex)
   Vorerst: pausieren auf „browser disconnected"-Event.

4. **Multi-Stream-State pro Target.** ✅ In Phase 3.0b entschieden: eine
   mpv-Instanz pro FreeEcho.2-Target (`FreeEcho2Stream`, sauber isoliert).
   `audio_manager.py` bleibt der Singleton nur für den lokalen Output.

5. **`_tool_index_clear` re-aktivieren?** ✅ Erledigt: das Tool gibt es nicht mehr.
   Den Index pro Source leert man in der Settings-UI (`audio_index_clear_source`).
