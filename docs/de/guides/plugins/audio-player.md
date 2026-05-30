# Audio Player Plugin

**Datei:** `aifred/plugins/tools/audio_player/`

Wiedergabe-Steuerung für lokale Audiodateien (Ordner via NAS oder lokale Platte)
und HTTP-Streams (Internetradio), mit Pause/Resume und Positions-Speicherung.
Das LLM sieht niemals rohe Pfade oder URLs — nur Labels aus der `settings.json`;
das ist ein bewusster SSRF-/Path-Traversal-Schutz (siehe
`docs/de/architecture/audio-pipeline.md`).

Phase 1.0 liefert lokale Wiedergabe; Browser- und FreeEcho.2-Ausgabe-Adapter
laufen über eine `AudioOutputChannel`-Registry.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `audio_play` | Item abspielen (`label/datei.mp3` oder Stream-`label`) von Anfang an; `restart=false` setzt ab gespeicherter Position fort | READONLY |
| `audio_play_folder` | Alle Audiodateien eines Ordners nacheinander abspielen (natürliche Sortierung), optional gemischt | READONLY |
| `audio_pause` | Target pausieren (auto / konkrete id / `all`); Position wird gespeichert | READONLY |
| `audio_resume` | Fortsetzen: unpause, konkretes `item` fortsetzen oder das zuletzt unfertige Audio aufnehmen | READONLY |
| `audio_stop` | Target stoppen (auto / konkrete id / `all`); Position wird gespeichert | READONLY |
| `audio_seek` | Zu absoluter Position in Sekunden springen | READONLY |
| `audio_skip` | Relativ zur aktuellen Position N Sekunden vor/zurück springen | READONLY |
| `audio_speed` | Wiedergabe-Geschwindigkeit setzen (Faktor 0,25–4,0) | READONLY |
| `audio_status` | Wiedergabe-Status je Target abfragen (alle oder eines) | READONLY |
| `audio_list` | Konfigurierte Quellen oder Items in einem Quellordner auflisten | READONLY |
| `audio_list_unfinished` | Items mit gespeicherter, noch nicht abgeschlossener Position auflisten | READONLY |
| `audio_targets` | Verfügbare Ausgabe-Targets auflisten (local, Browser-Tab, FreeEcho.2-Räume) | READONLY |
| `audio_search` | BM25-Volltextsuche über den Audio-Index (Artist/Album/Title/Dateiname/Pfad) | READONLY |
| `audio_index_rebuild` | SQLite/FTS5-Index für eine oder alle local_folder-Quellen (neu) aufbauen | WRITE_DATA |

Wiedergabe gilt als operativ, nicht destruktiv — deshalb sind die
Play/Pause/Stop-Tools `READONLY`. So kann der FreeEcho.2-Sprachkanal (der auf
dem `COMMUNICATE`-Tier läuft) sie aufrufen. Nur `audio_index_rebuild`, das den
Index schreibt, ist `WRITE_DATA`.

## Targets und Routing

Jedes Tool hat einen optionalen `target`-Parameter:

- **Weglassen** → automatisches Routing dorthin, wo die Anfrage herkam
  (FreeEcho.2-Wake → dieser Raum, Browser-Eingabe → dieser Tab, Text-Kanäle →
  serverseitig `local`). Das ist der Normalfall.
- Eine konkrete id wie `freeecho2:wohnzimmer`, `browser:<id>` oder `local`.
- `all` (für `audio_pause` / `audio_stop`) → jeder aktive Stream über alle
  Kanäle.

`audio_targets()` listet gültige ids auf. Das Standard-Routing lässt sich in der
`settings.json` unter `targets.default` festlegen (Default `"auto"`).

## Discovery und Suche

- `audio_list()` (ohne Argumente) listet konfigurierte Quellen mit Item-Anzahl
  pro Quelle.
- `audio_list(source='music', subdir='Klassik/Mozart')` listet Items — bevorzugt
  über den SQLite-Index, mit Fallback auf einen Filesystem-Walk, falls die Quelle
  noch nicht indiziert ist.
- `audio_search(query='mozart sonate')` macht Sub-Millisekunden-BM25-Suche über
  ID3/FLAC/Vorbis-Tags, Dateinamen und Pfade, auch bei NAS-Mounts mit über
  100.000 Dateien. Tokens werden als Prefix-Matches UND-verknüpft. Zurück kommen
  `state_key`-Werte, die direkt an `audio_play` weitergereicht werden.

Source-Labels und Pfade sind bei `audio_list` / `audio_play`
**case-sensitive**; `audio_search` ist case-insensitive.

## Resume

Die Position wird in `audio_state.json` gespeichert, sodass lange Hörbücher
Pausen, Neustarts und andere Medien dazwischen überstehen. `audio_resume` wählt
automatisch zwischen drei Verhalten: einfaches Unpause, konkretes `item`
fortsetzen oder den zuletzt unfertigen Eintrag aufnehmen — mit kurzem Pre-Roll
für Hörbücher.

## Konfiguration

`settings.json` (im Plugin-Verzeichnis):

- `sources` — benannte Audioquellen. Lokale Ordner werden unter
  `MEDIA_AUDIO_DIR` entdeckt; Einträge mit `type: "http_stream"` definieren
  Radio-Streams.
- `targets.default` — `"auto"` oder eine feste Target-id.
- `resume` — `pre_roll_sec`, `pre_roll_for_streams`,
  `min_audio_duration_for_pre_roll_sec`, `position_save_interval_sec`.
- `list` / `tts_list` — Listen-Limits.
- `limits` — `max_duration_min`, `max_buffer_mb`, `connect_timeout_sec`,
  `read_timeout_sec`.

Das Zahnrad-Icon im Plugin-Tab öffnet ein eigenes Settings-Modal
(`settings_event_name = "open_audio_settings"`).
