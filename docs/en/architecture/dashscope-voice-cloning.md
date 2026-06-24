# DashScope Qwen3-TTS — Cloud Voice Cloning

> As of: 2026-06-25 | Region: international/Singapore (`dashscope-intl.aliyuncs.com`)
> Code: [`aifred/lib/dashscope_enroll.py`](../../../aifred/lib/dashscope_enroll.py),
> [`aifred/lib/tts_engines/dashscope.py`](../../../aifred/lib/tts_engines/dashscope.py),
> [`aifred/state/_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)

DashScope is our **cloud TTS engine**: no local GPU, fast results, Qwen3-TTS as the
large model. This document records **how** voice cloning is addressed correctly and
— more importantly — **where the cloud API has fundamental limits**, so we don't have
to research this repeatedly.

## The correct enrollment API (verified 2026-06-25)

DashScope has two easily-confused cloning paths. For the **Qwen3-TTS** voices only
this one applies:

- **Model:** `qwen-voice-enrollment` (NOT `voice-enrollment` — that is the CosyVoice variant)
- **Action:** `"create"` (NOT `"create_voice"`)
- **Endpoint:** `POST /api/v1/services/audio/tts/customization`
- **Reference audio:** inline as a **base64 data URI** in `input.audio.data`
- **Synthesis model:** `qwen3-tts-vc-2026-01-22` — synthesis **must** use the same
  `target_model` the voice was created with.

The wrong path (SDK `VoiceEnrollmentService` with `model="voice-enrollment"`,
`action="create_voice"`, `url=...`) is the **CosyVoice** variant and fails for the
Qwen models with "preprocess service not found".

### Full reference length instead of a 10-second cap

We pass the reference WAV at **full length** (no `max_prompt_audio_length`). This
gives the cloud the complete reference instead of the 10-second default and yields
audibly better accent/character (empirically confirmed: 25 s > 10 s). The official
docs recommend 10–20 s (max 60 s) — both are within bounds; what matters is a clean,
noise-free reference.

## Automatic enrollment on engine switch

Selecting DashScope as the TTS engine enrolls the cloned voices **automatically** —
no manual trigger. Flow ([`_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)
→ `set_tts_engine_or_off`):

1. The SSOT voices folder [`docker/tts/voices/<Name>/<Name>.wav`](../../../docker/tts/voices/)
   is scanned.
2. `enroll_progress()` clones every **new or changed** reference WAV at full length.
3. It runs as visible generator steps (`add_debug` + `yield`) — each line reaches the
   debug console immediately, including a **completion line**, so the user sees both
   progress AND the end.

### Verification: is a voice already enrolled?

The cloud `list_voices` returns **0** for our enrollments and is useless as a check.
Instead a **local mapping** [`data/tts/dashscope_voices.json`](../../../data/tts/)
records, per voice name, the returned `voice_id`, the `target_model`, and the
**SHA-256 of the reference WAV**. A voice counts as enrolled **if and only if** its
name is in the mapping AND the current WAV hash matches the stored one:

- new WAV (name missing in mapping) → enroll
- edited WAV (hash differs) → re-enroll
- unchanged WAV (hash matches) → skip (no cloud call, no cost)

This makes the switch effectively instant after the first run (hash checks only).
**Force a re-enroll:** delete the mapping entry (or the whole file) — the folder is
not watched at runtime; the trigger is always the engine switch.

### Voice list

Cloned voices are **not** hardcoded. `_cloned_voices()` reads the mapping live
(`★ Name` → `qwen-tts-vc-*` id) and layers them on top of the built-in voices. A
freshly enrolled WAV therefore shows up in the selection without a restart.

## Fundamental limits of the cloud API

Researched in the official Model Studio docs (sources below). These limits are
**API-side** (server), not caused by the SDK — an SDK update does not fix them.

### No reference text at enrollment

The `qwen-voice-enrollment`/`create` endpoint takes **audio only**, no transcript
field (`text`/`reference_text`/`prompt_text`/`transcript` do not exist). Cloning is
deliberately audio-only ("3-second cloning", ASR-free).

**This explains the "wooden" sound versus local cloning:** our local qwen3-tts uses
the `with_transcript` mode (the `<Name>.txt` next to the WAV) and can thus carry over
**prosody/style** from the reference — the cloud cannot do this by design and only
transfers timbre.

### No sampling/style parameters for cloned voices

- `temperature`, `top_p`, `top_k`, `repetition_penalty`, `seed`: not
  documented/supported for **any** Qwen3-TTS model. The `**kwargs` of
  `MultiModalConversation.call` passes them through, but the TTS API ignores them.
- The only documented style lever is `input.instructions`
  (+ `optimize_instructions`) — natural-language control of emotion/pace — and it is
  supported **only by `qwen3-tts-instruct-flash`**, **not** by the voice-clone model.
  Cloning and style control are currently mutually exclusive in the cloud (different
  models).

### Model state & SDK

- `qwen3-tts-vc-2026-01-22` is the **current latest** VC snapshot (as of 06/2026).
- An SDK update brings **nothing** for our VC use case: `instruct` support landed
  before our installed 1.25.12; later patch releases (through 1.25.24) contain only
  CosyVoice/ASR/generic fixes, no qwen3-tts-vc changes. The SDK therefore stays put.

### The only real quality lever

For the cloud, only **reference audio quality** remains: clean, noise-free, no
singing, ≥ 24 kHz, 10–20 s (max 60). For genuine prosody/style control (reference
text + sampling), **local cloning** is unavoidable (see
[tts-comparison.md](../models/tts-comparison.md)).

## Sources

- https://www.alibabacloud.com/help/en/model-studio/qwen-tts-voice-cloning
- https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api
- https://www.alibabacloud.com/help/en/model-studio/qwen-tts
- https://qwen.ai/blog?id=qwen3-tts-vc-voicedesign
- https://github.com/dashscope/dashscope-sdk-python
