# Performance-Chronik — llama.cpp-Inferenz auf dem MiniPC

Fortlaufende Dokumentation aller Performance-Meilensteine und Messwerte.
**Pflegehinweis:** Bei jeder relevanten Änderung (llama.cpp-Flags,
Kalibrierung, Hardware, neue Modelle) einen Meilenstein ergänzen und die
Modell-Tabellen um aktuelle Messpunkte erweitern. Quellen: die
Session-Statistiken (`*( TTFT: … PP: … tok/s … )*` in
`data/sessions/*.json`), llama-bench-Läufe und direkte API-Messungen
(`timings` aus `/v1/chat/completions`).

Hardware-Basis seit 2026-06/07: 5 GPUs = 192 GB VRAM
(2× RTX 8000 48 GB + 3× V100 32 GB), Details siehe Memory/Setup-Doku.

---

## Meilensteine

| Datum | Änderung | Wirkung |
|---|---|---|
| ~2026-05-23 | **MTP Speculative Decoding** (`--spec-type draft-mtp --spec-draft-n-max 3`) für alle UD-MTP-GGUFs; non-MTP-Varianten entfernt (Commits `bbf98900`, `67ea0e18`) | Quantensprung bei der Token-Generierung: Accept-Raten 90–96 % gemessen; 397B lief damit ~20 tok/s (Stand Mai, IQ3_XXS), heute 36–46. Vor-MTP-Sessions sind nicht mehr vorhanden — Vergleichswerte aus der Zeit fehlen |
| ~2026-07-10 | Kalibrierungsrunde nach V100-Vollausbau (neue Tensor-Splits) | 122B End-to-End-PP 266–287 → 366–428 tok/s |
| 2026-07-31 | **`-ub` 512 → 2048** für MoE-Multi-GPU-Profile (`7f26658f`) + Neu-Kalibrierung 122B/397B | MoE-Experten-Reads amortisieren sich über größere Microbatches. llama-bench 397B pp8192: 240 → 399 tok/s (+66 %); 122B API-Messung: PP ~400 → 839–855 tok/s (~2,1×). Decode überall unverändert. VRAM-Preis real ~1 GB/GPU |
| 2026-07-31 | **MoE-Erkennung generisch** via `expert_count` aus GGUF-Metadaten (`80f5d739`) — auch Profile ohne bestehendes `-ub` (35B) bekommen `-b/-ub 2048`; greift automatisch für jedes neue Modell | llama-bench 35B-A3B pp8192: 1.585 → 2.423 tok/s (+53 %). Dense-Modelle bleiben bewusst bei ub 512 (27B: nur +6–8 % messbar) |
| 2026-07-31 | **Kalibrierungs-Umbau** (`ae98e3d3`, `fa087959`): bidirektionaler Math-Bias mit OOM-Floor, Fastest-First-Kaskade (idle schnellere Karten vor Downstream-Überlauf), mmproj-Gewichte + Encode-Buffer-Burn-In in der fit-params-Projektion | Keine Inferenz-Wirkung, aber: 35B-Komplettkalibration in 13 min (vorher 397B-Klasse: 2,5 h), korrekte 2×-RTX-Splits statt V100-Streuung, alle Side-Channel-Varianten ohne Extra-Probes abgeleitet |
| 2026-08-03/04 | **DeepSeek-V4-Flash-0731 + DSpark** (llama.cpp PR #25784, gemerged 02.08.): erstes Sidecar-Draft-Modell (`--model-draft` + `--spec-type draft-dspark`, n-max 5, Draft aufs Output-Device CUDA4 gepinnt); Kalibrierung um Draft-Projektion (`207a4dc6`) + gehärtete Verify-Probe (`e9855789`) erweitert; llama.cpp auf b10257 + cuBLAS-Workspace-Patch (PR #26574) wegen sporadischer Volta/Turing-Aborts (#26554, 4 Crashes) | Juli-Fehlversuch (11 TG, TTFT 6 min) → produktiv: PP med 325 (1,8× vs. 397B), TTFT med 35 s (3× schneller), TG 19–41 content-abhängig (Accept 61–65 %). Ctx 193K Basis / 425K TTS-Variante dank spottbilligem MLA-KV (~0,5 MB/1K auf engster Karte). User-Politik: DeepSeek nur noch MIT DSpark |

---

## llama-bench (isoliert, 2026-07-31)

Reines Prompt-Processing/Decode ohne Server-Overhead; `-fa 1 -b 2048`.

| Modell | GPUs | Test | ub 512 | ub 2048 | Δ |
|---|---|---|---|---|---|
| Qwen3.5-397B-A17B IQ3_S | 5 | pp2048 | 247 | 411 | +66 % |
| Qwen3.5-397B-A17B IQ3_S | 5 | pp8192 | 240 | 399 | +66 % |
| Qwen3.6-35B-A3B Q8 | 1× RTX 8000 | pp4096 | 1.624 | 2.488 | +53 % |
| Qwen3.6-35B-A3B Q8 | 1× RTX 8000 | pp8192 | 1.585 | 2.423 | +53 % |
| Qwen3.6-27B dense Q8 | 1× RTX 8000 | pp4096 | 799 | 848 | +6 % |
| Qwen3.6-27B dense Q8 | 1× RTX 8000 | pp8192 | 764 | 823 | +8 % |

Token-Generierung (tg64) in allen Fällen unverändert — der ub-Gewinn ist
ein reiner Prompt-Processing-/MoE-Effekt.

---

## End-to-End-Messwerte pro Modell (Session-Statistiken)

End-to-End-Werte schwanken mit Promptlänge, RAG-Kontext, Bildern und
Prompt-Cache — als Trend lesen, nicht als Benchmark. Sofern nicht anders
vermerkt: `-vlm-qwen3vl4b`-Variante, PP/TG in tok/s.

### Qwen3.5-122B-A10B-MTP Q8_K_XL (5 GPUs)

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 03.–08.07. | ub 512, alte Splits | 266–287 | 32–43 |
| 10.–30.07. | ub 512, Splits nach V100-Runde | 345–428 | 39–44 |
| 31.07. | **ub 2048**, speed (4–5 GPUs) | 503–655 (API-Messung ohne Overhead: 839–855) | 42–50 |

### Qwen3.5-397B-A17B-MTP IQ3_S (5 GPUs)

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 07.07. | ub 512, frühe Splits | 112–148 | 23–26 |
| 09.–18.07. | ub 512 | 129–190 | 32–35 |
| 31.07. früh | ub 2048, noch alte ub-512-Kalibrierung | 103–158 | 37–38 |
| 31.07. abends | **ub 2048, neu kalibriert** | 315 (API-Messung: 399–415) | 36–46 |

### Qwen3.5/3.6-35B-A3B-MTP Q8_K_XL (1× RTX 8000)

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 16.–17.07. | Qwen3.5-35B, ub 512 | 990–1.066 | 84–113 |
| 31.07. | **Qwen3.6-35B, ub 2048, speed** | 1.841 | 105 |

Direktvergleich identischer Prompt („Quantenphysik in 30 Sätzen"):
TTFT 23,7 → 14,4 s, PP 1.028 → 1.841 (+79 %), TG 91 → 105 tok/s.

### Qwen3.6-27B-MTP Q8_K_XL (dense, 1–2× RTX 8000)

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 06.07. (speed) | ub 512 | 355–667 | 24–27 |
| 09.–18.07. | ub 512 | 557–730 | 21–27 |

Dense — von der ub-Umstellung bewusst ausgenommen (Messgewinn nur +6–8 %).

### Qwen3.5-4B-MTP Q8_K_XL (1× RTX 8000)

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 30.07. | ub 512 (dense) | 2.738–3.211 | 66–92 |

### Qwen3.8-Flash-Next 180B-A4B UD-Q6_K_XL (5 GPUs, ohne Spekulation)

Erstes Modell mit **Lazy-Read-Tensor**: die PLE-Tabelle
(`per_layer_token_embd.weight`, **50,7 GiB Q8_0 als EIN Tensor**) bleibt auf
der Platte und wird zeilenweise gelesen. Sie passt auf keine einzelne Karte
(größte = 48 GB) und lässt sich bei `-sm layer` nicht aufteilen — llama.cpp
löst das über `TENSOR_READ_LAZY`, was zwingend mmap voraussetzt.

**Folge für die Flags:** `--direct-io` schließt mmap aus und damit Lazy Read;
die 50,7 GiB gehen dann in den Host-RAM (30 GB vorhanden) → OOM-Kill. Dieses
Modell braucht `--load-mode auto`, NICHT `dio`. `--mlock` ist wirkungslos:
beide Flags schreiben dasselbe Feld, der letzte gewinnt.

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 27.08. | ctx 32K, KV f16, split 12:12:8:8:8, `--load-mode auto` (lazy) | 522 | 35,4 |

Messung: llama-server Port 8099 direkt (nicht über llama-swap), Pin-Order
GPU0,GPU2,GPU3,GPU1,GPU4. TG = "Erkläre Quantenphysik in 30 Sätzen",
1200 Token, n=3 ohne Warmlauf (31,8/35,4/35,4). PP = 4.333-Token-Prompt.
VRAM real 107,5 GB über 5 Karten (Projektion von llama-fit-params punktgenau
getroffen), Host-RAM 4,5 GB. Ladezeit 4:34–4:45 min.

**Variantenvergleich** (gleiche Hardware, gleicher Prompt):

| Variante | Ergebnis |
|---|---|
| `--load-mode auto` + lazy auto | **läuft**, 107,5 GB VRAM / 4,5 GB RAM, 35,4 tok/s |
| `--tensor-read-lazy off` | **gescheitert** — Tabelle geht in den Host-RAM, bei RSS 17,8 GB abgebrochen |
| `--load-mode dio` | entfällt — kein mmap ⇒ kein Lazy Read ⇒ wie oben |

Random-Read-Latenz der Modellplatte (USB-NVMe, O_DIRECT, 4 KiB): **209 µs**.
Hochgerechnet ~1,67 ms je Token für 8 PLE-Lookups, also grob 5 % bei 30 ms
pro Token. Der Anteil steigt, je schneller das Modell wird.

**MTP ist für dieses Modell in llama.cpp nicht verfuegbar** (arch `qwen4exp`:
0 nextn/mtp-Tensoren, `supports_mtp_export = False` im Konverter) — im
Gegensatz zum 27B (arch `qwen35`, 4 MTP-Tensoren). Kein Anbieter-GGUF kann
das ändern.

### DeepSeek-V4-Flash-0731 284B-A13B UD-Q4_K_XL (5 GPUs, DSpark)

Erstes Modell mit **Sidecar-Draft** (separates 11-GB-DSpark-GGUF via
`--model-draft`, Accept-Raten 61–65 %) statt eingebauter MTP-Heads.
TG ist stark content-abhängig — die Session-Werte sind ANTWORT-
Durchschnitte (inkl. schlecht draftender Reasoning-Phasen ~20 tok/s);
die Momentan-Rate beim Schreiben von Code/Dateien liegt live bei
**40–45 tok/s** (llama-stats-Beobachtung 04.08. — strukturierter
Output drafted nahe am Acceptance-Maximum). Läuft auf llama.cpp
b10257 + cuBLAS-Workspace-Patch (PR #26574, Volta/Turing-Bug #26554).

| Zeitraum | Konfiguration | PP | TG |
|---|---|---|---|
| 10.07. | Alt-Modell UD-Q8_K_XL, **ohne** Spec-Decoding (Basis-Support #24162) | 142–144 | 10–12 (TTFT ~354 s!) → verworfen |
| 03.–04.08. | 0731 UD-Q4_K_XL, **DSpark n-max 5**, ub 2048, ctx 193K | 163–376 (med 325) | 19–41 (med 24) |

**Direktvergleich zum 397B (Sessions Juli/August):** PP med 325 vs.
181 (~1,8×), TTFT med 35 s vs. 114 s (~3×) — Generierung im
Antwort-Schnitt med 24 vs. 36 zugunsten des 397B; beim reinen
Code-Schreiben liegen beide gleichauf (40–45), der 397B-Vorsprung
entsteht in den Reasoning-Phasen (MTP drafted Denktext besser als
DSpark). Qualitativ (Aquarium-Burst-Benchmark des Users) ist
V4-Flash die neue Nr. 1 im Coding.

### Historische Referenz (Vor-MTP-Ära, 4-GPU-Setup, andere Modelle)

Aus `showcase-notes.md` (Frühjahr 2026): GPT-OSS-120B 541 PP / 50 TG;
Qwen3-235B (CPU-Offload) 54 PP / 6,4 TG; GLM 36 PP / 2,8 TG. Nicht
direkt vergleichbar (andere Modelle/Hardware), zeigt aber die
Größenordnung vor MTP + 5-GPU-Ausbau.

---

## Einordnung

- **MTP** hebt die Token-Generierung (Accept-Raten 90–96 %), lässt PP
  unberührt.
- **ub 2048** hebt das Prompt-Processing bei MoE-Modellen massiv
  (+53 % bis ~2,1×), lässt TG unberührt. Bei dense Modellen lohnt es
  nicht.
- End-to-End-PP liegt systematisch unter den llama-bench-Werten
  (Server-Overhead, Streaming, kurze Prompts amortisieren schlechter);
  die API-`timings` des llama-servers sind der ehrlichste Live-Wert.
- **DSpark** (Sidecar-Draft, DeepSeek-0731) wirkt wie MTP auf die
  Generierung, aber mit niedrigeren Accept-Raten (61–65 % vs. 90–96 %
  bei MTP) und stark content-abhängig: strukturierter Code drafted
  fast doppelt so schnell wie freie Prosa. Der PP-Vorsprung des
  V4-Flash kommt nicht vom Draft, sondern von der Architektur
  (43 Layer, Sparse-Attention-Indexer, billiger MLA-KV).
