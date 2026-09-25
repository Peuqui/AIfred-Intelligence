# TTS Containers — Conventions for New Engines

> **Deutsche Version:** [tts-container-conventions.md](../../de/architecture/tts-container-conventions.md)

As of: 2026-09-25. Living document.

When AIfred integrates a new TTS engine as a Docker container, it should
follow the conventions below. This keeps the audio setup
uniform, fast and easy to extend.

---

## Golden Rule

> **Audio bytes live in the container, not on the wire.**

Per TTS request, the AIfred path only sends:
- the input text,
- the voice name (speaker ID),
- the language.

The voice bytes sit as files in the container — either pre-warmed into VRAM
as a speaker embedding at startup, or read per request directly from
the mounted disk. What we do **not** do under any circumstances:
stuff base64-encoded WAV files into the JSON body of the API request
(>1 MB per request, redundant, slow).

---

## Voice Directory on the Host Side

All engines share **one** voice directory; each engine lives in its own
folder next to it:

```
docker/tts/
├── voices/              # shared SSOT, mounted read-only into every container
├── xtts/
├── qwen3-tts/
├── fish-speech/
└── moss-tts/
```

Layout: one subfolder per speaker. It serves all engines at once —
XTTS / MOSS / Qwen3-TTS read `<Name>/<Name>.wav` (+ `.txt`), Fish-Speech S2 Pro
uses the folder name as `reference_id` and reads the `.lab` transcript:

```
voices/
├── AIfred/
│   ├── AIfred.wav   # mono speech sample (the existing voices: 15–30 s)
│   ├── AIfred.txt   # transcript (XTTS / MOSS / Qwen3-TTS, improves cloning quality)
│   └── AIfred.lab   # same transcript under the name Fish-Speech expects
├── HAL9000/
├── Salomo/
└── Sokrates/
```

The servers look for voices via `glob("*/*.wav")` — the speaker name is the file stem.

---

## Volume Mount in `docker-compose.yml`

```yaml
volumes:
  - ../voices:/app/voices:ro       # XTTS / MOSS / Qwen3-TTS
  # OR for engines with a reference_id API:
  - ../voices:/app/references:ro   # Fish-Speech S2 Pro
```

Read-only — the container must not write anything into the mount (the cache of
pre-warmed embeddings goes into a separate named volume, e.g. XTTS: `xtts_voices`
on `/app/custom_voices`).

---

## API Schema

What AIfred sends over per request:

```json
// XTTS / MOSS / Qwen3-TTS — standard
{
    "text": "Hallo Welt.",
    "speaker": "AIfred",
    "language": "de"
}

// Fish-Speech S2 Pro — reference_id instead of speaker
{
    "text": "Hallo Welt.",
    "format": "wav",
    "reference_id": "AIfred",
    "normalize": true,
    "streaming": false
}
```

The server responds with `Content-Type: audio/wav` (or `audio/ogg`),
the body is the audio file. No JSON wrapping around the bytes — we save
ourselves the base64 decoding on the AIfred side.

Implementation per engine: the `generate_speech()` method of the respective
`TTSEngine` subclass under
[`aifred/lib/tts_engines/<engine>.py`](../../../aifred/lib/tts_engines/)
(e.g. `xtts.py`). `audio_processing.py` now only dispatches to
`eng.generate_speech_async(...)`.

---

## Voice Pre-Warming in the Container — Recommended

On the first request, voice cloning extracts a speaker embedding from
the reference WAV. Depending on the model, encoding takes 50–500 ms. If
the container redoes this per request, every speech output call pays
this surcharge. Therefore:

> **Precompute all voices once at container start, keep them in VRAM,
> and per request only pull the embedding from the dict.**

**Models to follow:**
- Qwen3-TTS: `_warm_clone_prompts()` in [`docker/tts/qwen3-tts/server.py`](../../../docker/tts/qwen3-tts/server.py)
  builds x-vector + with-transcript prompts per speaker in `_clone_prompts`.
- XTTS: identical pattern in [`docker/tts/xtts/server.py`](../../../docker/tts/xtts/server.py)
  (`_custom_voices`), plus a disk cache as `.pth` in `/app/custom_voices` so the
  second container start already comes up with warm embeddings.

**Exceptions:**
- MOSS-TTS has no pre-warming — the upstream `transformers` processor
  loads the reference WAV fresh from disk per request. It works,
  but stays slower than Qwen3/XTTS. Patching it would only pay off with a deep
  intervention in `transformers` and is therefore deliberately not done.
- Fish-Speech: the S2 Pro server is upstream code, internal caching is an
  implementation detail. `reference_id` is enough.

If the engine has no server of its own but rides on a ready-made
ASGI app (see Fish-Speech), the native `reference_id`
scheme is enough — no custom pre-warming logic needed.

---

## Idle Watchdog (Auto-Shutdown)

Every GPU TTS container occupies several GB of VRAM. So that the LLM on
the same GPU is not permanently calculated small, the container shuts
itself down after `<ENGINE>_KEEP_ALIVE` minutes of inactivity.

Implementation:
- Own server (XTTS / MOSS / Qwen3): idle thread directly in the server code,
  reset on every `/tts` request.
- Upstream server (Fish-Speech): ASGI middleware wrapper —
  [`docker/tts/fish-speech/aifred_idle_server.py`](../../../docker/tts/fish-speech/aifred_idle_server.py).

Health checks, `/openapi`, `/keep_alive` itself and the WebUI must
**not** count as activity — otherwise AIfred's `_detect_running_tts_engine()`
probe (see [`aifred/lib/tts_engine_manager.py`](../../../aifred/lib/tts_engine_manager.py))
keeps the container alive forever.

The env variable follows the scheme `<ENGINE>_KEEP_ALIVE=<minutes>`
(`XTTS_KEEP_ALIVE`, `MOSS_KEEP_ALIVE`, `QWEN3_KEEP_ALIVE`, `FISH_SPEECH_KEEP_ALIVE`;
the `docker-compose.yml` files set 45 each), `0` disables the watchdog.

---

## Port Assignment

Convention in the `docker-compose.yml` block:

```
XTTS         → 5051
Qwen3-TTS    → 5052
Fish-Speech  → 5053
(reserved)   → 5054
MOSS         → 5055
```

New engines get the next free number; we deliberately leave the `5054` slot
in place so the block keeps a clear rhythm when
reading.

---

## Calibration Reserve (VRAM)

So that LLM calibration does not plan too generously next to the TTS container,
the reserve per engine is **measured**, not hand-maintained:

- `resolve_tts_reserve(engine_key)` in
  [`aifred/lib/tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py)
  returns the cached peak from
  [`aifred/lib/tts_vram_cache.py`](../../../aifred/lib/tts_vram_cache.py)
  (`data/tts_vram_cache.json`) plus `LLAMACPP_TTS_BURNIN_HEADROOM_MB`
  (config.py, 512 MB).
- On a cache miss, the stress burn-in runs first: start the container, fire a
  deliberately long bilingual synthesis, poll the GPU memory every 100 ms, write the
  peak into the cache. No TTL — the value stays until the reset button in the
  calibration picker (`reset_tts_vram_cache`) clears the table or the entry is deleted.
- Manual re-run: `python -m aifred.lib.tts_stress_burnin <engine_key>`.
- The calibration (`_calibration_mixin.py`) plans the TTS GPU around this reserve;
  the engine's `calibration_setup()` deliberately leaves the container cold
  so the idle footprint is not counted twice.

There are no hand-set per-engine reserves: the calibration uses
`resolve_tts_reserve()` only. Engines that grow during generation (Qwen3-TTS,
Fish-Speech) override `calibration_setup()` so the container stays cold during
calibration — the burn-in peak already includes its idle footprint.

---

## Checklist for a New TTS Engine

1. **Use the shared voice directory** `docker/tts/voices/` (AIfred, HAL9000,
   Salomo, Sokrates); add missing transcripts there only if the engine needs a
   different file name (like `.lab` for Fish-Speech).
2. **`docker/tts/<engine>/docker-compose.yml`** with a read-only mount
   `../voices:/app/voices:ro` (or `/app/references` for Fish-Speech-like engines).
3. Add an **idle watchdog** with `<ENGINE>_KEEP_ALIVE`.
4. **Pre-warming in the server code**, if possible (voice embeddings in
   `_clone_prompts`/`_custom_voices` or similar at startup).
5. **`generate_speech()` method** in the new `TTSEngine` subclass
   under `aifred/lib/tts_engines/<engine>.py` — send only text + speaker name +
   language. Write the response as an audio body, never base64.
6. **Engine registration**: just the `TTSEngine` subclass (with `key`,
   `label_short`, `needs_gpu`, `image_name`, `compose_subdir` if the folder name
   differs from the key) in `aifred/lib/tts_engines/<engine>.py` —
   [`registry.py`](../../../aifred/lib/tts_engines/registry.py) discovers it
   automatically and builds `TTS_ENGINES`, no manual entry.
7. **VRAM reserve**: nothing to maintain — the stress burn-in measures it on the
   first calibration (see above).
8. **Port** according to the scheme.
9. Calibration profile in `~/.config/llama-swap/config.yaml` as a
   `<model>-tts-<engine>` variant (see the AIfred calibration docs).
