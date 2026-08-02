# llama.cpp + llama-swap Setup Guide

Reference document for the llama.cpp integration in AIfred via llama-swap.
Updated when hardware changes or new llama.cpp releases introduce relevant changes.

**Last updated:** 2026-02-18

> ⚠️ **Hardware note:** Benchmarks and GPU tables in this guide were measured
> on the previous GPU setup (Tesla P40 / Quadro RTX 8000 / RTX 3090 Ti). The
> current machine runs 2× RTX 8000 + 3× V100 (192 GB VRAM total). The
> workflow, configuration steps, and autoscan/calibration behaviour remain
> valid; absolute performance numbers are historical.

---

## Hardware Overview

### GPU Comparison

| Spec | Tesla P40 | Quadro RTX 8000 | RTX 3090 Ti |
|------|-----------|-----------------|-------------|
| Architecture | Pascal (GP102) | Turing (TU102) | Ampere (GA102) |
| Compute Capability | 6.1 | 7.5 | 8.6 |
| VRAM | 24 GB GDDR5X | 48 GB GDDR6 | 24 GB GDDR6X |
| Bandwidth | 346 GB/s | 672 GB/s | 936 GB/s |
| Tensor Cores | None | 576 (1st gen) | 328 (2nd gen) |
| FP16 | 1/64 of FP32 (!) | Full (via TC) | Full (via TC) |
| NVLink | No | Yes (100 GB/s) | No |
| TDP | 250W | 295W | 450W |

### NVIDIA llama.cpp Optimizations (CES 2026)

| Optimization | Effect | P40 | RTX 8000 | 3090 Ti |
|---|---|---|---|---|
| CUDA Graphs | up to 35% faster token gen | Yes | Yes | Yes |
| MMVQ Kernel | Better quantized inference | Yes | Yes | Yes |
| MMQ (INT8 TC) | INT8 Tensor Core kernels | No | Yes | Yes |
| Flash Attention | Less VRAM, faster PP | Yes (model-dependent!) | Yes | Yes |
| GPU Token Sampling | Eliminates CPU-GPU transfers | Yes | Yes | Yes |
| Model Loading | up to 65% faster | Yes | Yes | Yes |
| FP8 W8A16 | Weight-only FP8 | No | Yes (vLLM) | Yes (Marlin) |
| NVFP4 | New quantization format | No | No | No |
| vLLM compatible | Requires CC >= 7.0 | No | Yes | Yes |

---

## Performance Parameters

### All Relevant llama-server Flags

| Parameter | Syntax | Default | Description |
|---|---|---|---|
| `-ngl` | `-ngl 99` / `-ngl auto` | `auto` | GPU layer offload count |
| `-c` | `-c 8192` | from model | Context size (KV cache scales linearly) |
| `-fa` | `-fa on` / `--flash-attn off` | `auto` | Flash Attention (global, not per GPU) |
| `-ctk` | `-ctk q8_0` | `f16` | KV cache key type (q8_0, q4_0, f16, f32) |
| `-ctv` | `-ctv q8_0` | `f16` | KV cache value type |
| `-b` | `-b 2048` | `2048` | Logical batch size (prompt processing) |
| `-ub` | `-ub 512` | `512` | Physical batch size (GPU batch) |
| `-sm` | `-sm layer` | | Split mode: layer, row, none |
| `-ts` | `-ts 1,2.5` | | Tensor split ratio between GPUs |
| `-mg` | `-mg 1` | `0` | Main GPU (output layer, scratch buffers) |
| `--mlock` | `--mlock` | off | Pin RAM pages, prevent swapping |
| `--no-mmap` | `--no-mmap` | mmap on | Load model fully instead of lazy mmap |
| `-t` | `-t 8` | auto | CPU threads (generation) |
| `-tb` | `-tb 16` | same as `-t` | CPU threads (batch/prompt) |
| `-np` | `-np 1` | `auto` (=4!) | Parallel slots (multi-user) |
| `-fit` | `-fit off` | `on` | Auto-fit parameters to VRAM (P40: off!) |
| `-dev` | `-dev CUDA0,CUDA1` | | Explicit GPU selection |
| `-ot` | `-ot "regex=DEVICE"` | | Override tensor placement (ik_llama.cpp) |

### Environment Variables

```bash
export CUDA_DEVICE_ORDER=FASTEST_FIRST # Fastest GPU becomes CUDA0 (see tensor-split section)
export GGML_CUDA_GRAPH_OPT=1           # 10-15% faster token generation
```

### KV Cache Quantization

| Type | VRAM savings vs f16 | Speed impact (PP) | Quality impact | Notes |
|---|---|---|---|---|
| `q8_0` | ~50% | **+30-134% faster** | Negligible | **Recommended** |
| `q4_0` | ~72% | +20-30% faster | Noticeable at keys | Only if VRAM is extremely tight |
| `f16` | Baseline | Baseline | None | Default |

**Important:**
- KV cache quantization requires Flash Attention (`--flash-attn on`)
- Q8_0 is faster than Q4_0 at PP (less dequantization overhead)
- Generation speed is identical across all variants (~46-63 tok/s depending on model)
- KV quant ≠ model quant: KV quant saves attention bandwidth, model quant saves matmul compute

### P40-Specific Notes

| Parameter | Recommendation | Reason |
|---|---|---|
| `-fit` | `off` | `-fit on` crashes on Pascal with CUDA OOM at `cudaMemGetInfo` |
| `-np` | `1` | Default `auto` sets 4 slots = 4× compute buffer VRAM |
| `--flash-attn` | `on` | Less VRAM (compute buffer -86%), faster PP for Qwen3 |
| `-ctk`/`-ctv` | `q8_0` | Halve KV cache, negligible quality loss |

---

## Benchmark: Tesla P40 (2x 24 GB, llama.cpp v8076)

Date: 2026-02-17. Hardware: 2x Tesla P40 (24 GB GDDR5X, PCIe 3.0 x4 via OCuLink eGPU).
llama.cpp version: 8076 (d61290111), compiled with `GGML_CUDA=ON`.

**Test prompt:** `"Write a short poem about the sun in exactly 4 lines."` (21 tokens, max_tokens=100)
**Base parameters:** `-ngl 99 -np 1 -fit off -t 4 -b 2048 -ub 512`

### Qwen3-4B-Instruct-2507 (Q4_K_M, Single GPU CUDA0)

| Config | Context | Prompt (tok/s) | Gen (tok/s) | VRAM total (MiB) | KV buffer (MiB) | Compute buffer (MiB) |
|---|---|---|---|---|---|---|
| FA off, FP16 KV | 32K | 205.8 | 61.7 | 9,283 | 4,608 | 2,142 |
| **FA on, FP16 KV** | 32K | **352.9** | 63.9 | 7,441 | 4,608 | 302 |
| **FA on, Q8 KV** | 32K | **481.3** | 62.5 | **5,293** | **2,448** | 302 |
| FA on, Q8 KV | 131K | 490.2 | 62.7 | 12,747 | 9,792 | 410 |
| FA on, **Q4 KV** | 32K | 391.4 | 60.8 | 4,141 | **1,296** | 302 |

**Result:** FA + Q8 KV on P40 is the clear winner:
- **PP: +134% faster** than baseline (481 vs 206 tok/s)
- **Gen: unchanged** (~62 tok/s)
- **VRAM: -43%** (5,293 vs 9,283 MiB)
- **Max context: 131K** (native 128K) on a single P40 with 4B Q4_K_M

### Qwen3-30B-A3B-Instruct-2507 (Q8_0, Dual GPU, `-sm layer --tensor-split 1,1`)

| Config | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 VRAM (MiB) | GPU1 VRAM (MiB) | Free total (MiB) |
|---|---|---|---|---|---|---|
| FA off, FP16 KV | 32K | 95.8 | 46.8 | 19,913 | 18,837 | 7,064 |
| FA on, FP16 KV | 32K | 91.7 | 47.4 | 17,953 | 16,929 | 10,932 |
| **FA on, Q8 KV** | 32K | **124.6** | 45.8 | 17,215 | 16,251 | **12,348** |
| FA on, Q8 KV | 65K | 114.6 | 46.5 | 18,353 | 17,161 | 10,300 |
| FA on, Q8 KV | 98K | 117.7 | 46.4 | 19,491 | 18,071 | 8,252 |
| FA on, Q8 KV | 131K | 122.4 | 47.2 | 20,631 | 18,983 | 6,200 |
| FA on, Q8 KV | 200K | 108.0 | 46.3 | 21,777 | 20,103 | 3,934 |
| FA on, Q8 KV | 220K | 115.1 | 46.9 | 22,353 | 20,581 | 2,880 |
| FA on, **Q4 KV** | 32K | 123.2 | 46.1 | 16,815 | 15,883 | 13,116 |

**Result:** FA + Q8 KV is optimal for 30B as well:
- **PP: +30% faster** (124.6 vs 95.8 tok/s at 32K)
- **Gen: stable ~46-47 tok/s** regardless of context window
- **VRAM: -5,284 MiB freed** vs baseline
- **Max context: 220K** (native 262K) on 2x P40 with 30B Q8_0
- 262K crashes: "failed to allocate compute buffers" (793 MiB missing)

### Qwen3-Next-80B-A3B-Instruct (Q4_K_M, 48 layers, 512 experts/10 active)

Model: 46.6 GB (Q4_K_M), ~0.97 GB/layer. 2x P40 = 48 GB VRAM.
Base parameters: `-np 1 -fit off --flash-attn on -ctk q8_0 -ctv q8_0 -sm layer --tensor-split 1,1 -t 4 -b 512 -ub 256`

#### Layer Offloading (`-ngl`): Speed vs Context Trade-off

| -ngl | CPU layers (GB) | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 free (MiB) |
|---|---|---|---|---|---|
| 44 | 4 (~3.9 GB) | 4K | 75.7 | **24.1** | 452 |
| 44 | 4 | 32K | 91.8 | **24.3** | 232 |
| 44 | 4 | 57K | 81.5 | **22.9** | 42 |
| 44 | 4 | 65K | CRASH | - | - |
| 42 | 6 (~5.8 GB) | 4K | 65.2 | **21.9** | 1,344 |
| 42 | 6 | 131K | 75.3 | **21.7** | 236 |
| 42 | 6 | 144K | 75.3 | **21.5** | 92 |
| 42 | 6 | 160K | CRASH | - | - |
| **40** | **8 (~7.7 GB)** | **262K** | 63.7 | **20.5** | 380 |

#### MoE Offloading (`-cmoe`): Experts on CPU, attention on GPU

| Mode | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 used (MiB) |
|---|---|---|---|---|
| `-ngl 99 -cmoe` | 4K | 3.2 | **6.8** | 1,539 |
| `-ngl 99 -cmoe` | 262K | 3.4 | **6.4** | 3,143 |
| `-ngl 99 -ncmoe 40` | 4K | 4.4 | **9.0** | 1,539/8,797 |

**Results and recommendation:**

- **`-ngl 40` is the sweet spot:** Full native context (262K) with only 15% gen speed loss (20.5 vs 24.1 tok/s). ~7.7 GB on CPU fits easily in 30 GB RAM.
- **`-ngl 44`**: Maximum speed (24 tok/s), but only 57K context — insufficient for long reasoning.
- **`-cmoe`**: 262K context, but **3.5× slower** (6.4 tok/s). Every token must load 10 experts over PCIe 3.0. Only useful when speed does not matter.
- **Gen speed is context-independent**: ~20-24 tok/s whether at 4K or 262K.
- `-ngl 99` crashes (47 GB model does not fit in 48 GB VRAM)
- MoE weights are loaded via mmap — appear as "buffer/cache" in RAM, not "used"
- KV cache lives primarily in VRAM (proportional to GPU layers), not CPU RAM

### Key Findings

1. **Flash Attention on Pascal (P40) is NOT slower across the board.** For Qwen3 models PP is up to 134% faster. The known FA penalty affects mainly GLM-4.7-FLASH (Issue #19020). Root cause: head dimension (see below).

2. **Compute buffer is the largest VRAM consumer, not the KV cache.** FA reduces the compute buffer by 86% (2,142 → 302 MiB for the 4B model).

3. **Q8 KV quantization additionally accelerates PP** by reducing memory bandwidth in the attention computation.

4. **Gen speed is context-independent.** Whether at 32K or 262K: ~46-47 tok/s for 30B, ~62-63 tok/s for 4B, ~20-24 tok/s for 80B.

5. **Mandatory parameters for P40:** `-fit off -np 1 --flash-attn on -ctk q8_0 -ctv q8_0`
   - `-fit on` crashes with CUDA OOM on Pascal
   - `-np auto` sets 4 slots = unnecessary VRAM usage
   - FA + Q8 KV saves VRAM and is faster

6. **Layer offloading vs MoE offloading for oversized models:**
   - `-ngl <N>`: Whole layers on CPU. Speed drops moderately (~1 tok/s per 2 layers).
   - `-cmoe`: Only MoE experts on CPU. Speed drops to ~30% (PCIe bottleneck).
   - **Recommendation:** Prefer layer offloading unless maximum context matters more than speed.

7. **`-ub` (micro-batch) reduces compute buffer without gen speed loss.**
   `-ub 256` instead of `512` halves the compute buffer (196 vs 392 MiB for 30B).
   PP speed drops minimally (117 vs 120 tok/s). Gen speed identical.

### Why FA on P40 is model-dependent (technical)

Source: `ggml/src/ggml-cuda/fattn.cu` and `fattn-tile.cuh` in llama.cpp.

The P40 (CC 6.1) has **no Tensor Cores** and **no fast FP16** (`FAST_FP16_AVAILABLE` is
explicitly excluded for CC 6.1 in `common.cuh:230`). Therefore:

1. **Kernel selection:** P40 uses the generic `tile` kernel in FP32 mode. Turing+ GPUs use MMA kernels (Tensor Cores).

2. **Vec kernel (for token generation):** Only available when `dkq <= 256 && dkq % 64 == 0`. Qwen3 (dkq=128) qualifies, GLM (dkq=576) does not.

3. **FP32 compute scales with head dimension:**
   - Qwen3 (dkq=128): 128 FP32-MADs per dot product, tile config: `nbatch_fa=64, occupancy=3`
   - GLM (dkq=576): 576 FP32-MADs (4.5× more), tile config: `nbatch_fa=32, occupancy=2`

4. **Standard attention (no FA) uses cuBLAS GEMM**, which is highly optimized for FP32 on P40. For large dimensions (576) cuBLAS has better throughput than the fused FA kernel.

**Rule of thumb:** Models with `attention.key_length <= 256` benefit from FA on P40. Models with larger head dimensions (DeepSeek/MLA architecture) may be slower.

---

## Heterogeneous Multi-GPU (P40 + RTX 8000)

### What works

- **Layer split (`-sm layer`)** over PCIe x4 eGPU: Only ~8 KB per layer boundary, no bandwidth issue
- **Unequal distribution (`-ts`)**: More layers on the faster GPU
- **`-mg` on RTX 8000**: Output layer and scratch buffers on the faster GPU

### What does NOT work

- **Row split (`-sm row`)**: Constant All-Reduce synchronization, impractical over PCIe x4
- **Graph split (`-sm graph`, ik_llama.cpp)**: Assumes equally powerful GPUs, no auto-balancing
- **KV cache separation**: KV cache is architecturally bound to its layer GPU
- **Flash Attention per GPU**: Global switch, not controllable per GPU

### Flash Attention on P40

Older assumption: FA is universally slower on Pascal. **Refuted by benchmarks (2026-02-17):**
FA is **faster** for Qwen3 models on P40 (PP: +134% for 4B, +30% for 30B).
The known FA penalty mainly affects GLM-4.7-FLASH (see benchmark section).

**Recommendation:** Always enable FA (`--flash-attn on`).
KV cache quantization (`-ctk q8_0 -ctv q8_0`) saves an additional ~50% KV VRAM.

### Benchmark: Tensor Split Optimization (2026-02-18)

> **Real-world benchmark:** See [tensor-split.md](../benchmarks/tensor-split.md) for a
> full AIfred multi-agent tribunal comparison between aggressive (11:1) and balanced (2:1) split
> on Qwen3-Next-80B-A3B-Instruct.

**Hardware:** RTX 8000 (CUDA0, 46 GB) + Tesla P40 (CUDA1, 24 GB), `CUDA_DEVICE_ORDER=FASTEST_FIRST`
**Model:** Qwen3-32B Q4_K_M (18.8 GB)
**Tool:** `llama-bench` (llama.cpp v8076)
**Parameters:** `-ngl 99 -np 1 -fit off --flash-attn on -ctk q8_0 -ctv q8_0 -sm layer -t 4 -b 2048 -ub 512`

| Config | Tensor split (RTX8000:P40) | PP (tok/s) | TG (tok/s) | Notes |
|--------|---------------------------|-----------|-----------|-------|
| A | RTX 8000 alone | 631 | **22.19** | Baseline (single GPU) |
| B | P40 alone | 214 | **9.87** | For comparison |
| C | 1:1 | 326 | **13.42** | Equal split |
| D | 2:1 (previous default) | 394 | **15.59** | Was standard in llama-swap |
| E | 5:1 | 497 | **18.15** | Noticeably faster |
| F | 10:1 | 565 | **19.68** | Optimum for 32B |

**Findings:**

- **10:1 split** is **26% faster** than 2:1 at TG (19.68 vs 15.59 tok/s)
- Every layer on the P40 is a bottleneck due to lower bandwidth (346 GB/s vs 672 GB/s RTX 8000)
- PP optimum also approaches the single-GPU value (565 vs 631)
- **Rule of thumb:** Use as little P40 as possible (VRAM overflow only), maximize RTX 8000

**Recommendations by model size and context:**

| Model | Model VRAM | Context | KV type | Recommended split (RTX:P40) | Total VRAM needed |
|--------|------------|---------|---------|----------------------------|-------------------|
| 4B–14B Q4_K_M | < 10 GB | 131K | q8_0 | `-dev CUDA0` | < 20 GB |
| 32B Q4_K_M | 18.8 GB | 16K | q8_0 | `-dev CUDA0` | ~22 GB |
| 30B A3B Q8_0 | ~31 GB | 238K | q8_0 | `-ts 2,1` | ~50–55 GB |
| 80B Q4_K_M | 46.6 GB | 262K | q4_0 | `-ts 2,1` | ~65 GB |
| 120B Q8_0 | ~59 GB | 131K | q4_0 | `-ts 2,1` | ~65–70 GB |

**Why context determines the split:**

The KV cache is distributed across GPUs in the same ratio as the model (`-sm layer`).
With a 10:1 split, 91% of the KV cache sits on the RTX 8000. If the model already
occupies most of the RTX VRAM, there is not enough room for the KV cache of large contexts.

- **Model fits on RTX alone (< 46 GB):** High split is possible → minimize P40 as a speed bottleneck
- **Model does NOT fit on RTX alone (≥ 46 GB):** Split ≈ VRAM ratio (46:24 ≈ 2:1) is optimal,
  so the KV cache is distributed evenly across the free VRAM of both GPUs
- **2:1 as a rule of thumb:** For large models (80B+) and large contexts, stay close to the VRAM ratio

**Note on CUDA_DEVICE_ORDER:** The llama-swap service runs with `CUDA_DEVICE_ORDER=FASTEST_FIRST`,
so CUDA0 = RTX 8000 and CUDA1 = P40. Tensor split values refer to CUDA0:CUDA1.

### Tensor Split Recommendations

```bash
# Note: With CUDA_DEVICE_ORDER=FASTEST_FIRST: CUDA0=RTX 8000, CUDA1=P40

# 2x P40 (equal): distribute evenly
-sm layer --tensor-split 1,1

# RTX 8000 (CUDA0) + P40 (CUDA1): minimize P40 share (VRAM extension only)
# For models that need BOTH GPUs (> 46 GB)
-sm layer -ts 10,1   # 30B A3B Q8_0: good
-sm layer -ts 24,1   # 80B Q4_K_M: VRAM-limited

# Single GPU (model fits on RTX 8000)
-dev CUDA0           # Up to 32B Q4_K_M: no split, full RTX 8000 speed

# 2x RTX 8000 (equal): distribute evenly
-sm layer -ts 1,1
```

---

## GPU-Management: Was automatisch geht, was manuell muss

> **Note:** The following section is currently only available in German.

### Übersicht

| Szenario | Autoscan | Kalibrierung | Manuell? |
|----------|----------|--------------|----------|
| Neue GGUF-Datei hinzufuegen | Erkennt automatisch, erstellt Profil mit korrektem Tensor-Split | Context + Speed-Split per Kalibrierung | Nein |
| Neue lokale GPU einstecken | **Alle Profile** werden automatisch angepasst (Fingerprint-Erkennung) | Kalibrierung empfohlen fuer Context-Optimierung | Nein |
| GPU entfernen | **Alle Profile** werden automatisch angepasst (Fingerprint-Erkennung) | Kalibrierung empfohlen fuer Context-Optimierung | Nein |
| RPC-Worker hinzufuegen/entfernen | Nicht erkannt (RPC-Profile bleiben unangetastet) | Nicht unterstuetzt | **Ja** — `--rpc` muss manuell gesetzt werden |

### GPU-Hardware-Fingerprint (automatische Erkennung)

Der Autoscan speichert einen Hardware-Fingerprint in der ersten Zeile der llama-swap Config:

```yaml
# gpu_hardware: RTX_8000:48564,P40:24576
healthCheckTimeout: 600
models:
  ...
```

Bei jedem llama-swap Neustart vergleicht der Autoscan die aktuelle Hardware mit dem
gespeicherten Fingerprint (±512 MB Toleranz fuer Treiber-Varianz). Bei einer Aenderung:

1. **Alle lokalen Profile** (manuell UND `[autoscan]`) bekommen einen neuen Tensor-Split
2. RPC-Profile (`--rpc` im cmd) bleiben unangetastet
3. Context (`-c`) und NGL (`-ngl`) werden **nicht** geaendert — dafuer ist die Kalibrierung da
4. Hinweis: "Run 'Context kalibrieren' in AIfred to optimize context sizes"

**Beispielausgabe bei GPU-Aenderung:**
```
⚠️  GPU HARDWARE CHANGED!
   Stored:  RTX_8000:48564,P40:24576
   Current: RTX_8000:48564,P40:24576,RTX_3060:12288
   Updated tensor-split in 8 model(s)
   → Run 'Context kalibrieren' in AIfred to optimize context sizes
```

### Tensor-Split-Berechnung

Der Autoscan berechnet den Tensor-Split proportional zum VRAM:

```python
# Beispiel: 3 GPUs
per_gpu_vram = [48000, 24000, 12000]  # RTX 8000, P40, RTX 3060
min_vram = 12000
split_parts = [4, 2, 1]              # proportional zum VRAM
# → "--tensor-split 4,2,1"
```

**Regeln:**
- Modell passt auf groesste GPU allein → kein Tensor-Split, nur `-dev CUDA0`
- Modell braucht Multi-GPU → `--tensor-split X,Y[,Z...]` proportional zum VRAM
- Bei GPU-Entfernung: Split wird reduziert oder entfernt (sofern Modell auf verbleibende GPUs passt)

### Was die Kalibrierung (AIfred UI) zusaetzlich macht

"Context kalibrieren" in der AIfred UI fuehrt fuer das gewaehlte Modell durch:

1. **Phase 1: GPU-only Context** — Binary Search fuer maximalen Context bei `-ngl 99`
   - KV-Fallback-Chain: f16 → q8_0 (wenn < nativer Kontext) → q4_0 (letzter Ausweg, nur wenn q8_0 < 32K)
   - VRAM-Balance: Erkennt Asymmetrie zwischen GPUs, verschiebt Layer (±1 pro Durchlauf)
2. **Phase 2: Speed-Variante** — Min-GPU-Strategie: Berechnet minimale GPU-Anzahl fuer Modell-Gewichte
   - Weniger GPU-Grenzen = weniger Transfer-Overhead = schnellere Inferenz (Tradeoff: reduzierter max. Kontext)
   - Phase A: Binary Search fuer max. Layer auf schnellster GPU bei 32K Kontext (f16 KV)
   - Phase B: Kontext-Maximierung mit eigener KV-Chain (f16 → q8_0 falls f16 < 32K)
   - Erstellt separaten `modell-speed`-Eintrag in llama-swap YAML mit eigenem KV-Quant
3. **Phase 3: Hybrid NGL** — Falls GPU-only < 32K: Sucht optimalen `-ngl` mit CPU-Offload
   - Erbt KV-Quantisierung von Phase 1 (keine eigene KV-Chain)

Die Kalibrierung verfeinert den groben VRAM-proportionalen Split des Autoscans
durch tatsaechliche Performance-Messungen.

#### KV-Quantisierung: Entscheidungslogik

| Phase | KV-Chain | Schwellenwert | Kommentar |
|-------|----------|---------------|-----------|
| Phase 1 (Base) | f16 → q8_0 → q4_0 | `MIN_USEFUL_CONTEXT_TOKENS` (32K) | q8_0 wenn f16 < native, q4_0 nur wenn q8_0 < 32K |
| Phase 2 (Speed) | f16 → q8_0 | `MIN_USEFUL_CONTEXT_TOKENS` (32K) | Unabhaengig von Phase 1, eigenes KV im YAML |
| Phase 3 (Hybrid) | Erbt von Phase 1 | — | Kein eigenes KV, maximiert GPU-Layer |

**Hinweis:** f16 KV ist auf Hardware ohne Tensor Cores (z.B. Tesla P40) schneller als quantisiertes KV,
da keine Dequantisierung noetig ist. Daher startet jede KV-Chain mit f16.

### Handlungsanleitung: Neue lokale GPU hinzufuegen

1. **Physisch installieren**, Treiber pruefen: `nvidia-smi` muss alle GPUs zeigen
2. **llama-swap neustarten**: `sudo systemctl restart llama-swap`
   - Autoscan erkennt die neue GPU via Fingerprint-Vergleich
   - **Alle Profile** (manuell + autoscan) bekommen automatisch den neuen Tensor-Split
3. **Kalibrierung ausfuehren**: In AIfred UI "Context kalibrieren" fuer wichtige Modelle
   (Context und Speed-Split werden an die neue Hardware-Konfiguration angepasst)
4. **Performance pruefen**: tok/s kontrollieren, bei Bedarf Split-Ratios nachoptimieren

### Handlungsanleitung: RPC-Worker (Remote-GPU) hinzufuegen/entfernen

RPC wird vom Autoscan **nicht** unterstuetzt — vollstaendig manuell.

1. **Worker einrichten**: `rpc-server -H 0.0.0.0 -p 50052` auf dem Remote-Rechner
2. **Konnektivitaet testen**: `bash -c 'echo > /dev/tcp/<IP>:<PORT>' && echo OK`
3. **Neues Profil in llama-swap config** (oder bestehendes anpassen):
   ```yaml
   Modell-rpc:
     cmd: 'llama-server --model <path> -ngl 99 --rpc <IP>:<PORT> ...'
     ttl: 3600
     healthCheckTimeout: 900  # RPC-Modelle brauchen laenger zum Laden
   ```
4. **AIfred RPC Quick-Check**: Prueft vor jeder Inferenz per TCP-Connect (3s Timeout)
   ob der Worker erreichbar ist. Fehlermeldung sofort statt 15 Min warten.
5. **Kein automatischer Tensor-Split**: llama.cpp verteilt bei RPC automatisch nach VRAM,
   aber fuer optimale Performance ggf. manuellen `--tensor-split` setzen
6. **Separate Profile**: Lokales Profil (CPU-Offload, schnelles Laden) UND RPC-Profil
   (alle GPU, langsames Laden) als getrennte Eintraege — User waehlt in AIfred

### Warum RPC nicht automatisiert werden kann

- RPC-Endpoints sind Netzwerk-Ressourcen — kein lokaler Discovery-Mechanismus
- Worker kann ein-/ausgeschaltet sein → dynamisch, nicht statisch konfigurierbar
- Optimale Verteilung haengt von Netzwerklatenz ab (nicht nur VRAM)
- `healthCheckTimeout` und `ttl` muessen an Ladezeit angepasst werden

---

## llama-swap Configuration

### Installation

```bash
# llama-swap binary (Go, single file)
# Download from: https://github.com/mostlygeek/llama-swap/releases

# llama-server (part of llama.cpp)
# Build with CUDA support:
cd llama.cpp && cmake -B build -DGGML_CUDA=ON && cmake --build build -j
```

### Example Config: P40 + RTX 8000

```yaml
# llama-swap.yaml

groups:
  main:  # Large models, both GPUs, only 1 active at a time
    - qwen3-30b
    - llama3-70b

models:
  # --- Main models (group "main") ---
  qwen3-30b:
    cmd: >
      llama-server -m /models/Qwen3-30B-A3B-Thinking-2507-Q4_K_M.gguf
      -ngl 99 -sm layer --tensor-split 1,2.5 --main-gpu 1
      -c 16384 --flash-attn on -ctk q8_0 -ctv q8_0
      -b 2048 -ub 512 --mlock
      --port ${PORT}
    ttl: 300  # 5 min inactivity → unload

  llama3-70b:
    cmd: >
      llama-server -m /models/llama3-70b-Q4_K_M.gguf
      -ngl 99 -sm layer --tensor-split 1,2.5 --main-gpu 1
      -c 8192 --flash-attn on -ctk q8_0 -ctv q8_0
      --mlock --port ${PORT}
    ttl: 300

  # --- Small models (RTX 8000 only) ---
  qwen3-8b:
    cmd: >
      llama-server -m /models/Qwen3-8B-Q4_K_M.gguf
      -ngl 99 -dev CUDA1
      -c 32768 --flash-attn on -ctk q8_0 -ctv q8_0
      --port ${PORT}
    # No TTL = stays loaded

  # --- Embedding (CPU-only) ---
  nomic-embed:
    cmd: >
      llama-server -m /models/nomic-embed-text.gguf
      -ngl 0 --embedding -c 8192
      --port ${PORT}
```

### Example Config: 4x RTX 8000 (Full build-out)

```yaml
groups:
  main:  # Huge models, all 4 GPUs
    - qwen3-235b
    - llama3-405b

models:
  qwen3-235b:
    cmd: >
      llama-server -m /models/Qwen3-235B-A22B-Q4_K_M.gguf
      -ngl 99 -sm layer --tensor-split 1,1,1,1
      -c 16384 --flash-attn on -ctk q8_0 -ctv q8_0
      --mlock --port ${PORT}
    ttl: 600

  # Alternative: vLLM for MoE models (pipeline parallel)
  qwen3-235b-vllm:
    cmd: >
      vllm serve Qwen/Qwen3-235B-A22B-AWQ
      --pipeline-parallel-size 4
      --max-model-len 16384
      --port ${PORT}
    ttl: 600
```

### Starting llama-swap

```bash
# llama-swap on port 11435 (next to Ollama's 11434)
CUDA_DEVICE_ORDER=FASTEST_FIRST GGML_CUDA_GRAPH_OPT=1 \
  ~/bin/llama-swap --config ~/.config/llama-swap/config.yaml --listen :11435 --watch-config
```

### Systemd Service

```ini
# /etc/systemd/system/llama-swap.service  (system-level service)
[Unit]
Description=llama-swap - LLM Model Proxy for llama.cpp
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mp
Group=mp
ExecStartPre=/path/to/venv/bin/python /path/to/scripts/llama-swap-autoscan.py
ExecStart=/home/mp/bin/llama-swap --config /home/mp/.config/llama-swap/config.yaml --listen :11435 --watch-config
Restart=on-failure
RestartSec=5
Environment=PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
Environment=LD_LIBRARY_PATH=/usr/local/cuda/lib64
Environment=CUDA_DEVICE_ORDER=FASTEST_FIRST
Environment=GGML_CUDA_GRAPH_OPT=1

[Install]
WantedBy=multi-user.target
```

```bash
# Service management (system-level → sudo)
sudo systemctl enable llama-swap
sudo systemctl start llama-swap
sudo systemctl status llama-swap
sudo journalctl -u llama-swap -f
```

### API Endpoints

| Endpoint | Description |
|---|---|
| `GET /v1/models` | All configured models |
| `POST /v1/chat/completions` | Chat (OpenAI-compatible) |
| `GET /running` | Currently loaded models |
| `POST /models/unload` | Unload a model manually |
| `GET /health` | Health check |
| `GET /ui` | Web UI for monitoring |

---

## Automatic Model Discovery (Autoscan)

The script `scripts/llama-swap-autoscan.py` automatically detects new GGUF models
and configures them for llama-swap. It runs as `ExecStartPre` before every llama-swap start.

### What the script does

0. **GPU hardware fingerprint** — compares current GPUs against stored fingerprint in config. If hardware changed (GPU added/removed), updates tensor-split in ALL local profiles automatically (see [GPU-Management](#gpu-management-was-automatisch-geht-was-manuell-muss))
1. **Scan Ollama manifests** — reads Ollama manifests from system and user paths, finds GGUF blobs, creates symlinks with descriptive filenames in `~/models/`
   - Example: `sha256-6335adf...` → `Qwen3-14B-Q8_0.gguf`
   - Deduplication: multiple tags pointing to the same blob → longest/most descriptive name wins
   - Embedding models (BERT, nomic, etc.) are skipped
2. **Scan HuggingFace cache** — finds GGUFs in `~/.cache/huggingface/hub/` (active snapshot only), creates symlinks in `~/models/`
3. **Detect new GGUFs** — compares `~/models/*.gguf` against existing entries in the llama-swap config (and the skip list)
4. **Compatibility test** — each new model is briefly started with llama-server (max 6 seconds):
   - Process exits within 6s with an error → incompatible architecture detected (e.g. `deepseekocr`) → model is **not** added to the config
   - Process still running after 6s → server is up, architecture OK → model is added normally
   - Incompatible models are saved to `autoscan-skip.json` and **not re-tested** on subsequent starts
5. **Extend llama-swap config** — for each compatible new model, a YAML block is appended with default parameters (`-ngl 99`, `--flash-attn on`, `-ctk q8_0 -ctv q8_0`, etc.)
   - VL models (with a matching `mmproj-*.gguf`) automatically get a `--mmproj` argument
   - The config file is created from scratch if it does not exist yet
6. **Update groups** — the `groups.main.members` list is rewritten to include all configured models (excluding `-speed` variants). This ensures llama-swap enforces VRAM exclusivity between models.
7. **Prepare VRAM cache** — minimal entries in `data/model_vram_cache.json` (calibration is done later via the AIfred UI)

### Run manually

```bash
python scripts/llama-swap-autoscan.py
```

### Typical output

```
=== llama-swap Autoscan ===

Scanning Ollama models...
  + Symlink: Qwen3-14B-Q8_0.gguf → sha256-6335adf...
  = Exists:  Qwen3-8B-Q4_K_M.gguf
  ~ Skip:    nomic-embed-text-v2-moe (embedding model)
  3 Ollama models found, 1 new symlinks created

Scanning HuggingFace cache...
  No HuggingFace cache found or empty.

Scanning ~/models/ for GGUFs...
  1 model(s) skipped (known incompatible, remove from autoscan-skip.json to re-test):
    ~ Deepseek-OCR-3B-F16: unsupported architecture 'deepseekocr'
  Found 7 GGUFs, 1 new

Testing new models for llama-server compatibility...
  ✓ Qwen3-14B-Q8_0

Updating llama-swap config...
  + Added: Qwen3-14B-Q8_0 (native context: 40960)

Updating VRAM cache...
  + Added: Qwen3-14B-Q8_0

Done. 1 model(s) added to config, 1 VRAM cache entries created.
Groups updated: main → [Qwen3-14B-Q8_0, Qwen3-8B-Q4_K_M]
```

### Configuration constants

| Constant | Default | Description |
|---|---|---|
| `MODELS_DIR` | `~/models/` | Directory for GGUF files and symlinks |
| `OLLAMA_PATHS` | System + User | Ollama model directories |
| `HF_CACHE_DIR` | `~/.cache/huggingface/hub` | HuggingFace cache root |
| `LLAMASWAP_CONFIG` | `~/.config/llama-swap/config.yaml` | llama-swap config file |
| `LLAMA_SERVER_BIN` | `~/llama.cpp/build/bin/llama-server` | Path to llama-server binary |
| `DEFAULT_TTL` | 300 | Inactivity timeout in seconds |
| `DEFAULT_FLAGS` | `--flash-attn on -ctk q8_0 -ctv q8_0 -np 1 -t 4 --mlock` | Default llama-server flags |
| `AUTOSCAN_SKIP_FILE` | `~/.config/llama-swap/autoscan-skip.json` | Persistent list of incompatible models |
| `COMPAT_TEST_TIMEOUT` | 6 | Seconds to wait during compatibility test |

### Managing the skip list

Incompatible models are stored in `~/.config/llama-swap/autoscan-skip.json`:

```json
{
  "Deepseek-OCR-3B-F16": "unsupported architecture 'deepseekocr'",
  "Qwen3-VL-8B-Q4_K_M": "missing GGUF metadata key 'qwen3vl.rope.dimension_sections' — Ollama blob missing llama.cpp metadata; download official GGUF from HuggingFace"
}
```

After a llama.cpp update that adds the missing architecture: delete the entry from the file.
On the next llama-swap start, the model will be tested again and automatically added if compatible.

---

## AIfred Integration

llama-swap is OpenAI-compatible. In AIfred it is registered as a dedicated backend:

- Backend type: `llamacpp`
- URL: `LLAMACPP_URL` env var or `http://localhost:11435/v1`
- API key: dummy (local service)
- Model name in AIfred = model key in the llama-swap config
- Config path: `~/.config/llama-swap/config.yaml` (XDG standard)

---

## Sources

- [llama.cpp GitHub](https://github.com/ggml-org/llama.cpp)
- [llama-swap GitHub](https://github.com/mostlygeek/llama-swap)
- [ik_llama.cpp (Graph Parallel)](https://github.com/ikawrakow/ik_llama.cpp)
- [NVIDIA CUDA Graphs Blog](https://developer.nvidia.com/blog/optimizing-llama-cpp-ai-inference-with-cuda-graphs/)
- [NVIDIA RTX LLM Acceleration](https://developer.nvidia.com/blog/open-source-ai-tool-upgrades-speed-up-llm-and-diffusion-models-on-nvidia-rtx-pcs)
- [LocalScore.ai Benchmarks](https://www.localscore.ai)
- [llama.cpp Multi-GPU Discussion](https://github.com/ggml-org/llama.cpp/discussions/15013)
- [eGPU LLM Performance Impact](https://egpu.io/forums/pro-applications/impact-of-egpu-connection-speed-on-local-llm-inference-in-multi-egpu-setups/)
- [FA on Pascal: Issue #19020](https://github.com/ggml-org/llama.cpp/issues/19020) — FA is model-dependent, faster for Qwen3
- [FA Pascal Implementation: PR #7188](https://github.com/ggerganov/llama.cpp/pull/7188) — FA without Tensor Cores
- [Ollama KV Quant](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/) — Q8/Q4 KV in Ollama
- [FA + P40 Gibberish Fix: Issue #7400](https://github.com/ggml-org/llama.cpp/issues/7400) — MoE+FA bug, fixed
