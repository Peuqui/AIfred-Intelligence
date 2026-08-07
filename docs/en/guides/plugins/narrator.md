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
| `list_narrator_voices` | List the effective engine's voices (discovery for multi-voice) | READONLY |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `filename` | Yes | Source file relative to the documents root (e.g. `documents/meeting-DE.txt`) |
| `output_filename` | No | Default: `<name>.mp3` in the same folder; a `.wav` suffix skips the MP3 encode |
| `voice` | No | Default: the saved voice for the resolved engine, else its first own voice |
| `language` | No | Language code of the text (default `de`) |
| `engine` | No | Default: resolved via the plugin settings (see above) |
| `speaker_voices` | No | Multi-voice mapping speaker label → voice name (see below) |

Returns (JSON): `written`, `chunks`, `chars`, `engine`, `voice`, `size_mb` — in multi-voice mode additionally `speaker_voices`, `segments`.

## Multi-voice mode (audio drama)

Interview/dialog transcripts can be narrated with **one voice per speaker**. Speaker separation is done by the LLM (not acoustics): AIfred prepares the transcript as a marked-up dialog and calls `narrate_file` **once** with `speaker_voices`.

- **Voice discovery**: `list_narrator_voices` returns the valid voice names of the effectively resolved engine (plus the default voice) — voice names are engine-specific, the model calls this tool before a multi-voice run instead of guessing.
- **Cloned voices preferred**: voices with a `★` prefix are user-cloned voices (SSOT: the prefix comes straight from `engine.get_voices()`, e.g. on xtts and DashScope). The model is instructed to prefer them when assigning roles; `generate_tts` strips the prefix centrally before synthesis. Pure clone engines (qwen3local) carry no `★` — every voice there is a clone anyway.
- **Marker format**: lines starting with `[LABEL]:` (labels are free: `FRAGE`/`ANTWORT`/`S1`/…). A segment runs until the next marker; the marker itself is **not spoken**. Text before the first marker uses the default voice (`voice`).
- **`speaker_voices`**: JSON object label → voice name (taken from the `list_narrator_voices` result). All voices must belong to the **one** effective engine (no engine mix).
- **Strict validation**: an unknown voice or a label in the text without a mapping → clear error, **no silent fallback**.
- **Chunking**: a speaker change is always a hard chunk boundary; long segments are still split at paragraph boundaries internally.
- The markers survive the DeepL translation (`translate_file`) — translated audio dramas work with the same marked-file pipeline.

**Note:** Long documents take real time (roughly 5–10x the audio duration, engine-dependent). Progress appears every 5 chunks in the debug console.
