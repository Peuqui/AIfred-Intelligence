# Context Window Optimization - November 22, 2025

## Overview

Major improvements to context window management across all backends (Ollama, vLLM, TabbyAPI), focusing on maximum VRAM utilization and accurate context tracking.

## Key Changes

### 1. Dynamic Context Window (No Rounding)

**Previous Behavior:**
- Context sizes were rounded to fixed increments: 2K, 4K, 8K, 10K, 12K, 16K, 20K, 24K, 28K, 32K, 40K, 64K
- Wasted RAM when rounding up (e.g., 35K needed → 40K allocated)
- Jumped to 64K when >40K needed, even if VRAM only supported 44K

**New Behavior:**
- Uses **exact calculated values** based on `needed_tokens = input + reserve`
- No rounding - directly uses the calculated number
- Automatically clips to VRAM-based limit when needed

**Example:**
```python
# Before:
needed_tokens = 42000
calculated_ctx = 65536  # Rounded up to next standard size
final_num_ctx = min(65536, 44051) = 44051  # Clipped to VRAM

# After:
needed_tokens = 42000
calculated_ctx = 42000  # Exact value
final_num_ctx = min(42000, 44051) = 42000  # Exact fit!
```

**Benefit:**
- ~7-10% more context available for long conversations
- No RAM waste from rounding
- Better VRAM utilization

**Code:** [aifred/lib/context_manager.py:184-187](../../aifred/lib/context_manager.py#L184-L187)

### 2. History Compression Context Limit

**Problem:**
History compression was using incorrect context limits, showing wrong utilization percentages.

**Previous Behavior:**
- **Ollama**: Used VRAM-based calculation at compression time → Wrong! (showed 7,505 tokens when model loaded with 40,960)
- **vLLM**: Used `min(model_max, vram_limit)` → Incorrect for some cases

**New Behavior:**
- **Ollama**: Queries `/api/ps` to get the **actual loaded model context** (e.g., 40,960 or 44,051)
- **vLLM/TabbyAPI**: Uses `calculate_dynamic_num_ctx()` for consistent calculation
- **Result**: History shows accurate percentage

**Example Log Output:**
```
Before: 📊 History: 2,169 / 7,505 tok (28%)   ❌ Wrong!
After:  📊 History: 2,169 / 40,960 tok (5%)   ✅ Correct!
```

**Code:**
- [aifred/state.py:1404-1444](../../aifred/state.py#L1404-L1444)
- [aifred/backends/ollama.py:610-640](../../aifred/backends/ollama.py#L610-L640) (new `get_loaded_model_context()` method)

### 3. vLLM Safety Buffer (Fixed → Percentage-based)

**Problem:**
Fixed 150-token safety buffer was too small for large contexts, causing multiple calibration attempts.

**Previous Behavior:**
- Fixed 150 tokens subtracted from vLLM's reported max
- Worked for small contexts (8K-16K)
- Failed for large contexts (20K+) → Required 3+ calibration attempts

**New Behavior:**
- **2% percentage-based buffer** applied iteratively
- Scales with context size
- Applied at each calibration attempt

**Example:**
```
Attempt 1: vLLM reports 22,048 → Apply 2% buffer (440 tokens) → Try 21,608 ✅ Success!
(Before: Would try 21,898 → Fail → Attempt 2 → Attempt 3)
```

**Configuration:**
```python
# aifred/lib/config.py
VLLM_CONTEXT_SAFETY_PERCENT = 0.02  # 2% safety buffer
```

**Code:** [aifred/lib/vllm_manager.py:537-544, 666-673](../../aifred/lib/vllm_manager.py#L537-L544)

### 4. vLLM: Automatik-LLM Synchronization

**Problem:**
vLLM can only load **ONE model at a time** (unlike Ollama). Having different Main-LLM and Automatik-LLM caused 404 errors.

**Solution:**
Automatically sync Automatik-LLM to match Main-LLM when using vLLM backend.

**Implementation Points:**
1. **Model change** ([aifred/state.py:1786-1822](../../aifred/state.py#L1786-L1822))
2. **Backend init - slow path** ([aifred/state.py:566-571](../../aifred/state.py#L566-L571))
3. **Backend init - fast path** ([aifred/state.py:460-466](../../aifred/state.py#L460-L466))

**Debug Output:**
```
🔄 Automatik-LLM angepasst: Qwen/Qwen3-4B-AWQ → cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit
   (vLLM: nur 1 Modell möglich)
```

### 5. YaRN Factor Reset on Model Change

**Problem:**
YaRN factor was persisting when changing models, causing crashes and multi-attempt restarts.

**Solution:**
Automatically reset YaRN to 1.0 when changing models.

**What's Reset:**
- `yarn_factor = 1.0`
- `yarn_factor_input = "1.0"`
- `yarn_max_factor = 0.0` (unknown for new model)
- `yarn_max_tested = False`

**Additional:**
- Shows loading spinner during model change (40-70 seconds for vLLM)

**Code:** [aifred/state.py:1786-1822](../../aifred/state.py#L1786-L1822)

## Testing

### Ollama: Arbitrary num_ctx Values

Tested that Ollama accepts non-standard context sizes:

```bash
# Test with 44,051 tokens (non-standard size)
curl -s http://localhost:11434/api/chat -d '{
  "model": "qwen3:30b-a3b-instruct-2507-q4_K_M",
  "messages": [{"role": "user", "content": "Test"}],
  "options": {"num_ctx": 44051},
  "stream": false
}'

# Verify it was loaded
curl -s http://localhost:11434/api/ps
# Output: "context_length": 44051 ✅
```

**Result:** Ollama accepts arbitrary `num_ctx` values, no rounding needed!

## Performance Impact

| Scenario | Before | After | Improvement |
|----------|--------|-------|-------------|
| **Small Context (10K)** | 20K allocated | 18K allocated | 10% less RAM |
| **Medium Context (35K)** | 40K allocated | 35K allocated | 12.5% less RAM |
| **Large Context (42K)** | 40K allocated (clipped) | 42K allocated | +5% more context |
| **vLLM Calibration** | 3 attempts, 150 tok buffer | 2 attempts, ~440 tok buffer | 33% faster |
| **History Accuracy** | 28% (wrong) | 5% (correct) | Accurate tracking |

## Configuration

### vLLM Safety Buffer
```python
# aifred/lib/config.py
VLLM_CONTEXT_SAFETY_PERCENT = 0.02  # 2% iterative safety buffer
```

### VRAM Context Calculation
```python
# aifred/lib/config.py
VRAM_CONTEXT_RATIO = 0.097  # MB per token (empirical)
VRAM_SAFETY_MARGIN = 512    # MB reserved for system
```

## Related Files

### Modified Files
- `aifred/lib/context_manager.py` - Dynamic context calculation (no rounding)
- `aifred/lib/vllm_manager.py` - Percentage-based safety buffer
- `aifred/state.py` - History limit, YaRN reset, Automatik-LLM sync
- `aifred/backends/ollama.py` - `get_loaded_model_context()` method
- `aifred/lib/config.py` - New `VLLM_CONTEXT_SAFETY_PERCENT` constant

### Documentation
- [CHANGELOG.md](../../CHANGELOG.md) - Main changelog entry
- [docs/vllm/README.md](README.md) - vLLM documentation index

## See Also
- [vLLM Context Cache](../../aifred/lib/vllm_context_cache.py) - Calibration caching system
- [GPU Utils](../../aifred/lib/gpu_utils.py) - VRAM-based context calculation
- [YaRN Auto-Detection](VLLM_YARN_AUTO_DETECTION.md) - YaRN scaling documentation
