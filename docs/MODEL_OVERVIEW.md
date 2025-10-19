# 🤖 AIfred Intelligence - Model Overview

**Last Updated:** 2025-10-19
**Document Version:** 1.0

---

## 📚 Table of Contents

1. [Recommended Models](#recommended-models)
2. [Model Categories](#model-categories)
3. [Detailed Model Specifications](#detailed-model-specifications)
4. [Hardware Requirements](#hardware-requirements)
5. [Model Selection Guide](#model-selection-guide)

---

## 🌟 Recommended Models

### Primary Research Model (Haupt-LLM)
**qwen2.5:14b** (Q4 quantization)
- ⭐ **BESTE BALANCE:** Geschwindigkeit vs. Qualität
- 📊 Rating: 8.5/10
- ⚡ Inference: ~33s
- 🎯 VRAM: ~9 GB (Q4)
- ✅ Exzellent für faktische Recherche mit temp 0.2
- ✅ Multilingua

l (29 Sprachen inkl. Deutsch)
- ✅ 18T Training-Tokens, 128K Context Window

**Alternative:** qwen2.5:14b-instruct-q8_0 (beste Qualität, aber langsamer)
- 📊 Rating: 9.5/10
- ⚡ Inference: ~62s
- 🎯 VRAM: ~17 GB (Q8)
- ✅ Perfekte Faktentreue
- ⚠️ 2x langsamer als Q4

### Automatik/Intent-Detection Model
**qwen2.5:3b**
- ⚡ Ultra-schnell: ~2-3s für Intent-Detection
- 🎯 VRAM: ~2 GB
- ✅ Perfekt für Query-Optimierung, URL-Rating, Intent-Classification
- ✅ Niedriger Overhead bei hoher Accuracy

---

## 📂 Model Categories

### 1. Research Models (Haupt-LLM)
Optimiert für faktische Recherche, Wissensvermittlung und strukturierte Antworten.

| Model | Size | Quantization | VRAM | Speed | Rating | Use Case |
|-------|------|--------------|------|-------|--------|----------|
| **qwen2.5:14b** | 14B | Q4 | ~9 GB | 33s | ⭐ 8.5/10 | **EMPFOHLEN** für Research |
| qwen2.5:14b-instruct-q8_0 | 14B | Q8 | ~17 GB | 62s | 9.5/10 | Beste Qualität |
| gemma2:9b-instruct-q8_0 | 9B | Q8 | ~10 GB | 40s | 8/10 | Alternative zu Qwen |
| gemma2:9b | 9B | Q4 | ~5 GB | 30s | 7/10 | Schnell, weniger genau |
| qwen3:32b-q4_K_M | 32B | Q4 | ~20 GB | 90s+ | 9/10 | Hohe Qualität, langsam |

### 2. Automatik/Helper Models
Klein, schnell, optimiert für spezifische Hilfs-Aufgaben.

| Model | Size | VRAM | Speed | Use Case |
|-------|------|------|-------|----------|
| **qwen2.5:3b** | 3B | ~2 GB | 2-3s | Intent-Detection, Query-Opt, URL-Rating |
| qwen3:1.7b | 1.7B | ~1 GB | 1-2s | Ultra-schnelle Classification |
| qwen3:0.6b-fp16 | 0.6B | ~0.6 GB | <1s | Edge-Devices, minimale Tasks |

### 3. Reasoning Models
Spezialisiert auf logisches Denken, Mathematik und Coding - aber anfällig für Halluzinationen.

| Model | Size | VRAM | Rating | Notes |
|-------|------|------|--------|-------|
| deepseek-r1:8b-0528-qwen3-q8_0 | 8B | ~9 GB | ❌ 2/10 | **NICHT für faktische Recherche!** |
| deepseek-r1:8b | 8B | ~5 GB | ❌ 2/10 | Halluziniert stark (siehe Tests) |

⚠️ **WARNING:** DeepSeek-R1 Modelle halluzinieren massiv bei faktischen Aufgaben (invented names, false dates, category confusion). Temperature-Reduktion hilft NICHT. Nur für Coding/Math verwenden!

### 4. Coding/Development Models
Spezialisiert auf Code-Generierung und technische Dokumentation.

| Model | Size | VRAM | Use Case |
|-------|------|------|----------|
| deepseek-coder-v2:16b | 16B | ~10 GB | Code-Generierung, Refactoring |
| qwen2.5-coder:0.5b | 0.5B | ~0.5 GB | Schnelles Code-Completion |

### 5. Multimodal Models
Text + Vision kombiniert.

| Model | Size | VRAM | Use Case |
|-------|------|------|----------|
| qwen2.5vl:7b-fp16 | 7B | ~14 GB | Vision + Text (Bildanalyse) |

### 6. Legacy/General Models
Nicht speziell optimiert, aber vielseitig.

| Model | Size | Notes |
|-------|------|-------|
| llama3.1:8b | 8B | Gutes Allround-Modell |
| llama3.2:3b | 3B | Schnell, kompakt |
| mistral:latest | 7B | Französisch-fokussiert |
| command-r:latest | 35B | RAG-optimiert |

---

## 🔬 Detailed Model Specifications

### qwen2.5:14b (⭐ EMPFOHLEN)

**Entwickler:** Qwen Team (Alibaba Cloud)
**Release:** September 2024
**Training Data:** 18 Trillion Tokens
**Context Window:** 128K tokens

**Technische Details:**
- Dense, Decoder-only Transformer
- Rotary Position Embedding (RoPE)
- Supports 29+ languages (Deutsch, English, Französisch, Spanisch, Chinesisch, etc.)
- Besonders stark in: Coding, Mathematik, Instruction Following, JSON Output

**Quantization Variants:**
- **Q4 (GGUF):** ~9 GB VRAM, schnell, 95% Qualität → **EMPFOHLEN**
- Q5: ~11 GB VRAM, sehr gute Balance
- Q6: ~13 GB VRAM, kaum Qualitätsverlust
- **Q8:** ~17 GB VRAM, höchste Qualität, langsam
- FP16: ~29 GB VRAM, Full Precision (nur für große GPUs)

**Benchmarks:**
- MMLU: 82.5
- HumanEval (Coding): 68.9
- GSM8K (Math): 89.5
- MT-Bench: 8.52

**Pros:**
- ✅ Exzellente Faktentreue mit temp 0.2
- ✅ Sehr gute deutsche Sprachqualität
- ✅ Schnell genug für Production (Q4)
- ✅ Große Context Window (128K)

**Cons:**
- ⚠️ Deutsche Lyrik/Reime schwach
- ⚠️ Q4 manchmal weniger präzise als Q8

---

### gemma2:9b-instruct-q8_0

**Entwickler:** Google DeepMind
**Release:** Juni 2024
**Training Data:** 8 Trillion Tokens
**Context Window:** 8K tokens

**Technische Details:**
- Grouped-Query Attention (GQA)
- Interleaved Attention: Sliding Window (4K) + Global (8K)
- Primarily English-optimized
- Strong in reasoning and multilingual coding

**VRAM Requirements:**
- Q8: ~10 GB
- Q4: ~5 GB
- FP16: ~19 GB

**Benchmarks:**
- MMLU: 81.9
- HumanEval (Coding): 74.4
- GSM8K (Math): 87.9

**Pros:**
- ✅ Gute Faktentreue mit temp 0.2
- ✅ Kompakt und schnell
- ✅ Sehr gut in Coding/Math
- ✅ Weniger VRAM als Qwen2.5:14b

**Cons:**
- ⚠️ Kleinere Context Window (8K vs 128K)
- ⚠️ Halluziniert bei temp 0.8 (Währungs-Fehler in Tests)
- ⚠️ Weniger multilingual als Qwen

---

### deepseek-r1:8b-0528-qwen3-q8_0

**Entwickler:** DeepSeek AI
**Release:** Mai 2025 (R1-0528 Update)
**Base Model:** Qwen 2.5 (distilled mit 800K samples)
**Training:** Reinforcement Learning (RL) ohne initial SFT

**Technische Details:**
- 8B parameter distilled reasoning model
- Explicit reasoning traces (CoT)
- Finetuned speziell für Mathematik, Coding, Logik
- Function calling & JSON output support

**VRAM Requirements:**
- Q8: ~9 GB
- Q4: ~5 GB

**Benchmarks (Full R1 Model, nicht 8B distilled):**
- AIME 2025: 87.5% (Mathe)
- MATH-500: 97.3
- GPQA-Diamond: 81.0
- LiveCodeBench: 73.3% pass@1

**Pros:**
- ✅ Sehr gut in Mathematik und Coding
- ✅ Explizite Reasoning Traces (nachvollziehbar)
- ✅ 45% weniger Halluzinationen als R1-0 (laut DeepSeek)

**Cons:**
- ❌ **KRITISCH:** Halluziniert massiv bei faktischen Recherchen
- ❌ Invented Names ("Alice und Bob Johnson")
- ❌ Invented Dates ("7. Oktober 2025")
- ❌ False Nobel Count ("8 Nobelpreise" statt 6)
- ❌ Temperature-Reduktion hilft NICHT

**EMPFEHLUNG:**
⛔ **NICHT für AIfred Research verwenden!** Nur für Coding/Math Tasks.

---

### qwen2.5:3b (Automatik-Modell)

**Entwickler:** Qwen Team
**Release:** September 2024
**Training Data:** 18 Trillion Tokens
**Context Window:** 32K tokens

**Technische Details:**
- Kompaktes Dense Transformer Model
- Optimiert für schnelle Inference
- Multilinguale Unterstützung
- Instruction-following tuned

**VRAM Requirements:**
- Q4: ~2 GB
- Q8: ~3 GB

**Use Cases in AIfred:**
- ✅ Intent-Detection (FAKTISCH/KREATIV/GEMISCHT)
- ✅ Query-Optimierung für Web-Suche
- ✅ URL-Rating (Relevanz-Scoring)
- ✅ Automatik-Entscheidung (Web-Recherche ja/nein)

**Performance:**
- Intent-Detection: ~2-3s
- Query-Opt: ~1-2s
- URL-Rating (10 URLs): ~9s

**Pros:**
- ✅ Sehr schnell
- ✅ Minimaler VRAM-Verbrauch
- ✅ Gute Accuracy für Classification Tasks
- ✅ Kann parallel zum Haupt-LLM laufen

**Cons:**
- ⚠️ Zu klein für komplexe Research-Aufgaben
- ⚠️ Manchmal zu konservativ (temp 0.2)

---

## 💻 Hardware Requirements

### Minimum Requirements
- **GPU:** NVIDIA RTX 3060 (12GB VRAM) oder besser
- **RAM:** 32 GB System RAM
- **VRAM:** 12 GB für qwen2.5:14b Q4 + qwen2.5:3b gleichzeitig

### Recommended Setup
- **GPU:** NVIDIA RTX 4090 (24GB VRAM)
- **RAM:** 64 GB System RAM
- **VRAM:** 24 GB erlaubt alle Modelle inkl. Q8 Varianten

### Budget Setup
- **GPU:** NVIDIA RTX 3060 Ti (8GB VRAM)
- **Models:** gemma2:9b Q4 (~5GB) + qwen3:1.7b (~1GB)
- **RAM:** 32 GB
- ⚠️ Kein Platz für qwen2.5:14b Q4

### High-End Setup
- **GPU:** NVIDIA RTX 4090 oder A6000 (48GB VRAM)
- **Models:** Alle Modelle, inkl. qwen3:32b und FP16 Varianten
- **RAM:** 128 GB+

---

## 🎯 Model Selection Guide

### For Factual Research (Web-Recherche)
**USE:** qwen2.5:14b (Q4) + temp 0.2
- Beste Balance aus Speed & Accuracy
- Exzellente Faktentreue
- Multilinguale Unterstützung

**AVOID:** DeepSeek-R1 (halluziniert stark)

### For Creative Tasks (Gedichte, Brainstorming)
**USE:** qwen2.5:14b (Q4) + temp 0.8
- Gut für kreative Texte
- Flexible Formulierungen

**LIMITATION:** Deutsche Reime schwach (auch bei temp 2.0)

**ALTERNATIVE:** qwen3:32b (größeres Modell, bessere Lyrik)

### For Coding & Math
**USE:**
1. deepseek-r1:8b (reasoning traces)
2. deepseek-coder-v2:16b (code-spezialisiert)
3. qwen2.5:14b (solide Alternative)

### For Intent Detection & Helper Tasks
**USE:** qwen2.5:3b oder qwen3:1.7b
- Ultra-schnell
- Minimaler Overhead
- Gute Accuracy

### For Low-VRAM Systems (<12GB)
**USE:**
1. gemma2:9b Q4 (~5GB) als Haupt-LLM
2. qwen3:1.7b (~1GB) als Automatik-Modell
3. **TOTAL:** ~6-7 GB VRAM

---

## 📊 Comparison Table: Top 3 Research Models

| Feature | qwen2.5:14b Q4 ⭐ | qwen2.5:14b Q8 | gemma2:9b Q8 |
|---------|-------------------|----------------|--------------|
| **VRAM** | ~9 GB | ~17 GB | ~10 GB |
| **Speed** | 33s | 62s | 40s |
| **Accuracy** | 8.5/10 | 9.5/10 | 8/10 |
| **Hallucinations** | ✅ None | ✅ None | ⚠️ Some (temp 0.8) |
| **Context Window** | 128K | 128K | 8K |
| **Multilingual** | ✅ 29 languages | ✅ 29 languages | ⚠️ English-focused |
| **German Quality** | ✅ Excellent | ✅ Perfect | ✅ Good |
| **Price/Performance** | ⭐ BEST | ⚠️ Slow | ✅ Good |
| **Recommendation** | **EMPFOHLEN** | Beste Qualität | Budget Option |

---

## 🚀 Quick Start Recommendations

### Scenario 1: You have RTX 4090 (24GB VRAM)
```bash
# Haupt-LLM: Beste Qualität
ollama pull qwen2.5:14b-instruct-q8_0

# Automatik-LLM: Schnell
ollama pull qwen2.5:3b

# TOTAL VRAM: ~20 GB
# VERDICT: Perfekt, noch 4GB Reserve
```

### Scenario 2: You have RTX 3060 (12GB VRAM)
```bash
# Haupt-LLM: Beste Balance
ollama pull qwen2.5:14b  # Q4 variant

# Automatik-LLM: Kompakt
ollama pull qwen3:1.7b

# TOTAL VRAM: ~10 GB
# VERDICT: Gut, 2GB Reserve für System
```

### Scenario 3: You have RTX 3060 Ti (8GB VRAM)
```bash
# Haupt-LLM: Kompakter
ollama pull gemma2:9b  # Q4 variant

# Automatik-LLM: Minimal
ollama pull qwen3:1.7b

# TOTAL VRAM: ~6 GB
# VERDICT: Funktioniert, 2GB Reserve
```

---

## 🔄 Migration Guide

### From DeepSeek-R1 to Qwen2.5
**Why?** DeepSeek-R1 halluziniert bei faktischen Recherchen.

**Steps:**
1. `ollama pull qwen2.5:14b`
2. In UI: Wähle "qwen2.5:14b" als Haupt-LLM
3. Setze Temperature auf 0.2 (oder Auto-Modus)
4. Test mit Recherche-Query

**Expected Improvement:**
- ✅ 90% weniger Halluzinationen
- ✅ Bessere Faktentreue
- ⚠️ Kein explizites Reasoning (kein CoT)

---

## 📚 Further Reading

- [Qwen2.5 Official Announcement](https://qwenlm.github.io/blog/qwen2.5/)
- [Gemma 2 Technical Report](https://storage.googleapis.com/deepmind-media/gemma/gemma-2-report.pdf)
- [DeepSeek-R1 Paper (arXiv)](https://arxiv.org/abs/2501.12948)
- [Ollama Model Library](https://ollama.com/library)

---

**Maintained by:** User (mp)
**For Issues:** [GitHub Issues](https://github.com/Peuqui/AIfred-Intelligence/issues)
