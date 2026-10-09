# Vision Routing: The llama.cpp Describer Path

> **Deutsche Version:** [vision-routing.md](../../de/architecture/vision-routing.md)

Who looks at an image — chat upload, Symposion, sandbox screenshot, vision
tool, Vigilantia — and how the vision model gets next to the loaded chat LLM
without evicting it from VRAM. Status (2026-10-09): two selection rules in
[`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)
replace the former four-step precedence; whether a describer fits alongside is
decided by the measured burn-in peak, no longer by a reserved profile alone.
The side channel runs primarily via llama.cpp/llama-swap; Ollama remains as
the path for setups without describer profiles.

## Core idea

The chat LLM runs via **llama-swap** and occupies most of the VRAM.
For image analysis there are dedicated **describer profiles** (`<base>-visiond`
in the llama-swap config): lean instances of the same vision model with
a small context (`-c 24576` = `VLM_NUM_CTX` in
[`aifred/lib/config.py`](../../../aifred/lib/config.py)), without a draft
model, pinned via `CUDA_VISIBLE_DEVICES` to the side-channel card. They run in
the llama-swap group `vision` **in parallel** with the chat LLM — no model
swap for an image description.

The profiles are maintained by the autoscan
([`scripts/llama-swap-autoscan.py`](../../../scripts/llama-swap-autoscan.py)):
`ensure_visiond_profiles` creates a `-visiond` profile for every model with a
matching `mmproj-*.gguf` (GPU pin via `pick_vlm_gpu`, member of the `vision`
group), `enforce_visiond_ctx` pulls the `-c` of all `-visiond` profiles to
`VLM_NUM_CTX` (SSOT), and `cleanup_stale_vlm_variants` removes `-vlm-`
variants whose describer no longer has a profile. The calibration discovers
its describer choice from these profiles (`vlm_calibration_choices`).

The reserve is provided by calibration: as soon as a vision model is active,
`resolve_variant_suffix`
([`aifred/lib/calibration/llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py))
selects the `<base>-vlm-<key>` profile for the chat LLM, which leaves the reserve slot
(e.g. ~8 GB on a V100) free. Exception (`_is_self_describer`): if the vision
model is the chat model itself, there is nothing to reserve — the VLM tiers are
skipped and the base profile with full context wins.

## Selection rules

Both rules return a `Describer(model, evicts_chat_model)`; when it evicts the
chat LLM, `eviction_notice` announces it (reloading can take several minutes).

**Rule A — `chat_describer(main_model)`** (chat uploads, Symposion, sandbox
screenshots, uploaded images in the vision tool):

1. If the main model can see (`has_native_vision`), it describes itself.
2. Otherwise the vision LLM from the main settings via its `-visiond`
   profile, if it fits alongside (`check_visiond_fits`, see below).
3. Otherwise the effective variant of the vision LLM — it evicts the chat LLM.
4. No vision LLM configured: `NoVisionModelError`.

**Rule B — `camera_describer(vlm_model, explicit=…)`** (Vigilantia watcher,
event analysis, bulk, vision tool with a camera source):

1. The camera VLM (e.g. the 4B) fits next to what is loaded or already runs →
   its `-visiond` profile.
2. Otherwise a loaded profile that can see by itself.
3. Otherwise, only on an explicit request (`explicit=True`, e.g. Casus or the
   user asking AIfred), the vision LLM, evicting.
4. Otherwise `NoVisionModelError`: automatic paths never evict; the image goes
   out without a description and can be described later.

An Ollama model (no llama-swap entry) stays unchanged. `analyze_sequence`
([`aifred/lib/vision_analyzer.py`](../../../aifred/lib/vision_analyzer.py))
only dispatches: `has_native_vision` → llama-swap, otherwise Ollama.

**Does it fit alongside?** `check_visiond_fits`
([`aifred/lib/vision_vram_check.py`](../../../aifred/lib/vision_vram_check.py)):
an already loaded profile always fits; otherwise the burn-in peak from
`data/vlm_vram_cache.json` (model × context) plus `LLAMACPP_VLM_HEADROOM_MB`
against the free VRAM of the cards the profile is pinned to via
`CUDA_VISIBLE_DEVICES` (unpinned: sum of all cards). A missing measurement or
card means it does not fit.

### Chat images: VL Direct or two steps

`send_message` ([`aifred/state/_chat_mixin.py`](../../../aifred/state/_chat_mixin.py))
asks `_image_describer` ([`aifred/state/_agent_config_mixin.py`](../../../aifred/state/_agent_config_mixin.py)),
which applies rule A to the effective main model:

- **Main model can see, one agent:** VL Direct — the main model receives the
  images and answers as the agent (`_process_vision_request`).
- **Otherwise (or Symposion with ≥2 agents):** `_describe_images` describes
  the images once, neutrally (`vision/task_instruction_handoff`, with a user question `…_handoff_question`); the
  description is appended to the user turn as `[Image content: …]`, and the
  normal text pipeline answers — the main model with its full prompt, tools
  and history, or all Symposion agents from the same basis. For follow-up
  questions the agent takes another look via `vision_analyze` when needed
  (the `/_upload/` URL is in the turn).
- **No vision LLM:** the image goes on without a description, with a note in
  the debug console.

## Unload semantics (llama-swap groups)

```yaml
groups:
  main:
    exclusive: true    # chat models evict each other as before
    swap: true
  embed:
    exclusive: false   # CPU-only -embed servers, never part of the swap
    swap: true
    persistent: true
  vision:
    exclusive: false   # describer load does not evict the chat LLM
    swap: true         # describers evict each other (1 slot)
    persistent: true   # llama-swap NEVER unloads describers on its own
```

The autoscan (`update_groups_in_yaml`) writes these three groups.

Important: llama-swap's `exclusive` works **per request** — without
`persistent`, every request to the (already running!) chat LLM would unload the
describer (verified empirically 2026-08-16). With `persistent`:
**matching profile combinations coexist indefinitely; cleanup
happens only via the profile's `ttl`** (e.g. 900 s idle for
`Qwen3VL-4B-Instruct-Q8_0-visiond`; new profiles get their ttl from
`ttl_for_model_size`).

The conflict case — a main model wants a card a sidecar holds — is handled
by AIfred itself: `_free_gpus_for_load`
([`aifred/backends/base.py`](../../../aifred/backends/base.py))
runs in `_pre_request_check` of the llama.cpp and vLLM backends on every
request and reads llama-swap's `/running`. If the target model is already
running, nothing is touched. Otherwise the request triggers a load (also a
re-load of the same model after its ttl) and the guard first releases Whisper's
GPU worker (`release_whisper_gpu`; a running transcription gets a grace
period), then handles the sidecars. Profiles with a `-vlm-` marker (reserve by
calibration) and the sidecars themselves never evict sidecars; with no sidecar
(`-visiond`, `-embed`) loaded there is nothing more to do. It then
compares the GPUs of the target entry and of each running sidecar
(`CUDA_VISIBLE_DEVICES` from the llama-swap config, `entry_gpu_uuids`) and
unloads every sidecar sharing a GPU via `POST /api/models/unload/<sidecar>`
before the load starts. If the GPUs cannot be determined, it unloads all
sidecars. llama-swap has no VRAM/profile semantics — the decision is made by
the instance that understands the profiles.

## Badge logic

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
follows the runtime choice: `🧠 Chat-LLM` if the vision model is the main
model itself with its own vision encoder; `⚡ No Swap` if its `-visiond`
profile currently fits alongside (`check_visiond_fits`) or, without a
`-visiond` profile, it runs via the Ollama side channel; otherwise `🔄 Swap`.
Badges are recomputed per session, not taken from the global startup state.

## Verified scenarios (2026-08-16)

| Scenario | Behavior |
|---|---|
| Request to the warm chat LLM | Describer stays loaded |
| Chat LLM switch to another `-vlm-` reserve profile | Describer survives the swap |
| Chat LLM switch to a base profile without reserve | AIfred guard unloads the describer before the load |
| DeepSeek (all 5 cards) + describer | Reserve holds: 8.3 GB free on the V100, describer (6.7 GB) fits, chat LLM then answers in ~1 s |
| 15 min without an image request | `ttl` unloads the describer |

## Open items

- Describer quality comparison Qwen3.5-4B vs. Qwen3VL-4B; afterwards
  dispose of the old Qwen3VL-4B model (GGUF + Ollama store).
- Only create `-vlm-` reserve variants when the chat LLM really needs them
  (threshold logic). (The generation of `-visiond` profiles is done: autoscan,
  see above.)
- Dynamic placement (describer on the card with the most free
  VRAM instead of a fixed pin) via generated placement variants.
- Decommission the Ollama services (sudo, user decision) — since package 2
  no vision path calls Ollama anymore as long as describer profiles exist.
