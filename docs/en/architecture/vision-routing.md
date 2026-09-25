# Vision Routing: The llama.cpp Describer Path

> **Deutsche Version:** [vision-routing.md](../../de/architecture/vision-routing.md)

How an image description (Vigilantia, sandbox screenshot, vision tool) is
dispatched to a vision model without evicting the loaded chat LLM from VRAM.
Status (2026-09-25): vision rework packages 1 (describer path, 2026-08-16)
and 2 (Vigilantia migrated to the describer path, 2026-08-16) are
implemented; since then: autoscan-generated describer profiles, the chat LLM
as its own describer, and a GPU-based sidecar eviction guard. The side
channel runs primarily via llama.cpp/llama-swap; Ollama remains as the legacy
path for setups without describer profiles.

Chat image uploads (VL Direct, Symposion) do not use this path: there,
`_vl_choice` ([`aifred/state/_agent_config_mixin.py`](../../../aifred/state/_agent_config_mixin.py))
picks a vision-capable main model itself, otherwise the model of the vision role.

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

## Routing precedence

SSOT is the model choice in `analyze_sequence`
([`aifred/lib/vision_analyzer.py`](../../../aifred/lib/vision_analyzer.py))
for all its callers (Vigilantia watcher, doorman, event analysis, bulk,
sandbox, vision tool), first match wins:

1. **The chat LLM describes itself** — `self_describer_profile`
   ([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)):
   the configured vision model is the same model as the chat LLM
   (`same_model`, name-normalized and without variant suffixes) and its
   llama-swap profile loads its own vision encoder (`has_native_vision`:
   llama.cpp `--mmproj`, vLLM without `--language-model-only`). Returned is the
   profile actually loaded (e.g. a `-speed` variant); if a different model is
   running, this path does not apply. No second load of the same model — the
   description serializes with the chat in its single slot (`-np 1`).
2. **A `<vision-model>-visiond` profile exists → route there** —
   `visiond_profile_for`. The preferred path: same backend, only the profile
   name changes. The role's variant suffixes are stripped before the lookup
   (`strip_variant_suffixes`), and Ollama spellings
   (`qwen3-vl:4b-instruct-q8_0`) also match after name normalization — so ALL
   Vigilantia paths run through this resolution without their plugin settings
   being touched.
   `prewarm_vlm` (vision_mode "live") loads the describer profile via a
   llama-swap request; `check_vlm_fits` (bulk worker) is skipped on the
   describer path (the reserve slot is the guarantee).
3. **Otherwise** the model stays unchanged. Dispatch via `has_native_vision`:
   a llama-swap model with its own vision encoder describes via llama-swap,
   everything else goes to Ollama (`_analyze_via_ollama`, legacy path for setups
   without `-visiond` profiles).

Sandbox screenshots (`describe_sandbox_screenshots`,
[`aifred/lib/sandbox.py`](../../../aifred/lib/sandbox.py)) prefer the
configured vision role (its `-visiond` profile); a vision-capable main model
(`is_vision_model_sync`,
[`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py)) describes
only when no vision role is configured — otherwise every describe call would
overwrite the chat context in the only slot. `is_vision_model_sync` decides
for llama-swap entries by what their cmd loads (`has_native_vision`), and for
other IDs by name patterns on the base ID (`strip_variant_suffixes` — the
suffixes `-vlm-qwen3vl4b` and `-vllm` contain "vl" and would otherwise wrongly
classify text models as vision-capable).

`maybe_route_to_ollama` (`vision_routing.py`) describes the same order
(`-visiond` profile before the Ollama side channel) for a
`(backend_url, backend_type, model)` tuple; in production code it currently
has no caller, only tests.

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
by AIfred itself: `_evict_conflicting_sidecars`
([`aifred/backends/base.py`](../../../aifred/backends/base.py))
runs in `_pre_request_check` of the llama.cpp and vLLM backends (only on model
change, cached via `_last_guarded_model`). Profiles with a `-vlm-` marker
(reserve by calibration) and the sidecars themselves never evict. Otherwise the
guard reads llama-swap's `/running`; if the target model is already running or
no sidecar (`-visiond`, `-embed`) is loaded, nothing is touched. It then
compares the GPUs of the target entry and of each running sidecar
(`CUDA_VISIBLE_DEVICES` from the llama-swap config, `entry_gpu_uuids`) and
unloads every sidecar sharing a GPU via `POST /api/models/unload/<sidecar>`
before the load starts. If the GPUs cannot be determined, it unloads all
sidecars. llama-swap has no VRAM/profile semantics — the decision is made by
the instance that understands the profiles.

## Badge logic

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
shows `🧠 Chat-LLM` if the vision model is the chat LLM itself with its own
vision encoder (self-describer, checked first); otherwise `⚡ No Swap` if
(a) a `-visiond` profile exists (group semantics guarantee parallelism)
**or** (b) the Ollama side channel applies and the `-vlm-<key>` reserve profile
(or its `-speed` variant) is calibrated for the current chat LLM; otherwise
`🔄 Swap`. `vlm_key_for_model` matches
after name normalization — the llama-swap spelling
(`Qwen3VL-4B-Instruct-Q8_0`) also activates the automatic reserve.

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
