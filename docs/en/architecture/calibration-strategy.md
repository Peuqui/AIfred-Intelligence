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
- TTS containers (XTTS, MOSS, …) and the Vigilantia VLM (Ollama) each
  occupy part of a GPU, NOT a whole GPU. They run on the
  **side-channel tier** — the compute class below the fastest one,
  which stays reserved for the main LLM. For details see the section
  [Side-channel placement](#side-channel-placement-vlm--tts).

## Sorting (generic)

GPUs are sorted by:

1. **Compute capability** (desc) — RTX 8000 (7.5) before V100 (7.0) before P40 (6.1)
2. **Total VRAM** (desc) as tiebreaker at equal CC — e.g. two RTX 8000 ×
   48 GB are equal under this criterion
3. **CUDA ID** (asc) as final tiebreaker

Implemented in [`_gpu_ranking()`](../../../aifred/lib/process_utils.py).

## Side-channel placement (VLM + TTS)

> SSOT: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py).
> Hardware-agnostic — **nothing** is hardcoded to "always V100".

The fastest compute class stays completely free for the chat LLMs
(main + automatic via llama-swap). The side channels — the
Vigilantia VLM (Ollama) and the TTS containers — run on the
**side-channel tier**: the compute class directly below it.

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

### Split between TTS and VLM

- **`pick_tts_gpu()`** → first card of the tier.
- **`pick_vlm_gpu()`** → second card of the tier; if there is only one,
  the VLM shares it with the TTS (as before).
- **`pick_face_gpu()`** → follows the VLM (InsightFace is tiny at ~280 MB
  and belongs to vision thematically).

As soon as the tier has ≥ 2 cards, there is **no VRAM competition** anymore
between TTS container and VLM on one card. Previously, e.g.
Fish-TTS and Vigilantia-8B could not coexist (the combo capacity check
discarded the profile). With a single tier card this
check still applies: if TTS reserve + VLM reserve don't fit on it together,
exactly this combo is dropped (the rest runs).

### Examples

| Setup | LLM tier | TTS | VLM |
|---|---|---|---|
| 2× RTX 8000 + 1× V100 + 2× P40 | RTX 8000 ×2 | V100 | V100 (shared, P40 excluded by floor) |
| 2× RTX 8000 + 3× V100 (today) | RTX 8000 ×2 | V100 #1 | V100 #2 |
| 3× RTX 8000 | RTX 8000 #1 | RTX 8000 #2 | RTX 8000 #3 |
| P40s only | P40 #1 | P40 #2 | P40 #3 (soft fallback) |
| 1× RTX 8000 + 1× P40 | RTX 8000 | P40 | P40 (last resort) |

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
systemd drop-in (`CUDA_VISIBLE_DEVICES`, see `ollama_override_text()`)
must be pinned to that card — otherwise Ollama picks greedy first-fit.
With a single tier card this is identical to the previous pin,
so no action is required; it becomes relevant once TTS and VLM are split
across two different cards.

## User preferences (binding)

In this order:

1. **Lowest GPU count** — less inter-GPU sync = faster. If
   `[a,b,c,0]` reaches native context across 3 GPUs, NEVER prefer `[w,x,y,z]` across
   4 GPUs.
2. **Fill the fastest GPU class first** — layers land primarily on the RTX
   8000s, P40s only as spillover.
3. **Packed full up to the safety margin** ([`LLAMACPP_VRAM_SAFETY_MARGIN`](../../../aifred/lib/config.py),
   192 MB Linux, 1536 MB WSL) — NO spreading of headroom. A GPU with 2 GB
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
display-carrying GPU on desktop systems). For this GPU the optimizer
subtracts a **handicap** from the usable VRAM so that it does not end up
tighter than its siblings.

**Two effects the handicap compensates for:**

1. **Display/compositor overhead** — the display-carrying GPU already has
   a few hundred MB occupied at idle (X server, compositor, browser GPU
   acceleration).
2. **KV cache / output tensor asymmetry** — with `-sm layer`, llama.cpp pins
   the output tensor and parts of the KV cache setup to the
   first CUDA device. This makes GPU0 more loaded than its siblings
   with the same layer count, even without a display.

**Sizing:**

- Measured empirically as `max_sibling_free − first.free_mb` within the
  fastest class.
- **Floor:** `_MIN_FIRST_GPU_HANDICAP_MB` = **256 MB** (always at least).
- **Ceiling:** If the measured difference > `_HARDWARE_HANDICAP_THRESHOLD_MB`
  (500 MB), the handicap falls back to the floor. Otherwise an already
  loaded foreign model on GPU0 would be subtracted twice.
- Only one GPU in the fastest class → floor (no sibling comparison
  possible).

Implemented in [`measure_first_gpu_handicap()`](../../../aifred/lib/calibration/gpu.py).
Visible in the log as the line `📊 first-GPU handicap: <N> MB`.

**Practical effect:** In the split `[27, 29, 19, 14, 5]` for a 5-GPU 235B
model, GPU1 has two layers more than GPU0 — precisely because GPU0 got the
handicap and both end up **equally tight** against the safety margin.

## Algorithm

### Layer shift: glass cascade (SSOT)

When a probe OOMs, layers are redistributed — exactly **one whole layer** away from the
overloaded GPU. Both the blind shift in phase 1
([`_shift_one_layer_blind()`](../../../aifred/lib/calibration/flow.py)) and
the measurement-based refinement
([`_refine_split_from_measurement()`](../../../aifred/lib/calibration/flow.py))
choose the destination via **the same** SSOT function
[`_cascade_destination()`](../../../aifred/lib/calibration/flow.py). Two
different distribution philosophies for the same problem are forbidden
(see [First-GPU handicap](#first-gpu-handicap) and the
project rule "stick strictly to architecture decisions once they
are made").

**Cascade instead of "emptiest GPU":** The layer does not go to the globally emptiest
card, but **overflows** to the next card in fastest-first order that
can still hold it. `_cascade_destination()` iterates `src+1 … len(gpus)-1` and
takes the **first** GPU `i` with
`reserve_adjusted_free[i] − step × layer_cost[i] ≥ min_free`. If no
subsequent card has room, there is no destination (→ ctx shrink as the emergency exit).
This keeps the fastest class packed full and the spillover flows down in order
— the glass cascade from the [user preferences](#user-preferences-binding).

**Whole layers, never fractions (`_STEP = 1.0`):** llama.cpp with `-sm layer`
places **whole** layers only; a fractional `--tensor-split`
is rounded internally to whole layers. A 0.5 shift therefore often moves
**nothing at all** physically, but costs a full model reload (for a 122B
model ~125 GB of reading). That's why a shift always moves exactly one whole
layer, and the final fractional split is rounded to whole layers via
[`_quantize_split_to_layers()`](../../../aifred/lib/calibration/flow.py)
(largest-remainder rounding) that sum to exactly
`total_layers`.

**Reserve-adjusted free:** What is checked is not the raw nvidia-smi free value,
but `load_min_free` adjusted for side-channel reserves (resident TTS/VLM containers).
A V100 that reports 14 GB "free" but reserves 14 GB
for a running TTS container counts as **full** → the shift
skips it. Without this adjustment, a layer would be dumped onto a card that is only nominally
free, and the next probe would OOM again.

**Idle skip with locked active set:** If the caller passes `keep_active_set`
(speed variant, constant GPU count), the cascade skips idle GPUs
(`split[i] == 0`) — otherwise a shift would silently inflate the speed variant
into the base config.

### Phase 1: min GPUs for native ctx

```
for n in 1..len(gpus):                          // sorted fastest-first
    candidate = math_estimate(n, native_ctx)    // 1-2 s, no model load
    if candidate fail or ctx < native:
        next n                                  // math says no → no probe
    
    real_probe(candidate.split, native_ctx)     // 30-90 s
    if fits: BASE = candidate; BREAK
    
    // probe failed despite math OK
    // SHIFT LOOP runs at NATIVE ctx (not shrunk!) — goal: keep max ctx
    up to 15 layer shifts:
        shift 1 WHOLE layer via _cascade_destination (fastest-first,
          reserve-adjusted, do NOT activate idle — keeps n GPUs constant)
        real_probe(shifted_split, native_ctx)
        if fits: BASE = candidate with shifted_split; BREAK
    
    // ctx shrink ONLY as last resort, if all 15 shifts fail at native
    if all shifts fail:
        shrunk_ctx = _shrink_to_fit(...)
        real_probe(current_split, shrunk_ctx)
        if fits: BASE = candidate with shrunk_ctx
    
    // if nothing → next n
    if n == len(gpus): no fit (suggest hybrid)
```

**The order is binding:** first layer shift at native ctx (15
attempts), only then ctx shrink as an emergency. NEVER the other way round — layer
distribution can usually fix OOM without sacrificing ctx.

**Upward push (step 3):** After successful verify+refinement, if
`current_ctx < native_context` AND the tightest active GPU still has
`> 2 × safety_margin` free → binary search ctx upward. The math
projection is conservative; real-world probe headroom can
carry more ctx. Especially relevant for the speed variant (target_ctx comes from the
2-GPU math estimate, often smaller than actually feasible).

### Phase 2: speed variant (n_speed < n_base)

Speed variant = fewer GPUs than base, ctx may be reduced.
**The active set is locked**: the algorithm must NOT activate idle GPUs
during the shift loop, otherwise you end up with the base config.

```
for n_speed in (n_base - 1) downto 1:
    candidate = math_estimate for n_speed GPUs
    if ctx >= MIN_USEFUL_CONTEXT_TOKENS: real_probe(candidate.split, target_ctx)
    
    on OOM, in this order:
      1. shifts at target_ctx (max 15) via _cascade_destination
         with keep_active_set=True — NO activation of idle GPUs
      2. if all shifts fail: BINARY SEARCH ctx downward
         lo = MIN_USEFUL_CONTEXT_TOKENS (32768 from config.py)
         hi = target_ctx
         iterate until hi - lo < precision (256 tokens):
             mid = (lo+hi)/2
             real_probe(mid)
             fit → lo = mid (try higher)
             fail → hi = mid (go lower)
         results in the HIGHEST fitting ctx
    
    SPEED = (best_split, best_ctx, n_speed)
    BREAK out of n_speed loop
```

**Drop conditions for the speed variant:**
- Speed ctx ≥ native_ctx AND same KV quant → promoted to base (the speed
  variant is strictly better, only one config is written)
- Speed split == base split → no speed variant written (no gain)

The user chooses between BASE (max ctx) and SPEED (fewer GPUs, reduced
ctx). Both are written to llama-swap as separate configs
(`<model>` and `<model>-speed`).

### Phase 3: TTS variants

Per TTS backend (XTTS, MOSS):

1. Start the TTS container → occupies VRAM on the TTS GPU
2. Live hardware detection: `enumerate_gpus()` sees the current free VRAM values
3. **Run the complete calibration generator** (phase 1 + phase 2):
   - Phase 1: estimate + probe + 15 layer shifts at native ctx, then optionally
     ctx shrink
   - Phase 2: speed variant (n_speed < n_base) with binary search down to
     MIN_USEFUL_CONTEXT_TOKENS
4. Write the results as `<model>-tts-<backend>` (base) AND, if applicable,
   `<model>-tts-<backend>-speed` (speed variant) to the llama-swap config.
   The speed variant is skipped if:
   - `n_base == min_gpus_for_weights` (no room for speed) — typical for
     large models with MOSS, because 2 GPUs are not enough for model + MOSS
     container
   - speed split == base split (no speed gain possible)

**Generic:** Whether TTS is on GPU1 (today) or a V100 (future) — the
algorithm adapts automatically, because free VRAM is measured live.
The algorithm is identical to the base path, only with different
free VRAM values as input.

**Ctx reduction only when unavoidable:** With layer shifts at native ctx,
the algorithm aggressively tries to keep the native context. Only when
that is not enough either ("the GPUs are all really full") is ctx
reduced. For speed, via binary search down to MIN_USEFUL.

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

**For binary searches** (both downward in phase 2 / speed variant and
upward in step 3) the `_math_max_fitting_ctx` helper is used:
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
down (only 3–5 probes instead of 25+).

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

With `calibration_mode = "ai"`: a DashScope Qwen agent drives the loop
via function calls (`estimate_config`, `probe_config`, `finalize`).
It follows the same strategy via the system prompt
([prompts/de/calibration/system.txt](../../../prompts/de/calibration/system.txt)).
Advantage: it can handle unusual hardware mixes (e.g. heterogeneous cards) better
than the deterministic algorithm.

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
- `base_split[i] == 0` for a GPU means this GPU is unused — no
  CUDA_VISIBLE_DEVICES needed (llama.cpp ignores it automatically).
- Compute capability sorting is the only authoritative source for
  speed classes — no hardcoded lists ("RTX 8000 is fast").

## Code references

- Algorithm: [`aifred/lib/calibration/flow.py`](../../../aifred/lib/calibration/flow.py)
  - `calibrate_llamacpp_model()` — entry point
  - `_verify_and_refine()` — verify + shift + native push
  - `_shift_one_layer_blind()` — blind shift (phase 1)
  - `_refine_split_from_measurement()` — measurement-based refinement
  - `_cascade_destination()` — SSOT destination choice (cascade, reserve-adjusted, idle skip)
  - `_quantize_split_to_layers()` — round fractional split to whole layers
- Optimizer: [`aifred/lib/calibration/optimizer.py`](../../../aifred/lib/calibration/optimizer.py)
  - `fill_fastest_first()` — greedy fill by speed class
- Hardware: [`aifred/lib/process_utils.py`](../../../aifred/lib/process_utils.py)
  - `_gpu_ranking()` — compute capability sorting
  - `get_tts_gpu_uuid()` — TTS GPU pinning (UUID, via `pick_tts_gpu`)
- Side-channel placement: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
  - `_side_channel_tier()` — tier formation + compute floor
  - `pick_tts_gpu()` / `pick_vlm_gpu()` / `pick_face_gpu()` — card choice
- AI variant: [`aifred/lib/calibration/ai_agent.py`](../../../aifred/lib/calibration/ai_agent.py)
- Prompt: [`prompts/de/calibration/system.txt`](../../../prompts/de/calibration/system.txt)
