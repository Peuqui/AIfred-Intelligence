# Recommended Parameters per Model (llama-server)

> **Deutsche Version:** [model-params.md](../../de/benchmarks/model-params.md)

> **Historical benchmark.** Measured in early 2026 on the previous Tesla P40-based
> setup (Pascal). The current AIfred machine runs 2× Quadro RTX 8000 + 3× Tesla V100,
> so these numbers do not describe today's performance — they remain a useful
> reference for anyone running P40 or similar Pascal cards.

Last Updated: 2026-02-21 — Official Unsloth-Docs + Own Tests

---

## GPT-OSS-120B

| Parameter | Value | Important |
|-----------|-------|-----------|
| --jinja | yes, required | Harmony-Template |
| --reasoning-format | none | NOT deepseek! |
| --chat-template-kwargs | '{"reasoning_effort": "medium"}' | low/medium/high possible |
| --temp | 1.0 | |
| --top-p | 1.0 | |
| --top-k | 0 (official) or 100 (speed trick) | |
| --min-p | 0.0 | |
| --repeat-penalty | DO NOT set! | Explicitly forbidden |
| -ctk / -ctv | DO NOT set! | KV-Quant kills performance (84% slower PP, 58% slower TG) |
| -fa | on | |
| -b / -ub | 2048 / 2048 | |

## GLM-4.7-REAP-218B

| Parameter | Value | Important |
|-----------|-------|-----------|
| --jinja | yes, required | |
| --reasoning-format | deepseek | |
| --chat-template-kwargs | '{"enable_thinking": false}' | To disable thinking |
| --temp | 0.7 (Coding) / 1.0 (general) | |
| --top-p | 1.0 (Coding) / 0.95 (general) | |
| --min-p | 0.01 | llama.cpp default 0.1 is too high |
| --repeat-penalty | 1.0 (= disabled, CRITICAL!) | Any other value breaks output |
| -ctk / -ctv | q8_0 / q8_0 | OK, saves VRAM |
| -fa | on | |
| -b / -ub | 2048 / 512 | |

## MiniMax-M2.5

| Parameter | Value | Important |
|-----------|-------|-----------|
| --jinja | yes, required | Otherwise infinite loops |
| --reasoning-format | none | NOT deepseek! MiniMax format is different |
| --temp | 1.0 | |
| --top-p | 0.95 | |
| --top-k | 40 | |
| --min-p | 0.01 | |
| --repeat-penalty | 1.0 | |
| -ctk / -ctv | q4_0 / q4_0 | OK for Q2_K_XL model |
| -fa | on | |
| -b / -ub | 4096 / 4096 | MoE benefits from large batches |

## Qwen3 — Instruct-Variants (4B, 14B, 30B-A3B, 235B-A22B)

| Parameter | Value | Important |
|-----------|-------|-----------|
| --jinja | yes | |
| --reasoning-format | not needed | Instruct = no thinking |
| --temp | 0.7 | |
| --top-p | 0.8 | |
| --top-k | 20 | |
| --min-p | 0 | |
| --presence-penalty | 1.5 (for repetitions) | |
| -ctk / -ctv | q8_0 / q8_0 | OK |
| -fa | on | |
| --no-context-shift | yes | Required with KV-Quant |

## Qwen3-Next-80B (Thinking + Instruct)

| Parameter | Value | Important |
|-----------|-------|-----------|
| --jinja | yes | |
| --reasoning-format | deepseek (Thinking) / not needed (Instruct) | |
| --temp | 0.6 (Thinking) / 0.7 (Instruct) | |
| --top-p | 0.95 (Thinking) / 0.8 (Instruct) | |
| --top-k | 20 | |
| -ub | max 512! | Higher = Crash |
| -ctk / -ctv | q4_1 / q4_1 | |
| KV-Cache-Reuse | broken | Hybrid-Architecture |

---

## Key Findings (Original)

- **GPT-OSS**: No KV-Cache-Quant! (-ctk/-ctv kills performance massively)
- **GPT-OSS**: --reasoning-format none, NOT deepseek (Harmony-Format is incompatible)
- **GLM-REAP**: --repeat-penalty must be 1.0 (anything else = infinite loops)
- **MiniMax**: --reasoning-format none (no deepseek)
- **Qwen3-Next**: -ub max 512 (higher crashes due to hybrid-architecture)
- **--jinja is REQUIRED for ALL models**
- **--no-context-shift** is required for Qwen3 + KV-Quant

---

# 📌 Our Hardware-Specific Adjustments (Dual-GPU: RTX 8000 48GB + RTX P40 24GB)

**Last Updated:** 2026-02-21 — Stress-Tests with 200 Tokens passed

## 🚀 Direct-IO Performance

| Parameter | Value | Effect |
|-----------|-------|--------|
| --direct-io | **yes, on ALL models** | **~45x faster loading!** (60-90s → 2s) |

**Advantages:**
- Bypasses CPU-RAM Page-Cache
- Fills VRAM directly (no detour)
- Less CPU-RAM usage

**Works with:** ext4, xfs, btrfs file systems

---

## 200B+ Models: KV-Quantization & Batch-Sizes

### Qwen3-235B-A22B Instruct

| Parameter | Original | Our Adjustment | Reason |
|-----------|----------|----------------|--------|
| -ctk / -ctv | q8_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | Default | **1024 / 512** | Optimal for VRAM |
| --direct-io | — | **yes** | 2s loading |
| -ngl | — | 73 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Our Tests:**
- ✅ Stress-Test: 160 Tokens stable
- ✅ VRAM: 43.5 GB + 21.4 GB
- ✅ CPU-RAM: ~24.9 GB (6 GB free)
- ❌ q8_0: OOM (crashes)

### GLM-4.7-REAP-218B

| Parameter | Original | Our Adjustment | Reason |
|-----------|----------|----------------|--------|
| -ctk / -ctv | q8_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | 2048 / 512 | **2048 / 512** | Stable |
| --direct-io | — | **yes** | 2s loading |
| -ngl | — | 66 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Our Tests:**
- ✅ Stress-Test: 130 Tokens stable
- ✅ VRAM: 42 GB + 21 GB
- ✅ CPU-RAM: ~25 GB
- ❌ q8_0: OOM (crashes)

### MiniMax-M2.5

| Parameter | Original | Our Adjustment | Reason |
|-----------|----------|----------------|--------|
| -ctk / -ctv | q4_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | 4096 / 4096 | **1024 / 512** | 4096 = OOM! |
| --direct-io | — | **yes** | 2s loading |
| -ngl | — | 48 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Our Tests:**
- ✅ Stress-Test: 200 Tokens stable
- ✅ VRAM: 42 GB + 20.4 GB
- ✅ CPU-RAM: ~24.6 GB
- ❌ q8_0: OOM (crashes)
- ❌ -b 4096: OOM (batches too large)

---

## Smaller Models (<100B): KV-Cache f16

**Finding:** For smaller models, **f16 is faster than q8_0**!

| Model | -ctk / -ctv | Reason |
|-------|-------------|--------|
| Qwen3-4B | **f16** | Faster than q8_0 |
| Qwen3-14B | **f16** | Faster than q8_0 |
| Qwen3-30B-A3B | **f16** | Faster than q8_0 |
| Qwen3-Next-80B | **f16** | Hybrid-Architecture (KV-Reuse broken) |
| GPT-OSS-120B | **f16** | Officially recommended |

---

## 📋 Final Config for Our Hardware

**All models run stably with:**
- ✅ --direct-io (2s loading)
- ✅ KV-Quant q4_0 for 200B+ models
- ✅ KV-Cache f16 for <100B models
- ✅ Optimized batch-sizes

**Note:** For hardware upgrades (more VRAM), the original parameters from the table above can be tested!
