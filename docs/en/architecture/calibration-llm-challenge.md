# Calibration Challenge: LLM vs. Algorithm

> **Deutsche Version:** [calibration-llm-challenge.md](../../de/architecture/calibration-llm-challenge.md)

> Handover document for the experimental evaluation of whether an LLM can
> replace or complement the current algorithmic calibration.
> Starting point of the discussion: the current calibration with 6-13 variants
> takes ~45 min on the MiniPC setup; the largest share goes to
> binary searches after failed probes.

## Background

The existing algorithmic calibration path is documented in
[calibration-strategy.md](calibration-strategy.md).
Summary:

- **BASE**: greedy fill onto the fastest compute class, spillover onto
  the next slower one if needed. Min. GPUs, max. ctx.
- **SPEED**: fewer GPUs than BASE via ctx reduction.
- **TTS variants** (XTTS, Fish-Speech, MOSS): BASE/SPEED again, with a
  TTS reserve on the second-highest compute class (side-channel GPU
  via `pick_vlm_gpu`/`pick_tts_gpu`).
- **VLM variants** (qwen3-vl:4b, qwen3-vl:8b): analogous with a VLM reserve.
- **Combo variants** (TTS×VLM): both reserves on the same
  side-channel GPU. A combo is discarded if TTS+VLM overflow
  the card (capacity check).

Current LLM path: `calibration_mode = "ai"` calls
[ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) — but **only
for BASE**. Speed/TTS/VLM/combos still run algorithmically through
`calibrate_tts_variant_from_base` (re-projection via fit-params).

## Core hypothesis

**The only structural advantage of an LLM over the current
algorithm lies in speeding up the binary search.**

- `llama-fit-params` does not compute accurately enough. The math projection says
  "fits with X MB free", the real probe shows OOM or too much headroom.
- The algorithm repairs this via binary search (math-assisted with
  bias tracking, falls back to plain midpoint bisection when the math
  becomes uninformative), precision 256 tokens.
- An LLM could interpolate directly from the first probe result (predicted vs. measured
  per GPU) instead of halving. If that works:
  3-5 probes saved per variant.

Beyond this point an LLM brings **nothing** — the greedy cascade
(BASE, SPEED) is deterministically optimal, re-projection for TTS/VLM/
combo is math without a search space.

## What must be clarified beforehand

Before the comparison starts:

1. **Clear the cache** for the test model, otherwise the algorithm wins
   by cache hit. File: `data/model_vram_cache.json`. Remove the entry for the
   test model (or pick a model that has never been calibrated).
2. **Burn-in caches may be used** — `data/vlm_vram_cache.json`
   and `data/tts_vram_cache.json` are lookup tables with empirically
   measured reserves for VLMs and TTS engines. Both paths (algorithm and
   LLM) use them as input, fair for the comparison.
3. **Test at least 2-3 models of different sizes**, so that the
   comparison doesn't hinge on a special case. Suggestion:
   - Small (clearly fits on one class): e.g. Qwen3-4B or 8B
   - Medium/tight: e.g. Qwen3.6-27B or 30B
   - Large/requires spillover: e.g. Qwen3-122B or 70B+

## Analysis before the comparison (phase 0)

Before running the actual race, an **evaluation of the
most recently completed calibration** (122B with all variants):

### Data sources

- `journalctl --user -u aifred-intelligence` since the start of the calibration
  (complete log with all probe sequences).
- `data/logs/aifred_debug.log` (live debug output of the calibration mixin).

### Evaluate

Per variant (BASE, SPEED, each TTS, each VLM, each combo):

1. **How many probes were run** (phase 1 initial + layer shifts +
   binary search) — count from the log lines.
2. **Bias per probe**: `predicted_min_free − measured_min_free` (in MB)
   for each probe attempt. Lists how far off `llama-fit-params`
   was.
3. **Identify bias patterns**:
   - Systematically constant (e.g. always ~100-150 MB optimistic) →
     **bias tracking in the algorithm covers this, the LLM brings no
     advantage**.
   - Chaotic/model-dependent (sometimes +200, sometimes −50, sometimes +500) → **the LLM
     could do better at pattern matching**.
4. **Binary search cost**: per variant, count how many probes were still needed after
   the first OOM until ctx convergence. 1-2 cannot be
   beaten. 5+ is the lever the LLM would have to attack.

### Decision point

- If the analysis shows that the algorithm already converges with
  ≤3 probes per variant: **throw out the LLM path**, discussion over.
- If individual variants need 5+ probes with a recognizable
  bias pattern: **run the LLM race** (phase 1 below).

## Comparison race (phase 1)

### Setup

- Model chosen from the pre-selected candidates, cache cleared.
- Wall-clock stopwatch from "start" until "all profiles written to
  `~/.config/llama-swap/config.yaml`".
- Same variant list for both paths (BASE + SPEED + 3 TTS + 2 VLM +
  up to 6 combos).

### What the LLM (Claude) may do

- Read access to `nvidia-smi`, `llama-fit-params`, GGUF headers,
  llama-swap config, burn-in caches.
- Real `llama-server` start for verification (probe). Without a probe
  the result is only an estimate, not a comparison.
- **Not allowed**: writing to `~/.config/llama-swap/config.yaml` —
  the existing code does that, otherwise the comparison gets mixed up
  with write latency.

### What the algorithm does

Standard run via UI or CLI, identical model, same point in time
(no parallel GPU load) — `systemctl restart aifred-intelligence`
between runs for a clean GPU state.

### Success criteria

- **ctx values nearly identical** (within ~5% tolerance). If the
  LLM delivers significantly worse ctx values, it is not
  usable, no matter how fast it is.
- **Wall-clock time** clearly shorter (≥20% savings) for the path
  to be worth it. Marginal savings do not justify the API costs + the
  maintenance effort for two paths.
- **Check determinism**: run the LLM race 2x, see whether the results
  are consistent. If the LLM suggests 80k ctx one time and 60k ctx the next, it
  is not usable.

## Consequences per outcome

| Result | Consequence |
|---|---|
| LLM clearly wins (≥20% faster, same ctx values, deterministic) | Keep the AI path as an optional variant, split prompts per step (separate prompts for BASE + SPEED, re-projection stays algorithmic), possibly default later |
| LLM competitive but not clearly better | Keep the AI path as a last resort for edge cases (unusual hardware, fit-params fails completely). No investment in a refactor |
| LLM worse or non-deterministic | Throw out the AI path, delete [ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) + [prompts/de/calibration/system.txt](../../../prompts/de/calibration/system.txt) + the `calibration_mode` switch |

## Binding rules for the LLM side

For the comparison to be fair, the LLM must **follow exactly the same algorithm**
as described in [calibration-strategy.md](calibration-strategy.md).
No "getting smarter" where the algorithm is conservative:

- **Sorting**: compute_cap DESC → total_mb DESC → cuda_id ASC.
- **Order on OOM**: first max. 15 layer shifts at native ctx,
  **only then** ctx shrink. No shortcuts.
- Take the **first-GPU handicap** (256 MB floor, max 500 MB) into account —
  GPU0 has less usable VRAM due to display + KV cache output tensor
  pinning.
- **Native ctx is a hard cap** — never probe higher.
- **Hybrid mode** (CPU offload) only if explicitly allowed.

The **permitted advantage**: after each probe (predicted vs. measured per
GPU), interpolate directly instead of halving. That is the whole idea.

## Architectural follow-up question (only if the LLM wins)

If the AI path is expanded, new prompt layout:

- One prompt file per search step: `prompts/{lang}/calibration/base.txt`
  and `prompts/{lang}/calibration/speed.txt` (tribunal is dropped — it is
  agent choreography, not a calibration step).
- Inter-step context: after the BASE result, `(ctx, split, measured_free_mb)`
  is passed on into the SPEED prompt.
- Re-projection (TTS/VLM/combo) stays purely algorithmic — no
  LLM reasoning there.
- `agents.json` schema: possibly a separate model + reasoning toggle per step.

## References

- [calibration-strategy.md](calibration-strategy.md) — algorithmic
  strategy (SSOT)
- [aifred/lib/calibration/flow.py](../../../aifred/lib/calibration/flow.py) —
  `calibrate_llamacpp_model`
- [aifred/lib/calibration/ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) —
  current LLM path (BASE only)
- [aifred/lib/calibration/optimizer.py](../../../aifred/lib/calibration/optimizer.py) —
  `fill_fastest_first`
- [prompts/de/calibration/system.txt](../../../prompts/de/calibration/system.txt) —
  current LLM system prompt
- [data/vlm_vram_cache.json](../../../data/vlm_vram_cache.json),
  [data/tts_vram_cache.json](../../../data/tts_vram_cache.json) — burn-in lookups
- [data/model_vram_cache.json](../../../data/model_vram_cache.json) — calibration cache
  (clear for the test model before the comparison)
