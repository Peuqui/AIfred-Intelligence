# Narrator Plugin (document → audio)

**File:** `aifred/plugins/tools/narrator/`

Turns a whole text document from the documents tree into **one** audio file (MP3) — the spoken counterpart to `translate_file`. The file content is processed entirely server-side: read, split at paragraph boundaries into ~800-character chunks, synthesized chunk by chunk via the TTS engine, concatenated with ffmpeg, and written as MP3 (speech VBR ≈ 130 kbps, roughly 10x smaller than WAV) next to the source file. The text **never passes through the LLM context** — even a 100k-character transcript costs no context window.

The narrator reads **verbatim**: no translation, no summarizing, no correction. Those are the upstream steps (the pipeline's division of labor).

## Typical pipeline (meeting recording)

1. Audio upload → Whisper transcript lands as `transcript-….txt` in the workspace (original language, `language=auto`)
2. Optional: the LLM corrects/formats the raw transcript (`read_file` / `write_file`)
3. `translate_file` → DeepL translation as its own file
4. `narrate_file` → MP3 next to the source file, downloadable via the documents button (e.g. for your phone)

## Settings (gear icon in the Agent-Editor plugin tab)

- **Engine**: `(same as spoken output)` follows the main TTS engine. When spoken output is **off**, the GPU-free fallback engine is used instead — the loaded LLM keeps its VRAM (prevents the TTS container's silent CPU fallback).
- **GPU-free**: only engines without GPU requirements are selectable (Piper, Edge, eSpeak, DashScope — depending on installation). Default: Piper (local, offline).
- **Voice**: stored **per engine**. The list shows only the effective engine's own voices (`engine.get_voices()`) — clone voices like "AIfred" appear only on cloning engines; Piper lists its built-in speakers (Thorsten, Karlsson, …).
- GPU engines only appear when their `-tts-` profile variant is calibrated for the current model (same guard as the main TTS dropdown).

## Tools

| Tool | Description | Tier |
|------|-------------|------|
| `narrate_file` | Narrate a text file into one MP3 audio file | WRITE_DATA |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `filename` | Yes | Source file relative to the documents root (e.g. `documents/meeting-DE.txt`) |
| `output_filename` | No | Default: `<name>.mp3` in the same folder; a `.wav` suffix skips the MP3 encode |
| `voice` | No | Default: the saved voice for the resolved engine, else its first own voice |
| `language` | No | Language code of the text (default `de`) |
| `engine` | No | Default: resolved via the plugin settings (see above) |

Returns (JSON): `written`, `chunks`, `chars`, `engine`, `voice`, `size_mb`.

**Note:** Long documents take real time (roughly 5–10x the audio duration, engine-dependent). Progress appears every 5 chunks in the debug console.
