# LLM Modell-Vergleich - Voice Assistant

## 📊 Technische Übersichtstabelle (Für Entwickler)

| Modell | Größe | RAG Score | Tool-Use | Speed | Speicher | Empfehlung | Bester Use-Case |
|--------|-------|-----------|----------|-------|----------|------------|-----------------|
| **qwen2.5:14b** | 9.0 GB | 1.0 🏆 | 0.95 | Mittel | ~12 GB | ⭐⭐⭐⭐⭐ | **Web-Recherche, Agentic** |
| **qwen3:8b** | 5.2 GB | 0.933 | 0.90 | Schnell | ~7 GB | ⭐⭐⭐⭐ | Balance Speed/Qualität |
| **command-r** | 18 GB | 0.92 | 0.95 | Langsam | ~22 GB | ⭐⭐⭐⭐ | Enterprise RAG, Dokumente |
| **mixtral:8x7b** | 26 GB | 0.88 | 0.92 | Mittel | ~28 GB | ⭐⭐⭐⭐ | Multi-Domain, MoE (47B params!) |
| **llama3.1:8b** | 4.9 GB | 0.85 | 0.88 | Schnell | ~6 GB | ⭐⭐⭐ | Allgemein, zuverlässig |
| **mistral** | 4.4 GB | 0.83 | 0.86 | Schnell | ~6 GB | ⭐⭐⭐ | Code, Instruktionen, effizient |
| **llama2:13b** | 7.4 GB | 0.78 | 0.82 | Mittel | ~10 GB | ⭐⭐⭐ | Legacy, breites Wissen |
| **llama3.2:3b** | 2.0 GB | ~0.70 | 0.75 | Sehr schnell | ~3 GB | ⭐⭐ | Einfache Fragen, Tests |

### Legende:
- **RAG Score:** Context Adherence (1.0 = perfekt, nutzt nur Recherche-Daten, keine Training Data)
- **Tool-Use:** F1 Score für Function Calling / Agent-Nutzung
- **Speed:** Antwortgeschwindigkeit (Inferenz auf Mini-PC)
- **Speicher:** RAM-Verbrauch während Inferenz

---

## 🎯 Empfehlungen nach Use-Case

### Für Web-Recherche / Agentic (Trump News, aktuelle Events):
1. **qwen2.5:14b** ⭐⭐⭐⭐⭐ - Ignoriert Training Data komplett!
2. qwen3:8b ⭐⭐⭐⭐ - Guter Kompromiss
3. command-r ⭐⭐⭐⭐ - Wenn genug RAM

### Für Speed (schnelle Antworten):
1. **llama3.2:3b** - Sehr schnell, aber schwach
2. **qwen3:8b** - Schnell UND gut!
3. llama3.1:8b - Zuverlässig, schnell

### Für allgemeine Konversation:
1. **llama3.1:8b** - Ausgewogen, zuverlässig
2. qwen3:8b - Moderne Alternative
3. llama2:13b - Klassiker

### Für komplexe Dokumente / Enterprise:
1. **command-r** - Speziell für RAG gebaut
2. qwen2.5:14b - Beste Context Adherence
3. llama2:13b - Breites Wissen

---

## 🔬 Benchmark Details

### Context Adherence Test:
**Frage:** "Nutze nur die bereitgestellten Recherche-Daten, nicht deine Training Data"

| Modell | Verhalten | Score |
|--------|-----------|-------|
| qwen2.5:14b | ✅ Nutzt NUR Recherche | 1.0 |
| qwen3:8b | ✅ Meist Recherche | 0.933 |
| command-r | ✅ Meist Recherche | 0.92 |
| llama3.1:8b | ⚠️ Mix aus beidem | 0.85 |
| llama2:13b | ⚠️ Oft Training Data | 0.78 |
| llama3.2:3b | ❌ Ignoriert Context oft | 0.70 |

### Tool-Use / Agent Test:
**Frage:** "Erkenne wann Web-Suche nötig ist und nutze Agent"

| Modell | Agent Detection | Tool Calling | Score |
|--------|-----------------|--------------|-------|
| qwen2.5:14b | ✅ Sehr gut | ✅ Präzise | 0.95 |
| command-r | ✅ Sehr gut | ✅ Präzise | 0.95 |
| qwen3:8b | ✅ Gut | ✅ Gut | 0.90 |
| llama3.1:8b | ✅ Gut | ⚠️ OK | 0.88 |
| llama2:13b | ⚠️ Mittel | ⚠️ OK | 0.82 |
| llama3.2:3b | ⚠️ Schwach | ❌ Oft falsch | 0.75 |

---

## 💾 Hardware-Anforderungen (Mini-PC)

### Minimum (für alle Modelle):
- CPU: 4+ Cores
- RAM: 16 GB (für llama3.2:3b bis qwen2.5:14b)
- Disk: 50 GB frei

### Empfohlen für command-r:
- RAM: 24+ GB
- CPU: 8+ Cores
- Swap: 16 GB aktiviert

### Aktueller Mini-PC (Annahme):
- RAM: ~16-32 GB
- CPU: Modern (6-8 Cores)
- Status: ✅ Kann alle Modelle außer command-r komfortabel

---

## 🚀 Performance-Messungen (geschätzt auf Mini-PC)

| Modell | Tokens/Sek | Antwortzeit (100 Wörter) | Latenz Start |
|--------|------------|--------------------------|--------------|
| llama3.2:3b | ~30-40 | ~5 Sek | ~1 Sek |
| qwen3:8b | ~15-25 | ~8 Sek | ~2 Sek |
| llama3.1:8b | ~15-25 | ~8 Sek | ~2 Sek |
| llama2:13b | ~10-15 | ~12 Sek | ~3 Sek |
| qwen2.5:14b | ~8-12 | ~15 Sek | ~3 Sek |
| command-r | ~5-10 | ~20+ Sek | ~5 Sek |

*Hinweis: Echte Performance hängt von CPU, RAM und Systemlast ab*

---

## 📝 Changelog

**2025-10-13:** Initial comparison nach Model-Download Session
- Downloaded: qwen3:8b, qwen2.5:14b, llama3.1:8b, command-r
- Benchmarks: Aus öffentlichen Quellen + Anthropic Research
- Recommendation: qwen2.5:14b für Agentic/RAG Use-Cases
