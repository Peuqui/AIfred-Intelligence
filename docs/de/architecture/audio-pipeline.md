# Audio-Pipeline-Architektur

Stand: 2026-05-04. Lebendes Dokument — wird mit der Implementierung
weiter ausgebaut.

## Motivation

Bestehender Stand:
- `aifred/plugins/tools/audio_player/` mit drei Tools (`audio_play`,
  `audio_stop`, `audio_status`).
- `aifred/lib/audio_manager.py` als Subprocess-Wrapper um `aplay` (WAV)
  und `ffplay` (MP3/OGG/FLAC).
- Output ausschließlich an die Default-ALSA/PulseAudio des AIfred-
  Servers — d.h. der Mini-PC braucht physisch einen Lautsprecher oder
  Kopfhörer angeschlossen.

Was fehlt:
- **Mini ist headless.** Der primäre Use-Case ist ein User der per
  Browser/VS-Code-Server aus der Ferne arbeitet. Lokales ALSA-Output am
  Mini hilft dem User dann nicht.
- **Output-Routing** — Audio soll dort ankommen wo die Anfrage herkam
  (Browser-Anfrage → Browser-Output, Puck-Anfrage → Puck-Output) und
  optional vom User per Sprache umgelenkt werden können
  („spiel das im Wohnzimmer").
- **Pause / Resume** existiert nicht. Bei längeren Audios (Hörbüchern)
  ist das eine Grundanforderung.
- **Position-Save** — wenn ein 11-Stunden-Hörbuch unterbrochen wird,
  muss AIfred später bei der gleichen Stelle weitermachen können.
- **Mehrere Audio-Quellen parallel verwalten** — wer Hörbuch A pausiert
  um Musik B zu hören, will später Hörbuch A genau dort fortsetzen.
- **YouTube-/Internet-Streaming** als Source — eigenes Plugin, aber
  gemeinsamer Position-Save mit dem Audio-Player.
- **Sicherheit** — die LLM darf keine willkürlichen URLs ans Audio-
  Backend übergeben können (SSRF-Risiko).

## Leitprinzipien

1. **Source/Sink-Trennung.** Sources liefern URIs/Pfade
   (lokale Files, HTTP-Streams, YouTube-resolved URLs). Der zentrale
   `AudioManager` ist die einzige Sink — er routet zu lokalem ALSA,
   Browser, Puck oder anderen Outputs.
2. **Label-only LLM-API.** Die LLM kennt keine raw URLs. Sie wählt aus
   Source-Labels die der User in der Plugin-Config gepflegt hat — damit
   ist SSRF systembedingt ausgeschlossen.
3. **Position-Save zentral.** `data/audio_state.json` ist die SSOT für
   alle Resumes — egal ob Audio-Player oder YouTube-Plugin das Item
   gespielt hat.
4. **mpv als Engine.** Statt aplay/ffplay/eigenem Player nutzen wir mpv
   mit JSON-IPC. Das gibt uns Pause/Resume/Seek/Position/Volume nativ,
   plus HTTP-Streaming, plus die Möglichkeit PCM auf eine FIFO zu
   schreiben (für Browser-/Puck-Streaming).
5. **Auto-Target aus PluginContext.** Default-Output ist der Channel
   woher die Anfrage kam. User-Override per Sprache erkennt die LLM
   selbständig und gibt es als `target=`-Parameter weiter.

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
                            │   AudioManager       │
                            │   (Singleton)        │
                            │                      │
                            │   • mpv per IPC      │
                            │   • Position-Save    │
                            │   • Target-Routing   │
                            │   • Pre-Roll Resume  │
                            └──────────┬───────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
       ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
       │ LocalAdapter    │   │ BrowserAdapter  │   │ PuckAdapter     │
       │ mpv --ao=pulse  │   │ mpv → FIFO →    │   │ mpv → FIFO →    │
       │                 │   │ SSE/WS → HTML5  │   │ FreeEcho2-WS    │
       │                 │   │ <audio>         │   │ audio_type=     │
       │                 │   │                 │   │ music           │
       └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                      │                      │
              ▼                      ▼                      ▼
        Mini-Lautsprecher     Browser-Tab des         Puck im
        (3.5mm Kopfhörer)     Users (Office,           Wohnzimmer/
                              Wohnzimmer, etc.)        Schlafzimmer
```

## Plugin-Konfiguration

UI-pflegbar (Plugin-Settings → Audio Player). Beispiel-YAML:

```yaml
# Quellen — alle Audio-Items werden über Labels referenziert,
# nie über raw Paths/URLs.
sources:
  alarms:
    type: local_folder
    path: /home/mp/Audio/wecker

  music:
    type: local_folder
    path: /home/mp/Audio/musik

  hoerbuecher:
    type: local_folder
    path: /mnt/family-nas/Hoerbuecher    # NAS-Mount, aus AIfreds Sicht lokal

  sandbox:
    type: local_folder
    path: ./data/sandbox_output           # vom Sandbox-Plugin generierte WAVs

  swr3:
    type: http_stream
    url: https://liveradio.swr.de/sw282p3/swr3/play.mp3

  dlf:
    type: http_stream
    url: https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3

# Resume-Verhalten
resume:
  pre_roll_sec: 7                  # Bei resume_last 7s vor Position starten
  pre_roll_for_streams: false      # Streams haben keinen meaningful resume
  pre_roll_for_short_audio: false  # < 60s Audio kein Pre-Roll
  position_save_interval_sec: 30   # Wie oft Position auf Disk schreiben

# Output-Targets
targets:
  default: auto                    # = aus PluginContext.source ableiten
  available:
    - local                        # mpv default → ALSA/PulseAudio
    - browser:auto                 # = aktueller Browser-Tab des Users
    - puck:wohnzimmer              # FreeEcho2-Adapter für diesen Raum
    # weitere Pucks dynamisch über FreeEcho2-Plugin entdeckt

# Stream-Limits (DOS-Schutz für HTTP-Sources)
limits:
  max_duration_min: 240            # 4h Hard-Cap
  max_buffer_mb: 512               # mpv --demuxer-max-bytes
  connect_timeout_sec: 10
  read_timeout_sec: 30
```

## Tool-Inventar (Audio-Player Plugin)

| Tool | Tier | Zweck |
|---|---|---|
| `audio_play(item, target=None, restart=False)` | WRITE_DATA | Item starten. `target=None` → Auto aus PluginContext. `restart=True` ignoriert Position-State. |
| `audio_pause()` | READONLY | Aktuelle Wiedergabe pausieren. |
| `audio_resume()` | READONLY | Aus Pause weiterspielen. |
| `audio_resume_last(item=None)` | WRITE_DATA | Item aus Position-State fortsetzen. `item=None` → zuletzt gespieltes. Mit Pre-Roll. |
| `audio_stop()` | READONLY | Wiedergabe stoppen. Position bleibt erhalten. |
| `audio_seek(position_sec)` | READONLY | Absolute Position springen. |
| `audio_skip(delta_sec)` | READONLY | Relative Position (negativ = zurück). |
| `audio_speed(factor)` | READONLY | 0.5–3.0× Playback-Speed. |
| `audio_volume(percent)` | READONLY | 0–100% Lautstärke. |
| `audio_status()` | READONLY | Aktueller Zustand: playing/paused/stopped, file, position, duration, target, speed, volume. |
| `audio_list(source=None)` | READONLY | Items in einer Source listen. |
| `audio_list_unfinished()` | READONLY | Items mit gespeicherter Position (≠ completed). |
| `audio_targets()` | READONLY | Verfügbare Output-Targets mit Status. |

## State-Schema

`data/audio_state.json` — die SSOT für Resume:

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
- Periodisch alle 30s (konfigurierbar) während aktiver Wiedergabe.
- Bei `audio_pause()` und `audio_stop()` sofort.
- Bei `mpv eof-reached` → `completed: true` setzen.
- Beim nächsten Plugin-Start: `completed: true`-Einträge älter als
  7 Tage werden gelöscht (Cleanup-Pass).

## Auto-Target aus PluginContext

```python
def _resolve_target(ctx: PluginContext, default_config: str) -> str:
    if default_config != "auto":
        return default_config

    if ctx.source == "browser":
        return f"browser:{ctx.device_id}"
    if ctx.source == "freeecho2":
        return f"puck:{ctx.room}"
    if ctx.source in ("discord", "email", "telegram"):
        # Text-Channels — Audio dort nicht möglich
        return None  # Tool meldet Fehler an LLM
    return "local"   # Fallback (CLI, scheduler, …)
```

User-Sprach-Override: LLM erkennt aus Tool-Description und
Konversations-Hinweisen, welchen `target`-Parameter sie übergeben muss.
Kein zusätzlicher Code nötig — Tool-Description ist klar genug:

> _„target: Output destination. If omitted, the audio is routed to the
> channel where the request came from (browser tab, puck room, etc.).
> Use `audio_targets()` to see available options. Examples: `local`,
> `browser:abc123`, `puck:wohnzimmer`."_

## Sicherheits-Layer

| Bedrohung | Mitigation |
|---|---|
| Path-Traversal (`audio_play("/etc/passwd.wav")`) | Whitelist konfigurierter Pfade, `..` in Item-String wird abgelehnt. Items werden gegen die `sources`-Map resolved. |
| SSRF via HTTP-Stream | LLM kennt keine raw URLs. URLs nur in Plugin-Config (User-pflegbar). `audio_play(item="swr3")` → Plugin resolved zu der konfigurierten URL. |
| Internal-Network-Probing | Wenn doch URLs in der Config: blocke `127.*`, `10.*`, `192.168.*`, `172.16-31.*` außer explizit in `internal_allowed_hosts` whitelisted. |
| Decoder-Lücken (manipulierte MP3) | mpv läuft als Subprocess mit User-Privilegien. `apt update` regelmäßig. Optional: `firejail`/`bwrap`-Sandboxing. |
| DOS via Stream | `--demuxer-max-bytes=512MiB`, `--network-timeout=30`, `max_duration_min` Limit (mpv stoppt nach Ablauf). |
| Credential-Leak | Auth über `credential_broker` — nie Cookies/Headers im Plugin-Code. |

## Phasen-Plan

### Phase 1.0 — Core Audio-Player

**Code:**
- `aifred/lib/audio_manager.py` neu schreiben:
  - mpv-Subprocess mit `--input-ipc-server=/tmp/aifred_mpv.sock`
  - JSON-IPC-Client (asyncio-basiert, simpler Linewise-Parser)
  - Methoden: `play`, `pause`, `resume`, `stop`, `seek`, `skip`, `speed`, `volume`, `status`
- `aifred/lib/audio_state.py` (neu):
  - JSON-Persistenz der Position
  - Periodischer Save-Loop
  - Cleanup von completed-Einträgen
- `aifred/lib/audio_sources.py` (neu):
  - `LocalFolderSource` und `HttpStreamSource`-Klassen
  - Resolver: Label/File → URI für mpv
- `aifred/plugins/tools/audio_player/__init__.py` aufräumen:
  - Alle 13 Tools registrieren
  - settings.json mit `sources`/`resume`/`targets`/`limits`-Schema
  - Plugin-Settings UI generiert sich aus dem Schema

**Test (lokal mit Kopfhörern oder `--ao=null`):**
- Item abspielen, pausieren, fortsetzen
- Position über Restart hinweg merken
- Mehrere Items: einer pausiert, anderer läuft, erster wird fortgesetzt

**Aufwand:** ~3h Code + Lint + Test.

### Phase 1.1 — Browser-Adapter

**Code:**
- `aifred/lib/audio_adapters/browser.py` (neu):
  - mpv mit `--ao=pcm --ao-pcm-file=<fifo>`
  - FastAPI/Reflex-Endpoint: `/audio_stream/<device_id>` (SSE oder WebSocket)
  - PCM-Chunks von FIFO lesen, an HTML5 `<audio>` streamen
- HTML-Seite oder Reflex-Component: persistentes `<audio>`-Element
  in der UI, das den Stream-Endpoint konsumiert. Einmal pro Session
  initialisiert, bleibt offen für die ganze Sitzung.

**Test (vom Browser im Office aus):**
- Items werden im Browser-Tab abgespielt
- Pause/Resume funktioniert
- Position-Save/Restore funktioniert

**Aufwand:** ~2-3h.

### Phase 1.2 — Auto-Target-Routing

**Code:**
- `audio_player`-Plugin: `_resolve_target_from_ctx()`
- Tool-Description ergänzen: erklärt der LLM den `target`-Parameter
- `audio_targets()`-Tool: listet verfügbare Targets dynamisch

**Test:**
- Browser-Anfrage → audio im Browser
- (Später) Puck-Anfrage → audio am Puck
- LLM erkennt „spiel das im Schlafzimmer" → `target="puck:schlafzimmer"`

**Aufwand:** ~30 Min.

### Phase 1.3 — Hörbuch-Workflow polieren

**Code:**
- `audio_resume_last()` mit Pre-Roll
- `audio_list_unfinished()` mit `last_played`-Sortierung
- Cleanup: `completed:true && last_played > 7d` → entry löschen
- Plugin-Prompt-Hinweis: bei „Hörbuch fortsetzen"-Anfragen
  `audio_list_unfinished` zuerst aufrufen, dem User die Optionen
  präsentieren.

**Test:**
- Hörbuch starten, mittendrin pausieren, andere Audio spielen,
  Hörbuch fortsetzen → korrekte Position mit Pre-Roll
- Mehrere offene Hörbücher → `audio_list_unfinished()` listet alle

**Aufwand:** ~30 Min.

### Phase 2.0 — YouTube-Plugin (eigenes Plugin)

**Eigenes Plugin** in `aifred/plugins/tools/youtube/`. Nutzt denselben
`audio_state.json` Store wie Audio-Player.

**Code:**
- `youtube_search(query)` — yt-dlp Search-Mode (`yt-dlp ytsearch5:...`)
- `youtube_play(video_id_or_url, target=None)` — yt-dlp resolved Audio-
  URL → mpv direkt streamen (kein vollständiger Download)
- `youtube_play_search(query, target=None)` — top Treffer direkt spielen
- Items in `audio_state.json` mit Key `youtube:<video_id>`

**Aufwand:** ~3h.

### Phase 2.1 — Internet-Radio

Bereits durch Phase 1.0 abgedeckt — HTTP-Streams in der `sources`-Map
sind seit Phase 1 möglich. Nur Beispiele und Doku ergänzen.

### Phase 3.0 — Puck-Output-Adapter

**Code:**
- `aifred/lib/audio_adapters/puck.py` (neu):
  - mpv → FIFO → PCM
  - Bridge zum FreeEcho2-WebSocket: `audio_start` mit
    `audio_type=music`, dann PCM-Chunks
  - Konvertierung auf gewünschte Sample-Rate (48kHz, evtl. Stereo)
- FreeEcho2-Channel-Plugin: `send_audio_stream()`-Hilfsmethode
  die der Adapter aufrufen kann

**Test:**
- Item am Wohnzimmer-Puck abspielen
- Auto-Target funktioniert (Puck-Anfrage → Puck-Output)
- Mehrere Pucks parallel (Wohnzimmer + Schlafzimmer mit
  unterschiedlichen Items)

**Aufwand:** ~3-4h.

### Phase 3.1 — Local-ALSA-Adapter (trivial)

mpv default ist bereits `--ao=pulse`/`--ao=alsa` — der `LocalAdapter`
ist quasi der „kein-Adapter"-Fall. Nur: explizit als Target wählbar
machen, plus Test mit Kopfhörer am Mini.

### Phase 4.0 — Hörbuch-Modus mit Auto-Pause

Beim Wake-Word des Pucks oder beim Eingehen einer User-Anfrage:
Audio wird automatisch pausiert. Nach der TTS-Antwort: automatisch
fortgesetzt. State läuft über bestehenden Position-Save.

**Code:**
- Hook in `_chat_mixin.py` oder `freeecho2_channel`:
  - vor LLM-Call: `audio_manager.pause_for_inference()`
  - nach TTS-Ende: `audio_manager.resume_after_inference()`
- Mit Pre-Roll? Vermutlich kürzer (3s reichen, weil User bewusst
  pausiert hat).

**Aufwand:** ~2h.

### Phase 4.1 (optional) — Mehrere Output-Targets gleichzeitig

Beispiel: „Spiel das in Wohnzimmer und Schlafzimmer parallel."
Erfordert mpv-Multi-Output oder zwei mpv-Instanzen mit synchronisiertem
Position-Save. Im Backlog.

## Offene Fragen

1. **Browser-Adapter — SSE oder WebSocket?** SSE simpler aber
   uni-direktional. WebSocket erlaubt Pause/Volume-Commands vom Browser
   zum Server (z.B. wenn der User den Browser-Tab pausiert wäre nice
   wenn AIfred das mitbekommt). Erstmal SSE, später ggf. WebSocket.

2. **Auto-Discovery aktiver Pucks.** FreeEcho2-Plugin kennt registrierte
   Räume. `audio_targets()` muss live diese Liste abfragen. Wie? Über
   eine Plugin-Method `freeecho2_plugin.get_active_rooms()`?

3. **Hörbuch-Modus während TTS.** Auto-Pause + Resume klingt simpel,
   aber während TTS spricht muss der Audio-Stream auch pausiert sein
   sonst Audio-Konflikt am Puck. mpv macht das nicht automatisch — wir
   müssen explizit `pause` rufen vor TTS-Stream.

4. **Browser-Tab geschlossen / inactive.** Wenn der User den Browser
   schließt während Hörbuch läuft, soll AIfred:
   a) Pausieren und Position speichern? (User kommt später wieder)
   b) Weiterlaufen lassen? (Kein Hörer, sinnlos)
   c) Auf den Puck migrieren? (smart, aber komplex)
   Vorerst: pausieren auf "browser disconnected" Event.

5. **Speed-Control über alle Targets.** mpv `set speed N` funktioniert
   für alle Adapter — Lokal/Browser/Puck — solange wir den PCM-Stream
   nach mpv abgreifen. Geprüft: ja, mpv resampled korrekt.
