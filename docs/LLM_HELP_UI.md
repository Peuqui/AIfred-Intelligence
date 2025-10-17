# LLM Model-Auswahl Hilfe (UI Version)

Diese Tabellen sind für die Anzeige in der Web-UI optimiert.

## 📊 Schnellübersicht - Alle installierten Modelle

### 🏆 Top-Empfehlungen

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **qwen3:32b** | 20 GB | ⭐⭐⭐⭐⭐ | **Beste Qualität** (Reasoning, komplexe Aufgaben) |
| **qwen2.5:32b** | 19 GB | ⭐⭐⭐⭐⭐ | **Enterprise RAG, Coding** (sehr zuverlässig) |
| **qwen2.5:14b** | 9 GB | ⭐⭐⭐⭐⭐ | **Web-Recherche, News** (nutzt NUR Recherche-Daten!) |
| **qwen3:8b** | 5.2 GB | ⭐⭐⭐⭐ | Balance: Schnell + Gut |
| **command-r** | 18 GB | ⭐⭐⭐⭐ | Enterprise RAG, Dokumente |

### 🚀 Schnelle Modelle (< 5 GB)

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **qwen3:4b** | 2.5 GB | ⭐⭐⭐⭐ | **Beste kleine Model** (Entscheidungen, Agent) |
| **llama3.1:8b** | 4.9 GB | ⭐⭐⭐ | Allgemein, zuverlässig |
| **mistral:latest** | 4.4 GB | ⭐⭐⭐ | Code-Generation, Instruktionen |
| **llama3.2:3b** | 2.0 GB | ⭐⭐ | Tests, einfache Fragen |
| **qwen2.5:3b** | 1.9 GB | ⭐⭐⭐ | Überraschend gut für 3B |
| **qwen3:1.7b** | 1.4 GB | ⭐⭐ | Extrem schnell, einfache Tasks |
| **qwen3:0.6b** | 522 MB | ⭐ | Kleinste, für Experimente |
| **qwen2.5:0.5b** | 397 MB | ⭐ | Minimal, sehr schnell |

### 🐘 Große Modelle (> 20 GB)

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **mixtral:8x7b** | 26 GB | ⭐⭐⭐⭐ | Mixture-of-Experts, vielseitig |
| **llama2:13b** | 7.4 GB | ⭐⭐⭐ | Bewährt, breites Wissen |

---

## 🎯 Empfehlungen nach Use-Case

### 💬 Voice Assistant (Haupt-Model)
**Empfohlen:** `qwen2.5:14b` oder `qwen3:8b`
- Beste Balance aus Qualität & Speed
- Exzellent für Web-Recherche
- Passt perfekt auf Mini-PC

### 🤖 Automatik-Modus (Entscheidungen)
**Empfohlen:** `qwen3:4b` oder `qwen3:1.7b`
- Schnelle Entscheidungen (Web-Recherche ja/nein)
- Niedriger Speicherverbrauch
- **qwen3:4b rivalisiert 32B Modelle in Benchmarks!**

### 📚 Komplexe Reasoning-Aufgaben
**Empfohlen:** `qwen3:32b` oder `qwen2.5:32b`
- Beste Qualität für komplexe Probleme
- Math, Coding, Logik
- **Achtung:** AMD iGPU → CPU-only (langsam!)

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

| Model | Größe | RAG Score | Tool-Use | Speed | RAM | Context |
|-------|-------|-----------|----------|-------|-----|---------|
| qwen3:32b | 20 GB | 0.98 | 0.98 | Langsam | ~24 GB | 128K |
| qwen2.5:32b | 19 GB | 0.98 | 0.97 | Langsam | ~23 GB | 128K |
| mixtral:8x7b | 26 GB | 0.95 | 0.93 | Sehr langsam | ~30 GB | 32K |
| command-r | 18 GB | 0.92 | 0.95 | Langsam | ~22 GB | 128K |
| qwen2.5:14b | 9 GB | 1.0 🏆 | 0.95 | Mittel | ~12 GB | 128K |
| llama2:13b | 7.4 GB | 0.78 | 0.82 | Mittel | ~10 GB | 4K |
| qwen3:8b | 5.2 GB | 0.933 | 0.90 | Schnell | ~7 GB | 128K |
| llama3.1:8b | 4.9 GB | 0.85 | 0.88 | Schnell | ~6 GB | 128K |
| mistral:latest | 4.4 GB | 0.88 | 0.85 | Schnell | ~6 GB | 32K |
| qwen3:4b | 2.5 GB | 0.92 | 0.88 | Sehr schnell | ~4 GB | 32K |
| llama3.2:3b | 2.0 GB | ~0.70 | 0.75 | Sehr schnell | ~3 GB | 128K |
| qwen2.5:3b | 1.9 GB | 0.85 | 0.80 | Sehr schnell | ~3 GB | 32K |
| qwen3:1.7b | 1.4 GB | 0.80 | 0.75 | Extrem schnell | ~2 GB | 32K |
| qwen3:0.6b | 522 MB | 0.65 | 0.60 | Extrem schnell | ~1 GB | 32K |
| qwen2.5:0.5b | 397 MB | 0.60 | 0.55 | Extrem schnell | ~800 MB | 32K |

**Legende:**
- **RAG Score:** Context Adherence (1.0 = perfekt, nutzt nur Recherche-Daten)
- **Tool-Use:** Function Calling / Agent F1 Score
- **Speed:** Inferenz-Geschwindigkeit auf Mini-PC
- **RAM:** Geschätzter Speicherverbrauch
- **Context:** Natives Max Context Window

---

## 🔧 Hardware-Erkennung & GPU-Support

### AMD Radeon 780M iGPU (11.6 GB VRAM)

**✅ Funktioniert mit GPU:**
- qwen3:8b, qwen2.5:14b, llama3.1:8b, mistral
- qwen3:4b, 1.7b, 0.6b (alle kleinen Modelle)
- llama3.2:3b, qwen2.5:3b, 0.5b

**⚠️ Hybrid-Modus (GPU + CPU):**
- command-r (Teilweise Layers auf GPU)
- llama2:13b (Teilweise Layers auf GPU)

**❌ CPU-only (GPU crasht):**
- **qwen3:32b** - GPU Hang Issue → Auto-Fallback auf CPU
- **qwen2.5:32b** - GPU Hang Issue → Auto-Fallback auf CPU
- **mixtral:8x7b** - Zu groß für VRAM

**Status:** Automatische Hardware-Erkennung aktiv! AIfred erkennt AMD iGPU und wechselt automatisch auf CPU bei 32B Modellen.

### NVIDIA RTX 3060 (12 GB VRAM)

**✅ Funktioniert mit GPU:**
- Alle Modelle bis 14B
- qwen3:32b mit Layer-Limit (25 Layers)
- qwen2.5:32b mit Layer-Limit (25 Layers)

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
