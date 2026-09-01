# vLLM-Autokalibration: Topologie-Suche und k-Sweep auf heterogenen GPUs

Stand: 2026-08-30 · Englische Fassung: [vllm-autocalibration.md](../../en/benchmarks/vllm-autocalibration.md)

AIfreds Kalibrieren-Button vermisst vLLM-Checkpoints vollautomatisch:
Er entlädt laufende Modelle, baut aus der vorhandenen GPU-Bestückung eine
Topologie-Leiter (TP innerhalb einer Compute-Klasse, PP über
Klassengrenzen), bootet und misst jede Sprosse real — bei kurzem **und**
bei langem Kontext —, fährt einen Spekulationstiefen-Sweep (MTP, `k`)
und persistiert das Ergebnis als llama-swap-Eintrag samt
Betriebspunkt-Profil mit Hardware-Fingerprint.


> Der Algorithmus selbst (Entscheidungsregeln, Phasen, Begründungen)
> ist separat beschrieben: [calibration-vllm.md](../architecture/calibration-vllm.md).

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

## Kachel-Tuning-Runde (2026-08-30 abends)

Nach den Kernel-Fixes wurden beide Attention-Pfade systematisch
kachel-getunt — gleiche Methodik auf beiden Architekturen: JIT-Probe
einzelner Kernel-Instanzen mit übersteuerten Kachel-Konstanten,
Numerik-Check gegen die Referenzkachel, dann Mikrobench in
Produktionsgeometrie (H=4/HK=1/D=128 pro GPU bei TP2, ~31k paged KV;
Decode/Verify per q-Skalierung, Prefill als 2048er-Chunk).

### Turing (FA2-Fork): Dispatch und Align wollen entgegengesetzte Kacheln

| Kachel M×N | q=1 | q=2 (Verify) | q=8 | Chunk 2048 |
|---|---:|---:|---:|---:|
| 64×64 (vorher, beide Pfade) | 0,298 | 0,298 | 0,246 | 6,40 ms |
| **64×32** → neuer Dispatch | **0,243** | **0,243** | **0,243** | 6,61 ms |
| **128×64** → neuer Align | 1,00 | 0,81 | 0,81 | **4,06 ms** |

Der Dispatch-Pfad (Decode/Verify) gewinnt **18 %** durch die halbe
N-Kachel: 32 statt 48 KB Shared Memory heißt 2 statt 1 CTA pro SM. Der
Align-Pfad (Prefill-Chunks) gewinnt **37 %** durch die doppelte M-Kachel
— 128×64 ist zugleich exakt die Kachel des Standard-Kernels, das
Bitgleichheits-Argument des Align-Pfads bleibt also intakt. Beide
Tabellen sind getrennt änderbar; Numerik-Suite PASS (2,4e-4, fp16-Rauschen).

**End-to-end ist der Gewinn beim 27B nicht messbar** — weder im Gitter
(864/33,0 vs. 863/33,1) noch isoliert auf den RTX (A/B mit alter .so:
533/33,8 vs. 535/33,9 tok/s). Die Attention ist bei diesem Modell nicht
der Engpass, die NVFP4/QPN8-GEMMs dominieren die Schrittzeit. Die
Kacheln bleiben trotzdem: keinerlei Regression, und bei D=256-Modellen
(Flash-Next) wächst der Attention-Anteil.

### Volta (1Cat flash_attn_v100): Vermessung mit demselben Protokoll

Die CUDA-Quellen liegen im 1Cat-Repo (`flash-attention-v100/`,
~14.000 Zeilen; das Wheel liefert nur die Binärdatei). Messwerte auf
einer V100 (ms/Aufruf):

| Pfad | q=1 | q=2 | q=8 | Chunk 2048 |
|---|---:|---:|---:|---:|
| decode_paged D128 (27B-Pfad) | 0,153 | 0,222 | 0,632 | — |
| *Turing FA2 getunt (Vergleich)* | *0,243* | *0,243* | *0,243* | *4,06 ms* |
| prefill_paged D128 | — | — | — | 16,10 ms |
| decode_paged D256 H6 | 0,209 | 0,353 | 1,153 | — |
| decode_paged_xqa D256 H6 | 0,168 | 0,272 | 0,805 | — |
| decode_paged_xqa D256 H8 | 0,183 | 0,300 | 0,897 | — |

Drei Befunde:

1. **Der Volta-Verify skaliert linear mit q** (0,153 → 0,632 für
   q=1 → 8): Der smallq-Pfad behandelt Verify-Tokens als eigene
   Batch-Zeilen, jede läuft den kompletten KV einzeln ab. Die getunte
   Turing-FA2 erledigt q≤8 in einem KV-Durchlauf (flach 0,243). Bei
   k=2 ist Volta trotzdem leicht vorn — aber das erklärt gemessen,
   warum die V100 im k-Sweep kleine k bevorzugen: Jedes weitere
   Spekulationstoken kostet einen vollen KV-Durchlauf. Der
   handkuratierte XQA-Kernel greift per Gate nur bei D=256 — **das 27B
   (D=128) nutzt ihn gar nicht.**
2. **Der Volta-Prefill ist die offene Flanke**: 16,1 ms pro Chunk gegen
   4,06 auf der getunten RTX (Faktor 4 bei ~15–20 % Hardware-Abstand).
   Ursache ist die Prefill-Kachel 32×176: M=32 amortisiert den
   KV-Verkehr schlecht. Größeres M ist in diesem Kernel-Design aber
   strukturell teuer — die Smem-Bilanz beträgt empirisch
   ≈ 272·N + 856·M + 4·M·N Bytes (Score-Matrix und Out-Tile liegen im
   Shared Memory), und an der 96-KB-Wand der V100 passten nur wenige
   Kandidaten:

   | Kachel M×N | Chunk 2048 |
   |---|---:|
   | 32×176 (1Cat-Referenz) | 16,10 ms |
   | 64×64 | 14,63 ms |
   | **64×80** | **14,06 ms (−13 %)** |
   | 48×112, 80×48 | Numerik-Fehler (M muss Vielfaches von 32 sein) |

3. **Die Restlücke ist strukturell**, nicht per Kachel schließbar: kein
   Double-Buffering der KV-Ladungen (sm70 hat kein cp.async), Score und
   Out im Shared Memory. Das ist Material für den 1Cat-Kontakt — die
   Kachel 64×80 als Sofortmaßnahme, die Struktur als Vorschlag.

Der Gegenversuch auf Volta (Kachel 64×80 im Neubau der Extension aus
dem v1.3.0-Stand, deployed mit Backup) bestätigte dann das
Gesamtmuster: auch dort end-to-end neutral (863/33,1 vs. 864/33,0).
Beim 27B auf ~31k Kontext ist die Attention auf keiner der beiden
Architekturen der Engpass — die NVFP4/QPN8-GEMMs takten jede Stufe.
Die Kachelgewinne sind eine Investition in lange Kontexte und D=256.

### QSA (Flash-Next-Grundmodell): der dritte Attention-Pfad

Der Sparse-Attention-Kernel des 180B-Hybrids (Triton, Kommentar im
Quellcode: „Tuned on GB300") lief auf Pre-Ampere mit
Blackwell-Profilen. Wichtige Fork-Eigenheit: sm70/75 nutzt den
`amd/`-Modulzweig (reines Triton) — die `nvidia/`-Zwillingsdatei ist
dort tot. Mikrobench am 2048er-Prefill-Chunk (Produktionsgeometrie
H12/KV1/D256, TOPK 2048):

| Karte | GB300-Profil | bestes Pre-Ampere-Profil | Faktor |
|---|---:|---:|---:|
| V100 (96 KB Smem) | 527 ms (N64/S1/W2) | **27,3 ms** (N16/S1/W4) | **19,3** |
| RTX 8000 (64 KB) | 199,7 ms (geklammert N32/W2) | **47,9 ms** (N16/S4/W4) | **4,2** |

Zwei Warps können die emulierte bf16-Latenz auf diesen Karten nicht
verstecken; die schmale 4-Warp-Kachel braucht zudem null
Split-Workspace. Decode/Verify-Zweige waren bereits optimal. End-to-end
am Flash-Next: Prefill 392–448 → **1.482–1.696 tok/s**, Kohärenz 3/3 —
aber die Lang-Akzeptanz sinkt deterministisch von 19,0 auf 13,3 %
(Kachel-Numerik verschiebt den ohnehin fragilen Drafter), Long-Decode
26–29 → 22,5–25,6. Netto gewinnt ein kompletter Langkontext-Turn
deutlich; die k-Wahl entscheidet die Kalibration neu.

## Flash-Next-Mini-Sweep (2026-08-30 abends)

Statt einer Stunden-Kalibration: gezielter k-Sweep am handkuratierten
Gitterpunkt (TP2×PP2, Partition 24/24, MML 16.384), Langpunkt 13k,
Akzeptanz aus den vLLM-Countern. Ergebnis (tok/s):

| Konfiguration | kurz | Prefill | lang 13k | Akzeptanz kurz/lang |
|---|---:|---:|---:|---|
| k=4, GMU 0,95 (handkuratiert) | 54,1 | 335 | 12,6 | — |
| k=3, GMU 0,95 | 38,5 | 334 | 12,8 | — |
| k=0, GMU 0,95 | 32,2 | 359 | 26,3 | — |
| **k=4, GMU 0,93** | **54,3** | **392** | **28,3** | 57,9 % / 19,0 % |
| k=0, GMU 0,93 | 32,2 | 363 | 26,4 | — |
| k=4, GMU 0,93, MBT 4096/Block 32 | 54,1 | 343 | 26,4 | 57,9 % / 14,8 % |

Vier Befunde:

1. **GMU 0,95 drosselte den Long-Decode auf weniger als die Hälfte**
   (12,6 statt 28,3): Der QSA-Triton-Kernel und der Verify brauchen pro
   Schritt temporäre Puffer; ohne freien VRAM zahlt jeder Schritt
   synchrone Allokator-Strafen (ein Lauf kippte ganz mit Triton-OOM).
   k=0 war immun — der Druck traf nur den Spekulationspfad. Neuer
   Betriebspunkt: GMU 0,93.
2. **Die MTP-Akzeptanz kollabiert am Langkontext** (57,9 % → 19,0 %) —
   extern bestätigt: vllm#47602 misst dasselbe an Qwen3.6-27B
   (64,9 % → 39,1 %, Speedup +129 % → −51 %); Ursache dort wie hier:
   ein flacher Draft-Kopf driftet mit wachsendem Kontext vom
   Hauptmodell weg. Das 27B ist die Ausnahme, nicht die Regel: Sein
   Kopf hält 97 % auch lang.
3. **Spekulation bleibt trotzdem netto positiv** (28,3 vs. 26,4 lang,
   +69 % kurz) — ein längenabhängiges Abschalten lohnt hier nicht.
4. **MBT 4096/Block 32 überträgt sich NICHT** (−12 % Prefill, Akzeptanz
   sinkt weiter): Die Chunk-/Blockgrößen-Achse ist modellspezifisch —
   die Kalibrations-Defaults bleiben deshalb neutral (2048/16), nur
   gemessene Betriebspunkte tragen abweichende Werte.

Nebenbefund: k=0 zeigt reproduzierbar Kohärenz 2/3 (mit Spekulation
3/3) — Kernel-Pfad-Numerik kippt bei Temperatur 0 einen Tie-Break;
Produktion läuft mit Spekulation.

> **Qualitätsseite:** Dieses Dokument behandelt Tempo und Kalibration.
> Wie sich die Quantisierungsformate auf die Antwortqualität auswirken —
> und warum NVFP4 beim 27B deutsche Sätze ins Englische kippen lässt —
> steht in [quantisierung-qualitaet.md](quantisierung-qualitaet.md).

## Fazit: Netto-Bilanz (Stand 2026-08-30 nachts)

Was die Kampagne aus denselben Karten herausgeholt hat — alles ohne
neue Hardware, nur Kernel- und Konfigurationsarbeit:

**Qwen3.8-27B (Produktionsmodell, Gitter TP2×PP2, 262k Kontext):**

| Messpunkt | vor der Kampagne (Triton-Ära) | heute | Faktor |
|---|---:|---:|---:|
| Lang-Decode 31k (bestes k) | ~16 tok/s (k=0; Spekulation verlor) | 36,9 | **×2,3** |
| Lang-Prefill | 194 tok/s | 864 | **×4,5** |
| Kurz-Decode | 58,5 (k=6) | 69,1 (k=2) | ×1,2 |
| TTFT bei 31k | ~160 s | ~36 s (4,9 s ab Cache) | **×4,5** |

**Qwen3.8-Flash-Next-180B (Gitter TP2×PP2, 16k Fenster):**

| Messpunkt | handkuratiert (28.08.) | kalibriert (30.08.) | Faktor |
|---|---:|---:|---:|
| Kontextfenster | 16.384 | **262.144** | **×16** |
| Lang-Prefill | 335 tok/s | **1.191** | **×3,6** |
| Lang-Decode | 12,6 tok/s | **49,5** | **×3,9** |
| Kurz-Decode | 54,1 | 42,1 (bei k=2 statt k=4) | ×0,8 |

> **Messstand:** Die Zeilen stammen aus der AIfred-Autokalibration
> (eigener Boot, eigene Langprobe bei 28.843 Tokens, Kohärenztor
> bestanden) und stehen so im persistierten Betriebspunkt. Der
> Kurz-Decode sinkt, weil der Sieger nach der Langkontext-Regel gekürt
> wird — k=4 wäre kurz schneller, bricht aber lang auf 17,4 ein.

Die Beiträge im Einzelnen: Split-KV-Fix (Verify 20× am Kernel, machte
Spekulation am Langkontext erst zum Gewinn), sm75-Enablement inkl.
gemm-Indexfehler-Fix (Prefill ×2,6), GMU-Headroom (Flash-Next-Decode
×2,2), QSA-Pre-Ampere-Kacheln (Prefill ×4 am 180B), Chunk-Tuning
(+3,5 % am 27B), Kachel-Feinschliff FA2/Volta (kernel-belegt,
E2E-neutral am 27B — Investition in D=256 und lange Kontexte).

**Einordnung gegen llama.cpp** (Äpfel und Birnen: llama.cpp fährt
Q8_K_XL, vLLM NVFP4/AWQ — anderes Quantisierungsformat, andere
Qualitätsklasse; Zahlen von derselben RTX-8000-Hardware, Kampagne
2026-08-24): llama.cpp Q8 + MTP n=3 lieferte 32/26/35 tok/s über die
drei Promptklassen, vLLM schon vor den Kernel-Fixes 52/36/46. Nach den
Fixes kommt das Gitter auf 69/37 (kurz/lang) bei 262k Kontext — mehr
Kontext, als llama.cpp auf dieser Hardware überhaupt anbietet. Das
Entscheidungskriterium der vLLM-Rückkehr („vLLM muss die produktive
llama.cpp-Config schlagen") ist damit beim 27B doppelt erfüllt; beim
122B-MoE bleibt llama.cpp vorn (GPTQ-MoE auf sm75 nur Legacy-Kernel).

## Flash-Next-Vollkalibration (2026-08-30 nachts)

Erste Kalibration mit dem vollständigen Werkzeugkasten (getunte
FA2-Kacheln, QSA-Pre-Ampere-Profile, QPN8-Index-Cache, Chunk-A/B,
GMU-A/B). Ergebnis der k-Leiter am Gitter TP2×PP2, alle Sprossen bei
**262.144 Kontext** (der handkuratierte Punkt stand auf 16.384):

| k | kurz | Prefill | lang (28,8k) | Akzeptanz |
|---:|---:|---:|---:|---:|
| 0 | 32,7 | 1.228 | 23,5 | — |
| 1 | 39,5 | 1.102 | 19,7 | 9 % |
| **2** | **42,1** | **1.102** | **36,7** | **43 %** |
| 3 | 44,0 | 1.098 | 21,6 | 7 % |
| 4 | 41,3 | 1.103 | 17,4 | 4 % |

k=2 ist ein scharfer Ausreißer: 43 % Akzeptanz, während k=1/3/4 auf
einstellige Werte kollabieren — die Verify-Batchgröße 3 trifft eine
Capture-Größe, die anderen k laufen teils ungecaptured.

Beide Herausforderer-Boots gewannen bei ihrem Ersteinsatz:

| Phase | Ergebnis | lang | Prefill |
|---|---|---:|---:|
| k-Sweep-Sieger (k=2) | — | 36,7 | 1.102 |
| Chunk-A/B 4096 | **gewinnt** | **49,4** | 1.154 |
| GMU-A/B 0,95 | **gewinnt** | **49,5** | **1.191** |

Der Chunk-Befund bestätigt die Modellspezifik der Achse ein zweites
Mal: Am k=4-Punkt des Mini-Sweeps hatte 4096 noch 12 % Prefill
gekostet, am kalibrierten k=2-Punkt bringt es 34 % Decode. Genau dafür
gibt es den Messpunkt statt einer Formel. Das GMU-A/B fand stillen
Allokator-Druck, den kein OOM angezeigt hätte.

**Persistierter Betriebspunkt:** TP2×PP2, k=2, Chunk 4096, GMU 0,95,
262.144 Kontext — **49,5 tok/s Lang-Decode bei 1.191 tok/s Prefill.**
Das 180B decodiert am Langkontext damit schneller als das 27B (36,9);
die A4B-Sparsamkeit zahlt sich aus, sobald die Kernel nicht mehr im Weg
stehen.

## Modellqualität: Qwen3.8-Flash-Next-180B (NVFP4)

Ein Nebenbefund der Kernel-Arbeit, der ohne sie nicht aufgefallen wäre:
Das 180B zerfällt bei langer Generierung sprachlich, ohne fachlich
falsch zu werden. Zwei A/B-Sitzungen mit identischen Prompts (drei
Turns: Quantenphysik, Regenbogen, Coandă-Effekt, je dreißig Sätze)
zeigen ein stabiles Muster:

- **Wortverstümmelungen** häufen sich im letzten Viertel jeder Antwort:
  „ruhsquiete", „Strald", „vomombraften", „geinnenwin",
  „Zentripetalbedarf". Nicht am Gesprächskontext hängend, sondern an
  der Länge der einzelnen Antwort.
- **Sprachwechsel**: vereinzelte chinesische Zeichen mitten im Satz
  („verborgener örtlicher变量", „Interferenzlehre补齐te später"),
  ebenfalls bei 76–81 % der Antwortlänge. Qwen-Modelle sind
  chinesischen Ursprungs; unter Rundungsrauschen greift das Modell zum
  semantisch richtigen Token in der falschen Sprache.
- **Gelegentliche Halluzination von Eigennamen**: In einem Lauf wurden
  zwei Wissenschaftler samt Jahreszahlen erfunden („Heinrich Rössel"
  1880, „Basilie Craioș" 1932) und eine Aristoteles-Stelle fingiert.
  Das ist die heikelste Klasse, weil sie sich nicht als Fehler zeigt.
- **Der fachliche Kern bleibt dagegen tragfähig**: 42°/40° für Rot und
  Violett, 138° Gesamtablenkung, Alexanders dunkles Band zwischen 42°
  und 51°, Descartes 1637, Youngs Interferenz für die Nebenbögen — alles
  korrekt. Ebenso die Tippfehler-Korrektur „Kuanda" → Coandă.

**Nicht die Quantisierung allein**: Der Uploader liefert Metriken mit,
AIME26 pass@1 = 98,75 % und majority@8 = 100 %. Das Denkvermögen ist
also praktisch unbeschädigt — beschädigt ist die Sprachführung bei
langer Generierung. Und höhere Präzision ist auf dieser Hardware kaum
zu haben: 128 GB bei vier Bit, während vLLM ohne die Side-Channel-Karte
nur 160 GB zur Verfügung hat. Fünf oder sechs Bit passen erst mit
TP1×PP5 (192 GB) — dann allerdings zum Preis von rund 30 % Decode.

Einordnung: Flash-Next ist eine Architektur-Vorschau (linear attention
plus sparse attention mit `indexer_budget` 2048). Es ist plausibel, dass
die Oberflächenqualität mit der nächsten Generation nachzieht; für
Recherchearbeit mit Zitatanspruch ist das Modell derzeit mit Vorsicht zu
verwenden.

## Qwen3.5-122B-A10B-NVFP4: Vollkalibration (2026-09-01)

Erster Hybrid-Checkpoint (Mamba/SSM-Anteil) auf diesem Stack.
`Qwen3_5MoeForConditionalGeneration`, 48 Schichten, 77,7 GiB, nativer
Kontext 262.144, MTP-Block 4,70 GiB **BF16** (unquantisiert).
TP2×PP2 über GPUs [0, 2, 1, 4] — beide RTX 8000 und zwei V100.

### Der Boot-Stolperstein

vLLM ignoriert bei Hybriden unsere `--block-size` und leitet die
Attention-Seitengröße aus der Mamba-State-Seite ab
(`platforms/interface.py`, `attn_block_size = alignment × ceil(mamba_page /
(alignment × attn_page_1_token))`). Hier: 16 × 131 = 2.096 — über unserem
Chunk-Deckel von 2.048, also Boot-Abbruch per Assertion. Seit
`parse_vllm_required_batched_tokens()` übernimmt der Kalibrator die von
vLLM selbst genannte Zahl. Mit geladenem Draftkopf ändert sich die
Arithmetik nochmals auf 2.176.

### Messmatrix (TP2×PP2, ctx 262.144)

| k | kurz | Prefill | lang | Akzeptanz |
|---:|---:|---:|---:|---:|
| **0** | 32,4 | **1.628** | **23,5** | — |
| 1 | **34,0** | 842 | 15,3 | 90 % |
| 2 | 29,5 | 833 | 15,4 | 87 % |
| 3 | 24,2 | 824 | 14,5 | 79 % |
| 4 | 18,5 | 806 | 13,2 | 70 % |
| 5 | 14,1 | 793 | 11,6 | 64 % |
| 6 | 11,5 | 772 | 10,9 | 64 % |
| 7 | 9,1 | 747 | 12,1 | 61 % |

Lang = Decode bei 28.843 Token Kontext, Prefill kalt gemessen
(`max_tokens=1` gegen frisch gebooteten Server).

### Befund: Akzeptanz rettet einen BF16-Draftkopf nicht

k=1 nimmt **90 %** der Entwürfe an und bleibt im Langkontext trotzdem
35 % hinter k=0. Der Kopf rät also gut — er kostet nur mehr Bandbreite,
als er einbringt. Die Vorabschätzung `worthwhile=True` wiegt bislang nur
die Blockgröße, nicht den Datentyp; der Sweep über sieben Tiefen kostete
1,5 h für ein Ergebnis, das am unquantisierten Block absehbar war. Einzig
der Kurzkontext profitiert marginal (k=1: 34,0 gegen 32,4).

### A/B-Verfeinerung und Betriebspunkt

Chunk 4.192 gewinnt knapp gegen 2.096 (23,6 gegen 23,5 lang) und wird
übernommen; GMU 0,95 verliert gegen 0,97 und bleibt liegen.

**Betriebspunkt: 23,6 tok/s Langkontext-Decode bei vollem 262k-Kontext**,
TP2×PP2, k=0, GMU 0,97, Chunk 4.192. Kohärenz 3/3.

## Ausblick

- Lineare Verify-Skalierung des Volta-Kernels und die 64×80-Kachel als
  Befund an 1Cat melden (siehe Kachel-Tuning-Runde).
- Publikationswelle: Split-KV-Fix und sm75-Enablement an
  vllm-project/flash-attention, FA-Versions-Erkennung an vllm-project/vllm.
- Erledigt seit dieser Runde: Flash-Next-Vollkalibration, QSA-Vermessung
  und -Retune, `_sm70_qpn8_indices` memoisiert (war ~9 % CPU je Schritt).
