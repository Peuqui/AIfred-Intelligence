# Performance-Chronik — llama.cpp- und vLLM-Inferenz auf dem MiniPC

Fortlaufende Dokumentation aller Performance-Meilensteine und Messwerte.
**Pflegehinweis:** Bei jeder relevanten Änderung (llama.cpp-Flags,
Kalibrierung, Hardware, neue Modelle) einen Meilenstein ergänzen und die
Modell-Tabellen um aktuelle Messpunkte erweitern. Quellen: die
Session-Statistiken (`*( TTFT: … PP: … tok/s … )*` in
`data/sessions/*.json`), llama-bench-Läufe und direkte API-Messungen
(`timings` aus `/v1/chat/completions`). Die vergleichbaren Session-Tabellen
erzeugt `scripts/session_speed_stats.py` — die Vergleichsregeln stehen dort
an genau einer Stelle, Tabellen nicht von Hand fortschreiben.

Hardware-Basis seit 2026-06/07: 5 GPUs = 192 GB VRAM
(2× RTX 8000 48 GB + 3× V100 32 GB), Details siehe Memory/Setup-Doku.

---

## Meilensteine

| Datum | Änderung | Wirkung |
|---|---|---|
| 2026-09-14 | AIfred-Modellvergleich aller Chat-Modelle, beide Backends, drei feste Fragen inkl. Kuanda-Fangfrage (siehe Abschnitt unten) | Flash-Next 180B beste Qualität, 27B DFlash2 schnellster Decode (64–76 tok/s) aber Kuanda-Halluzination; QUASAR-Eintrag war auf 8.192 Kontext begrenzt (nachgemessen mit 262K/MTP, 1 GPU) |
| 2026-09-10 | vLLM-Produktion auf 1Cat `work-main` (Upstream + unsere PRs + v100-skinny, Tag `verified-2026-09-10`) | Alt gegen neu am selben Tag bitgleich bzw. kohärent; 27B mit DFlash2 im Bench 77 tok/s, aber noch nicht in llama-swap |
| 2026-09-01 | AIfred misst vLLM-Prefill und -Decode aus vLLMs eigenen Zählern statt Wanduhr (`7f870514`) | Erst ab hier sind vLLM-Session-Werte mit llama.cpps Server-Timings vergleichbar; ältere vLLM-Werte (Wanduhr inkl. Prefill) fließen nirgends ein |
| ~2026-05-23 | **MTP Speculative Decoding** (`--spec-type draft-mtp --spec-draft-n-max 3`) für alle UD-MTP-GGUFs; non-MTP-Varianten entfernt (Commits `bbf98900`, `67ea0e18`) | Quantensprung bei der Token-Generierung: Accept-Raten 90–96 % gemessen; 397B lief damit ~20 tok/s (Stand Mai, IQ3_XXS), heute 36–46. Vor-MTP-Sessions sind nicht mehr vorhanden — Vergleichswerte aus der Zeit fehlen |
| ~2026-07-10 | Kalibrierungsrunde nach V100-Vollausbau (neue Tensor-Splits) | 122B End-to-End-PP 266–287 → 366–428 tok/s |
| 2026-07-31 | **`-ub` 512 → 2048** für MoE-Multi-GPU-Profile (`7f26658f`) + Neu-Kalibrierung 122B/397B | MoE-Experten-Reads amortisieren sich über größere Microbatches. llama-bench 397B pp8192: 240 → 399 tok/s (+66 %); 122B API-Messung: PP ~400 → 839–855 tok/s (~2,1×). Decode überall unverändert. VRAM-Preis real ~1 GB/GPU |
| 2026-07-31 | **MoE-Erkennung generisch** via `expert_count` aus GGUF-Metadaten (`80f5d739`) — auch Profile ohne bestehendes `-ub` (35B) bekommen `-b/-ub 2048`; greift automatisch für jedes neue Modell | llama-bench 35B-A3B pp8192: 1.585 → 2.423 tok/s (+53 %). Dense-Modelle bleiben bewusst bei ub 512 (27B: nur +6–8 % messbar) |
| 2026-07-31 | **Kalibrierungs-Umbau** (`ae98e3d3`, `fa087959`): bidirektionaler Math-Bias mit OOM-Floor, Fastest-First-Kaskade (idle schnellere Karten vor Downstream-Überlauf), mmproj-Gewichte + Encode-Buffer-Burn-In in der fit-params-Projektion | Keine Inferenz-Wirkung, aber: 35B-Komplettkalibration in 13 min (vorher 397B-Klasse: 2,5 h), korrekte 2×-RTX-Splits statt V100-Streuung, alle Side-Channel-Varianten ohne Extra-Probes abgeleitet |
| 2026-08-03/04 | **DeepSeek-V4-Flash-0731 + DSpark** (llama.cpp PR #25784, gemerged 02.08.): erstes Sidecar-Draft-Modell (`--model-draft` + `--spec-type draft-dspark`, n-max 5, Draft aufs Output-Device CUDA4 gepinnt); Kalibrierung um Draft-Projektion (`207a4dc6`) + gehärtete Verify-Probe (`e9855789`) erweitert; llama.cpp auf b10257 + cuBLAS-Workspace-Patch (PR #26574) wegen sporadischer Volta/Turing-Aborts (#26554, 4 Crashes) | Juli-Fehlversuch (11 TG, TTFT 6 min) → produktiv: PP med 325 (1,8× vs. 397B), TTFT med 35 s (3× schneller), TG 19–41 content-abhängig (Accept 61–65 %). Ctx 193K Basis / 425K TTS-Variante dank spottbilligem MLA-KV (~0,5 MB/1K auf engster Karte). User-Politik: DeepSeek nur noch MIT DSpark |

---

## AIfred-Modellvergleich — Nachtmessung 2026-09-14

Alle in AIfred eingetragenen Chat-Modelle auf beiden Backends, bedient über
die AIfred-Oberfläche per Chrome DevTools (Test-User, Agent AIfred, Modus
„💡 Wissen“ ohne Websuche, jede Frage in frisch geleertem Chat). Drei Fragen
im festen Wortlaut:

1. „Erkläre Quantenphysik in 30 Sätzen.“
2. „Erkläre den Regenbogeneffekt in 30 Sätzen.“
3. „Erkläre den Kuanda-Effekt in 30 Sätzen.“ — **Fangfrage**: den
   Kuanda-Effekt gibt es nicht. Bestanden, wenn das Modell den Coandă-Effekt
   erkennt und erklärt oder den Begriff zurückweist; durchgefallen bei
   Erfindung oder Zerfasern.

Messwerte aus den Footer-Metadaten der Session (seit 2026-09-13 über alle
Anfragen eines Turns summiert). Einstellungen je Modell wie in AIfred
hinterlegt (Denken an, reasoning_effort medium, Automatik-LLM = AIfred).
**Lesehilfe Prefill:** Frage 1 rechnet nach dem Modellwechsel den ganzen
Prompt kalt (~3.350 Token). Frage 2 und 3 treffen meist den Prefix-Cache und
rechnen nur wenige hundert Token — deren PP-Raten sind nicht mit Frage 1
vergleichbar (llama.cpp verlor den Slot-Cache bei Frage 2/3 teilweise).
„Load“ = Kaltstart des Modells vor Frage 1.

### Leistung

| Backend | Modell | Frage | TTFT | PP tok/s (gerechnet) | TG tok/s | Token | Thinking | Inference | Load |
|---|---|---|---|---|---|---|---|---|---|
| vLLM | DeepSeek-V4-Flash NVFP4 DSpark | Quanten | 32,9 s | 105,5 (3.439) | 15,3 | 1.291 | 6,1 s | 1:57 min | 10:58 min |
| vLLM | DeepSeek-V4-Flash NVFP4 DSpark | Regenbogen | 24,3 s | 142,1 (3.442) | 15,9 | 1.210 | 11,7 s | 1:40 min | – |
| vLLM | DeepSeek-V4-Flash NVFP4 DSpark | Kuanda | 24,2 s | 142,5 (3.441) | 14,7 | 1.457 | 15,2 s | 2:03 min | – |
| vLLM | Qwen3.8-27B QUASAR NVFP4 (MTP, 1 GPU) ¹ | Quanten | 6,8 s | 565,4 (5.827) | 42,8 | 7.716 | 2:24 min | 3:11 min | 1:06 min |
| vLLM | Qwen3.8-27B QUASAR NVFP4 (MTP, 1 GPU) ¹ | Regenbogen | 3,8 s | 541,5 (3.959) | 40,7 | 16.688 | 6:19 min | 6:58 min | – |
| vLLM | Qwen3.8-27B QUASAR NVFP4 (MTP, 1 GPU) ¹ | Kuanda | 2,4 s | 534,6 (3.124) | 41,0 | 13.583 | 5:07 min | 5:37 min | – |
| vLLM | Qwen3.8-27B NVFP4 DFlash2 | Quanten | 6,9 s | 560,6 (3.353) | 65,4 | 4.810 | 52,4 s | 1:20 min | 2:40 min |
| vLLM | Qwen3.8-27B NVFP4 DFlash2 | Regenbogen | 1,9 s | 509,0 (859) | 63,5 | 9.072 | 2:10 min | 2:24 min | – |
| vLLM | Qwen3.8-27B NVFP4 DFlash2 | Kuanda | 1,7 s | 508,8 (859) | 76,4 | 5.508 | 54,6 s | 1:13 min | – |
| vLLM | Qwen3.8-27B NVFP4 (MTP) | Quanten | 6,4 s | 527,1 (3.353) | 60,5 | 10.025 | 2:27 min | 2:52 min | 1:26 min |
| vLLM | Qwen3.8-27B NVFP4 (MTP) | Regenbogen | 2,1 s | 455,9 (955) | 66,8 | 6.818 | 1:31 min | 1:44 min | – |
| vLLM | Qwen3.8-27B NVFP4 (MTP) | Kuanda | 2,1 s | 492,2 (2.264) | 59,1 | 12.229 | 2:53 min | 3:32 min | – |
| vLLM | Qwen3.8-Flash-Next 180B-A4B NVFP4 MTPQ | Quanten | 7,6 s | 512,7 (3.353) | 49,2 | 3.471 | 51,6 s | 1:17 min | 5:13 min |
| vLLM | Qwen3.8-Flash-Next 180B-A4B NVFP4 MTPQ | Regenbogen | 4,2 s | 434,4 (1.739) | 47,0 | 4.749 | 1:20 min | 1:45 min | – |
| vLLM | Qwen3.8-Flash-Next 180B-A4B NVFP4 MTPQ | Kuanda | 4,0 s | 438,3 (1.739) | 45,1 | 5.797 | 1:53 min | 2:12 min | – |
| llama.cpp | Qwen3.8-27B MTP UD-Q8_K_XL | Quanten | 6,6 s | 543,8 (3.353) | 30,4 | 4.970 | 2:15 min | 2:49 min | 47,9 s |
| llama.cpp | Qwen3.8-27B MTP UD-Q8_K_XL | Regenbogen | 1,2 s | 543,0 (11.680) | 29,0 | 12.430 | 6:47 min | 7:32 min | – |
| llama.cpp | Qwen3.8-27B MTP UD-Q8_K_XL | Kuanda | 6,5 s | 545,2 (6.470) | 25,7 | 3.563 | 2:01 min | 2:31 min | – |

¹ QUASAR am 14.09. vormittags nachgemessen. In der Nacht lief der
llama-swap-Eintrag noch als Autoscan-Seed mit `--max-model-len 8192` ohne
MTP: alle drei Antworten liefen im Denkblock ins Kontextende (je ~4.837
Token, 30 tok/s, keine sichtbare Antwort). Seitdem Parameter wie beim
27B NVFP4 (262.144 Kontext, MTP k=3) — einzige Abweichung Tensor-Parallel 1
statt 2: unter TP2 scheitert der SM70-NVFP4-Kernel am QUASAR-Checkpoint
(„size_n = 8240 is not divisible by tile_n_size = 64“). Mit einer GPU
decodiert QUASAR 41–43 tok/s, das TP2-NVFP4 59–67 tok/s. Auffällig: Frage
2 und 3 trafen den Prefix-Cache kaum (3.959 bzw. 3.124 gerechnete Token).

**Vision-Modelle** (nicht Teil des Chat-Vergleichs): Qwen3VL-30B-A3B
UD-Q8_K_XL auf llama.cpp — TTFT 0,6–2,5 s, PP 1.811 tok/s kalt (4.252 Token),
TG 53–54 tok/s, Inference 16–29 s, Load 42 s, ohne Denken. Qwen3VL-4B
ausgeschlossen: lief bei Frage 1 und 2 in eine Wiederholungsschleife bis zum
Token-Limit (258.679 Token, je ~33 min, 129 tok/s) und kann den großen
AIfred-Kontext nicht.

### Qualität (händisch gelesen)

| Modell | Quantenphysik | Regenbogen | Kuanda (Fangfrage) |
|---|---|---|---|
| DeepSeek-V4-Flash (vLLM) | gut, 30 Sätze; kleine Ungenauigkeiten (Noether-Theorem als Quantenphysik-Grundlage, Bell 1964 „experimentell“) | brauchbar; Fehler „Totalreflexion“ im Tropfen, nur 27 Sätze | **bestanden** — Coandă erkannt und weitgehend richtig erklärt |
| Qwen3.8-27B QUASAR (vLLM, 1 GPU) | sehr gut, fachlich präzise (h-Wert, Nobelpreis 1921, EPR 1935, BEC 1995) | **durchgefallen** — erfindet einen „Börsenregenbogen“ (lange Erholung nach Crashs, Kursdaten stimmen), Optik nur in einem Satz; Tippfehler | **bestanden mit Fehlern** — Coandă erkannt; Tragflächen blasen „entlang der Unterseite“, „skaleninvariant“ falsch; sehr lange Denkphasen (5–6 min) |
| Qwen3.8-27B DFlash2 (vLLM) | sehr gut, 30 Sätze Fließtext | gut; ein Fehler (Himmel innerhalb des Bogens „dunkler“) | **durchgefallen** — erfindet einen „Kunda-Effekt“ nach Ziva Kunda mit frei erfundenem Inhalt |
| Qwen3.8-27B NVFP4 MTP (vLLM) | sehr gut, 30 Sätze | als Dünnschicht-Schillern (Öl, Seifenblase, CD) gedeutet — vertretbare Lesart, Regenbogen zusätzlich korrekt | **bestanden mit Fehlern** — Coandă erkannt; erfundene Etymologie („koandaisch“, rumänisch „fliegen“), widersprüchliche Krümmungsaussage |
| Qwen3.8-Flash-Next 180B (vLLM) | **beste Antwort** — fachlich präzise (Nobelpreis 2022, g-2 auf 11–12 Stellen) | sehr detailliert (Polarisation, Young, Sonnenstand > 42°), aber Alexanderband vertauscht (innen „dunkel“, zwischen den Bögen „hell“) | **bestanden, beste Antwort** — Coandă erkannt, Druckgradient quer zur Stromlinie, Fluidik; kleine Fehler (Ausstellung „Wien“ statt Paris, „konkav“) |
| Qwen3.8-27B MTP Q8 (llama.cpp) | gut, 30 Sätze | brauchbar; „Totalreflexion“, falscher Begriff „Überregner“; langsam (7:32 min, 12.430 Token) | **nicht halluziniert**, Begriff zurückgewiesen — Coandă aber nicht genannt (bietet Kundt, Kuhn, Ku-Band an) |
| Qwen3VL-30B (llama.cpp, Vision) | schwach — verwechselt Superposition mit „spukhafter Fernwirkung“ | mittel; erfundene Erinnerung („wie gestern“), überzählige Bögen falsch „außerhalb“ | Coandă erkannt; dieselbe erfundene Etymologie („fliegen“) |

**Kurzfazit:** Bestes Gesamtpaket ist Flash-Next 180B (beste Qualität bei
45–49 tok/s). Schnellster ist das 27B mit DFlash2 (64–76 tok/s), das aber als
einziges Modell den Kuanda-Effekt frei erfand. DeepSeek-V4-Flash antwortet
kurz und solide, ist mit 15 tok/s und 24–33 s TTFT aber das langsamste. Das
27B auf llama.cpp liefert dieselbe Qualitätsklasse wie auf vLLM bei halber
Decode-Rate. QUASAR denkt am längsten (bis 6:19 min) und erfand beim
Regenbogen einen Börsenbegriff. Zwei Physikfehler traten modellübergreifend auf: „Totalreflexion“
im Regentropfen und vertauschte Helligkeit rund um den Bogen.

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
| 27.08. | ctx 32K, KV f16, split 12:12:8:8:8, `--load-mode auto` (lazy) | 522 | 35,4* |
| 28.08. | ctx 256K, split 14:14:4:8:8 (Prod), **wechselnde Prompts** | — | **33,0 ± 0,6** |

\* Der 27.08.-Wert entstand mit **wiederholt identischem** Prompt. Der
belastbare Alltagswert ist die 28.08.-Zeile.

Messung: llama-server direkt (nicht über llama-swap), Pin-Order
GPU0,GPU2,GPU3,GPU1,GPU4. PP = 4.333-Token-Prompt.

**Der Page-Cache bestimmt den Durchsatz.** Die PLE-Tabelle wird lazy
gelesen; wie schnell das Modell ist, hängt daran, wie viel davon im RAM
liegt (Sättigung bei 19 GB von 50,7 GB = 37 %):

| Zustand | TG |
|---|---:|
| erster Lauf nach dem Laden (kalter Cache) | 22–31 |
| eingeschwungen, wechselnde Prompts | **33,0** |
| eingeschwungen, identischer Prompt wiederholt | 35,7 |

Nach jedem Modellstart ist die erste Antwort also die langsamste.
Mehr RAM wäre der einzige Hebel — im Mini nicht erweiterbar.

**ngram-Spekulation bringt bei Prosa nichts:** 32,3 tok/s mit gegen 33,0
ohne. Die zwischenzeitlich gemessenen 49,7 tok/s (+39 %) waren ein
Artefakt wiederholt identischer Prompts bei `temperature 0` — der Drafter
lernt dabei die eigene Ausgabe auswendig. **Bei Spekulations-Benchmarks
immer die Prompts variieren.** Ungeprüft bleibt strukturierter Output
(Code), wo n-Gramme besser treffen könnten.
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

## vLLM gegen llama.cpp (Session-Statistiken)

Stand 10.09.2026, erzeugt mit `venv/bin/python scripts/session_speed_stats.py`.
Vergleichbar sind nur Server-Messungen: llama.cpp meldet `predicted_per_second`,
vLLM seit dem 01.09., 17:34 (`7f870514`) seine eigenen Zähler. 28 ältere
vLLM-Antworten (Wanduhr einschließlich Prefill) sind verworfen. Modellnamen
ohne die llama-swap-Gruppenzusätze (`-vlm-…`, `-tts-…`, `-speed`).

| Modell | Backend | n | Decode Median (Spanne) | Prefill Median | Zeitraum |
|---|---|---:|---|---:|---|
| DeepSeek-V4-Flash-0731-UD-Q4_K_XL | llamacpp | 12 | 19,4 (16,2–25,8) | 228 | 2026-08-13 – 2026-09-01 |
| Qwen3.5-122B-A10B-UD-Q4_K_XL | llamacpp | 3 | 58,4 (57,6–60,2) | 468 | 2026-09-01 – 2026-09-01 |
| Qwen3.5-122B-A10B-UD-Q8_K_XL | llamacpp | 3 | 44,7 (44,4–45,3) | 399 | 2026-09-01 – 2026-09-01 |
| Qwen3.8-27B-MTP-UD-Q8_K_XL | llamacpp | 20 | 24,8 (20,8–32,6) | 200 | 2026-08-15 – 2026-09-02 |
| Qwen3.8-27B-NVFP4-vllm | vllm | 14 | 48,6 (38,3–72,5) | 619 | 2026-09-04 – 2026-09-06 |
| Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTPQ-vllm | vllm | 3 | 48,6 (42,6–68,0) | 463 | 2026-09-07 – 2026-09-08 |
| Qwen3.8-Flash-Next-180B-A4B-UD-Q6_K_XL | llamacpp | 8 | 28,1 (22,2–30,0) | 274 | 2026-08-28 – 2026-09-01 |

**Direktvergleich (Decode, tok/s):**

| Modell | vLLM | llama.cpp | Faktor |
|---|---|---|---|
| Qwen3.8-27B | NVFP4, MTP k=3, TP2 auf dem RTX-Paar: **48,6** (n=14) | Q8_K_XL, MTP n=3: 24,8 (n=20) | ~2,0× für vLLM |
| Qwen3.8-Flash-Next 180B | NVFP4-MTPQ, MTP k=4, TP2×PP2: **48,6** (n=3) | UD-Q6_K_XL, ohne MTP: 28,1 (n=8) | ~1,7× für vLLM |
| DeepSeek-V4-Flash | keine Session-Daten; Bench 03.09. (PP5): 21,3 Essay / 26,7 Code | UD-Q4_K_XL mit DSpark: 19,4 (n=12); Bench 40,4 | llama.cpp vorn |

**Grenzen:** 4 Bit (NVFP4) gegen Q6/Q8 — nicht dieselbe Qualitätsstufe;
verschiedene Zeiträume, Gespräche und llama.cpp-Builds; Flash-Next unter vLLM
nur n=3. Prefill (Median) 27B 619 gegen 200 tok/s, Flash-Next 463 gegen 274 —
Prompt-Größen und Cache-Treffer unterscheiden sich, daher nur als Tendenz. Das
Kriterium „vLLM muss llama.cpp auf gleicher Hardware schlagen" ist damit beim
27B und bei Flash-Next erfüllt, bei DeepSeek nicht.

---

## Einordnung

- **vLLM** (1Cat plus v100-skinny) schlägt llama.cpp in den Sessions beim
  27B (~2×) und bei Flash-Next (~1,7×) in der Generierung — bei 4 Bit gegen
  Q6/Q8. Bei DeepSeek-V4-Flash bleibt llama.cpp vorn.
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
