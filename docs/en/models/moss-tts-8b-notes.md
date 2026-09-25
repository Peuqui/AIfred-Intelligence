# MOSS-TTS 8B on Turing GPUs (RTX 8000 / RTX 6000)

> **Deutsche Version:** [moss-tts-8b-notes.md](../../de/models/moss-tts-8b-notes.md)

> As of: 2026-02-20 | Tested on: NVIDIA RTX 8000 (48 GB, Turing, CC 7.5)

## Summary

The MOSS-TTS Delay 8B model (`OpenMOSS-Team/MOSS-TTS`, MossTTSDelayModel)
runs on Turing GPUs (compute capability < 8.0) only in **float32**.
Float16 and bfloat16 lead to NaN values and crashes.

---

## Problems and Solutions

### 1. Missing generation_config.json

**Error:**
```
OSError: OpenMOSS-Team/MOSS-TTS does not appear to have a file named generation_config.json
```

**Cause:** The 8B HuggingFace repo has no `generation_config.json` (unlike
standard HF models).

**Solution:** Do not load `GenerationConfig` via `from_pretrained()`; instantiate it
directly or omit it entirely — the 8B model does not need it.

### 2. Incompatible generate() API

**Error:**
```
TypeError: MossTTSDelayModel.generate() got an unexpected keyword argument 'generation_config'
```

**Cause:** The 8B model (MossTTSDelay) has its own `generate()` signature,
which is NOT compatible with the standard HuggingFace API.

**8B generate() signature:**
```python
model.generate(
    input_ids=...,
    attention_mask=...,
    max_new_tokens=4096,
    text_temperature=1.5,      # Temperature for text tokens
    text_top_p=0.8,
    text_top_k=25,
    audio_temperature=1.7,     # Temperature for audio tokens
    audio_top_p=0.8,
    audio_top_k=25,
    audio_repetition_penalty=1.0,
)
```

**Solution:** Pass parameters directly instead of via `GenerationConfig`.

### 3. Float16 NaN on Turing GPUs

**Error:**
```
RuntimeError: probability tensor contains either inf, nan or element < 0
```
(via `torch.multinomial` in the generate() method)

**Cause:** The generate() method divides logits by the temperature in place
(`logit / text_temperature`). In float16 on Turing GPUs (CC 7.5, no native
bfloat16 support) the values overflow and produce NaN/Inf.

**Failed approaches:**
- Monkey-patching `sample_token` for a float32 cast: the NaN arises BEFORE sample_token
- Patching in `inference_utils` and `modeling_moss_tts`: same reason

**Solution:** Load the entire model in **float32**:
```python
def resolve_dtype():
    if device != "cuda":
        return torch.float32
    major, _ = torch.cuda.get_device_capability()
    if major >= 8:  # Ampere+: native bfloat16
        return torch.bfloat16
    else:           # Turing/older: float32
        return torch.float32
```

---

## VRAM Usage

| Precision | VRAM (measured) | Model size | Status |
|-----------|----------------|----------------|--------|
| bfloat16  | ~17 GB         | ~17 GB         | Ampere+ only (CC >= 8.0) |
| float16   | ~17 GB         | ~17 GB         | **NaN on Turing** — do not use! |
| float32   | ~41.6 GB       | ~34 GB         | Works on all GPUs |

**Minimum VRAM for float32:** ~34 GB (model) + overhead = approx. 38-42 GB
→ RTX 8000 (48 GB) fits, RTX 3090 Ti (24 GB) is NOT enough.

---

## Performance

Measured on RTX 8000 (float32):
- **Generation time:** ~13.5 seconds for 2 sentences (~30 words)
- **Token speed:** ~9.3 tokens/second
- **Quality:** Good, but less intonation/emotion than XTTS v2

Comparison with the 1.7B model (MossTTSLocal):
- 1.7B on RTX 8000 (bfloat16): ~18-22 seconds per sentence
- 8B on RTX 8000 (float32): ~13.5 seconds for 2 sentences
- Despite float32, the 8B model is faster than the 1.7B (different architecture: Delay vs Local)

---

## Comparison 1.7B (Local) vs 8B (Delay)

| Property | MOSS-TTS Local 1.7B | MOSS-TTS Delay 8B |
|-------------|---------------------|-------------------|
| Architecture | MossTTSLocal (Global Latent + Local Transformer) | MossTTSDelay (Delay Pattern) |
| generate() API | Standard HuggingFace (`generation_config`) | Own API (direct parameters) |
| VRAM (BF16) | ~11.5 GB | ~17 GB |
| VRAM (FP32) | ~22 GB | ~34 GB |
| Min. GPU (BF16) | RTX 3090 Ti (24 GB) | RTX A5000 (24 GB) — tight |
| Min. GPU (FP32) | RTX 8000 (48 GB) — oversized | RTX 8000 (48 GB) |
| Sample rate | 22.05 kHz | 22.05 kHz |
| Seed-TTS EN SIM | 73.42% (better) | 71.46% |
| Seed-TTS ZH SIM | 78.82% (better) | 77.05% |
| Parameter control | Standard (temperature, top_p, top_k) | Separate: audio_* and text_* |

---

## Docker Configuration

**docker-compose.8b.yml specifics:**
- `MOSS_VRAM_THRESHOLD=30.0` (float32 needs ~34 GB)
- `MOSS_MODEL=OpenMOSS-Team/MOSS-TTS` (8B main repo, not Local/Realtime)
- Separate volume `moss_models_8b` (~17 GB download)
- `start_period: 600s` in the healthcheck (10 min for the initial download)

**server_8b.py features:**
- Web UI with parameter sliders (audio/text temperature, top-p, top-k, repetition penalty)
- Per-request parameter overrides via the `/tts` endpoint
- Reset-to-defaults button
- Playback speed controls

---

## Known Limitations

1. **No float16 on Turing**: NaN in the generate() method, not patchable
2. **High VRAM usage**: 41.6 GB of 48 GB — little headroom for long texts
3. **Less expressive than XTTS v2**: Good quality, but more monotonous intonation
4. **Cross-lingual voice cloning**: Keeps the accent of the reference language (like 1.7B)
5. **No streaming**: Chunk-based, generates the complete audio block at once
