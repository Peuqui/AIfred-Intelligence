# vLLM Documentation

vLLM backend reference and implementation notes. Kept slim — historical
debugging/rollback docs from the November 2025 crash episode were
removed (they were context-specific and the fixes are long merged).

> **Current status:** vLLM is **available** as a backend (selectable in
> the UI) but **not the primary path** on this rig. The Pascal-generation
> Tesla P40s in the active GPU pool have well-known issues with vLLM's
> AWQ Marlin kernels. Once the P40 → V100 swap is complete, vLLM is
> planned to come back as the primary backend.

## Documents

- **[vllm_vram_detection.md](vllm_vram_detection.md)** — How the vLLM
  manager detects free VRAM and clamps `gpu_memory_utilization` to
  avoid OOM during model load
- **[VLLM_YARN_AUTO_DETECTION.md](VLLM_YARN_AUTO_DETECTION.md)** —
  Auto-detection of YARN / RoPE scaling factor from the model's
  `config.json`, used to pick a safe max-context per model

## Key Code Paths

- vLLM Manager: [aifred/lib/vllm_manager.py](../../aifred/lib/vllm_manager.py)
- GPU Detection: [aifred/lib/gpu_detection.py](../../aifred/lib/gpu_detection.py)
- Backend Adapter: [aifred/backends/vllm_backend.py](../../aifred/backends/vllm_backend.py)

## Hardware Compatibility

| GPU | Compute Cap | vLLM AWQ Marlin | Status |
|---|---|---|---|
| Tesla V100 | 7.0 | ⚠️ Limited (no native AWQ Marlin) | Workable with FP16 |
| Tesla P40 | 6.1 | ❌ Not supported | Use llama.cpp/Ollama |
| Quadro RTX 8000 | 7.5 | ✅ Supported | Recommended |
| RTX Ampere+ | 8.0+ | ✅ Full support | Recommended |
