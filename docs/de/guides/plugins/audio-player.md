# Audio Player Plugin

**Datei:** `aifred/plugins/tools/audio_player/`

Server-seitige Audio-Wiedergabe von Audiodateien.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `audio_play` | Audiodatei abspielen | WRITE_DATA |
| `audio_stop` | Wiedergabe stoppen | READONLY |
| `audio_status` | Aktuellen Wiedergabestatus abfragen | READONLY |

## Features

- Spielt WAV, MP3, OGG, FLAC direkt auf dem Server ab
- Geplant für Puck-Hardware-Integration
