# Vision Routing: The llama.cpp Describer Path

> **Deutsche Version:** [vision-routing.md](../../de/architecture/vision-routing.md)

How an image request (chat image upload, sandbox screenshot) is dispatched to a
vision model without evicting the loaded chat LLM from VRAM.
Status: vision rework package 1 (2026-08-16) — the side channel
runs primarily via llama.cpp/llama-swap; Ollama remains as the legacy path
for setups without describer profiles.

## Core idea

The chat LLM runs via **llama-swap** and occupies most of the VRAM.
For image analysis there are dedicated **describer profiles** (`<base>-visiond`
in the llama-swap config): lean instances of the same vision model with
a small context (`-c 9216` = `VLM_NUM_CTX`), without draft flags, pinned to
the VRAM reserve card. They run in the llama-swap group `vision`
**in parallel** with the chat LLM — no model swap for an image description.

The reserve is provided by calibration: as soon as a vision model is active,
`resolve_variant_suffix`
([`aifred/lib/calibration/llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py))
selects the `<base>-vlm-<key>` profile for the chat LLM, which leaves the reserve slot
(e.g. ~8 GB on a V100) free.

## Routing precedence

`maybe_route_to_ollama` / `visiond_profile_for`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)),
first match wins:

1. **Vision-capable main model describes itself** (sandbox path only,
   `describe_sandbox_screenshots`): the model is already loaded — no
   second load needed. Detection via `is_vision_model_sync`
   ([`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py)):
   mmproj check on the full profile ID, name heuristic on the
   base ID (`strip_variant_suffixes` — the variant suffix
   `-vlm-qwen3vl4b` contains "vl" and would otherwise wrongly classify e.g. DeepSeek variants
   as vision-capable).
2. **A `<vision-model>-visiond` profile exists → route there.** The
   preferred path: same backend, only the profile name changes.
   The role's variant suffixes are stripped before the lookup, and
   Ollama spellings (`qwen3-vl:4b-instruct-q8_0`) also match
   after name normalization — so ALL Vigilantia paths (watcher,
   doorman, event analysis, bulk) run through the SSOT resolution in
   `analyze_sequence` without their plugin settings being touched.
   `prewarm_vlm` (vision_mode "live") loads the describer profile via a
   llama-swap request; `check_vlm_fits` (bulk worker) is skipped on the
   describer path (the reserve slot is the guarantee).
3. **An Ollama counterpart exists → Ollama side channel** (legacy path,
   only for setups without `-visiond` profiles).
4. **Otherwise:** pass through unchanged — classic llama-swap path with
   swap.

## Unload semantics (llama-swap groups)

```yaml
groups:
  vision:
    exclusive: false   # describer load does not evict the chat LLM
    swap: true         # describers evict each other (1 slot)
    persistent: true   # llama-swap NEVER unloads describers on its own
  main:
    exclusive: true    # chat models evict each other as before
    swap: true
```

Important: llama-swap's `exclusive` works **per request** — without
`persistent`, every request to the (already running!) chat LLM would unload the
describer (verified empirically 2026-08-16). With `persistent`:
**matching profile combinations coexist indefinitely; cleanup
happens only via `ttl`** (900 s idle).

The only real conflict case — switching to a chat profile **without** a
`-vlm-` reserve, which may need the full card — is handled by AIfred itself:
`_evict_visiond_if_conflicting`
([`aifred/backends/llamacpp.py`](../../../aifred/backends/llamacpp.py))
runs in `_pre_request_check` (only on model change, cached), checks
llama-swap's `/running` and unloads the describers via
`POST /api/models/unload` before the load starts. If the target model is
already running, nothing is touched. llama-swap has no VRAM/profile
semantics — the decision is made by the instance that understands the profiles.

## Badge logic

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
shows `⚡ No Swap` if (a) a `-visiond` profile exists
(group semantics guarantee parallelism) **or** (b) the
Ollama side channel applies and the `-vlm-<key>` reserve profile is calibrated for the
current chat LLM. `vlm_key_for_model` matches
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

## Open items (package 3)

- Describer quality comparison Qwen3.5-4B vs. Qwen3VL-4B; afterwards
  dispose of the old Qwen3VL-4B model (GGUF + Ollama store).
- Generate `-visiond` profiles from calibration instead of maintaining them by hand;
  only create `-vlm-` reserve variants when the chat LLM
  really needs them (threshold logic).
- Dynamic placement (describer on the card with the most free
  VRAM instead of a fixed reserve card) via generated placement variants.
- Decommission the Ollama services (sudo, user decision) — since package 2
  no vision path calls Ollama anymore as long as describer profiles exist.
