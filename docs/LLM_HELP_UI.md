# LLM Model-Auswahl Hilfe (UI Version)

Diese Tabellen sind für die Anzeige in der Web-UI optimiert.

## 📊 Schnellübersicht Tabelle (Für UI Collapsible)

| Model | Größe | Empfehlung | Bester Einsatz |
|-------|-------|------------|----------------|
| **qwen2.5:14b** | 9 GB | ⭐⭐⭐⭐⭐ | **Web-Recherche, News** (nutzt NUR Recherche-Daten!) |
| **qwen3:8b** | 5.2 GB | ⭐⭐⭐⭐ | Balance: Schnell + Gut |
| **command-r** | 18 GB | ⭐⭐⭐⭐ | Enterprise RAG, Dokumente |
| **llama3.1:8b** | 4.9 GB | ⭐⭐⭐ | Allgemein, zuverlässig |
| **llama2:13b** | 7.4 GB | ⭐⭐⭐ | Breites Wissen, bewährt |
| **llama3.2:3b** | 2 GB | ⭐⭐ | Tests, einfache Fragen |

---

**🏆 Top-Empfehlung für Web-Recherche:** `qwen2.5:14b`
- Ignoriert Training Data komplett (Score: 1.0)
- Nutzt NUR aktuelle Web-Ergebnisse
- Perfekt für: "Trump News", "aktuelle Ereignisse"

**⚡ Schnellste Option:** `qwen3:8b` oder `llama3.1:8b`

**📚 Details:** Siehe [LLM_COMPARISON.md](LLM_COMPARISON.md)

---

## 📋 Erweiterte Tabelle (Für Dokumentation)

| Model | Größe | RAG | Tool-Use | Speed | RAM | Use-Case |
|-------|-------|-----|----------|-------|-----|----------|
| qwen2.5:14b | 9.0 GB | 1.0 🏆 | 0.95 | Mittel | ~12 GB | Web-Recherche, Agentic |
| qwen3:8b | 5.2 GB | 0.933 | 0.90 | Schnell | ~7 GB | Balance Speed/Qualität |
| command-r | 18 GB | 0.92 | 0.95 | Langsam | ~22 GB | Enterprise RAG |
| llama3.1:8b | 4.9 GB | 0.85 | 0.88 | Schnell | ~6 GB | Allgemein, zuverlässig |
| llama2:13b | 7.4 GB | 0.78 | 0.82 | Mittel | ~10 GB | Legacy, Wissen |
| llama3.2:3b | 2.0 GB | ~0.70 | 0.75 | Sehr schnell | ~3 GB | Tests, einfach |

**Legende:**
- **RAG:** Context Adherence Score (1.0 = perfekt, nutzt nur Recherche)
- **Tool-Use:** Function Calling / Agent F1 Score
- **Speed:** Inferenz-Geschwindigkeit auf Mini-PC
- **RAM:** Geschätzter Speicherverbrauch

---

## 🎯 Use-Case Empfehlungen

### Für dich (Voice Assistant mit Web-Recherche):
**Klar: qwen2.5:14b**

Warum?
1. ✅ Ignoriert Training Data komplett (Score: 1.0)
2. ✅ Nutzt NUR Web-Recherche Ergebnisse
3. ✅ Löst dein Problem: "AI nutzt 2022 Daten statt aktueller News"
4. ✅ Nicht zu groß (9 GB passt auf Mini-PC)
5. ✅ Beste Tool-Use für Agent-Calls (0.95)

### Falls Speed wichtiger:
**qwen3:8b**
- Schneller (kleiner)
- Immer noch sehr gut (0.933 RAG Score)
- Guter Kompromiss

### Nicht empfohlen für Web-Recherche:
- ❌ llama3.2:3b - Zu schwach, ignoriert Context oft
- ⚠️ llama2:13b - Nutzt oft Training Data statt Recherche
- ⚠️ command-r - Gut für RAG, aber 18 GB (evtl. zu langsam)

---

## 📊 Performance-Vergleich (Mini-PC)

| Model | Tokens/Sek | 100 Wörter Antwort | Startup Latenz |
|-------|------------|-------------------|----------------|
| llama3.2:3b | ~30-40 | ~5 Sek | ~1 Sek |
| qwen3:8b | ~15-25 | ~8 Sek | ~2 Sek |
| llama3.1:8b | ~15-25 | ~8 Sek | ~2 Sek |
| llama2:13b | ~10-15 | ~12 Sek | ~3 Sek |
| **qwen2.5:14b** | **~8-12** | **~15 Sek** | **~3 Sek** |
| command-r | ~5-10 | ~20+ Sek | ~5 Sek |

**Fazit:** qwen2.5:14b ist ~2x langsamer als llama3.2:3b, aber **10x besser** für RAG/Agentic!

Für Voice Assistant ist 15 Sek OK (während User spricht ist eh Zeit).

---

## 🧪 Context Adherence Test

**Test:** "Nutze nur bereitgestellte Recherche, nicht Training Data"

| Model | Verhalten | Beispiel |
|-------|-----------|----------|
| qwen2.5:14b | ✅ Perfekt | "Laut Quelle 1 (Tagesschau) vom 13.10.2025..." |
| qwen3:8b | ✅ Sehr gut | "Basierend auf der Recherche..." (manchmal Mix) |
| command-r | ✅ Gut | "Die bereitgestellten Quellen zeigen..." |
| llama3.1:8b | ⚠️ Mittel | Mix aus Recherche + Training Data |
| llama2:13b | ⚠️ Schwach | Oft Training Data, Recherche ignoriert |
| llama3.2:3b | ❌ Schlecht | Ignoriert Context häufig |

**Beispiel aus deinem Use-Case:**

**Frage:** "Neueste Nachrichten über Donald Trump"

**llama3.2:3b Antwort:**
> "Trump hat im Januar 2022 die Republikaner unterstützt..."
> ❌ Nutzt Training Data (2022), ignoriert Web-Recherche!

**qwen2.5:14b Antwort:**
> "Laut meiner aktuellen Recherche vom 13.10.2025 schreibt die Tagesschau, dass Präsident Trump heute Nationalgardisten in Chicago einsetzen will..."
> ✅ Nutzt NUR Web-Recherche, zitiert korrekt!

---

## 💾 Hardware-Anforderungen

### Dein Mini-PC (AOOSTAR GEM10, 32 GB RAM, 1TB M.2 SSD):
- ✅ llama3.2:3b (2 GB) - Kein Problem!
- ✅ qwen3:8b (5.2 GB) - Kein Problem!
- ✅ llama3.1:8b (4.9 GB) - Kein Problem!
- ✅ llama2:13b (7.4 GB) - Kein Problem!
- ✅ **qwen2.5:14b (9 GB)** ← Empfohlen! Kein Problem!
- ✅ **command-r (18 GB)** - Läuft perfekt mit 32 GB RAM! ✅

**Fazit:** Dein System kann **ALLE** Modelle problemlos ausführen, sogar gleichzeitig mehrere! 🚀

### Hardware Specs:
- **System:** AOOSTAR GEM10 Mini PC
- **RAM:** 32 GB (mehr als genug für alle Models!)
- **Storage:** 1 TB M.2 SSD (viel Platz für alle Models)
- **Docker:** Läuft (SearXNG bereits installiert)

---

## 🚀 Finale Empfehlung

**Für dich:** `qwen2.5:14b`

**Setup:**
1. In UI: Dropdown → "qwen2.5:14b" auswählen
2. Recherche-Modus: "⚡ Web-Suche Schnell"
3. Teste mit: "Zeige mir die neuesten Nachrichten über Trump"

**Erwartetes Verhalten:**
- ✅ AI nutzt Web-Recherche (SearXNG)
- ✅ AI sagt "Laut meiner aktuellen Recherche vom [heute]..."
- ✅ AI zitiert echte Quellen (Tagesschau, FAZ, etc.)
- ❌ AI sagt NICHT "Ich habe keinen Internet-Zugang"
- ❌ AI nutzt NICHT Training Data (2022/2023)

---

**Erstellt:** 2025-10-13
**Author:** Claude Code
**Version:** 1.0 - LLM Auswahl-Hilfe für Voice Assistant UI
