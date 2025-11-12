# GPU Compatibility Guide

**AIfred Intelligence - Backend & GPU Compatibility Matrix**

This guide helps you choose the right backend and models for your GPU.

---

## Quick Reference

| Your GPU | Recommended Backend | Supported Backends | Performance |
|----------|-------------------|-------------------|-------------|
| **RTX 40 Series** (4090, 4080, etc.) | vLLM (AWQ) | Ollama, vLLM, TabbyAPI | ⭐⭐⭐⭐⭐ Excellent |
| **RTX 30 Series** (3090, 3080, etc.) | vLLM (AWQ) | Ollama, vLLM, TabbyAPI | ⭐⭐⭐⭐ Very Good |
| **RTX 20 Series** (2080 Ti, etc.) | vLLM (AWQ) | Ollama, vLLM, TabbyAPI | ⭐⭐⭐ Good |
| **A100 / H100** | vLLM (AWQ) | Ollama, vLLM, TabbyAPI | ⭐⭐⭐⭐⭐ Excellent |
| **Tesla P40 / P4** | **Ollama (GGUF)** | Ollama only | ⭐⭐ Fair (Pascal limitations) |
| **Tesla P100** | Ollama (GGUF) | Ollama, vLLM (slow) | ⭐⭐ Fair |
| **GTX 10 Series** (1080 Ti, etc.) | Ollama (GGUF) | Ollama only | ⭐⭐ Fair |

---

## Backend Comparison

### 1. **Ollama (GGUF Format)**
**Best for: Universal compatibility, older GPUs**

#### Requirements:
- ✅ No minimum compute capability
- ✅ Works on any NVIDIA GPU (even Kepler 3.5+)
- ✅ Uses INT8/Q4/Q8 quantization (no FP16 needed)

#### Advantages:
- 🟢 Works on **all** GPUs including Pascal (P40, GTX 10 series)
- 🟢 Simple setup, no special configuration
- 🟢 Good performance on older hardware
- 🟢 Can run multiple models simultaneously

#### Disadvantages:
- 🔴 Slower than vLLM on modern GPUs (Ampere+)
- 🔴 Less optimized for batch inference

#### Recommended Models:
```bash
ollama pull qwen3:30b-instruct  # Main LLM (256K context)
ollama pull qwen3:8b            # Automatik LLM (optional thinking)
ollama pull qwen2.5:3b          # Ultra-fast decisions
```

---

### 2. **vLLM (AWQ/GPTQ Format)**
**Best for: Modern GPUs (RTX 20+, A100, H100)**

#### Requirements:
- ⚠️ **Minimum: Compute Capability 7.5** (Turing = RTX 20 series)
- ⚠️ Requires fast FP16 performance
- ⚠️ Triton compiler support needed

#### Advantages:
- 🟢 **Fastest inference** on modern GPUs
- 🟢 Excellent batch processing
- 🟢 AWQ Marlin kernel optimization
- 🟢 YaRN context extension support (32K→64K→128K)

#### Disadvantages:
- 🔴 **Not compatible** with Pascal GPUs (P40, GTX 10 series)
- 🔴 Can only load **one model at a time**
- 🔴 More complex setup

#### Recommended Models:
```bash
# Qwen3 AWQ (40K native, YaRN→128K)
./download_vllm_models.sh
# Models: Qwen3-8B-AWQ, Qwen3-14B-AWQ, Qwen3-32B-AWQ

# Qwen2.5 Instruct-AWQ (128K native)
# Models: Qwen2.5-14B-Instruct-AWQ, Qwen2.5-32B-Instruct-AWQ
```

---

### 3. **TabbyAPI (EXL2 Format)**
**Best for: ExLlamaV2/V3 enthusiasts**

#### Requirements:
- ⚠️ **Minimum: Compute Capability 7.0** (Volta = V100, RTX 20+)
- ⚠️ Requires fast FP16 performance
- ⚠️ Similar to vLLM requirements

#### Advantages:
- 🟢 ExLlamaV2/V3 optimizations
- 🟢 Flexible quantization (2-8 bit EXL2)
- 🟢 Good for experimentation

#### Disadvantages:
- 🔴 **Not compatible** with Pascal GPUs
- 🔴 Less mature than Ollama/vLLM
- 🔴 Slower FP16 performance hits Pascal hard

---

## Detailed GPU Information

### Modern GPUs (Ampere / Ada / Hopper)

| GPU | Compute Cap | VRAM | FP16 Performance | Best Backend |
|-----|-------------|------|------------------|--------------|
| RTX 4090 | 8.9 | 24GB | Excellent | vLLM (AWQ) |
| RTX 4080 | 8.9 | 16GB | Excellent | vLLM (AWQ) |
| RTX 4070 Ti | 8.9 | 12GB | Excellent | vLLM (AWQ) |
| RTX 3090 | 8.6 | 24GB | Excellent | vLLM (AWQ) |
| RTX 3080 | 8.6 | 10GB/12GB | Excellent | vLLM (AWQ) |
| A100 | 8.0 | 40GB/80GB | Excellent | vLLM (AWQ) |
| A40 | 8.6 | 48GB | Excellent | vLLM (AWQ) |
| H100 | 9.0 | 80GB | Excellent | vLLM (AWQ) |

### Turing Generation

| GPU | Compute Cap | VRAM | FP16 Performance | Best Backend |
|-----|-------------|------|------------------|--------------|
| RTX 2080 Ti | 7.5 | 11GB | Good | vLLM (AWQ) |
| RTX 2080 | 7.5 | 8GB | Good | vLLM (AWQ) |
| RTX 2070 | 7.5 | 8GB | Good | vLLM (AWQ) |
| Tesla T4 | 7.5 | 16GB | Good | vLLM (AWQ) |

### Pascal Generation (❌ vLLM Incompatible)

| GPU | Compute Cap | VRAM | FP16 Performance | Best Backend |
|-----|-------------|------|------------------|--------------|
| **Tesla P40** | 6.1 | 24GB | **Very Poor (1:64 ratio)** | **Ollama (GGUF)** |
| Tesla P100 | 6.0 | 16GB | Moderate (1:2 ratio) | Ollama (GGUF) |
| Tesla P4 | 6.1 | 8GB | Very Poor (1:64 ratio) | Ollama (GGUF) |
| GTX 1080 Ti | 6.1 | 11GB | Poor | Ollama (GGUF) |
| GTX 1080 | 6.1 | 8GB | Poor | Ollama (GGUF) |
| GTX 1070 | 6.1 | 8GB | Poor | Ollama (GGUF) |

---

## Why Pascal GPUs Don't Work with vLLM/AWQ

### Technical Explanation:

1. **Compute Capability Too Low**
   - vLLM requires: Compute Capability 7.0+ (Volta)
   - Triton compiler requires: 7.0+
   - Pascal has: 6.1 (P40, GTX 10 series)

2. **FP16 Performance Bottleneck**
   - Modern inference engines (vLLM, ExLlama) use FP16 internally
   - **Tesla P40**: FP16:FP32 ratio = **1:64** (extremely slow!)
   - **Tesla P100**: FP16:FP32 ratio = 1:2 (moderate)
   - Ampere+ GPUs: FP16:FP32 ratio = 1:1 or better (fast)

3. **Real-World Performance**
   ```
   Tesla P40 + ExLlamaV2/GPTQ:
   - 34B model: ~1.19 tok/s (unusable)
   - 7B model: ~5-10 tok/s (very slow)
   - GPU utilization: Only 80W (underutilized)

   Tesla P40 + Ollama GGUF:
   - 14B model: ~17 tok/s (acceptable)
   - 7B model: ~25 tok/s (good)
   - GPU utilization: Full
   ```

4. **AWQ Marlin Kernel Requirements**
   - AWQ Marlin requires: Compute Capability 8.0+ (Ampere)
   - Provides significant speedup on Ampere/Ada
   - Not available on Pascal/Turing

---

## Model Format Comparison

| Format | Quantization | Compute Cap | Best For | Speed on Pascal |
|--------|-------------|-------------|----------|-----------------|
| **GGUF** | INT8/Q4/Q8 | Any (3.5+) | Universal compatibility | ⭐⭐⭐ Good |
| **AWQ** | INT4 (FP16 internal) | 7.5+ | Modern GPUs | ❌ Incompatible |
| **GPTQ** | INT4 (FP16 internal) | 7.0+ | Turing+ | ⭐ Very Slow |
| **EXL2** | 2-8 bit (FP16 internal) | 7.0+ | ExLlama users | ⭐ Very Slow |

---

## Recommendations by Use Case

### 🏠 Home Lab / Older Hardware (Pascal, GTX 10 series)
```bash
Backend: Ollama
Models: GGUF (Q4/Q8)
Script: ./download_ollama_models.sh

Recommended:
- qwen3:30b-instruct (18GB)
- qwen3:8b (5.2GB)
- qwen2.5:3b (1.9GB)
```

### 🏢 Datacenter / Modern Hardware (RTX 30/40, A100)
```bash
Backend: vLLM
Models: AWQ quantization
Script: ./download_vllm_models.sh

Recommended:
- Qwen3-14B-AWQ (~8GB, 32K→128K with YaRN)
- Qwen3-32B-AWQ (~18GB, 32K→128K with YaRN)
- Qwen2.5-32B-Instruct-AWQ (~18GB, 128K native)
```

### ⚡ Maximum Performance (A100, H100)
```bash
Backend: vLLM
Models: AWQ with YaRN
Script: ./download_vllm_models.sh

Recommended:
- Qwen3-32B-AWQ with YaRN factor=4.0 (128K context)
- Batch processing with vLLM
- Use Marlin kernel optimization
```

---

## Troubleshooting

### ❌ "vLLM not supported on this GPU"
**Cause**: Your GPU has Compute Capability < 7.0 (Pascal or older)
**Solution**: Use Ollama with GGUF models instead
```bash
./download_ollama_models.sh
```

### ❌ "Very slow inference with vLLM/ExLlama"
**Cause**: Pascal GPU with poor FP16 performance
**Solution**: Switch to Ollama (GGUF)
```bash
# In AIfred UI: Settings → Backend → Select "ollama"
```

### ⚠️ "AWQ models fail to load"
**Cause 1**: Compute Capability < 7.5
**Cause 2**: Missing AWQ Marlin support
**Solution**: Use GGUF or switch GPU

---

## Performance Benchmarks

### Tesla P40 (Pascal 6.1)
```
Ollama GGUF Q4:
- qwen3:14b → ~17 tok/s ✅ Acceptable
- qwen3:30b → ~8 tok/s ✅ Usable

vLLM AWQ (if forced):
- Cannot run (Triton compiler error) ❌

ExLlamaV2 EXL2:
- 14B model → ~1-5 tok/s ❌ Too slow
```

### RTX 4090 (Ada 8.9)
```
vLLM AWQ + Marlin:
- Qwen3-14B-AWQ → ~120 tok/s ⭐⭐⭐⭐⭐
- Qwen3-32B-AWQ → ~60 tok/s ⭐⭐⭐⭐⭐

Ollama GGUF Q4:
- qwen3:14b → ~45 tok/s ⭐⭐⭐⭐
- qwen3:30b → ~25 tok/s ⭐⭐⭐
```

---

## GPU Detection in AIfred

AIfred automatically detects your GPU on startup and warns about incompatible backends:

**On Tesla P40:**
```
🔍 Detecting GPU capabilities...
✅ GPU: Tesla P40 (Compute 6.1)
⚠️ Incompatible backends: vllm, tabbyapi
⚠️ vllm: requires compute capability 7.5+ (you have 6.1)
⚠️ Tesla P40: Extremely slow FP16 performance
⚠️ Recommendation: Use Ollama (GGUF) only
```

**On RTX 4090:**
```
🔍 Detecting GPU capabilities...
✅ GPU: NVIDIA GeForce RTX 4090 (Compute 8.9)
✅ All backends compatible
✅ Recommended: vLLM with AWQ for best performance
```

---

## Summary

✅ **Modern GPUs (RTX 20+, A100, H100)** → Use **vLLM with AWQ**
✅ **Pascal GPUs (P40, GTX 10 series)** → Use **Ollama with GGUF**
✅ **Not sure?** → Start with Ollama (works everywhere)

Need help? Check the main README or open an issue on GitHub!
