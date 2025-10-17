# LLM-Parameter Guide

**Autor:** AIfred Intelligence Team
**Datum:** 2025-10-17
**Version:** 1.0

## Übersicht

Ollama unterstützt zahlreiche Parameter zur Feinsteuerung der LLM-Ausgaben. Dieses Dokument erklärt alle verfügbaren Parameter und gibt Empfehlungen für verschiedene Anwendungsfälle.

---

## Sampling-Parameter (Kreativität)

### temperature (0.0 - 2.0, default: 0.8)

**Was ist das?**
- Steuert die "Kreativität" bzw. Zufälligkeit der Antworten
- Niedrig = vorhersehbar, hoch = kreativ

**Technisch:**
- Dividiert die Logits (Modell-Scores) vor Softmax
- Höhere Werte "glätten" die Wahrscheinlichkeitsverteilung

**Werte:**
```
0.0   → Deterministisch (immer gleiche Antwort)
0.1   → Sehr präzise, fast keine Variation
0.3   → Fokussiert, gut für Fakten
0.5   → Ausgewogen präzise
0.8   → Standard (Ollama Default)
1.0   → Ausgewogen kreativ
1.2   → Kreativ, interessante Formulierungen
1.5   → Sehr kreativ, manchmal überraschend
2.0   → Extrem kreativ, oft wirr
```

**Anwendungsfälle:**
- **Code-Generierung:** 0.2-0.3 (präzise Syntax)
- **Fakten/Recherche:** 0.3-0.5 (fokussiert)
- **Chat/Q&A:** 0.7-0.9 (natürlich)
- **Geschichten:** 1.0-1.3 (kreativ)
- **Gedichte:** 1.2-1.5 (sehr kreativ)
- **Brainstorming:** 1.3-1.7 (unkonventionell)

**Beispiel:**
```
Prompt: "Nenne 3 Farben"

temp=0.0: "Rot, Blau, Grün" (immer gleich)
temp=0.8: "Blau, Grün, Gelb"
temp=1.5: "Türkis, Magenta, Safrangelb"
```

---

### top_p (0.0 - 1.0, default: 0.9)

**Was ist das?**
- "Nucleus Sampling" - wählt aus den wahrscheinlichsten Tokens
- Je niedriger, desto fokussierter die Auswahl

**Technisch:**
- Wählt Tokens bis kumulative Wahrscheinlichkeit ≥ top_p
- Dynamische Auswahl-Größe (im Gegensatz zu top_k)

**Werte:**
```
0.1   → Nur die allerbesten Tokens (~10%)
0.5   → Mittlere Auswahl (~50%)
0.9   → Breite Auswahl (Standard)
0.95  → Sehr breite Auswahl
1.0   → Alle möglichen Tokens
```

**Interaktion mit temperature:**
```
temp=0.3, top_p=0.5  → Sehr fokussiert (Fakten)
temp=0.8, top_p=0.9  → Ausgewogen (Standard)
temp=1.2, top_p=0.95 → Sehr kreativ
```

---

### top_k (1-100, default: 40)

**Was ist das?**
- Limitiert Auswahl auf Top-K wahrscheinlichste Tokens
- Feste Anzahl (im Gegensatz zu top_p)

**Werte:**
```
1    → Greedy (immer bestes Token) = temp=0
10   → Sehr eng
40   → Standard
100  → Breit
```

**top_k vs. top_p:**
- top_k = feste Anzahl
- top_p = dynamisch basierend auf Wahrscheinlichkeit
- Meist nur EINES verwenden (top_p empfohlen)

---

### min_p (0.0 - 1.0, default: 0.0)

**Was ist das?**
- Minimum-Probability-Threshold
- Filtert sehr unwahrscheinliche Tokens aus

**Verwendung:**
```
0.0   → Kein Filter (Standard)
0.05  → Entfernt die letzten 5%
0.1   → Entfernt die letzten 10%
```

---

### typical_p (0.0 - 1.0, default: 0.7)

**Was ist das?**
- "Typical Sampling" - wählt "typische" Tokens
- Alternative zu top_p/top_k

**Technisch:**
- Basiert auf Information Content statt Wahrscheinlichkeit
- Filtert sowohl sehr wahrscheinliche als auch sehr unwahrscheinliche Tokens

**Verwendung:** Meist deaktiviert (1.0) wenn top_p verwendet wird

---

## Output-Kontrolle

### num_predict (-1 oder 1-999999, default: -1)

**Was ist das?**
- Maximale Anzahl zu generierender Tokens
- `-1` = unbegrenzt (bis Modell stoppt)

**Werte:**
```
50    → Sehr kurze Antwort (~40 Wörter)
200   → Kurze Antwort (~150 Wörter)
500   → Mittlere Antwort (~375 Wörter)
1000  → Lange Antwort (~750 Wörter)
2000  → Sehr lange Antwort (~1500 Wörter)
-1    → Unbegrenzt (Standard)
```

**Wichtig:**
- Ist ein **Maximum**, kein Minimum!
- Modell kann früher stoppen wenn "fertig"
- Nützlich für Benchmarks (fixe Länge)

**Anwendungsfälle:**
- **Kurze Antworten:** 100-200
- **Chat:** 500
- **Artikel:** 1000-2000
- **Keine Limits:** -1

---

### stop (array, default: [])

**Was ist das?**
- Liste von Sequenzen, bei denen Generation stoppt
- Nützlich für strukturierte Outputs

**Beispiele:**
```json
"stop": ["\n\n"]        // Stop bei Doppel-Zeilenumbruch
"stop": ["END", "###"]  // Stop bei Keywords
"stop": ["\n---\n"]     // Stop bei Separator
```

**Anwendung:**
```
Prompt: "Schreibe ein Gedicht über Winter.\n\n"
stop: ["\n\n\n"]  // Stop nach Gedicht (vor Erklärung)
```

---

### penalize_newline (boolean, default: false)

**Was ist das?**
- Bestraft Zeilenumbrüche
- Erzwingt kompakte Antworten

**Verwendung:**
```
false  → Normal (Standard)
true   → Vermeidet Absätze (kompakter Text)
```

---

## Repetition-Kontrolle

### repeat_penalty (1.0 - 2.0, default: 1.1)

**Was ist das?**
- Bestraft Wiederholung von Tokens
- Verhindert langweilige, sich wiederholende Antworten

**Werte:**
```
1.0   → Keine Strafe (reine Modell-Ausgabe)
1.1   → Leichte Strafe (Standard)
1.2   → Mittlere Strafe
1.5   → Starke Strafe (vermeidet Wiederholungen stark)
2.0   → Sehr starke Strafe (kann zu unnatürlich wirken)
```

**Technisch:**
- Reduziert Wahrscheinlichkeit bereits verwendeter Tokens
- Wirkt auf die letzten N Tokens (siehe repeat_last_n)

**Anwendungsfälle:**
- **Code:** 1.0-1.1 (Wiederholungen OK)
- **Chat:** 1.1-1.2 (leicht variierend)
- **Kreatives Schreiben:** 1.2-1.3 (abwechslungsreich)
- **Vermeidung von Loops:** 1.5+ (bei Problemen)

---

### repeat_last_n (1-999999, default: 64)

**Was ist das?**
- Größe des Fensters für repeat_penalty
- Wie weit "zurückschauen" für Wiederholungen?

**Werte:**
```
32   → Kurzes Fenster (~24 Wörter)
64   → Standard
128  → Langes Fenster (~96 Wörter)
256  → Sehr langes Fenster
```

**Kombination:**
```
repeat_penalty=1.2, repeat_last_n=64
→ Bestraft Wiederholungen der letzten 64 Tokens
```

---

### presence_penalty (0.0 - 2.0, default: 0.0)

**Was ist das?**
- Bestraft bereits vorhandene Tokens (unabhängig von Häufigkeit)
- OpenAI-Style Parameter

**Unterschied zu repeat_penalty:**
- `repeat_penalty`: Bestraft häufige Wiederholungen
- `presence_penalty`: Bestraft bereits verwendete Tokens einmalig

**Werte:**
```
0.0   → Keine Strafe (Standard)
0.5   → Leichte Strafe
1.0   → Mittlere Strafe
1.5   → Starke Strafe (Standard in Ollama)
```

---

### frequency_penalty (0.0 - 2.0, default: 0.0)

**Was ist das?**
- Bestraft Tokens proportional zu ihrer Häufigkeit
- OpenAI-Style Parameter

**Werte:**
```
0.0   → Keine Strafe (Standard)
0.5   → Leichte Strafe
1.0   → Mittlere Strafe (Standard in Ollama)
1.5   → Starke Strafe
```

---

## Mirostat Sampling (Fortgeschritten)

### mirostat (0, 1, oder 2, default: 0)

**Was ist das?**
- Alternativer Sampling-Algorithmus
- Steuert "Perplexity" (Vorhersagbarkeit)

**Werte:**
```
0  → Deaktiviert (Standard) - nutze temperature/top_p
1  → Mirostat v1
2  → Mirostat v2 (empfohlen wenn Mirostat)
```

**Wann verwenden?**
- Bei Problemen mit zu langweiligen/repetitiven Antworten
- Alternative zu temperature-tuning
- **Meist nicht nötig!**

---

### mirostat_tau (0.0 - 10.0, default: 5.0)

**Was ist das?**
- Target Entropy (Ziel-Perplexity)
- Wie "überraschend" sollen Antworten sein?

**Werte:**
```
0.0   → Sehr vorhersehbar
5.0   → Standard
10.0  → Sehr überraschend
```

---

### mirostat_eta (0.0 - 1.0, default: 0.1)

**Was ist das?**
- Learning Rate für Mirostat
- Wie schnell anpassen an Ziel-Perplexity?

**Werte:**
```
0.05  → Langsame Anpassung
0.1   → Standard
0.2   → Schnelle Anpassung
```

---

## Context & Memory

### num_ctx (128 - 131072, default: 2048)

**Was ist das?**
- Context Window Größe
- Wie viel Text kann das Modell "sehen"?

**Siehe:** [HARDWARE_DETECTION.md](HARDWARE_DETECTION.md) für Details

**Wichtig:**
- Größerer Context = mehr RAM/VRAM
- Qwen3 unterstützt bis 128K nativ
- AIfred setzt automatisch basierend auf Hardware

---

### num_keep (1-999999, default: 0)

**Was ist das?**
- Anzahl Tokens aus Prompt zu behalten
- Bei Multi-Turn Chats wichtig

**Verwendung:**
```
0    → Standard (behalte alle)
-1   → Behalte alle Tokens
4    → Behalte erste 4 Tokens (z.B. System-Prompt)
```

---

### seed (integer, default: random)

**Was ist das?**
- Random Seed für Reproduzierbarkeit
- Gleicher Seed = gleiche Antwort (bei temp>0)

**Verwendung:**
```
-1         → Zufällig (Standard)
42         → Fester Seed (reproduzierbar)
123456789  → Beliebige Zahl
```

**Anwendungsfälle:**
- **Testing:** Fester Seed für identische Ergebnisse
- **Debugging:** Reproduzierbare Fehler
- **Produktion:** -1 (zufällig)

---

## Hardware & Performance

### num_gpu (0 - 999, default: -1)

**Was ist das?**
- Anzahl Modell-Layer auf GPU
- **NICHT Anzahl GPUs!**

**Siehe:** [HARDWARE_DETECTION.md](HARDWARE_DETECTION.md)

**AIfred setzt automatisch:**
- Kleine Modelle: Auto-Detect
- Große Modelle: Konservatives Limit
- AMD iGPU + 32B: Force CPU (0)

---

### num_thread (1-999, default: auto)

**Was ist das?**
- Anzahl CPU-Threads für Inferenz
- Nur relevant bei CPU-Modus

**Werte:**
```
Auto  → Ollama wählt optimal
4     → 4 Threads
8     → 8 Threads (gut für 8-Core CPU)
```

---

### num_batch (1-999, default: 512)

**Was ist das?**
- Batch Size für Prompt-Processing
- Höher = schneller, aber mehr RAM

---

## Presets für verschiedene Aufgaben

### 🎯 Fakten & Recherche (Präzise)
```json
{
  "temperature": 0.3,
  "top_p": 0.5,
  "repeat_penalty": 1.2,
  "num_predict": 500
}
```

### 💻 Code-Generierung (Sehr präzise)
```json
{
  "temperature": 0.2,
  "top_p": 0.5,
  "repeat_penalty": 1.1,
  "num_predict": 1000
}
```

### 💬 Chat / Q&A (Ausgewogen)
```json
{
  "temperature": 0.8,
  "top_p": 0.9,
  "repeat_penalty": 1.1,
  "num_predict": -1
}
```

### 🎨 Kreatives Schreiben (Kreativ)
```json
{
  "temperature": 1.2,
  "top_p": 0.95,
  "repeat_penalty": 1.0,
  "num_predict": 2000
}
```

### 📝 Gedichte (Sehr kreativ)
```json
{
  "temperature": 1.4,
  "top_p": 0.95,
  "repeat_penalty": 0.9,
  "num_predict": 300
}
```

### 🔬 Benchmarks (Reproduzierbar)
```json
{
  "temperature": 0.3,
  "seed": 42,
  "num_predict": 200
}
```

---

## API-Verwendung

### Python (ollama-python)
```python
import ollama

response = ollama.chat(
    model="qwen3:8b",
    messages=[{"role": "user", "content": "Hallo"}],
    options={
        "temperature": 0.8,
        "top_p": 0.9,
        "num_predict": 500,
        "repeat_penalty": 1.1
    }
)
```

### REST API
```bash
curl http://localhost:11434/api/chat -d '{
  "model": "qwen3:8b",
  "messages": [{"role": "user", "content": "Hallo"}],
  "options": {
    "temperature": 0.8,
    "num_predict": 500
  }
}'
```

### CLI
```bash
ollama run qwen3:8b \
  --temperature 0.8 \
  --num-predict 500 \
  "Dein Prompt hier"
```

---

## Best Practices

### DO ✅
- Starte mit Defaults (temp=0.8, top_p=0.9)
- Passe temperature für Aufgabe an
- Nutze num_predict für Benchmarks
- Setze seed für reproduzierbare Tests
- Kombiniere temperature + top_p sinnvoll

### DON'T ❌
- Extreme Werte (temp>1.8, top_p<0.3)
- Zu viele Parameter gleichzeitig ändern
- Mirostat + temperature/top_p mischen
- num_ctx zu groß setzen (RAM!)
- repeat_penalty zu hoch (>1.5)

---

## Troubleshooting

### Problem: Langweilige, repetitive Antworten
**Lösung:**
- Erhöhe `temperature` (0.8 → 1.0)
- Erhöhe `repeat_penalty` (1.1 → 1.3)
- Prüfe `top_p` (sollte ~0.9 sein)

### Problem: Wirre, unverständliche Antworten
**Lösung:**
- Senke `temperature` (1.5 → 0.8)
- Senke `top_p` (0.95 → 0.85)
- Prüfe ob Modell zu klein für Aufgabe

### Problem: Antworten zu kurz
**Lösung:**
- Erhöhe `num_predict` (-1 oder 1000+)
- Prüfe Prompt (fordere längere Antwort)
- Checke `stop` Sequenzen

### Problem: Antworten zu lang
**Lösung:**
- Setze `num_predict` (z.B. 300)
- Füge `stop` Sequenzen hinzu
- Präzisiere Prompt ("in 3 Sätzen")

### Problem: Nicht reproduzierbar
**Lösung:**
- Setze `seed` auf feste Zahl
- Verwende `temperature=0` für deterministisch
- Prüfe ob andere Parameter auch gleich

---

## Weitere Dokumentation

- [HARDWARE_DETECTION.md](HARDWARE_DETECTION.md) - Hardware & Context Limits
- [API_SETUP.md](API_SETUP.md) - API Keys & Konfiguration
- [INDEX.md](INDEX.md) - Hauptindex

---

**Stand:** 2025-10-17
**Referenz:** [Ollama API Docs](https://docs.ollama.com/api)
