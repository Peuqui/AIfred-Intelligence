# vLLM Auto-Calibration: Topology Search and k-Sweep on Heterogeneous GPUs

Status: 2026-08-30 · German version: [vllm-autokalibration.md](../../de/benchmarks/vllm-autokalibration.md)

AIfred's calibrate button measures vLLM checkpoints fully automatically:
it unloads running models, builds a topology ladder from the installed
GPUs (TP within a compute class, PP across class boundaries), boots and
measures every rung for real — at short **and** long context —, runs a
speculation-depth sweep (MTP, `k`) and persists the result as a
llama-swap entry plus an operating-point profile with a hardware
fingerprint.


> The algorithm itself (decision rules, phases, rationale) is
> documented separately: [calibration-vllm.md](../architecture/calibration-vllm.md).

## Test system

| Component | Value |
|---|---|
| GPUs (calibratable) | 2× Quadro RTX 8000 48 GB (SM75) + 2× Tesla V100-PCIE-32GB (SM70) |
| Side channel (reserved) | 1× V100 32 GB (TTS + vision, shared card) |
| Interconnect | PCIe Gen3 x4 per card (M.2 OCuLink / USB4); P2P disabled (`NCCL_P2P_DISABLE=1`) |
| Stack | [1Cat-vLLM 1.3.0](https://github.com/dnv2003/v100-skinny) with v100-skinny kernels (NVFP4 on Volta/Turing) |
| Attention | Volta: the stack's XQA backend · Turing: FlashAttention-2 from [Peuqui/flash-attention](https://github.com/Peuqui/flash-attention) (branch `sm75-enablement`, see below) |
| Model | RadixArk/Qwen3.8-27B-NVFP4 (20.4 GiB, native context 262,144) |

## Methodology

- **Two measurement points per configuration.** Short context: fixed
  technical prose prompt, 200 tokens, `ignore_eos`, wall clock including
  prefill. Long context: ~29,000 tokens of filler (45 % of the window,
  capped), first a prefill measurement (`max_tokens=1`), then pure decode
  through the prefix cache.
- **Winner rule: long-context decode decides.** Long sessions (research,
  coding, grown history) define the user experience; a short turn is over
  in seconds anyway. On a near tie (≤ 5 %) the higher **long-context
  prefill** breaks it — but only when the prefills differ substantially
  (> 10 %), i.e. when comparing topologies.
- **Context priority still comes first:** every rung competes with the
  largest context it can carry (starting native). Only rungs carrying the
  full native context compete for the operating point; reduced-context
  rungs are measured and reported as a speed variant.
- **Acceptance diagnostics:** for every k the long-context probe reads
  the speculation counters (drafted vs. accepted tokens) from the
  Prometheus metrics. This separates acceptance problems from cost
  problems — and is what convicted the kernel bug described below.
- **OOM retry ladder:** if vLLM states a context limit itself, it is
  adopted. A bare CUDA OOM first raises the per-card reserve (+1/+2 GB —
  the compile workspace needs room on a card deliberately filled via GMU;
  this costs KV pool blocks, not context tokens), only then is the
  context halved. An OOM that only shows up on the first real request
  re-boots the rung once with a lowered GMU — and that GMU is carried
  into the persisted entry.
- **k candidates:** block-size-friendly depths, filtered for structurally
  impossible ones (capture sizes must be multiples of k+1; this stack
  supports capture 8 at most ⇒ k ≤ 7). In baseline mode (`config.py`:
  `VLLM_CALIBRATION_K_EXHAUSTIVE`) every admissible k is measured.
- **Preparation:** the run stops llama-swap, waits process-based for the
  calibratable cards to be free of VRAM consumers (side channels may stay
  busy) and restarts the service at the end — that restart also loads the
  freshly persisted operating point.

## Results Qwen3.8-27B-NVFP4 (2026-08-30)

Complete matrix: 3 topologies × k=0…7, short and long, 2 h 03 min, fully
automatic (08:58–11:01). All values tok/s, coherence 3/3 at every
measurement point.

| Topology | k | Context | short | prefill | **long** | acceptance |
|---|---:|---:|---:|---:|---:|---:|
| TP1 RTX 8000 | 0 | 94,080 | 27.4 | 377 | 19.2 | — |
| TP2 RTX 8000 | 0 | 262,144 | 42.0 | 510 | 29.7 | — |
| TP2 RTX 8000 | 1 | 262,144 | 59.8 | 504 | 29.5 | 87 % |
| TP2 RTX 8000 | **2** | 262,144 | **69.1** | 503 | **37.6** | 97 % |
| TP2 RTX 8000 | 3 | 262,144 | 68.2 | 503 | 36.0 | 66 % |
| TP2 RTX 8000 | 4 | 262,144 | 68.6 | 504 | 32.6 | 50 % |
| TP2 RTX 8000 | 5 | 262,144 | 63.9 | 503 | 31.1 | 40 % |
| TP2 RTX 8000 | 6 | 262,144 | 60.2 | 503 | 30.1 | 34 % |
| TP2 RTX 8000 | 7 | 262,144 | 56.1 | 504 | 26.7 | 29 % |
| TP2 V100 | 0 | 235,200 | 41.0 | 667 | 30.3 | — |
| TP2 V100 | 1 | 183,200 | 48.9 | 582 | 30.9 | 85 % |
| **TP2 V100 (speed variant)** | **2** | 132,000 | 57.5 | 581 | **38.1** | 97 % |
| TP2 V100 | 3 | 129,600 | 61.0 | 580 | 37.2 | 66 % |
| TP2 V100 | 4 | 126,480 | 63.1 | 587 | 31.6 | 50 % |
| TP2 V100 | 5 | 172,992 | 60.6 | 587 | 29.5 | 40 % |
| TP2 V100 | 6 | 170,544 | 58.1 | 587 | 28.6 | 34 % |
| TP2 V100 | 7 | 168,064 | 55.6 | 588 | 25.3 | 29 % |
| TP2×PP2 grid | 0 | 262,144 | 40.6 | 839 | 29.4 | — |
| TP2×PP2 grid | 1 | 262,144 | 52.8 | 828 | 29.2 | 86 % |
| **Grid (operating point)** | **2** | **262,144** | 61.0 | **833** | **36.9** | 97 % |
| TP2×PP2 grid | 3 | 262,144 | 62.6 | 833 | 35.7 | 66 % |
| TP2×PP2 grid | 4 | 262,144 | 63.8 | 832 | 31.3 | 50 % |
| TP2×PP2 grid | 5 | 262,144 | 61.2 | 832 | 29.7 | 40 % |
| TP2×PP2 grid | 6 | 262,144 | 58.4 | 832 | 28.8 | 34 % |
| TP2×PP2 grid | 7 | 262,144 | 55.4 | 824 | 25.6 | 29 % |

The context values differ per k on the V100 rows because the draft head
costs KV budget and the calibration allows reduced context there (speed
candidate); on the full-context topologies every k carries the native
262,144.

**Chosen operating point:** TP2×PP2 grid, k=2, **36.9 tok/s
long-context decode at the full 262k context**, plus 833 tok/s prefill
and 61.0 tok/s at short context. The RTX TP2 rung is nominally 0.7 tok/s
faster in long-context decode (37.6) but sits inside the tie band — and
the grid ingests long prompts **65 % faster** (833 vs. 503 tok/s, i.e.
35 s instead of 58 s for 29k tokens). A speed variant (`…-vllm-speed`)
is persisted alongside: 2× V100 with k=2 at 38.1 tok/s with a reduced
132k context.

## Evolution: three eras

All rows RTX TP2 at the full 262k context, identical probe — only the
attention backend differs:

| k | Triton short | Triton long | FA2 short | FA2 long | FA2+fix short | FA2+fix long |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 42.7 | ~16 | 42.2 | 29.8 | 42.0 | 29.7 |
| 1 | — | — | 59.2 | 14.8 | 59.8 | **29.5** |
| 2 | — | — | 68.8 | 20.9 | 69.1 | **37.6** |
| 3 | — | — | 67.9 | 20.5 | 68.2 | **36.0** |
| 6 | 58.5 | ~6 | 60.0 | 18.6 | 60.2 | 30.1 |

Two jumps, two causes:

1. **Turing gets real FlashAttention.** Lacking FA2, vLLM fell back to
   `TRITON_ATTN` on sm75, whose long-context behaviour collapsed. The
   dormant sm75 path inside FlashAttention-2 could be enabled — but it
   computed wrong results for head dims > 64, because the software
   pipeline indexes the smem→register copies with the MMA K-step (1:1 on
   sm80+, but on sm75 the atom's K is 8 against a 16-wide `ldmatrix`, so
   accesses ran into the neighbouring tile). After the fix: prefill ×2.6
   (194 → 505 tok/s), long-context decode +47 %.
2. **The speculative verify ran without KV splits.** Split-k was only
   enabled for the q=1 decode path ("Only apply split-k for decoding"),
   so the multi-token verify walked the entire paged KV **serially** —
   measured at the kernel level as 20× slower than the q=1 path (2.6 ms
   instead of 0.13 ms per layer at 31k context). That alone turned
   speculation into a loss at long context. Enabling the splits exposed
   two further latent bugs in the combine kernel (varlen output written
   with a batch stride instead of `cu_seqlens` packing; the same
   assumption in the unpadded LSE) — both fixed.

The diagnosis ran through the acceptance counters: at k=2 acceptance was
97 %, and speculation was **still** slower than k=0. That ruled out an
acceptance collapse and proved a cost problem; py-spy showed 83 % GPU
wait time and the MTP profiler exonerated the drafter (10.7 ms per
step), leaving only the verify.

Both findings are upstream material for
[vllm-project/flash-attention](https://github.com/vllm-project/flash-attention);
the split finding also affects Ampere, more mildly there because serial
block work is faster.

## Findings

1. **Short-context measurements mislead.** Across all eras the short
   values barely moved (68.8 → 69.1 at k=2) while long-context decode
   nearly doubled. Measuring only short context hides the decisive
   difference — which is why the long-context decode now decides.
2. **The optimal k is not a constant**, but the acceptance curve depends
   on the model, not the hardware: 97 % at k=2, 66 % at k=3, 29 % at
   k=7 — identical across all three topologies. What differs is the
   verify cost per architecture.
3. **Tensor parallelism pays off even on PCIe x4 without P2P** (+53 %
   over TP1): during decode the all-reduce payloads are tiny
   (latency-bound, not bandwidth-bound) while the per-card weight read
   load halves.
4. **The 4-card grid wins on prefill, not on decode.** A single stream
   cannot pipeline during decode (stage 2 waits for stage 1), but it can
   during prefill: chunked prefill keeps both stages busy simultaneously,
   yielding 833 instead of 510 tok/s. For research sessions with large
   contexts that is the more noticeable gain.
5. **Volta and Turing are now on par.** Before the kernel fixes the V100
   speed variant beat the RTX speculation path by 83 %; now it is 1.3 %
   — with 130k fewer context tokens.
6. **vLLM's context-limit estimate is too optimistic with an MTP draft
   head loaded** — the calibration therefore adopts it iteratively across
   several boot rounds.
7. **A probe OOM is a property of the topology, not of a single k.** In
   this run six of seven grid rungs needed the same re-boot at a lowered
   GMU, because the full V100 stage left no room for the dequant
   workspace; on the V100 it additionally cascaded through several
   context reductions. About 30 of the 123 minutes went into that. The
   learned GMU is now carried through the rest of the sweep — with a
   fallback should it no longer carry the native context at a smaller k
   (context priority).

## Tile-tuning round (evening of 2026-08-30)

After the kernel fixes, both attention paths were tile-tuned
systematically — same methodology on both architectures: JIT probes of
single kernel instantiations with overridden tile constants, numerics
check against the reference tile, then microbench at production
geometry (H=4/HK=1/D=128 per GPU at TP2, ~31k paged KV; decode/verify
via q scaling, prefill as a 2048-token chunk).

### Turing (FA2 fork): dispatch and align want opposite tiles

| Tile M×N | q=1 | q=2 (verify) | q=8 | chunk 2048 |
|---|---:|---:|---:|---:|
| 64×64 (before, both paths) | 0.298 | 0.298 | 0.246 | 6.40 ms |
| **64×32** → new dispatch | **0.243** | **0.243** | **0.243** | 6.61 ms |
| **128×64** → new align | 1.00 | 0.81 | 0.81 | **4.06 ms** |

The dispatch path (decode/verify) gains **18 %** from the half-size N
tile: 32 instead of 48 KB shared memory means 2 CTAs per SM instead
of 1. The align path (prefill chunks) gains **37 %** from the
double-size M tile — and 128×64 is exactly the standard kernel's tile,
so the align path's bitwise-numerics argument stays intact. Both
tables are independently changeable; numerics suite PASS (2.4e-4,
fp16 noise).

**End to end the gain is not measurable on the 27B** — neither in the
grid (864/33.0 vs. 863/33.1) nor isolated on the RTX pair (A/B against
the old .so: 533/33.8 vs. 535/33.9 tok/s). Attention is not the
bottleneck on this model; the NVFP4/QPN8 GEMMs dominate step time. The
tiles stay regardless: zero regression, and on D=256 models
(Flash-Next) the attention share grows.

### Volta (1Cat flash_attn_v100): measured with the same protocol

The CUDA sources live in the 1Cat repo (`flash-attention-v100/`,
~14,000 lines; the wheel ships only the binary). Measurements on one
V100 (ms/call):

| Path | q=1 | q=2 | q=8 | chunk 2048 |
|---|---:|---:|---:|---:|
| decode_paged D128 (27B path) | 0.153 | 0.222 | 0.632 | — |
| *tuned Turing FA2 (reference)* | *0.243* | *0.243* | *0.243* | *4.06 ms* |
| prefill_paged D128 | — | — | — | 16.10 ms |
| decode_paged D256 H6 | 0.209 | 0.353 | 1.153 | — |
| decode_paged_xqa D256 H6 | 0.168 | 0.272 | 0.805 | — |
| decode_paged_xqa D256 H8 | 0.183 | 0.300 | 0.897 | — |

Three findings:

1. **Volta verify scales linearly with q** (0.153 → 0.632 for
   q=1 → 8): the smallq path treats verify tokens as separate batch
   rows, each walking the full KV on its own. The tuned Turing FA2
   handles q≤8 in a single KV pass (flat 0.243). At k=2 Volta is still
   slightly ahead — but this measurably explains why the V100s prefer
   small k in the k-sweep: every additional speculation token costs a
   full KV pass. The hand-curated XQA kernel is gated to D=256 only —
   **the 27B (D=128) never uses it.**
2. **Volta prefill is the open flank**: 16.1 ms per chunk against 4.06
   on the tuned RTX (factor 4 at ~15–20 % hardware distance). The cause
   is the 32×176 prefill tile: M=32 amortizes KV traffic poorly. But
   larger M is structurally expensive in this kernel design — the
   empirical smem bill is ≈ 272·N + 856·M + 4·M·N bytes (score matrix
   and out tile live in shared memory), and only a few candidates fit
   under the V100's 96 KB wall:

   | Tile M×N | chunk 2048 |
   |---|---:|
   | 32×176 (1Cat reference) | 16.10 ms |
   | 64×64 | 14.63 ms |
   | **64×80** | **14.06 ms (−13 %)** |
   | 48×112, 80×48 | numerics errors (M must be a multiple of 32) |

3. **The remaining gap is structural**, not closable via tiles: no
   double buffering of KV loads (sm70 has no cp.async), score and out
   in shared memory. That is material for the 1Cat contact — the 64×80
   tile as the immediate measure, the structure as a suggestion.

The counter-experiment on Volta (tile 64×80 in a rebuild of the
extension from the v1.3.0 state, deployed with backup) then confirmed
the overall pattern: end to end neutral there too (863/33.1 vs.
864/33.0). On the 27B at ~31k context, attention is not the bottleneck
on either architecture — the NVFP4/QPN8 GEMMs pace every stage. The
tile gains are an investment in long contexts and D=256.

## Flash-Next mini-sweep (evening of 2026-08-30)

Instead of an hours-long calibration: a targeted k-sweep at the
hand-curated grid point (TP2×PP2, partition 24/24, MML 16,384), long
point 13k, acceptance from the vLLM counters. Results (tok/s):

| Configuration | short | prefill | long 13k | accept short/long |
|---|---:|---:|---:|---|
| k=4, GMU 0.95 (hand-curated) | 54.1 | 335 | 12.6 | — |
| k=3, GMU 0.95 | 38.5 | 334 | 12.8 | — |
| k=0, GMU 0.95 | 32.2 | 359 | 26.3 | — |
| **k=4, GMU 0.93** | **54.3** | **392** | **28.3** | 57.9 % / 19.0 % |
| k=0, GMU 0.93 | 32.2 | 363 | 26.4 | — |
| k=4, GMU 0.93, MBT 4096/block 32 | 54.1 | 343 | 26.4 | 57.9 % / 14.8 % |

Four findings:

1. **GMU 0.95 throttled long decode to less than half** (12.6 instead
   of 28.3): the QSA Triton kernel and the verify need per-step
   temporary buffers; with no free VRAM every step pays synchronous
   allocator penalties (one run tipped over entirely with a Triton
   OOM). k=0 was immune — the pressure only hit the speculation path.
   New operating point: GMU 0.93.
2. **MTP acceptance collapses at long context** (57.9 % → 19.0 %) —
   externally confirmed: vllm#47602 measures the same on Qwen3.6-27B
   (64.9 % → 39.1 %, speedup +129 % → −51 %); the cause there as here:
   a shallow draft head drifts away from the main model as context
   grows. The 27B is the exception, not the rule: its head holds 97 %
   at long context too.
3. **Speculation still stays net positive** (28.3 vs. 26.4 long,
   +69 % short) — a length-dependent spec toggle is not worth it here.
4. **MBT 4096/block 32 does NOT transfer** (−12 % prefill, acceptance
   drops further): the chunk/block-size axis is model-specific — the
   calibration defaults therefore stay neutral (2048/16); only
   measured operating points carry deviating values.

Side note: k=0 reproducibly shows coherence 2/3 (3/3 with
speculation) — kernel-path numerics flip a tie-break at temperature 0;
production runs with speculation.

## Outlook

- Qwen3.8-Flash-Next-180B: mini-sweep done (see above), GMU 0.93 in
  the operating point. A full calibration (topology ladder) remains
  optional; the QSA Triton kernel as a third attention path is still
  unmeasured on sm70/sm75.
- The Volta prefill tile 64×80 has been rebuilt from the v1.3.0 source
  state and deployed (microbench 14.09 ms confirmed, coherence 3/3) —
  end to end on the 27B at ~31k just as neutral as the Turing tiles
  (863/33.1 vs. 864/33.0): on this model the NVFP4 GEMMs dominate both
  stages. The kernel gains only become visible at much longer contexts
  (the attention share grows with the KV) and at D=256 (Flash-Next).
  Report the linear verify scaling to 1Cat.
- Open side issue: `_sm70_qpn8_indices` consumes roughly 9 % of the CPU
  time per step (dequant helper path).
