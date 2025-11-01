# 🤖 AIfred Intelligence - Model Overview

**Last Updated:** 2025-10-21
**Document Version:** 1.1

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

**Alternative:** qwen3:32b-q4_K_M (größeres Modell für komplexe Aufgaben)
- 📊 Rating: 9/10
- ⚡ Inference: ~90s+
- 🎯 VRAM: ~20 GB (Q4)
- ✅ Hervorragendes Reasoning
- ⚠️ Langsamer, aber beste Qualität

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
| gemma2:9b-instruct-q8_0 | 9B | Q8 | ~10 GB | 40s | 8/10 | Alternative zu Qwen |
| gemma2:9b | 9B | Q4 | ~5 GB | 30s | 7/10 | Schnell, weniger genau |
| qwen3:32b-q4_K_M | 32B | Q4 | ~20 GB | 90s+ | 9/10 | Hohe Qualität, langsam |

### 2. Automatik/Helper Models
Klein, schnell, optimiert für spezifische Hilfs-Aufgaben.

| Model | Size | VRAM | Speed | Use Case |
|-------|------|------|-------|----------|
| **qwen2.5:3b** | 3B | ~2 GB | 2-3s | Intent-Detection, Query-Opt, URL-Rating |
| qwen3:1.7b | 1.7B | ~1 GB | 1-2s | Ultra-schnelle Classification |
| qwen3:1.7b-fp16 | 1.7B | ~4 GB | 1-2s | FP16 Präzision für höhere Accuracy |
| qwen3:0.6b | 0.6B | ~0.5 GB | <1s | Kleinster Qwen3, minimale Tasks |
| qwen3:0.6b-fp16 | 0.6B | ~1.5 GB | <1s | FP16 für Edge-Devices mit Präzision |

### 3. Reasoning Models
⚠️ **HINWEIS:** DeepSeek-R1 Modelle wurden entfernt aufgrund massiver Halluzinationen bei faktischen Recherchen (14,3% Hallucination Rate laut Vectara Tests 2025).

**Alternative:** Nutze `qwen3:32b-q4_K_M` für komplexes Reasoning ohne Halluzinationen.

### 4. FP16 High-Precision Models
Full-Precision Modelle ohne Quantisierung für maximale Genauigkeit (langsamer, mehr VRAM).

| Model | Size | VRAM | Use Case |
|-------|------|------|----------|
| qwen3:8b-fp16 | 8B | ~16 GB | Maximale Präzision für kritische Tasks |
| qwen3:4b-fp16 | 4B | ~8 GB | Thinking Model in FP16 für komplexes Reasoning |
| qwen3:1.7b-fp16 | 1.7B | ~4 GB | Intent-Detection mit höchster Accuracy |
| qwen3:0.6b-fp16 | 0.6B | ~1.5 GB | Mini-Model mit FP16 Präzision |

**Wann FP16 verwenden?**
- ✅ Wenn Q4/Q8 Quantisierung zu ungenau ist
- ✅ Für wissenschaftliche/medizinische Anwendungen
- ✅ Wenn VRAM verfügbar und Präzision wichtiger als Speed
- ❌ **NICHT** für normale Web-Recherche (Q4/Q8 reicht!)

### 5. Coding/Development Models
Spezialisiert auf Code-Generierung und technische Dokumentation.

| Model | Size | VRAM | Use Case | Rating |
|-------|------|------|----------|--------|
| **qwen2.5-coder:14b-instruct-q4_K_M** | 14B | ~9 GB | **EMPFOHLEN** - Code-Gen, Refactoring, Debugging | ⭐ 9/10 |
| deepseek-coder-v2:16b | 16B | ~10 GB | Code-Generierung, Architektur | 8/10 |
| qwen2.5-coder:0.5b | 0.5B | ~0.5 GB | Schnelles Code-Completion | 6/10 |

**Besonderheit qwen2.5-coder:14b:**
- ✅ Speziell auf Coding trainiert (5.5T Code-Tokens)
- ✅ Unterstützt 92 Programmiersprachen
- ✅ Weniger Halluzinationen als DeepSeek-R1
- ✅ Exzellente Code-Completion und Debugging
- ✅ Passt perfekt auf RTX 3060 12GB

### 6. Multimodal Models
Text + Vision kombiniert.

| Model | Size | VRAM | Use Case |
|-------|------|------|----------|
| qwen2.5vl:7b-fp16 | 7B | ~14 GB | Vision + Text (Bildanalyse) |

### 7. Legacy/General Models
Nicht speziell optimiert, aber vielseitig.

| Model | Size | VRAM | Notes |
|-------|------|------|-------|
| command-r:latest | 35B | ~18 GB | RAG-optimiert, 128K Context, zitiert Quellen |
| llama3.1:8b | 8B | ~5 GB | Meta's Allround-Modell, solide Baseline |
| mistral:latest | 7B | ~4.4 GB | Instruction-Following, Code-optimiert |
| llama3.2:3b | 3B | ~2 GB | Schnell, aber ignoriert oft Context |

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

### qwen2.5-coder:14b-instruct-q4_K_M ⭐ **NEU**

**Entwickler:** Qwen Team (Alibaba Cloud)
**Release:** September 2024
**Training Data:** 5.5 Trillion Code-Tokens
**Context Window:** 128K tokens

**Technische Details:**
- Spezialisiertes Coding-Modell basierend auf Qwen 2.5
- Trainiert auf 92 Programmiersprachen
- Q4_K_M Quantisierung für optimale Balance
- Exzellentes Instruction-Following für Code-Tasks

**VRAM Requirements:**
- Q4_K_M: ~9 GB
- Q8: ~17 GB

**Benchmarks:**
- HumanEval (Python): 88.7%
- MBPP (Python): 83.5%
- LiveCodeBench: 42.3% pass@1
- MultiPL-E (Avg): 78.9%

**Pros:**
- ✅ Speziell für Code-Generierung optimiert
- ✅ Unterstützt 92 Programmiersprachen
- ✅ Weniger Halluzinationen als DeepSeek-R1
- ✅ Passt perfekt auf RTX 3060 12GB
- ✅ Exzellente Code-Completion & Debugging
- ✅ 128K Context für große Codebases

**Cons:**
- ⚠️ Fokus auf Code, weniger gut für allgemeine Texte
- ⚠️ Größer als kleinere Coder-Modelle

**EMPFEHLUNG:**
✅ **PERFEKT für Entwicklung auf RTX 3060 12GB!** Ideal für Code-Reviews, Refactoring, Debugging.

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

### Your Setup: RTX 3060 12GB + 64GB RAM ⭐ **OPTIMAL**
**Das ist deine aktuelle Konfiguration - perfekt für Entwicklung!**

**Empfohlene Modelle:**
- ✅ **qwen2.5-coder:14b-q4_K_M** (~9 GB) - Hauptmodell für Coding
- ✅ **qwen2.5:14b** (~9 GB) - Hauptmodell für Web-Recherche
- ✅ **qwen2.5:3b** (~2 GB) - Automatik/Helper
- ✅ **Gesamt:** ~11 GB VRAM (1GB Reserve für System)

**Was NICHT passt:**
- ❌ Q8 Varianten (15-17 GB) - zu groß
- ❌ qwen3:32b ohne Optimierung - braucht Layer-Limit
- ❌ FP16 Modelle - zu groß

**Perfekte Kombination für RTX 3060 12GB:**
```bash
# Coding & Development
ollama pull qwen2.5-coder:14b-instruct-q4_K_M  # 9 GB

# Web-Recherche (alternativ)
ollama pull qwen2.5:14b  # 9 GB

# Helper Tasks
ollama pull qwen2.5:3b  # 2 GB
```

### Minimum Requirements
- **GPU:** NVIDIA RTX 3060 (12GB VRAM)
- **RAM:** 32 GB System RAM
- **VRAM:** 12 GB für qwen2.5:14b Q4 + qwen2.5:3b gleichzeitig

### Recommended Setup (dein Setup!)
- **GPU:** NVIDIA RTX 3060 (12GB VRAM)
- **RAM:** 64 GB System RAM
- **CPU:** Ryzen 9900X3D
- **Models:** qwen2.5-coder:14b, qwen2.5:14b, qwen2.5:3b
- **VRAM:** 12 GB - perfekt für Q4 Modelle

### High-End Setup
- **GPU:** NVIDIA RTX 4090 (24GB VRAM)
- **RAM:** 128 GB System RAM
- **Models:** Alle Modelle, inkl. qwen3:32b und Q8 Varianten

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

### For Coding & Development
**USE:**
1. **qwen2.5-coder:14b** (beste Balance, 92 Sprachen) ⭐ **EMPFOHLEN**
2. deepseek-coder-v2:16b (architektur-fokussiert)
3. qwen2.5:14b (solide Allrounder)

### For Math & Reasoning
**USE:**
1. **qwen3:32b** (beste Reasoning-Fähigkeiten)
2. qwen2.5:14b (gute Balance)
3. gemma2:9b (kompakt, gut)

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

### ⭐ Your Setup: RTX 3060 12GB + 64GB RAM (DEIN SYSTEM!)
```bash
# Coding & Development (Hauptmodell)
ollama pull qwen2.5-coder:14b-instruct-q4_K_M  # 9 GB

# Web-Recherche & Allgemein
ollama pull qwen2.5:14b  # 9 GB

# Helper/Automatik Tasks
ollama pull qwen2.5:3b  # 2 GB

# Power-Modell (für CPU, langsam aber beste Qualität)
ollama pull qwen3:32b-q4_K_M  # 20 GB (nutzt RAM)

# VERDICT: PERFEKT für deine Hardware!
# Du kannst zwischen Coding/Research wechseln
# qwen2.5:3b läuft parallel als Helper
```

### Scenario: RTX 4090 (24GB VRAM)
```bash
# Haupt-LLM: Beste Qualität
ollama pull qwen3:32b-q4_K_M  # 20 GB

# Coding-LLM
ollama pull qwen2.5-coder:14b-instruct-q4_K_M  # 9 GB

# Automatik-LLM
ollama pull qwen2.5:3b  # 2 GB

# TOTAL VRAM: Kann auch Q8 Varianten nutzen
# VERDICT: Perfekt, alle Modelle laufen auf GPU
```

### Scenario: RTX 3060 Ti (8GB VRAM)
```bash
# Haupt-LLM: Kompakter
ollama pull gemma2:9b  # Q4 variant, ~5 GB

# Automatik-LLM: Minimal
ollama pull qwen3:1.7b  # 1.4 GB

# TOTAL VRAM: ~6 GB
# VERDICT: Funktioniert, 2GB Reserve
```

---

## 🔄 Migration Guide

### ✅ DURCHGEFÜHRT: DeepSeek-R1 entfernt
**Grund:** 14,3% Hallucination-Rate (Vectara Tests 2025) - ungeeignet für faktische Recherche.

**Gelöschte Modelle:**
- ❌ deepseek-r1:8b-0528-qwen3-q8_0 (8.9 GB)
- ❌ deepseek-r1:8b (5.2 GB)
- ❌ qwen2.5:14b-instruct-q8_0 (15 GB) - zu groß für 12GB VRAM

**Freigegebener Speicherplatz:** ~29 GB

### 🆕 Neues Modell hinzugefügt
**qwen2.5-coder:14b-instruct-q4_K_M**
- ✅ Speziell für Coding optimiert
- ✅ 92 Programmiersprachen
- ✅ Weniger Halluzinationen
- ✅ Passt perfekt auf RTX 3060 12GB

**Migration:**
```bash
# Altes DeepSeek-R1 entfernen (ERLEDIGT)
ollama rm deepseek-r1:8b-0528-qwen3-q8_0
ollama rm deepseek-r1:8b

# Neues Coding-Modell installieren (LÄUFT GERADE)
ollama pull qwen2.5-coder:14b-instruct-q4_K_M
```

**Expected Improvement:**
- ✅ 85% weniger Halluzinationen (14.3% → <2%)
- ✅ Bessere Code-Qualität
- ✅ Mehr VRAM verfügbar (29 GB gespart)

---

## 📚 Further Reading

- [Qwen2.5 Official Announcement](https://qwenlm.github.io/blog/qwen2.5/)
- [Gemma 2 Technical Report](https://storage.googleapis.com/deepmind-media/gemma/gemma-2-report.pdf)
- [DeepSeek-R1 Paper (arXiv)](https://arxiv.org/abs/2501.12948)
- [Ollama Model Library](https://ollama.com/library)

---

**Maintained by:** User (mp)
**For Issues:** [GitHub Issues](https://github.com/Peuqui/AIfred-Intelligence/issues)
