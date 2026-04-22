# Audio Player Plugin

**File:** `aifred/plugins/tools/audio_player/`

Server-side audio playback of audio files.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `audio_play` | Play audio file | WRITE_DATA |
| `audio_stop` | Stop playback | READONLY |
| `audio_status` | Query current playback status | READONLY |

## Features

- Plays WAV, MP3, OGG, FLAC directly on the server
- Planned for Puck hardware integration
