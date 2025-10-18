# Hardware-Erkennung & Portabilität

**Autor:** AIfred Intelligence Team
**Datum:** 2025-10-17
**Version:** 1.0

## Übersicht

AIfred Intelligence erkennt automatisch die verfügbare Hardware (GPU-Typ, VRAM, Stabilität) und passt die LLM-Parameter dynamisch an. Dies ermöglicht portablen Code, der auf verschiedenen Systemen optimal läuft.

---

## Unterstützte Hardware

### GPU-Typen
- ✅ **AMD GPUs** (via ROCm) - Radeon 780M, 680M, RX-Serie
- ✅ **NVIDIA GPUs** (via CUDA) - RTX 30/40-Serie, etc.
- ✅ **CPU-only** - Fallback wenn keine GPU

### Hardware-Erkennung

Die Funktion `_detect_hardware()` in `lib/ollama_wrapper.py` erkennt automatisch:

```python
{
    "vram_gb": 11.6,                    # Verfügbarer VRAM in GB
    "gpu_type": "AMD",                  # AMD, NVIDIA, INTEL, CPU
    "gpu_name": "Radeon 780M",          # Konkreter GPU-Name
    "library": "ROCm",                  # ROCm, CUDA, CPU
    "is_igpu": True,                    # Integrierte GPU?
    "is_stable_for_32b": False          # Bekannte Probleme?
}
```

**Erkennungsmethoden:**
- **AMD:** `rocm-smi --showproductname` + `rocm-smi --showmeminfo vram`
- **NVIDIA:** `nvidia-smi --query-gpu=name,memory.total`
- **Fallback:** CPU-only wenn keine GPU erkannt

**Caching:** Hardware-Info wird einmal beim Start gecacht und wiederverwendet.

---

## Bekannte Hardware-Probleme

### AMD iGPU + qwen3:32B = GPU Hang

**Problem:**
- AMD Radeon 780M/680M (iGPU) crasht bei qwen3:32B mit GPU-Beschleunigung
- Fehler: `HW Exception by GPU node-1: GPU Hang`
- Grund: VRAM-Fragmentierung + Compute Buffer Allocation Failure

**Lösung:**
- Automatischer CPU-Fallback für 32B Modelle auf AMD iGPUs
- User wird informiert: `"⚠️ AMD iGPU crasht bei 32B mit GPU → CPU-only"`
- Kleinere Modelle (8B, 4B) funktionieren problemlos auf GPU

**Code-Logik:**
```python
if hw['is_igpu'] and hw['gpu_type'] == 'AMD' and not hw['is_stable_for_32b']:
    if '32b' in model_name:
        config['force_cpu'] = True
        config['reason'] = "AMD iGPU crasht bei 32B"
```

---

## Dynamische Parameter-Konfiguration

### Context Window Größen

Die Context-Größe (`num_ctx`) wird basierend auf Hardware und Modell dynamisch gesetzt:

| Modell | GPU-Typ | VRAM | num_ctx | Begründung |
|--------|---------|------|---------|------------|
| qwen3:32b | AMD iGPU | 11.6 GB | 8192 | CPU-Fallback, RAM-limitiert |
| qwen3:32b | RTX 3060 | 12 GB | 16384 | Hybrid GPU/CPU, genug VRAM |
| qwen3:32b | RTX 4090 | 24 GB | 65536 | Volle GPU, großer Context |
| qwen3:8b | Beliebig | >8 GB | 32768 | Natives Maximum |
| qwen3:4b | Beliebig | >4 GB | 32768 | Natives Maximum |

**Warum unterschiedlich?**
- Größerer Context = mehr KV Cache = mehr RAM/VRAM
- 128K Context mit 32B = **~10 GB nur für KV Cache!**
- Muss an verfügbaren Speicher angepasst werden

### GPU Layer Offloading

Die Anzahl der GPU-Layer (`num_gpu`) wird basierend auf VRAM berechnet:

| Modell | VRAM | num_gpu | Layers Total | GPU % |
|--------|------|---------|--------------|-------|
| qwen3:32b | <12 GB | 0 (CPU) | 65 | 0% |
| qwen3:32b | 12 GB | 25 | 65 | 38% |
| qwen3:32b | 16 GB | 35 | 65 | 54% |
| qwen3:32b | 24 GB | 50 | 65 | 77% |
| qwen3:8b | >8 GB | Auto | 37 | 100% |

**Auto-Detect vs. Manuell:**
- `num_gpu=None` → Ollama entscheidet selbst (Auto-Detect)
- `num_gpu=25` → Genau 25 Layer auf GPU (manuell)
- Auto-Detect gut für kleine Modelle, manuell besser für große

---

## Beispiel-Konfigurationen

### MiniPC (AMD Ryzen 7 7840HS + Radeon 780M)

```yaml
Hardware:
  CPU: AMD Ryzen 7 7840HS (8C/16T)
  iGPU: Radeon 780M
  VRAM: 11.6 GB (UMA, shared with RAM)
  RAM: 64 GB DDR5

Konfiguration qwen3:32b:
  force_cpu: true
  num_gpu: 0
  num_ctx: 8192
  reason: "AMD iGPU crasht bei 32B"

Konfiguration qwen3:8b:
  force_cpu: false
  num_gpu: auto (alle 37 Layer)
  num_ctx: 32768
  Performance: ~12 t/s (GPU)
```

### Hauptrechner (AMD Ryzen 9 9900X3D + RTX 3060)

```yaml
Hardware:
  CPU: AMD Ryzen 9 9900X3D (12C/24T)
  GPU: NVIDIA GeForce RTX 3060
  VRAM: 12 GB GDDR6
  RAM: 64 GB DDR5

Konfiguration qwen3:32b:
  force_cpu: false
  num_gpu: 25 (Hybrid)
  num_ctx: 16384
  reason: "12 GB VRAM ausreichend für Hybrid"
  Performance: ~8-10 t/s (geschätzt)

Konfiguration qwen3:8b:
  force_cpu: false
  num_gpu: auto (alle 37 Layer)
  num_ctx: 32768
  Performance: ~25-30 t/s (geschätzt)
```

---

## Ollama-Bug: Compute Buffers

### Problem

Ollama's Auto-Detect berücksichtigt **nicht** die Compute Buffers in der VRAM-Kalkulation:

```python
# Ollama rechnet:
VRAM_benötigt = Model_Weights + KV_Cache

# Tatsächlich benötigt:
VRAM_benötigt = Model_Weights + KV_Cache + Compute_Buffers + Graph_Memory
```

**Resultat:** Ollama sagt "passt rein", crasht aber mit `failed to allocate compute buffers`.

### Lösung in AIfred

Wir setzen **konservative Layer-Limits** für große Modelle:
- 32B mit 11.6 GB VRAM: Max 25 Layer (statt Auto-Detect 32)
- Spart ~2 GB für Compute Buffers

**GitHub Issues:**
- [#11202](https://github.com/ollama/ollama/issues/11202) - Overcommitting GPU memory
- [#1385](https://github.com/ollama/ollama/issues/1385) - Context size not in VRAM calculation (behoben)

---

## Context Size Limits

### Was ist Context Window?

Der Context Window (`num_ctx`) bestimmt:
- Wie viel Text das Modell "sehen" kann
- Größe des KV Cache (Key-Value Cache)
- RAM/VRAM Verbrauch

**Faustregeln:**
- 1 Token ≈ 0.75 Wörter (Deutsch)
- 2048 Tokens ≈ 1500 Wörter ≈ 3 Seiten Text
- 8192 Tokens ≈ 6000 Wörter ≈ 12 Seiten Text
- 32768 Tokens ≈ 24.000 Wörter ≈ 48 Seiten Text

### RAM/VRAM Verbrauch pro Context

**qwen3:32B Modell:**
```
Context Size → KV Cache → Total RAM (mit Model)
2048 (2K)    → 1.0 GB   → 19.4 GB
4096 (4K)    → 1.3 GB   → 19.7 GB
8192 (8K)    → 2.0 GB   → 20.4 GB
16384 (16K)  → 4.0 GB   → 22.4 GB
32768 (32K)  → 8.0 GB   → 26.4 GB
131072 (128K)→ 32.0 GB  → 50.4 GB (!)
```

**qwen3:8B Modell:**
```
Context Size → KV Cache → Total RAM (mit Model)
2048 (2K)    → 0.3 GB   → 5.4 GB
8192 (8K)    → 1.0 GB   → 6.1 GB
32768 (32K)  → 4.0 GB   → 9.1 GB
131072 (128K)→ 16.0 GB  → 21.1 GB
```

### Natives Maximum vs. Erweitert

**Qwen3 Spezifikationen:**
- **0.6B / 1.7B / 4B:** 32K nativ, erweiterbar auf ~112K (YaRN)
- **8B / 14B / 32B:** 128K nativ

**Aber:** Großer Context braucht viel RAM!
- 32B mit 128K = 50 GB RAM (unrealistisch auf MiniPC)
- Daher: Konservative Limits setzen

---

## Testing & Validation

### Hardware-Erkennung testen

```python
from lib.ollama_wrapper import get_hardware_info

hw = get_hardware_info()
print(f"GPU: {hw['gpu_name']}")
print(f"VRAM: {hw['vram_gb']:.1f} GB")
print(f"Typ: {hw['gpu_type']}")
print(f"Stabil für 32B: {hw['is_stable_for_32b']}")
```

### Parameter-Konfiguration testen

```python
from lib.ollama_wrapper import _get_safe_config_for_model

config = _get_safe_config_for_model("qwen3:32b")
print(f"Force CPU: {config['force_cpu']}")
print(f"num_gpu: {config['num_gpu']}")
print(f"num_ctx: {config['num_ctx']}")
print(f"Reason: {config['reason']}")
```

### Log-Überwachung

```bash
# AIfred Logs
journalctl -u aifred-intelligence -f | grep -E "Hardware|GPU|CPU-Fallback"

# Ollama Logs
journalctl -u ollama -f | grep -E "library=|layers.offload=|num_ctx="
```

**Erwartete Ausgabe (MiniPC, qwen3:32b, GPU aktiviert):**
```
🔍 [Hardware] AMD GPU erkannt: Radeon 780M, VRAM: 11.6 GB
⚠️ [ollama.chat] CPU-Fallback erzwungen für qwen3:32b
   Grund: AMD iGPU (Radeon 780M) crasht bei 32B mit GPU → CPU-only
🔧 [ollama.chat] Context auf 8192 gesetzt (CPU-Mode)
```

---

## Portabilität

### Code bleibt identisch

Der **gleiche Code** läuft optimal auf:
- ✅ MiniPC mit AMD iGPU (11.6 GB VRAM)
- ✅ Hauptrechner mit RTX 3060 (12 GB VRAM)
- ✅ Workstation mit RTX 4090 (24 GB VRAM)
- ✅ Server ohne GPU (CPU-only)

**Keine manuellen Anpassungen nötig!**

### Settings bleiben portabel

`assistant_settings.json` ist portabel:
```json
{
  "model": "qwen3:32b",
  "enable_gpu": true
}
```

Hardware-spezifische Anpassung erfolgt automatisch:
- MiniPC: GPU-Toggle aktiv, aber 32B läuft auf CPU
- Hauptrechner: GPU-Toggle aktiv, 32B läuft hybrid auf GPU

---

## Zusammenfassung

### Was automatisch funktioniert

✅ Hardware-Erkennung (GPU-Typ, VRAM, Name)
✅ Problematische Kombinationen erkennen (AMD iGPU + 32B)
✅ Automatischer CPU-Fallback mit Warnung
✅ Context-Größe an RAM/VRAM anpassen
✅ Layer-Offloading optimieren
✅ Portabler Code zwischen Systemen

### Was noch manuell ist

❌ LLM-Parameter (Temperature, etc.) → **Wird in v1.1 implementiert**
❌ Benchmark-Ergebnisse zwischen Systemen vergleichen
❌ Modell-spezifische Feintuning

---

## Weitere Dokumentation

- [MEMORY_MANAGEMENT.md](MEMORY_MANAGEMENT.md) - RAM/VRAM Management
- [LLM_COMPARISON.md](LLM_COMPARISON.md) - Modell-Vergleiche
- [MODEL_COMPARISON_DETAILED.md](MODEL_COMPARISON_DETAILED.md) - Detaillierte Benchmarks
- [INDEX.md](INDEX.md) - Hauptindex

---

**Stand:** 2025-10-17
**Getestet auf:** AMD Ryzen 7 7840HS + Radeon 780M, 64GB RAM, Ollama v0.4.x
