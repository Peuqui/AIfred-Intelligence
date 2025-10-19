# 🌡️ LLM Model & Temperature Comparison

**Test Date:** 2025-01-19
**Test Query:** "Welche Nobelpreise wurden dieses Jahr vergeben?"
**Research Mode:** Web-Suche Ausführlich (3 Quellen)
**Sources Used:** ZDFheute, Blick.ch, Tagesschau (identical for all tests)

---

## 📊 Test Results Summary

| Model | Quantization | Temp | Inference Time | Accuracy | Hallucinations | Rating | Recommendation |
|-------|-------------|------|----------------|----------|----------------|--------|----------------|
| **deepseek-r1:8b-0528-qwen3** | Q8 | 0.8 | ~45s | ❌ Poor | ⚠️⚠️⚠️ Severe | 2/10 | ❌ NICHT verwenden |
| **deepseek-r1:8b-0528-qwen3** | Q8 | 0.2 | ~45s | ❌ Poor | ⚠️⚠️⚠️ Severe | 2/10 | ❌ NICHT verwenden |
| **gemma2:9b-instruct** | Q8 | 0.8 | ~40s | ⚠️ Medium | ⚠️ Medium | 6/10 | ⚠️ Nur mit Temp 0.2 |
| **gemma2:9b-instruct** | Q8 | 0.2 | ~40s | ✅ Good | ✅ None | 8/10 | ✅ OK für schnelle Recherche |
| **qwen2.5:14b** | Q4 | 0.2 | **33s** | ✅ Excellent | ✅ None | **8.5/10** | ⭐ **EMPFOHLEN** (beste Balance) |
| **qwen2.5:14b-instruct** | Q8 | 0.8 | 84s | ⚠️ Medium | ⚠️ Category errors | 7/10 | ⚠️ Zu langsam |
| **qwen2.5:14b-instruct** | Q8 | 0.2 | 62s | ✅ Perfect | ✅ None | **9.5/10** | ✅ BESTE QUALITÄT (aber langsam) |

---

## 🔬 Detailed Test Results

### 1. DeepSeek-R1:8b-0528-qwen3-q8_0

**Temperature 0.8:**
```
Inference: ~45s
VRAM: 8.9 GB (fits in 12GB)
```

**Hallucinations Found:**
- ❌ **Invented Names:** "Alice und Bob Johnson" (klassische Krypto-Placeholder-Namen!)
- ❌ **False Nobel Count:** "8 Nobelpreise" (es gibt nur 6 Kategorien)
- ❌ **False Death Story:** "starb durch Explosion" (Nobel starb friedlich)
- ❌ **Invented Date:** "7. Oktober 2025" (nicht in Quellen)
- ❌ **Category Confusion:** Physik/Medizin gemischt

**User Feedback:** *"Das ist kein besonders tolles Modell, ja."*

**Temperature 0.2:**
```
Inference: ~45s
Result: KEINE VERBESSERUNG - identische Halluzinationen
```

**Conclusion:**
⛔ **NICHT GEEIGNET für faktische Recherche!** Temperature-Reduktion hat KEINEN Effekt auf Halluzinationen. Das Reasoning-Feature führt zu Spekulation und Konfabulation.

---

### 2. Gemma2:9b-instruct-q8_0

**Temperature 0.8:**
```
Inference: ~40s
VRAM: 9.8 GB (fits in 12GB)
```

**Hallucinations Found:**
- ⚠️ **Währung falsch:** "über 800'000 Franken" (Quelle sagt: "11 Millionen SEK" ≈ "1 Million Euro")
- ✅ Keine erfundenen Namen
- ✅ Kategorien korrekt

**Temperature 0.2:**
```
Inference: ~40s
Result: DEUTLICH BESSER
```

**Improvements:**
- ✅ Keine Währungs-Halluzinationen mehr
- ✅ Konservativer in Aussagen
- ✅ Ehrlich bei fehlenden Details ("nicht spezifiziert")

**Rating:** 8/10 - Gute Faktentreue mit Temp 0.2

---

### 3. qwen2.5:14b (Q4 Quantization)

**Temperature 0.2:**
```
Inference: 33s ⚡ (SCHNELLSTES Modell!)
VRAM: ~8-9 GB (fits in 12GB)
```

**Performance:**
- ✅ **Keine Halluzinationen**
- ✅ Ehrlich bei fehlenden Namen: "Namen der Preisträger sind laut den Quellen nicht spezifiziert"
- ✅ Korrekte Fakten (11 Mio SEK, Standard-Nobelpreis-Zeitplan)
- ✅ Keine Kategorie-Verwirrung
- ✅ **SCHNELLSTE Inferenz** (33s!)

**Rating:** 8.5/10 - **BESTE SPEED/QUALITY BALANCE**

**Empfehlung:** ⭐ **DEFAULT MODEL** für AIfred Intelligence

---

### 4. qwen2.5:14b-instruct-q8_0

**Temperature 0.8:**
```
Inference: 84s (LANGSAMSTES Modell)
VRAM: 15 GB (OVERFLOW → nutzt System-RAM)
```

**Issues:**
- ⚠️ **Kategorie-Verwirrung:** Physik-Nobelpreis als "Physiologie oder Medizin" bezeichnet
- ⚠️ **2x langsamer** als Q4 wegen RAM-Overflow
- ✅ Ansonsten korrekte Fakten

**Temperature 0.2:**
```
Inference: 62s (immer noch langsam)
Result: PERFEKTE QUALITÄT
```

**Improvements:**
- ✅ **Keine Kategorie-Fehler mehr**
- ✅ **Perfekte Faktentreue** (9.5/10)
- ✅ Ehrlich bei fehlenden Details
- ✅ Keine Halluzinationen
- ❌ **Aber:** 2x langsamer als Q4 (62s vs 33s)

**Rating:** 9.5/10 - Beste Qualität, aber Geschwindigkeit leidet

---

## 🔍 Key Findings

### 1. Temperature Impact

| Temperature | Effect | Use Case |
|------------|--------|----------|
| **0.0** | Deterministisch, identische Ausgaben | Debugging, Tests |
| **0.2** | ⭐ **Faktentreu, konservativ** | **RECHERCHE (empfohlen)** |
| **0.8** | Kreativ, variiert, risikoreich | Kreative Tasks (Gedichte, Brainstorming) |
| **1.5+** | Sehr kreativ, unpredictable | Experimentelles Schreiben |

**Wichtig:** Bei **DeepSeek-R1** hat Temperature KEINEN Effekt auf Halluzinationen!

### 2. Q8 vs Q4 Quantization

| Aspect | Q4 | Q8 |
|--------|----|----|
| **Qualität** | Gut (8.5/10) | Exzellent (9.5/10) |
| **Geschwindigkeit** | ⚡ Schnell (33s) | 🐢 Langsam (62s) |
| **VRAM** | ~8-9 GB | ~15 GB (Overflow!) |
| **RAM-Overflow** | ❌ Nein | ✅ Ja (nutzt System-RAM) |
| **Empfehlung** | ⭐ **Standard** | Nur für höchste Qualität |

**Conclusion:** Q4 mit Temp 0.2 ist **ausreichend** für faktische Recherche!

### 3. Model Recommendations

**Für Web-Recherche (faktische Aufgaben):**
1. ⭐ **qwen2.5:14b** (Q4, Temp 0.2) - **BESTE WAHL**
2. ✅ gemma2:9b-instruct-q8_0 (Temp 0.2) - Schnelle Alternative
3. ⚠️ qwen2.5:14b-instruct-q8_0 (Temp 0.2) - Nur wenn Zeit keine Rolle spielt
4. ❌ **deepseek-r1:8b** - **NICHT verwenden** (severe hallucinations)

**Für kreative Aufgaben (Gedichte, Brainstorming):**
- Alle Modelle mit Temp 0.8-1.5 OK (außer DeepSeek-R1)

---

## 🚫 Anti-Hallucination System Prompts

**Implementiert in:** `lib/agent_core.py` (Lines 498-504, 333-340)

```python
# 🚫 ABSOLUTES VERBOT - NIEMALS ERFINDEN:
- ❌ KEINE Namen von Personen, Preisträgern, Wissenschaftlern (außer explizit in Quellen genannt!)
- ❌ KEINE Daten, Termine, Jahreszahlen (außer explizit in Quellen genannt!)
- ❌ KEINE Entdeckungen, Erfindungen, wissenschaftliche Details (außer explizit beschrieben!)
- ❌ KEINE Zahlen, Statistiken, Messungen (außer explizit in Quellen!)
- ❌ KEINE Zitate oder wörtliche Rede (außer explizit zitiert!)
- ⚠️ BEI UNSICHERHEIT: "Laut den Quellen ist [Detail] nicht spezifiziert"
- ❌ NIEMALS aus Kontext "raten" oder "folgern" was gemeint sein könnte!
```

**Effektivität:**
- ✅ **Funktioniert** bei Qwen2.5, Gemma2
- ❌ **Funktioniert NICHT** bei DeepSeek-R1 (Reasoning-Modell spekuliert trotzdem)

---

## 📈 Performance Comparison Chart

```
Inference Time (seconds):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

qwen2.5:14b Q4 (Temp 0.2)          ████████████ 33s ⚡ FASTEST
gemma2:9b Q8 (Temp 0.2)             █████████████ 40s
deepseek-r1:8b Q8 (any temp)        ██████████████ 45s
qwen2.5:14b-instruct Q8 (Temp 0.2)  ███████████████████ 62s
qwen2.5:14b-instruct Q8 (Temp 0.8)  ██████████████████████████ 84s 🐢 SLOWEST

Factual Accuracy (Rating):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

qwen2.5:14b-instruct Q8 (Temp 0.2)  ██████████████████ 9.5/10 ⭐ BEST
qwen2.5:14b Q4 (Temp 0.2)           █████████████████ 8.5/10 ⭐ EMPFOHLEN
gemma2:9b Q8 (Temp 0.2)             ████████████████ 8/10
qwen2.5:14b-instruct Q8 (Temp 0.8)  ██████████████ 7/10
gemma2:9b Q8 (Temp 0.8)             ████████████ 6/10
deepseek-r1:8b Q8 (any temp)        ████ 2/10 ❌ WORST
```

---

## 🎯 Final Configuration Recommendation

**Default Settings (implemented in `lib/config.py`):**
```python
DEFAULT_SETTINGS = {
    "model": "qwen2.5:14b",      # Q4 quantization
    "temperature": 0.2,          # Factual accuracy
    # ...
}
```

**Reasoning:**
- ⚡ **Schnellste Inferenz** (33s) unter allen getesteten Modellen
- ✅ **Hohe Faktentreue** (8.5/10) ohne Halluzinationen
- 💾 **Passt ins VRAM** (kein RAM-Overflow)
- 🎯 **Beste Speed/Quality Balance** für Recherche-Tasks

---

## 🔮 Future Work: Adaptive Temperature

**Planned Feature:** Automatische Temperature-Anpassung basierend auf User-Intent

**Hybrid Approach:**
1. **Keyword-basierte Erkennung** (schnell):
   - "Wetter", "News", "aktuell", "Nobelpreis" → Temp 0.2 (Recherche)
   - "Gedicht", "kreativ", "Geschichte", "erfinde" → Temp 0.8+ (Kreativ)

2. **LLM-basierte Intent Detection** (bei Unsicherheit):
   - Fallback wenn Keywords nicht eindeutig
   - qwen3:1.7b klassifiziert Intent (~2-3s overhead)

3. **Default für Research Mode:**
   - Web-Suche Schnell/Ausführlich → Temp 0.2 (fest)
   - Eigenes Wissen → Adaptive (Keyword + LLM)

**Implementation Status:** 📋 TODO (siehe TODO.md)

---

**Last Updated:** 2025-01-19 03:00 CET
**Tested By:** User (mp)
**Test Duration:** ~2 hours (extensive cross-model comparison)
