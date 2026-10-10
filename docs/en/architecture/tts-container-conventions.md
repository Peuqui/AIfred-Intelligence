# TTS Containers — Conventions for New Engines

> **Deutsche Version:** [tts-container-conventions.md](../../de/architecture/tts-container-conventions.md)

As of: 2026-10-10. Living document.

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
`TTSEngine` subclass in
[`aifred/lib/tts_engines/<key>/engine.py`](../../../aifred/lib/tts_engines/)
(e.g. `xtts/engine.py`). Container engines call the base class's `_synthesize_via_http()`
for it. `audio_processing.py` only dispatches to `eng.generate_speech_async(...)`.

---

## Engine Structure in the Code

Every engine lives in **its own folder** `aifred/lib/tts_engines/<key>/`:

```
aifred/lib/tts_engines/
├── base.py              # TTSEngine: base class including the container scaffolding
├── registry.py          # finds the folders, builds TTS_ENGINES
├── xtts/
│   ├── engine.py        # the TTSEngine subclass
│   └── i18n.json        # {"de": "...", "en": "..."} — label for dropdowns
├── qwen3local/ · moss/ · fishspeech/ · piper/ · edge/ · espeak/ · browser/ · dashscope_audio3/
```

- The **folder name is the engine key**. `registry.py` imports `*/engine.py` of every folder;
  the `TTSEngine` subclass thereby registers itself, sorted by `display_order`. There is no
  central dict and no list to keep in sync.
- `i18n.json` holds the long label in German and English (`engine.label(lang)`).
- The **default escalation list** of a fresh install is derived from the engines: all with
  `in_default_escalation = True`, in `display_order`
  (`registry.default_escalation()`, via `config.default_tts_escalation()` in
  `DEFAULT_SETTINGS["tts_escalation"]`). The browser has the highest `display_order` and so
  comes last. See [tts-escalation.md](tts-escalation.md).

### Container scaffolding in `base.py`

Start, stop and readiness of a container exist **once** in
[`base.py`](../../../aifred/lib/tts_engines/base.py) (`start()`, `stop()`, `ensure_ready()`,
`is_running()`). A container engine only sets class attributes and overrides hooks where its
container differs:

| Attribute / hook | Meaning | Default |
|------------------|---------|---------|
| `runs_in_container`, `needs_gpu` | Docker container; occupies VRAM | `False` |
| `image_name` | local Docker image — its existence decides "installed" (`is_installed()`) | `None` |
| `compose_subdir` | folder under `docker/tts/` if it differs from the key (`moss` → `moss-tts`) | key |
| `default_port` | port of the REST API; `None` = no REST (cloud/CLI), the engine cannot run on another machine | `None` |
| `health_path` | health endpoint relative to the service URL | `/health` |
| `_model_ready(health)` | when the health answer means "model loaded" | `health["model_loaded"]` |
| `_device(health)` | compute target from the health answer (`cuda:0`, `cpu`) | `health["device"]` |
| `startup_timeout_s` | seconds for container and model to become ready (> 0 for container engines) | 0 |
| `restart_when_on_cpu` | restart the container once if it lands on the CPU although a GPU is free (MOSS) | `False` |
| `max_parallel_requests` | simultaneous syntheses per engine and machine | 2 |
| `in_default_escalation` | part of the default escalation list | `False` |
| `cloud` | the text leaves the house (label in the list) | `False` |
| `default_speech_unit` | default unit of spoken output (`sentence`/`paragraph`/`whole`) | `sentence` |
| `default_voice` | voice when neither the user nor `agents.json` names one | `None` |

Times of the existing container engines: XTTS 60 s, Qwen3-TTS 240 s, MOSS 180 s, Fish-Speech
600 s (`startup_timeout_s`); Qwen3-TTS has `max_parallel_requests = 1`.

The same scaffolding applies to containers on another machine (`at_host()`): nothing is started
from here, only the health check is used, the host starts its containers itself (see
[tts-vram-workflow.md](tts-vram-workflow.md)).

### Completeness test

[`tests/test_tts_engine_completeness.py`](../../../tests/test_tts_engine_completeness.py)
checks for all engines:

- Every folder with an `engine.py` holds exactly one registered engine; the folder name is the key.
- Every engine has its folder (`package_dir`) and non-empty labels in `de` and `en` that
  round-trip through `tts_key_to_label` / `tts_label_to_key`.
- Every container engine matches its `docker-compose.yml` (`image:` name, host port
  `default_port`) and has `startup_timeout_s > 0`.
- Engines without a container declare neither `image_name` nor a compose file.
- The default escalation list comes from the engines, the browser comes last, all entries are
  enabled and `host: null`.

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
`resolve_tts_reserve()` only. Engines that grow during generation (XTTS, Qwen3-TTS,
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
5. **Engine folder** `aifred/lib/tts_engines/<key>/` with `engine.py` (`TTSEngine` subclass:
   `key`, `label_short`, `display_order`, for container engines `runs_in_container`, `needs_gpu`,
   `image_name`, `default_port`, `startup_timeout_s`, optionally `compose_subdir`, `health_path`,
   `_model_ready`, `_device`, `restart_when_on_cpu`) and `i18n.json` (`de`/`en`). The folder name
   is the key and must match the suffix of the llama-swap profiles `<model>-tts-<key>`.
   Start/stop/readiness come from the base class — do not rebuild them.
6. **`generate_speech()`**: send only text + speaker name + language (for container engines
   via `_synthesize_via_http()`), response as an audio body, never base64; on failure raise
   `TTSFailure` instead of returning `None`.
   **Registration:** nothing more — [`registry.py`](../../../aifred/lib/tts_engines/registry.py)
   discovers the folder automatically. If the engine belongs in the default escalation list:
   `in_default_escalation = True`.
7. **VRAM reserve**: nothing to maintain — the stress burn-in measures it on the
   first calibration (see above).
8. **Port** according to the scheme.
   Finally run `python -m pytest tests/test_tts_engine_completeness.py`.
9. Calibration profile in `~/.config/llama-swap/config.yaml` as a
   `<model>-tts-<engine>` variant (see the AIfred calibration docs).
