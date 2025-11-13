# Scripts

This directory contains utility scripts for AIfred Intelligence setup, maintenance, and model management.

## Model Download Scripts

### Download All Models
```bash
./scripts/download_all_models.sh
```
Downloads both Ollama (GGUF) and vLLM (AWQ) models for complete setup.

### Ollama Models (GGUF)
```bash
./scripts/download_ollama_models.sh
```
Downloads GGUF models via Ollama:
- **qwen3:30b-instruct** - Main LLM (256K context, multilingual)
- **qwen3:8b** - Automatik LLM (optional thinking mode)
- **qwen2.5:3b** - Ultra-fast decision making

**Compatibility**: Works on ALL GPUs (Pascal P40, GTX 10 series, and newer)

### vLLM Models (AWQ)
```bash
./scripts/download_vllm_models.sh
```
Downloads AWQ-quantized models from Hugging Face:
- **Qwen3-8B-AWQ** - 8B model with 40K native context
- **Qwen3-14B-AWQ** - 14B model with 40K native context
- **Qwen3-32B-AWQ** - 32B model with 40K native context

**Compatibility**: Requires Volta+ GPU (Compute Capability 7.0+, fast FP16)
- ✅ RTX 20/30/40 series, A100, H100
- ❌ Tesla P40, GTX 10 series (use Ollama instead)

## Runtime Scripts

### Run AIfred
```bash
./scripts/run_aifred.sh
```
Starts AIfred Intelligence in development mode with Reflex.

## Vector Cache Maintenance

### List Cache Entries
```bash
./scripts/list_cache.py
```
Lists all entries in the ChromaDB vector cache with metadata.

### Search Cache
```bash
./scripts/search_cache.py "search query"
```
Search the vector cache for specific content.

### Chroma Maintenance
```bash
./scripts/chroma_maintenance.py
```
Maintenance utilities for ChromaDB vector database:
- Check database health
- Compact collections
- Remove duplicates

## Usage Notes

### Model Storage Locations
- **Ollama**: `~/.ollama/models/`
- **vLLM/HuggingFace**: `~/.cache/huggingface/hub/`

### Disk Space Requirements
- **Ollama Models**: ~25 GB (qwen3:30b + qwen3:8b + qwen2.5:3b)
- **vLLM AWQ Models**: ~25 GB (Qwen3-8B + Qwen3-14B + Qwen3-32B)
- **Total**: ~50 GB for all models

### GPU Requirements
Check [GPU_COMPATIBILITY.md](../docs/GPU_COMPATIBILITY.md) for detailed compatibility information.

## See Also
- [README.md](../README.md) - Main project README
- [GPU_COMPATIBILITY.md](../docs/GPU_COMPATIBILITY.md) - GPU compatibility matrix
- [docs/vllm/](../docs/vllm/) - vLLM-specific documentation
