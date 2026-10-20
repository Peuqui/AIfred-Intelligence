# vLLM auto-calibration: the algorithm

> **Deutsche Version:** [calibration-vllm.md](../../de/architecture/calibration-vllm.md)

> SSOT companion to the code (`aifred/lib/calibration/vllm_flow.py`,
> `vllm_probe.py`, `vllm_model_meta.py`). The code is the truth — this
> document explains the decision rules and their measured rationale so
> that third parties (and future us) can follow them.
> Measurement evidence: [vLLM auto-calibration (benchmarks)](../benchmarks/vllm-autocalibration.md).
> Sister document for llama.cpp: `docs/de/architecture/calibration-strategy.md`.

## Problem statement

A checkpoint ships no operating parameters. What the vendor provides
(architecture, ring arithmetic, context window) is half the truth — the
other half (topology, speculation depth k, GPU memory utilization,
chunk size) only emerges in interaction with the concrete rig and is
**not derivable, only measurable**. Auto-calibration fills in this
package insert and persists it as an operating-point YAML
(`data/operating_points/<entry>-vllm.yaml`).

## Principles

1. **Measure, don't guess.** Every rule below exists because an
   assumption failed a measurement.
2. **Context first.** The native context window is never sacrificed to
   save a faster rung (exception: the explicitly informational speed
   variant).
3. **Long decode crowns the winner.** Short-context numbers stayed
   nearly constant across all kernel eras while long decode doubled —
   measuring only short means measuring the wrong thing.
4. **Hardware-agnostic.** No model- or card-specific special cases;
   everything derives from enumerated capabilities (compute capability,
   VRAM) and checkpoint metadata.

## Inputs

- **Checkpoint analysis** (`analyze_checkpoint`): reads only the
  safetensors headers (no weights). Yields parameter count, MTP block
  size, architecture, the QSA rings' `compress_ratio`.
- **Ring arithmetic** (`allowed_k_block_sizes`): the QSA ring capacity
  must divide the attention block size: `capacity = ratio *
  ceil((ratio + k) / ratio)`, block size = `lcm(16, capacity)`. This
  determines which speculation depths are economical (Flash-Next:
  k=5–8 force block 48 → uneconomical).
- **GPU enumeration** over every card: before calibration llama-swap
  and the side models are stopped, then the cards are drained via the
  process list (`drain_gpus`, SSOT primitives, no dual state with loaded
  models).

## Flow

### Phase A — topology ladder

Candidate topologies (TP×PP combinations over the free cards, fastest
compute class first — since 2026-08-30 that order is measured, not just
convention: the drafter on the slower class costs 2–4 %). Each rung
boots with k=0 and iterates the context window: vLLM's own limit
estimate is too optimistic with the MTP draft head loaded, so its error
message is adopted as the new `max-model-len` and the rung reboots (up
to 3 rounds).

### KV blocks (override on every boot)

Every calibration boot runs with `--num-gpu-blocks-override`. Only then
does the boot log name, per worker, how many blocks the budget under the
GMU would hold (`Overriding num_gpu_blocks=<A_i>`) and, after the boot,
the pool size (`GPU KV cache size`, `Maximum concurrency`). From these
(`KvReport` in `vllm_probe.py`): `N_auto = min(A_i)` and the block count
of one full context `N_min = N × mml / pool tokens`. The target is
`max(N_auto, N_min)` — as much cache as the budget holds, at least one
full context (decision 2026-10-20). `_boot` settles the number:

- Start check failed below the budget → back to the budget.
- The budget holds no full context → the override forces one (that is
  how DeepSeek-V4 runs). If that overruns the card, vLLM's estimate for
  the budget becomes the context limit.
- Passed with fewer blocks than the target → reboot: with a smaller
  cache the allocator would have more room than in production.

The start value is the block count of the existing llama-swap entry;
without one, a single block, whose certain rejection yields both numbers
(costs one load). Every further rung starts with `N_min` at the native
window — the number depends on the context, not on the topology.

### Phase B — coherence gate

Three deterministic checks (factual question, arithmetic, code). Speed
without correct output is worthless; an incoherent rung is out.

### Phase C — measurement points

- **Short probe** (toggle `VLLM_CALIBRATION_SHORT_PROBE`): ~200 tokens
  of decode on a short prompt. Informational only — unless a rung
  cannot carry the long point, then it is the fallback metric.
- **Long probe** (`probe_long_context`): prompt at 45 % of the window,
  capped at 30k. Prefill rate via a `max_tokens=1` call; decode rate
  via a second call that hits the prefix cache (measuring pure decode
  against the full KV). Acceptance rate from the vLLM counters
  (`spec_decode_num_draft/accepted_tokens_total`) as a delta around the
  measurement.

### Phase D — k-sweep

Candidates from top down (exhaustive via
`VLLM_CALIBRATION_K_EXHAUSTIVE`, spread otherwise); structurally
impossible k (capture sizes above the stack limit) are never booted.
Three failure classes, three answers:

- **Boot OOM** → context adoption (phase-A logic) per k.
- **Probe OOM** (boot fine, first real request tips over): a property
  of the *topology*, not the k → one retry with GMU−0.02, and the
  learned GMU **holds for the rest of the sweep** (carry). Fallback
  clause: if the carried GMU no longer supports the native context at a
  smaller k, that k gets the full rung GMU back (context first).
- **Any other probe crash** → k rejected.

The last measured GMU goes into the operating point (outage
2026-08-30: measured at 0.95, persisted 0.97 → production OOM on the
first request; mandatory ever since).

### Phase E — winner rule (`_beats`)

Primary metric: long decode. Tie-break: if two candidates are within
5 % AND their long prefills differ by more than 10 %, prefill decides
(the 10 % hurdle exists because prefill noise otherwise displaces a
better k — incident 2026-08-30).

### Phase F — challenger boots (chunk A/B, GMU A/B)

A single counter-boot of the overall winner with doubled chunk size
(`max_num_batched_tokens`), same winner rule
(`VLLM_CALIBRATION_CHUNK_AB`). Rationale: this axis is model-specific
and not derivable — 4096 gave the dense 27B +2.7 % prefill but cost
the QSA/GDN hybrid Flash-Next 12 %. The one measurement point replaces
the formula. Then GMU A/B (`VLLM_CALIBRATION_GMU_AB`): the same
winner once at GMU−0.02 to detect *soft* allocator pressure — which
throws no error but silently eats throughput (Flash-Next: GMU 0.95
halved long decode without any message). If the lower GMU no longer
carries the context, its boot fails and the incumbent stays (context
first). If any challenger fails, the incumbent always stays — a lost
boot is the price of the measurement, never a risk to the operating
point.

### Phase G — persistence

Before the winner is persisted it runs the longest request
(`probe_fill_context`, 95 % of the window, tokens counted at the server).
Only then does "free under load" hold: the allocator keeps its high-water
mark, and buffers such as the DSv4 indexer grow with the context. If a
card overruns, first the cache drops to one full context, then the
context. What each card has free afterwards goes into `meta.kv` with the
block counts and bytes per block, the boot parameter set into
`meta.spec` — the basis of the profiles in phase H.

Operating-point YAML with the full boot command, environment, hardware
fingerprint and meta (k-sweep matrix, long-context measurements,
acceptance). Optionally a `-speed` variant (fewer cards, reduced
context) — persisted only if it beats the operating point. After
calibration, llama-swap restarts and loads the freshly persisted point.

### Phase H — profiles for the side models

Route A (decision 2026-10-20): the base runs on every card, none stays
free. Every ticked cell of the calibration matrix except the base stands
for a profile `<entry>-tts-<engine>[-vlm-<key>]`, which
`calibrate_vllm_side_channel_variant` derives from the base's `meta.kv`
and `meta.spec`. Budgets are the bare burn-in peaks plus ONE residual
`SIDE_CHANNEL_RESIDUAL_MB` per card holding side models
(`side_channel_needs`, the same rule as for llama.cpp). The VLM card is
fixed (shared card); the planner picks the TTS card: a card without the
model, then the largest measured headroom, then the fewest KV blocks
given up. The topology stays; the first step that fits wins:

1. a card the topology does not use — no boot,
2. measured headroom on a model card — no boot,
3. shrink the KV cache, at most down to one full context,
4. lower the GMU, PLE models only (PLE fills its stage up to the GMU),
5. shrink the context.

Steps 3–5 are booted (block count fixed, `policy="keep"`), run the
longest request and are adjusted by the measured headroom. If nothing
fits, the profile is not written and is recorded as `capacity_exceeded`
(red dot). STT gets no profile of its own: it takes what stays free,
the CPU if need be.

## Known limits

- The acceptance rate is measured and logged but does not enter the
  rule — long decode contains it implicitly.
- Chunk A/B only tests doubling, not halving; GMU A/B only one step
  (−0.02), no cascade.
