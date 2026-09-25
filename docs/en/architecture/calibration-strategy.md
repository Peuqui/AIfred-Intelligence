# Calibration Strategy

> **Deutsche Version:** [calibration-strategy.md](../../de/architecture/calibration-strategy.md)

> SSOT for the layer distribution strategy. Used by both the algorithm
> and the AI agent. Do not discuss it twice — when in doubt, look here
> or update this file.

## Goal

For a given model on a multi-GPU rig: find the configuration with
**maximum context at minimum GPU count**, plus optionally a
speed variant with fewer GPUs (= less inter-GPU sync = higher throughput)
at reduced context.

## Hardware assumptions

- Multiple GPUs of different speed classes (compute capability + VRAM size)
- The GPU TTS containers (XTTS, MOSS, Fish-Speech, Qwen3-TTS — every
  engine with `needs_gpu = True` in
  [`aifred/lib/tts_engines/`](../../../aifred/lib/tts_engines/)) and the
  Vigilantia VLM (Ollama) each occupy part of a GPU, NOT a whole GPU. They
  share **one card** of the **side-channel tier** — the compute class below
  the fastest one, which stays reserved for the main LLM. For details see
  the section [Side-channel placement](#side-channel-placement-vlm--tts).

## Sorting (generic)

GPUs are sorted by:

1. **Compute capability** (desc) — RTX 8000 (7.5) before V100 (7.0) before P40 (6.1)
2. **Total VRAM** (desc) as tiebreaker at equal CC — e.g. two RTX 8000 ×
   48 GB are equal under this criterion
3. **GPU name**, then **UUID** (asc) as final, deterministic tiebreakers

Implemented in [`enumerate_gpus()`](../../../aifred/lib/calibration/gpu.py).
GPUs are identified by their NVIDIA UUID, and llama-server sees them in
exactly this order via `CUDA_VISIBLE_DEVICES=<UUIDs>`. The side-channel
picker ranks separately with `_rank()` in
[`vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
(compute capability desc, total VRAM desc, PCI_BUS_ID index asc).

## Side-channel placement (VLM + TTS)

> SSOT: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py).
> Hardware-agnostic — **nothing** is hardcoded to "always V100".

The fastest compute class stays completely free for the chat LLMs
(main + automatic via llama-swap). The side channels — the
Vigilantia VLM (Ollama) and the TTS containers — run on the
**side-channel tier**: the compute class directly below it. Both share
**one card** of this tier (decision 2026-08-29), so all other cards stay
free for backend topologies (e.g. TP2×PP2 across four cards in the vLLM
calibration).

### Tier formation (`_side_channel_tier()`)

1. **Candidates** = all cards **below** the fastest
   compute class (the top tier stays LLM-only).
2. **Homogeneous setup** (all cards of the same class, e.g. only V100s):
   the fastest card stays with the LLM, all others form the tier.
3. **Compute floor (soft):** candidates with compute ≥ 7.0 (Volta+)
   are preferred. A P40 (Pascal, 6.1) only becomes the
   side-channel host if there is no faster card at all
   (last resort instead of "no vision"). Constant:
   `SIDE_CHANNEL_MIN_COMPUTE = (7, 0)`.

### Shared card for TTS and VLM (`pick_side_channel_gpu()`)

- **`pick_side_channel_gpu()`** → the shared card: the **second** card of
  the tier; if the tier has only one card, that one.
- **Weakest attachment first:** if the tier cards sit at different PCI
  depths (`attachment_depth()`, e.g. a card behind a USB4/Thunderbolt
  tunnel with extra bridges), the most deeply attached card becomes the
  shared card. Side channels are single-card loads and tolerate the
  tunnel; a TP/PP group does not, because every token syncs across all
  of its cards. With uniform depth (or unknown bus IDs) the rule stays
  "second tier card".
- **`pick_tts_gpu()`** and **`pick_vlm_gpu()`** both return this shared
  card. TTS containers get its UUID via `get_tts_gpu_uuid()`
  ([`process_utils.py`](../../../aifred/lib/process_utils.py)).
- **InsightFace** (face recognition) follows the VLM: `gpu_id: "auto"` in
  the vision plugin's `face_recognition` settings resolves via
  `resolve_gpu_id()` → `pick_vlm_gpu()`.

The price of the shared card: very large TTS engines (Fish, MOSS) no longer
fit onto one card together with the VLM. The combo capacity check of the
calibration catches this: if TTS reserve + VLM reserve exceed the shared
card's total VRAM, exactly this combo profile is not written (the rest runs).

### Examples

| Setup | LLM tier | Shared TTS + VLM card |
|---|---|---|
| 2× RTX 8000 + 1× V100 + 2× P40 | RTX 8000 ×2 | V100 (only tier card, P40 excluded by floor) |
| 2× RTX 8000 + 3× V100 (today) | RTX 8000 ×2 | V100 #2 — or the more deeply attached V100 if the depths differ (on the Mini the tunnelled card, see `TestWeakAttachmentPreference` in [`tests/test_vision_gpu_select.py`](../../../tests/test_vision_gpu_select.py)) |
| 3× RTX 8000 | RTX 8000 #1 | RTX 8000 #3 (second tier card) |
| 3× P40 only | P40 #1 | P40 #3 (soft fallback) |
| 1× RTX 8000 + 1× P40 | RTX 8000 | P40 (last resort) |

### P40 floor: measurements

Measured (qwen3-vl Q8_0, vlm_stress_image, warm, 100 decode tokens):

| GPU | Prefill 4B | Prefill 8B | Decode 4B | Decode 8B |
|---|---|---|---|---|
| V100 | 0.94 s | 1.92 s | 96.6 tok/s | 68.8 tok/s |
| RTX 8000 | 1.03 s | 2.34 s | 93.0 tok/s | 59.1 tok/s |
| **P40** | **4.07 s** | **6.89 s** | **45.7 tok/s** | **29.0 tok/s** |

A complete 8B analysis (prefill + decode, model resident) costs
**~3.4 s** on the V100 and **~10.3 s** on the P40 (3×). The prefill — the
vision encoding, the dominant share for a VLM — is 3.6–4.3× slower
on the P40. Hence the floor: Pascal cards are the wrong choice as
vision host as long as something faster is available.

### Deployment note (Ollama)

The calibration accounts for the VLM on the card chosen by the picker.
For Ollama to actually load the model there at runtime, the
systemd drop-in (`CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES`,
text generated by `ollama_override_text()`) must be pinned to that card —
otherwise Ollama picks greedy first-fit. This VLM pin lives in externally
managed configs (`ollama-vlm.service`, `-visiond` entries of the llama-swap
config) that are **not** rewritten automatically. That is why the shared
card is deliberately the second tier card, the one the VLM always ran on;
TTS resolves its card itself via `get_tts_gpu_uuid()`. If the picker's
choice changes (hardware change, attachment depth), the VLM pin must be
adjusted by hand.

## User preferences (binding)

In this order:

1. **Lowest GPU count** — less inter-GPU sync = faster. If
   `[a,b,c,0]` reaches native context across 3 GPUs, NEVER prefer `[w,x,y,z]` across
   4 GPUs.
2. **Fill the fastest GPU class first** — layers land primarily on the RTX
   8000s, P40s only as spillover.
3. **Packed full up to the safety margin** ([`LLAMACPP_VRAM_SAFETY_MARGIN`](../../../aifred/lib/config.py),
   192 MB Linux, 1536 MB under WDDM, i.e. WSL2/Windows) — NO spreading of headroom. A GPU with 2 GB
   free is fine; one with 20 GB free is waste if we would have to add inactive
   GPUs.
4. **Reach native context** — never reduce if avoidable.
   `model.native_context` is the HARD upper bound (llama.cpp clamps above it).

Example splits for an 80B model, 4 GPUs (2× RTX 8000, 2× P40):

| Split | Assessment |
|---|---|
| `[23, 23, 2, 0]` | ✅ GOOD — fastest GPUs nearly full, 2 layers of spillover, 4th GPU idle |
| `[23, 24, 1, 0]` | ❌ OOM — an RTX 8000 with 24 layers has no room left for the KV cache |
| `[22, 22, 9, 9]` | ❌ bad — needlessly activates the 4th GPU, leaves headroom on the RTX 8000s |

## First-GPU handicap

Within the fastest compute class, one GPU is marked as `first_in_class`
— the one with the least free VRAM (usually the
display-carrying GPU on desktop systems). It is the only marked card in the
whole system: in the pinned fill order it is CUDA device 0; slower classes
carry none of its buffers and get no handicap. For this GPU the optimizer
subtracts a **handicap** from the usable VRAM so that it does not end up
tighter than its siblings.

**Two effects the handicap compensates for:**

1. **Display/compositor overhead** — the display-carrying GPU already has
   a few hundred MB occupied at idle (X server, compositor, browser GPU
   acceleration).
2. **Main-device buffers** — with `-sm layer`, llama.cpp places its
   main-device buffers (logits/output tensor, compute workspace, MTP
   draft) on the first CUDA device. This makes GPU0 more loaded than its
   siblings with the same layer count, even without a display.

**Sizing (two stages):**

1. **Idle measurement** (budget value before fit-params,
   [`measure_first_gpu_handicap()`](../../../aifred/lib/calibration/gpu.py)):
   - Measured empirically as `max_sibling_free − first.free_mb` within the
     fastest class.
   - **Floor:** `_MIN_FIRST_GPU_HANDICAP_MB` = **256 MB** (always at least).
   - **Ceiling:** If the measured difference > `_HARDWARE_HANDICAP_THRESHOLD_MB`
     (500 MB), the handicap falls back to the floor. Otherwise an already
     loaded foreign model on GPU0 would be subtracted twice.
   - Only one GPU in the fastest class → floor (no sibling comparison
     possible).
2. **Model-derived** (as soon as fit-params has run,
   [`first_gpu_handicap_mb()`](../../../aifred/lib/calibration/optimizer.py)):
   the idle delta does not see the transient load peak of the main-device
   buffers, but fit-params does, as an asymmetry against the active
   siblings: `(base_overhead[first] − mean(base_overhead[siblings])) +
   max(0, slope asymmetry) × layers[first] × ctx`. It scales with the
   target context; the 256 MB floor stays the lower bound.
   `fill_fastest_first()` uses this value.

Visible in the log as the line
`Free VRAM: …, first-GPU handicap (idle floor): <N> MB — model-derived per cell once fit-params ran`.

**Practical effect:** In the split `[27, 29, 19, 14, 5]` for a 5-GPU 235B
model, GPU1 has two layers more than GPU0 — precisely because GPU0 got the
handicap and both end up **equally tight** against the safety margin.

## Algorithm

[`calibrate_llamacpp_model()`](../../../aifred/lib/calibration/flow.py) runs
the phases under the same names as its code sections and log lines:

| Phase | Content | Log marker |
|-------|---------|------------|
| A | GGUF metadata, stable VRAM, budget per GPU (margins, side-channel reserves) | `Reading GGUF metadata: …` |
| 1 | Base configuration: fewest GPUs, highest KV quality at native ctx | `Phase 1: searching …` |
| — | Hybrid (CPU offload), only if Phase 1 verifies nothing and hybrid is allowed in the settings | `No GPU-only configuration verified — trying hybrid` |
| E | Speed variant: fewer GPUs than the base | `Phase E: speed variant …` |
| D | Write the llama-swap entries and the VRAM cache | — |

The TTS/VLM variants are not a phase of this run: the calibration mixin
calibrates them afterwards from the finished base (see
[TTS variants](#tts-variants-after-the-base-run)).

### Layer shift: glass cascade (SSOT)

When a probe OOMs, layers are redistributed — exactly **one whole layer** away from the
overloaded GPU. Both the blind shift in phase 1
([`_shift_one_layer_blind()`](../../../aifred/lib/calibration/split_refine.py)) and
the measurement-based refinement
([`_refine_split_from_measurement()`](../../../aifred/lib/calibration/split_refine.py))
choose the destination via **the same** SSOT function
[`_cascade_destination()`](../../../aifred/lib/calibration/split_refine.py). Two
different distribution philosophies for the same problem are forbidden
(see [First-GPU handicap](#first-gpu-handicap) and the
project rule "stick strictly to architecture decisions once they
are made").

**Cascade instead of "emptiest GPU":** The layer does not go to the globally emptiest
card, but **overflows** to the next card in fastest-first order that
can still hold it. A card `i` holds it if
`free_estimate[i] − step × layer_cost_per_gpu[i] ≥ min_free_mb`
(cost per layer = weights + KV at this ctx). `_cascade_destination()` checks
in this order:

1. **Idle card before `src`** (only without `keep_active_set`): an
   entirely empty card that comes earlier in the order is by sorting
   faster or equally fast and free — it wins over spilling down onto
   slower cards.
2. **Downstream:** iterates `src+1 … len(gpus)-1` and takes the **first**
   card that holds the layer.
3. **Upstream fallback:** if no downstream card holds it (typically: `src`
   is the last card of the cascade), the card before `src` with the
   largest remaining headroom after `+step` wins. Only with a free
   estimate — without one there is no upstream guessing.

If none holds the layer, there is no destination (→ ctx search as the
emergency exit). This keeps the fastest class packed full and the spillover
flows down in order — the glass cascade from the
[user preferences](#user-preferences-binding).

**Reserve cards are never a destination (`blocked_dest`):** GPUs carrying a
side-channel reserve (TTS/VLM) are excluded as shift destinations
([`_verify_and_refine()`](../../../aifred/lib/calibration/ctx_search.py)
builds the set from `budget.gpu_reserve_mb`). A layer there would eat
exactly the space the container claims later. The source may be any card.

**Context-maximizing swap first:** If the failing probe left a steady-state
measurement, the shift loop first tries
[`_context_refine_swap()`](../../../aifred/lib/calibration/split_refine.py):
a single whole-layer swap that relieves the ctx-limiting card onto the
card with the highest ctx ceiling (upstream allowed). Only if it finds
nothing does the blind shift via `_cascade_destination()` run.

**Whole layers, never fractions (`_STEP = 1.0` in `_shift_one_layer_blind()`):**
llama.cpp with `-sm layer`
places **whole** layers only; a fractional `--tensor-split`
is rounded internally to whole layers. A 0.5 shift therefore often moves
**nothing at all** physically, but costs a full model reload (for a 122B
model ~125 GB of reading). That's why a shift always moves exactly one whole
layer, and the final fractional split is rounded to whole layers via
[`_quantize_split_to_layers()`](../../../aifred/lib/calibration/fit_math.py)
(largest-remainder rounding) that sum to exactly
`total_layers`.

**Reserve-adjusted free:** What is checked is not the raw nvidia-smi free value,
but the lowest free value observed during load (`load_min_free_mb` of the
`VerifyResult`) minus the side-channel reserves (`budget.gpu_reserve_mb`).
A V100 that reports 14 GB "free" but reserves 14 GB
for a TTS container counts as **full** → the shift
skips it. Without this adjustment, a layer would be dumped onto a card that is only nominally
free, and the next probe would OOM again.

**Idle skip with locked active set:** If the caller passes `keep_active_set`
(speed variant, constant GPU count), the cascade skips idle GPUs
(`split[i] == 0`) — otherwise a shift would silently inflate the speed variant
into the base config.

### Phase 1: min GPUs for native ctx

In [`calibrate_llamacpp_model()`](../../../aifred/lib/calibration/flow.py);
verify, shifts and ctx search in
[`_verify_and_refine()`](../../../aifred/lib/calibration/ctx_search.py).
The GPU sets come from
[`_enumerate_gpu_configs()`](../../../aifred/lib/calibration/fit_math.py):
first single GPUs (compute-DESC), then per count `n` ascending the
homogeneous set of one speed class, then the compute-first fill
(the first `n` cards, mixing classes).

```
for kv in KV levels (f16 first, then q8_0, …):  // KV quality before GPU count
  for active in _enumerate_gpu_configs(...):      // fewest GPUs first
    candidate = _project_cell(active, kv)         // math, 1-2 s, no model load
    if candidate fail or ctx < native:
        next config                               // math says no → no probe

    real_probe(candidate.split, native_ctx)       // 30-90 s
    if fits: BASE = candidate; BREAK

    // probe failed despite math OK
    // SHIFT LOOP runs at NATIVE ctx (not shrunk!) — goal: keep max ctx
    up to 15 layer shifts:
        with measurement: _context_refine_swap first
        otherwise shift 1 WHOLE layer away from the OOM card via
          _cascade_destination (fastest-first, reserve-adjusted,
          reserve cards blocked as destination)
        real_probe(shifted_split, native_ctx)
        if fits: BREAK out of the shift loop
        split already seen (oscillation) → end the shift loop

    // ctx search ONLY as last resort, if the shifts are exhausted at native
    if still OOM:
        _binary_search_fitting_ctx(lo=MIN_USEFUL_CONTEXT_TOKENS, hi=native_ctx)
        // math-guided, bias-corrected, 256-token precision

    result ≥ native → BASE; BREAK
    result < native → keep as fallback, try the next config

// no config reaches native:
//   best-effort probe of the best unverified candidate (≥ MIN_USEFUL),
//   then final choice over all real measurements: highest KV quality,
//   within it the largest ctx
// still nothing → hybrid (only if allowed in the settings), otherwise error
```

**The order is binding:** first layer shift at native ctx (15
attempts), only then ctx search downward as an emergency. NEVER the other way round — layer
distribution can usually fix OOM without sacrificing ctx.

**Upward push (step 3):** After successful verify+refinement, if
`current_ctx < native_context` (for variants: `< ctx_ceiling`, e.g.
the base ctx) and a steady-state measurement is available → binary search
ctx upward. Probe-first since 2026-07-07: there is **no** headroom gate
anymore (previously only with `> 2 × safety_margin` free on the tightest
GPU, which pinned the 397B to 89k although ~171k ran). The math
projection is conservative; real-world probe headroom can
carry more ctx. On an OOM during the push, `_context_refine_swap()`
relieves the ctx-limiting card before the search window shrinks.
Especially relevant for the speed variant (its start ctx comes from the
math estimate of the smaller GPU set, often smaller than actually feasible).

### Phase E: speed variant (n_speed < n_base)

Speed variant = fewer GPUs than base, ctx may be reduced.
**The active set is locked**: the algorithm must NOT activate idle GPUs
during the shift loop, otherwise you end up with the base config.

Only when there is more than one GPU and BASE uses more than one.
Candidate choice in
[`_find_speed_candidate()`](../../../aifred/lib/calibration/flow.py)
(math only, no probe):

```
if n_base <= find_min_gpus_for_weights(...): no speed variant

for each speed class (fastest first, cumulative):
    for n in (smallest possible n) .. (n_base - 1):
        candidate = projection (reused from Phase 1 if the same GPU set,
                    otherwise _project_cell; same KV as BASE)
        if candidate.max_ctx >= MIN_USEFUL_CONTEXT_TOKENS:
            take candidate; stop
    // nothing found → extend by the next slower class

_verify_and_refine(candidate, lock_active_gpus=True):
    real_probe(candidate.split, candidate.max_ctx)
    on OOM, in this order:
      1. shifts (max 15) with keep_active_set=True — NO activation of idle GPUs
      2. if the shifts are exhausted: _binary_search_fitting_ctx
         lo = MIN_USEFUL_CONTEXT_TOKENS (32768 from config.py)
         hi = candidate ctx
         math-guided + bias tracking, final midpoint bisection
         down to 256-token precision
         → the HIGHEST fitting ctx
    then upward push (step 3) up to native

SPEED = (best_split, best_ctx, n_speed)
```

**Drop conditions for the speed variant:**
- Speed ctx ≥ native_ctx AND same KV quant → promoted to base (the speed
  variant is strictly better, only one config is written)
- Speed split == base split → no speed variant written (no gain)

The user chooses between BASE (max ctx) and SPEED (fewer GPUs, reduced
ctx). Both are written to llama-swap as separate configs
(`<model>` and `<model>-speed`).

### TTS variants (after the base run)

Orchestrated in the calibration mixin
([`_calibration_mixin.py`](../../../aifred/state/_calibration_mixin.py)).
Per installed GPU TTS engine (`installed_gpu_engines()` from
[`tts_engines/registry.py`](../../../aifred/lib/tts_engines/registry.py) —
XTTS, MOSS, Fish-Speech, Qwen3-TTS, as far as their Docker image exists
on this host) whose `|<engine>` cell is ticked in the calibration picker
(`calibration_matrix`):

1. **Isolated mode:** if the GPU set of the base (or speed) profile is
   disjoint from the TTS card (`side_channel_disjoint()` in
   [`llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py)),
   the profile is copied unchanged as `<model>-tts-<engine>` (or
   `-speed`) — no projection, no probe. If every candidate is covered
   this way, the engine is done.
2. Otherwise: start the TTS container → occupies VRAM on the TTS card. The
   **TTS reserve** comes from `resolve_tts_reserve()`
   ([`tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py)):
   measured peak from `data/tts_vram_cache.json` +
   `LLAMACPP_TTS_BURNIN_HEADROOM_MB` (512 MB); on a cache miss the stress
   burn-in runs first.
3. **Fast path:** [`calibrate_tts_variant_from_base()`](../../../aifred/lib/calibration/flow.py)
   re-projects the **same active GPU set** as BASE under the TTS-aware free
   VRAM (`enumerate_gpus()` live + reserve) and runs `_verify_and_refine()`;
   the ctx is capped at the base ctx (TTS only takes VRAM away, never adds).
   If there is a speed variant, the same fast path runs a second time with
   speed split + speed ctx as the "base".
4. **Full calibration only as a fallback:** if the fast path finds no fit,
   the complete calibration generator (Phase A, 1, E) runs with the
   TTS card and reserve as input.
5. Write the results as `<model>-tts-<engine>` (base) AND, if applicable,
   `<model>-tts-<engine>-speed` (speed variant) to the llama-swap config.
   The speed variant is dropped if:
   - `n_base <= find_min_gpus_for_weights` (no room for speed) — typical for
     large models with MOSS, because 2 GPUs are not enough for model + MOSS
     container
   - speed split == base split (no speed gain possible)

VLM and combo variants (TTS × VLM) go through the same
`calibrate_tts_variant_from_base()`, but derive their split proportionally
from the base split via
[`_derive_reserved_split()`](../../../aifred/lib/calibration/fit_math.py)
(relieves every reserve-loaded card) and verify with `lock_split=True` —
only the context is free there.

**Generic:** The TTS card is always the shared side-channel card
(`pick_tts_gpu()` → `get_tts_gpu_uuid()`), whichever card that is on the
current hardware — the algorithm adapts automatically, because free VRAM
is measured live. The verify/refine loop is identical to the base path,
only with different free VRAM values and reserves as input.

**Ctx reduction only when unavoidable:** With layer shifts at native ctx,
the algorithm aggressively tries to keep the native context. Only when
that is not enough either ("the GPUs are all really full") is ctx
reduced — for every variant via the same binary search
(`_binary_search_fitting_ctx()`) down to MIN_USEFUL.

## Estimate vs. probe

- **Estimate** (`_project_cell` / `llama-fit-params`, 1-2 s): math projection
  without model load. Returns predicted free VRAM per GPU. Optimistic
  for MoE models — runtime activation memory is underestimated.
- **Probe** (`verify`, 30-90 s): real server start + short inference +
  VRAM measurement. The truth. Required for verification because the estimate can
  wrongly say GO at native_ctx where the probe OOMs.

**Strategy:** Estimate as a cheap pre-filter — if the math says
"doesn't fit at native_ctx" → no probe. If the math says "fits" → probe.
On probe OOM: layer shift, then the estimate filter again (very cheap),
followed by a probe if the math is green.

**For binary searches** (both downward after exhausted shifts —
[`_binary_search_fitting_ctx()`](../../../aifred/lib/calibration/ctx_search.py),
SSOT for base, speed, VLM and best-effort — and upward in step 3) the
[`_math_max_fitting_ctx`](../../../aifred/lib/calibration/fit_math.py) helper is used:
the math searches the whole range in <100 ms (binary search via `_math_predicts_fit`)
and returns the highest ctx the fit-params modelling considers
fitting. Exactly that value is real-probed:
- Probe ✓ → this is the new known fit (lo).
- Probe ✗ → the math was too optimistic, set hi to the failed ctx, the math
  searches again in the narrower range.

**Fine-tuning below math resolution:** When the math no longer
finds anything higher than the current known fit (lo), the
algorithm falls back to plain midpoint bisection without math. This is
the final sprint to 256-token precision (`LLAMACPP_CALIBRATION_PRECISION`
in config.py) — at this scale the math becomes uninformative, real probes
are the only reliable source.

**Bias tracking (math-vs-real correction):** For MoE models, fit-params is
often consistently too optimistic by e.g. ~110 MB — if the
algorithm is not careful, it creeps along this gap in 256-token steps
(each iteration → ~4 MB more free, i.e. 25+ probes for a
100 MB correction, slower than a plain binary search). Solution: after
each failed probe, `bias = predicted_min_free − measured_min_free` is
determined and passed on as `extra_safety_margin` to the next math search.
The math thus directly picks a realistic ctx further
down (only 3–5 probes instead of 25+). The bias is tracked bidirectionally
(positive = math too optimistic, negative = too pessimistic; `_learn_bias()`
in `ctx_search.py`) and seeded from the OOM that triggered the search.

## Operation: gate, cancel, timeout

Calibration runs as a Reflex background event
([`calibrate_context`](../../../aifred/state/_calibration_mixin.py), `@rx.event(background=True)`),
so the UI stays usable during the minutes-long probes (cancel
button, debug console). Progress messages go through a module-wide
buffer that is flushed under the state lock — so debugging does not trigger
a session sync storm.

- **Process-wide inference gate**
  ([`calibration_gate.py`](../../../aifred/lib/calibration_gate.py)): While
  a calibration is running, regular chat inference is blocked —
  a second llama-server on the same GPUs would distort the VRAM
  measurement. `set_calibration_active()` / `is_calibration_active()`.
- **Immediate cancel:** The cancel button sets `request_cancel()`; the
  verify loop
  ([`verifier.py`](../../../aifred/lib/calibration/verifier.py)) checks
  `is_cancel_requested()` while waiting for the server and before every spawn, and shuts down
  test servers cleanly.
- **Size-scaled health timeout:** The load timeout grows with the
  model size (`LLAMACPP_HEALTH_TIMEOUT_PER_GB` in config.py, summed over
  multi-part GGUFs). A 122B model needs ~750 s to load — a fixed
  360 s timeout used to wrongly report "failure" although the server
  was still loading.

## AI calibration (alternative)

With `calibration_mode = "ai"` (default: `"legacy"` = the algorithm): a
cloud LLM drives the loop via function calls (`estimate_config`,
`probe_config`, `finalize`) —
[`calibrate_with_ai()`](../../../aifred/lib/calibration/ai_agent.py).
Provider, model and reasoning toggle come from the `calibration` system
agent in `data/agents.json` (`cloud_provider`, default `qwen` = DashScope;
editable in the Agent Editor). In the UI the AI option is only enabled
with a DashScope key. It follows the same strategy via the system prompt
([prompts/en/calibration/system.txt](../../../prompts/en/calibration/system.txt);
`ai_agent.py` always loads it with `lang="en"`).

Scope: BASE (`_try_ai_calibration()` in `flow.py`), the speed variant (the
GPU set is chosen deterministically by `_find_speed_candidate()`, the AI
optimizes ctx/split on exactly that locked set) and the TTS/VLM/combo cells
(`calibrate_tts_variant_from_base()` dispatches to `_ai_variant_from_base()`
in AI mode). AI mode is **terminal**: on failure it reports an error and
never falls back to the algorithm. Advantage: it can handle unusual hardware
mixes (e.g. heterogeneous cards) better than the deterministic algorithm.
Evaluation of this path:
[calibration-llm-challenge.md](calibration-llm-challenge.md).

## What is NEVER done

- "Balancing" layers so that all GPUs have the same free VRAM — this
  needlessly activates additional GPUs.
- Pushing ctx above `native_context` — physically impossible, llama.cpp clamps.
- Activating more GPUs than necessary "because it would give a bit more ctx".
- Reducing ctx while the layer shift loop is not yet exhausted.
- Shifting fractions of a layer — llama.cpp rounds to whole
  layers anyway; a sub-layer shift often moves nothing physically and just burns
  a full model reload.
- Dumping a layer onto a GPU whose free VRAM is only nominally free
  (side-channel reserve) — always check reserve-adjusted.

## Important invariants

- `len(active_gpus_in_split) ≤ len(active_gpus_in_speed_split)` is NOT to be
  guaranteed — speed may have fewer GPUs, that's its purpose.
- `base_split[i] == 0` for a GPU means this GPU is unused. The written
  profile pins exactly the active cards by UUID via `CUDA_VISIBLE_DEVICES`
  (in calibration order) and rewrites the tensor split to one value per
  active card (`update_llamaswap_cuda_visible()` in
  [`llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py)) —
  env and split must never diverge.
- Compute capability sorting is the only authoritative source for
  speed classes — no hardcoded lists ("RTX 8000 is fast").

## Code references

- Algorithm: [`aifred/lib/calibration/flow.py`](../../../aifred/lib/calibration/flow.py)
  - `calibrate_llamacpp_model()` — entry point (Phase A, 1, E, D)
  - `_find_speed_candidate()` — speed candidate (fewer GPUs, class cascade)
  - `calibrate_tts_variant_from_base()` — TTS/VLM/combo variant from base
- Verify + ctx search: [`aifred/lib/calibration/ctx_search.py`](../../../aifred/lib/calibration/ctx_search.py)
  - `_verify_and_refine()` — verify + shift + ctx search + upward push
  - `_binary_search_fitting_ctx()` — math-guided ctx search on a fixed split
- Split refinement: [`aifred/lib/calibration/split_refine.py`](../../../aifred/lib/calibration/split_refine.py)
  - `_shift_one_layer_blind()` — blind shift (load OOM)
  - `_refine_split_from_measurement()` — measurement-based refinement
  - `_context_refine_swap()` — ctx-maximizing swap (first OOM, upward push)
  - `_cascade_destination()` — SSOT destination choice (cascade, reserve-adjusted, idle skip, `blocked_dest`)
- Math: [`aifred/lib/calibration/fit_math.py`](../../../aifred/lib/calibration/fit_math.py)
  - `_enumerate_gpu_configs()` — GPU sets in priority order
  - `_quantize_split_to_layers()` — round fractional split to whole layers
  - `_math_max_fitting_ctx()` / `_math_predicts_fit()` — math pre-filter
  - `_derive_reserved_split()` — proportional split for reserve-loaded cards
- Optimizer: [`aifred/lib/calibration/optimizer.py`](../../../aifred/lib/calibration/optimizer.py)
  - `fill_fastest_first()` — greedy fill by speed class
  - `first_gpu_handicap_mb()` — model-derived first-GPU handicap
- Hardware: [`aifred/lib/calibration/gpu.py`](../../../aifred/lib/calibration/gpu.py)
  - `enumerate_gpus()` — compute capability sorting, speed classes, `first_in_class`
  - `measure_first_gpu_handicap()` — idle handicap (floor/ceiling)
- TTS pinning: [`aifred/lib/process_utils.py`](../../../aifred/lib/process_utils.py)
  - `get_tts_gpu_uuid()` — TTS GPU pinning (UUID, via `pick_tts_gpu`)
- Side-channel placement: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
  - `_side_channel_tier()` — tier formation + compute floor
  - `pick_side_channel_gpu()` — shared card (second tier card, weakest attachment first)
  - `pick_tts_gpu()` / `pick_vlm_gpu()` — both return the shared card
  - `resolve_gpu_id()` — `"auto"` → `pick_vlm_gpu()` (InsightFace)
- AI variant: [`aifred/lib/calibration/ai_agent.py`](../../../aifred/lib/calibration/ai_agent.py)
  - `calibrate_with_ai()` — tool loop (`estimate_config`, `probe_config`, `finalize`)
- Prompt: [`prompts/en/calibration/system.txt`](../../../prompts/en/calibration/system.txt)
