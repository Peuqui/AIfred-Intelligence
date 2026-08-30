# vLLM Auto-Calibration: Topology Search and k-Sweep on Heterogeneous GPUs

Status: 2026-08-30 · German version: [vllm-autokalibration.md](../../de/benchmarks/vllm-autokalibration.md)

AIfred's calibrate button measures vLLM checkpoints fully automatically:
it unloads running models, builds a topology ladder from the installed
GPUs (TP within a compute class, PP across class boundaries), boots and
measures every rung for real — at short **and** long context —, runs a
speculation-depth sweep (MTP, `k`) and persists the result as a
llama-swap entry plus an operating-point profile with a hardware
fingerprint.

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

Complete matrix: 3 topologies × k=0…7, short and long, ~2 hours, fully
automatic. All values tok/s, coherence 3/3 at every measurement point.

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
| **TP2 V100 (speed)** | **2** | 132,000 | 57.5 | 581 | **38.1** | 97 % |
| TP2 V100 | 3 | 129,600 | 61.0 | 580 | 37.2 | 66 % |
| TP2 V100 | 5 | 172,992 | 60.6 | 587 | 29.5 | 40 % |
| TP2 V100 | 7 | 168,064 | 55.6 | 588 | 25.3 | 29 % |
| TP2×PP2 grid | 0 | 262,144 | 40.6 | 839 | 29.4 | — |
| **Grid (operating point)** | **2** | **262,144** | 61.0 | **833** | **36.9** | 97 % |
| TP2×PP2 grid | 3 | 262,144 | 62.6 | 833 | 35.7 | 66 % |
| TP2×PP2 grid | 5 | 262,144 | 61.2 | 832 | 29.7 | 40 % |
| TP2×PP2 grid | 7 | 262,144 | 55.4 | 824 | 25.6 | 29 % |

(V100 and grid rows abridged; the complete matrix is kept as
`final-matrix-2026-08-30.txt` in the v100-skinny repo.)

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

## Outlook

- Qwen3.8-Flash-Next-180B (NVFP4, quantized MTP block): calibration
  against the hand-curated operating point (51.9/68.2 tok/s) is pending —
  results will follow here.
- The V100 XQA verify might benefit from the same split principle
  (separate kernel path, not yet investigated).
- Open side issue: `_sm70_qpn8_indices` consumes roughly 9 % of the CPU
  time per step (dequant helper path).
