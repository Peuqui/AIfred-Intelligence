# vLLM-Autokalibration: Topologie-Suche und k-Sweep auf heterogenen GPUs

Stand: 2026-08-30 · Englische Fassung: [vllm-autocalibration.md](../../en/benchmarks/vllm-autocalibration.md)

AIfreds Kalibrieren-Button vermisst vLLM-Checkpoints vollautomatisch:
Er entlädt laufende Modelle, baut aus der vorhandenen GPU-Bestückung eine
Topologie-Leiter (TP innerhalb einer Compute-Klasse, PP über
Klassengrenzen), bootet und misst jede Sprosse real — bei kurzem **und**
bei langem Kontext —, fährt einen Spekulationstiefen-Sweep (MTP, `k`)
und persistiert das Ergebnis als llama-swap-Eintrag samt
Betriebspunkt-Profil mit Hardware-Fingerprint.

## Testsystem

| Komponente | Wert |
|---|---|
| GPUs (kalibrierbar) | 2× Quadro RTX 8000 48 GB (SM75) + 2× Tesla V100-PCIE-32GB (SM70) |
| Side-Channel (reserviert) | 1× V100 32 GB (TTS + Vision, Sammelkarte) |
| Anbindung | PCIe Gen3 x4 je Karte (M.2-OCuLink bzw. USB4); P2P deaktiviert (`NCCL_P2P_DISABLE=1`) |
| Stack | [1Cat-vLLM 1.3.0](https://github.com/dnv2003/v100-skinny) mit v100-skinny-Kerneln (NVFP4 auf Volta/Turing) |
| Attention | Volta: XQA-Backend des Stacks · Turing: FlashAttention-2 aus [Peuqui/flash-attention](https://github.com/Peuqui/flash-attention) (Branch `sm75-enablement`, siehe unten) |
| Modell | RadixArk/Qwen3.8-27B-NVFP4 (20,4 GiB, nativer Kontext 262.144) |

## Methodik

- **Zwei Messpunkte je Konstellation.** Kurzkontext: fester technischer
  Prosa-Prompt, 200 Token, `ignore_eos`, Wall-Clock inkl. Prefill.
  Langkontext: ~29.000 Token Fülltext (45 % des Fensters, gedeckelt),
  erst Prefill-Messung (`max_tokens=1`), dann reiner Decode über den
  Prefix-Cache.
- **Siegerregel: Lang-Decode entscheidet.** Lange Sessions (Recherche,
  Coding, gewachsene Historie) bestimmen das Nutzungserlebnis; ein
  kurzer Turn ist ohnehin in Sekunden vorbei. Bei Quasi-Gleichstand
  (≤ 5 %) bricht der höhere **Lang-Prefill** das Patt — aber nur, wenn
  sich die Prefills wesentlich (> 10 %) unterscheiden, also beim
  Topologie-Vergleich.
- **Kontext-Vorrang bleibt vorgeschaltet:** Jede Sprosse tritt mit dem
  maximal tragbaren Kontext an (Start: nativ). Um den Betriebspunkt
  konkurrieren nur Sprossen mit vollem nativem Kontext;
  kontext-reduzierte werden mitgemessen und als Speed-Variante
  ausgewiesen.
- **Akzeptanz-Diagnose:** Bei jedem k liest die Langkontext-Sonde die
  Spekulations-Zähler (gedraftete vs. akzeptierte Token) aus den
  Prometheus-Metriken. Das trennt Akzeptanz-Probleme von Kostenproblemen
  — und hat den unten beschriebenen Kernel-Bug überführt.
- **OOM-Retry-Leiter:** Nennt vLLM selbst eine Kontextgrenze, wird sie
  übernommen. Ein nacktes CUDA-OOM erhöht zuerst die pro-Karte-Reserve
  (+1/+2 GB — der Compile-Workspace braucht Luft auf der per GMU
  absichtlich vollen Karte; kostet nur KV-Pool-Blöcke, kein
  Kontext-Token), erst danach wird der Kontext halbiert. Ein OOM, das
  erst bei der ersten echten Anfrage auftritt, bootet die Sprosse einmal
  mit gesenkter GMU nach — und diese GMU wandert auch in den
  persistierten Eintrag.
- **k-Kandidaten:** blockgrößen-günstige Tiefen, gefiltert um strukturell
  unmögliche (Capture-Größen müssen Vielfache von k+1 sein; dieser Stack
  trägt maximal Capture 8 ⇒ k ≤ 7). Im Baseline-Modus (`config.py`:
  `VLLM_CALIBRATION_K_EXHAUSTIVE`) werden alle zulässigen k gemessen.
- **Vorbereitung:** Der Lauf stoppt llama-swap, wartet prozessbasiert auf
  freien VRAM der kalibrierbaren Karten (Side-Channels dürfen belegt
  bleiben) und startet den Dienst am Ende neu — der Neustart lädt
  zugleich den frisch persistierten Betriebspunkt.

## Ergebnisse Qwen3.8-27B-NVFP4 (2026-08-30)

Vollständige Matrix: 3 Topologien × k=0…7, kurz und lang, 2 h 03 min,
vollautomatisch (08:58–11:01). Alle Werte tok/s, Kohärenz 3/3 bei jedem
Messpunkt.

| Topologie | k | Kontext | kurz | Prefill | **lang** | Akzeptanz |
|---|---:|---:|---:|---:|---:|---:|
| TP1 RTX 8000 | 0 | 94.080 | 27,4 | 377 | 19,2 | — |
| TP2 RTX 8000 | 0 | 262.144 | 42,0 | 510 | 29,7 | — |
| TP2 RTX 8000 | 1 | 262.144 | 59,8 | 504 | 29,5 | 87 % |
| TP2 RTX 8000 | **2** | 262.144 | **69,1** | 503 | **37,6** | 97 % |
| TP2 RTX 8000 | 3 | 262.144 | 68,2 | 503 | 36,0 | 66 % |
| TP2 RTX 8000 | 4 | 262.144 | 68,6 | 504 | 32,6 | 50 % |
| TP2 RTX 8000 | 5 | 262.144 | 63,9 | 503 | 31,1 | 40 % |
| TP2 RTX 8000 | 6 | 262.144 | 60,2 | 503 | 30,1 | 34 % |
| TP2 RTX 8000 | 7 | 262.144 | 56,1 | 504 | 26,7 | 29 % |
| TP2 V100 | 0 | 235.200 | 41,0 | 667 | 30,3 | — |
| TP2 V100 | 1 | 183.200 | 48,9 | 582 | 30,9 | 85 % |
| **TP2 V100 (Speed-Variante)** | **2** | 132.000 | 57,5 | 581 | **38,1** | 97 % |
| TP2 V100 | 3 | 129.600 | 61,0 | 580 | 37,2 | 66 % |
| TP2 V100 | 4 | 126.480 | 63,1 | 587 | 31,6 | 50 % |
| TP2 V100 | 5 | 172.992 | 60,6 | 587 | 29,5 | 40 % |
| TP2 V100 | 6 | 170.544 | 58,1 | 587 | 28,6 | 34 % |
| TP2 V100 | 7 | 168.064 | 55,6 | 588 | 25,3 | 29 % |
| TP2×PP2-Gitter | 0 | 262.144 | 40,6 | 839 | 29,4 | — |
| TP2×PP2-Gitter | 1 | 262.144 | 52,8 | 828 | 29,2 | 86 % |
| **Gitter (Betriebspunkt)** | **2** | **262.144** | 61,0 | **833** | **36,9** | 97 % |
| TP2×PP2-Gitter | 3 | 262.144 | 62,6 | 833 | 35,7 | 66 % |
| TP2×PP2-Gitter | 4 | 262.144 | 63,8 | 832 | 31,3 | 50 % |
| TP2×PP2-Gitter | 5 | 262.144 | 61,2 | 832 | 29,7 | 40 % |
| TP2×PP2-Gitter | 6 | 262.144 | 58,4 | 832 | 28,8 | 34 % |
| TP2×PP2-Gitter | 7 | 262.144 | 55,4 | 824 | 25,6 | 29 % |

Die Kontextwerte der V100-Zeilen unterscheiden sich je k, weil der
Draftkopf KV-Budget kostet und die Kalibration dort reduzierten Kontext
zulässt (Speed-Kandidat); auf den Voll-Kontext-Topologien tragen alle k
die nativen 262.144.

**Gewählter Betriebspunkt:** TP2×PP2-Gitter, k=2, **36,9 tok/s
Lang-Decode bei vollem 262k-Kontext**, dazu 833 tok/s Prefill und
61,0 tok/s im Kurzkontext. Die RTX-TP2-Sprosse ist im Lang-Decode
nominell 0,7 tok/s schneller (37,6), liegt damit aber im Patt-Band —
und das Gitter liest lange Prompts **65 % schneller** ein (833 vs. 503
tok/s, also 35 s statt 58 s für 29k Token). Als Speed-Variante
(`…-vllm-speed`) wird zusätzlich 2× V100 mit k=2 persistiert: 38,1 tok/s
bei reduziertem 132k-Kontext.

## Entwicklung: drei Epochen

Alle Zeilen RTX-TP2 bei vollem 262k-Kontext, identische Sonde — nur das
Attention-Backend unterscheidet sich:

| k | Triton kurz | Triton lang | FA2 kurz | FA2 lang | FA2+Fix kurz | FA2+Fix lang |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 42,7 | ~16 | 42,2 | 29,8 | 42,0 | 29,7 |
| 1 | — | — | 59,2 | 14,8 | 59,8 | **29,5** |
| 2 | — | — | 68,8 | 20,9 | 69,1 | **37,6** |
| 3 | — | — | 67,9 | 20,5 | 68,2 | **36,0** |
| 6 | 58,5 | ~6 | 60,0 | 18,6 | 60,2 | 30,1 |

Zwei Sprünge, zwei Ursachen:

1. **Turing bekommt echtes FlashAttention.** vLLM fiel auf sm75 mangels
   FA2 auf `TRITON_ATTN` zurück, dessen Langkontext-Verhalten
   zusammenbrach. Der schlafende sm75-Pfad in FlashAttention-2 ließ sich
   aktivieren — er rechnete allerdings für Head-Dims > 64 falsch, weil
   die Software-Pipeline die Smem→Register-Kopien mit dem MMA-K-Index
   indiziert (auf sm80+ 1:1, auf sm75 Atom-K 8 gegen 16-breite
   `ldmatrix` ⇒ Zugriffe liefen ins Nachbar-Tile). Nach dem Fix: Prefill
   ×2,6 (194 → 505 tok/s), Lang-Decode +47 %.
2. **Der Spekulations-Verify lief ohne KV-Splits.** Split-k war nur für
   den q=1-Decode aktiviert („Only apply split-k for decoding"), sodass
   der Multi-Token-Verify den kompletten Paged-KV **seriell** durchlief —
   am Kernel gemessen 20× langsamer als der q=1-Pfad (2,6 ms statt
   0,13 ms je Schicht bei 31k Kontext). Genau das machte Spekulation bei
   langem Kontext zum Verlustgeschäft. Das Aktivieren der Splits legte
   zwei weitere latente Fehler im Combine-Kernel frei (varlen-Ausgabe mit
   Batch-Stride statt `cu_seqlens`-Packung; dieselbe Annahme beim
   unpadded LSE) — beide behoben.

Die Diagnose lief über die Akzeptanz-Zähler: Bei k=2 lagen 97 %
Akzeptanz an, und Spekulation war **trotzdem** langsamer als k=0. Damit
war ein Akzeptanz-Kollaps ausgeschlossen und ein Kostenproblem bewiesen;
py-spy zeigte 83 % GPU-Wartezeit, der MTP-Profiler entlastete den
Drafter (10,7 ms je Schritt), womit nur der Verify übrig blieb.

Beide Funde sind Upstream-Material für
[vllm-project/flash-attention](https://github.com/vllm-project/flash-attention);
der Split-Fund betrifft auch Ampere, dort milder, weil serielle
Blockarbeit schneller ist.

## Erkenntnisse

1. **Kurzkontext-Messungen führen in die Irre.** Über alle Epochen
   blieben die Kurzwerte nahezu unverändert (68,8 → 69,1 bei k=2),
   während der Lang-Decode sich fast verdoppelte. Wer nur kurz misst,
   sieht den entscheidenden Unterschied nicht — deshalb entscheidet
   jetzt der Lang-Decode.
2. **Das optimale k ist keine Konstante**, aber die Akzeptanzkurve ist
   modell-, nicht hardwareabhängig: 97 % bei k=2, 66 % bei k=3, 29 % bei
   k=7 — auf allen drei Topologien identisch. Was sich unterscheidet,
   sind die Verify-Kosten je Architektur.
3. **Tensor-Parallelität lohnt auch auf PCIe x4 ohne P2P** (+53 % gegen
   TP1): Beim Decode sind die All-Reduce-Nutzlasten winzig
   (latenz-, nicht bandbreitengebunden), während sich die
   Gewichts-Leselast pro Karte halbiert.
4. **Das 4-Karten-Gitter gewinnt über den Prefill, nicht über den
   Decode.** Ein Einzelstream kann beim Decode nicht pipelinen (Stufe 2
   wartet auf Stufe 1), beim Prefill dagegen schon: chunked Prefill
   beschäftigt beide Stufen gleichzeitig, was 833 statt 510 tok/s
   bringt. Für Recherche-Sessions mit großen Kontexten ist das der
   spürbarere Gewinn.
5. **Volta und Turing liegen jetzt gleichauf.** Vor den Kernel-Fixes war
   die V100-Speed-Variante dem RTX-Spekulationspfad um 83 % überlegen;
   jetzt sind es 1,3 % — bei 130k Token weniger Kontext.
6. **vLLMs Kontextgrenzen-Schätzung ist mit geladenem MTP-Draftkopf zu
   optimistisch** — die Kalibration übernimmt sie deshalb iterativ über
   mehrere Boot-Runden.
7. **Ein Proben-OOM ist eine Topologie-Eigenschaft, kein k-Problem.** In
   diesem Lauf brauchten sechs von sieben Gitter-Sprossen denselben
   Nachboot mit gesenkter GMU, weil die volle V100-Stufe dem
   Dequant-Workspace keine Luft ließ; auf den V100 kaskadierte es
   zusätzlich über mehrere Kontextreduktionen. Rund 30 der 123 Minuten
   gingen dafür drauf. Die gelernte GMU gilt seitdem für den Rest des
   Sweeps — mit einer Rückfallklausel, falls sie bei kleinerem k den
   nativen Kontext nicht mehr trägt (Kontext-Vorrang).

## Ausblick

- Qwen3.8-Flash-Next-180B (NVFP4, quantisierter MTP-Block): Kalibration
  gegen den handkuratierten Betriebspunkt (51,9/68,2 tok/s) steht aus —
  Ergebnisse folgen hier.
- Der V100-XQA-Verify könnte vom selben Split-Prinzip profitieren
  (separater Kernel-Pfad, noch nicht untersucht).
- Offene Nebenbaustelle: `_sm70_qpn8_indices` verbraucht rund 9 % der
  CPU-Zeit je Schritt (Dequant-Hilfspfad).
