# llama.cpp + llama-swap Einrichtungsanleitung

> **English version:** [llamacpp-setup.md](../../en/guides/llamacpp-setup.md)

Referenzdokument für die llama.cpp-Integration in AIfred über llama-swap.
Wird aktualisiert, wenn sich die Hardware ändert oder neue llama.cpp-Releases relevante Änderungen bringen.

**Zuletzt aktualisiert:** 25.09.2026

> ⚠️ **Hinweis zur Hardware:** Alle Abschnitte mit dem Vermerk **(historisch, P40-Ära)**
> wurden im Februar 2026 auf dem vorherigen GPU-Setup gemessen (Tesla P40 / Quadro
> RTX 8000 / RTX 3090 Ti). Der aktuelle Rechner läuft mit 2× Quadro RTX 8000
> (Turing, Compute Capability 7.5) + 3× Tesla V100 32 GB (Volta, Compute
> Capability 7.0), insgesamt 192 GB VRAM — eine Pascal-Karte gibt es nicht mehr.
> Ablauf, Konfigurationsschritte und das Verhalten von Autoscan/Kalibrierung in den
> übrigen Abschnitten gelten für das aktuelle Setup; die absoluten Zahlen der
> historischen Abschnitte nicht.

---

## Hardware-Überblick

### GPU-Vergleich (historisch, P40-Ära)

| Spec | Tesla P40 | Quadro RTX 8000 | RTX 3090 Ti |
|------|-----------|-----------------|-------------|
| Architektur | Pascal (GP102) | Turing (TU102) | Ampere (GA102) |
| Compute Capability | 6.1 | 7.5 | 8.6 |
| VRAM | 24 GB GDDR5X | 48 GB GDDR6 | 24 GB GDDR6X |
| Bandbreite | 346 GB/s | 672 GB/s | 1008 GB/s |
| Tensor Cores | Keine | 576 (2. Gen.) | 336 (3. Gen.) |
| FP16 | 1/64 von FP32 (!) | Voll (über TC) | Voll (über TC) |
| NVLink | Nein | Ja (100 GB/s) | Ja (112,5 GB/s) |
| TDP | 250W | 295W | 450W |

### NVIDIA-Optimierungen für llama.cpp (CES 2026, historisch, P40-Ära)

| Optimierung | Wirkung | P40 | RTX 8000 | 3090 Ti |
|---|---|---|---|---|
| CUDA Graphs | bis zu 35 % schnellere Token-Generierung | Ja | Ja | Ja |
| MMVQ Kernel | Bessere quantisierte Inferenz | Ja | Ja | Ja |
| MMQ (INT8 TC) | INT8-Tensor-Core-Kernel | Nein | Ja | Ja |
| Flash Attention | Weniger VRAM, schnellere PP | Ja (modellabhängig!) | Ja | Ja |
| GPU Token Sampling | Eliminiert CPU-GPU-Transfers | Ja | Ja | Ja |
| Model Loading | bis zu 65 % schneller | Ja | Ja | Ja |
| FP8 W8A16 | FP8 nur für Gewichte | Nein | Ja (vLLM) | Ja (Marlin) |
| NVFP4 | Neues Quantisierungsformat | Nein | Nein | Nein |
| vLLM-kompatibel | Erfordert CC >= 7.0 | Nein | Ja | Ja |

---

## Performance-Parameter

### Alle relevanten llama-server-Flags

| Parameter | Syntax | Default | Beschreibung |
|---|---|---|---|
| `-ngl` | `-ngl 99` / `-ngl auto` | `auto` | Anzahl der auf die GPU ausgelagerten Layer |
| `-c` | `-c 8192` | aus dem Modell | Context-Größe (KV-Cache skaliert linear) |
| `-fa` | `-fa on` / `--flash-attn off` | `auto` | Flash Attention (global, nicht pro GPU) |
| `-ctk` | `-ctk q8_0` | `f16` | KV-Cache-Typ der Keys (q8_0, q4_0, f16, f32) |
| `-ctv` | `-ctv q8_0` | `f16` | KV-Cache-Typ der Values |
| `-b` | `-b 2048` | `2048` | Logische Batch-Größe (Prompt Processing) |
| `-ub` | `-ub 512` | `512` | Physische Batch-Größe (GPU-Batch) |
| `-sm` | `-sm layer` | | Split-Modus: layer, row, none |
| `-ts` | `-ts 1,2.5` | | Tensor-Split-Verhältnis zwischen den GPUs |
| `-mg` | `-mg 1` | `0` | Haupt-GPU (Output-Layer, Scratch-Buffer) |
| `--mlock` | `--mlock` | aus | RAM-Seiten festpinnen, Swapping verhindern |
| `--no-mmap` | `--no-mmap` | mmap an | Modell vollständig laden statt lazy mmap |
| `-t` | `-t 8` | auto | CPU-Threads (Generierung) |
| `-tb` | `-tb 16` | wie `-t` | CPU-Threads (Batch/Prompt) |
| `-np` | `-np 1` | `auto` (=4!) | Parallele Slots (Multi-User) |
| `-fit` | `-fit off` | `on` | Parameter automatisch an den VRAM anpassen (der Autoscan setzt `-fit off` in Multi-GPU-Profilen; auf der P40 stürzte `-fit on` ab) |
| `-dev` | `-dev CUDA0,CUDA1` | | Explizite GPU-Auswahl |
| `-ot` | `-ot "regex=DEVICE"` | | Tensor-Platzierung überschreiben (ik_llama.cpp) |

### Umgebungsvariablen

```bash
export CUDA_DEVICE_ORDER=FASTEST_FIRST # Schnellste GPU wird CUDA0 (siehe Abschnitt Tensor-Split)
export GGML_CUDA_GRAPH_OPT=1           # 10-15 % schnellere Token-Generierung
```

### KV-Cache-Quantisierung

> Die Tempo-Angaben in dieser Tabelle stammen aus der P40-Ära (siehe Benchmark
> unten). Die Kalibrierung bevorzugt heute `f16`-KV und geht nur bei Bedarf auf
> `q8_0` — siehe [Was die Kalibrierung zusätzlich macht](#was-die-kalibrierung-aifred-ui-zusätzlich-macht).

| Typ | VRAM-Ersparnis ggü. f16 | Einfluss auf Tempo (PP) | Einfluss auf Qualität | Hinweise |
|---|---|---|---|---|
| `q8_0` | ~50 % | **+30-134 % schneller** | Vernachlässigbar | **Empfohlen** |
| `q4_0` | ~72 % | +20-30 % schneller | Spürbar bei den Keys | Nur wenn der VRAM extrem knapp ist |
| `f16` | Basis | Basis | Keiner | Default |

**Wichtig:**
- KV-Cache-Quantisierung erfordert Flash Attention (`--flash-attn on`)
- Q8_0 ist bei der PP schneller als Q4_0 (weniger Dequantisierungs-Overhead)
- Die Generierungsgeschwindigkeit ist bei allen Varianten gleich (~46-63 tok/s je nach Modell)
- KV-Quant ≠ Modell-Quant: KV-Quant spart Attention-Bandbreite, Modell-Quant spart Matmul-Rechenaufwand

### P40-spezifische Hinweise (historisch, P40-Ära)

| Parameter | Empfehlung | Grund |
|---|---|---|
| `-fit` | `off` | `-fit on` stürzt auf Pascal mit CUDA OOM bei `cudaMemGetInfo` ab |
| `-np` | `1` | Default `auto` setzt 4 Slots = 4× Compute-Buffer-VRAM |
| `--flash-attn` | `on` | Weniger VRAM (Compute-Buffer -86 %), schnellere PP für Qwen3 |
| `-ctk`/`-ctv` | `q8_0` | KV-Cache halbieren, vernachlässigbarer Qualitätsverlust |

---

## Benchmark: Tesla P40 (2x 24 GB, llama.cpp v8076) — historisch, P40-Ära

Datum: 17.02.2026. Hardware: 2x Tesla P40 (24 GB GDDR5X, PCIe 3.0 x4 über OCuLink-eGPU).
llama.cpp-Version: 8076 (d61290111), kompiliert mit `GGML_CUDA=ON`.

**Test-Prompt:** `"Write a short poem about the sun in exactly 4 lines."` (21 Tokens, max_tokens=100)
**Basis-Parameter:** `-ngl 99 -np 1 -fit off -t 4 -b 2048 -ub 512`

### Qwen3-4B-Instruct-2507 (Q4_K_M, Single GPU CUDA0)

| Konfiguration | Context | Prompt (tok/s) | Gen (tok/s) | VRAM gesamt (MiB) | KV-Buffer (MiB) | Compute-Buffer (MiB) |
|---|---|---|---|---|---|---|
| FA off, FP16 KV | 32K | 205,8 | 61,7 | 9.283 | 4.608 | 2.142 |
| **FA on, FP16 KV** | 32K | **352,9** | 63,9 | 7.441 | 4.608 | 302 |
| **FA on, Q8 KV** | 32K | **481,3** | 62,5 | **5.293** | **2.448** | 302 |
| FA on, Q8 KV | 131K | 490,2 | 62,7 | 12.747 | 9.792 | 410 |
| FA on, **Q4 KV** | 32K | 391,4 | 60,8 | 4.141 | **1.296** | 302 |

**Ergebnis:** FA + Q8 KV ist auf der P40 der klare Sieger:
- **PP: +134 % schneller** als die Basis (481 gegenüber 206 tok/s)
- **Gen: unverändert** (~62 tok/s)
- **VRAM: -43 %** (5.293 gegenüber 9.283 MiB)
- **Max. Context: 131K** (nativ 128K) auf einer einzelnen P40 mit 4B Q4_K_M

### Qwen3-30B-A3B-Instruct-2507 (Q8_0, Dual GPU, `-sm layer --tensor-split 1,1`)

| Konfiguration | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 VRAM (MiB) | GPU1 VRAM (MiB) | Frei gesamt (MiB) |
|---|---|---|---|---|---|---|
| FA off, FP16 KV | 32K | 95,8 | 46,8 | 19.913 | 18.837 | 7.064 |
| FA on, FP16 KV | 32K | 91,7 | 47,4 | 17.953 | 16.929 | 10.932 |
| **FA on, Q8 KV** | 32K | **124,6** | 45,8 | 17.215 | 16.251 | **12.348** |
| FA on, Q8 KV | 65K | 114,6 | 46,5 | 18.353 | 17.161 | 10.300 |
| FA on, Q8 KV | 98K | 117,7 | 46,4 | 19.491 | 18.071 | 8.252 |
| FA on, Q8 KV | 131K | 122,4 | 47,2 | 20.631 | 18.983 | 6.200 |
| FA on, Q8 KV | 200K | 108,0 | 46,3 | 21.777 | 20.103 | 3.934 |
| FA on, Q8 KV | 220K | 115,1 | 46,9 | 22.353 | 20.581 | 2.880 |
| FA on, **Q4 KV** | 32K | 123,2 | 46,1 | 16.815 | 15.883 | 13.116 |

**Ergebnis:** FA + Q8 KV ist auch für 30B optimal:
- **PP: +30 % schneller** (124,6 gegenüber 95,8 tok/s bei 32K)
- **Gen: stabil ~46-47 tok/s**, unabhängig vom Context-Fenster
- **VRAM: -5.284 MiB frei geworden** gegenüber der Basis
- **Max. Context: 220K** (nativ 262K) auf 2x P40 mit 30B Q8_0
- 262K stürzt ab: "failed to allocate compute buffers" (793 MiB fehlen)

### Qwen3-Next-80B-A3B-Instruct (Q4_K_M, 48 Layer, 512 Experten/10 aktiv)

Modell: 46,6 GB (Q4_K_M), ~0,97 GB/Layer. 2x P40 = 48 GB VRAM.
Basis-Parameter: `-np 1 -fit off --flash-attn on -ctk q8_0 -ctv q8_0 -sm layer --tensor-split 1,1 -t 4 -b 512 -ub 256`

#### Layer-Offloading (`-ngl`): Abwägung Tempo gegen Context

| -ngl | CPU-Layer (GB) | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 frei (MiB) |
|---|---|---|---|---|---|
| 44 | 4 (~3,9 GB) | 4K | 75,7 | **24,1** | 452 |
| 44 | 4 | 32K | 91,8 | **24,3** | 232 |
| 44 | 4 | 57K | 81,5 | **22,9** | 42 |
| 44 | 4 | 65K | ABSTURZ | - | - |
| 42 | 6 (~5,8 GB) | 4K | 65,2 | **21,9** | 1.344 |
| 42 | 6 | 131K | 75,3 | **21,7** | 236 |
| 42 | 6 | 144K | 75,3 | **21,5** | 92 |
| 42 | 6 | 160K | ABSTURZ | - | - |
| **40** | **8 (~7,7 GB)** | **262K** | 63,7 | **20,5** | 380 |

#### MoE-Offloading (`-cmoe`): Experten auf der CPU, Attention auf der GPU

| Modus | Context | Prompt (tok/s) | Gen (tok/s) | GPU0 belegt (MiB) |
|---|---|---|---|---|
| `-ngl 99 -cmoe` | 4K | 3,2 | **6,8** | 1.539 |
| `-ngl 99 -cmoe` | 262K | 3,4 | **6,4** | 3.143 |
| `-ngl 99 -ncmoe 40` | 4K | 4,4 | **9,0** | 1.539/8.797 |

**Ergebnisse und Empfehlung:**

- **`-ngl 40` ist der Sweet Spot:** Voller nativer Context (262K) bei nur 15 % Verlust an Gen-Tempo (20,5 gegenüber 24,1 tok/s). ~7,7 GB auf der CPU passen locker in 30 GB RAM.
- **`-ngl 44`**: Maximales Tempo (24 tok/s), aber nur 57K Context — zu wenig für langes Reasoning.
- **`-cmoe`**: 262K Context, aber **3,5× langsamer** (6,4 tok/s). Jedes Token muss 10 Experten über PCIe 3.0 laden. Nur sinnvoll, wenn das Tempo keine Rolle spielt.
- **Gen-Tempo ist Context-unabhängig**: ~20-24 tok/s, egal ob bei 4K oder 262K.
- `-ngl 99` stürzt ab (47-GB-Modell passt nicht in 48 GB VRAM)
- MoE-Gewichte werden per mmap geladen — erscheinen im RAM als "buffer/cache", nicht als "used"
- Der KV-Cache liegt hauptsächlich im VRAM (proportional zu den GPU-Layern), nicht im CPU-RAM

### Wichtigste Erkenntnisse

1. **Flash Attention auf Pascal (P40) ist NICHT pauschal langsamer.** Bei Qwen3-Modellen ist die PP bis zu 134 % schneller. Der bekannte FA-Nachteil betrifft hauptsächlich GLM-4.7-FLASH (Issue #19020). Ursache: die Head-Dimension (siehe unten).

2. **Der Compute-Buffer ist der größte VRAM-Verbraucher, nicht der KV-Cache.** FA reduziert den Compute-Buffer um 86 % (2.142 → 302 MiB beim 4B-Modell).

3. **Q8-KV-Quantisierung beschleunigt die PP zusätzlich**, weil sie die Speicherbandbreite in der Attention-Berechnung reduziert.

4. **Gen-Tempo ist Context-unabhängig.** Egal ob bei 32K oder 262K: ~46-47 tok/s für 30B, ~62-63 tok/s für 4B, ~20-24 tok/s für 80B.

5. **Pflicht-Parameter für die P40:** `-fit off -np 1 --flash-attn on -ctk q8_0 -ctv q8_0`
   - `-fit on` stürzt auf Pascal mit CUDA OOM ab
   - `-np auto` setzt 4 Slots = unnötiger VRAM-Verbrauch
   - FA + Q8 KV spart VRAM und ist schneller

6. **Layer-Offloading gegen MoE-Offloading bei zu großen Modellen:**
   - `-ngl <N>`: Ganze Layer auf der CPU. Das Tempo sinkt moderat (~1 tok/s pro 2 Layer).
   - `-cmoe`: Nur die MoE-Experten auf der CPU. Das Tempo sinkt auf ~30 % (PCIe-Flaschenhals).
   - **Empfehlung:** Layer-Offloading bevorzugen, außer maximaler Context ist wichtiger als Tempo.

7. **`-ub` (Micro-Batch) verkleinert den Compute-Buffer ohne Verlust an Gen-Tempo.**
   `-ub 256` statt `512` halbiert den Compute-Buffer (196 gegenüber 392 MiB bei 30B).
   Das PP-Tempo sinkt minimal (117 gegenüber 120 tok/s). Gen-Tempo identisch.

### Warum FA auf der P40 modellabhängig ist (technisch, historisch, P40-Ära)

Quelle: `ggml/src/ggml-cuda/fattn.cu` und `fattn-tile.cuh` in llama.cpp.

Die P40 (CC 6.1) hat **keine Tensor Cores** und **kein schnelles FP16** (`FAST_FP16_AVAILABLE` ist
für CC 6.1 in `common.cuh:230` ausdrücklich ausgeschlossen). Daher:

1. **Kernel-Auswahl:** Die P40 nutzt den generischen `tile`-Kernel im FP32-Modus. Turing+-GPUs nutzen MMA-Kernel (Tensor Cores).

2. **Vec-Kernel (für die Token-Generierung):** Nur verfügbar bei `dkq <= 256 && dkq % 64 == 0`. Qwen3 (dkq=128) erfüllt das, GLM (dkq=576) nicht.

3. **FP32-Rechenaufwand skaliert mit der Head-Dimension:**
   - Qwen3 (dkq=128): 128 FP32-MADs pro Skalarprodukt, Tile-Konfiguration: `nbatch_fa=64, occupancy=3`
   - GLM (dkq=576): 576 FP32-MADs (4,5× mehr), Tile-Konfiguration: `nbatch_fa=32, occupancy=2`

4. **Standard-Attention (ohne FA) nutzt cuBLAS GEMM**, das auf der P40 für FP32 hochoptimiert ist. Bei großen Dimensionen (576) hat cuBLAS einen höheren Durchsatz als der fusionierte FA-Kernel.

**Faustregel:** Modelle mit `attention.key_length <= 256` profitieren auf der P40 von FA. Modelle mit größeren Head-Dimensionen (DeepSeek/MLA-Architektur) können langsamer sein.

---

## Heterogenes Multi-GPU (P40 + RTX 8000) — historisch, P40-Ära

Die Erkenntnisse unten stammen vom Setup P40 + RTX 8000. Das llama.cpp-Verhalten
unter „Was funktioniert“ / „Was NICHT funktioniert“ ist nicht P40-spezifisch; die
Messwerte und Split-Verhältnisse sind es.

### Was funktioniert

- **Layer-Split (`-sm layer`)** über PCIe-x4-eGPU: Nur ~8 KB pro Layer-Grenze, kein Bandbreitenproblem
- **Ungleiche Verteilung (`-ts`)**: Mehr Layer auf der schnelleren GPU
- **`-mg` auf der RTX 8000**: Output-Layer und Scratch-Buffer auf der schnelleren GPU

### Was NICHT funktioniert

- **Row-Split (`-sm row`)**: Ständige All-Reduce-Synchronisation, über PCIe x4 unpraktikabel
- **Graph-Split (`-sm graph`, ik_llama.cpp)**: Setzt gleich starke GPUs voraus, kein Auto-Balancing
- **Trennung des KV-Caches**: Der KV-Cache ist architekturbedingt an die GPU seines Layers gebunden
- **Flash Attention pro GPU**: Globaler Schalter, nicht pro GPU steuerbar

### Flash Attention auf der P40

Ältere Annahme: FA ist auf Pascal grundsätzlich langsamer. **Durch Benchmarks widerlegt (17.02.2026):**
FA ist für Qwen3-Modelle auf der P40 **schneller** (PP: +134 % bei 4B, +30 % bei 30B).
Der bekannte FA-Nachteil betrifft hauptsächlich GLM-4.7-FLASH (siehe Benchmark-Abschnitt).

**Empfehlung:** FA immer einschalten (`--flash-attn on`).
KV-Cache-Quantisierung (`-ctk q8_0 -ctv q8_0`) spart zusätzlich ~50 % KV-VRAM.

### Benchmark: Tensor-Split-Optimierung (18.02.2026)

> **Praxis-Benchmark:** Siehe [tensor-split.md](../benchmarks/tensor-split.md) für einen
> vollständigen Vergleich im AIfred-Multi-Agent-Tribunal zwischen aggressivem (11:1) und ausgewogenem (2:1) Split
> mit Qwen3-Next-80B-A3B-Instruct.

**Hardware:** RTX 8000 (CUDA0, 46 GB) + Tesla P40 (CUDA1, 24 GB), `CUDA_DEVICE_ORDER=FASTEST_FIRST`
**Modell:** Qwen3-32B Q4_K_M (18,8 GB)
**Tool:** `llama-bench` (llama.cpp v8076)
**Parameter:** `-ngl 99 -np 1 -fit off --flash-attn on -ctk q8_0 -ctv q8_0 -sm layer -t 4 -b 2048 -ub 512`

| Konfiguration | Tensor-Split (RTX8000:P40) | PP (tok/s) | TG (tok/s) | Hinweise |
|--------|---------------------------|-----------|-----------|-------|
| A | RTX 8000 allein | 631 | **22,19** | Basis (Single GPU) |
| B | P40 allein | 214 | **9,87** | Zum Vergleich |
| C | 1:1 | 326 | **13,42** | Gleichmäßiger Split |
| D | 2:1 (bisheriger Default) | 394 | **15,59** | War Standard in llama-swap |
| E | 5:1 | 497 | **18,15** | Spürbar schneller |
| F | 10:1 | 565 | **19,68** | Optimum für 32B |

**Erkenntnisse:**

- **10:1-Split** ist bei TG **26 % schneller** als 2:1 (19,68 gegenüber 15,59 tok/s)
- Jeder Layer auf der P40 ist wegen der geringeren Bandbreite ein Flaschenhals (346 GB/s gegenüber 672 GB/s der RTX 8000)
- Auch das PP-Optimum nähert sich dem Single-GPU-Wert (565 gegenüber 631)
- **Faustregel:** So wenig P40 wie möglich (nur für VRAM-Überlauf), RTX 8000 maximal ausnutzen

**Empfehlungen nach Modellgröße und Context:**

| Modell | Modell-VRAM | Context | KV-Typ | Empfohlener Split (RTX:P40) | Benötigter VRAM gesamt |
|--------|------------|---------|---------|----------------------------|-------------------|
| 4B–14B Q4_K_M | < 10 GB | 131K | q8_0 | `-dev CUDA0` | < 20 GB |
| 32B Q4_K_M | 18,8 GB | 16K | q8_0 | `-dev CUDA0` | ~22 GB |
| 30B A3B Q8_0 | ~31 GB | 238K | q8_0 | `-ts 2,1` | ~50–55 GB |
| 80B Q4_K_M | 46,6 GB | 262K | q4_0 | `-ts 2,1` | ~65 GB |
| 120B Q8_0 | ~59 GB | 131K | q4_0 | `-ts 2,1` | ~65–70 GB |

**Warum der Context den Split bestimmt:**

Der KV-Cache wird im selben Verhältnis wie das Modell auf die GPUs verteilt (`-sm layer`).
Bei einem 10:1-Split liegen 91 % des KV-Caches auf der RTX 8000. Belegt das Modell bereits
den Großteil des RTX-VRAMs, bleibt nicht genug Platz für den KV-Cache großer Contexts.

- **Modell passt allein auf die RTX (< 46 GB):** Hoher Split möglich → P40 als Tempo-Flaschenhals minimieren
- **Modell passt NICHT allein auf die RTX (≥ 46 GB):** Split ≈ VRAM-Verhältnis (46:24 ≈ 2:1) ist optimal,
  damit sich der KV-Cache gleichmäßig auf den freien VRAM beider GPUs verteilt
- **2:1 als Faustregel:** Bei großen Modellen (80B+) und großen Contexts nahe am VRAM-Verhältnis bleiben

**Hinweis zu CUDA_DEVICE_ORDER:** Der llama-swap-Dienst läuft mit `CUDA_DEVICE_ORDER=FASTEST_FIRST`,
in diesem Setup also CUDA0 = RTX 8000 und CUDA1 = P40. Die Tensor-Split-Werte beziehen sich auf CUDA0:CUDA1.

### Tensor-Split-Empfehlungen (historisch, P40-Ära)

Für das aktuelle Setup berechnet der Autoscan den Split selbst (siehe
[Tensor-Split-Berechnung](#tensor-split-berechnung)), die Kalibrierung verfeinert ihn.

```bash
# Hinweis: Mit CUDA_DEVICE_ORDER=FASTEST_FIRST: CUDA0=RTX 8000, CUDA1=P40

# 2x P40 (gleich): gleichmäßig verteilen
-sm layer --tensor-split 1,1

# RTX 8000 (CUDA0) + P40 (CUDA1): P40-Anteil minimieren (nur VRAM-Erweiterung)
# Für Modelle, die BEIDE GPUs brauchen (> 46 GB)
-sm layer -ts 10,1   # 30B A3B Q8_0: gut
-sm layer -ts 24,1   # 80B Q4_K_M: VRAM-begrenzt

# Single GPU (Modell passt auf die RTX 8000)
-dev CUDA0           # Bis 32B Q4_K_M: kein Split, volles RTX-8000-Tempo

# 2x RTX 8000 (gleich): gleichmäßig verteilen
-sm layer -ts 1,1
```

---

## GPU-Management: Was automatisch geht, was manuell muss

### Übersicht

| Szenario | Autoscan | Kalibrierung | Manuell? |
|----------|----------|--------------|----------|
| Neue GGUF-Datei hinzufügen | Erkennt automatisch, erstellt Profil mit VRAM-proportionalem Tensor-Split und einer ersten Context/KV/NGL-Anpassung per `llama-fit-params` | Context + Speed-Split per Kalibrierung | Nein |
| Neue lokale GPU einstecken | **Alle Profile** werden automatisch angepasst (Fingerprint-Erkennung) | Kalibrierung empfohlen für Context-Optimierung | Nein |
| GPU entfernen | **Alle Profile** werden automatisch angepasst (Fingerprint-Erkennung) | Kalibrierung empfohlen für Context-Optimierung | Nein |
| RPC-Worker hinzufügen/entfernen | Nicht erkannt (RPC-Profile bleiben unangetastet) | Nicht unterstützt | **Ja** — `--rpc` muss manuell gesetzt werden |

### GPU-Hardware-Fingerprint (automatische Erkennung)

Der Autoscan speichert einen Hardware-Fingerprint in der ersten Zeile der llama-swap-Config:

```yaml
# gpu_hardware: RTX_8000:49152,RTX_8000:49152,Tesla_V100-PCIE-32GB:32768,Tesla_V100-PCIE-32GB:32768,Tesla_V100-PCIE-32GB:32768
healthCheckTimeout: 900
models:
  ...
```

Format: `<Name>:<VRAM MiB>` pro GPU, absteigend nach VRAM sortiert (dieselbe
Reihenfolge wie `CUDA_DEVICE_ORDER=FASTEST_FIRST`); die Präfixe `NVIDIA `,
`GeForce ` und `Quadro ` werden aus dem Namen entfernt.

Bei jedem llama-swap-Neustart vergleicht der Autoscan die aktuelle Hardware mit dem
gespeicherten Fingerprint (±512 MB Toleranz pro GPU für Treiber-Varianz; eine andere
GPU-Anzahl gilt immer als Änderung). Bei einer Änderung:

1. **Alle lokalen Profile** (manuell UND `[autoscan]`) bekommen einen neuen Tensor-Split
2. RPC-Profile (`--rpc` im cmd) bleiben unangetastet
3. Context (`-c`) und NGL (`-ngl`) werden **nicht** geändert — dafür ist die Kalibrierung da
4. Hinweis: "Run 'Context kalibrieren' in AIfred to optimize context sizes"

**Beispielausgabe bei GPU-Änderung** (Format wie vom Autoscan ausgegeben; hier wurde
eine V100 entfernt, die Modellanzahl ist nur ein Beispiel):
```
⚠️  GPU HARDWARE CHANGED!
   Stored:  RTX_8000:49152,RTX_8000:49152,Tesla_V100-PCIE-32GB:32768,Tesla_V100-PCIE-32GB:32768,Tesla_V100-PCIE-32GB:32768
   Current: RTX_8000:49152,RTX_8000:49152,Tesla_V100-PCIE-32GB:32768,Tesla_V100-PCIE-32GB:32768
   Updated tensor-split in 8 model(s)
   → Run 'Context kalibrieren' in AIfred to optimize context sizes
```

### Tensor-Split-Berechnung

Der Autoscan berechnet den Tensor-Split proportional zum VRAM
(`max(1, round(vram / min_vram))` pro GPU):

```python
# Beispiel: aktueller Rechner, 5 GPUs (absteigend nach VRAM sortiert)
per_gpu_vram = [49152, 49152, 32768, 32768, 32768]  # 2x RTX 8000, 3x V100
min_vram = 32768
split_parts = [2, 2, 1, 1, 1]                        # round(49152 / 32768) = 2
# → "--tensor-split 2,2,1,1,1"
```

**Regeln:**
- Modelldatei ≤ 80 % des VRAMs der größten GPU (`MULTI_GPU_VRAM_THRESHOLD`) →
  gar keine GPU-Flags (kein Tensor-Split)
- Größeres Modell → neue Einträge bekommen `-sm layer --tensor-split X,Y[,Z...] -fit off -b 512 -ub 512`
- Bei einer Hardware-Änderung wird in bestehenden Einträgen der `--tensor-split`-Wert
  ersetzt, `-sm layer --tensor-split … -fit off` eingefügt (ein Single-GPU-`-dev …`
  fliegt dabei raus) oder die Split-Flags werden entfernt, wenn das Modell jetzt auf
  eine GPU passt

### Was die Kalibrierung (AIfred-UI) zusätzlich macht

„Context kalibrieren“ in der AIfred-UI führt für das gewählte Modell Folgendes durch
(Algorithmus-Modus, `aifred/lib/calibration/flow.py`):

1. **Phase A: Metadaten + Budget** — liest die GGUF-Metadaten, wartet auf stabilen
   VRAM und baut das VRAM-Budget pro GPU (Sicherheitsmarge, Zusatzmarge für
   Draft-Sidecar-Profile, Reserven für eine TTS-Engine oder das VLM auf der
   Side-Channel-GPU)
2. **Phase 1: Basis-Konfiguration** — geht die Zellen (KV-Qualität × GPU-Satz) durch,
   wenigste GPUs zuerst, höchste KV-Qualität zuerst. Pro Zelle läuft zuerst eine
   mathematische Projektion; nur wenn die den nativen Context erreicht, folgt eine
   echte Probe (mit Layer-Verschiebungen bei OOM). Die erste Zelle, die beim nativen
   Context verifiziert ist, wird die Basis.
   - Erreicht keine Zelle den nativen Context: Die verifizierten Ergebnisse unterhalb
     von nativ plus eine Best-Effort-Probe der besten unverifizierten Zelle werden
     verglichen — höchste KV-Qualität zuerst, dann größter Context, mindestens
     `MIN_USEFUL_CONTEXT_TOKENS` (32K)
3. **Hybrid-Modus** — nur wenn sich keine reine GPU-Konfiguration verifizieren lässt
   **und** der Schalter „Hybrid“ neben dem Kalibrierungsmodus an ist: reduziert `-ngl`
   und lagert Layer auf die CPU aus. Ist der Schalter aus, endet die Kalibrierung mit
   einem Fehler.
4. **Phase E: Speed-Variante** — bei Multi-GPU-Basen: eine Konfiguration mit weniger
   GPUs als die Basis und gleicher KV-Qualität, beginnend mit der schnellsten
   GPU-Klasse; die nächste Klasse kommt nur dazu, wenn dort nichts
   `MIN_USEFUL_CONTEXT_TOKENS` erreicht. Wird als eigener `<modell>-speed`-Eintrag
   geschrieben. Erreicht sie den nativen Context bei gleicher KV-Qualität, ersetzt sie
   stattdessen die Basis; ist ihr Split identisch mit dem der Basis, entfällt sie.
5. **Phase D: Schreiben** — Basis- (und Speed-)Eintrag in die llama-swap-YAML,
   Ergebnisse nach `data/model_vram_cache.json`

Alternativ übergibt der Kalibrierungsmodus „🤖 KI“ die ganze Suche an einen
LLM-gesteuerten Kalibrierer (`aifred/lib/calibration/ai_agent.py`); die beiden Modi
schließen sich aus, es gibt keinen Rückfall vom einen auf den anderen.

Die Kalibrierung verfeinert den groben VRAM-proportionalen Split des Autoscans
durch tatsächliche Messungen. Strategie-Referenz (SSOT):
[calibration-strategy.md](../architecture/calibration-strategy.md).

#### KV-Quantisierung: Entscheidungslogik

| Schritt | KV-Stufen | Schwellenwert | Kommentar |
|---------|-----------|---------------|-----------|
| Phase 1 (Basis) | f16 → q8_0 (q4_0 nur bei ausdrücklicher Freigabe, `min_kv="q4_0"`) | `MIN_USEFUL_CONTEXT_TOKENS` (32K) | Der Standardablauf nimmt lieber eine GPU dazu, als auf q4_0 zu gehen |
| Phase E (Speed) | Gleiches KV wie die Basis | `MIN_USEFUL_CONTEXT_TOKENS` (32K) | Ersetzt die Basis, wenn sie den nativen Context erreicht |

**Hinweis:** Laut Code-Kommentar in `flow.py` ist KV in voller Präzision auf den hier
verwendeten GPUs schneller (P40/V100/RTX 8000 haben keinen schnellen Attention-Pfad
für quantisiertes KV) und hat die höhere Qualität. Daher beginnt die Suche immer mit f16.

### Handlungsanleitung: Neue lokale GPU hinzufügen

1. **Physisch einbauen**, Treiber prüfen: `nvidia-smi` muss alle GPUs zeigen
2. **llama-swap neu starten**: `llama-swap-restart` (oder `sudo systemctl restart llama-swap`)
   - Der Autoscan erkennt die neue GPU per Fingerprint-Vergleich
   - **Alle Profile** (manuell + autoscan) bekommen automatisch den neuen Tensor-Split
3. **Kalibrierung ausführen**: In der AIfred-UI "Context kalibrieren" für wichtige Modelle
   (Context und Speed-Split werden an die neue Hardware-Konfiguration angepasst)
4. **Performance prüfen**: tok/s kontrollieren, bei Bedarf die Split-Verhältnisse nachoptimieren

### Handlungsanleitung: RPC-Worker (Remote-GPU) hinzufügen/entfernen

RPC wird vom Autoscan **nicht** unterstützt — vollständig manuell.

1. **Worker einrichten**: `rpc-server -H 0.0.0.0 -p 50052` auf dem Remote-Rechner
2. **Konnektivität testen**: `bash -c 'echo > /dev/tcp/<IP>:<PORT>' && echo OK`
3. **Neues Profil in der llama-swap-Config** (oder bestehendes anpassen):
   ```yaml
   Modell-rpc:
     cmd: 'llama-server --model <path> -ngl 99 --rpc <IP>:<PORT> ...'
     ttl: 3600
     healthCheckTimeout: 900  # RPC-Modelle brauchen länger zum Laden
   ```
4. **AIfred-RPC-Quick-Check**: Prüft vor jeder Inferenz per TCP-Connect (3 s Timeout),
   ob der Worker erreichbar ist. Fehlermeldung sofort statt 15 Min. Wartezeit.
5. **Kein automatischer Tensor-Split**: llama.cpp verteilt bei RPC automatisch nach VRAM,
   für optimale Performance ggf. aber manuell `--tensor-split` setzen
6. **Separate Profile**: Lokales Profil (CPU-Offload, schnelles Laden) UND RPC-Profil
   (alles auf GPU, langsames Laden) als getrennte Einträge — der User wählt in AIfred

### Warum RPC nicht automatisiert werden kann

- RPC-Endpoints sind Netzwerk-Ressourcen — es gibt keinen lokalen Discovery-Mechanismus
- Der Worker kann ein- oder ausgeschaltet sein → dynamisch, nicht statisch konfigurierbar
- Die optimale Verteilung hängt von der Netzwerklatenz ab (nicht nur vom VRAM)
- `healthCheckTimeout` und `ttl` müssen an die Ladezeit angepasst werden

---

## llama-swap-Konfiguration

### Installation

```bash
# llama-swap-Binary (Go, einzelne Datei)
# Download unter: https://github.com/mostlygeek/llama-swap/releases

# llama-server (Teil von llama.cpp)
# Mit CUDA-Support bauen:
cd llama.cpp && cmake -B build -DGGML_CUDA=ON && cmake --build build -j
```

### Beispiel-Config (Aufbau, wie ihn der Autoscan schreibt)

Normalerweise schreibst du die Config nicht von Hand — der Autoscan legt sie an (siehe
[Automatische Modellerkennung](#automatische-modellerkennung-autoscan)). Das Beispiel
zeigt den Aufbau, den er erzeugt; Modellnamen, Pfade und Context-Werte sind
Platzhalter (`<…>`):

```yaml
# gpu_hardware: <Fingerprint, siehe GPU-Hardware-Fingerprint>
models:
  # [autoscan]
  <Small-Model-Q8_0>:
    # Passt auf die größte GPU (≤ 80 % ihres VRAMs) → keine GPU-Flags;
    # f16-KV → kein -ctk/-ctv
    cmd: /home/YOUR_USER/llama.cpp/build/bin/llama-server --port ${PORT} --model /home/YOUR_USER/models/<Small-Model-Q8_0>.gguf -ngl 99 -c <ctx> --flash-attn on -np 1 -t 4 --mlock --direct-io --jinja --no-context-shift --temp 0.8 --top-k 40 --top-p 0.95 --min-p 0.05 --repeat-penalty 1.0
    ttl: 1800   # Modell < 20 GB

  # [autoscan]
  <Large-Model-Q4_K_M>:
    # Multi-GPU (aktueller Rechner: 2x RTX 8000 + 3x V100 → 2,2,1,1,1);
    # Anpassung hat q8_0-KV gewählt → -ctk/-ctv vor den Default-Flags
    cmd: /home/YOUR_USER/llama.cpp/build/bin/llama-server --port ${PORT} --model /home/YOUR_USER/models/<Large-Model-Q4_K_M>.gguf -ngl 99 -c <ctx> -sm layer --tensor-split 2,2,1,1,1 -fit off -b 512 -ub 512 -ctk q8_0 -ctv q8_0 --flash-attn on -np 1 -t 4 --mlock --direct-io --jinja --no-context-shift --temp 0.8 --top-k 40 --top-p 0.95 --min-p 0.05 --repeat-penalty 1.0
    ttl: 3600   # Modell ≥ 20 GB

  # Handgepflegtes reines CPU-Embedding-Profil (-ngl 0) → Gruppe "embed"
  bge-m3-567M-Q8_0-embed:
    cmd: /home/YOUR_USER/llama.cpp/build/bin/llama-server --port ${PORT} --model /home/YOUR_USER/models/bge-m3-567M-Q8_0.gguf --embedding --pooling cls -ub 8192 -c 8192 -ngl 0 …
    ttl: 1800
    env:
    - CUDA_VISIBLE_DEVICES=

groups:
  main:            # alles, was VRAM belegt — immer nur ein Modell gleichzeitig
    exclusive: true
    swap: true
    members:
      - <Large-Model-Q4_K_M>
      - <Small-Model-Q8_0>
  embed:           # reine CPU-Server, bleiben geladen, verdrängen "main" nie
    exclusive: false
    swap: true
    persistent: true
    members:
      - bge-m3-567M-Q8_0-embed
```

Hinweise:
- Die Sampling-Flags stammen aus den GGUF-Metadaten (`general.sampling.*`); die
  Werte oben sind die llama.cpp-Defaults, die der Autoscan nimmt, wenn das GGUF
  keine mitbringt.
- `llama-swap-restart` ruft zusätzlich `scripts/llama-swap-build-config` auf, das
  den Einträgen Speculative-Decoding-Flags (`--spec-type …`) und `--cache-reuse 256`
  (Modelle ohne `--mmproj`) hinzufügt.
- Eine dritte Gruppe `vision` (persistent) enthält die `-visiond`-Describer-Profile,
  sofern es welche gibt.
- vLLM-Checkpoints laufen ebenfalls unter llama-swap, als `-vllm`-Einträge (vom
  Autoscan aus `data/vllm_runtime.yaml` angelegt, eigene Kalibrierung).

### llama-swap starten

```bash
# llama-swap auf Port 11435 (neben Ollamas 11434)
CUDA_DEVICE_ORDER=FASTEST_FIRST GGML_CUDA_GRAPH_OPT=1 \
  ~/bin/llama-swap --config ~/.config/llama-swap/config.yaml --listen :11435 --watch-config
```

### Systemd-Dienst

Unit auf Systemebene, identisch mit der aus
[deployment.md, Abschnitt 5](deployment.md#5-systemd-dienste-einrichten) (die Referenz).
`$USER`/`$HOME` setzt die Shell beim Schreiben des Heredocs ein; pass die beiden
`$HOME/Projekte/AIfred-Intelligence`-Pfade an, wenn du das Repo woanders geklont hast:

```bash
sudo tee /etc/systemd/system/llama-swap.service > /dev/null << EOF
[Unit]
Description=llama-swap - LLM Model Proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
ExecStartPre=$HOME/Projekte/AIfred-Intelligence/venv/bin/python \
    $HOME/Projekte/AIfred-Intelligence/scripts/llama-swap-autoscan.py
ExecStart=$HOME/bin/llama-swap \
    --config $HOME/.config/llama-swap/config.yaml \
    --listen :11435 --watch-config
Restart=on-failure
RestartSec=5
TimeoutStartSec=300
Environment=PATH=/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
Environment=LD_LIBRARY_PATH=/usr/local/cuda/lib64
Environment=CUDA_DEVICE_ORDER=FASTEST_FIRST
Environment=GGML_CUDA_GRAPH_OPT=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable llama-swap
```

```bash
# Dienstverwaltung (Systemebene → sudo, außer eine PolKit-Regel erlaubt es)
sudo systemctl start llama-swap
sudo systemctl status llama-swap
sudo journalctl -u llama-swap -f

# Wartungs-Neustart nach Modell-Download oder YAML-Änderung
llama-swap-restart
```

`llama-swap-restart` (`scripts/llama-swap-restart`, von `install-services.sh` nach
`~/bin/llama-swap-restart` verlinkt) stoppt den Dienst, beendet übrig gebliebene
`llama-server`-Prozesse, wartet auf die VRAM-Freigabe, löscht verwaiste Lookup-Caches
(`~/.cache/llama_lookup_*.bin`), führt `llama-swap-build-config` aus und startet
llama-swap wieder (der Autoscan läuft als `ExecStartPre`); nach dem Start läuft
`llama-swap-build-config` ein zweites Mal für Einträge, die der Autoscan gerade
angelegt hat.

### API-Endpoints

| Endpoint | Beschreibung |
|---|---|
| `GET /v1/models` | Alle konfigurierten Modelle |
| `POST /v1/chat/completions` | Chat (OpenAI-kompatibel) |
| `GET /running` | Aktuell geladene Modelle |
| `POST /models/unload` | Modell manuell entladen |
| `GET /health` | Health-Check |
| `GET /ui` | Web-UI zur Überwachung |

---

## Automatische Modellerkennung (Autoscan)

Das Skript `scripts/llama-swap-autoscan.py` erkennt neue GGUF-Modelle automatisch
und konfiguriert sie für llama-swap. Es läuft als `ExecStartPre` vor jedem llama-swap-Start.

### Was das Skript macht

0. **GPU-Hardware-Fingerprint** — vergleicht die aktuellen GPUs mit dem in der Config gespeicherten Fingerprint. Hat sich die Hardware geändert (GPU hinzugefügt/entfernt), wird der Tensor-Split in ALLEN lokalen Profilen automatisch aktualisiert (siehe [GPU-Management](#gpu-management-was-automatisch-geht-was-manuell-muss))
1. **Ollama-Manifeste scannen** — liest Ollama-Manifeste aus System- und User-Pfaden, findet GGUF-Blobs und legt Symlinks mit sprechenden Dateinamen in `~/models/` an
   - Beispiel: `sha256-6335adf...` → `Qwen3-14B-Q8_0.gguf`
   - Deduplizierung: Zeigen mehrere Tags auf denselben Blob, gewinnt der längste/aussagekräftigste Name
   - Embedding-Modelle (BERT, nomic usw.) werden übersprungen
2. **HuggingFace-Cache scannen** — findet GGUFs in `~/.cache/huggingface/hub/` (nur den aktiven Snapshot) und legt Symlinks in `~/models/` an
3. **Aufräumen** — entfernt tote Symlinks, Config-Einträge ohne Modelldatei, veraltete Skip-Listen-Einträge und Operating-Point-Profile (`data/operating_points/*.yaml`), deren Modelldatei fehlt; pflegt die `-visiond`-Describer-Profile für Modelle mit `mmproj-*.gguf` (legt sie an, setzt `-c` auf `VLM_NUM_CTX` aus `aifred/lib/config.py`, entfernt Varianten verschwundener VLMs)
4. **Neue GGUFs erkennen** — durchsucht `~/models/` rekursiv (inklusive Unterordnern aus `hf download --local-dir`) und vergleicht mit den vorhandenen Einträgen in der llama-swap-Config (und der Skip-Liste). Übersprungen werden: `mmproj-*`-Dateien, Speculative-Decoding-Sidecars (`mtp-`, `eagle3-`, `dflash-`, `dspark-`), unvollständige Split-GGUFs und Embedding-Architekturen
5. **Kompatibilitätstest** — jedes neue Modell wird kurz mit llama-server gestartet (max. 6 Sekunden):
   - Prozess endet innerhalb von 6 s mit einem Fehler → inkompatible Architektur erkannt (z. B. `deepseekocr`) → Modell wird **nicht** in die Config aufgenommen
   - Prozess läuft nach 6 s noch → Server ist oben, Architektur OK → Modell wird normal aufgenommen
   - Inkompatible Modelle werden in `autoscan-skip.json` gespeichert und bei späteren Starts **nicht erneut getestet**
6. **Erste Anpassung mit `llama-fit-params`** — pro neuem Modell und KV-Stufe (f16 → q8_0 → q4_0): Binärsuche nach dem größten Context bei `-ngl 99`; passt nicht einmal die Untergrenze (32768 oder der native Context, je nachdem was kleiner ist), folgt eine Binärsuche nach dem besten `-ngl` (Hybrid). Die erste KV-Stufe, die passt, gewinnt. Jede GPU muss `VRAM_SAFETY_MARGIN_MB` (1024 MiB) frei behalten. Liegt kein `llama-fit-params` neben `llama-server`, gelten sichere Defaults (Context ≤ 32768, q4_0-KV)
7. **llama-swap-Config erweitern** — für jedes angepasste Modell wird ein mit `# [autoscan]` markierter YAML-Block angehängt: `-ngl`, `-c`, GPU-Flags, `-ctk/-ctv` (nur bei quantisiertem KV), `DEFAULT_FLAGS_BASE` und die Sampling-Parameter aus den GGUF-Metadaten; `ttl` nach Modellgröße (siehe Konstanten)
   - VL-Modelle (mit passender `mmproj-*.gguf`) bekommen automatisch ein `--mmproj`-Argument
   - Existiert die Config-Datei noch nicht, wird sie neu angelegt
8. **VRAM-Cache vorbereiten** — Einträge in `data/model_vram_cache.json` (die vollständige Kalibrierung erfolgt später über die AIfred-UI)
9. **vLLM-Einträge anlegen** — Checkpoint-Verzeichnisse (`config.json` + `*.safetensors`) unter `~/models/` oder im HF-Cache bekommen generische `-vllm`-Einträge, sofern `data/vllm_runtime.yaml` existiert
10. **Gruppen aktualisieren** (wenn sich die Config geändert hat) — schreibt den ganzen `groups:`-Abschnitt neu: `main` (alle VRAM-belegenden Modelle einschließlich `-speed`-Varianten, `exclusive` + `swap`), `embed` (reine CPU-Profile, `persistent`) und `vision` (`-visiond`-Profile, `persistent`). So erzwingt llama-swap die VRAM-Exklusivität zwischen den Modellen.
11. **Einrückung normalisieren** bei den Unterschlüsseln der Modelle (repariert von Hand bearbeitete Einträge)

### Manuell ausführen

```bash
venv/bin/python scripts/llama-swap-autoscan.py

# Alle [autoscan]-Einträge entfernen (mit Zeitstempel-Backup von YAML und Cache)
# und alles neu scannen + anpassen
venv/bin/python scripts/llama-swap-autoscan.py --recalibrate
```

### Typische Ausgabe

Auszug; `…` steht für rechnerspezifische Werte:

```
=== llama-swap Autoscan ===

GPU hardware: RTX_8000:49152,RTX_8000:49152,Tesla_V100-PCIE-32GB:32768,…

Scanning Ollama models...
  + Symlink: Qwen3-14B-Q8_0.gguf → sha256-6335adf...
  = Exists:  Qwen3-8B-Q4_K_M.gguf
  ~ Skip:    nomic-embed-text-v2-moe:latest (embedding model)
  3 Ollama models found, 1 new symlinks created

Scanning HuggingFace cache...
  No HuggingFace cache found or empty.

Cleaning up...
Maintaining -visiond describer profiles...
  visiond profiles up to date

Scanning ~/models/ for GGUFs...
  1 model(s) skipped (known incompatible, remove from autoscan-skip.json to re-test):
    ~ Deepseek-OCR-3B-F16: unsupported architecture 'deepseekocr'
  Found 7 GGUFs, 1 new

Testing new models for llama-server compatibility...
  ✓ Qwen3-14B-Q8_0 (OK)

Calibrating new models (llama-fit-params)...
    GPU: single (model … MB = …% of largest GPU … MB)
  Qwen3-14B-Q8_0 (… MB, native context: 40,960):
    ✓ KV=f16, context=40,960 (min free: … MB)

Updating llama-swap-config.yaml...
  + Added: Qwen3-14B-Q8_0 (context: 40,960)
Updating VRAM cache...
  + Added: Qwen3-14B-Q8_0

Scanning for vLLM checkpoint directories...
  no vLLM checkpoint dirs found

Groups updated: main → [Qwen3-14B-Q8_0, Qwen3-8B-Q4_K_M]

Done. 1 added, 1 VRAM cache entries added.
```

### Konfigurationskonstanten

| Konstante | Default | Beschreibung |
|---|---|---|
| `MODELS_DIR` | `~/models/` | Verzeichnis für GGUF-Dateien und Symlinks. Überschreibbar per Umgebungsvariable `AIFRED_MODELS_DIR` (dieselbe, die `aifred/lib/config.py` liest) |
| `OLLAMA_PATHS` | `/usr/share/ollama/.ollama/models`, `~/.ollama/models` | Ollama-Modellverzeichnisse (Systemdienst, User-Installation) |
| `HF_CACHE_DIR` | `~/.cache/huggingface/hub` | Wurzel des HuggingFace-Caches |
| `LLAMASWAP_CONFIG` | `~/.config/llama-swap/config.yaml` | llama-swap-Config-Datei. Überschreibbar per Umgebungsvariable `LLAMASWAP_CONFIG` (lesen auch `aifred/lib/config.py`, `llama-swap-restart` und `llama-swap-build-config`) |
| `LLAMA_SERVER_BIN` | `~/llama.cpp/build/bin/llama-server` | Pfad zum llama-server-Binary — nur genutzt, wenn kein vorhandener Config-Eintrag einen `llama-server`-Pfad enthält |
| `DEFAULT_TTL_SMALL` | 1800 | Inaktivitäts-Timeout in Sekunden für Modelle < `LARGE_MODEL_GB` |
| `DEFAULT_TTL_LARGE` | 3600 | Inaktivitäts-Timeout in Sekunden für Modelle ≥ `LARGE_MODEL_GB` (Nachladen dauert Minuten) |
| `LARGE_MODEL_GB` | 20 | Größenschwelle (GB, alle Split-Teile summiert) zwischen den beiden TTLs |
| `DEFAULT_NGL` | 99 | GPU-Layer für neue Einträge |
| `DEFAULT_FLAGS_BASE` | `--flash-attn on -np 1 -t 4 --mlock --direct-io --jinja --no-context-shift` | Default-Flags für llama-server; `-ctk <q> -ctv <q>` wird nur vorangestellt, wenn die Anpassung einen quantisierten KV-Cache gewählt hat |
| `DEFAULT_CONTEXT` / `FALLBACK_CONTEXT` | 32768 | Context bei unlesbaren GGUF-Metadaten / Untergrenze der Context-Suche |
| `MULTI_GPU_VRAM_THRESHOLD` | 0.80 | Modelldatei > 80 % des VRAMs der größten GPU → Tensor-Split |
| `VRAM_SAFETY_MARGIN_MB` | 1024 | Minimal freier VRAM pro GPU in der `llama-fit-params`-Projektion |
| `AUTOSCAN_SKIP_FILE` | `autoscan-skip.json` neben `LLAMASWAP_CONFIG` | Persistente Liste inkompatibler Modelle |
| `COMPAT_TEST_TIMEOUT` | 6 | Wartezeit in Sekunden beim Kompatibilitätstest |

### Skip-Liste verwalten

Inkompatible Modelle werden in `autoscan-skip.json` neben der Config gespeichert
(Default `~/.config/llama-swap/autoscan-skip.json`):

```json
{
  "Deepseek-OCR-3B-F16": "unsupported architecture 'deepseekocr'",
  "Qwen3-VL-8B-Q4_K_M": "missing GGUF metadata key 'qwen3vl.rope.dimension_sections' — Ollama blob missing llama.cpp metadata; download official GGUF from HuggingFace"
}
```

Nach einem llama.cpp-Update, das die fehlende Architektur nachrüstet: den Eintrag aus der Datei löschen.
Beim nächsten llama-swap-Start wird das Modell erneut getestet und bei Kompatibilität automatisch aufgenommen.

---

## AIfred-Integration

llama-swap ist OpenAI-kompatibel. In AIfred ist es als eigenes Backend registriert:

- Backend-Typ: `llamacpp`
- URL: Umgebungsvariable `LLAMACPP_URL` oder `http://localhost:11435/v1`
- API-Key: Dummy (lokaler Dienst)
- Modellname in AIfred = Modell-Key in der llama-swap-Config
- Config-Pfad: `~/.config/llama-swap/config.yaml` (XDG-Standard), überschreibbar per Umgebungsvariable `LLAMASWAP_CONFIG`
- vLLM-Einträge (`-vllm`) liegen im selben llama-swap-Katalog und laufen über dieselbe URL

---

## Quellen

- [llama.cpp GitHub](https://github.com/ggml-org/llama.cpp)
- [llama-swap GitHub](https://github.com/mostlygeek/llama-swap)
- [ik_llama.cpp (Graph Parallel)](https://github.com/ikawrakow/ik_llama.cpp)
- [NVIDIA CUDA Graphs Blog](https://developer.nvidia.com/blog/optimizing-llama-cpp-ai-inference-with-cuda-graphs/)
- [NVIDIA RTX LLM Acceleration](https://developer.nvidia.com/blog/open-source-ai-tool-upgrades-speed-up-llm-and-diffusion-models-on-nvidia-rtx-pcs)
- [LocalScore.ai Benchmarks](https://www.localscore.ai)
- [llama.cpp Multi-GPU Discussion](https://github.com/ggml-org/llama.cpp/discussions/15013)
- [eGPU LLM Performance Impact](https://egpu.io/forums/pro-applications/impact-of-egpu-connection-speed-on-local-llm-inference-in-multi-egpu-setups/)
- [FA auf Pascal: Issue #19020](https://github.com/ggml-org/llama.cpp/issues/19020) — FA ist modellabhängig, schneller bei Qwen3
- [FA-Implementierung für Pascal: PR #7188](https://github.com/ggerganov/llama.cpp/pull/7188) — FA ohne Tensor Cores
- [Ollama KV Quant](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/) — Q8/Q4 KV in Ollama
- [FA + P40 Gibberish-Fix: Issue #7400](https://github.com/ggml-org/llama.cpp/issues/7400) — MoE+FA-Bug, behoben
