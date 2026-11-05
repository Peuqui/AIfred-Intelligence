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
[`aifred/lib/config.py`](../../../aifred/lib/config.py), enough for bursts of
10 keyframes) and a quantized KV cache (`-ctk/-ctv q8_0` =
`VLM_KV_CACHE_TYPE`; for the 4B a 7,396 instead of 8,988 MiB peak), without a
draft model. The home profile `<base>-visiond` is pinned via
`CUDA_VISIBLE_DEVICES` to the side-channel card; in addition every other card
gets a **placement variant** `<base>-visiond-gpu<N>` with the same command
line. `fitting_visiond`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py))
picks an already loaded placement when describing, otherwise the home if it
fits, otherwise the fitting card with the most free VRAM — so the describer
also runs next to a main model that occupies all five cards
(DeepSeek-V4-Flash: the 4B on GPU 0). All of them run in the llama-swap group
`vision` **in parallel** with the chat LLM — no model swap for an image
description; the group holds exactly one describer.

The profiles are maintained by the autoscan
([`scripts/llama-swap-autoscan.py`](../../../scripts/llama-swap-autoscan.py)):
`ensure_visiond_profiles` creates a `-visiond` profile for every model with a
matching `mmproj-*.gguf` (GPU pin via `pick_vlm_gpu`, member of the `vision`
group), `enforce_visiond_flags` pulls `-c` and KV type of the home profiles to
`VLM_NUM_CTX`/`VLM_KV_CACHE_TYPE` (SSOT), `sync_visiond_placements` derives
the placement variants from their home (rewrites deviating ones, removes
variants without home or card), and `cleanup_stale_vlm_variants` removes `-vlm-`
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

**The describer list** ([`aifred/lib/describer_list.py`](../../../aifred/lib/describer_list.py)),
edited in the Vigilantia settings like the TTS list (arrows, switches): entries are a model of another
machine (`<machine>/<model>`), this machine (the VLM of the Vigilantia settings via its `-visiond` profile)
and the main model, when it sees itself. The first entry switched on that can right now describes. The list
shown and saved is always matched with what is there: new sources come switched off at the end, a model an
answering machine no longer has drops out, the entries of a machine that does not answer stay. Without a
stored list: this machine, then the main model. Two rules are fixed and no entries (reasons below).

**Rule A — `chat_describer(main_model)`** (chat uploads, Symposion, sandbox
screenshots, uploaded images in the vision tool):

1. If the main model can see (`has_native_vision`), it describes itself and is never evicted. Fixed: it
   answers the turn anyway; if it sees, it gets the image directly with history and tools; any other
   describer would hand it only a text description, and reloading costs more.
2. Otherwise the first entry of the list switched on that can right now (the entry "main model" does not
   count here; "this machine" only through a `-visiond` profile).
3. Otherwise the vision LLM from the main settings via its `-visiond`
   profile, if it fits alongside (`check_visiond_fits`, see below).
4. Otherwise the effective variant of the vision LLM — it evicts the chat LLM.
5. No vision LLM configured: `NoVisionModelError`.

**Rule B — `camera_describer(vlm_model, explicit=…)`** (Vigilantia watcher,
event analysis, bulk, vision tool with a camera source):

1. The first entry of the list switched on that can right now: a model of another machine (answers, not
   held, fits there), this machine (the camera VLM, e.g. the 4B, fits next to what is loaded or already
   runs → its `-visiond` profile; an Ollama VLM over the side channel) or the loaded model that sees itself.
2. Otherwise, only on an explicit request (`explicit=True`: the Casus button "Analyse", the user asking
   AIfred about a camera image), the vision LLM, evicting. No question first: the click or the question is
   the consent; the debug console announces it.
3. Otherwise `NoVisionModelError`: automatic paths (watcher, alarms, live, night run) never evict; the image
   goes out without a description and can be described later. Fixed: a motion event would otherwise unload
   the main model, and reloading it takes minutes.

An Ollama model (no llama-swap entry) stays unchanged. `analyze_sequence`
([`aifred/lib/vision_analyzer.py`](../../../aifred/lib/vision_analyzer.py))
only dispatches: `has_native_vision` → llama-swap, otherwise Ollama.

**Does an entry see?** `has_native_vision`
([`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py)) reads the
llama-swap entry's start command: llama.cpp with `--mmproj`; vLLM when the
checkpoint carries a vision encoder and neither `--language-model-only` nor an
image limit of 0 in `--limit-mm-per-prompt` switches it off (vLLM then rejects
every image). The model's name does not count.

**Does it fit alongside?** `check_visiond_fits`
([`aifred/lib/vision_vram_check.py`](../../../aifred/lib/vision_vram_check.py)):
an already loaded profile always fits; otherwise the burn-in peak from
`data/vlm_vram_cache.json` (model × context) plus `VLM_HEADROOM_MIB`
against the free VRAM of the cards the profile is pinned to via
`CUDA_VISIBLE_DEVICES` (unpinned: sum of all cards). A missing measurement or
card means it does not fit.

**While a big model loads, no describer is added.** `load_in_progress`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)) asks GPU-Orc's traffic
light (`GET /loading`). During a load nvidia-smi shows cards that are about to
fill up; a describer going by the free memory would take the model's room and it would die with an
OOM. A describer that is already loaded keeps working. Otherwise there is none: automatic paths
send the picture without a description, which can be made afterwards, and not even an explicit
request evicts a loading model (`NoVisionModelError`).

### Describers on other machines

[`aifred/lib/remote_describers.py`](../../../aifred/lib/remote_describers.py). Another machine
(e.g. a workstation with its own card) runs its own llama-swap and its own GPU-Orc. AIfred knows it
exactly once, in `VISION_REMOTE_HOSTS` (`.env`, a JSON list with `name`, `llamaswap_url`,
`gpu_orc_url`, `gpu_orc_token_file`), without models: which models it has, which of them see
(`capabilities.vision`), their context and whether they are loaded, its `/v1/models` reports. Every
model there that sees is an entry of the describer list; a new profile there shows up by itself, switched
off at the end.

- **Address:** a model there is called `<machine>/<model>`, the way llama-swap names the models of
  its peers. Callers hand the id on like any other; `analyze_sequence` and `_describe_images` send to
  the machine's llama-swap, with the id there. No `peers:` entry in the own llama-swap YAML is needed
  (it would need a fixed model list).
- **Reachable?** Before every choice one `GET /health` with a 0.5 s limit
  (`VISION_REMOTE_HEALTH_TIMEOUT_S`). A machine that is off or asleep is the normal case: the choice
  goes on to the next stage without a warning, the log file says why.
- **Does it fit?** Loaded fits; while a big model loads there (traffic light of the GPU-Orc there)
  nothing new goes on; otherwise the measured need plus `VLM_HEADROOM_MIB` against the free memory of
  the cards there (`/status` of the GPU-Orc there; what it clears before a load counts as free). A
  model never measured does not fit.
- **Measuring:** a button next to the choice (`stress_prewarm_remote` in
  [`aifred/lib/vlm_stress_prewarm.py`](../../../aifred/lib/vlm_stress_prewarm.py)): the llama-swap
  there unloads everything and loads the model with the burn-in's stress image; stored is the cards'
  peak minus what they held before, under `<machine>/<model>` and the context the machine reports.
- **Clearing the cards:** before a load AIfred asks the GPU-Orc of the machine the model loads on
  (image path `release_side_channels_for_describer`, chat path `_free_gpus_for_load`).
- **Keeping the card free:** the status bar shows a line per machine (its GPU-Orc: reachable, held,
  memory in use, what is on the card) with the button "Keep the card free" / "Release the card"
  (`/api/service-status/hosts`, `/api/service-status/hold`). Keeping it free means `POST /hold` at the
  GPU-Orc there: it clears every card at once and names the hold on its traffic light until
  `DELETE /hold`; the hold lives in a file there and outlasts a restart. Meanwhile nothing fits there
  (`check_fits`), the burn-in refuses, and the TTS-Suite starts no container there (it asks the machine's
  traffic light before every start). The same light also stops this machine's describers
  (`load_in_progress`) while somebody holds its cards.
- **Failure mid-request:** `describe_with_second_attempt` (every image path). If the machine answers
  no more afterwards, the debug console says so loudly and the same choice runs exactly once more: it
  no longer finds the machine and takes the next stage. If it still answers, the error lies in the
  request or the model and reaches the caller unchanged. In the chat path the backend first retries
  connection errors itself (`worth_retrying`, window `STREAM_RETRY_WINDOW_S`); the second attempt
  follows after that.

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

**Thinking while describing:** every description (`_describe_images`, `analyze_sequence` →
`_analyze_via_llamacpp`) is a task of the agent "Vision". It thinks as the vision row says
for the model that describes (`agent_settings.thinking_mode`, stored per model) and sends
the level in `chat_template_kwargs` like the chat backends. The 4B thus stays without
thinking, GLM as main model describes with the level set for GLM in the vision row. VL
Direct follows the row of the agent that answers.

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
by [GPU-Orc](https://github.com/Peuqui/GPU-Orc), a service of its own per machine:
`_free_gpus_for_load` ([`aifred/backends/base.py`](../../../aifred/backends/base.py))
runs in `_pre_request_check` of the llama.cpp and vLLM backends on every
request and reads llama-swap's `/running`. If the target model is already
running, nothing is touched. Otherwise the request triggers a load (also a
re-load of the same model after its ttl), and AIfred first asks GPU-Orc to clear
the model's cards (`POST /free {"model": …}`, `aifred/lib/gpu_orc_client.py`).
GPU-Orc reads the entry's cards from the llama-swap config, releases the GPU
workers of the STT and TTS suites there and unloads the persistent models of
the groups with `persistent: true` (sidecars `-visiond`, `-embed`). Profiles
with a `-vlm-` marker (reserve by calibration) and the sidecars themselves
evict no sidecar (`no_eviction_models` in GPU-Orc's config). What GPU-Orc cannot
free, or a GPU-Orc that does not answer, is logged loudly; the load goes on.
The describer is cleared by the same call with its `-visiond` profile
(`release_side_channels_for_describer`), and the room check
(`check_visiond_fits`) counts the measured memory GPU-Orc's `GET /status`
reports for STT and TTS on the card as free.

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
- Burn-in per describer VLM: without a measurement in
  `data/vlm_vram_cache.json` a profile never fits (e.g. Qwen3VL-30B-A3B so far
  unmeasured).
- Decommission the Ollama services (sudo, user decision) — since package 2
  no vision path calls Ollama anymore as long as describer profiles exist.
