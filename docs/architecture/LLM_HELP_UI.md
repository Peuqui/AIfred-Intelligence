# LLM Model-Auswahl Hilfe (UI Version)

Diese Tabellen sind für die Anzeige in der Web-UI optimiert.

## 📊 Schnellübersicht - Modelle nach Hardware

---

### 🖥️ **MINI-PC (GEM 10)** - AMD Radeon 780M iGPU (8GB VRAM)
**Hardware:** 32GB RAM total (8GB für iGPU, 24GB System)

#### 🏆 Top-Empfehlungen für Mini-PC

| Model | Größe | GPU | Empfehlung | Bester Einsatz |
|-------|-------|-----|------------|----------------|
| **qwen2.5:7b-instruct-q4_K_M** | 4.7 GB | ✅ | ⭐⭐⭐⭐⭐ | **HAUPT-MODELL** - Beste Balance! 🆕 |
| **phi3:mini** | 2.2 GB | ✅ | ⭐⭐⭐⭐⭐ | **AIFRED AUTOMATIK** - Ultra-schnell! 🆕 |
| **llama3.1:8b** | 4.9 GB | ✅ | ⭐⭐⭐⭐ | Meta's Allrounder |
| **mistral:latest** | 4.4 GB | ✅ | ⭐⭐⭐⭐ | Code & Speed |
| **qwen2.5:3b** | 1.9 GB | ✅ | ⭐⭐⭐⭐ | AIfred Backup (32K Context) |

#### 🚀 Mini-Modelle für Tests

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **qwen2.5:0.5b** | 397 MB | ⭐⭐ | Tiny-Tests, sehr schnell |
| **qwen2.5-coder:0.5b** | 397 MB | ⭐⭐ | Mini-Code-Completion |

#### 🐘 CPU-Modelle (nutzen RAM, langsam aber beste Qualität)

| Model | Größe | CPU-only | Empfehlung | Hinweis |
|-------|-------|----------|------------|---------|
| **qwen3:32b-q4_K_M** | 20 GB | ❌ | ⭐⭐⭐⭐⭐ | **BESTE QUALITÄT** - optimierte Q4 Version! 🆕 |
| **qwen2.5:14b** | 9 GB | ❌ | ⭐⭐⭐⭐ | CPU-Backup für Qualität |
| **mixtral:8x7b** | 26 GB | ❌ | ⭐⭐⭐⭐⭐ | MoE-Champion |
| **command-r** | 18 GB | ⚠️ | ⭐⭐⭐⭐ | RAG-optimiert |

---

### 💻 **HAUPT-PC (Aragon)** - RTX 3060 12GB + Ryzen 9900X3D
**Hardware:** RTX 3060 12GB VRAM + 64GB RAM

#### 🏆 Top-Empfehlungen für Haupt-PC (RTX 3060 12GB)

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **qwen2.5-coder:14b-q4_K_M** | 9 GB | ⭐⭐⭐⭐⭐ | **CODING** (92 Sprachen, beste Code-Qualität) 🆕 |
| **qwen2.5:14b** | 9 GB | ⭐⭐⭐⭐⭐ | **Web-Recherche** (RAG Score 1.0!) |
| **qwen2.5:7b-instruct-q4_K_M** | 4.7 GB | ⭐⭐⭐⭐⭐ | **SPEED** - Schneller als 14B! 🆕 |
| **qwen3:8b** | 5.2 GB | ⭐⭐⭐⭐ | Balance: Schnell + Gut |
| **llama3.1:8b** | 4.9 GB | ⭐⭐⭐⭐ | Meta's Allrounder |
| **mistral:latest** | 4.4 GB | ⭐⭐⭐⭐ | Code & Speed |
| **phi3:mini** | 2.2 GB | ⭐⭐⭐⭐⭐ | **AIFRED AUTOMATIK** 🆕 |
| **qwen2.5:3b** | 1.9 GB | ⭐⭐⭐⭐ | AIfred Backup (32K Context) |
| **qwen2.5-coder:0.5b** | 397 MB | ⭐⭐ | Mini-Code-Tests |

#### 🐘 Große Modelle für Haupt-PC (nutzen CPU + RAM)

| Model | Größe | GPU/CPU | Empfehlung | Hinweis |
|-------|-------|---------|------------|---------|
| **qwen3:32b-q4_K_M** | 20 GB | CPU+RAM | ⭐⭐⭐⭐⭐ | **BESTE QUALITÄT** - optimierte Q4 Version! 🆕 |
| **command-r** | 18 GB | GPU+CPU | ⭐⭐⭐⭐ | Enterprise RAG, zitiert Quellen |
| **qwen2.5vl:7b-fp16** | 16 GB | CPU+RAM | ⭐⭐⭐⭐ | **VISION + Text** (Bildanalyse, FP16 Präzision) |

#### 📊 Embedding-Modelle (für RAG/Suche)

| Model | Größe | Dimensionen | Bester Einsatz |
|-------|-------|-------------|----------------|
| **mxbai-embed-large** | 669 MB | 1024 | Hochqualitative Embeddings für Suche |
| **qwen3-embedding:8b** | 4.7 GB | 8192 | Sehr große Embeddings (präzise) |

**Hinweis:** Embedding-Modelle sind KEINE Chat-Modelle! Sie konvertieren Text in Vektoren für Suche/RAG.

---

## 🎯 Empfehlungen nach Use-Case

### 💻 Coding & Development 🆕
**Empfohlen:** `qwen2.5-coder:14b-instruct-q4_K_M`
- ⭐ **BESTE WAHL für Coding!**
- 92 Programmiersprachen
- Exzellente Code-Completion & Debugging
- Passt perfekt auf RTX 3060 12GB
- Weniger Halluzinationen als DeepSeek-R1 (14.3% → <2%)
- HumanEval: 88.7% | MBPP: 83.5%

**Für Mini-Code-Tasks:** `qwen2.5-coder:0.5b`
- Ultra-schnell
- Nur 397 MB
- Gut für einfache Code-Snippets

### 💬 Web-Recherche (Haupt-Model)
**Empfohlen:** `qwen2.5:14b`
- Beste RAG-Scores (1.0 = perfekt!)
- Nutzt NUR Recherche-Daten
- Exzellent für faktische Aufgaben
- Passt perfekt auf RTX 3060 12GB

**Alternative:** `qwen3:8b`
- Schneller, weniger VRAM
- Immer noch sehr gut

### 🤖 AIfred Intelligence Automatik 🆕
**PRIMÄR:** `phi3:mini` ⭐⭐⭐⭐⭐
- ⭐ **BESTE WAHL für Automatik!**
- Hallucination-Rate: <3% (vs. DeepSeek-R1: 14.3%)
- Ultra-schnell: 40-60 tokens/sec
- Microsoft Production-Quality
- Performance wie 38B Modell!
- Nur 2.2 GB - läuft parallel zu Haupt-LLM

**BACKUP:** `qwen2.5:3b`
- 32K Context (vs. Phi3's 4K) - wichtig für längere Texte!
- Nur 1.9 GB
- Gute Fallback-Option
- Bereits installiert auf beiden Systemen

### 📚 Komplexe Reasoning-Aufgaben
**Empfohlen:** `qwen3:32b-q4_K_M`
- Beste Qualität für komplexe Probleme
- Math, Reasoning, Logik
- **RTX 3060:** Nutzt CPU + RAM (langsam, aber beste Qualität)
- **RTX 4090:** Läuft auf GPU (schnell!)

### ⚡ Maximale Geschwindigkeit
**Empfohlen:** `qwen3:0.6b` oder `qwen2.5:0.5b`
- Extrem schnell (< 2 Sek für Antwort)
- Für einfache Tasks ausreichend
- Ideal für Benchmarks

### 🏢 Enterprise / Produktion
**Empfohlen:** `command-r` oder `qwen2.5:32b`
- Beste Zuverlässigkeit
- RAG-optimiert
- Function Calling

---

## 📊 Erweiterte Vergleichs-Tabelle

### Haupt-PC (RTX 3060 12GB) - GPU-optimiert

| Model | Größe | RAG Score | Tool-Use | Speed | VRAM | Context |
|-------|-------|-----------|----------|-------|------|---------|
| **qwen2.5-coder:14b** 🆕 | 9 GB | 0.92 | 0.96 | Mittel | ✅ 9 GB | 128K |
| **qwen2.5:14b** | 9 GB | 1.0 🏆 | 0.95 | Mittel | ✅ 9 GB | 128K |
| gemma2:9b-instruct-q8_0 | 9.8 GB | 0.88 | 0.89 | Mittel | ✅ 10 GB | 8K |
| deepseek-coder-v2:16b | 8.9 GB | 0.90 | 0.94 | Mittel | ✅ 9 GB | 16K |
| qwen3:8b | 5.2 GB | 0.933 | 0.90 | Schnell | ✅ 5 GB | 128K |
| gemma2:9b | 5.4 GB | 0.82 | 0.85 | Schnell | ✅ 5 GB | 8K |
| llama3.1:8b | 4.9 GB | 0.85 | 0.88 | Schnell | ✅ 5 GB | 128K |
| mistral:latest | 4.4 GB | 0.88 | 0.85 | Schnell | ✅ 4 GB | 32K |

### Mini-PC (AMD 780M iGPU 8GB) - iGPU-optimiert

| Model | Größe | RAG Score | Tool-Use | Speed | VRAM | Context |
|-------|-------|-----------|----------|-------|------|---------|
| qwen3:8b | 5.2 GB | 0.933 | 0.90 | Schnell | ✅ 5 GB | 128K |
| gemma2:9b | 5.4 GB | 0.82 | 0.85 | Schnell | ✅ 5 GB | 8K |
| llama3.1:8b | 4.9 GB | 0.85 | 0.88 | Schnell | ✅ 5 GB | 128K |
| mistral:latest | 4.4 GB | 0.88 | 0.85 | Schnell | ✅ 4 GB | 32K |
| qwen3:4b | 2.5 GB | 0.92 | 0.88 | Sehr schnell | ✅ 3 GB | 32K |
| llama3.2:3b | 2.0 GB | ~0.70 | 0.75 | Sehr schnell | ✅ 2 GB | 128K |
| qwen2.5:3b | 1.9 GB | 0.85 | 0.80 | Sehr schnell | ✅ 2 GB | 32K |
| qwen3:1.7b | 1.4 GB | 0.80 | 0.75 | Extrem schnell | ✅ 1 GB | 32K |

### Große Modelle (CPU + RAM auf beiden Systemen)

| Model | Größe | RAG Score | Tool-Use | Speed | GPU | Context |
|-------|-------|-----------|----------|-------|-----|---------|
| qwen3:32b | 20 GB | 0.98 | 0.98 | Langsam | ❌ CPU | 128K |
| command-r | 18 GB | 0.92 | 0.95 | Langsam | ⚠️ Hybrid | 128K |
| qwen2.5vl:7b-fp16 | 16 GB | - | - | Langsam | ❌ CPU | 32K |
| qwen3:8b-fp16 | 16 GB | 0.95 | 0.92 | Mittel | ❌ CPU | 128K |

**Legende:**
- **RAG Score:** Context Adherence (1.0 = perfekt, nutzt nur Recherche-Daten)
- **Tool-Use:** Function Calling / Agent F1 Score
- **Speed:** Inferenz-Geschwindigkeit auf Mini-PC
- **RAM:** Geschätzter Speicherverbrauch
- **Context:** Natives Max Context Window

---

## 🔧 Hardware-Erkennung & GPU-Support

### 🖥️ Mini-PC: AMD Radeon 780M iGPU (8 GB VRAM effektiv)
**System:** 32GB RAM total (8GB für iGPU reserviert, 24GB System)

**✅ Läuft perfekt auf iGPU (< 8 GB):**
- **qwen3:8b** (5.2 GB) ⭐ **EMPFOHLEN**
- **gemma2:9b** (5.4 GB)
- **llama3.1:8b** (4.9 GB)
- **mistral** (4.4 GB)
- **qwen3:4b, 1.7b, 0.6b** (alle kleinen Modelle)
- **llama3.2:3b, qwen2.5:3b, qwen2.5-coder:0.5b**

**⚠️ Grenzwertig (nutzt CPU-Fallback bei Bedarf):**
- **qwen2.5:14b** (9 GB) - Kann GPU-Limit überschreiten
- **command-r** (18 GB) - Hybrid-Modus (teilweise Layers auf GPU)

**❌ CPU-only (zu groß für 8GB iGPU):**
- **qwen3:32b** (20 GB) - GPU Hang → Auto-Fallback CPU
- **qwen2.5vl:7b-fp16** (16 GB) - Zu groß
- **qwen3:8b-fp16** (16 GB) - Zu groß
- **qwen2.5-coder:14b** (9 GB) - NICHT für Mini-PC empfohlen!

**Status:** AIfred erkennt automatisch AMD iGPU und wechselt auf CPU bei großen Modellen.

---

### 💻 Haupt-PC: NVIDIA RTX 3060 (12 GB VRAM)
**System:** RTX 3060 12GB + Ryzen 9900X3D + 64GB RAM

**✅ Läuft perfekt auf GPU (< 12 GB):**
- **qwen2.5-coder:14b** (9 GB) ⭐ **EMPFOHLEN für Coding**
- **qwen2.5:14b** (9 GB) ⭐ **EMPFOHLEN für Research**
- **gemma2:9b-instruct-q8_0** (9.8 GB)
- **deepseek-coder-v2:16b** (8.9 GB)
- **qwen3:8b** (5.2 GB)
- **Alle Modelle < 5 GB**

**⚠️ Hybrid-Modus (GPU teilweise + CPU):**
- **command-r** (18 GB) - Einige Layers auf CPU

**❌ CPU+RAM (zu groß für 12GB VRAM):**
- **qwen3:32b** (20 GB) - Nutzt 64GB System RAM (langsam, aber beste Qualität)
- **qwen2.5vl:7b-fp16** (16 GB)
- **qwen3:8b-fp16** (16 GB)

**Vorteil RTX 3060:**
- Große Modelle laufen zwar auf CPU, aber **deutlich schneller** als auf Mini-PC dank Ryzen 9900X3D!

---

## 💡 Spezial-Tipps

### 🎯 Für Web-Recherche (bester RAG Score)
1. **qwen2.5:14b** - RAG Score 1.0 (perfekt!)
2. qwen3:32b - RAG Score 0.98
3. qwen2.5:32b - RAG Score 0.98

### ⚡ Für Automatik-Modus (schnellste Entscheidung)
1. **qwen3:4b** - Beste Qualität bei < 3B
2. qwen3:1.7b - Sehr schnell, gut genug
3. qwen2.5:3b - Überraschend zuverlässig

### 🧮 Für Coding & Math
1. **qwen2.5:32b** - Coding-optimiert
2. qwen3:32b - Beste Reasoning
3. qwen2.5:14b - Guter Kompromiss

### 📝 Für kreative Aufgaben
1. **qwen3:32b** - Kreativste Antworten
2. mixtral:8x7b - Vielfältige Perspektiven
3. command-r - Strukturierte Kreativität

---

## 🚀 Performance-Vergleich (Mini-PC, CPU-only)

| Model | Tokens/Sek | 100 Wörter | Startup | GPU-Support |
|-------|------------|------------|---------|-------------|
| qwen3:0.6b | ~50-70 | ~2 Sek | <1 Sek | ✅ Ja |
| qwen2.5:0.5b | ~50-70 | ~2 Sek | <1 Sek | ✅ Ja |
| qwen3:1.7b | ~35-50 | ~3 Sek | ~1 Sek | ✅ Ja |
| qwen2.5:3b | ~30-40 | ~4 Sek | ~1 Sek | ✅ Ja |
| llama3.2:3b | ~30-40 | ~5 Sek | ~1 Sek | ✅ Ja |
| qwen3:4b | ~25-35 | ~5 Sek | ~1 Sek | ✅ Ja |
| mistral | ~15-25 | ~8 Sek | ~2 Sek | ✅ Ja |
| qwen3:8b | ~15-25 | ~8 Sek | ~2 Sek | ✅ Ja |
| llama3.1:8b | ~15-25 | ~8 Sek | ~2 Sek | ✅ Ja |
| llama2:13b | ~10-15 | ~12 Sek | ~3 Sek | ⚠️ Hybrid |
| qwen2.5:14b | ~8-12 | ~15 Sek | ~3 Sek | ✅ Ja |
| command-r | ~5-10 | ~20 Sek | ~5 Sek | ⚠️ Hybrid |
| mixtral:8x7b | ~3-8 | ~25+ Sek | ~8 Sek | ❌ CPU-only |
| qwen2.5:32b | ~2-5 | ~40+ Sek | ~10 Sek | ❌ CPU-only |
| qwen3:32b | ~2-5 | ~40+ Sek | ~10 Sek | ❌ CPU-only |

**Mit GPU (AMD 780M):** ~2-3x schneller für unterstützte Modelle!

---

## 🎨 LLM-Parameter Empfehlungen

### Für Fakten & Code (präzise)
```
Model: qwen2.5:14b oder qwen3:8b
Temperature: 0.3
Top P: 0.5
Top K: 20
Repeat Penalty: 1.1
```

### Für Chat (ausgewogen)
```
Model: qwen3:8b oder qwen2.5:14b
Temperature: 0.8
Top P: 0.9
Top K: 40
Repeat Penalty: 1.1
```

### Für Kreativität (vielfältig)
```
Model: qwen3:32b oder mixtral:8x7b
Temperature: 1.2
Top P: 0.95
Top K: 80
Repeat Penalty: 1.0
```

### Für Benchmarks (reproduzierbar)
```
Model: beliebig
Temperature: 0.3
Seed: 42
Max Tokens: 200
```

---

## 📋 Model-Familien Übersicht

### Qwen 3 Familie (neueste, beste Reasoning)
- **qwen3:32b** - Flagship, beste Qualität
- **qwen3:8b** - Sweet Spot
- **qwen3:4b** - Beste kleine Model
- **qwen3:1.7b** - Schnell, kompakt
- **qwen3:0.6b** - Minimal

**Context:** 32K (kleine) bis 128K (große)
**Stärken:** Reasoning, Math, Coding

### Qwen 2.5 Familie (RAG-optimiert)
- **qwen2.5:32b** - Enterprise Coding
- **qwen2.5:14b** - RAG Champion (Score 1.0!)
- **qwen2.5:3b** - Überraschend gut
- **qwen2.5:0.5b** - Minimal

**Context:** 32K bis 128K
**Stärken:** RAG, Web-Recherche, Coding

### Llama Familie (Meta, bewährt)
- **llama3.1:8b** - Aktuell, zuverlässig
- **llama3.2:3b** - Klein, schnell
- **llama2:13b** - Legacy, breites Wissen

**Context:** 4K-128K
**Stärken:** Allgemein, stabil

### Andere
- **mixtral:8x7b** - Mixture-of-Experts (47B Parameter!)
- **command-r** - Cohere, Enterprise RAG
- **mistral** - Code-Generation

---

## 🔄 Model-Wechsel Empfehlungen

### Aktuelles Setup optimieren

**Haupt-Model (Voice Assistant):**
- Von: llama2:13b oder llama3.1:8b
- Zu: **qwen2.5:14b** (beste RAG!)
- Grund: Perfekt für Web-Recherche, ignoriert Training Data

**Automatik-Model (Entscheidungen):**
- Von: llama3.2:3b (unzuverlässig!)
- Zu: **qwen3:4b** (beste 4B!)
- Grund: Rivalisiert große Modelle in Benchmarks

**Für komplexe Aufgaben:**
- Zu: **qwen3:32b** (beste Reasoning)
- Achtung: AMD iGPU → CPU-only (langsam, aber beste Qualität)

---

## 💾 Speicherplatz Management

**Aktuell installiert:** 15 Modelle (~110 GB total)

**Empfohlenes Minimal-Set (3 Modelle):**
1. `qwen2.5:14b` - Haupt-Model (9 GB)
2. `qwen3:4b` - Automatik-Model (2.5 GB)
3. `qwen3:32b` - Qualitäts-Model (20 GB)
**Total:** ~32 GB

**Empfohlenes Standard-Set (5 Modelle):**
1. `qwen2.5:14b` - Web-Recherche (9 GB)
2. `qwen3:8b` - Balance (5.2 GB)
3. `qwen3:4b` - Automatik (2.5 GB)
4. `qwen3:1.7b` - Schnell (1.4 GB)
5. `qwen3:32b` - Qualität (20 GB)
**Total:** ~38 GB

**Zum Löschen empfohlen (falls Platz knapp):**
- `qwen2.5:0.5b` - Zu klein, kaum nützlich
- `llama3.2:3b` - Unzuverlässig in Benchmarks
- Doppelte: qwen2.5:32b UND qwen3:32b (einer reicht)

---

## 🆘 Troubleshooting

### GPU-Fehler bei llama2:13b
**Problem:** "GPU error" oder langsame Inferenz
**Lösung:**
- GPU Toggle ausschalten (CPU-only Modus)
- Oder: Wechsel zu qwen2.5:14b (besser optimiert)

### "Model requires more system memory"
**Problem:** num_ctx zu groß
**Lösung:** Automatisch gefixt! Hardware-Erkennung passt Context an.

### "Model not found"
**Problem:** Model nicht installiert
**Lösung:**
```bash
ollama pull <model-name>
# Beispiel: ollama pull qwen3:4b
```

---

**Erstellt:** 2025-10-17
**Author:** Claude Code
**Version:** 2.0 - Vollständige Übersicht aller 15 Modelle + Hardware-Erkennung
