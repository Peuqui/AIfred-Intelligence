# Narrator Plugin (document → audio)

> **Deutsche Version:** [narrator.md](../../../de/guides/plugins/narrator.md)

**File:** `aifred/plugins/tools/narrator/`

Turns a whole text document from the documents tree into **one** audio file (MP3) — the spoken counterpart to `translate_file`. The file content is processed entirely server-side: read, split at paragraph boundaries into ~800-character chunks, synthesized chunk by chunk via the TTS engine, concatenated with ffmpeg, and written as MP3 (speech VBR ≈ 130 kbps, roughly 10x smaller than WAV) next to the source file. The text **never passes through the LLM context** — even a 100k-character transcript costs no context window.

The narrator reads **verbatim**: no translation, no summarizing, no correction. Those are the upstream steps (the pipeline's division of labor).

## Typical pipeline (meeting recording)

1. Audio upload → Whisper transcript lands as `transcript-….txt` in the workspace (original language, `language=auto`)
2. Optional: the LLM corrects/formats the raw transcript (`read_file` / `write_file`)
3. `translate_file` → DeepL translation as its own file
4. `narrate_file` → MP3 next to the source file, downloadable via the documents button (e.g. for your phone)

## Engine choice and settings

**Engine:** The narrator has no engine setting of its own. It takes the engine from the **TTS escalation list** (`aifred/lib/tts_escalation.py`, `choose_speaker`):

- Without the `engine` parameter: the topmost list entry that can speak now. A local GPU entry whose container is not running is started if its measured peak need fits into the free VRAM of a card — the main model is never reloaded for it. If it does not fit, the list moves on.
- With the `engine` parameter (engine key, e.g. `piper`): exactly that engine on this machine, provided it can speak now; otherwise an error with the reason (`NoSpeechAvailable`), no switching to another engine.
- The chosen engine stays for the **whole file**: no voice change in the middle of an audiobook, not even on a failure.

The engines live as plugins in `aifred/lib/tts_engines/<key>/engine.py` (each with its own `i18n.json`); order, hosts and activation of the list: [TTS + VRAM workflow](../../architecture/tts-vram-workflow.md).

**Voice** (gear icon in the Agent-Editor plugin tab, `narrator_voices` in the settings): stored **per engine**. The dialog has two selects — the engine whose voice is being edited (only engines that are ready here or listed on another host) and that engine's own voices (`voice_names`). It therefore does not choose the engine of the narration. Voice resolution order: the `voice` parameter, else the saved voice of the chosen engine, else its first own voice (never the clone name "AIfred" for e.g. Piper), finally `NARRATE_DEFAULT_VOICE` (`AIfred`).

## Tools

| Tool | Description | Tier |
|------|-------------|------|
| `narrate_file` | Narrate a text file into one MP3 audio file | WRITE_DATA |
| `list_narrator_voices` | List the effective engine's voices (discovery for multi-voice) | READONLY |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `filename` | Yes | Source file relative to the documents root, without a `documents/` prefix (e.g. `meeting-DE.txt`) |
| `output_filename` | No | Default: `<name>.mp3` in the same folder; a `.wav` suffix skips the MP3 encode |
| `voice` | No | Default: the saved voice for the chosen engine, else its first own voice |
| `language` | No | Language code of the text (default `de`) |
| `engine` | No | Engine key. Default: the topmost entry of the TTS escalation list that can speak now (see above) |
| `speaker_voices` | No | Multi-voice mapping speaker label → voice name (see below) |

Returns (JSON): `written`, `url`, `chunks`, `chars`, `engine`, `voice`, `size_mb` — in multi-voice mode additionally `speaker_voices`, `segments`.

## Multi-voice mode (audio drama)

Interview/dialog transcripts can be narrated with **one voice per speaker**. Speaker separation is done by the LLM (not acoustics): AIfred prepares the transcript as a marked-up dialog and calls `narrate_file` **once** with `speaker_voices`.

- **Voice discovery**: `list_narrator_voices` returns the valid voice names of the effectively resolved engine (plus the default voice) — voice names are engine-specific, the model calls this tool before a multi-voice run instead of guessing.
- **Cloned voices preferred**: voices with a `★` prefix are user-cloned voices (SSOT: the prefix comes straight from `engine.get_voices()`, e.g. on xtts and DashScope). The model is instructed to prefer them when assigning roles; `generate_tts` strips the prefix centrally before synthesis. Pure clone engines (qwen3local) carry no `★` — every voice there is a clone anyway.
- **Marker format**: lines starting with `[LABEL]:` (labels are free: `FRAGE`/`ANTWORT`/`S1`/…). A segment runs until the next marker; the marker itself is **not spoken**. Text before the first marker uses the default voice (`voice`).
- **`speaker_voices`**: JSON object label → voice name (taken from the `list_narrator_voices` result). All voices must belong to the **one** effective engine (no engine mix).
- **Strict validation**: an unknown voice or a label in the text without a mapping → clear error, **no silent fallback**.
- **Speaker count**: unlimited — the parser has no limit. In practice only the engine's voice count bounds the variety; multiple labels may share the same voice (a ten-character audio drama with four voices is legitimate).
- **Chunking**: a speaker change is always a hard chunk boundary; long segments are still split at paragraph boundaries internally.
- The markers survive the DeepL translation (`translate_file`) — translated audio dramas work with the same marked-file pipeline.

**Note:** Long documents take real time (roughly 5–10x the audio duration, engine-dependent). Progress appears every 5 chunks in the debug console.
