# vLLM-Autokalibration: der Algorithmus

> **English version:** [calibration-vllm.md](../../en/architecture/calibration-vllm.md)

> SSOT-Begleitdokument zum Code (`aifred/lib/calibration/vllm_flow.py`,
> `vllm_probe.py`, `vllm_model_meta.py`). Der Code ist die Wahrheit —
> dieses Dokument erklärt die Entscheidungsregeln und ihre gemessenen
> Begründungen, damit Dritte (und wir selbst) sie nachvollziehen können.
> Messbelege: [vLLM-Autokalibration (Benchmarks)](../benchmarks/vllm-autocalibration.md).
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
- **GPU-Enumeration** über alle Karten: vor der Kalibration werden
  llama-swap und die Zusatzmodelle gestoppt, danach wird per Prozessliste
  gedraint (`drain_gpus`, SSOT-Primitive, kein Doppelzustand mit geladenen
  Modellen).

## Ablauf

### Phase A — Topologie-Leiter

Kandidaten-Topologien (TP×PP-Kombinationen über die freien Karten,
schnellste Compute-Klasse zuerst — die Reihenfolge ist seit 2026-08-30
gemessen, nicht nur Konvention: der Drafter auf der langsameren Klasse
kostet 2–4 %). Jede Sprosse bootet mit k=0 und iteriert das
Kontextfenster: vLLMs eigene Grenzschätzung ist mit geladenem
MTP-Draftkopf zu optimistisch, deshalb wird ihre Fehlermeldung als
neues `max-model-len` übernommen und erneut gebootet (bis zu 3 Runden).

### KV-Blöcke (Override bei jedem Boot)

Jeder Kalibrier-Boot läuft mit `--num-gpu-blocks-override`. Nur dann
nennt das Boot-Log je Worker, wie viele Blöcke das Budget unter der GMU
hielte (`Overriding num_gpu_blocks=<A_i>`), und nach dem Boot die
Poolgröße (`GPU KV cache size`, `Maximum concurrency`). Daraus
(`KvReport` in `vllm_probe.py`): `N_auto = min(A_i)` und die Blockzahl
eines vollen Kontexts `N_min = N × mml / Pool-Token`. Ziel ist
`max(N_auto, N_min)` — so viel Cache, wie das Budget trägt, mindestens ein
voller Kontext (Beschluss 2026-10-20). `_boot` regelt die Zahl ein:

- Startprüfung unter dem Budget gescheitert → zurück aufs Budget.
- Das Budget trägt keinen vollen Kontext → der Override erzwingt ihn (so
  läuft DeepSeek-V4). Läuft die Karte damit über, gilt vLLMs Schätzung
  für das Budget als Kontextgrenze.
- Bestanden mit weniger Blöcken als dem Ziel → neu booten: mit kleinerem
  Cache hätte der Allokator mehr Luft als im Betrieb.

Startwert ist die Blockzahl des vorhandenen llama-swap-Eintrags; ohne
einen solchen ein einzelner Block, dessen sichere Absage beide Zahlen
liefert (kostet einen Ladevorgang). Jede weitere Sprosse startet mit
`N_min` beim nativen Fenster — die Zahl hängt am Kontext, nicht an der
Topologie.

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

Bevor der Sieger persistiert wird, fährt er die längste Anfrage
(`probe_fill_context`, 95 % des Fensters, Token am Server gezählt). Erst
danach gilt „frei unter Last": Der Allokator behält seine
Hochwassermarke, und Puffer wie der DSv4-Indexer wachsen mit dem Kontext.
Läuft dabei eine Karte über, sinkt zuerst der Cache auf einen vollen
Kontext, danach der Kontext. Was danach je Karte frei ist, steht mit den
Blockzahlen und Bytes je Block in `meta.kv`, der Boot-Parametersatz in
`meta.spec` — die Grundlage der Profile in Phase H.

Betriebspunkt-YAML mit vollständigem Boot-Kommando, Umgebung,
Hardware-Fingerprint und Meta (k-Sweep-Matrix, Langkontext-Messwerte,
Akzeptanz). Zusätzlich optional eine `-speed`-Variante (weniger Karten,
reduzierter Kontext) — nur persistiert, wenn sie den Betriebspunkt
schlägt. Nach der Kalibration startet llama-swap neu und lädt den
frisch persistierten Punkt.

### Phase H — Profile für die Zusatzmodelle

Weg A (Beschluss 2026-10-20): Die Basis rechnet auf allen Karten, keine
bleibt frei. Jede angehakte Zelle der Kalibrier-Matrix außer der Basis
steht für ein Profil `<entry>-tts-<engine>[-vlm-<key>]`, das
`calibrate_vllm_side_channel_variant` aus `meta.kv` und `meta.spec` der
Basis ableitet. Budgets sind die nackten Burn-in-Spitzen, dazu EIN
Restpuffer `SIDE_CHANNEL_RESIDUAL_MB` je Karte mit Zusatzmodellen
(`side_channel_needs`, dieselbe Regel wie bei llama.cpp). Die VLM-Karte
steht fest (Sammelkarte); die TTS-Karte wählt der Planer: eine Karte ohne
Modell, dann der größte gemessene Rest, dann der kleinste Verzicht auf
KV-Blöcke. Die Topologie bleibt; die erste tragende Stufe gewinnt:

1. eine Karte, die die Topologie nicht nutzt — ohne Boot,
2. gemessener Rest auf einer Modellkarte — ohne Boot,
3. KV-Cache verkleinern, höchstens bis ein voller Kontext,
4. GMU senken, nur bei PLE-Modellen (PLE füllt seine Stufe bis zur GMU),
5. Kontext verkleinern.

Stufe 3–5 werden gebootet (Blockzahl fest, `policy="keep"`), fahren die
längste Anfrage und werden nach dem gemessenen Rest nachgeregelt. Passt
nichts, bleibt das Profil ungeschrieben und als `capacity_exceeded`
vermerkt (roter Punkt). Die STT bekommt kein eigenes Profil: Sie nimmt,
was frei bleibt, notfalls die CPU.

## Bekannte Grenzen

- Die Akzeptanzrate wird gemessen und protokolliert, fließt aber nicht
  in die Regel ein — der Lang-Decode enthält sie implizit.
- Chunk-A/B testet nur die Verdopplung, keine Halbierung; GMU-A/B nur
  eine Stufe (−0,02), keine Kaskade.
