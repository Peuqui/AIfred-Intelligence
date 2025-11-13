# vLLM Documentation

This directory contains documentation related to vLLM backend configuration, troubleshooting, and hardware-specific optimizations.

## Documents

### Configuration & Setup
- **[VLLM_RTX3060_CONFIG.md](VLLM_RTX3060_CONFIG.md)** - RTX 3060 optimal configuration (26K context @ 90% GPU memory)
  - Hardware limits and VRAM usage
  - Command-line examples
  - Performance specifications

### Troubleshooting & Fixes
- **[VLLM_FIX_SUMMARY.md](VLLM_FIX_SUMMARY.md)** - Summary of vLLM crash fixes
  - Root cause analysis
  - Solution implementation
  - Rollback strategy

- **[VLLM_CHANGES_ANALYSIS.md](VLLM_CHANGES_ANALYSIS.md)** - Detailed commit history analysis
  - What changed between working/broken commits
  - Key code differences
  - Systematic debugging approach

### Development History
- **[COMMIT_HISTORY_VLLM.md](COMMIT_HISTORY_VLLM.md)** - Complete commit history for vLLM-related changes
  - Chronological log of all vLLM modifications
  - Useful for future debugging

## Related Documentation

- [GPU_COMPATIBILITY.md](../GPU_COMPATIBILITY.md) - GPU compatibility matrix for all backends
- [CHANGELOG.md](../../CHANGELOG.md) - Main project changelog

## Quick Links

### GPU-Specific Configurations
- **RTX 3060 (12GB, Ampere 8.6)**: 26,608 tokens @ 90% GPU memory ✅ Tested
- **Tesla P40 (24GB, Pascal 6.1)**: ❌ Not compatible (use Ollama instead)

### Key Features
- ✅ Auto-detection of model context limits (40K-128K)
- ✅ AWQ Marlin quantization for Ampere+ GPUs
- ✅ Automatic quantization format detection
- ✅ Hardware-constrained memory management

## See Also
- Main project README: [../../README.md](../../README.md)
- GPU Detection: [aifred/lib/gpu_detection.py](../../aifred/lib/gpu_detection.py)
- vLLM Manager: [aifred/lib/vllm_manager.py](../../aifred/lib/vllm_manager.py)
