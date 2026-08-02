> ⚠️ **Historical (as of 2025-11, pre state-refactor):** Code references to `aifred/state.py` (now the `aifred/state/` package) and to removed modules like `vllm_context_cache.py` no longer resolve, and hardware notes refer to the pre-migration GPU setup. **Deliberately kept:** vLLM returns as the main backend after the P40→V100 migration (see TODO.md) — these notes will be revised then. Do not delete.

# vLLM Changes Analysis - Debugging Crashes

**Ziel**: Systematisch zurückgehen zu einer funktionierenden vLLM-Konfiguration

## Relevante Commits (seit November 2025)

### 🔴 4bb64ca - fix: vLLM quantization auto-detection for better GPU compatibility
**Date**: 2025-11-12 (HEUTE)
**Geändert**:
- **ENTFERNT**: `detect_quantization()` Funktion
- **ENTFERNT**: `--quantization awq_marlin` Flag
- **Begründung**: vLLM auto-detects AWQ from config.json, expliziter Flag verursacht config mismatch

**Wichtigste Änderung**:
```python
# VORHER (commit 3732393):
quant = detect_quantization(model)
if quant:
    cmd.extend(["--quantization", quant])

# NACHHER (commit 4bb64ca):
# Note: We don't add --quantization for AWQ models because:
# 1. AWQ models have quantization in their config.json
# 2. vLLM auto-detects AWQ and uses Marlin kernel on Ampere+ GPUs
# 3. Specifying --quantization awq_marlin causes a config mismatch error
logger.info(f"✅ vLLM will auto-detect quantization from model config")
```

**Status**: ⚠️ Diese Änderung ist aktuell AKTIV (HEAD)

---

### 🟡 3732393 - feat: 8x Performance boost + Context auto-detection + Real tokenizer
**Date**: 2025-11-11
**Geändert**:
- **HINZUGEFÜGT**: `detect_quantization()` Funktion
- **HINZUGEFÜGT**: Automatisches Setzen von `--quantization` Flag
- **Logik**: Model-Name parsen → awq/gptq/gguf erkennen → Flag setzen

**vLLM Config in state.py (commit 3732393)**:
```python
self._vllm_manager = vLLMProcessManager(
    port=8001,
    max_model_len=16384,  # ⚠️ 16K Context (zu wenig!)
    gpu_memory_utilization=0.85
)
```

**Status**: ✅ Dieser Commit war die LETZTE bekannte funktionierende Version

---

### 🟢 aa0ffad - feat: Upgrade vLLM models to Qwen2.5-Instruct-AWQ (128K context)
**Date**: 2025-11-09
**Geändert**: Nur Model-Downloads, KEINE vLLM-Code-Änderungen

---

### 🟢 2fa2365 - feat: Add Qwen3 AWQ models for vLLM (14B + 32B)
**Date**: 2025-11-08
**Geändert**: Nur Model-Downloads, KEINE vLLM-Code-Änderungen

---

## State.py Änderungen (seit heute)

### Aktuelle Konfiguration (HEAD):
```python
self._vllm_manager = vLLMProcessManager(
    port=8001,
    max_model_len=22256,  # ⬇️ Reduziert wegen P40 VRAM-Limit
    gpu_memory_utilization=0.95  # ⬆️ Erhöht von 0.85
)
```

### Vorherige Konfiguration (commit 3732393):
```python
self._vllm_manager = vLLMProcessManager(
    port=8001,
    max_model_len=16384,  # 16K Context
    gpu_memory_utilization=0.85
)
```

---

## Rollback-Plan

### Schritt 1: Teste commit 3732393 (letzte funktionierende Version)
```bash
# Checkout alte vllm_manager.py
git checkout 3732393 -- aifred/lib/vllm_manager.py

# Teste mit 16K Context + 0.85 GPU Memory
# In state.py ändern:
max_model_len=16384
gpu_memory_utilization=0.85
```

**Erwartung**: Sollte funktionieren (war die funktionierende Version)

---

### Schritt 2: Teste mit höherem Context
```bash
# Behalte commit 3732393 vllm_manager.py
# Teste mit 32K Context + 0.85 GPU Memory
max_model_len=32768
gpu_memory_utilization=0.85
```

**Erwartung**: Evtl. KV-Cache Error (zu wenig VRAM)

---

### Schritt 3: Teste mit mehr GPU Memory
```bash
# Behalte commit 3732393 vllm_manager.py
# Teste mit 32K Context + 0.95 GPU Memory
max_model_len=32768
gpu_memory_utilization=0.95
```

**Erwartung**: Evtl. funktioniert es mit mehr VRAM

---

### Schritt 4: Finde Maximum für P40
```bash
# Behalte commit 3732393 vllm_manager.py
# Binary Search für max_model_len:
# Test 1: 24000 (zwischen 16K und 32K)
# Test 2: 28000 oder 20000 (je nach Ergebnis)
# Test 3: ...
```

**Ziel**: Maximalen stabilen Context für Tesla P40 finden

---

## Unterschiede zwischen Commits

### detect_quantization() Funktion

**Commit 3732393 (FUNKTIONIERT)**:
```python
def detect_quantization(model_name: str) -> str:
    model_lower = model_name.lower()
    if "awq" in model_lower:
        return "awq_marlin"  # Fastest on Ampere+ GPUs
    elif "gguf" in model_lower:
        return "gguf"
    elif "gptq" in model_lower:
        return "gptq"
    else:
        return ""  # No quantization (FP16/BF16)

# Usage:
quant = detect_quantization(model)
if quant:
    cmd.extend(["--quantization", quant])
    logger.info(f"✅ Using quantization: {quant}")
```

**Commit 4bb64ca (AKTUELL - CRASHT)**:
```python
# Funktion wurde ENTFERNT

# Begründung im Code:
# Note: We don't add --quantization for AWQ models because:
# 1. AWQ models have quantization in their config.json
# 2. vLLM auto-detects AWQ and uses Marlin kernel on Ampere+ GPUs
# 3. Specifying --quantization awq_marlin causes a config mismatch error
logger.info(f"✅ vLLM will auto-detect quantization from model config")
```

---

## Hypothesen

### Hypothese 1: `--quantization` Flag ist nötig
- ✅ Commit 3732393 funktionierte MIT Flag
- ❌ Commit 4bb64ca crasht OHNE Flag
- **Test**: Restore `detect_quantization()` und `--quantization` Flag

### Hypothese 2: 22K Context ist zu viel
- ⚠️ vLLM sagte "max 22256 tokens possible"
- ⚠️ Aber evtl. ist das zu knapp?
- **Test**: Gehe zurück zu 16K (funktionierte vorher)

### Hypothese 3: 0.95 GPU Memory ist zu viel
- ⚠️ Evtl. braucht vLLM mehr Overhead
- ⚠️ 0.85 war sicherer
- **Test**: Gehe zurück zu 0.85

### Hypothese 4: Kombination aus allen Faktoren
- ⚠️ 16K + 0.85 + quantization Flag = funktioniert
- ❌ 22K + 0.95 + KEIN Flag = crasht
- **Test**: Systematisch einzelne Variablen ändern

---

## Nächste Schritte

1. ✅ **Restore commit 3732393 vllm_manager.py** komplett
2. ✅ **Setze state.py zurück auf 16K + 0.85**
3. 🧪 **Teste ob vLLM startet**
4. 🧪 **Falls ja: Schrittweise max_model_len erhöhen**
5. 🧪 **Finde maximalen stabilen Wert für P40**
6. 📝 **Dokumentiere finalen Wert**

---

## Tesla P40 Hardware Limits

- **VRAM**: 24 GB
- **Compute Capability**: 6.1 (Pascal)
- **FP16 Performance**: 1:64 ratio (sehr langsam!)
- **Recommended Backend**: Ollama (GGUF)
- **KV-Cache Limit**: ~3 GiB bei 95% GPU Memory

**Qwen3-8B-AWQ KV-Cache Requirements**:
- 16K context → ~1.4 GiB KV cache ✅ Funktioniert
- 22K context → ~2.7 GiB KV cache ⚠️ Knapp
- 32K context → ~4.0 GiB KV cache ❌ Zu viel
- 40K context → ~5.6 GiB KV cache ❌ Viel zu viel

---

## Zusammenfassung

**Funktionierende Konfiguration (commit 3732393)**:
- ✅ `detect_quantization()` vorhanden
- ✅ `--quantization awq_marlin` Flag gesetzt
- ✅ `max_model_len=16384` (16K)
- ✅ `gpu_memory_utilization=0.85`

**Aktuelle Konfiguration (HEAD - CRASHT)**:
- ❌ `detect_quantization()` entfernt
- ❌ KEIN `--quantization` Flag
- ❌ `max_model_len=22256` (22K)
- ❌ `gpu_memory_utilization=0.95`

**Plan**: Schritt für Schritt zurück zur funktionierenden Konfiguration gehen!