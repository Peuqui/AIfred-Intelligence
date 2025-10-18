# LLM Modell-Vergleich - AIfred Intelligence

**Letzte Aktualisierung:** 2025-10-15 (Finale Benchmark-Ergebnisse)

---

## 🧪 AKTUELLE BENCHMARK-ERGEBNISSE (2025-10-15)

**Methode:** Automatisierter Benchmark mit sauberem RAM, korrektem Thinking-Mode Parser, manuelle Auswertung

### Test-Szenarien:
1. **Trump/Hamas Test**: Komplexe News-Frage, erfordert Web-Recherche (erwartet: `yes`)
2. **Guten Morgen Test**: Einfache Begrüßung, keine Web-Recherche nötig (erwartet: `no`)
3. **Wetter Test**: "Wetter morgen in Berlin" - MUSS Web-Recherche auslösen (erwartet: `yes`)
4. **Geschwindigkeitstest**: Einfache Aufgabe zur Speed-Messung

### 📊 Ergebnisse-Übersicht

| Modell | Test 1 (Trump/Hamas) | Test 2 (Guten Morgen) | Test 3 (Wetter) | Speed Test | **Status** |
|--------|----------------------|-----------------------|-----------------|------------|------------|
| **qwen3:1.7b** | ✅ yes (31.1s) | ✅ no (5.0s) | ✅ yes (6.3s) | 4.4s | **✅ ALLE BESTANDEN** |
| **qwen3:8b** | ✅ yes (72.4s) | ✅ no (19.0s) | ✅ yes (13.3s) | 22.3s | **✅ ALLE BESTANDEN** |
| **qwen2.5:32b** | ✅ yes (88.4s) | ✅ no (13.9s) | ✅ yes (14.4s) | 6.4s | **✅ ALLE BESTANDEN** |
| qwen3:0.6b | ✅ yes (4.7s) | ✅ no (2.3s) | ❌ no (2.2s) | 2.0s | ❌ Versagt bei Wetter |
| qwen3:4b | ❌ no (315.0s) | ✅ no (40.6s) | ✅ yes (54.5s) | 68.4s | ❌ Versagt bei Trump/Hamas |
| llama3.2:3b | ❌ no (10.3s) | ✅ no (1.4s) | ❌ no (1.4s) | 1.1s | ❌ Versagt bei 2 Tests |

**Detaillierte Ergebnisse:** [BENCHMARK_RESULTS_FINAL.md](../BENCHMARK_RESULTS_FINAL.md)

---

## 🏆 EMPFEHLUNGEN FÜR AIFRED INTELLIGENCE

### ⭐⭐⭐⭐⭐ PRIMÄR-EMPFEHLUNG: qwen3:1.7b

**Warum?**
- ✅ Alle Tests bestanden (einziges kleines Modell!)
- ⚡ Schnellste Entscheidung bei voller Korrektheit (31.1s)
- 💾 Sehr klein: 1.7B Parameter ≈ 2 GB RAM
- 🎯 Erkennt alle kritischen Trigger (News, Wetter, etc.)

**Einsatzgebiet:**
- **Automatik-Entscheidung**: Soll Web-Recherche durchgeführt werden?
- **Schnelle Anfragen**: Wenn Speed wichtiger ist als perfekte Qualität

**Hardware:**
- RAM-Bedarf: ~2-3 GB
- CPU-Last: Moderat
- Inference: ~31s für komplexe Entscheidungen, ~5s für einfache

---

### ⭐⭐⭐⭐ SEKUNDÄR-EMPFEHLUNG: qwen3:8b

**Warum?**
- ✅ Alle Tests bestanden
- 🧠 Bessere Reasoning-Qualität als qwen3:1.7b
- 📊 Moderate Geschwindigkeit (72.4s für Trump/Hamas)
- 💪 Robuster bei komplexen Fragen

**Einsatzgebiet:**
- **Haupt-LLM**: Finale Antwortgenerierung nach erfolgreicher Web-Recherche
- **Komplexe Aufgaben**: Wenn Qualität wichtiger ist als Geschwindigkeit

**Hardware:**
- RAM-Bedarf: ~8 GB
- CPU-Last: Höher als qwen3:1.7b
- Inference: ~72s für komplexe Aufgaben, ~19s für einfache

---

### ⭐⭐⭐ OPTIONAL: qwen2.5:32b (nur wenn viel RAM)

**Warum?**
- ✅ Alle Tests bestanden
- 🎯 Höchste Qualität
- ⚡ Kein Thinking Mode (direkte Antworten, spart Tokens)

**ABER:**
- ⚠️ Sehr groß: 32B Parameter ≈ 21 GB RAM!
- ⚠️ Langsam: 88.4s für Trump/Hamas
- ⚠️ Nicht geeignet für Automatik-Entscheidung

**Einsatzgebiet:**
- **Fallback**: Nur wenn genug RAM verfügbar
- **Premium-Antworten**: Wenn beste Qualität benötigt wird

**Hardware:**
- RAM-Bedarf: ~21-24 GB (!)
- CPU-Last: Sehr hoch
- Inference: ~88s für komplexe Aufgaben

---

## ❌ NICHT EMPFOHLEN

### llama3.2:3b
- **Problem:** Versagt bei Trump/Hamas (News) UND Wetter
- **Grund:** Versteht kontextuelle Trigger nicht zuverlässig
- **Fazit:** Trotz hoher Geschwindigkeit (1.1s) nicht zuverlässig genug

### qwen3:0.6b
- **Problem:** Versagt bei Wetter-Erkennung
- **Grund:** Zu klein (0.6B) für komplexe Entscheidungslogik
- **Fazit:** Schnell (4.7s), aber unzuverlässig

### qwen3:4b
- **Problem:** Versagt bei Trump/Hamas trotz 315s (!) Thinking-Zeit
- **Grund:** Extrem langsam UND trotzdem fehleranfällig
- **Fazit:** Schlechtestes Preis/Leistungs-Verhältnis aller Modelle

---

## 📊 Technische Übersichtstabelle

| Modell | Größe | Automatik-Entscheidung | Qualität | Speed | RAM | Status |
|--------|-------|------------------------|----------|-------|-----|--------|
| **qwen3:1.7b** | 2 GB | ✅ 31.1s | ⭐⭐⭐⭐ | Schnell | ~3 GB | ✅ **EMPFOHLEN** |
| **qwen3:8b** | 8 GB | ✅ 72.4s | ⭐⭐⭐⭐⭐ | Mittel | ~8 GB | ✅ **EMPFOHLEN** |
| **qwen2.5:32b** | 21 GB | ✅ 88.4s | ⭐⭐⭐⭐⭐ | Langsam | ~22 GB | ⚠️ Optional |
| qwen3:0.6b | 1 GB | ❌ Versagt | ⭐⭐ | Sehr schnell | ~2 GB | ❌ |
| qwen3:4b | 4 GB | ❌ Versagt | ⭐ | Sehr langsam | ~5 GB | ❌ |
| llama3.2:3b | 2 GB | ❌ Versagt | ⭐⭐ | Sehr schnell | ~3 GB | ❌ |

**Legende:**
- **Automatik-Entscheidung:** Zeit für Trump/Hamas-Test (komplexe Entscheidung)
- **Qualität:** Reasoning-Qualität und Zuverlässigkeit
- **Speed:** Relative Geschwindigkeit
- **RAM:** Speicherbedarf während Inferenz

---

## 🎯 Implementierungs-Empfehlung

### ✅ **IMPLEMENTIERT in AIfred Intelligence (2025-10-15)**

```python
# aifred_intelligence.py
AUTOMATIK_MODEL = "qwen3:1.7b"  # Hardcoded für schnelle AI-Entscheidungen
```

**Hardcoded für Automatik-Tasks:**
- ✅ **Automatik-Entscheidung**: Web-Recherche JA/NEIN? → `qwen3:1.7b`
- ✅ **Query-Optimierung**: Keyword-Extraktion → `qwen3:1.7b`
- ✅ **URL-Bewertung**: 15 URLs filtern (7s/URL) → `qwen3:1.7b`

**User-wählbar für Finale Antwort:**
- 🔽 **Dropdown in UI**: User wählt zwischen qwen3:1.7b, qwen3:8b, qwen2.5:32b
- 🎯 **Empfohlen**: qwen3:8b (Default)

---

### Ablauf im Automatik-Modus:

```
1. User stellt Frage
   ↓
2. qwen3:1.7b entscheidet (~5-30s): Web-Recherche nötig?
   ↓ JA
3. qwen3:1.7b optimiert Query zu Keywords (~5-15s)
   ↓
4. Web-Recherche via SearXNG (15 URLs)
   ↓
5. qwen3:1.7b bewertet URLs (~105s für 15 URLs)
   ↓
6. Scrape Top 3-5 URLs
   ↓
7. USER-GEWÄHLTES MODELL generiert finale Antwort
   - qwen3:1.7b: ~30s (schnell)
   - qwen3:8b: ~1-2 Min (default, gute Qualität)
   - qwen2.5:32b: ~3-5 Min (beste Qualität)
```

**Vorteile dieser Strategie:**
- ⚡ **Schnelle Vorauswahl**: qwen3:1.7b filtert in ~2-3 Min (Entscheidung + Query + URL-Rating)
- 🎯 **Flexible Qualität**: User wählt Speed vs. Quality für finale Antwort
- 💾 **Moderater RAM**: ~11 GB wenn qwen3:8b gewählt
- 🔍 **Content-basierte URL-Bewertung**: Nicht Domain-basiert!

**Benchmark-Ergebnisse:**
- Automatik-Phase (qwen3:1.7b): ~2-3 Min total
  - Entscheidung: ~5-30s
  - Query-Opt: ~5-15s
  - URL-Rating: ~105s (15 URLs)
- Finale Antwort (User-wählbar):
  - qwen3:1.7b: +30s
  - qwen3:8b: +1-2 Min
  - qwen2.5:32b: +3-5 Min

---

## 🔬 Benchmark-Details

### Test 1: Trump/Hamas Entscheidung (Komplex)

**Frage:** "Präsident Trump hat mit der Hamas und Präsident Netanyahu ein Friedensabkommen geschlossen, welches von Präsident Biden bereits vor Jahren vorbereitet war. Bitte recherchiere die entsprechenden Dokumente von Präsident Biden."

**Trigger-Wörter:** Aktuelle News, Recherche, Dokumente, spezifische Events

| Modell | Antwort | Korrekt? | Zeit | Bemerkung |
|--------|---------|----------|------|-----------|
| qwen3:1.7b | `<search>yes</search>` | ✅ | 31.1s | Gute Analyse im Thinking Mode |
| qwen3:8b | `<search>yes</search>` | ✅ | 72.4s | Klare, korrekte Analyse |
| qwen2.5:32b | `<search>yes</search>` | ✅ | 88.4s | Direkte Antwort, kein Thinking |
| qwen3:0.6b | `<search>yes</search>` | ✅ | 4.7s | Korrekt, aber versagt bei Wetter |
| qwen3:4b | `EIGENES WISSEN REICHT` | ❌ | 315.0s | 300+ Zeilen Thinking, falsch! |
| llama3.2:3b | `<search>no</search>` | ❌ | 10.3s | Versteht News-Trigger nicht |

### Test 2: Einfache Begrüßung

**Frage:** "Guten Morgen"

**Erwartung:** Keine Web-Recherche nötig

| Modell | Antwort | Korrekt? | Zeit |
|--------|---------|----------|------|
| qwen3:1.7b | `<search>no</search>` | ✅ | 5.0s |
| qwen3:8b | `<search>no</search>` | ✅ | 19.0s |
| qwen2.5:32b | `<search>no</search>` | ✅ | 13.9s |
| qwen3:0.6b | `<search>no</search>` | ✅ | 2.3s |
| qwen3:4b | `<search>no</search>` | ✅ | 40.6s |
| llama3.2:3b | `<search>no</search>` | ✅ | 1.4s |

**Erkenntnis:** Alle Modelle bestehen den einfachen Test ✅

### Test 3: Wetter-Anfrage (KRITISCH!)

**Frage:** "Wie wird das Wetter morgen in Berlin?"

**Erwartung:** IMMER Web-Recherche (Wetter = Live-Daten)

| Modell | Antwort | Korrekt? | Zeit | Bemerkung |
|--------|---------|----------|------|-----------|
| qwen3:1.7b | `<search>yes</search>` | ✅ | 6.3s | **Mindestgröße** für Wetter-Erkennung |
| qwen3:8b | `<search>yes</search>` | ✅ | 13.3s | Zuverlässig |
| qwen2.5:32b | `<search>yes</search>` | ✅ | 14.4s | Zuverlässig |
| qwen3:0.6b | `<search>no</search>` | ❌ | 2.2s | **Zu klein** für diese Entscheidung |
| qwen3:4b | `<search>yes</search>` | ✅ | 54.5s | Korrekt, aber sehr langsam |
| llama3.2:3b | `<search>no</search>` | ❌ | 1.4s | Versteht Wetter-Trigger nicht |

**Erkenntnis:** Nur Modelle mit ≥1.7B Parametern erkennen Wetter-Trigger korrekt!

### Test 4: Geschwindigkeitstest

**Frage:** "Wünsche mir einen guten Abend und hänge drei Emojis dran."

| Modell | Zeit | Ranking | Bemerkung |
|--------|------|---------|-----------|
| llama3.2:3b | 1.1s | 🥇 | Extrem schnell, aber unzuverlässig |
| qwen3:0.6b | 2.0s | 🥈 | Sehr schnell, aber Wetter-Fehler |
| qwen3:1.7b | 4.4s | 🥉 | **Bester Kompromiss** |
| qwen2.5:32b | 6.4s | #4 | Schnell für Größe |
| qwen3:8b | 22.3s | #5 | Akzeptabel |
| qwen3:4b | 68.4s | #6 | Inakzeptabel langsam |

---

## 💾 Hardware-Anforderungen (Mini-PC mit 32GB RAM)

### Minimum für qwen3:1.7b:
- RAM: 4 GB frei
- CPU: 4+ Cores
- Disk: 2 GB

### Empfohlen für qwen3:8b:
- RAM: 10 GB frei
- CPU: 6+ Cores
- Disk: 6 GB

### Für qwen2.5:32b:
- RAM: 24+ GB frei (!)
- CPU: 8+ Cores
- Disk: 18 GB

### Aktueller Mini-PC Status:
- RAM: 32 GB total
- Status: ✅ Kann qwen3:1.7b + qwen3:8b gleichzeitig (~11 GB)
- Status: ⚠️ qwen2.5:32b nur wenn nichts anderes läuft

---

## 🚀 Performance-Messungen (Real)

**Basierend auf echten Benchmark-Messungen vom 2025-10-15:**

| Modell | Komplexe Aufgabe | Einfache Aufgabe | Durchschnitt |
|--------|------------------|------------------|--------------|
| qwen3:1.7b | 31.1s | 5.0s | ~18s |
| qwen3:8b | 72.4s | 19.0s | ~46s |
| qwen2.5:32b | 88.4s | 13.9s | ~51s |
| qwen3:0.6b | 4.7s | 2.3s | ~3.5s |
| qwen3:4b | 315.0s (!) | 40.6s | ~178s (!) |
| llama3.2:3b | 10.3s | 1.4s | ~6s |

**Hinweis:** Zeiten gemessen mit sauberem RAM, ohne Swap-Druck

---

## 📝 Changelog

**2025-10-15:** Finale Benchmark-Ergebnisse mit korrektem Parser
- ✅ Vollständiger Benchmark aller 6 Modelle
- ✅ Sauberer RAM (kein Swap-Verfälschung)
- ✅ Korrigierter Thinking-Mode Parser
- ✅ Manuelle Auswertung der Log-Dateien
- 🎯 **Hauptempfehlung:** qwen3:1.7b für Automatik-Entscheidung
- 🎯 **Sekundärempfehlung:** qwen3:8b für Hauptantworten
- ❌ **Nicht empfohlen:** llama3.2:3b, qwen3:0.6b, qwen3:4b

**2025-10-14:** Model-Downloads
- Downloaded: qwen3:0.6b, qwen3:1.7b, qwen3:4b
- Erste Tests mit fehlerhaftem Parser

**2025-10-13:** Initial comparison
- Downloaded: qwen3:8b, qwen2.5:32b
- Basis-Vergleich ohne Benchmarks

---

**Detaillierte Benchmark-Logs:** [benchmarks/logs_sequential/](../benchmarks/logs_sequential/)
**Finale Qualitäts-Auswertung:** [BENCHMARK_QUALITY_ANALYSIS.md](../benchmarks/BENCHMARK_QUALITY_ANALYSIS.md)
