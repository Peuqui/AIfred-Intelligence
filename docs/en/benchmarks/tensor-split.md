# Tensor Split Benchmark: Speed Variant vs. Full Context

Real-world performance comparison of two llama-swap configurations for the same model,
measured through AIfred's multi-agent tribunal system (AIfred + Sokrates + Salomo, 2 rounds).

**Date:** 2026-02-19

---

## Test Setup

| Parameter | Value |
|-----------|-------|
| **Model** | Qwen3-Next-80B-A3B-Instruct-Q4_K_M (46.6 GB) |
| **Hardware** | RTX 8000 (CUDA0, 48 GB GDDR6, USB4 eGPU) + Tesla P40 (CUDA1, 24 GB GDDR5X, OCuLink eGPU), both PCIe 3.0 x4 |
| **CUDA order** | `CUDA_DEVICE_ORDER=FASTEST_FIRST` |
| **Prompt** | "Ist Wasser nass?" (philosophical question, 6-turn multi-agent debate) |
| **Mode** | Auto-Consensus (AIfred → Sokrates R1 → Salomo R1 → AIfred R2 → Sokrates R2 → Salomo R2) |
| **llama.cpp** | v8076, `GGML_CUDA_GRAPH_OPT=1` |
| **Base flags** | `-ngl 99 -np 1 -fit off --flash-attn on -t 4 -b 512 -ub 256` |

### Configurations Compared

| | Speed Variant (11:1) | Normal (2:1) |
|---|---|---|
| **llama-swap model** | `qwen3-next-80b-a3b-instruct-q4_k_m-speed` | `qwen3-next-80b-a3b-instruct-q4_k_m` |
| **Tensor split** | `-ts 11,1` (92% on RTX 8000) | `-ts 2,1` (67% on RTX 8000) |
| **Context** | 32,768 tokens | 262,144 tokens (native max) |
| **KV cache type** | `-ctk q4_0 -ctv q4_0` | `-ctk q4_0 -ctv q4_0` |
| **VRAM usage** | 52,082 MB (35,275 + 16,807) | ~65,000 MB (balanced) |

The speed variant was found via binary search during calibration:

```
99:1 → failed    50:1 → failed    26:1 → failed    14:1 → failed
 8:1 → fits      11:1 → fits      12:1 → failed
Result: 11:1 in 7 iterations
```

---

## Per-Turn Results

### Round 1 (Initial Responses)

| Turn | Metric | Speed (11:1) | Normal (2:1) | Delta |
|------|--------|-------------|-------------|-------|
| **AIfred** | TTFT | 3.89s | 3.86s | +0.8% |
| | PP | 310.1 tok/s | 312.5 tok/s | -0.8% |
| | **Gen tok/s** | **33.4** | **29.0** | **+15.2%** |
| | Tokens | 321 | 305 | +5.2% |
| | Inference | 9.6s | 10.5s | -8.6% |
| **Sokrates** | TTFT | 8.39s | 8.16s | +2.8% |
| | PP | 319.0 tok/s | 325.9 tok/s | -2.1% |
| | **Gen tok/s** | **35.0** | **30.2** | **+15.9%** |
| | Tokens | 799 | 761 | +5.0% |
| | Inference | 22.9s | 25.2s | -9.1% |
| **Salomo** | TTFT | 7.76s | 7.33s | +5.9% |
| | PP | 331.3 tok/s | 343.3 tok/s | -3.5% |
| | **Gen tok/s** | **30.9** | **28.3** | **+9.2%** |
| | Tokens | 548 | 558 | -1.8% |
| | Inference | 17.7s | 19.7s | -10.2% |

### Round 2 (Refinement + Critical Review + Synthesis)

| Turn | Metric | Speed (11:1) | Normal (2:1) | Delta |
|------|--------|-------------|-------------|-------|
| **AIfred R2** | TTFT | 11.57s | 11.30s | +2.4% |
| | PP | 334.2 tok/s | 339.3 tok/s | -1.5% |
| | **Gen tok/s** | **18.8** | **20.5** | **-8.3%** |
| | Tokens | 332 | 430 | -22.8% |
| | Inference | 17.6s | 21.0s | -16.2% |
| **Sokrates R2** | TTFT | 13.14s | 13.10s | +0.3% |
| | PP | 333.0 tok/s | 338.3 tok/s | -1.6% |
| | **Gen tok/s** | **27.2** | **20.8** | **+30.6%** |
| | Tokens | 719 | 519 | +38.5% |
| | Inference | 26.5s | 24.9s | +6.4% |
| **Salomo R2** | TTFT | 12.47s | 11.65s | +7.0% |
| | PP | 336.3 tok/s | 347.7 tok/s | -3.3% |
| | **Gen tok/s** | **19.2** | **21.2** | **-9.4%** |
| | Tokens | 368 | 471 | -21.9% |
| | Inference | 19.2s | 22.3s | -13.9% |

---

## Aggregated Analysis

### Average by Round

| Metric | Speed R1 | Normal R1 | Delta | Speed R2 | Normal R2 | Delta |
|--------|---------|----------|-------|---------|----------|-------|
| TTFT | 6.68s | 6.45s | +3.6% | 12.39s | 12.02s | +3.1% |
| PP | 320.1 tok/s | 327.2 tok/s | -2.2% | 334.5 tok/s | 341.8 tok/s | -2.1% |
| **Gen tok/s** | **33.1** | **29.2** | **+13.4%** | **21.7** | **20.8** | **+4.3%** |

### Session Totals

| Metric | Speed (11:1) | Normal (2:1) | Delta |
|--------|-------------|-------------|-------|
| Total inference time | 113.5s | 123.6s | **-8.2%** |
| Total tokens generated | 3,087 | 3,044 | +1.4% |
| Effective throughput | 27.2 tok/s | 24.6 tok/s | **+10.6%** |
| Total TTFT (sum) | 57.2s | 55.5s | +3.1% |
| Average PP | 327.3 tok/s | 334.5 tok/s | -2.2% |
| Context utilization | 11% of 32K | 1% of 262K | — |

---

## Key Findings

1. **Generation speed is 10–15% faster with 11:1 in Round 1** — when prompt context is still
   small. Minimizing layers on the slower P40 (346 GB/s vs 672 GB/s RTX 8000) reduces the
   per-token bottleneck during autoregressive generation.

2. **The advantage shrinks in Round 2 (~4%)** — as context grows, more of the generation
   time is spent in attention (which scales with sequence length). The R2 results show higher
   variance (18.8–27.2 tok/s), suggesting that output length and content complexity matter
   more than split ratio at these context sizes.

3. **Prompt processing (PP) is ~2% faster with normal 2:1 split** — PP benefits from parallel
   KV cache writes across both GPUs. With 11:1, 92% of the KV cache is written to one GPU,
   creating a bottleneck. The effect is small but consistent across all turns.

4. **TTFT is ~3% slower with the speed variant** — TTFT = PP + model overhead + first-token
   latency. Since PP is slightly slower with aggressive split, TTFT follows. The difference is
   <1s in absolute terms.

5. **Inference time ≠ generation speed** — shorter wall-clock inference does not always mean
   higher tok/s. Example: AIfred R2 speed variant finishes in 17.6s (332 tokens, 18.8 tok/s)
   vs normal in 21.0s (430 tokens, 20.5 tok/s). The speed variant is faster in wall-clock
   because it generated **fewer tokens** — not because it generated them faster. The model
   is nondeterministic, so output length varies between runs. Always compare tok/s (throughput)
   rather than inference time (which conflates throughput with output length).

6. **Overall wall-clock time is 10s shorter with the speed variant** — 113.5s vs 123.6s total.
   This reflects a combination of genuinely faster generation in R1 and nondeterministic output
   length differences across 6 turns.

---

## When to Use Which Configuration

| Use case | Recommended config | Reason |
|----------|--------------------|--------|
| Short conversations (<8K context) | Speed (11:1, 32K) | 10–15% faster generation |
| Multi-turn tribunal (typical) | Speed (11:1, 32K) | ~10% faster overall, 32K is sufficient |
| Long conversations (>32K tokens) | Normal (2:1, 262K) | Speed variant would truncate history |
| RAG with large context | Normal (2:1, 262K) | Need full context for document retrieval |
| Batch processing (many short prompts) | Speed (11:1, 32K) | Maximizes throughput per request |

**Rule of thumb:** Use the speed variant by default. Switch to normal only when you
actually need >32K context tokens.

---

## Calibration Details

The speed variant was automatically calibrated by AIfred's VRAM calibration system:

- **Phase 1:** Native context test → 262,144 tokens fits with 2:1 split
- **Phase 2:** Binary search for maximum tensor split at 32K context
  - Tested 7 ratios (99:1 → 11:1), converged in ~7 minutes
  - Result: 11:1 (91.7% on RTX 8000, 8.3% on P40)
  - VRAM breakdown: CUDA0 = 35,275 MB, CUDA1 = 16,807 MB

Both configurations are stored in the llama-swap config and selectable via model name:
- `qwen3-next-80b-a3b-instruct-q4_k_m` → full context, balanced split
- `qwen3-next-80b-a3b-instruct-q4_k_m-speed` → reduced context, aggressive split

---

## Hardware Context

See [llamacpp-setup.md](../guides/llamacpp-setup.md#benchmark-tensor-split-optimization-2026-02-18)
for synthetic `llama-bench` results on the same hardware, including per-split-ratio measurements
for Qwen3-32B.

---

*Generated from AIfred session data on 2026-02-19. Sessions: `a903c6f6` (speed) and `0adf7397` (normal).*
