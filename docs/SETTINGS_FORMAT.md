# AIfred Settings Format

## Settings File Location
`~/.config/aifred/settings.json`

## Format (New - Multi-Backend)

```json
{
  "backend_type": "ollama",
  "research_mode": "automatik",
  "temperature": 0.2,
  "enable_thinking": true,
  "backend_models": {
    "ollama": {
      "selected_model": "qwen3:8b",
      "automatik_model": "qwen2.5:3b"
    },
    "vllm": {
      "selected_model": "Qwen/Qwen3-8B-AWQ",
      "automatik_model": "Qwen/Qwen3-4B-AWQ"
    },
    "tabbyapi": {
      "selected_model": "turboderp/Qwen3-8B-4.0bpw-exl2",
      "automatik_model": "turboderp/Qwen3-4B-4.0bpw-exl2"
    }
  }
}
```

## Backend-Specific Default Models

Jedes Backend hat unterschiedliche Modellnamen und Quantisierungen:

**Ollama (GGUF Q4/Q8)**
- Main Model: `qwen3:8b` (~5.2GB)
- Automatik Model: `qwen2.5:3b` (~1.9GB)

**vLLM (Dynamic Model Loading) - Sleep Mode Enabled**
- Main Model: `Qwen/Qwen3-8B-AWQ` (~5GB, default)
- Automatik Model: `Qwen/Qwen3-4B-AWQ` (~2.5GB, default)
- Supports different models via Sleep Mode (Level 1: ~0.1-0.8s switching)
- Service starts with model from settings (or config.py defaults)
- Auto-detects quantization: AWQ (awq_marlin), GGUF, GPTQ, or FP16/BF16

**TabbyAPI (EXL2)**
- Main Model: `turboderp/Qwen3-8B-4.0bpw-exl2`
- Automatik Model: `turboderp/Qwen3-8B-4.0bpw-exl2` (same as main - TabbyAPI loads only ONE model)

Diese Defaults sind in `config.py` unter `BACKEND_DEFAULT_MODELS` definiert.

## Behavior

### On Startup
1. Load `backend_type` (last used backend)
2. Restore models for that backend from `backend_models[backend_type]`
3. If no saved models exist, use config.py defaults

### On Backend Switch
1. Save current backend's models to `backend_models[old_backend]`
2. Switch to new backend
3. Restore models from `backend_models[new_backend]` (if available)
4. If no saved models exist, use defaults from `config.BACKEND_DEFAULT_MODELS[new_backend]`
5. Save settings

### On Model Change
1. Update `backend_models[current_backend]` immediately
2. Save settings

## Backward Compatibility

Old settings format (without `backend_models`) is still supported:

```json
{
  "backend_type": "ollama",
  "selected_model": "qwen3:8b",
  "automatik_model": "qwen2.5:3b",
  "research_mode": "automatik",
  "temperature": 0.2
}
```

When loaded, these will be migrated to the new format automatically.

## Qwen3 Thinking Mode

- `enable_thinking: true` → Temperature 0.6, CoT reasoning
- `enable_thinking: false` → Temperature 0.7, direct answers
- Only visible in UI when Qwen3/QwQ models are selected
