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

## ✅ Adaptive Temperature System - IMPLEMENTIERT

**Implementation Date:** 2025-10-19
**Status:** ✅ **PRODUKTIV**

### Architektur

Das System verwendet **LLM-basierte Intent Detection** mit zwei Modi:

#### 1. Auto-Modus (Standard)
- **Web-Recherche:** Fest temp 0.2 (faktisch, kein Overhead)
- **Cache-Hit:** Intent-Detection → 0.2/0.5/0.8 (~7s Overhead)
- **Eigenes Wissen:** Intent-Detection → 0.2/0.5/0.8 (~7s Overhead)

**Intent-Detection mit qwen2.5:3b (temp 0.2):**
```python
def detect_cache_followup_intent(original_query, followup_query, automatik_model):
    """Klassifiziert Follow-up als FAKTISCH/KREATIV/GEMISCHT"""
    # Analysiert beide Queries im Kontext
    # Returns: FAKTISCH (0.2), KREATIV (0.8), GEMISCHT (0.5)
```

#### 2. Manual Override-Modus (neu)
- User setzt Temperature explizit (0.0-2.0)
- **KEINE Intent-Detection** (spart ~7s)
- Gilt für ALLE Modi (Web-Recherche, Cache-Hit, Eigenes Wissen)
- Nützlich für Testing und Power-User

### UI-Integration

**Settings:** `⚙️ LLM-Parameter (Erweitert)`
- **🎛️ Temperature Modus:** Radio-Buttons (Auto / Manual)
- **🌡️ Temperature:** Slider 0.0-2.0 (nur aktiv bei Manual)
- Settings werden persistent gespeichert

### Test-Ergebnisse (2025-10-19)

**Test-Query:** "Welche Nobelpreise wurden dieses Jahr vergeben?" (Cache erstellt)

#### Auto-Modus Tests:

| Follow-up Query | Erwarteter Intent | Erkannter Intent | Temperature | Ergebnis |
|-----------------|-------------------|------------------|-------------|----------|
| "Erkläre Quelle 1 genauer" | FAKTISCH | ✅ FAKTISCH | 0.2 | ✅ PERFEKT |
| "Schreibe Geschichte über Daten" | KREATIV | ❌ **FAKTISCH** | 0.2 | ⚠️ Falsch erkannt |
| "Schreibe Gedicht über Preisträger" | KREATIV | ✅ KREATIV | 0.8 | ✅ PERFEKT |
| "Gedicht umschreiben, dass es sich reimt" | KREATIV | ✅ KREATIV | 0.8 | ✅ PERFEKT |
| "Warum reimt's nicht?" | FAKTISCH | ✅ FAKTISCH | 0.2 | ✅ PERFEKT |

**Erfolgsquote:** 4/5 = 80%

**Problem identifiziert:**
- "Schreibe Geschichte über recherchierten Daten" wird als FAKTISCH klassifiziert
- Ursache: "recherchierten Daten" verwirrt Intent-Classifier
- Output war trotzdem kreativ (LLM folgt Prompt-Instruktion)

#### Manual Override Tests:

| Query | Manual Temp | Log Output | Ergebnis |
|-------|-------------|------------|----------|
| "Gedicht umschreiben" | 1.6 | `🌡️ Eigenes Wissen Temperature: 1.6 (MANUAL OVERRIDE)` | ✅ Funktioniert |
| "Gedicht mit Reimen" | 2.0 | `🌡️ Cache-Hit Temperature: 2 (MANUAL OVERRIDE)` | ✅ Funktioniert |

**Beobachtungen:**
- Keine Intent-Detection bei Manual (Performance-Gewinn)
- Auch temp 2.0 produziert keine korrekten deutschen Reime (Modell-Limitation)
- Manual Override für alle Modi korrekt implementiert

### Empfehlungen

**Für faktische Recherche:**
- ✅ Auto-Modus mit qwen2.5:14b (Q4)
- ✅ Temp 0.2 bei Web-Recherche (fest)
- ✅ Intent-Detection für Cache-Hits funktioniert gut

**Für kreative Aufgaben:**
- ✅ Auto-Modus erkennt "Gedicht", "Brainstorming" korrekt
- ⚠️ "Geschichte über Daten" braucht Manual Override (temp 0.8)
- ❌ Deutsche Lyrik: qwen2.5:14b hat Schwächen bei Reimen (unabhängig von Temperature)

**Für Testing:**
- ✅ Manual Override ideal um verschiedene Temps zu vergleichen
- ✅ Derselbe Cache kann mit verschiedenen Temps getestet werden

### Performance

**Intent-Detection Overhead:**
- qwen2.5:3b: ~7 Sekunden (akzeptabel)
- Nur bei Cache-Hit und Eigenes Wissen
- Kein Overhead bei Web-Recherche (fest temp 0.2)
- **Kein Overhead bei Manual Override**

**Gesamtzeit Cache-Hit:**
- Auto-Modus: ~32-43s (Intent-Detection 7s + Inference 25-36s)
- Manual-Modus: ~25-29s (nur Inference)

### Zukünftige Verbesserungen

**Intent-Detection Prompt optimieren:**
```python
# Aktuell: temp 0.2 für Konsistenz
# Testen: temp 0.3-0.5 für bessere "Geschichte"-Erkennung
```

**Alternativen für deutsche Lyrik:**
- Größeres Modell testen (qwen3:32b)
- Spezialisiertes Lyrik-Modell suchen
- Few-Shot Prompting mit Reim-Beispielen

---

**Last Updated:** 2025-10-19 15:15 CET
**Tested By:** User (mp)
**Test Duration:** ~3 hours (implementation + extensive testing)
**Implementation:** [lib/agent_core.py](../lib/agent_core.py), [aifred_intelligence.py](../aifred_intelligence.py)
