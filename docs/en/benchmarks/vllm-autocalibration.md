# vLLM Auto-Calibration: Topology Search and k-Sweep on Heterogeneous GPUs

As of 2026-08-29 · German version: [vllm-autokalibration.md](../../de/benchmarks/vllm-autokalibration.md)

AIfred's calibrate button measures vLLM checkpoints fully automatically:
it derives a topology ladder from the installed GPU set (TP within a
compute class, PP across class boundaries), boots and probes every rung
for real, runs a speculation-depth sweep (MTP, `k`) on the winning
candidates, and persists the result as a llama-swap entry plus an
operating-point profile bound to a hardware fingerprint.

## Test system

| Component | Value |
|---|---|
| GPUs (calibratable) | 2× Quadro RTX 8000 48 GB (SM75) + 2× Tesla V100-PCIE-32GB (SM70) |
| Side channel (reserved) | 1× V100 32 GB (TTS + vision, shared card) |
| Interconnect | PCIe Gen3 x4 per card (M.2 OCuLink / USB4); P2P disabled (`NCCL_P2P_DISABLE=1`) |
| Stack | [1Cat-vLLM 1.3.0](https://github.com/dnv2003/v100-skinny) with v100-skinny kernels (NVFP4 on Volta/Turing) |
| Model | RadixArk/Qwen3.8-27B-NVFP4 (20.4 GiB, native context 262,144) |

## Methodology

- **Probe:** fixed technical prose prompt (hard for the drafter),
  200 tokens, `ignore_eos`, 1 warmup + 2 measured runs, **wall-clock
  including prefill**. Comparability between rungs is the point, not the
  absolute number — structured prompts (math/code) score much higher
  with MTP.
- **Context first:** every rung competes at the largest context it can
  hold (starting at native). The winner is the fastest rung **among
  those carrying the full native context**; reduced-context rungs are
  still measured and reported as a speed candidate, but cannot win the
  operating point.
- **OOM retry ladder:** if vLLM states a context limit itself, it is
  adopted. A bare CUDA OOM first raises the per-GPU reserve (+1/+2 GB —
  the Inductor compile workspace needs headroom on a card that GMU fills
  on purpose; this only costs KV pool blocks, not a single context
  token), and only then halves the context.
- **k candidates:** block-size-friendly depths, filtered for
  structurally impossible ones (capture sizes must be multiples of k+1;
  this stack supports capture sizes up to 8 ⇒ k ≤ 7).
- GMU is derived from a fixed per-card reserve (1,024 MB), not a
  percentage rule of thumb.

## Results, Qwen3.8-27B-NVFP4 (2026-08-29)

Full run: 4 topologies + 2 complete k-sweeps + speed candidate,
~45 minutes, fully automatic.

| Topology | GPUs | Context | k=0 | k=5 | k=6 | k=7 |
|---|---|---:|---:|---:|---:|---:|
| TP1 | 1× RTX 8000 | 94,080 | 27.6 | | | |
| **TP2 (operating point)** | 2× RTX 8000 | **262,144** | 42.7 | | **58.5** | 54.6 |
| TP2×PP2 grid | 2× RTX + 2× V100 | 262,144 | 40.9 | | 57.3 | 54.5 |
| TP2 V100 | 2× V100 | 235,200 | 40.9 | | | |
| **TP2 V100 (speed candidate)** | 2× V100 | ~140,000 | | **60.5** | 58.0 | 55.6 |

All values in tok/s; coherence 3/3 at every scored data point.

**Chosen operating point:** TP2 on the RTX 8000 pair, k=6, **58.5 tok/s
at the full 262k context** (persisted; llama-swap adopts the entry 1:1
on model start).

## Findings

1. **Tensor parallelism pays off even on PCIe x4 without P2P** (+55%
   over TP1): decode-time all-reduce payloads are tiny (latency-bound,
   not bandwidth-bound) while the per-card weight read halves.
2. **The 4-card grid does not pay for this model:** host-mediated PP
   overhead exactly eats the XQA verifier advantage of the V100 last
   stage (57.3 vs 58.5).
3. **Volta beats Turing once speculation runs:** 2× V100 with the XQA
   verifier at k=5 deliver 60.5 tok/s — more than the RTX operating
   point — at roughly half the context. Without speculation (k=0)
   Turing is slightly ahead (kernel advantage); with speculation the
   picture flips (verifier advantage).
4. **The optimal k depends on topology and stack** and is not a
   constant: RTX optimum k=6, V100 optimum k=5, and k=8 is structurally
   impossible on this stack (capture-size arithmetic). The hand-tuned
   campaign value k=7 was the optimum on no topology — measuring beats
   assuming.
5. **vLLM's context-limit estimate is too optimistic with the MTP draft
   head loaded** — the calibration therefore adopts it iteratively
   across several boot rounds.

## Outlook

- Qwen3.8-Flash-Next-180B (NVFP4, quantized MTP block): calibration
  against the hand-tuned operating point (51.9/68.2 tok/s) is pending —
  results will land here.
- Reduced-context speed candidates as a dedicated llama-swap entry
  (`…-vllm-speed`) are under consideration, mirroring the GGUF speed
  variants.
