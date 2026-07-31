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
