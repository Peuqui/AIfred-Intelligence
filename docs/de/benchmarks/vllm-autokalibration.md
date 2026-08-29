# vLLM-Autokalibration: Topologie-Suche und k-Sweep auf heterogenen GPUs

Stand: 2026-08-29 · Englische Fassung: [vllm-autocalibration.md](../../en/benchmarks/vllm-autocalibration.md)

AIfreds Kalibrieren-Button vermisst vLLM-Checkpoints vollautomatisch:
Er baut aus der vorhandenen GPU-Bestückung eine Topologie-Leiter
(TP innerhalb einer Compute-Klasse, PP über Klassengrenzen), bootet und
misst jede Sprosse real, fährt auf den Gewinner-Kandidaten einen
Spekulationstiefen-Sweep (MTP, `k`) und persistiert das Ergebnis als
llama-swap-Eintrag samt Betriebspunkt-Profil mit Hardware-Fingerprint.

## Testsystem

| Komponente | Wert |
|---|---|
| GPUs (kalibrierbar) | 2× Quadro RTX 8000 48 GB (SM75) + 2× Tesla V100-PCIE-32GB (SM70) |
| Side-Channel (reserviert) | 1× V100 32 GB (TTS + Vision, Sammelkarte) |
| Anbindung | PCIe Gen3 x4 je Karte (M.2-OCuLink bzw. USB4); P2P deaktiviert (`NCCL_P2P_DISABLE=1`) |
| Stack | [1Cat-vLLM 1.3.0](https://github.com/dnv2003/v100-skinny) mit v100-skinny-Kerneln (NVFP4 auf Volta/Turing) |
| Modell | RadixArk/Qwen3.8-27B-NVFP4 (20,4 GiB, nativer Kontext 262.144) |

## Methodik

- **Sonde:** fester technischer Prosa-Prompt (schwer für Drafter),
  200 Token, `ignore_eos`, 1 Warmup + 2 Messläufe, **Wall-Clock inkl.
  Prefill**. Vergleichbarkeit zwischen Sprossen zählt, nicht der
  Absolutwert — strukturierte Prompts (Mathe/Code) liefern mit MTP
  deutlich höhere Zahlen.
- **Kontext-Vorrang:** Jede Sprosse tritt mit dem maximal tragbaren
  Kontext an (Start: nativ). Sieger wird die schnellste Sprosse **unter
  denen mit vollem nativem Kontext**; kontext-reduzierte Sprossen werden
  mitgemessen und als Speed-Kandidat ausgewiesen, gewinnen den
  Betriebspunkt aber nicht.
- **OOM-Retry-Leiter:** Nennt vLLM selbst eine Kontextgrenze, wird sie
  übernommen. Ein nacktes CUDA-OOM erhöht zuerst die pro-Karte-Reserve
  (+1/+2 GB — der Inductor-Compile-Workspace braucht Luft auf der per
  GMU absichtlich vollen Karte; kostet nur KV-Pool-Blöcke, kein
  Kontext-Token), erst danach wird der Kontext halbiert.
- **k-Kandidaten:** blockgrößen-günstige Tiefen, gefiltert um strukturell
  unmögliche (Capture-Größen müssen Vielfache von k+1 sein; dieser Stack
  trägt maximal Capture 8 ⇒ k ≤ 7).
- GMU entsteht aus einer festen MB-Reserve je Karte (1.024 MB), nicht
  aus einem Prozent-Daumenwert.

## Ergebnisse Qwen3.8-27B-NVFP4 (2026-08-29)

Kompletter Lauf: 4 Topologien + 2 volle k-Sweeps + Speed-Kandidat,
~45 Minuten, vollautomatisch.

| Topologie | GPUs | Kontext | k=0 | k=5 | k=6 | k=7 |
|---|---|---:|---:|---:|---:|---:|
| TP1 | 1× RTX 8000 | 94.080 | 27,6 | | | |
| **TP2 (Betriebspunkt)** | 2× RTX 8000 | **262.144** | 42,7 | | **58,5** | 54,6 |
| TP2×PP2-Gitter | 2× RTX + 2× V100 | 262.144 | 40,9 | | 57,3 | 54,5 |
| TP2 V100 | 2× V100 | 235.200 | 40,9 | | | |
| **TP2 V100 (Speed-Kandidat)** | 2× V100 | ~140.000 | | **60,5** | 58,0 | 55,6 |

Alle Werte tok/s, Kohärenz 3/3 bei jedem gewerteten Messpunkt.

**Gewählter Betriebspunkt:** TP2 auf den RTX 8000, k=6, **58,5 tok/s
bei vollem 262k-Kontext** (persistiert, wird beim Modellstart 1:1 von
llama-swap übernommen).

## Erkenntnisse

1. **Tensor-Parallelität lohnt auch auf PCIe x4 ohne P2P** (+55 %
   gegenüber TP1): Beim Decode sind die All-Reduce-Nutzlasten winzig
   (latenz-, nicht bandbreitengebunden), während sich die
   Gewichts-Leselast pro Karte halbiert.
2. **Das 4-Karten-Gitter lohnt für dieses Modell nicht:** Der
   PP-Overhead über den Host frisst den XQA-Verifier-Vorteil der
   V100-Endstufe exakt auf (57,3 vs. 58,5).
3. **Volta schlägt Turing, sobald Spekulation läuft:** 2× V100 mit
   XQA-Verifier und k=5 liefern 60,5 tok/s — mehr als der
   RTX-Betriebspunkt — bei etwa der Hälfte des Kontexts. Ohne
   Spekulation (k=0) liegt Turing knapp vorn (Kernel-Vorteil), mit
   Spekulation kehrt sich das Bild (Verifier-Vorteil).
4. **Das optimale k ist topologie- und stackabhängig** und keine
   Konstante: RTX-Optimum k=6, V100-Optimum k=5, und k=8 ist auf diesem
   Stack strukturell unmöglich (Capture-Arithmetik). Der handkuratierte
   Kampagnenwert k=7 war auf keiner Topologie das Optimum — messen
   schlägt annehmen.
5. **vLLMs Kontextgrenzen-Schätzung ist mit geladenem MTP-Draftkopf zu
   optimistisch** — die Kalibration übernimmt sie deshalb iterativ über
   mehrere Boot-Runden.

## Ausblick

- Qwen3.8-Flash-Next-180B (NVFP4, quantisierter MTP-Block): Kalibration
  gegen den handkuratierten Betriebspunkt (51,9/68,2 tok/s) steht aus —
  Ergebnisse folgen hier.
- Kontext-reduzierte Speed-Kandidaten als eigener llama-swap-Eintrag
  (`…-vllm-speed`) sind angedacht, analog zu den GGUF-Speed-Varianten.
