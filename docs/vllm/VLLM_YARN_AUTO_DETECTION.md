> ⚠️ **Historical (as of 2025-11, pre state-refactor):** Code references to `aifred/state.py` (now the `aifred/state/` package) and to removed modules like `vllm_context_cache.py` no longer resolve, and hardware notes refer to the pre-migration GPU setup. **Deliberately kept:** vLLM returns as the main backend after the P40→V100 migration (see TODO.md) — these notes will be revised then. Do not delete.

# vLLM YaRN & Auto-Detection

**Datum:** 2025-11-13
**Status:** ✅ Implementiert & Getestet

---

## 🎯 Features

### 1. YaRN Context Extension (RoPE Scaling)

**Was ist YaRN?**
- **Y**et **a**nother **R**oPE e**N**largement
- Erweitert den Context durch RoPE (Rotary Position Embedding) Skalierung
- Ermöglicht längere Kontext-Fenster als das Model nativ unterstützt

**Einstellungen in UI:**
- Toggle: `enable_yarn` (Ein/Aus)
- Faktor: `yarn_factor` (1.0 - 8.0, Schritte: 0.5)
- Live-Preview: Zeigt geschätzte Tokens (`vllm_max_tokens * yarn_factor`)

**Beispiele:**
```
Basis: 26,624 tokens (RTX 3060 Hardware-Limit)
YaRN 1.5x: ~40,000 tokens (nativ)
YaRN 2.0x: ~53,000 tokens
YaRN 4.0x: ~106,000 tokens (benötigt mehr VRAM!)
```

**Wichtig:**
- ⚠️ YaRN-Faktor > 2.0 kann VRAM überschreiten → Crash-Risiko
- 🔄 Benötigt vLLM Backend-Neustart nach Änderung
- 💾 Doppelter VRAM-Verbrauch proportional zum Faktor

---

### 2. Automatische Context-Erkennung

**Problem gelöst:**
- Jede GPU hat unterschiedliche VRAM-Limits
- Hardcoded Werte (z.B. 26.608 für RTX 3060) funktionieren nicht auf anderen GPUs
- User wissen nicht, was ihr Hardware-Limit ist

**Lösung: 2-Stufen Auto-Detection**

#### Stufe 1: Native Context Versuch
```
📊 Native Context: 40,960 tokens (from config.json)
🔧 Auto-Detection: Trying native context (40,960 tokens)...
```

vLLM versucht mit nativem Model-Context zu starten (z.B. 40K für Qwen3-8B-AWQ).

#### Stufe 2: Hardware-Limit Erkennung
```
⚠️ Native context too large, detecting hardware limit...
📊 Hardware Limit detected: 26,624 tokens (VRAM-constrained)
🔄 Restarting with hardware limit...
✅ vLLM started successfully with 26,624 tokens
```

Falls VRAM nicht ausreicht:
1. Parse Error-Message: `"estimated maximum model length is 26624"`
2. Extrahiere Hardware-Limit via Regex
3. Stoppe crashed Process
4. Restart mit erkanntem Limit

**Regex Pattern:**
```python
r"(?:estimated )?maximum model length is (\d+)"
```

Matched beide Formate:
- `"Maximum model length is 26608 for this GPU"`
- `"the estimated maximum model length is 26624"`

---

## 💾 Settings Persistence

**Settings-Datei:** `~/.config/aifred/settings.json`

**Gespeicherte Werte:**
```json
{
  "enable_yarn": false,
  "yarn_factor": 1.0,
  "vllm_max_tokens": 26624,      // Auto-detected (0 = noch nicht erkannt)
  "vllm_native_context": 40960    // From config.json
}
```

**Verhalten:**

| Szenario | vllm_max_tokens | Verhalten |
|----------|-----------------|-----------|
| **First Run** | 0 (Default) | Auto-Detection → Speichert erkannten Wert |
| **Second Run** | 26624 (gespeichert) | Direkt-Start mit bekanntem Limit (kein Crash!) |
| **GPU-Wechsel** | Alte GPU-Werte | User muss Settings löschen oder neu erkennen |

**Debug-Log bei gespeichertem Wert:**
```
📋 Using saved context limit: 26,624 tokens (aus Settings)
✅ vLLM started successfully with 26,624 tokens (~40s statt ~70s)
```

---

## 🔧 Technische Details

### Code-Struktur

**1. vLLM Manager** ([aifred/lib/vllm_manager.py](../../aifred/lib/vllm_manager.py))
- `get_model_native_context()`: Liest config.json aus HuggingFace Cache
- `start_with_auto_detection()`: 2-Stufen Auto-Detection Logic
- `_read_stderr()`: Background-Thread für Error-Capture
- Regex-Parsing für Hardware-Limit Extraktion

**2. State Management** ([aifred/state.py](../../aifred/state.py))
- `vllm_max_tokens`: Hardware-constrained Context (Default: 0)
- `vllm_native_context`: Model-native Context (Default: 0)
- `enable_yarn`: YaRN Toggle
- `yarn_factor`: RoPE Scaling Factor
- `_save_settings()`: Speichert alle 4 Werte
- `on_load()`: Lädt Werte aus Settings beim Start

**3. UI** ([aifred/aifred.py](../../aifred/aifred.py))
- YaRN Toggle-Switch
- Numeric Input (1.0-8.0, step 0.5)
- Live Token Preview: `(~{vllm_max_tokens * yarn_factor} tokens)`
- Warning-Box bei `yarn_factor > 2.0`
- Info-Text: "Modell: 40K nativ | HW-Limit: 26K"

### Settings Files

**Location:** `~/.config/aifred/settings.json`
**Not in Git:** ✅ In `.gitignore` (`settings.json`, `**/settings.json`)

**Default Values** ([aifred/lib/settings.py](../../aifred/lib/settings.py)):
```python
{
    "enable_yarn": False,
    "yarn_factor": 1.0,
    "vllm_max_tokens": 0,       # 0 = auto-detect
    "vllm_native_context": 0    # 0 = auto-detect
}
```

---

## 📊 Performance

### Auto-Detection Timings (RTX 3060)

| Durchlauf | Native (40K) | Crash + Parse | Hardware (26K) | Total |
|-----------|--------------|---------------|----------------|-------|
| **First Start** | ~28s | ~2s | ~40s | **~70s** |
| **Second Start** | - | - | ~40s | **~40s** (gespeichert!) |

**Einsparung:** 30 Sekunden (43% schneller) bei jedem weiteren Start!

### YaRN Memory Usage

| YaRN Factor | Context | VRAM (geschätzt) | RTX 3060 (12GB) |
|-------------|---------|------------------|-----------------|
| 1.0x | 26,624 | ~12 GB | ✅ Optimal |
| 1.5x | ~40,000 | ~18 GB | ❌ Zu viel |
| 2.0x | ~53,000 | ~24 GB | ❌ Zu viel |

**Empfehlung für RTX 3060:** Maximal 1.0x (kein YaRN), da bereits am VRAM-Limit.

---

## 🐛 Bekannte Bugs (Fixed)

### ✅ Settings wurden nicht gespeichert
**Problem:** `vllm_max_tokens` wurde erkannt, aber nicht in Settings-Datei geschrieben.
**Fix:** `_save_settings()` & `on_load()` erweitert um YaRN/Context-Werte.

### ✅ Backend-Switch Error
**Problem:** `'LLMClient' object has no attribute 'backend'`
**Fix:** `llm_client.backend` → `llm_client._get_backend()`

### ✅ Regex Pattern Mismatch
**Problem:** vLLM Error-Format war anders als erwartet.
**Fix:** Regex von `"Maximum model length is (\d+)"` zu `"(?:estimated )?maximum model length is (\d+)"`

### ✅ Stderr nicht erfasst
**Problem:** `communicate()` funktioniert nicht auf toten Prozess.
**Fix:** Background-Thread `_read_stderr()` liest kontinuierlich in Buffer.

### ✅ Second Start hing
**Problem:** Crashed Process nicht gestoppt vor Retry.
**Fix:** Explizites `await self.stop()` vor zweitem Start-Versuch.

---

## 🎯 Future Ideas

### Progressive Debug-Output
**Problem:** Debug-Console zeigt alles erst am Ende (buffert während vLLM-Start).
**Lösung:** `_start_vllm_server()` als Generator mit `yield` nach jedem Log-Eintrag.
**Status:** ⏳ Geplant (komplexer Umbau, erstmal zurückgestellt)

### GPU-Wechsel Detection
**Problem:** Alte Settings von RTX 3060 funktionieren nicht auf RTX 4090.
**Lösung:** GPU-ID in Settings speichern, bei Wechsel Auto-Detection neu triggern.
**Status:** 💡 Idee

### YaRN-Test Button
**Problem:** User weiß nicht, ob YaRN-Faktor zu hoch ist ohne Crash.
**Lösung:** "Test"-Button, der vLLM temporär mit YaRN startet und Erfolg meldet.
**Status:** 💡 Idee

---

## 📝 Changelog

### 2025-11-13
- ✅ YaRN UI implementiert (Toggle, Factor, Live-Preview, Warning)
- ✅ Auto-Detection 2-Stufen-System (Native → Hardware-Limit)
- ✅ Settings Persistence für alle YaRN/Context-Werte
- ✅ `.gitignore` erweitert (`settings.json`, `**/settings.json`)
- ✅ Backend-Switch Error gefixt (`llm_client._get_backend()`)
- ✅ Regex Pattern erweitert (beide vLLM Error-Formate)
- ✅ Stderr Capture via Background-Thread
- ✅ Process Cleanup vor Retry

---

**Autor:** AIfred Intelligence Team
**Maintainer:** AI Assistant + mp
**Related Docs:**
- [VLLM_RTX3060_CONFIG.md](VLLM_RTX3060_CONFIG.md) - GPU-spezifische Optimierung
- [VLLM_FIX_SUMMARY.md](VLLM_FIX_SUMMARY.md) - Crash-Fix Zusammenfassung