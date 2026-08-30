# vLLM-Autokalibration: der Algorithmus

> SSOT-Begleitdokument zum Code (`aifred/lib/calibration/vllm_flow.py`,
> `vllm_probe.py`, `vllm_model_meta.py`). Der Code ist die Wahrheit —
> dieses Dokument erklärt die Entscheidungsregeln und ihre gemessenen
> Begründungen, damit Dritte (und wir selbst) sie nachvollziehen können.
> Messbelege: [vLLM-Autokalibration (Benchmarks)](../benchmarks/vllm-autokalibration.md).
> Schwesterdokument für llama.cpp: [calibration-strategy.md](calibration-strategy.md).

## Problemstellung

Ein Checkpoint liefert keine Betriebsparameter. Was der Hersteller
mitliefert (Architektur, Ring-Arithmetik, Kontextfenster), ist die halbe
Wahrheit — die andere Hälfte (Topologie, Spekulationstiefe k,
GPU-Memory-Utilization, Chunk-Größe) entsteht erst im Zusammenspiel mit
dem konkreten Rig und ist **nicht ableitbar, nur messbar**. Die
Autokalibration füllt diesen Beipackzettel aus und persistiert ihn als
Betriebspunkt-YAML (`data/operating_points/<entry>-vllm.yaml`).

## Prinzipien

1. **Messen statt raten.** Jede Regel unten existiert, weil eine
   Vermutung an einer Messung gescheitert ist.
2. **Kontext-Vorrang.** Das native Kontextfenster wird nie geopfert, um
   eine schnellere Sprosse zu retten (Ausnahme: die explizit als
   Info-Messung laufende Speed-Variante).
3. **Lang-Decode kürt den Sieger.** Kurzkontext-Werte sind über alle
   Kernel-Epochen nahezu konstant geblieben, während der Lang-Decode
   sich verdoppelte — wer nur kurz misst, misst das Falsche.
4. **Hardware-agnostisch.** Keine modell- oder kartenspezifischen
   Sonderfälle; alles leitet sich aus enumerierten Fähigkeiten
   (Compute Capability, VRAM) und Checkpoint-Metadaten ab.

## Eingaben

- **Checkpoint-Analyse** (`analyze_checkpoint`): liest nur die
  safetensors-Header (keine Gewichte). Ergebnis: Parameterzahl,
  MTP-Block-Größe, Architektur, `compress_ratio` der QSA-Ringe.
- **Ring-Arithmetik** (`allowed_k_block_sizes`): Die QSA-Ringkapazität
  muss die Attention-Blockgröße teilen: `capacity = ratio *
  ceil((ratio + k) / ratio)`, Blockgröße = `lcm(16, capacity)`. Das
  bestimmt, welche Spekulationstiefen wirtschaftlich sind (Flash-Next:
  k=5–8 erzwingen Block 48 → unwirtschaftlich).
- **GPU-Enumeration** mit Reserven: belegte Karten (Side-Channel: TTS,
  VLM) werden abgezogen; vor der Kalibration wird das llama-swap-Backend
  gestoppt und per Prozessliste gedraint (SSOT-Primitive, kein
  Doppelzustand mit geladenen Modellen).

## Ablauf

### Phase A — Topologie-Leiter

Kandidaten-Topologien (TP×PP-Kombinationen über die freien Karten,
schnellste Compute-Klasse zuerst — die Reihenfolge ist seit 2026-08-30
gemessen, nicht nur Konvention: der Drafter auf der langsameren Klasse
kostet 2–4 %). Jede Sprosse bootet mit k=0 und iteriert das
Kontextfenster: vLLMs eigene Grenzschätzung ist mit geladenem
MTP-Draftkopf zu optimistisch, deshalb wird ihre Fehlermeldung als
neues `max-model-len` übernommen und erneut gebootet (bis zu 3 Runden).

### Phase B — Kohärenz-Tor

Drei deterministische Prüfungen (Faktenfrage, Arithmetik, Code). Tempo
ohne korrekte Ausgabe ist wertlos; eine inkohärente Sprosse fliegt.

### Phase C — Messpunkte

- **Kurzprobe** (Toggle `VLLM_CALIBRATION_SHORT_PROBE`): ~200 Tokens
  Decode auf Kurzprompt. Reine Information — außer eine Sprosse trägt
  den Langpunkt nicht, dann ist sie Ersatzmetrik.
- **Langprobe** (`probe_long_context`): Prompt auf 45 % des Fensters,
  gedeckelt bei 30k. Prefill-Rate über einen `max_tokens=1`-Aufruf;
  Decode-Rate über einen Zweitaufruf, der den Prefix-Cache trifft
  (misst also reinen Decode am vollen KV). Akzeptanzrate aus den
  vLLM-Countern (`spec_decode_num_draft/accepted_tokens_total`) als
  Differenz um die Messung.

### Phase D — k-Sweep

Kandidaten von oben nach unten (exhaustiv per
`VLLM_CALIBRATION_K_EXHAUSTIVE`, sonst gespreizt); strukturell
unmögliche k (Capture-Größen über dem Stack-Limit) werden gar nicht
gebootet. Drei Fehlerklassen, drei Antworten:

- **Boot-OOM** → Kontextübernahme (Phase A-Logik) pro k.
- **Proben-OOM** (Boot ok, erste echte Anfrage kippt): Eigenschaft der
  *Topologie*, nicht des k → einmaliger Retry mit GMU−0,02, und die
  gelernte GMU **gilt für den Rest des Sweeps** (Carry). Rückfallklausel:
  trägt die übernommene GMU bei kleinerem k den nativen Kontext nicht
  mehr, bekommt das k die volle Sprossen-GMU (Kontext-Vorrang).
- **Sonstiger Proben-Crash** → k verworfen.

Die zuletzt gemessene GMU wandert in den Betriebspunkt (Ausfall
2026-08-30: mit 0,95 gemessen, 0,97 persistiert → Produktions-OOM beim
ersten Request; seitdem Pflicht).

### Phase E — Siegerregel (`_beats`)

Primärmetrik Lang-Decode. Tie-Break: Liegen zwei Kandidaten <5 %
auseinander UND unterscheiden sich die Lang-Prefills >10 %, entscheidet
der Prefill (die 10-%-Hürde existiert, weil Prefill-Rauschen sonst ein
besseres k verdrängt — Vorfall 2026-08-30).

### Phase F — Herausforderer-Boots (Chunk-A/B, GMU-A/B)

Ein einziger Gegen-Boot des Gesamtsiegers mit verdoppelter Chunk-Größe
(`max_num_batched_tokens`), gleiche Siegerregel
(`VLLM_CALIBRATION_CHUNK_AB`). Begründung: Die Achse ist
modellspezifisch und nicht ableitbar — 4096 brachte dem dichten 27B
+2,7 % Prefill, kostete den QSA/GDN-Hybrid Flash-Next aber 12 %. Der
eine Messpunkt ersetzt die Formel. Danach GMU-A/B
(`VLLM_CALIBRATION_GMU_AB`): derselbe Sieger einmal mit GMU−0,02, um
*weichen* Allokator-Druck zu erkennen — der wirft keinen Fehler,
sondern frisst still Durchsatz (Flash-Next: GMU 0,95 halbierte den
Long-Decode ohne jede Meldung). Trägt die niedrigere GMU den Kontext
nicht mehr, scheitert ihr Boot und der Amtsinhaber bleibt
(Kontext-Vorrang). Scheitert ein Herausforderer, bleibt in jedem Fall
der Amtsinhaber — ein verlorener Boot ist der Preis der Messung, nie
ein Risiko für den Betriebspunkt.

### Phase G — Persistierung

Betriebspunkt-YAML mit vollständigem Boot-Kommando, Umgebung,
Hardware-Fingerprint und Meta (k-Sweep-Matrix, Langkontext-Messwerte,
Akzeptanz). Zusätzlich optional eine `-speed`-Variante (weniger Karten,
reduzierter Kontext) — nur persistiert, wenn sie den Betriebspunkt
schlägt. Nach der Kalibration startet llama-swap neu und lädt den
frisch persistierten Punkt.

## Bekannte Grenzen

- Die Akzeptanzrate wird gemessen und protokolliert, fließt aber nicht
  in die Regel ein — der Lang-Decode enthält sie implizit.
- Chunk-A/B testet nur die Verdopplung, keine Halbierung; GMU-A/B nur
  eine Stufe (−0,02), keine Kaskade.
