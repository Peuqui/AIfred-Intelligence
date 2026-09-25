# Tensor-Split-Benchmark: Speed-Variante gegen vollen Kontext

> **English version:** [tensor-split.md](../../en/benchmarks/tensor-split.md)

> **Historischer Benchmark.** Gemessen Anfang 2026 auf dem früheren Aufbau mit
> Tesla P40 (Pascal). Der heutige AIfred-Rechner läuft mit 2× Quadro RTX 8000 +
> 3× Tesla V100; die Zahlen beschreiben also nicht die aktuelle Leistung, bleiben
> aber eine brauchbare Referenz für alle, die P40 oder ähnliche Pascal-Karten fahren.

Praxisvergleich zweier llama-swap-Konfigurationen für dasselbe Modell,
gemessen über AIfreds Multi-Agent-Tribunal (AIfred + Sokrates + Salomo, 2 Runden).

**Datum:** 2026-02-19

---

## Test-Setup

| Parameter | Wert |
|-----------|-------|
| **Modell** | Qwen3-Next-80B-A3B-Instruct-Q4_K_M (46,6 GB) |
| **Hardware** | RTX 8000 (CUDA0, 48 GB GDDR6, USB4-eGPU) + Tesla P40 (CUDA1, 24 GB GDDR5X, OCuLink-eGPU), beide PCIe 3.0 x4 |
| **CUDA-Reihenfolge** | `CUDA_DEVICE_ORDER=FASTEST_FIRST` |
| **Prompt** | „Ist Wasser nass?" (philosophische Frage, Multi-Agent-Debatte mit 6 Turns) |
| **Modus** | Auto-Konsens (AIfred → Sokrates R1 → Salomo R1 → AIfred R2 → Sokrates R2 → Salomo R2) |
| **llama.cpp** | v8076, `GGML_CUDA_GRAPH_OPT=1` |
| **Basis-Flags** | `-ngl 99 -np 1 -fit off --flash-attn on -t 4 -b 512 -ub 256` |

### Verglichene Konfigurationen

| | Speed-Variante (11:1) | Normal (2:1) |
|---|---|---|
| **llama-swap-Modell** | `qwen3-next-80b-a3b-instruct-q4_k_m-speed` | `qwen3-next-80b-a3b-instruct-q4_k_m` |
| **Tensor-Split** | `-ts 11,1` (92 % auf der RTX 8000) | `-ts 2,1` (67 % auf der RTX 8000) |
| **Kontext** | 32.768 Token | 262.144 Token (natives Maximum) |
| **KV-Cache-Typ** | `-ctk q4_0 -ctv q4_0` | `-ctk q4_0 -ctv q4_0` |
| **VRAM-Belegung** | 52.082 MB (35.275 + 16.807) | ~65.000 MB (ausgeglichen) |

Die Speed-Variante wurde bei der Kalibrierung per binärer Suche gefunden:

```
99:1 → failed    50:1 → failed    26:1 → failed    14:1 → failed
 8:1 → fits      11:1 → fits      12:1 → failed
Result: 11:1 in 7 iterations
```

---

## Ergebnisse pro Turn

### Runde 1 (Erstantworten)

| Turn | Metrik | Speed (11:1) | Normal (2:1) | Delta |
|------|--------|-------------|-------------|-------|
| **AIfred** | TTFT | 3,89 s | 3,86 s | +0,8 % |
| | PP | 310,1 tok/s | 312,5 tok/s | −0,8 % |
| | **Gen tok/s** | **33,4** | **29,0** | **+15,2 %** |
| | Token | 321 | 305 | +5,2 % |
| | Inference | 9,6 s | 10,5 s | −8,6 % |
| **Sokrates** | TTFT | 8,39 s | 8,16 s | +2,8 % |
| | PP | 319,0 tok/s | 325,9 tok/s | −2,1 % |
| | **Gen tok/s** | **35,0** | **30,2** | **+15,9 %** |
| | Token | 799 | 761 | +5,0 % |
| | Inference | 22,9 s | 25,2 s | −9,1 % |
| **Salomo** | TTFT | 7,76 s | 7,33 s | +5,9 % |
| | PP | 331,3 tok/s | 343,3 tok/s | −3,5 % |
| | **Gen tok/s** | **30,9** | **28,3** | **+9,2 %** |
| | Token | 548 | 558 | −1,8 % |
| | Inference | 17,7 s | 19,7 s | −10,2 % |

### Runde 2 (Überarbeitung + kritische Prüfung + Synthese)

| Turn | Metrik | Speed (11:1) | Normal (2:1) | Delta |
|------|--------|-------------|-------------|-------|
| **AIfred R2** | TTFT | 11,57 s | 11,30 s | +2,4 % |
| | PP | 334,2 tok/s | 339,3 tok/s | −1,5 % |
| | **Gen tok/s** | **18,8** | **20,5** | **−8,3 %** |
| | Token | 332 | 430 | −22,8 % |
| | Inference | 17,6 s | 21,0 s | −16,2 % |
| **Sokrates R2** | TTFT | 13,14 s | 13,10 s | +0,3 % |
| | PP | 333,0 tok/s | 338,3 tok/s | −1,6 % |
| | **Gen tok/s** | **27,2** | **20,8** | **+30,6 %** |
| | Token | 719 | 519 | +38,5 % |
| | Inference | 26,5 s | 24,9 s | +6,4 % |
| **Salomo R2** | TTFT | 12,47 s | 11,65 s | +7,0 % |
| | PP | 336,3 tok/s | 347,7 tok/s | −3,3 % |
| | **Gen tok/s** | **19,2** | **21,2** | **−9,4 %** |
| | Token | 368 | 471 | −21,9 % |
| | Inference | 19,2 s | 22,3 s | −13,9 % |

---

## Zusammengefasste Auswertung

### Durchschnitt je Runde

| Metrik | Speed R1 | Normal R1 | Delta | Speed R2 | Normal R2 | Delta |
|--------|---------|----------|-------|---------|----------|-------|
| TTFT | 6,68 s | 6,45 s | +3,6 % | 12,39 s | 12,02 s | +3,1 % |
| PP | 320,1 tok/s | 327,2 tok/s | −2,2 % | 334,5 tok/s | 341,8 tok/s | −2,1 % |
| **Gen tok/s** | **33,1** | **29,2** | **+13,4 %** | **21,7** | **20,8** | **+4,3 %** |

### Summen der Session

| Metrik | Speed (11:1) | Normal (2:1) | Delta |
|--------|-------------|-------------|-------|
| Inference-Zeit gesamt | 113,5 s | 123,6 s | **−8,2 %** |
| Erzeugte Token gesamt | 3.087 | 3.044 | +1,4 % |
| Effektiver Durchsatz | 27,2 tok/s | 24,6 tok/s | **+10,6 %** |
| TTFT gesamt (Summe) | 57,2 s | 55,5 s | +3,1 % |
| Durchschnittliche PP | 327,3 tok/s | 334,5 tok/s | −2,2 % |
| Kontextauslastung | 11 % von 32K | 1 % von 262K | — |

---

## Wichtigste Erkenntnisse

1. **Die Generierung ist mit 11:1 in Runde 1 um 10–15 % schneller** — solange der Prompt-Kontext
   noch klein ist. Weniger Layer auf der langsameren P40 (346 GB/s gegen 672 GB/s bei der RTX 8000)
   verringern den Engpass pro Token bei der autoregressiven Generierung.

2. **In Runde 2 schrumpft der Vorteil (~4 %)** — mit wachsendem Kontext entfällt mehr der
   Generierungszeit auf die Attention (die mit der Sequenzlänge skaliert). Die R2-Ergebnisse streuen
   stärker (18,8–27,2 tok/s), was nahelegt, dass Ausgabelänge und inhaltliche Komplexität bei diesen
   Kontextgrößen mehr ausmachen als das Split-Verhältnis.

3. **Das Prompt-Processing (PP) ist mit dem normalen 2:1-Split ~2 % schneller** — PP profitiert von
   parallelen KV-Cache-Schreibvorgängen auf beiden GPUs. Bei 11:1 landen 92 % des KV-Caches auf einer
   GPU, was einen Engpass erzeugt. Der Effekt ist klein, zieht sich aber durch alle Turns.

4. **Die TTFT ist mit der Speed-Variante ~3 % langsamer** — TTFT = PP + Modell-Overhead + Latenz bis
   zum ersten Token. Da PP beim aggressiven Split etwas langsamer ist, folgt die TTFT. Absolut liegt der
   Unterschied unter 1 s.

5. **Inference-Zeit ≠ Generierungstempo** — eine kürzere Wanduhr-Inference bedeutet nicht immer
   mehr tok/s. Beispiel: AIfred R2 ist mit der Speed-Variante nach 17,6 s fertig (332 Token, 18,8 tok/s),
   normal nach 21,0 s (430 Token, 20,5 tok/s). Die Speed-Variante ist nach der Wanduhr schneller,
   weil sie **weniger Token** erzeugt hat — nicht, weil sie sie schneller erzeugt hat. Das Modell ist
   nicht deterministisch, die Ausgabelänge schwankt also zwischen den Läufen. Immer tok/s (Durchsatz)
   vergleichen, nicht die Inference-Zeit (die Durchsatz und Ausgabelänge vermischt).

6. **Die Gesamtzeit nach der Wanduhr ist mit der Speed-Variante 10 s kürzer** — 113,5 s gegen 123,6 s.
   Das spiegelt eine Mischung aus tatsächlich schnellerer Generierung in R1 und nicht deterministischen
   Unterschieden in der Ausgabelänge über 6 Turns wider.

---

## Wann welche Konfiguration

| Anwendungsfall | Empfohlene Konfiguration | Grund |
|----------|--------------------|--------|
| Kurze Gespräche (<8K Kontext) | Speed (11:1, 32K) | 10–15 % schnellere Generierung |
| Tribunal über mehrere Turns (typisch) | Speed (11:1, 32K) | insgesamt ~10 % schneller, 32K reichen |
| Lange Gespräche (>32K Token) | Normal (2:1, 262K) | Die Speed-Variante würde den Verlauf abschneiden |
| RAG mit großem Kontext | Normal (2:1, 262K) | Voller Kontext für die Dokumentensuche nötig |
| Stapelverarbeitung (viele kurze Prompts) | Speed (11:1, 32K) | Maximiert den Durchsatz pro Anfrage |

**Faustregel:** Standardmäßig die Speed-Variante nutzen. Nur auf Normal wechseln, wenn du
tatsächlich mehr als 32K Kontext-Token brauchst.

---

## Details zur Kalibrierung

Die Speed-Variante wurde automatisch von AIfreds VRAM-Kalibrierung ermittelt:

- **Phase 1:** Test des nativen Kontexts → 262.144 Token passen mit 2:1-Split
- **Phase 2:** Binäre Suche nach dem maximalen Tensor-Split bei 32K Kontext
  - 7 Verhältnisse getestet (99:1 → 11:1), konvergiert in ~7 Minuten
  - Ergebnis: 11:1 (91,7 % auf der RTX 8000, 8,3 % auf der P40)
  - VRAM-Aufteilung: CUDA0 = 35.275 MB, CUDA1 = 16.807 MB

Beide Konfigurationen stehen in der llama-swap-Config und lassen sich über den Modellnamen wählen:
- `qwen3-next-80b-a3b-instruct-q4_k_m` → voller Kontext, ausgeglichener Split
- `qwen3-next-80b-a3b-instruct-q4_k_m-speed` → reduzierter Kontext, aggressiver Split

---

## Hardware-Kontext

Siehe [llamacpp-setup.md](../guides/llamacpp-setup.md#benchmark-tensor-split-optimierung-18022026)
für synthetische `llama-bench`-Ergebnisse auf derselben Hardware, einschließlich Messungen pro
Split-Verhältnis für Qwen3-32B.

---

*Erzeugt aus AIfred-Session-Daten vom 2026-02-19. Sessions: `a903c6f6` (Speed) und `0adf7397` (Normal).*
